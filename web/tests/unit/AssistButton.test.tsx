import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef, useState } from "react";
import AssistButton from "../../src/components/AssistButton";

// A made-up kind: the button is generic, so any kind and result shape works.
type Rubric = { rubric: string };

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn(async (url: string) => {
    if (url === "/v1/assist/rubric") return json({ result: { rubric: "PASS only if report.md exists." }, rationale: "r" });
    if (url === "/v1/assist/broken") return json({ detail: "The model could not write a valid broken suggestion" }, 502);
    return json({ detail: "unexpected" }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

function Field({ kind = "rubric", withUndo = true }: { kind?: string; withUndo?: boolean }) {
  const [value, setValue] = useState("draft");
  const ref = useRef<HTMLInputElement>(null);
  return (
    <div>
      <input aria-label="Rubric" ref={ref} value={value} onChange={(e) => setValue(e.target.value)} />
      <AssistButton<Rubric>
        kind={kind}
        slug="demo"
        version={3}
        input={{ case_input: "Write report.md" }}
        resultText={(r) => r.rubric}
        onResult={(r) => setValue(r.rubric)}
        undoValue={value}
        onUndo={withUndo ? setValue : undefined}
        inputRef={ref}
        label="Suggest"
        ariaLabel="Suggest a rubric"
      />
    </div>
  );
}

const field = () => screen.getByLabelText("Rubric") as HTMLInputElement;

describe("AssistButton", () => {
  it("posts kind, slug, version and kind input; delivers the result; uses the custom label", async () => {
    render(<Field />);
    const button = screen.getByRole("button", { name: "Suggest a rubric" });
    expect(button).toHaveTextContent("Suggest");
    await userEvent.click(button);
    await waitFor(() => expect(field().value).toBe("PASS only if report.md exists."));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/v1/assist/rubric");
    expect(JSON.parse(init.body)).toEqual({ slug: "demo", version: 3, input: { case_input: "Write report.md" }, avoid: [] });
    expect(screen.getByRole("button", { name: "Undo" })).toBeInTheDocument();
  });

  it("drops Undo once the field is edited, and offers none without onUndo", async () => {
    const { unmount } = render(<Field />);
    await userEvent.click(screen.getByRole("button", { name: "Suggest a rubric" }));
    await screen.findByRole("button", { name: "Undo" });
    await userEvent.type(field(), "!");
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
    unmount();
    render(<Field withUndo={false} />);
    await userEvent.click(screen.getByRole("button", { name: "Suggest a rubric" }));
    await waitFor(() => expect(field().value).toBe("PASS only if report.md exists."));
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
  });

  it("shows server errors inline for any kind", async () => {
    render(<Field kind="broken" />);
    await userEvent.click(screen.getByRole("button", { name: "Suggest a rubric" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not write a valid broken suggestion");
    expect(field().value).toBe("draft");
  });
});
