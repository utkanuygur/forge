import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { defineConfig, devices } from "@playwright/test";

// One fresh data dir per `playwright test` invocation. Workers re-evaluate this file, so the dir is
// chosen once in the main process and inherited through the environment.
process.env.FORGE_E2E_DATA_DIR ??= mkdtempSync(join(tmpdir(), "forge-e2e-"));
// The server port: FORGE_E2E_PORT if set, else a free port picked once (same inheritance trick), so several
// suites (other checkouts, `make test-fast`) can run side by side.
process.env.FORGE_E2E_PORT ||= execFileSync(process.execPath, ["-e",
  "const s=require('net').createServer();s.listen(0,'127.0.0.1',()=>{process.stdout.write(String(s.address().port));s.close()})",
]).toString().trim();
const PORT = Number(process.env.FORGE_E2E_PORT);
// Tests are independent (unique slugs per test), so workers only trade speed for load. PW_WORKERS overrides.
const WORKERS = Number(process.env.PW_WORKERS) || (process.env.CI ? 2 : 4);

export default defineConfig({
  testDir: "tests/e2e",
  fullyParallel: true,
  workers: WORKERS,
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
  webServer: {
    command: `cd .. && uv run uvicorn forge.server.app:app --host 127.0.0.1 --port ${PORT} --no-access-log`,
    url: `http://127.0.0.1:${PORT}/v1/templates`,
    timeout: 120_000,
    reuseExistingServer: false,
    stdout: "pipe",
    stderr: "pipe",
    env: {
      FORGE_DATA_DIR: process.env.FORGE_E2E_DATA_DIR,
      FORGE_EXECUTOR: "docker",
      FORGE_DEFAULT_MODEL: "fake:hello",
      FORGE_GRADER_MODEL: "fake:grader",
      // Give fake:hello a price (USD per million tokens) so spend shows up in the observability screens.
      FORGE_LOG_JSON: "0",
      FORGE_PRICES: JSON.stringify({ "fake:hello": [1000, 2000] }),
    },
  },
});
