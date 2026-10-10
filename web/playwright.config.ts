import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { defineConfig, devices } from "@playwright/test";

// One fresh data dir per `playwright test` invocation. Workers re-evaluate this file, so the dir is
// chosen once in the main process and inherited through the environment.
process.env.FORGE_E2E_DATA_DIR ??= mkdtempSync(join(tmpdir(), "forge-e2e-"));
process.env.FORGE_E2E_SETTINGS_DATA_DIR ??= mkdtempSync(join(tmpdir(), "forge-e2e-settings-"));
const PORT = Number(process.env.FORGE_E2E_PORT || 8011);
// settings.spec.ts changes server wide settings (the default model), so it gets its own server, one port up,
// that leaves the default and grader models unset in the environment (environment values are read only).
const SETTINGS_PORT = PORT + 1;

const server = (port: number, dataDir: string, env: Record<string, string>) => ({
  command: `cd .. && uv run uvicorn forge.server.app:app --host 127.0.0.1 --port ${port} --no-access-log`,
  url: `http://127.0.0.1:${port}/v1/templates`,
  timeout: 120_000,
  reuseExistingServer: false,
  stdout: "pipe" as const,
  stderr: "pipe" as const,
  env: { FORGE_DATA_DIR: dataDir, FORGE_EXECUTOR: "docker", FORGE_LOG_JSON: "0", ...env },
});

export default defineConfig({
  testDir: "tests/e2e",
  fullyParallel: true,
  workers: process.env.CI ? 2 : 4,
  retries: process.env.CI ? 1 : 0,
  timeout: 120_000,
  expect: { timeout: 10_000 },
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  outputDir: "test-results",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] }, testIgnore: /settings\.spec\.ts/ },
    {
      name: "settings",
      use: { ...devices["Desktop Chrome"], baseURL: `http://127.0.0.1:${SETTINGS_PORT}` },
      testMatch: /settings\.spec\.ts/,
    },
  ],
  webServer: [
    server(PORT, process.env.FORGE_E2E_DATA_DIR, {
      FORGE_DEFAULT_MODEL: "fake:hello",
      FORGE_GRADER_MODEL: "fake:grader",
      // Give fake:hello a price (USD per million tokens) so spend shows up in the observability screens.
      FORGE_PRICES: JSON.stringify({ "fake:hello": [1000, 2000] }),
    }),
    // Starts with the built in default model; the spec switches it to a fake model in the Settings page.
    // Empty values count as unset, and they stop a developer's .env (loaded by the server, never overriding
    // variables that exist) from making these settings read only or sending a real Claude key anywhere.
    server(SETTINGS_PORT, process.env.FORGE_E2E_SETTINGS_DATA_DIR, {
      FORGE_DEFAULT_MODEL: "",
      FORGE_GRADER_MODEL: "",
      ANTHROPIC_API_KEY: "",
      OPENAI_API_KEY: "",
      FORGE_OPENAI_ENDPOINTS: "",
    }),
  ],
});
