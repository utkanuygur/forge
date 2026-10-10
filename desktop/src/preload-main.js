// Main window bridge for the web Settings page (web/src/settingsApi.ts desktopBridge): open the native
// Settings window for API keys, and restart the server after restart settings change.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('forgeDesktop', {
  openKeySettings: () => ipcRenderer.send('main:openKeySettings'),
  restartServer: () => ipcRenderer.invoke('main:restartServer'),
});
