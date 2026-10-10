import { expect, test } from "@playwright/test";
import { createAgent, RUN_TIMEOUT, uniqueSlug } from "./helpers";

test("share chat: two turns, files, thumbs, and another visitor cannot see the conversation", async ({ browser, request }) => {
  const slug = uniqueSlug("share-chat");
  const secret = `SECRET-PROMPT-${slug}`;
  const agent = await createAgent(request, { slug, model: "fake:session", system_prompt: secret });
  const url = `/s/${slug}?share=${agent.share_token}`;

  // Visitor A in a fresh browser context.
  const ctxA = await browser.newContext();
  const a = await ctxA.newPage();
  await a.goto(url);
  const chat = a.getByTestId("share-chat");
  const box = chat.getByRole("textbox", { name: "Message" });
  await box.fill("create hello.py");
  await box.press("Enter");
  const turns = chat.getByTestId("turn");
  await expect(turns.nth(0)).toHaveAttribute("data-status", "succeeded", { timeout: RUN_TIMEOUT });
  await expect(turns.nth(0).getByTestId("user-message")).toHaveText("create hello.py");
  await expect(turns.nth(0).getByTestId("final-answer")).toBeVisible();
  // Steps are collapsed by default and open on demand.
  const steps = turns.nth(0).getByTestId("steps");
  await expect(steps).not.toHaveAttribute("open", "");
  await steps.locator("summary").click();
  await expect(steps).toContainText("Write(hello.py)");
  await expect(a).toHaveURL(/[?&]c=s_[0-9a-f]{12}/);
  const conversationUrl = a.url();

  await expect(box).toBeEnabled();
  await box.fill("what is in hello.py?");
  await box.press("Enter");
  await expect(turns).toHaveCount(2);
  await expect(turns.nth(1).getByTestId("final-answer")).toContainText(
    "Turn 2: hello.py contains print('hello from forge') (history had 1 earlier turn(s)).",
    { timeout: RUN_TIMEOUT },
  );

  // Thumbs per turn.
  const up = turns.nth(1).getByRole("button", { name: "Helpful", exact: true });
  await up.click();
  await expect(up).toHaveAttribute("aria-pressed", "true");

  // Files: the session workspace, with a download of the archive.
  await chat.getByRole("button", { name: "Files" }).click();
  const files = chat.getByTestId("share-files");
  await files.getByRole("button", { name: /hello\.py/ }).click();
  await expect(files.getByTestId("file-content")).toContainText("print('hello from forge')");
  const href = await files.getByTestId("download-workspace").getAttribute("href");
  expect(href).toContain("visitor=vis_");
  const archive = await a.request.get(href!);
  expect(archive.ok()).toBeTruthy();

  // Recent conversations and New conversation.
  await chat.getByRole("button", { name: "New conversation" }).click();
  await expect(chat.getByTestId("turn")).toHaveCount(0);
  await chat.getByRole("button", { name: "Recent conversations" }).click();
  await chat.getByTestId("conversation-item").first().click();
  await expect(chat.getByTestId("turn")).toHaveCount(2);
  expect(await a.content()).not.toContain(secret);

  // Visitor B: a second, fresh context with the same link and even the conversation URL sees nothing of A's.
  const ctxB = await browser.newContext();
  const b = await ctxB.newPage();
  await b.goto(conversationUrl);
  const chatB = b.getByTestId("share-chat");
  await expect(chatB.getByTestId("share-notice")).toContainText("This conversation is not available");
  await expect(chatB.getByTestId("turn")).toHaveCount(0);
  await expect(chatB.getByRole("button", { name: "Recent conversations" })).toHaveCount(0);
  expect(await b.content()).not.toContain("create hello.py");
  const turnId = (await request.get(`/v1/agents/${slug}/runs`).then((r) => r.json()))[0].id as string;
  const peek = await b.request.get(`/v1/runs/${turnId}?share=${agent.share_token}`);
  expect(peek.status()).toBe(404);
  await ctxA.close();
  await ctxB.close();
});

test("share chat: Stop ends a running turn at 390 px", async ({ browser, request }) => {
  const slug = uniqueSlug("share-stop");
  const agent = await createAgent(request, { slug, model: "fake:slow" });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await ctx.newPage();
  await page.goto(`/s/${slug}?share=${agent.share_token}`);
  const chat = page.getByTestId("share-chat");
  const box = chat.getByRole("textbox", { name: "Message" });
  await box.fill("wait for a while");
  await box.press("Enter");
  await expect(chat.getByTestId("status-line")).toContainText("Working", { timeout: RUN_TIMEOUT });
  await chat.getByRole("button", { name: "Stop" }).click();
  const turn = chat.getByTestId("turn").first();
  await expect(turn).toHaveAttribute("data-status", "cancelled", { timeout: 30_000 });
  await expect(turn).toContainText("Stopped.");
  await expect(box).toBeEnabled();
  // No horizontal scroll on a phone.
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await ctx.close();
});

test("share chat: the daily limit shows inline", async ({ browser, request }) => {
  const slug = uniqueSlug("share-limit");
  const agent = await createAgent(request, { slug, model: "fake:echo" });
  const patched = await request.patch(`/v1/agents/${slug}`, { data: { share_limits: { daily_turns: 1 } } });
  expect(patched.ok()).toBeTruthy();
  const ctx = await browser.newContext({ colorScheme: "dark" });
  const page = await ctx.newPage();
  await page.goto(`/s/${slug}?share=${agent.share_token}`);
  const chat = page.getByTestId("share-chat");
  const box = chat.getByRole("textbox", { name: "Message" });
  await box.fill("one");
  await box.press("Enter");
  await expect(chat.getByTestId("turn").first()).toHaveAttribute("data-status", "succeeded", { timeout: RUN_TIMEOUT });
  await expect(box).toBeEnabled();
  await box.fill("two");
  await box.press("Enter");
  await expect(chat.getByTestId("share-notice")).toHaveText(
    "You've reached today's limit for this link. Try again tomorrow.",
  );
  await ctx.close();
});

test("deploy tab: share link limits are editable next to the link", async ({ page }) => {
  const slug = uniqueSlug("share-limits");
  await createAgent(page.request, { slug, model: "fake:echo" });
  await page.goto(`/agents/${slug}/deploy`);
  const limits = page.getByTestId("share-limits");
  const perConversation = limits.getByRole("textbox", { name: "Messages per conversation" });
  await expect(perConversation).toHaveAttribute("placeholder", "Default 20");
  await perConversation.fill("5");
  await limits.getByRole("button", { name: "Save limits" }).click();
  await expect.poll(async () => (await (await page.request.get(`/v1/agents/${slug}`)).json()).share_limits.session_turns).toBe(5);
  await expect(page.locator("pre").filter({ hasText: "/sessions/$SESSION/messages" })).toBeVisible();
});
