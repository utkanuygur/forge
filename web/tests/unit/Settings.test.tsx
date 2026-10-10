import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import SettingsPage from "../../src/pages/SettingsPage";
import ModelPicker, { modelInstalled } from "../../src/components/settings/ModelPicker";
import { fromDraft, toDraft } from "../../src/components/settings/SettingField";
import { ToastProvider } from "../../src/components/Toast";
import type { ProviderStatus, SettingView, SettingsPayload } from "../../src/settingsApi";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

function setting(over: Partial<SettingView>): SettingView {
  return {
    key: "k", env: "FORGE_K", type: "string", label: "K", description: "A setting.", group: "Models", apply: "live",
    secret: false, source: "default", editable: true, needs_restart: false, error: null, value: "", default: "",
    ...over,
  };
}

const PROVIDERS: ProviderStatus[] = [
  { id: "anthropic", name: "Claude", kind: "anthropic", base_url: "https://api.anthropic.com", prefix: "anthropic:",
    configured: true, reachable: true, models: ["claude-sonnet-5-5"], error: null, checked_at: 0 },
  { id: "openai_compat:http://localhost:11434/v1", name: "Ollama", kind: "openai_compat",
    base_url: "http://localhost:11434/v1", prefix: "openai_compat:http://localhost:11434/v1|", configured: true,
    reachable: true, models: ["qwen3:4b-instruct"], error: null, checked_at: 0 },
  { id: "openai_compat:https://gpu.example/v1", name: "GPU", kind: "openai_compat", base_url: "https://gpu.example/v1",
    prefix: "openai_compat:https://gpu.example/v1|", configured: true, reachable: false, models: [],
    error: "connection refused", checked_at: 0 },
];

function payload(over: Partial<Record<string, Partial<SettingView>>> = {}, needsRestart = false): SettingsPayload {
  const s = (o: Partial<SettingView>) => setting({ ...o, ...(over[o.key!] ?? {}) });
  return {
    desktop: false,
    needs_restart: needsRestart,
    info: { data_dir: "/tmp/forge-data", version: "0.1.0", executor: "docker",
            docker: { status: "ok", server_version: "27.0", image: "forge-sandbox" } },
    groups: [
      { name: "Models", settings: [
        s({ key: "default_model", type: "model", label: "Default agent model", value: "anthropic:claude-sonnet-5-5",
            default: "anthropic:claude-sonnet-5-5" }),
        s({ key: "anthropic_api_key", type: "secret", label: "Claude API key", secret: true, editable: false,
            is_set: true, from_desktop: false, value: undefined, env: "ANTHROPIC_API_KEY" }),
      ] },
      { name: "Evaluations", settings: [
        s({ key: "eval_repeats", type: "int", label: "Default eval repeats", value: 3, default: 3, min: 1, max: 9,
            group: "Evaluations" }),
      ] },
      { name: "Server", settings: [
        s({ key: "max_concurrent_runs", type: "int", label: "Max concurrent runs", value: 8, default: 8, min: 1,
            max: 256, apply: "restart", group: "Server" }),
        s({ key: "sandbox_image", type: "string", label: "Sandbox image", value: "from-env", source: "env",
            editable: false, env: "FORGE_SANDBOX_IMAGE", group: "Server" }),
      ] },
      { name: "Pricing", settings: [
        s({ key: "prices", type: "json", widget: "prices", label: "Model prices", value: {}, default: {},
            group: "Pricing" }),
      ] },
    ],
  };
}

let fetchMock: ReturnType<typeof vi.fn>;
let current: SettingsPayload;
let patchResponse: (body: Record<string, unknown>) => Response;

beforeEach(() => {
  current = payload();
  patchResponse = (body) => {
    const over: Record<string, Partial<SettingView>> = {};
    for (const [k, v] of Object.entries(body)) over[k] = { value: v, source: "saved" };
    current = payload(over, "max_concurrent_runs" in body);
    return json(current);
  };
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/v1/settings" && (!init?.method || init.method === "GET")) return json(current);
    if (url === "/v1/settings" && init?.method === "PATCH") return patchResponse(JSON.parse(String(init.body)));
    if (url === "/v1/settings/reset") {
      current = payload();
      return json(current);
    }
    if (url.startsWith("/v1/models/available")) return json({ providers: PROVIDERS });
    return json({ detail: "unexpected" }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.forgeDesktop;
});

function renderPage() {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <SettingsPage />
      </ToastProvider>
    </MemoryRouter>,
  );
}

const calls = (method: string) => fetchMock.mock.calls.filter(([u, i]) => u === "/v1/settings" && i?.method === method);

