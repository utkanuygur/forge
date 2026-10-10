// Where everything lives. FORGE_DESKTOP_USERDATA moves settings, data and logs to another folder
// (used for testing so the real settings stay untouched). Call setup() before app ready.
const path = require('path');
const { app } = require('electron');

const isDev = process.env.FORGE_DESKTOP_DEV === '1' || !app.isPackaged;
const repoRoot = path.resolve(__dirname, '..', '..');
const override = process.env.FORGE_DESKTOP_USERDATA;

// Dev mode uses its own name, so its settings folder and Keychain item stay apart from the real app.
function setup() {
  app.setName(isDev ? 'Forge Dev' : 'Forge');
  if (isDev) app.setPath('userData', path.join(app.getPath('appData'), 'Forge Dev'));
  if (override) app.setPath('userData', path.resolve(override));
}

const p = {
  isDev,
  repoRoot,
  userData: () => app.getPath('userData'),
  dataDir: () => path.join(app.getPath('userData'), 'data'),
  logDir: () => (override ? path.join(app.getPath('userData'), 'logs') : path.join(app.getPath('home'), 'Library', 'Logs', isDev ? 'Forge Dev' : 'Forge')),
  serverLog: () => path.join(p.logDir(), 'server.log'),
  settingsFile: () => path.join(app.getPath('userData'), 'settings.json'),
  windowStateFile: () => path.join(app.getPath('userData'), 'window-state.json'),
  serverBinary: () => path.join(process.resourcesPath, 'forge-server', 'forge-server'),
  dockerfileDir: () => (isDev ? path.join(repoRoot, 'sandbox') : path.join(process.resourcesPath, 'sandbox')),
  ui: (name) => path.join(__dirname, 'ui', name),
};

module.exports = { ...p, setup };
