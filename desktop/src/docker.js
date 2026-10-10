// Docker checks and the first-run sandbox image build.
const fs = require('fs');
const { execFile, spawn } = require('child_process');
const { toolPath, findExecutable, stopProcess } = require('./proc');

// The server reads the same variable, so both always agree on the image name.
const IMAGE = process.env.FORGE_SANDBOX_IMAGE || 'forge-sandbox';
const SIMULATE_MISSING = process.env.FORGE_DESKTOP_SIMULATE_NO_DOCKER === '1';
let buildChild = null;

function dockerBinary() {
  return SIMULATE_MISSING ? null : findExecutable('docker');
}

function desktopInstalled() {
  return !SIMULATE_MISSING && fs.existsSync('/Applications/Docker.app');
}

function run(args, timeout) {
  return new Promise((resolve) => {
    execFile(dockerBinary(), args, { timeout, env: { ...process.env, PATH: toolPath() } }, (err, stdout, stderr) => {
      resolve({ ok: !err, stdout: String(stdout).trim(), stderr: String(stderr).trim(), err });
    });
  });
}

// { ok: true, version } | { ok: false, reason: 'missing' | 'stopped', detail }
async function check() {
  if (!dockerBinary()) return { ok: false, reason: 'missing', detail: 'The docker command was not found.' };
  const r = await run(['info', '--format', '{{.ServerVersion}}'], 20000);
  if (r.ok && r.stdout) return { ok: true, version: r.stdout };
  const detail = (r.stderr || r.stdout || (r.err && r.err.message) || '').split('\n').find(Boolean) || '';
  return { ok: false, reason: 'stopped', detail };
}

async function imageExists() {
  return (await run(['image', 'inspect', IMAGE], 20000)).ok;
}

// Builds the image from the bundled Dockerfile, calling onLine with each progress line.
function buildImage(contextDir, onLine) {
  return new Promise((resolve, reject) => {
    const tail = [];
    buildChild = spawn(dockerBinary(), ['build', '--progress=plain', '-t', IMAGE, contextDir], {
      env: { ...process.env, PATH: toolPath() },
    });
    const onData = (chunk) => {
      for (const line of String(chunk).split('\n').map((l) => l.trim()).filter(Boolean)) {
        tail.push(line);
        if (tail.length > 30) tail.shift();
        onLine(line);
      }
    };
    buildChild.stdout.on('data', onData);
    buildChild.stderr.on('data', onData);
    buildChild.on('error', reject);
    buildChild.on('exit', (code) => {
      buildChild = null;
      if (code === 0) resolve();
      else reject(new Error(`docker build exited with code ${code}\n${tail.slice(-8).join('\n')}`));
    });
  });
}

function stopBuild() {
  return stopProcess(buildChild);
}

module.exports = { IMAGE, desktopInstalled, check, imageExists, buildImage, stopBuild };
