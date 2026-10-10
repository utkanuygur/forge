import { expect, test } from "@playwright/test";
import { createAgent, hasNoHorizontalScroll, uniqueSlug } from "./helpers";

// The browser server uses fake:grader, which answers like fake:copilot (forge/copilot/fake.py) for the
// assistant's system prompt: "add N eval cases to <slug>" makes N create_eval_case calls named
// assistant-case-<turn>-<i>, confirmed on one card.

test.describe("Forge assistant", () => {
  test("AS4: on the Evaluations tab, add three cases, approve, and see them appear", async ({ page, request }) => {
    const slug = uniqueSlug("asst");
    await createAgent(request, { slug, name: "Shop helper", model: "fake:echo" });
    await page.goto(`/agents/${slug}/evals`);
    await expect(page.getByText("assistant-case-0-1")).toHaveCount(0);

    await page.getByTestId("assistant-toggle").click();
    const panel = page.getByTestId("assistant-panel");
    await expect(panel).toBeVisible();
    await expect(panel.getByTestId("assistant-context")).toHaveText("Shop helper · Evaluations");
    await expect(panel.getByRole("button", { name: "Add eval cases" })).toBeVisible();

    const box = panel.getByRole("textbox", { name: "Message" });
    await expect(box).toBeFocused();
    await box.fill(`add 3 eval cases to ${slug}`);
    await box.press("Enter");

    const card = panel.getByTestId("confirm-card");
    await expect(card).toContainText(`Add 3 eval cases to ${slug}`);
    await expect(card.getByRole("listitem")).toHaveCount(3);
    await card.getByRole("button", { name: "Approve" }).click();
    await expect(card).toContainText("Approved");

    await expect(panel.getByTestId("tool-step")).toHaveCount(3);
    await expect(panel.getByTestId("tool-step").first()).toContainText(`create_eval_case(${slug}, assistant-case-0-1)`);
    await expect(panel.getByTestId("assistant-text").last()).toContainText("Added 3 eval cases");
    // The Evaluations tab refetched without a reload.
    for (const i of [1, 2, 3]) await expect(page.getByText(`assistant-case-0-${i}`).first()).toBeVisible();

    // Ctrl+J / Cmd+J closes it; the state is remembered across a reload.
    await page.keyboard.press("ControlOrMeta+KeyJ");
    await expect(panel).toBeHidden();
    await page.keyboard.press("ControlOrMeta+KeyJ");
    await expect(panel).toBeVisible();
    await page.reload();
    await expect(page.getByTestId("assistant-panel")).toBeVisible();
    await expect(page.getByTestId("assistant-panel").getByTestId("user-bubble")).toContainText("add 3 eval cases");
  });

  test("AS5: phone width shows the panel as a sheet without horizontal scroll", async ({ page, request }) => {
    const slug = uniqueSlug("asstphone");
    await createAgent(request, { slug, model: "fake:echo" });
    await page.setViewportSize({ width: 390, height: 800 });
    await page.goto(`/agents/${slug}/evals`);
    await page.getByTestId("assistant-toggle").click();
    const panel = page.getByTestId("assistant-panel");
    await expect(panel).toBeVisible();
    const box = await panel.boundingBox();
    expect(box?.width).toBeLessThanOrEqual(390);
    const { scrollWidth, clientWidth } = await hasNoHorizontalScroll(page);
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth);
    await panel.getByRole("button", { name: "Close assistant" }).click();
    await expect(panel).toBeHidden();
  });
});
