import { expect, test } from "@playwright/test";
import { createAgent, expectRunSucceeded, runViaApi, uniqueSlug } from "./helpers";

test.describe("Workspace files", () => {
  test("Files toggle on the run page lists the workspace, marks changes and shows file content", async ({ page, request }) => {
    const slug = uniqueSlug("files");
    await createAgent(request, { slug, model: "fake:hello" });
    const run = await runViaApi(request, slug, "write hello.py and run it");

    const list = await request.get(`/v1/runs/${run.id}/files?changed=1`);
    expect(list.ok()).toBeTruthy();
    expect(await list.json()).toContainEqual(expect.objectContaining({ path: "hello.py", is_text: true, changed: true, change: "added" }));

    await page.goto(`/runs/${run.id}`);
    await expectRunSucceeded(page);
    const changes = page.getByRole("region", { name: "Changes" });
    await expect(changes.getByTestId("diff-view")).toBeVisible();
    await expect(changes.getByRole("button", { name: "Diff", exact: true })).toHaveAttribute("aria-pressed", "true");

    await changes.getByRole("button", { name: "Files", exact: true }).click();
    await expect(changes.getByRole("button", { name: "Files", exact: true })).toHaveAttribute("aria-pressed", "true");
    const panel = changes.getByTestId("files-panel");
    await expect(panel).toBeVisible();
    await expect(changes.getByTestId("diff-view")).toHaveCount(0);

    const hello = panel.locator('[data-testid="tree-file"][data-path="hello.py"]');
    await expect(hello).toHaveAttribute("data-changed", "true");
    // The first changed text file opens automatically, with line numbers and highlighting.
    const content = panel.getByTestId("file-content");
    await expect(content).toHaveAttribute("data-path", "hello.py");
    await expect(content.locator('[data-line="1"]')).toContainText("print('hello from forge')");
    await expect(content.locator('[data-token="string"]')).toHaveText("'hello from forge'");
    expect(await panel.locator('[data-testid="tree-file"]').evaluateAll((els) => els.some((e) => (e as HTMLElement).dataset.path?.startsWith(".git")))).toBe(false);

    // Switching back shows the diff again.
    await changes.getByRole("button", { name: "Diff", exact: true }).click();
    await expect(changes.getByTestId("diff-view")).toBeVisible();
  });
});
