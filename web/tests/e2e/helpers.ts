import { expect, type APIRequestContext, type Page } from "@playwright/test";

/** Each run takes a few seconds in Docker; allow generous headroom under parallel load. */
export const RUN_TIMEOUT = 90_000;
export const IMPROVE_TIMEOUT = 240_000;

export function uniqueSlug(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

export interface AgentJson {
  id: string;
  slug: string;
  name: string;
  share_token: string;
  prod_version: number;
  api_key?: string;
}

export async function createAgent(
  request: APIRequestContext,
  opts: { slug: string; name?: string; system_prompt?: string; model?: string; description?: string },
): Promise<AgentJson> {
  const res = await request.post("/v1/agents", {
    data: {
      slug: opts.slug,
      name: opts.name ?? `Agent ${opts.slug}`,
      description: opts.description ?? "",
      system_prompt: opts.system_prompt ?? "You are a helpful test agent.",
      ...(opts.model ? { model: opts.model } : {}),
    },
  });
  expect(res.ok(), await res.text()).toBeTruthy();
  return res.json();
}

/** Start a run through the API and wait for it to finish (localhost builder access, no key needed). */
export async function runViaApi(request: APIRequestContext, slug: string, input: string): Promise<{ id: string; status: string }> {
  const res = await request.post(`/v1/agents/${slug}/runs`, { data: { input, wait: true }, timeout: RUN_TIMEOUT });
  expect(res.ok(), await res.text()).toBeTruthy();
  const run = await res.json();
  expect(run.status).toBe("succeeded");
  return run;
}

/** The header status pill on a run page. */
export function runStatus(page: Page) {
  return page.getByTestId("status-pill").first();
}

export async function expectRunSucceeded(page: Page) {
  await expect(runStatus(page)).toHaveAttribute("data-status", "succeeded", { timeout: RUN_TIMEOUT });
}

export async function hasNoHorizontalScroll(page: Page): Promise<{ scrollWidth: number; clientWidth: number }> {
  return page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
}
