// Forge desktop: checks Docker, prepares the sandbox image, runs the bundled server on 127.0.0.1
// and shows the Forge UI. See desktop/README.md.
const fs = require('fs');
const path = require('path');
const { app, BrowserWindow, Menu, shell, dialog, ipcMain, clipboard, screen, session } = require('electron');
const paths = require('./paths');

paths.setup();

const docker = require('./docker');
const server = require('./server');
const settings = require('./settings');
const tunnel = require('./tunnel');

const WEBSITE = 'https://utkanuygur.github.io/forge-site/';
const DOCKER_DOWNLOAD = 'https://www.docker.com/products/docker-desktop/';
// FORGE_DESKTOP_TEST=1 (automated checks): windows are never shown or focused, there is no Dock
// icon and no dialogs, so a test run never comes to the foreground. Screenshots use capturePage.
const TEST = process.env.FORGE_DESKTOP_TEST === '1';
const reveal = (w) => { if (!TEST && w && !w.isDestroyed()) w.show(); };
const focus = (w) => { if (!TEST && w && !w.isDestroyed()) w.focus(); };
const message = (opts) => (TEST ? Promise.resolve({ response: -1 }) : dialog.showMessageBox(opts));

const STEPS = ['Check Docker', 'Prepare the sandbox image', 'Start the Forge server', 'Wait for Forge to be ready'];

let mainWin = null;
let startupWin = null;
let settingsWin = null;
let port = null;
let base = null; // http://127.0.0.1:<port> once the server is ready
let bootId = 0;
let ui = null;
let tunnelStarting = false;

if (!app.requestSingleInstanceLock()) {
  app.quit();
}

// ---------------------------------------------------------------- startup window

function sendUi() {
  if (startupWin && !startupWin.isDestroyed()) startupWin.webContents.send('startup:update', ui);
}

function setStep(i, state, detail = '') {
  ui.steps[i] = { ...ui.steps[i], state, detail };
  sendUi();
}

function showProblem(problem) {
  ui.problem = problem;
  showStartupWindow();
  sendUi();
}

function showStartupWindow() {
  if (startupWin && !startupWin.isDestroyed()) {
    reveal(startupWin);
    return;
  }
  const w = new BrowserWindow({
    width: 480,
    height: 340,
    resizable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    title: 'Starting Forge',
    titleBarStyle: 'hiddenInset',
    backgroundColor: '#f6f5f2',
    show: false,
    webPreferences: secureWebPreferences('preload-startup.js'),
  });
  startupWin = w;
  w.loadFile(paths.ui('startup.html'));
  w.webContents.once('did-finish-load', sendUi);
  w.once('ready-to-show', () => reveal(w));
  w.on('closed', () => {
    if (startupWin === w) startupWin = null;
    if (!mainWin) app.quit();
  });
}

function closeStartupWindow() {
  if (startupWin && !startupWin.isDestroyed()) {
    const w = startupWin;
    startupWin = null;
    w.close();
  }
}

const STARTUP_ACTIONS = {
  retry: () => boot(),
  getDocker: () => shell.openExternal(DOCKER_DOWNLOAD),
  openDocker: () => shell.openPath('/Applications/Docker.app'),
  showLogs: () => showLogs(),
  quit: () => app.quit(),
};

ipcMain.on('startup:action', (e, id) => {
  if (!startupWin || e.sender !== startupWin.webContents) return;
  if (Object.hasOwn(STARTUP_ACTIONS, id)) STARTUP_ACTIONS[id]();
});

function dockerProblem(result) {
  const installed = docker.desktopInstalled();
  if (result.reason === 'missing' && !installed) {
    return {
      title: 'Forge needs Docker Desktop',
      message: 'Every agent run happens in its own Docker container, so Forge needs Docker Desktop installed and running. Install it, start it once, then click Retry.',
      detail: '',
      actions: [{ id: 'quit', label: 'Quit' }, { spacer: true }, { id: 'retry', label: 'Retry' }, { id: 'getDocker', label: 'Get Docker Desktop', primary: true }],
    };
  }
  return {
    title: 'Docker Desktop is not running',
    message: 'Forge runs every agent in its own Docker container. Start Docker Desktop, wait until it says it is running, then click Retry.',
    detail: result.detail,
    actions: [{ id: 'quit', label: 'Quit' }, { spacer: true }, { id: 'retry', label: 'Retry', primary: !installed }, ...(installed ? [{ id: 'openDocker', label: 'Open Docker Desktop', primary: true }] : [{ id: 'getDocker', label: 'Get Docker Desktop' }])],
  };
}

// ---------------------------------------------------------------- boot

