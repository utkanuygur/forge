import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { formatPairs, parsePairs, parseSchema, sampleArgs, urlPlaceholders } from "../../src/components/ToolsConfig";
import TraceTimeline from "../../src/components/TraceTimeline";
import type { ForgeEvent } from "../../src/api";

describe("parseSchema", () => {
  it("accepts object schemas and explains what is wrong otherwise", () => {
    expect(parseSchema('{"type":"object","properties":{"a":{"type":"string"}}}').error).toBeNull();
    expect(parseSchema("{not json").error).toMatch(/Not valid JSON/);
    expect(parseSchema("[1]").error).toMatch(/JSON object/);
    expect(parseSchema('{"type":"string"}').error).toMatch(/"type": "object"/);
    expect(parseSchema('{"type":"object","properties":[]}').error).toMatch(/properties/);
  });
});

describe("pairs", () => {
  it("parses headers and env lines and formats them back", () => {
    const h = parsePairs("Authorization: Bearer {{API_KEY}}\n\nX-Trace: on", ":");
    expect(h).toEqual({ pairs: { Authorization: "Bearer {{API_KEY}}", "X-Trace": "on" }, error: null });
    expect(formatPairs(h.pairs)).toBe("Authorization: Bearer {{API_KEY}}\nX-Trace: on");
    expect(parsePairs("TOKEN={{T}}\nMODE=a=b", "=").pairs).toEqual({ TOKEN: "{{T}}", MODE: "a=b" });
    expect(parsePairs("no separator", ":").error).toMatch(/Line 1/);
  });
});

describe("url placeholders and sample args", () => {
  it("finds {param} but not {{SECRET}}", () => {
    expect(urlPlaceholders("https://h/{org}/items/{id}?k={{KEY}}")).toEqual(["org", "id"]);
  });
  it("builds typed example arguments", () => {
    const { schema } = parseSchema(
      '{"type":"object","properties":{"id":{"type":"string"},"n":{"type":"integer"},"f":{"type":"boolean"}}}',
    );
    expect(sampleArgs(schema)).toEqual({ id: "example", n: 1, f: true });
  });
});

describe("TraceTimeline child runs", () => {
  it("links an agent tool result to its child run, except on the share page", () => {
    const evs: ForgeEvent[] = [
      { run_id: "r_1", seq: 0, ts: 0, type: "tool_call", payload: { id: "c1", name: "agent__helper", args: { input: "hi" } } },
      {
        run_id: "r_1",
        seq: 1,
        ts: 0,
        type: "tool_result",
        payload: { id: "c1", name: "agent__helper", output: "echo: hi", is_error: false, child_run_id: "r_abcdefabcdef" },
      },
    ];
    const { unmount } = render(<TraceTimeline events={evs} />);
    expect(screen.getByTestId("child-run-link")).toHaveAttribute("href", "/runs/r_abcdefabcdef");
    unmount();
    render(<TraceTimeline events={evs} simple />);
    expect(screen.queryByTestId("child-run-link")).toBeNull();
  });
});
