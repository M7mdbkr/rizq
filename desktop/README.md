# Rizq desktop builds

The desktop edition opens the existing Arabic POS and Python agent in a native
desktop window using the operating system's webview. The UI itself is still HTML,
not a rewrite in SwiftUI, WinUI, or GTK widgets. Build separately on macOS,
Windows, and Linux; cross-compilation is not supported by PyInstaller.

From the repository root, use Python 3.10 or newer in a virtual environment:

```sh
python -m pip install -r desktop/requirements.txt
python -m PyInstaller --clean --noconfirm desktop/Rizq.spec
```

On Linux install a supported pywebview GUI backend, for example
`python -m pip install 'pywebview[qt]'` plus its system dependencies. Windows
requires the WebView2 runtime. The output is `dist/Rizq.app` on macOS and the
`dist/Rizq/` application directory on Windows and Linux. Distribution signing,
notarization, and installer creation are not configured.

The app listens on `127.0.0.1:47192` only. The port is intentionally fixed because
the POS database belongs to its browser origin. If that port is busy, the app
stops rather than opening an empty or unrelated database. Webview storage is
persistent and separate from the browser version: existing browser POS data is
not automatically imported. Use the POS backup/export and restore workflow to
migrate only after verifying it on the target platform.

The app's agent state lives in the user's application-data directory, not in the
installed bundle. API keys entered in the app stay in server memory until exit.
The packaged agent fixture contains only the database schema; the developer's
fixture rows are not shipped.
The source-code modification assistant is disabled in the desktop edition because
the packaged source is not an editable developer checkout. Other agent behavior
and approval checks remain the same. Desktop packages and platform-specific
storage, downloads, dialogs, and POS migration must be tested on each target OS
before release.
