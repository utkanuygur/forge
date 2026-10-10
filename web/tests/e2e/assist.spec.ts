import { expect, test } from "@playwright/test";
import { createAgent, uniqueSlug } from "./helpers";

// fake:grader answers the assist kinds deterministically (forge/assist/kinds): eval_cases without a checker type
// returns the first FAKE_SHELL cases not already taken, first the train ones then the holdout ones; instructions
// appends one fixed line to the current text.
const FIRST = ["workspace-summary-file", "line-count-report", "hello-script-runs"];

test.describe("Generate buttons", () => {
  test("GEN10: Evaluations > Generate several > Add selected adds only the checked cases", async ({ page, request }) => {
    const slug = uniqueSlug("gencases");
    await createAgent(request, { slug, model: "fake:echo" });

    await page.goto(`/agents/${slug}/evals`);
    await page.getByRole("button", { name: "Generate several" }).click();
    const dialog = page.getByRole("dialog", { name: "Generate evaluation cases" });
    await dialog.getByLabel("How many cases").selectOption("3");
    await dialog.getByLabel("Holdout cases").selectOption("1");
    await dialog.getByRole("button", { name: "Generate cases" }).click();

    const items = dialog.getByTestId("review-case");
    await expect(items).toHaveCount(3);
    await expect(items.nth(0)).toHaveAttribute("data-name", FIRST[0]);
    await expect(items.nth(2)).toContainText("holdout");
    // Nothing is created by generating.
    expect(await (await request.get(`/v1/agents/${slug}/evals`)).json()).toEqual([]);

    await dialog.getByLabel(FIRST[1]).uncheck();
    await dialog.getByRole("button", { name: "Add selected (2)" }).click();
    await expect(dialog).toBeHidden();
    const rows = page.getByTestId("eval-case");
    await expect(rows).toHaveCount(2);
    await expect(page.locator(`[data-testid="eval-case"][data-name="${FIRST[0]}"]`)).toBeVisible();
    await expect(page.locator(`[data-testid="eval-case"][data-name="${FIRST[2]}"]`)).toContainText("holdout");
    const saved = await (await request.get(`/v1/agents/${slug}/evals`)).json();
    expect(saved.map((c: { name: string }) => c.name).sort()).toEqual([FIRST[0], FIRST[2]].sort());
  });

  test("GEN10: Evaluations > Generate case opens a prefilled form and saves only on Add case", async ({ page, request }) => {
    const slug = uniqueSlug("gencase");
    await createAgent(request, { slug, model: "fake:echo" });

    await page.goto(`/agents/${slug}/evals`);
    await page.getByRole("button", { name: "Generate case" }).click();
    const dialog = page.getByRole("dialog", { name: "Review generated case" });
    await expect(dialog.getByLabel("Name")).toHaveValue(FIRST[0]);
    await expect(dialog.getByLabel("Checker")).toHaveValue(/set -euo pipefail/);
    expect(await (await request.get(`/v1/agents/${slug}/evals`)).json()).toEqual([]);
    await dialog.getByLabel("Name").fill("my-summary-case");
    await dialog.getByRole("button", { name: "Add case" }).click();
    await expect(page.locator('[data-testid="eval-case"][data-name="my-summary-case"]')).toBeVisible();
  });

  test("GEN10: Configure > instructions Generate shows a diff to accept or reject", async ({ page, request }) => {
    const slug = uniqueSlug("geninstr");
    await createAgent(request, { slug, model: "fake:echo", system_prompt: "You fix bugs." });

    await page.goto(`/agents/${slug}/configure`);
    const instructions = page.getByLabel("Instructions", { exact: true });
    await expect(instructions).toHaveValue("You fix bugs.");
    await page.getByRole("button", { name: "Generate an improved prompt" }).click();
    const proposal = page.getByTestId("instructions-proposal");
    await expect(proposal.getByTestId("diff-view").locator('[data-kind="add"]').last()).toHaveText(
      "+Always finish with a one line summary of what you changed.",
    );
    await expect(proposal).toContainText("Adds a closing summary requirement.");
    await expect(instructions).toHaveValue("You fix bugs.");

    await proposal.getByRole("button", { name: "Reject" }).click();
    await expect(proposal).toBeHidden();
    await expect(instructions).toHaveValue("You fix bugs.");

    await page.getByRole("button", { name: "Generate an improved prompt" }).click();
    await page.getByTestId("instructions-proposal").getByRole("button", { name: "Accept" }).click();
    await expect(instructions).toHaveValue(/Always finish with a one line summary of what you changed\.$/);
    await expect(page.getByRole("button", { name: "Save as new version" })).toBeEnabled();
    await page.getByRole("button", { name: "Undo" }).click();
    await expect(instructions).toHaveValue("You fix bugs.");
  });
});
