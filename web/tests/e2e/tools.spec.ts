import { expect, test } from "@playwright/test";
import { createAgent, expectRunSucceeded, RUN_TIMEOUT, uniqueSlug } from "./helpers";

test("FR15: add an HTTP tool in Configure, validate its schema, test it and save a version", async ({ page, request }) => {
  const slug = uniqueSlug("tools");
  await createAgent(request, { slug });

  await page.goto(`/agents/${slug}/configure`);
  const tools = page.getByRole("region", { name: "Tools" });
  await expect(tools).toContainText("No HTTP tools");
  await page.getByRole("switch", { name: /Network access/ }).click();

  await tools.getByRole("button", { name: "Add HTTP tool" }).click();
  const dialog = page.getByRole("dialog", { name: "Add HTTP tool" });
  await dialog.getByLabel("Name").fill("get_item");
  await dialog.getByLabel("Description").fill("Fetch an item by id.");
  await dialog.getByLabel("URL").fill("http://127.0.0.1:9/items/{id}");

  // Schema validation happens as you type and blocks saving.
  const schema = dialog.getByLabel("Parameters (JSON schema)");
  const good = await schema.inputValue();
  await schema.fill('{"type": "string"}');
  await expect(dialog.getByTestId("http-tool-problems")).toContainText('needs "type": "object"');
  await expect(dialog.getByRole("button", { name: "Save tool" })).toBeDisabled();
  await schema.fill(good);
  await expect(dialog.getByTestId("http-tool-problems")).toHaveCount(0);

  // The Test button runs the tool on the server; loopback is refused by SSRF protection.
  await dialog.getByRole("button", { name: "Test" }).click();
  const result = dialog.getByTestId("http-test-result");
  await expect(result).toContainText("Failed");
  await expect(result).toContainText("blocked request to 127.0.0.1");

  await dialog.getByRole("button", { name: "Save tool" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(tools.locator('[data-testid="tool-item"][data-name="get_item"]')).toContainText("GET http://127.0.0.1:9/items/{id}");

  await page.getByLabel("What changed?").fill("Add get_item");
  await page.getByRole("button", { name: "Save as new version" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Saved v2" })).toBeVisible();
  const v2 = await (await request.get(`/v1/agents/${slug}/versions/2`)).json();
  expect(v2.network).toBe(true);
  expect(v2.http_tools).toHaveLength(1);
  expect(v2.http_tools[0]).toMatchObject({ name: "get_item", method: "GET", url: "http://127.0.0.1:9/items/{id}" });
  expect(v2.http_tools[0].parameters.required).toEqual(["id"]);
});

test("FR15: a parent run links to the child run it started, and the child links back", async ({ page, request }) => {
  const child = uniqueSlug("child");
  const parent = uniqueSlug("parent");
  await createAgent(request, { slug: child, model: "fake:echo", description: "Echoes its input." });
  const created = await request.post("/v1/agents", {
    data: { slug: parent, name: "Parent", system_prompt: "Delegate.", model: "fake:tools", callable_agents: [child] },
  });
  expect(created.ok(), await created.text()).toBeTruthy();

  // Callable agents show up in Configure as a multi select.
  await page.goto(`/agents/${parent}/configure`);
  await expect(page.getByLabel(`Allow calling ${child}`)).toBeChecked();

  const input = JSON.stringify({ tool: `agent__${child}`, args: { input: "hello from parent" } });
  const res = await request.post(`/v1/agents/${parent}/runs`, { data: { input, wait: true }, timeout: RUN_TIMEOUT });
  expect(res.ok(), await res.text()).toBeTruthy();
  const run = await res.json();
  expect(run.status).toBe("succeeded");

  await page.goto(`/runs/${run.id}`);
  await expectRunSucceeded(page);
  const card = page.locator(`[data-testid="tool-card"][data-tool="agent__${child}"]`);
  const link = card.getByTestId("child-run-link");
  await expect(link).toBeVisible();
  await expect(page.getByTestId("child-usage")).toBeVisible();
  await link.click();

  await expect(page).toHaveURL(/\/runs\/r_[0-9a-f]{12}$/);
  expect(page.url()).not.toContain(run.id);
  await expectRunSucceeded(page);
  await expect(page.getByTestId("transcript").locator('[data-kind="final"]')).toContainText("echo: hello from parent");
  const calledBy = page.getByTestId("called-by");
  await expect(calledBy).toContainText(run.id);
  await calledBy.getByRole("link").click();
  await expect(page).toHaveURL(new RegExp(`/runs/${run.id}$`));
});
