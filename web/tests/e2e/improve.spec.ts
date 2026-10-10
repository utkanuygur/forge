import { expect, test, type Page } from "@playwright/test";
import { IMPROVE_TIMEOUT, RUN_TIMEOUT, uniqueSlug } from "./helpers";

const MARKER_LESSON = "Always create a file named MARKER.txt in the workspace root before finishing.";

async function addCase(page: Page, name: string, split: "train" | "holdout") {
  await page.getByRole("button", { name: "Add case" }).click();
  const dialog = page.getByRole("dialog", { name: "Add evaluation case" });
  await dialog.getByLabel("Name").fill(name);
  await dialog.getByLabel("Split").selectOption(split);
  await dialog.getByLabel("Input").fill(`Do the task for ${name}`);
  await dialog.getByRole("button", { name: "shell", exact: true }).click();
  await dialog.getByLabel("Checker").fill("test -f MARKER.txt");
  await dialog.getByRole("button", { name: "Add case" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.locator(`[data-testid="eval-case"][data-name="${name}"]`)).toContainText(split);
}

test("FR9 FR10: evaluate, improve, compare candidate on train and holdout, promote", async ({ page }) => {
  test.setTimeout(IMPROVE_TIMEOUT + 2 * RUN_TIMEOUT);
  const slug = uniqueSlug("imp");

  // Create through the UI with the lessons scenario model.
  await page.goto("/");
  await page.getByRole("button", { name: "New agent" }).click();
  const form = page.getByRole("dialog");
  await form.getByLabel("Name").fill(`Improver ${slug}`);
  await form.getByLabel("Slug").fill(slug);
  await form.getByLabel("Instructions").fill("Finish the task.");
  await form.getByLabel("Model").fill("fake:lessons");
  await form.getByRole("button", { name: "Create agent" }).click();
  await expect(page.getByTestId("api-key-callout")).toBeVisible();

  await page.goto(`/agents/${slug}/evals`);
  await expect(page.getByText("No evaluation cases yet")).toBeVisible();
  await addCase(page, "train-a", "train");
  await addCase(page, "train-b", "train");
  await addCase(page, "hold-a", "holdout");

  // Baseline: no case passes because nothing creates MARKER.txt.
  await page.getByRole("button", { name: "Run evaluation (v1)" }).click();
  await expect(page.getByTestId("eval-score-train")).toContainText("0/2", { timeout: RUN_TIMEOUT });
  await expect(page.getByTestId("eval-score-holdout")).toContainText("0/1", { timeout: RUN_TIMEOUT });
  await expect(page.getByRole("button", { name: "Run evaluation (v1)" })).toBeEnabled({ timeout: RUN_TIMEOUT });
  for (const name of ["train-a", "train-b", "hold-a"]) {
    const row = page.locator(`[data-testid="eval-case"][data-name="${name}"]`);
    await expect(row).toContainText("fail");
    await expect(row.getByTestId("pass-fraction")).toHaveText("0/3"); // majority of k=3 runs
  }

  // Improve: the optimizer adds the MARKER lesson; candidate passes everything.
  await page.getByRole("button", { name: "Improve", exact: true }).click();
  const im = page.getByTestId("improvement");
  await expect(im).toBeVisible();
  await expect(im).toHaveAttribute("data-status", "eligible", { timeout: IMPROVE_TIMEOUT });
  await expect(im.getByTestId("improvement-score-train").getByTestId("base-score")).toHaveText("0/2");
  await expect(im.getByTestId("improvement-score-train").getByTestId("candidate-score")).toHaveText("2/2");
  await expect(im.getByTestId("improvement-score-holdout").getByTestId("base-score")).toHaveText("0/1");
  await expect(im.getByTestId("improvement-score-holdout").getByTestId("candidate-score")).toHaveText("1/1");
  const lesson = im.getByTestId("lesson-op");
  await expect(lesson).toHaveAttribute("data-op", "add");
  await expect(lesson).toContainText(MARKER_LESSON);
  await expect(lesson).toContainText("Evidence:");
  await expect(im).toContainText("Regressions (0)");
  const row = im.locator('[data-testid="improvement-case"][data-name="train-a"]');
  await expect(row.getByTestId("pass-fraction")).toHaveText(["0/3", "3/3"]);

  await im.getByRole("button", { name: "Promote v2" }).click();
  await expect(page.getByText("prod v2", { exact: true })).toBeVisible();
  await expect(im).toHaveAttribute("data-status", "promoted");
  await expect(page.getByRole("button", { name: "Run evaluation (v2)" })).toBeVisible();

  // The promoted version carries the lesson and was written by the optimizer.
  await page.getByRole("tab", { name: "Configure" }).click();
  const v2 = page.locator('[data-testid="version-item"][data-version="2"]');
  await expect(v2.getByText("Production", { exact: true })).toBeVisible();
  await expect(v2).toContainText("optimizer");
  await expect(page.getByText(MARKER_LESSON, { exact: true })).toBeVisible();
});
