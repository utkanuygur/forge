// Startup window bridge: receive status updates, trigger one of the offered actions.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('forgeStartup', {
  onUpdate: (fn) => ipcRenderer.on('startup:update', (_e, state) => fn(state)),
  action: (id) => ipcRenderer.send('startup:action', String(id)),
});
