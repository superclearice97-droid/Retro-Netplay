'use strict';
// What the interface may ask of the desktop shell: nothing but a folder picker.
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('shell', { pickFolder: () => ipcRenderer.invoke('pick-folder') });
