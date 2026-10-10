// Which build of Forge this is. Packaged: the version is the app's own (electron-builder's
// extraMetadata.version in releases, e.g. 0.1.3 or 0.1.4-preview), commit and build time come from
// Resources/build-info.json. Dev: channel "dev" and the checkout's commit; the server resolves the rest
// from git itself (forge/buildinfo.py).
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { app } = require('electron');
const paths = require('./paths');

const ENV_KEYS = { version: 'FORGE_VERSION', commit: 'FORGE_COMMIT', built_at: 'FORGE_BUILT_AT', channel: 'FORGE_CHANNEL' };

const text = (v) => (typeof v === 'string' && v.trim() ? v.trim() : null);
const channelFor = (version) => (String(version).includes('-preview') ? 'preview' : 'release');

function readFile() {
  try {
    return JSON.parse(fs.readFileSync(path.join(process.resourcesPath, 'build-info.json'), 'utf8'));
  } catch {
    return {};
  }
}

function gitCommit() {
  try {
    return text(execFileSync('git', ['rev-parse', '--short', 'HEAD'], { cwd: paths.repoRoot, encoding: 'utf8', timeout: 5000 }));
  } catch {
    return null;
  }
}

let cached = null;

function get() {
  if (cached) return cached;
  if (paths.isDev) {
    cached = { version: null, commit: gitCommit(), built_at: null, channel: 'dev' };
  } else {
    const file = readFile();
    const version = app.getVersion();
    cached = { version, commit: text(file.commit), built_at: text(file.built_at), channel: channelFor(version) };
  }
  return cached;
}

// Env for the packaged server. Dev servers run from the checkout and resolve their own build info.
function serverEnv() {
  if (paths.isDev) return {};
  const info = get();
  const env = {};
  for (const [k, name] of Object.entries(ENV_KEYS)) if (info[k]) env[name] = info[k];
  return env;
}

function windowTitle() {
  const { version } = get();
  return version ? `Forge ${version}` : 'Forge Dev';
}

function aboutDetail() {
  const info = get();
  let built = 'unknown';
  if (info.built_at) {
    const d = new Date(info.built_at);
    built = Number.isNaN(d.getTime()) ? info.built_at : d.toLocaleString();
  }
  return [
    `Version: ${info.version || 'dev'}`,
    `Channel: ${info.channel}`,
    `Commit: ${info.commit || 'unknown'}`,
    `Built: ${built}`,
  ].join('\n');
}

module.exports = { ENV_KEYS, get, serverEnv, windowTitle, aboutDetail, channelFor };
