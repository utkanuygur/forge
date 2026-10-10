import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import StatusPill, { statusClasses } from "../../src/components/StatusPill";

const CASES: [string, string, string][] = [
  ["queued", "bg-neutral-100", "bg-neutral-400"],
  ["running", "bg-blue-50", "animate-pulse"],
  ["succeeded", "bg-emerald-50", "bg-emerald-500"],
  ["failed", "bg-red-50", "bg-red-500"],
  ["timeout", "bg-amber-50", "bg-amber-500"],
  ["eligible", "bg-accent-50", "bg-accent-500"],
  ["promoted", "bg-emerald-50", "bg-emerald-500"],
  ["rejected", "bg-neutral-100", "bg-neutral-400"],
];

describe("statusClasses", () => {
  it.each(CASES)("maps %s", (status, pill, dot) => {
    const c = statusClasses(status);
    expect(c.pill).toContain(pill);
    expect(c.dot).toContain(dot);
  });

  it("falls back to the queued style for unknown statuses, including prototype keys", () => {
    expect(statusClasses("mystery")).toEqual(statusClasses("queued"));
    expect(statusClasses("constructor")).toEqual(statusClasses("queued"));
  });
});

describe("StatusPill", () => {
  it("renders the status text and data attribute", () => {
    render(<StatusPill status="failed" />);
    const pill = screen.getByTestId("status-pill");
    expect(pill).toHaveTextContent("failed");
    expect(pill).toHaveAttribute("data-status", "failed");
    expect(pill.className).toContain("bg-red-50");
  });
});
