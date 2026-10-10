import { expect, test } from "@playwright/test";
import { createAgent, expectRunSucceeded, uniqueSlug } from "./helpers";

// fake:grader answers assist kind "task" with the first of FAKE_TASKS (forge/assist/kinds/task.py) that is not
// already a recent input or in the avoid list.
const FIRST = "Can you list every file in the workspace and write SUMMARY.md";
const SECOND = "Please count the lines in each file of the workspace";

test.describe("Generate sample task", () => {
  test("GEN5: Generate on the Runs tab fills the task box and the run succeeds", async ({ page, request }) => {
    const slug = uniqueSlug("gen");
    await createAgent(request, { slug, model: "fake:echo" });

    await page.goto(`/agents/${slug}/runs`);
    const box = page.getByRole("textbox", { name: "Task" });
    const generate = page.getByRole("button", { name: "Generate a sample task" });
    await expect(generate).toHaveText("Generate");
    await generate.click();
    await expect(box).toHaveValue(new RegExp(`^${FIRST}`));
    await expect(box).toBeFocused();

    // A second generation replaces the text (it avoids the first) and offers Undo.
    await page.keyboard.press("Alt+KeyG");
    await expect(box).toHaveValue(new RegExp(`^${SECOND}`));
    await page.getByRole("button", { name: "Undo" }).click();
    await expect(box).toHaveValue(new RegExp(`^${FIRST}`));

    await page.getByRole("button", { name: "Run", exact: true }).click();
    await expect(page).toHaveURL(/\/runs\/r_[0-9a-f]{12}$/);
    await expectRunSucceeded(page);
    await expect(page.getByRole("region", { name: "Output" })).toContainText(`echo: ${FIRST}`);
  });

  test("GEN5: the share page generates with its token and gets no configuration", async ({ page, request }) => {
    const slug = uniqueSlug("genshare");
    const agent = await createAgent(request, { slug, model: "fake:echo" });

    await page.goto(`/s/${slug}?share=${agent.share_token}`);
    const responsePromise = page.waitForResponse((r) => r.url().includes("/v1/assist/task"));
    await page.getByRole("button", { name: "Generate a sample task" }).click();
    const response = await responsePromise;
    expect(response.request().headers()["x-share-token"]).toBe(agent.share_token);
    expect(Object.keys(await response.json()).sort()).toEqual(["rationale", "result"]);
    await expect(page.getByRole("textbox", { name: "Task" })).toHaveValue(new RegExp(`^${FIRST}`));
  });
});