async function boot({ title = 'Starting Forge' } = {}) {
  const id = ++bootId;
  const current = () => id === bootId;
  ui = { title, steps: STEPS.map((label) => ({ label, state: 'pending', detail: '' })), problem: null };
  showStartupWindow();
  sendUi();

  setStep(0, 'active');
  const d = await docker.check();
  if (!current()) return false;
  if (!d.ok) {
    setStep(0, 'error');
    server.note(`docker check failed: ${d.reason} ${d.detail}`);
    showProblem(dockerProblem(d));
    return false;
  }
  setStep(0, 'done', `Docker ${d.version}`);

  setStep(1, 'active');
  if (!(await docker.imageExists())) {
    setStep(1, 'active', 'Building the sandbox image. This happens once and takes a few minutes.');
    server.note('building the forge-sandbox image');
    try {
      await docker.buildImage(paths.dockerfileDir(), (line) => {
        server.note(`docker build: ${line}`);
        if (current()) setStep(1, 'active', line.slice(0, 160));
      });
    } catch (err) {
      if (!current()) return false;
      setStep(1, 'error');
      showProblem({
        title: 'The sandbox image could not be built',
        message: 'Forge builds its sandbox image the first time it starts. Check that Docker Desktop is running and has network access, then try again.',
        detail: err.message,
        actions: [{ id: 'showLogs', label: 'Show Logs' }, { spacer: true }, { id: 'quit', label: 'Quit' }, { id: 'retry', label: 'Retry', primary: true }],
      });
      return false;
    }
  }
  if (!current()) return false;
  setStep(1, 'done', `Image ${docker.IMAGE} is ready`);

  setStep(2, 'active');
  try {
    await server.stop();
    const previous = port;
    port = await server.freePort(port);
    if (previous && previous !== port && tunnel.running()) await stopTunnel();
    base = null;
    server.start(port, settings.forServer());
  } catch (err) {
    setStep(2, 'error');
    showProblem({
      title: 'The Forge server could not start',
      message: err.message,
      detail: '',
      actions: [{ id: 'showLogs', label: 'Show Logs' }, { spacer: true }, { id: 'quit', label: 'Quit' }, { id: 'retry', label: 'Retry', primary: true }],
    });
    return false;
  }
  setStep(2, 'done', `127.0.0.1:${port}`);

  setStep(3, 'active');
  try {
    base = await server.waitReady(port);
  } catch (err) {
    if (!current()) return false;
    setStep(3, 'error');
    await server.stop();
    showProblem({
      title: 'Forge did not start',
      message: err.message,
      detail: `Server log: ${paths.serverLog()}`,
      actions: [{ id: 'showLogs', label: 'Show Logs' }, { spacer: true }, { id: 'quit', label: 'Quit' }, { id: 'retry', label: 'Retry', primary: true }],
    });
    return false;
  }
  if (!current()) return false;
  setStep(3, 'done');

  if (mainWin && !mainWin.isDestroyed()) mainWin.loadURL(base);
  else createMainWindow();
  closeStartupWindow();
  buildMenu();
  return true;
}

// ---------------------------------------------------------------- main window

function secureWebPreferences(preload) {
  return {
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
    webSecurity: true,
    backgroundThrottling: !TEST,
    spellcheck: false,
    ...(preload ? { preload: path.join(__dirname, preload) } : {}),
  };
}

function loadWindowState() {
  try {
    const s = JSON.parse(fs.readFileSync(paths.windowStateFile(), 'utf8'));
    const area = screen.getDisplayMatching(s).workArea;
    const visible = s.x < area.x + area.width && s.x + s.width > area.x && s.y < area.y + area.height && s.y + s.height > area.y;
    return visible ? s : { width: s.width, height: s.height };
  } catch {
    return {};
  }
}

function saveWindowState() {
  if (!mainWin || mainWin.isDestroyed() || mainWin.isMinimized() || mainWin.isFullScreen()) return;
  try {
    fs.writeFileSync(paths.windowStateFile(), JSON.stringify(mainWin.getNormalBounds()));
  } catch {
    // not important enough to interrupt anything
  }
}

function createMainWindow() {
  const state = loadWindowState();
  mainWin = new BrowserWindow({
    width: 1360,
    height: 880,
    minWidth: 1024,
    minHeight: 700,
    ...state,
    title: 'Forge',
    show: false,
    backgroundColor: '#f6f5f2',
    webPreferences: secureWebPreferences(null),
  });
  let saveTimer = null;
  const queueSave = () => {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveWindowState, 400);
  };
  mainWin.on('resize', queueSave);
  mainWin.on('move', queueSave);
  mainWin.on('close', saveWindowState);
  mainWin.on('closed', () => {
    mainWin = null;
  });
  mainWin.once('ready-to-show', () => {
    reveal(mainWin);
    if (settings.forWindow().firstRun) openSettings();
  });
  mainWin.loadURL(base);
}

