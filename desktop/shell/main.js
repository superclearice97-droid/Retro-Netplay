'use strict';
// Retro-Netplay's desktop window. It starts the engine (engine.py, or the packaged Retro-Netplay-Engine), waits for
// the line "RETRO_NETPLAY_UI <address>" and shows that page. The page can only reach the engine on this computer;
// links to other sites open in the normal browser.
const { app, BrowserWindow, dialog, ipcMain, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

app.setName('Retro-Netplay');
// An AppImage can't use Chromium's setuid sandbox helper, and Ubuntu 24.04+ blocks the user-namespace sandbox for apps
// without an AppArmor profile, so Electron would refuse to start there. This window only shows the engine's own page
// on this computer (other sites open in the normal browser), so in that one case it runs without Chromium's sandbox.
if (process.env.APPIMAGE) {
  try {
    if (fs.readFileSync('/proc/sys/kernel/apparmor_restrict_unprivileged_userns', 'utf8').trim() === '1') app.commandLine.appendSwitch('no-sandbox');
  } catch {}
}
let engine, win, origin, quitting = false;
const logFile = path.join(app.getPath('userData'), 'engine.log');

function engineCommand() {
  if (app.isPackaged) {
    const exe = path.join(process.resourcesPath, 'engine', process.platform === 'win32' ? 'Retro-Netplay-Engine.exe' : 'Retro-Netplay-Engine');
    return [exe, ['--no-browser']];
  }
  const py = process.env.RETRO_NETPLAY_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  return [py, [path.join(__dirname, '..', 'engine.py'), '--no-browser']];
}

function startEngine() {
  return new Promise((resolve, reject) => {
    const [cmd, args] = engineCommand();
    fs.mkdirSync(path.dirname(logFile), { recursive: true });
    const log = fs.createWriteStream(logFile, { flags: 'w', mode: 0o600 });
    engine = spawn(cmd, args, { stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
    let buf = '';
    const timer = setTimeout(() => reject(new Error('The engine took too long to start.')), 30000);
    engine.stdout.on('data', d => {
      log.write(d);
      buf += d.toString();
      const m = buf.match(/RETRO_NETPLAY_UI (http:\/\/127\.0\.0\.1:\d+\/#t=[\w-]+)/);
      if (m) { clearTimeout(timer); resolve(m[1]); }
    });
    engine.stderr.on('data', d => log.write(d));
    engine.on('error', e => { clearTimeout(timer); reject(e); });
    engine.on('exit', code => {
      clearTimeout(timer);
      if (!quitting) {
        dialog.showErrorBox('Retro-Netplay stopped', `The engine stopped (code ${code}). Details: ${logFile}`);
        app.quit();
      }
      reject(new Error(`The engine stopped (code ${code}).`));
    });
  });
}

function openLink(url) {      // netplay:// links: hand them to the page, which knows what to do
  if (win && typeof url === 'string' && url.startsWith('netplay://')) {
    win.webContents.executeJavaScript(`window.dispatchEvent(new CustomEvent('netplay-link', {detail: ${JSON.stringify(url)}}))`);
    win.show(); win.focus();
  }
}

async function start() {
  const url = await startEngine();
  origin = new URL(url).origin;
  win = new BrowserWindow({ width: 1360, height: 880, minWidth: 380, minHeight: 600, backgroundColor: '#0d1110',
    title: 'Retro-Netplay', icon: path.join(__dirname, '..', 'icon-256.png'), autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, sandbox: true, nodeIntegration: false } });
  win.webContents.on('will-navigate', (e, u) => { if (new URL(u).origin !== origin) { e.preventDefault(); if (/^https:\/\//.test(u)) shell.openExternal(u); } });
  win.webContents.setWindowOpenHandler(({ url: u }) => { if (/^https:\/\//.test(u)) shell.openExternal(u); return { action: 'deny' }; });
  win.webContents.session.setPermissionRequestHandler((_wc, perm, cb) => cb(perm === 'clipboard-sanitized-write'));
  await win.loadURL(url);
  const link = process.argv.find(a => a.startsWith('netplay://'));
  if (link) openLink(link);
}

ipcMain.handle('pick-folder', async () => {
  const r = await dialog.showOpenDialog(win, { title: 'Where are your games?', properties: ['openDirectory'] });
  return r.canceled ? '' : r.filePaths[0];
});

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.setAsDefaultProtocolClient('netplay');
  app.on('second-instance', (_e, argv) => { if (win) { win.show(); win.focus(); } openLink(argv.find(a => a.startsWith('netplay://'))); });
  app.on('open-url', (e, u) => { e.preventDefault(); openLink(u); });
  app.whenReady().then(start).catch(e => { dialog.showErrorBox('Retro-Netplay could not start', `${e.message}\nDetails: ${logFile}`); app.quit(); });
  app.on('window-all-closed', () => app.quit());
  app.on('before-quit', () => { quitting = true; if (engine && engine.exitCode === null) engine.kill(); });
}
