// Runs the Forge server: the frozen binary in the packaged app, `uv run uvicorn` from the repo in dev.
const fs = require('fs');
const net = require('net');
const path = require('path');
const { spawn } = require('child_process');
const paths = require('./paths');
const buildinfo = require('./buildinfo');
const { toolPath, findExecutable, stopProcess } = require('./proc');
const { secretEnv } = require('./keys');

const HOST = '127.0.0.1';
const LOG_LIMIT = 5 * 1024 * 1024;
const LOG_KEEP = 3;

let child = null;
let logStream = null;
let logBytes = 0;

function rotateLogs() {
  const file = paths.serverLog();
  if (logStream) logStream.end();
  for (let i = LOG_KEEP - 1; i >= 1; i--) {
    if (fs.existsSync(`${file}.${i}`)) fs.renameSync(`${file}.${i}`, `${file}.${i + 1}`);
  }
  if (fs.existsSync(file)) fs.renameSync(file, `${file}.1`);
  logStream = fs.createWriteStream(file, { flags: 'a' });
  logBytes = 0;
}

function openLog() {
  fs.mkdirSync(paths.logDir(), { recursive: true });
  const file = paths.serverLog();
  if (fs.existsSync(file) && fs.statSync(file).size > LOG_LIMIT) rotateLogs();
  else if (!logStream) {
    logStream = fs.createWriteStream(file, { flags: 'a' });
    logBytes = fs.existsSync(file) ? fs.statSync(file).size : 0;
  }
}

function log(text) {
  if (!logStream) openLog();
  logStream.write(text);
  logBytes += Buffer.byteLength(text);
  if (logBytes > LOG_LIMIT) rotateLogs();
}

function note(message) {
  log(`[forge desktop ${new Date().toISOString()}] ${message}\n`);
}

function portFree(port) {
  return new Promise((resolve) => {
    const s = net.createServer();
    s.once('error', () => resolve(false));
    s.listen(port, HOST, () => s.close(() => resolve(true)));
  });
}

async function freePort(preferred) {
  if (preferred && (await portFree(preferred))) return preferred;
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.once('error', reject);
    s.listen(0, HOST, () => {
      const { port } = s.address();
      s.close(() => resolve(port));
    });
  });
}

function command(port) {
  if (paths.isDev) {
    const uv = findExecutable('uv');
    if (!uv) throw new Error('uv was not found. Install it from https://docs.astral.sh/uv/ to use dev mode.');
    return { cmd: uv, args: ['run', 'uvicorn', 'forge.server.app:app', '--host', HOST, '--port', String(port)], cwd: paths.repoRoot, group: true };
  }
  const bin = paths.serverBinary();
  if (!fs.existsSync(bin)) throw new Error(`The bundled server is missing at ${bin}.`);
  return { cmd: bin, args: ['--host', HOST, '--port', String(port)], cwd: paths.dataDir(), group: false };
}

// settings: { apiKey, secrets: { NAME: key } } from settings.forServer(). Only keys are passed; every other
// setting is saved by the server itself (web Settings page), so nothing here makes it read only there.
function start(port, settings) {
  fs.mkdirSync(paths.dataDir(), { recursive: true });
  openLog();
  const { cmd, args, cwd, group } = command(port);
  const env = {
    ...process.env,
    PATH: toolPath(),
    FORGE_DATA_DIR: paths.dataDir(),
    FORGE_EXECUTOR: 'docker',
    FORGE_PARENT_PID: String(process.pid),
    FORGE_DESKTOP: '1', // the web Settings page offers Restart and Manage keys through the desktop bridge
    PYTHONUNBUFFERED: '1',
  };
  for (const k of ['ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'FORGE_DEFAULT_MODEL', 'FORGE_GRADER_MODEL',
    ...Object.values(buildinfo.ENV_KEYS)]) {
    if (!paths.isDev) delete env[k];
  }
  Object.assign(env, secretEnv(settings.apiKey, settings.secrets));
  Object.assign(env, buildinfo.serverEnv()); // FORGE_VERSION, FORGE_CHANNEL, FORGE_COMMIT, FORGE_BUILT_AT

  note(`starting ${path.basename(cmd)} ${args.join(' ')} (api key ${settings.apiKey ? 'set' : 'not set'}, ` +
    `${Object.keys(settings.secrets || {}).length} other keys)`);
  child = spawn(cmd, args, { cwd, env, detached: group, stdio: ['ignore', 'pipe', 'pipe'] });
  child.group = group;
  child.stdout.on('data', (d) => log(String(d)));
  child.stderr.on('data', (d) => log(String(d)));
  const me = child;
  child.on('exit', (code, signal) => {
    note(`server exited (code ${code}, signal ${signal})`);
    if (child === me) child = null;
  });
  child.on('error', (err) => note(`server failed to start: ${err.message}`));
  return child;
}

async function waitReady(port, timeoutMs = 90000) {
  const proc = child;
  const deadline = Date.now() + timeoutMs;
  const base = `http://${HOST}:${port}`;
  let probe = '/healthz';
  while (Date.now() < deadline) {
    if (!proc || proc.exitCode !== null || proc.signalCode !== null) {
      throw new Error('The Forge server stopped while starting. Show logs for details.');
    }
    try {
      const res = await fetch(base + probe, { signal: AbortSignal.timeout(3000) });
      if (res.status === 404 && probe === '/healthz') probe = '/v1/templates';
      // 503 from /healthz means the server is up but reports a component problem; it is still usable.
      else if (res.ok || (probe === '/healthz' && res.status === 503)) {
        note(`server ready on ${base} (${probe} ${res.status})`);
        return base;
      }
    } catch {
      // not listening yet
    }
    await new Promise((r) => setTimeout(r, 400));
  }
  throw new Error(`The Forge server did not become ready within ${Math.round(timeoutMs / 1000)} seconds.`);
}

async function stop() {
  const proc = child;
  if (!proc) return;
  note('stopping server');
  await stopProcess(proc, { group: proc.group });
}

function pid() {
  return child ? child.pid : null;
}

module.exports = { HOST, freePort, start, waitReady, stop, pid, note };
