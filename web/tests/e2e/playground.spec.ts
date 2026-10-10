import { expect, test } from "@playwright/test";
import { createAgent, RUN_TIMEOUT, uniqueSlug } from "./helpers";

test("playground: two turns share history and files, recall and feedback work", async ({ page }) => {
  const slug = uniqueSlug("pg");
  await createAgent(page.request, { slug, model: "fake:session" });
  await page.goto(`/agents/${slug}/playground`);
  const pg = page.getByTestId("playground");
  await expect(pg.getByText("New session ·")).toBeVisible();
  await expect(pg.getByTestId("suggested-prompts")).toHaveCount(0); // field absent on this server

  const box = pg.getByRole("textbox", { name: "Message" });
  await box.fill("create hello.py");
  await box.press("Enter");
  const turns = pg.getByTestId("turn");
  await expect(turns.nth(0)).toContainText("● Write(hello.py)", { timeout: RUN_TIMEOUT });
  await expect(turns.nth(0)).toHaveAttribute("data-status", "succeeded", { timeout: RUN_TIMEOUT });
  await expect(pg.getByTestId("status-line")).toContainText("Done in");
  await expect(page).toHaveURL(/[?&]session=s_[0-9a-f]{12}/);
  await expect(pg.getByTestId("session-item").first()).toContainText("create hello.py");

  await expect(box).toBeEnabled();
  await box.fill("what is in hello.py?");
  await box.press("Enter");
  await expect(turns).toHaveCount(2);
  const second = turns.nth(1);
  await expect(second.getByTestId("transcript-input")).toContainText("what is in hello.py?");
  await expect(second).toContainText("● Bash(cat hello.py)", { timeout: RUN_TIMEOUT });
  await expect(second.locator('[data-testid="narration"][data-kind="final"]')).toContainText(
    "Turn 2: hello.py contains print('hello from forge') (history had 1 earlier turn(s)).",
    { timeout: RUN_TIMEOUT },
  );
  // Both turns stay stacked like a Claude Code session.
  await expect(turns.nth(0).getByTestId("transcript-input")).toContainText("create hello.py");

  // Up arrow in the empty composer recalls the last prompt.
  await expect(box).toBeEnabled();
  await box.press("ArrowUp");
  await expect(box).toHaveValue("what is in hello.py?");
  await box.fill("");

  // Per-turn feedback.
  const good = second.getByTestId("turn-feedback").getByRole("button", { name: "Good" });
  await good.click();
  await expect(good).toHaveAttribute("aria-pressed", "true");

  // Ctrl+O expands every tool output across turns.
  await page.keyboard.press("Control+o");
  await expect(turns.nth(0).getByTestId("numbered-code")).toContainText("print('hello from forge')");

  // Reloading restores the session from the URL.
  await page.reload();
  await expect(page.getByTestId("turn")).toHaveCount(2);
});

test("playground: Stop ends a running turn and the session can continue", async ({ page }) => {
  const slug = uniqueSlug("pg-stop");
  await createAgent(page.request, { slug, model: "fake:slow" });
  await page.goto(`/agents/${slug}/playground`);
  const pg = page.getByTestId("playground");
  const box = pg.getByRole("textbox", { name: "Message" });
  await box.fill("wait for a while");
  await box.press("Enter");
  await expect(pg.getByTestId("esc-hint")).toHaveText("Esc to stop");
  await expect(pg.locator('[data-testid="tool-card"][data-status="pending"]')).toBeVisible({ timeout: RUN_TIMEOUT });
  await expect(pg.getByTestId("status-line")).toContainText("Working…");
  await expect(box).toBeDisabled();

  await pg.getByRole("button", { name: "Stop" }).click();
  const turn = pg.getByTestId("turn").first();
  await expect(turn).toHaveAttribute("data-status", "cancelled", { timeout: 30_000 });
  await expect(turn.getByTestId("transcript-error")).toContainText("Stopped by user");
  await expect(pg.getByTestId("status-line")).toContainText("Stopped after");
  await expect(box).toBeEnabled();

  // Esc stops too.
  await box.fill("again");
  await box.press("Enter");
  await expect(pg.getByTestId("turn")).toHaveCount(2);
  await expect(pg.locator('[data-testid="tool-card"][data-status="pending"]')).toBeVisible({ timeout: RUN_TIMEOUT });
  await page.keyboard.press("Escape");
  await expect(pg.getByTestId("turn").nth(1)).toHaveAttribute("data-status", "cancelled", { timeout: 30_000 });
});

test("playground: Test from Configure runs unsaved edits as a draft", async ({ page }) => {
  const slug = uniqueSlug("pg-draft");
  await createAgent(page.request, { slug, model: "fake:echo", system_prompt: "Original instructions." });
  await page.goto(`/agents/${slug}/configure`);
  await page.locator("#instructions").fill("Edited, unsaved instructions.");
  await page.getByRole("button", { name: "Test" }).click();
  await expect(page).toHaveURL(new RegExp(`/agents/${slug}/playground`));
  const pg = page.getByTestId("playground");
  await expect(pg.getByTestId("draft-banner")).toBeVisible();
  await expect(pg.getByText("New session ·")).toContainText("Unsaved draft");

  const box = pg.getByRole("textbox", { name: "Message" });
  await box.fill("hi there");
  await box.press("Enter");
  await expect(pg.locator('[data-testid="narration"][data-kind="final"]')).toContainText("echo: hi there", {
    timeout: RUN_TIMEOUT,
  });
  await expect(pg.getByTestId("session-item").first()).toContainText("draft");

  // Draft turns never show up in the agent's runs (metrics).
  const runs = await (await page.request.get(`/v1/agents/${slug}/runs`)).json();
  expect(runs).toHaveLength(0);

  // The draft's config is what ran, and it can be saved as a version (not promoted).
  const sessionId = new URL(page.url()).searchParams.get("session")!;
  const session = await (await page.request.get(`/v1/sessions/${sessionId}`)).json();
  expect(session.version_n).toBeNull();
  expect(session.draft.system_prompt).toBe("Edited, unsaved instructions.");
  await pg.getByRole("button", { name: "Save as new version" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Saved v2" })).toBeVisible();
  const agent = await (await page.request.get(`/v1/agents/${slug}`)).json();
  expect(agent.prod_version).toBe(1);
  expect(agent.versions.find((v: { n: number }) => v.n === 2).system_prompt).toBe("Edited, unsaved instructions.");
});

test("playground: suggested prompts start a session when the agent has them", async ({ page }) => {
  const slug = uniqueSlug("pg-suggest");
  await createAgent(page.request, { slug, model: "fake:echo" });
  // suggested_prompts comes from another branch; inject it so the cards render here.
  await page.route(`**/v1/agents/${slug}`, async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    await route.fulfill({ response: res, json: { ...body, suggested_prompts: [{ title: "Say hello", prompt: "hello cards" }] } });
  });
  await page.goto(`/agents/${slug}/playground`);
  const cards = page.getByTestId("suggested-prompts");
  await cards.getByRole("button", { name: /Say hello/ }).click();
  await expect(page.locator('[data-testid="narration"][data-kind="final"]')).toContainText("echo: hello cards", {
    timeout: RUN_TIMEOUT,
  });
  await expect(cards).toHaveCount(0);
});
