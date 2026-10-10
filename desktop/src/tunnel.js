// Public link through a Cloudflare quick tunnel (cloudflared tunnel --url ...).
const { spawn } = require('child_process');
const { toolPath, findExecutable, stopProcess } = require('./proc');
const server = require('./server');

const URL_RE = /https:\/\/[a-z0-9-]+\.trycloudflare\.com/;
let child = null;
let url = null;

function binary() {
  return findExecutable('cloudflared');
}

function start(port) {
  return new Promise((resolve, reject) => {
    const bin = binary();
    if (!bin) return reject(Object.assign(new Error('cloudflared is not installed.'), { code: 'MISSING' }));
    const tail = [];
    child = spawn(bin, ['tunnel', '--no-autoupdate', '--url', `http://${server.HOST}:${port}`], {
      env: { ...process.env, PATH: toolPath() },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    const me = child;
    const timer = setTimeout(() => {
      stop();
      reject(new Error(`No public link after 45 seconds.\n${tail.slice(-5).join('\n')}`));
    }, 45000);
    const onData = (d) => {
      const text = String(d);
      tail.push(...text.split('\n').filter(Boolean));
      if (tail.length > 20) tail.splice(0, tail.length - 20);
      const m = text.match(URL_RE);
      if (m && !url) {
        url = m[0];
        clearTimeout(timer);
        server.note(`public link started: ${url}`);
        resolve(url);
      }
    };
    child.stdout.on('data', onData);
    child.stderr.on('data', onData);
    child.on('error', (err) => {
      clearTimeout(timer);
      reject(err);
    });
    child.on('exit', (code) => {
      clearTimeout(timer);
      if (child === me) {
        child = null;
        url = null;
      }
      reject(new Error(`cloudflared exited with code ${code}\n${tail.slice(-5).join('\n')}`));
    });
  });
}

async function stop() {
  const proc = child;
  child = null;
  url = null;
  if (proc) {
    server.note('stopping public link');
    await stopProcess(proc);
  }
}

module.exports = { start, stop, binary, running: () => Boolean(child), url: () => url };
