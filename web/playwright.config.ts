import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { defineConfig, devices } from "@playwright/test";

// One fresh data dir per `playwright test` invocation. Workers re-evaluate this file, so the dir is
// chosen once in the main process and inherited through the environment.
process.env.FORGE_E2E_DATA_DIR ??= mkdtempSync(join(tmpdir(), "forge-e2e-"));
process.env.FORGE_E2E_EMPTY_DATA_DIR ??= mkdtempSync(join(tmpdir(), "forge-e2e-empty-"));
const PORT = Number(process.env.FORGE_E2E_PORT || 8011);
// A second server that only tests/e2e/examples.spec.ts uses, so it can start from zero agents.
const EMPTY_PORT = Number(process.env.FORGE_E2E_EMPTY_PORT || PORT + 1);
process.env.FORGE_E2E_EMPTY_URL = `http://127.0.0.1:${EMPTY_PORT}`;
const serverEnv = {
  FORGE_EXECUTOR: "docker",
  FORGE_DEFAULT_MODEL: "fake:hello",
  FORGE_GRADER_MODEL: "fake:grader",
  FORGE_LOG_JSON: "0",
  // Give fake:hello a price (USD per million tokens) so spend shows up in the observability screens.
  FORGE_PRICES: JSON.stringify({ "fake:hello": [1000, 2000] }),
};

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
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `cd .. && uv run uvicorn forge.server.app:app --host 127.0.0.1 --port ${PORT} --no-access-log`,
      url: `http://127.0.0.1:${PORT}/v1/templates`,
      timeout: 120_000,
      reuseExistingServer: false,
      stdout: "pipe",
      stderr: "pipe",
      env: { ...serverEnv, FORGE_DATA_DIR: process.env.FORGE_E2E_DATA_DIR },
    },
    {
      command: `cd .. && uv run uvicorn forge.server.app:app --host 127.0.0.1 --port ${EMPTY_PORT} --no-access-log`,
      url: `http://127.0.0.1:${EMPTY_PORT}/v1/templates`,
      timeout: 120_000,
      reuseExistingServer: false,
      stdout: "pipe",
      stderr: "pipe",
      env: { ...serverEnv, FORGE_DATA_DIR: process.env.FORGE_E2E_EMPTY_DATA_DIR },
    },
  ],
});
