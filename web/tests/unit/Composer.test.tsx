import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import Composer from "../../src/components/playground/Composer";

function Harness(props: { running?: boolean; lastPrompt?: string; onSend?: (t: string) => void; onStop?: () => void }) {
  const [value, setValue] = useState("");
  return (
    <Composer
      value={value}
      onChange={setValue}
      onSend={props.onSend ?? (() => {})}
      onStop={props.onStop ?? (() => {})}
      running={Boolean(props.running)}
      lastPrompt={props.lastPrompt}
    />
  );
}

describe("Composer", () => {
  it("Enter sends the trimmed text, Shift+Enter inserts a newline", async () => {
    const onSend = vi.fn();
    render(<Harness onSend={onSend} />);
    const box = screen.getByRole("textbox", { name: "Message" });
    await userEvent.type(box, "line one{Shift>}{Enter}{/Shift}line two");
    expect(box).toHaveValue("line one\nline two");
    expect(onSend).not.toHaveBeenCalled();
    await userEvent.type(box, "  {Enter}");
    expect(onSend).toHaveBeenCalledWith("line one\nline two");
  });

  it("does not send empty input; the send button follows the text", async () => {
    const onSend = vi.fn();
    render(<Harness onSend={onSend} />);
    const box = screen.getByRole("textbox", { name: "Message" });
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    await userEvent.type(box, "   {Enter}");
    expect(onSend).not.toHaveBeenCalled();
    await userEvent.type(box, "go");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(onSend).toHaveBeenCalledWith("go");
  });

  it("Up arrow in an empty composer recalls the last prompt, not when there is text", async () => {
    render(<Harness lastPrompt="previous prompt" />);
    const box = screen.getByRole("textbox", { name: "Message" });
    await userEvent.type(box, "x{ArrowUp}");
    expect(box).toHaveValue("x");
    await userEvent.clear(box);
    await userEvent.type(box, "{ArrowUp}");
    expect(box).toHaveValue("previous prompt");
  });

  it("while running: disabled, shows Esc to stop and a Stop button", async () => {
    const onStop = vi.fn();
    const onSend = vi.fn();
    render(<Harness running onStop={onStop} onSend={onSend} />);
    expect(screen.getByRole("textbox", { name: "Message" })).toBeDisabled();
    expect(screen.getByTestId("esc-hint")).toHaveTextContent("Esc to stop");
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(onStop).toHaveBeenCalledTimes(1);
    expect(onSend).not.toHaveBeenCalled();
  });
});
