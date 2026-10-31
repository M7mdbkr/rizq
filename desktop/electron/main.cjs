'use strict';
const { app, BrowserWindow, dialog, session } = require('electron');
const path = require('node:path');
const { PORT, startBackend } = require('./backend.cjs');

const origin = `http://127.0.0.1:${PORT}`;
const root = path.resolve(__dirname, '..', '..');
let backend, mainWindow, exiting = false;
app.setName('Rizq');

function openWindow() {
  mainWindow = new BrowserWindow({ width: 1280, height: 820, minWidth: 900, minHeight: 600,
    title: 'رزق | Rizq', backgroundColor: '#18211b',
    webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true } });
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  mainWindow.webContents.on('will-navigate', (event, url) => {
    try { if (new URL(url).origin !== origin) event.preventDefault(); }
    catch { event.preventDefault(); }
  });
  mainWindow.loadURL(`${origin}/pos/`);
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => {
    if (mainWindow) { if (mainWindow.isMinimized()) mainWindow.restore(); mainWindow.focus(); }
  });
  app.whenReady().then(async () => {
    session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    try {
      backend = startBackend(root, app.getPath('userData'));
      await backend.ready;
      backend.child.on('exit', () => {
        if (!exiting) { dialog.showErrorBox('Rizq', 'The local agent stopped. Please restart Rizq.'); app.quit(); }
      });
      openWindow();
    } catch (err) {
      dialog.showErrorBox('Rizq could not start', String(err.message || err));
      app.quit();
    }
  });
  app.on('window-all-closed', () => app.quit());
  app.on('before-quit', () => { exiting = true; if (backend && !backend.child.killed) backend.child.kill(); });
}
