import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { PassCell, passFraction } from "../../src/pages/agent/ImprovementView";

describe("passFraction", () => {
  it("shows passes/runs only for repeated runs", () => {
    expect(passFraction(2, 3)).toBe("2/3");
    expect(passFraction(0, 3)).toBe("0/3");
    expect(passFraction(1, 1)).toBeNull();
    expect(passFraction(undefined, 3)).toBeNull();
    expect(passFraction(1, undefined)).toBeNull();
  });
});

describe("PassCell", () => {
  it("colors by the majority verdict and shows the fraction", () => {
    render(
      <MemoryRouter>
        <PassCell passed={true} reason="ok" runId="r_1" passes={2} runs={3} />
        <PassCell passed={false} reason="exit 1" passes={1} runs={2} />
      </MemoryRouter>,
    );
    const [pass, fail] = screen.getAllByTestId("pass-fraction");
    expect(pass).toHaveTextContent("2/3");
    expect(pass.parentElement).toHaveTextContent("pass");
    expect(pass.parentElement?.className).toContain("bg-emerald-50");
    expect(pass.parentElement?.getAttribute("title")).toContain("passed 2/3 runs (majority)");
    expect(fail).toHaveTextContent("1/2");
    expect(fail.parentElement?.className).toContain("bg-red-50");
    expect(screen.getByRole("link")).toHaveAttribute("href", "/runs/r_1");
  });

  it("omits the fraction for single-run verdicts", () => {
    render(<PassCell passed={true} runs={1} passes={1} />);
    expect(screen.queryByTestId("pass-fraction")).toBeNull();
    expect(screen.getByText("pass")).toBeInTheDocument();
  });
});
