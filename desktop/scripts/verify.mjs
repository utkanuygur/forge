// End to end check of the desktop app with Playwright's Electron support.
// Usage:
//   node scripts/verify.mjs                        dev mode (Electron from node_modules, server via uv)
//   node scripts/verify.mjs --app /path/Forge.app  a built app
//   add --no-docker to check the Docker missing screen, --tunnel to also try a public link
// Uses a temporary userData folder (FORGE_DESKTOP_USERDATA) so real settings stay untouched.
import { _electron as electron } from 'playwright-core';
import { execSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const desktop = path.resolve(here, '..');
const args = process.argv.slice(2);
const appPath = args.includes('--app') ? args[args.indexOf('--app') + 1] : null;
const noDocker = args.includes('--no-docker');
const withTunnel = args.includes('--tunnel');
const imageTag = args.includes('--image') ? args[args.indexOf('--image') + 1] : null; // exercises the first-run image build
const outDir = args.includes('--out') ? args[args.indexOf('--out') + 1] : path.join(desktop, 'build', 'verify');
fs.mkdirSync(outDir, { recursive: true });

const userData = fs.mkdtempSync(path.join(os.tmpdir(), 'forge-desktop-verify-'));
const env = { ...process.env, FORGE_DESKTOP_USERDATA: userData, FORGE_DESKTOP_TEST: '1' }; // never shows a window
delete env.ANTHROPIC_API_KEY;
if (noDocker) env.FORGE_DESKTOP_SIMULATE_NO_DOCKER = '1';
if (!appPath) env.FORGE_DESKTOP_DEV = '1';
if (imageTag) {
  env.FORGE_SANDBOX_IMAGE = imageTag;
  try {
    execSync(`docker rmi ${imageTag}`, { stdio: 'ignore' });
  } catch {
    // not present
  }
}

const pgrep = (pattern) => {
  try {
    return execSync(`pgrep -f ${JSON.stringify(pattern)}`, { encoding: 'utf8' }).trim().split('\n').filter(Boolean);
  } catch {
    return [];
  }
};
const log = (...m) => console.log('[verify]', ...m);
const fail = (m) => {
  throw new Error(m);
};

// Windows stay hidden in test mode, so capture them with webContents.capturePage in the main process.
async function shot(app, page, name) {
  const url = page.url();
  const b64 = await app.evaluate(async ({ BrowserWindow }, target) => {
    const w = BrowserWindow.getAllWindows().find((x) => x.webContents.getURL() === target);
    return (await w.webContents.capturePage()).toPNG().toString('base64');
  }, url);
  fs.writeFileSync(path.join(outDir, name), Buffer.from(b64, 'base64'));
}
const setValue = (page, sel, value) => page.evaluate(([s, v]) => {
  const el = document.querySelector(s);
  el.value = v;
  el.dispatchEvent(new Event('input', { bubbles: true }));
}, [sel, value]);
const click = (page, sel) => page.evaluate((s) => document.querySelector(s).click(), sel);
const waitFor = async (page, fn, arg, timeout = 60000) => page.waitForFunction(fn, arg, { timeout, polling: 250 });

async function windowWhere(app, pred, timeout = 120000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    for (const w of app.windows()) {
      try {
        if (pred(w.url())) return w;
      } catch {
        // window closing
      }
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  fail(`no window matched within ${timeout} ms; open: ${app.windows().map((w) => w.url()).join(', ')}`);
}

const serverPattern = appPath ? 'forge-server/forge-server' : 'uvicorn forge.server.app:app';
const tunnelsBefore = pgrep('cloudflared tunnel --no-autoupdate --url http://127.0.0.1');
const serversBefore = pgrep(serverPattern);

const launch = appPath
  ? { executablePath: path.join(appPath, 'Contents', 'MacOS', 'Forge'), env }
  : { args: [desktop], cwd: desktop, env };
log('userData', userData);
const app = await electron.launch(launch);
const results = {};

try {
  const startup = await app.firstWindow();
  await startup.waitForLoadState('domcontentloaded');

  if (noDocker) {
    await waitFor(startup, () => document.querySelector('#problem.show'), null, 30000);
    await startup.waitForTimeout(300);
    await shot(app, startup, 'docker-missing.png');
    results.dockerMissing = await startup.evaluate(() => document.querySelector('#problem-title').textContent);
    log('docker missing screen:', results.dockerMissing);
  } else {
    await startup.waitForTimeout(200);
    await shot(app, startup, 'startup.png').catch((e) => log('startup shot', e.message));
    if (imageTag) {
      await waitFor(startup, () => /Building|#\d/.test(document.querySelector('li.active .detail')?.textContent || ''));
      await shot(app, startup, 'image-build.png');
      results.imageBuild = await startup.evaluate(() => document.querySelector('li.active')?.innerText || '');
      log('image build in progress:', results.imageBuild.replace(/\n/g, ' | '));
    }
    const main = await windowWhere(app, (u) => u.startsWith('http://127.0.0.1:'));
    await main.waitForLoadState('load');
    const origin = new URL(main.url()).origin;
    log('main window at', origin);

    const health = await main.evaluate(async () => (await fetch('/healthz')).json());
    results.health = health;
    if (health.executor !== 'docker' || health.components.docker.status !== 'ok') fail(`healthz: ${JSON.stringify(health)}`);
    log('healthz ok, docker', health.components.docker.server_version);
    const procs = pgrep(serverPattern).filter((p) => !serversBefore.includes(p));
    log('server processes', procs);
    if (!procs.length) fail('no server process found');

    // First run with no key opens Settings.
    const settingsWin = await windowWhere(app, (u) => u.endsWith('settings.html'), 20000);
    await waitFor(settingsWin, () => document.querySelector('#first-run') && !document.querySelector('#first-run').hidden);
    await settingsWin.waitForTimeout(300);
    await shot(app, settingsWin, 'settings.png');
    const dummy = 'sk-ant-api03-DUMMYVERIFYKEY-0000000000';
    await setValue(settingsWin, '#key', dummy);
    await setValue(settingsWin, '#default-model', 'fake:hello');
    await click(settingsWin, '#save');
    await waitFor(settingsWin, () => document.querySelector('#status.ok'), null, 120000);
    log('settings saved:', await settingsWin.evaluate(() => document.querySelector('#status').textContent));
    await shot(app, settingsWin, 'settings-saved.png');
    const raw = fs.readFileSync(path.join(userData, 'settings.json'), 'utf8');
    const stored = JSON.parse(raw);
    if (raw.includes(dummy) || raw.includes('DUMMYVERIFYKEY')) fail('the key is stored in plain text');
    const cipher = Buffer.from(stored.anthropicApiKeyEncrypted || '', 'base64');
    if (cipher.length < 16 || cipher.includes(Buffer.from('DUMMYVERIFYKEY'))) fail('the key is not encrypted');
    results.settingsFile = { keys: Object.keys(stored), cipherPrefix: cipher.subarray(0, 3).toString(), cipherBytes: cipher.length };
    log('settings.json fields', results.settingsFile);
    const logText = fs.readFileSync(path.join(userData, 'logs', 'server.log'), 'utf8');
    if (logText.includes('DUMMYVERIFYKEY')) fail('the key appears in the server log');
    await click(settingsWin, '#cancel');

    // Create an agent and run it in Docker through the local API (the main window is a localhost client).
    const page = await windowWhere(app, (u) => u.startsWith(origin));
    await page.waitForLoadState('load');
    const run = await page.evaluate(async () => {
      const post = async (url, body) => {
        const r = await fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
        const j = await r.json();
        if (!r.ok) throw new Error(`${url} ${r.status} ${JSON.stringify(j)}`);
        return j;
      };
      const agent = await post('/v1/agents', { slug: 'hello-desktop', name: 'Hello desktop', system_prompt: 'Write and run hello.py.' });
      const run = await post(`/v1/agents/${agent.slug}/runs`, { input: 'Say hello', wait: true });
      const events = await (await fetch(`/v1/runs/${run.id}/events.json`)).json();
      const version = await (await fetch(`/v1/agents/${agent.slug}/versions/prod`)).json();
      return { model: version.model, id: run.id, status: run.status, output: run.output, error: run.error,
               tool: events.filter((e) => e.type === 'tool_result').map((e) => JSON.stringify(e.payload).slice(0, 200)) };
    });
    results.run = run;
    log('run', run);
    if (run.model !== 'fake:hello') fail('default model setting did not reach the server');
    if (run.status !== 'succeeded' || !run.tool.some((t) => t.includes('hello from forge'))) fail('run did not succeed in the sandbox');

    await page.goto(`${origin}/runs/${run.id}`);
    await page.waitForTimeout(1500);
    await shot(app, page, 'run.png');
    await page.goto(`${origin}/`);
    await page.waitForTimeout(1200);
    await shot(app, page, 'main.png');

    if (withTunnel) {
      await app.evaluate(({ Menu }) => {
        const share = Menu.getApplicationMenu().items.find((i) => i.label === 'Share');
        share.submenu.items.find((i) => i.label === 'Start Public Link').click();
      });
      const deadline = Date.now() + 60000;
      let url = null;
      while (!url && Date.now() < deadline) {
        const m = fs.readFileSync(path.join(userData, 'logs', 'server.log'), 'utf8').match(/public link started: (\S+)/);
        url = m && m[1];
        if (!url) await new Promise((r) => setTimeout(r, 500));
      }
      results.tunnel = { url, processes: pgrep('cloudflared tunnel --no-autoupdate --url http://127.0.0.1').filter((p) => !tunnelsBefore.includes(p)) };
      log('tunnel', results.tunnel);
      if (!url) fail('no public link');
      // Resolve through DNS over HTTPS: the system resolver may cache the name as missing for a while.
      const status = (u) => {
        try {
          return execSync(`curl -s -o /dev/null -w '%{http_code}' --max-time 10 --doh-url https://1.1.1.1/dns-query ${u}`, { encoding: 'utf8' }).trim();
        } catch (e) {
          return `error: ${String(e.stdout || e.message).trim()}`;
        }
      };
      for (let i = 0; i < 20; i++) {
        results.tunnel.templates = status(`${url}/v1/templates`);
        if (results.tunnel.templates === '200') break;
        await new Promise((r) => setTimeout(r, 3000));
      }
      results.tunnel.agents = status(`${url}/v1/agents`);
      log('through tunnel: /v1/templates', results.tunnel.templates, '/v1/agents', results.tunnel.agents);
    }
  }
} finally {
  await app.close().catch((e) => log('close error', e.message));
  await new Promise((r) => setTimeout(r, 2500));
  const servers = pgrep(serverPattern).filter((p) => !serversBefore.includes(p));
  const tunnels = pgrep('cloudflared tunnel --no-autoupdate --url http://127.0.0.1').filter((p) => !tunnelsBefore.includes(p));
  results.afterQuit = { servers, tunnels };
  if (imageTag) {
    try {
      results.imageBuilt = execSync(`docker image inspect ${imageTag} --format '{{.Id}}'`, { encoding: 'utf8' }).trim();
      execSync(`docker rmi ${imageTag}`, { stdio: 'ignore' });
    } catch {
      results.imageBuilt = null;
    }
    log('image built by the app:', results.imageBuilt);
  }
  log('after quit, leftover server processes:', servers.length, 'tunnel processes:', tunnels.length);
  fs.writeFileSync(path.join(outDir, noDocker ? 'results-no-docker.json' : 'results.json'), JSON.stringify(results, null, 2));
  if (servers.length || tunnels.length) {
    console.error('[verify] FAIL: orphan processes');
    process.exitCode = 1;
  }
}