// Only the local server origin and our own bundled pages may load inside the app.
function allowedInApp(url) {
  try {
    const u = new URL(url);
    if (u.protocol === 'file:') return path.resolve(decodeURIComponent(u.pathname)).startsWith(path.join(__dirname, 'ui') + path.sep);
    return Boolean(base) && u.origin === new URL(base).origin;
  } catch {
    return false;
  }
}

function openExternal(url) {
  try {
    const u = new URL(url);
    if (['http:', 'https:', 'mailto:'].includes(u.protocol)) shell.openExternal(url);
  } catch {
    // ignore malformed links
  }
}

app.on('web-contents-created', (_e, contents) => {
  const guard = (event, url) => {
    if (!allowedInApp(url)) {
      event.preventDefault();
      openExternal(url);
    }
  };
  contents.on('will-navigate', guard);
  contents.on('will-redirect', guard);
  contents.on('will-attach-webview', (event) => event.preventDefault());
  contents.setWindowOpenHandler(({ url }) => {
    openExternal(url);
    return { action: 'deny' };
  });
});

// ---------------------------------------------------------------- settings

function openSettings() {
  if (settingsWin && !settingsWin.isDestroyed()) {
    focus(settingsWin);
    return;
  }
  settingsWin = new BrowserWindow({
    width: 520,
    height: 536,
    resizable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    title: 'Settings',
    titleBarStyle: 'hiddenInset',
    backgroundColor: '#f6f5f2',
    show: false,
    parent: undefined,
    webPreferences: secureWebPreferences('preload-settings.js'),
  });
  settingsWin.loadFile(paths.ui('settings.html'));
  settingsWin.once('ready-to-show', () => reveal(settingsWin));
  settingsWin.on('closed', () => {
    settingsWin = null;
    settings.dismissFirstRun();
  });
}

function fromSettings(e) {
  if (!settingsWin || e.sender !== settingsWin.webContents) throw new Error('not allowed');
}

ipcMain.handle('settings:get', (e) => {
  fromSettings(e);
  return settings.forWindow();
});

ipcMain.handle('settings:save', async (e, values) => {
  fromSettings(e);
  try {
    settings.save({
      apiKey: typeof values.apiKey === 'string' ? values.apiKey : '',
      clearKey: values.clearKey === true,
      defaultModel: typeof values.defaultModel === 'string' ? values.defaultModel : '',
      graderModel: typeof values.graderModel === 'string' ? values.graderModel : '',
    });
  } catch (err) {
    return { ok: false, error: err.message };
  }
  const ok = await boot({ title: 'Restarting Forge' });
  focus(settingsWin);
  return ok ? { ok: true } : { ok: false, error: 'Saved, but the server did not restart. See the Forge window for details.' };
});

ipcMain.handle('settings:test', async (e, typed) => {
  fromSettings(e);
  const key = String(typed || '').trim() || settings.apiKey();
  if (!key) return { ok: false, message: 'Enter a key to test.' };
  try {
    const res = await fetch('https://api.anthropic.com/v1/models', {
      headers: { 'x-api-key': key, 'anthropic-version': '2023-06-01' },
      signal: AbortSignal.timeout(15000),
    });
    const body = await res.json().catch(() => ({}));
    if (res.ok) {
      const n = Array.isArray(body.data) ? body.data.length : 0;
      return { ok: true, message: `The key works. ${n} models are available to it.` };
    }
    const reason = (body.error && body.error.message) || res.statusText;
    return { ok: false, message: `The key was rejected (HTTP ${res.status}): ${reason}` };
  } catch (err) {
    return { ok: false, message: `Could not reach api.anthropic.com: ${err.message}` };
  }
});

ipcMain.on('settings:close', (e) => {
  if (settingsWin && e.sender === settingsWin.webContents) settingsWin.close();
});

// ---------------------------------------------------------------- public link