describe("SettingsPage", () => {
  it("shows groups, descriptions, source badges and status cards", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getByText("Models")).toBeInTheDocument();
    expect(screen.getByTestId("source-sandbox_image")).toHaveTextContent("Environment");
    expect(screen.getByTestId("source-eval_repeats")).toHaveTextContent("Default");
    expect(screen.getByText(/Set by the environment variable/)).toBeInTheDocument();
    expect(screen.getByLabelText("Sandbox image")).toBeDisabled();
    await waitFor(() => expect(screen.getByTestId("status-ollama")).toHaveTextContent("Running, 1 models"));
    expect(screen.getByTestId("status-docker")).toHaveTextContent("image forge-sandbox present");
    expect(screen.getByTestId("status-gpu")).toHaveTextContent("Configured, not reachable");
    expect(screen.getByTestId("secret-anthropic_api_key")).toHaveTextContent("Set by the ANTHROPIC_API_KEY");
    expect(screen.getAllByText("Set keys in .env, then restart the server.").length).toBeGreaterThan(0);
    expect(screen.getByTestId("info-data-dir")).toHaveTextContent("/tmp/forge-data");
  });

  it("saves only the changed keys of one section and shows the saved badge", async () => {
    const user = userEvent.setup();
    renderPage();
    const repeats = await screen.findByLabelText("Default eval repeats");
    const save = screen.getByRole("button", { name: "Save Evaluations" });
    expect(save).toBeDisabled();
    await user.clear(repeats);
    await user.type(repeats, "5");
    await user.click(save);
    await waitFor(() => expect(calls("PATCH")).toHaveLength(1));
    expect(JSON.parse(String(calls("PATCH")[0][1].body))).toEqual({ eval_repeats: 5 });
    await waitFor(() => expect(screen.getByTestId("source-eval_repeats")).toHaveTextContent("Saved"));
    await user.click(screen.getByRole("button", { name: "Reset Default eval repeats to default" }));
    await waitFor(() => expect(screen.getByTestId("source-eval_repeats")).toHaveTextContent("Default"));
  });

  it("validates inline before sending and shows server errors per key", async () => {
    const user = userEvent.setup();
    renderPage();
    const repeats = await screen.findByLabelText("Default eval repeats");
    await user.clear(repeats);
    await user.type(repeats, "12");
    await user.click(screen.getByRole("button", { name: "Save Evaluations" }));
    expect(await screen.findByTestId("error-eval_repeats")).toHaveTextContent("must be at most 9");
    expect(calls("PATCH")).toHaveLength(0);

    patchResponse = () => json({ detail: { message: "Some settings were not saved", errors: { eval_repeats: "nope from server" } } }, 422);
    await user.clear(repeats);
    await user.type(repeats, "4");
    await user.click(screen.getByRole("button", { name: "Save Evaluations" }));
    expect(await screen.findByText("nope from server")).toBeInTheDocument();
  });

  it("shows the restart banner, with a Restart button only inside the desktop app", async () => {
    const user = userEvent.setup();
    const restartServer = vi.fn(async () => ({ ok: true }));
    const openKeySettings = vi.fn();
    window.forgeDesktop = { restartServer, openKeySettings };
    renderPage();
    const field = await screen.findByLabelText("Max concurrent runs");
    expect(screen.queryByTestId("restart-banner")).not.toBeInTheDocument();
    await user.clear(field);
    await user.type(field, "3");
    await user.click(screen.getByRole("button", { name: "Save Server" }));
    const banner = await screen.findByTestId("restart-banner");
    expect(banner).toHaveTextContent("Restart required");
    await user.click(within(banner).getByRole("button", { name: "Restart" }));
    expect(restartServer).toHaveBeenCalled();
    await user.click(screen.getAllByRole("button", { name: /Manage keys/ })[0]);
    expect(openKeySettings).toHaveBeenCalled();
  });

  it("edits the prices table", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Add price" }));
    await user.type(screen.getByLabelText("Price 1 model"), "fake:hello");
    await user.type(screen.getByLabelText("Price 1 input"), "1.5");
    await user.type(screen.getByLabelText("Price 1 output"), "2");
    await user.click(screen.getByRole("button", { name: "Save Pricing" }));
    await waitFor(() => expect(calls("PATCH")).toHaveLength(1));
    expect(JSON.parse(String(calls("PATCH")[0][1].body))).toEqual({ prices: { "fake:hello": [1.5, 2] } });
  });
});

describe("ModelPicker", () => {
  it("lists provider models by tab, marks installed ones and fills the text field", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<ModelPicker id="m" label="Model" value="" onChange={onChange} providers={PROVIDERS} />);
    expect(screen.getByRole("tab", { name: "Presets", selected: true })).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Ollama" }));
    await user.click(screen.getByRole("button", { name: /qwen3:4b-instruct/ }));
    expect(onChange).toHaveBeenCalledWith("openai_compat:http://localhost:11434/v1|qwen3:4b-instruct");
    await user.click(screen.getByRole("tab", { name: "GPU" }));
    expect(screen.getByText(/GPU is not reachable \(connection refused\)/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Model"), "x");
    expect(onChange).toHaveBeenLastCalledWith("x");
  });

  it("reports whether the chosen model is installed", () => {
    expect(modelInstalled("anthropic:claude-sonnet-5-5", PROVIDERS)).toBe(true);
    expect(modelInstalled("openai_compat:http://localhost:11434/v1|qwen3:8b", PROVIDERS)).toBe(false);
    expect(modelInstalled("openai_compat:https://gpu.example/v1|m", PROVIDERS)).toBeNull();
    expect(modelInstalled("fake:hello", PROVIDERS)).toBeNull();
    const { rerender } = render(<ModelPicker id="m" label="Model" value="openai_compat:http://localhost:11434/v1|qwen3:8b" onChange={() => {}} providers={PROVIDERS} />);
    expect(screen.getByText("Not available on its provider right now")).toBeInTheDocument();
    rerender(<ModelPicker id="m" label="Model" value="anthropic:claude-sonnet-5-5" onChange={() => {}} providers={PROVIDERS} />);
    expect(screen.getByText("Installed and available")).toBeInTheDocument();
  });
});

describe("draft conversion", () => {
  it("round trips numbers and prices", () => {
    const n = setting({ key: "n", type: "int", value: 3, min: 1, max: 9 });
    expect(toDraft(n)).toBe("3");
    expect(fromDraft(n, "4")).toBe(4);
    expect(() => fromDraft(n, "4.5")).toThrow("whole number");
    const p = setting({ key: "prices", type: "json", widget: "prices", value: { a: [1, 2] } });
    expect(toDraft(p)).toEqual([{ model: "a", input: "1", output: "2" }]);
    expect(() => fromDraft(p, [{ model: "", input: "1", output: "2" }])).toThrow("needs a model");
  });
});
