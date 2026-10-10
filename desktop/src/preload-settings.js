// Settings window bridge: read settings (never the key itself), save, and test a key.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('forgeSettings', {
  get: () => ipcRenderer.invoke('settings:get'),
  save: (values) => ipcRenderer.invoke('settings:save', values),
  test: (apiKey) => ipcRenderer.invoke('settings:test', String(apiKey || '')),
  close: () => ipcRenderer.send('settings:close'),
});
