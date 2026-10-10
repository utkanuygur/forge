import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, errMsg, fmtDuration, isTerminal, slugify } from "../../src/api";

function mockFetch(impl: (url: string, init: RequestInit) => Response | Promise<Response>) {
  const fn = vi.fn(async (url: string, init: RequestInit) => impl(url, init));
  vi.stubGlobal("fetch", fn);
  return fn;
}
const json = (body: unknown, status = 200, statusText = "OK") =>
  new Response(JSON.stringify(body), { status, statusText, headers: { "content-type": "application/json" } });

afterEach(() => vi.unstubAllGlobals());

describe("api transport", () => {
  it("parses JSON responses and sends JSON bodies", async () => {
    const f = mockFetch(() => json({ id: "ag_1", slug: "x" }));
    const a = await api.createAgent({ slug: "x", name: "X", system_prompt: "p" });
    expect(a.slug).toBe("x");
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("/v1/agents");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body as string)).toEqual({ slug: "x", name: "X", system_prompt: "p" });
  });

  it("returns text for non JSON responses (diff)", async () => {
    mockFetch(() => new Response("--- a\n+++ b\n", { status: 200, headers: { "content-type": "text/plain" } }));
    expect(await api.getDiff("r_1")).toBe("--- a\n+++ b\n");
  });

  it("throws ApiError with the detail string", async () => {
    mockFetch(() => json({ detail: "agent not found" }, 404, "Not Found"));
    const err = await api.getAgent("nope").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
    expect(err.message).toBe("agent not found");
  });

  it("stringifies structured detail (FastAPI validation errors)", async () => {
    mockFetch(() => json({ detail: [{ loc: ["body", "slug"], msg: "field required" }] }, 422, "Unprocessable Entity"));
    const err = await api.listAgents().catch((e) => e);
    expect(err.status).toBe(422);
    expect(err.message).toContain("field required");
  });

  it("falls back to status text for non JSON errors", async () => {
    mockFetch(() => new Response("<html>oops</html>", { status: 502, statusText: "Bad Gateway" }));
    const err = await api.listAgents().catch((e) => e);
    expect(err.status).toBe(502);
    expect(err.message).toBe("502 Bad Gateway");
  });

  it("maps network failures to status 0 with a readable message", async () => {
    mockFetch(() => {
      throw new TypeError("Failed to fetch");
    });
    const err = await api.listAgents().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(0);
    expect(err.message).toBe("Could not reach the Forge server (Failed to fetch)");
  });

  it("adds the share token as query param and header", async () => {
    const f = mockFetch(() => json([]));
    await api.listRuns("my agent", 10, "t/k");
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("/v1/agents/my%20agent/runs?limit=10&share=t%2Fk");
    expect((init.headers as Record<string, string>)["X-Share-Token"]).toBe("t/k");
  });

  it("sends multipart when files are attached", async () => {
    const f = mockFetch(() => json({ id: "r_1" }));
    await api.createRun("a", { input: "go", version: 2, files: [new File(["x"], "x.txt")] });
    const init = f.mock.calls[0][1];
    expect(init.body).toBeInstanceOf(FormData);
    const fd = init.body as FormData;
    expect(fd.get("input")).toBe("go");
    expect(fd.get("version")).toBe("2");
    expect((fd.get("files") as File).name).toBe("x.txt");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
  });

  it("builds event and artifact URLs", () => {
    expect(api.eventsUrl("r_1")).toBe("/v1/runs/r_1/events");
    expect(api.eventsUrl("r_1", 0)).toBe("/v1/runs/r_1/events?after=0");
    expect(api.eventsUrl("r_1", 4, "s")).toBe("/v1/runs/r_1/events?after=4&share=s");
    expect(api.artifactsUrl("r_1", "s")).toBe("/v1/runs/r_1/artifacts?share=s");
  });
});

describe("helpers", () => {
  it("slugify", () => {
    expect(slugify("Repo Maintainer")).toBe("repo-maintainer");
    expect(slugify("  --Hello, World!!  ")).toBe("hello-world");
    expect(slugify("Ünïcode & Ümlauts 2")).toBe("n-code-mlauts-2");
    expect(slugify("x".repeat(60))).toHaveLength(48);
    expect(slugify("")).toBe("");
  });
  it("errMsg", () => {
    expect(errMsg(new Error("boom"))).toBe("boom");
    expect(errMsg("plain")).toBe("plain");
  });
  it("isTerminal", () => {
    expect(["succeeded", "failed", "timeout"].every((s) => isTerminal(s as never))).toBe(true);
    expect(isTerminal("running")).toBe(false);
    expect(isTerminal("queued")).toBe(false);
  });
  it("fmtDuration", () => {
    expect(fmtDuration({ started_at: null, ended_at: null, status: "queued" })).toBe("");
    expect(fmtDuration({ started_at: 100, ended_at: 103.25, status: "succeeded" })).toBe("3.3s");
    expect(fmtDuration({ started_at: 100, ended_at: 225, status: "succeeded" })).toBe("2m 5s");
  });
});