async function startTunnel() {
  if (!base) {
    message({ type: 'info', message: 'Forge is still starting', detail: 'Try again once the Forge window is open.' });
    return;
  }
  if (!tunnel.binary()) {
    const r = await message({
      type: 'info',
      message: 'Public links need cloudflared',
      detail: 'Install it with Homebrew by running this command in Terminal:\n\nbrew install cloudflared\n\nThen choose Share > Start Public Link again.',
      buttons: ['Copy Command', 'OK'],
      defaultId: 1,
    });
    if (r.response === 0) clipboard.writeText('brew install cloudflared');
    return;
  }
  tunnelStarting = true;
  buildMenu();
  try {
    await tunnel.start(port);
    tunnelStarting = false;
    buildMenu();
    showTunnelDialog();
  } catch (err) {
    tunnelStarting = false;
    buildMenu();
    message({ type: 'warning', message: 'The public link could not start', detail: err.message });
  }
}

async function showTunnelDialog() {
  const url = tunnel.url();
  if (!url) return;
  const r = await message({
    type: 'info',
    message: 'Your public link is ready',
    detail:
      `${url}\n\n` +
      'The builder stays on this Mac only. Through this link people can use share links and the API with an agent key, nothing else.\n\n' +
      `To share an agent, copy its share link in Forge and replace ${base} with the address above. The link works while Forge is open.`,
    buttons: ['Copy Link', 'Done'],
    defaultId: 0,
  });
  if (r.response === 0) clipboard.writeText(url);
}

async function stopTunnel() {
  await tunnel.stop();
  buildMenu();
}

// ---------------------------------------------------------------- menus

function showLogs() {
  const log = paths.serverLog();
  if (fs.existsSync(log)) shell.openPath(log);
  else shell.openPath(paths.logDir());
}

function buildMenu() {
  const running = tunnel.running() && Boolean(tunnel.url());
  const template = [
    {
      label: 'Forge',
      submenu: [
        { role: 'about', label: 'About Forge' },
        { type: 'separator' },
        { label: 'Settings…', accelerator: 'CmdOrCtrl+,', click: openSettings },
        { type: 'separator' },
        { role: 'services' },
        { type: 'separator' },
        { role: 'hide', label: 'Hide Forge' },
        { role: 'hideOthers' },
        { role: 'unhide' },
        { type: 'separator' },
        { role: 'quit', label: 'Quit Forge' },
      ],
    },
    {
      label: 'File',
      submenu: [
        { label: 'Open Data Folder', click: () => { fs.mkdirSync(paths.dataDir(), { recursive: true }); shell.openPath(paths.dataDir()); } },
        { type: 'separator' },
        { role: 'close' },
      ],
    },
    { role: 'editMenu' },
    { role: 'viewMenu' },
    {
      label: 'Share',
      submenu: [
        { label: tunnelStarting ? 'Starting Public Link…' : 'Start Public Link', enabled: !running && !tunnelStarting, click: startTunnel },
        { label: 'Show Public Link', enabled: running, click: showTunnelDialog },
        { label: 'Stop Public Link', enabled: running, click: stopTunnel },
      ],
    },
    { role: 'windowMenu' },
    {
      role: 'help',
      submenu: [
        { label: 'Show Logs', click: showLogs },
        { label: 'Forge Website', click: () => shell.openExternal(WEBSITE) },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ---------------------------------------------------------------- lifecycle

let cleanedUp = false;
let cleaning = false;

app.on('before-quit', (e) => {
  if (cleanedUp) return;
  e.preventDefault();
  if (cleaning) return;
  cleaning = true;
  bootId++; // cancel any boot in progress
  Promise.allSettled([tunnel.stop(), server.stop(), docker.stopBuild()]).then(() => {
    cleanedUp = true;
    app.quit();
  });
});

for (const sig of ['SIGTERM', 'SIGINT', 'SIGHUP']) process.on(sig, () => app.quit());

app.on('second-instance', () => {
  const w = mainWin || startupWin;
  if (w) {
    if (w.isMinimized() && !TEST) w.restore();
    focus(w);
  }
});

app.on('window-all-closed', () => {
  // macOS convention: the app stays running until Quit. The server keeps running too.
});

app.on('activate', () => {
  if (!mainWin && base) createMainWindow();
  else if (!mainWin && !startupWin) boot();
});

app.whenReady().then(() => {
  if (TEST) {
    app.setActivationPolicy('accessory');
    if (app.dock) app.dock.hide();
  }
  app.setAboutPanelOptions({ applicationName: 'Forge', applicationVersion: app.getVersion(), website: WEBSITE });
  if (paths.isDev && app.dock) app.dock.setIcon(path.join(__dirname, '..', 'assets', 'icon.png'));
  const allow = new Set(['clipboard-sanitized-write']);
  session.defaultSession.setPermissionRequestHandler((_wc, perm, cb) => cb(allow.has(perm)));
  session.defaultSession.setPermissionCheckHandler((_wc, perm) => allow.has(perm));
  buildMenu();
  boot();
});
