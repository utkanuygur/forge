import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef, useState } from "react";
import GenerateTaskButton from "../../src/components/GenerateTaskButton";
import RunForm from "../../src/components/RunForm";

const TASK = { title: "Refund endpoint", prompt: "Add POST /orders/{id}/refund in src/acme/app.py.", rationale: "r" };

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

let fetchMock: ReturnType<typeof vi.fn>;
let respond: () => Promise<Response> | Response;

beforeEach(() => {
  respond = () => json(TASK);
  fetchMock = vi.fn(async (url: string) => {
    if (String(url).startsWith("/v1/agents/demo/sample-task")) return respond();
    return json({ detail: "unexpected" }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function Harness({ initial = "", share }: { initial?: string; share?: string }) {
  const [value, setValue] = useState(initial);
  const ref = useRef<HTMLTextAreaElement>(null);
  return (
    <div>
      <textarea aria-label="Task" ref={ref} value={value} onChange={(e) => setValue(e.target.value)} />
      <GenerateTaskButton slug="demo" share={share} value={value} inputRef={ref} onGenerated={setValue} />
    </div>
  );
}

const box = () => screen.getByLabelText("Task") as HTMLTextAreaElement;
const button = () => screen.getByRole("button", { name: "Generate a sample task" });

describe("GenerateTaskButton", () => {
  it("shows a spinner and Generating… while the request is in flight, then fills and focuses the box", async () => {
    let release!: () => void;
    respond = () => new Promise<Response>((r) => (release = () => r(json(TASK))));
    render(<Harness />);
    await userEvent.click(button());
    expect(button()).toBeDisabled();
    expect(button()).toHaveTextContent("Generating…");
    await act(async () => release());
    await waitFor(() => expect(box().value).toBe(TASK.prompt));
    expect(button()).toHaveTextContent("Generate");
    expect(button()).not.toBeDisabled();
    await waitFor(() => expect(document.activeElement).toBe(box()));
    expect(box().selectionStart).toBe(TASK.prompt.length);
    expect(box().selectionEnd).toBe(TASK.prompt.length);
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull(); // the box was empty
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/v1/agents/demo/sample-task");
    expect(JSON.parse(init.body)).toEqual({ avoid: [] });
  });

  it("replaces existing text and offers Undo, which restores it", async () => {
    render(<Harness initial="my own draft" />);
    await userEvent.click(button());
    await waitFor(() => expect(box().value).toBe(TASK.prompt));
    await userEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(box().value).toBe("my own draft");
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
  });

  it("hides Undo after 10 seconds", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<Harness initial="old" />);
    fireEvent.click(button());
    await waitFor(() => expect(screen.getByRole("button", { name: "Undo" })).toBeInTheDocument());
    act(() => {
      vi.advanceTimersByTime(10_001);
    });
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
    expect(box().value).toBe(TASK.prompt);
  });

  it("shows the server error inline and leaves the box unchanged", async () => {
    respond = () => json({ detail: "Too many sample tasks from this link. Try again in 12 min." }, 429);
    render(<Harness initial="keep me" share="tok" />);
    await userEvent.click(button());
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many sample tasks");
    expect(box().value).toBe("keep me");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/v1/agents/demo/sample-task?share=tok");
    expect(init.headers["X-Share-Token"]).toBe("tok");
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("Alt+G in the focused box triggers a generation; later calls avoid earlier prompts", async () => {
    render(<Harness />);
    box().focus();
    fireEvent.keyDown(box(), { key: "©", code: "KeyG", altKey: true });
    await waitFor(() => expect(box().value).toBe(TASK.prompt));
    fireEvent.keyDown(box(), { key: "g", code: "KeyG" }); // plain g does nothing
    expect(fetchMock).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(box(), { key: "g", code: "KeyG", altKey: true });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ avoid: [TASK.prompt] });
  });

  it("is mounted in RunForm and fills its task box", async () => {
    render(<RunForm slug="demo" onCreated={vi.fn()} />);
    await userEvent.click(button());
    await waitFor(() => expect(box().value).toBe(TASK.prompt));
    expect(screen.getByRole("button", { name: /Run/ })).not.toBeDisabled();
  });
});
