// Child process helpers: a PATH that GUI apps can use, and clean shutdown.
const fs = require('fs');
const os = require('os');
const path = require('path');

// macOS GUI apps start with a minimal PATH, so look where Docker, Homebrew and uv usually live.
const TOOL_DIRS = [
  '/usr/local/bin',
  '/opt/homebrew/bin',
  '/Applications/Docker.app/Contents/Resources/bin',
  path.join(os.homedir(), '.docker', 'bin'),
  path.join(os.homedir(), '.local', 'bin'),
  path.join(os.homedir(), '.cargo', 'bin'),
];
const SYSTEM_DIRS = ['/usr/bin', '/bin', '/usr/sbin', '/sbin'];

function toolPath() {
  const dirs = [...TOOL_DIRS, ...(process.env.PATH || '').split(':'), ...SYSTEM_DIRS].filter(Boolean);
  return [...new Set(dirs)].join(':');
}

function findExecutable(name) {
  for (const dir of toolPath().split(':')) {
    const candidate = path.join(dir, name);
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
      return candidate;
    } catch {
      // keep looking
    }
  }
  return null;
}

// SIGTERM, then SIGKILL after graceMs. With group=true the whole process group is signalled
// (the child must have been spawned with detached: true).
function stopProcess(child, { graceMs = 5000, group = false } = {}) {
  return new Promise((resolve) => {
    if (!child || child.exitCode !== null || child.signalCode !== null) return resolve();
    const signal = (sig) => {
      try {
        if (group) process.kill(-child.pid, sig);
        else child.kill(sig);
      } catch {
        // already gone
      }
    };
    const timer = setTimeout(() => signal('SIGKILL'), graceMs);
    child.once('exit', () => {
      clearTimeout(timer);
      if (group) signal('SIGKILL'); // stragglers left in the group
      resolve();
    });
    signal('SIGTERM');
  });
}

module.exports = { toolPath, findExecutable, stopProcess };
