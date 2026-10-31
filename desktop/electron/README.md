# Rizq Electron desktop development

This is a desktop shell, like Alfred's Electron shell, around Rizq's existing
POS and Python agent. It starts its own loopback-only agent server and uses a
fixed origin so its local POS database stays associated with the same address.
The Electron database is separate from Safari, Chrome, and the pywebview edition;
no existing POS data is migrated automatically.

Use Python 3.9 or newer and install Electron 40.2.1 in this directory, then run
`npm start`. For development on the current Mac, the already-installed Electron
runtime in the local Alfred checkout can run this folder without another install.
Double-click `desktop/تشغيل رزق كتطبيق.command`, or run:

```sh
"$HOME/Projects/alfred/node_modules/.bun/electron@40.2.1/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron" desktop/electron
```

Closing the desktop window stops its agent server. The source development shell
requires Python to be installed; distributable installers and bundled Python are
not yet implemented or verified. Code modification is disabled in desktop mode.
The app's renderer has no Node integration and restricts navigation to its own
server. API keys entered in the POS are held in server memory until exit.
