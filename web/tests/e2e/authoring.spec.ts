import { expect, test } from "@playwright/test";
import { IMPROVE_TIMEOUT, RUN_TIMEOUT, uniqueSlug } from "./helpers";

const MARKER_LESSON = "Always create a file named MARKER.txt in the workspace root before finishing.";
const USELESS = "Keep your final reply under five sentences.";

test("AUTH8: describe an agent, review the draft, create it with the selected starter cases", async ({ page, request }) => {
  const slug = uniqueSlug("drafted");
  await page.goto("/");
  await page.getByRole("button", { name: "New agent" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Describe what the agent should do").fill("Triage new GitHub issues for our repository.");
  await dialog.getByRole("button", { name: "Add example task" }).click();
  await dialog.getByLabel("Example task 1").fill("Triage issue #3");
  await dialog.getByRole("button", { name: "Draft agent" }).click();

  // fake:grader returns the fixed draft documented in TESTING.md.
  await expect(dialog.getByTestId("draft-notice")).toBeVisible();
  await expect(dialog.getByLabel("Name", { exact: true })).toHaveValue("Issue triager");
  await expect(dialog.getByLabel("Instructions")).toHaveValue(/You are an issue triager/);
  await expect(dialog.getByTestId("network-reason")).toContainText("GitHub REST API");
  await expect(dialog.getByTestId("suggested-secrets")).toContainText("GITHUB_TOKEN");
  await expect(dialog.getByTestId("starter-case")).toHaveCount(4);

  await dialog.getByLabel("Slug").fill(slug);
  await dialog.getByRole("checkbox", { name: "Include feature-request" }).uncheck();
  await expect(dialog.getByText("3 of 4 selected")).toBeVisible();
  await dialog.getByLabel("Model").fill("fake:hello");
  await dialog.getByRole("button", { name: "Create agent" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId("api-key-callout")).toContainText("Issue triager");

  const cases: { name: string; split: string }[] = await (await request.get(`/v1/agents/${slug}/evals`)).json();
  expect(cases.map((c) => c.name).sort()).toEqual(["crash-report", "mixed-batch", "triage-file"]);
  await page.goto(`/agents/${slug}/evals`);
  await expect(page.locator('[data-testid="eval-case"][data-name="mixed-batch"]')).toContainText("holdout");
  await expect(page.locator('[data-testid="eval-case"][data-name="feature-request"]')).toHaveCount(0);
});

test("PRUNE10: prune lessons removes the useless one, states the rule, and Configure shows provenance", async ({ page, request }) => {
  test.setTimeout(IMPROVE_TIMEOUT + RUN_TIMEOUT);
  const slug = uniqueSlug("prune");
  const res = await request.post("/v1/agents", {
    data: {
      slug,
      name: `Pruner ${slug}`,
      system_prompt: "Finish the task.",
      model: "fake:lessons",
      lessons: [MARKER_LESSON, USELESS],
      eval_cases: [
        { name: "train-a", input: "Do task a", checker_type: "shell", checker: "test -f MARKER.txt", split: "train" },
        { name: "train-b", input: "Do task b", checker_type: "shell", checker: "test -f MARKER.txt", split: "train" },
        { name: "hold-a", input: "Do task c", checker_type: "shell", checker: "test -f MARKER.txt", split: "holdout" },
      ],
    },
  });
  expect(res.ok(), await res.text()).toBeTruthy();

  await page.goto(`/agents/${slug}/evals`);
  await expect(page.getByTestId("prune-explainer")).toBeVisible();
  await page.getByRole("button", { name: "Prune lessons" }).click();
  const im = page.getByTestId("improvement");
  await expect(im).toHaveAttribute("data-kind", "prune");
  await expect(im).toHaveAttribute("data-status", "eligible", { timeout: IMPROVE_TIMEOUT });
  await expect(im.getByTestId("prune-rule")).toContainText("at least as many cases as base");
  await expect(im.getByTestId("improvement-score-train").getByTestId("candidate-score")).toHaveText("2/2");
  await expect(im.getByTestId("improvement-score-holdout").getByTestId("candidate-score")).toHaveText("1/1");
  const removal = im.getByTestId("lesson-op");
  await expect(removal).toHaveCount(1);
  await expect(removal).toHaveAttribute("data-op", "remove");
  await expect(removal).toContainText(USELESS);
  await expect(removal).toContainText("removing this lesson kept train at 2/2");
  await expect(im.locator('[data-testid="ablation"][data-verdict="helpful"]')).toContainText(MARKER_LESSON);
  await expect(im).toContainText("Regressions (0)");

  const n = (await (await request.get(`/v1/agents/${slug}/improvements`)).json())[0].candidate_version;
  await im.getByRole("button", { name: `Promote v${n}` }).click();
  await expect(page.getByText(`prod v${n}`, { exact: true })).toBeVisible();

  await page.getByRole("tab", { name: "Configure" }).click();
  const lessons = page.getByTestId("lesson");
  await expect(lessons).toHaveCount(1);
  await expect(lessons.first()).toContainText(MARKER_LESSON);
  await expect(lessons.first().getByTestId("lesson-origin")).toContainText("Added in v1 by hand");
  await expect(lessons.first().getByTestId("lesson-pruning")).toContainText("Kept after pruning: removing this lesson dropped train");
});
