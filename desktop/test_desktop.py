import importlib.util
import os
from pathlib import Path
import runpy
import sqlite3
import tempfile
import types
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("rizq_desktop", Path(__file__).with_name("main.py"))
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)


class DesktopTests(unittest.TestCase):
    def test_platform_data_paths(self):
        home = Path("/test-user")
        self.assertEqual(desktop.app_data_dir("darwin", {}, home), home / "Library/Application Support/Rizq")
        self.assertEqual(desktop.app_data_dir("win32", {"LOCALAPPDATA": "/local"}, home), Path("/local/Rizq"))
        self.assertEqual(desktop.app_data_dir("linux", {"XDG_DATA_HOME": "/data"}, home), Path("/data/rizq"))

    def test_window_uses_fixed_origin_and_persistent_storage(self):
        gui = types.SimpleNamespace(settings={}, create_window=mock.Mock(), start=mock.Mock())
        server = mock.Mock()
        fake_package = types.ModuleType("rizq_agent")
        fake_package.web = types.SimpleNamespace(Handler=object(), load_dotenv=mock.Mock())
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch.dict("sys.modules", {"webview": gui, "rizq_agent": fake_package}), \
             mock.patch.object(desktop, "app_data_dir", return_value=Path(tmp)), \
             mock.patch.object(desktop, "ThreadingHTTPServer", return_value=server) as create_server:
            desktop.main()
            create_server.assert_called_once_with(("127.0.0.1", desktop.PORT), fake_package.web.Handler)
            self.assertEqual(gui.create_window.call_args.args[1], "http://127.0.0.1:47192/pos/")
            self.assertFalse(gui.start.call_args.kwargs["private_mode"])
            self.assertEqual(gui.start.call_args.kwargs["storage_path"], str(Path(tmp) / "webview"))
            self.assertEqual(os.environ["RIZQ_RESULTS_DIR"], str(Path(tmp) / "results"))
            fake_package.web.load_dotenv.assert_called_once()
            server.shutdown.assert_called_once()
            server.server_close.assert_called_once()

    def test_occupied_port_never_opens_another_origin(self):
        gui = types.SimpleNamespace(settings={}, create_window=mock.Mock(), start=mock.Mock())
        fake_package = types.ModuleType("rizq_agent")
        fake_package.web = types.SimpleNamespace(Handler=object(), load_dotenv=mock.Mock())
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch.dict("sys.modules", {"webview": gui, "rizq_agent": fake_package}), \
             mock.patch.object(desktop, "app_data_dir", return_value=Path(tmp)), \
             mock.patch.object(desktop, "ThreadingHTTPServer", side_effect=OSError("busy")):
            desktop.main()
        self.assertNotIn("http://127.0.0.1", str(gui.create_window.call_args))
        self.assertIn("47192", gui.create_window.call_args.kwargs["html"])

    def test_packaging_fixture_contains_schema_without_developer_rows(self):
        analysis = mock.Mock(side_effect=lambda *args, **kwargs: types.SimpleNamespace(
            pure=[], scripts=[], binaries=[], datas=kwargs["datas"]))
        with mock.patch("sys.platform", "linux"):
            built = runpy.run_path(str(Path(__file__).with_name("Rizq.spec")), init_globals={
                "SPECPATH": str(Path(__file__).parent), "Analysis": analysis,
                "PYZ": lambda *_: None, "EXE": lambda *a, **k: None,
                "COLLECT": lambda *a, **k: None, "BUNDLE": lambda *a, **k: None,
            })
        fixture = next(Path(src) for src, dst in built["datas"] if dst == "agent/runtime/fixtures")
        with sqlite3.connect(str(fixture)) as db:
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            self.assertIn("orders", tables)
            self.assertIn("users", tables)
            for table in tables:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM "' + table.replace('"', '""') + '"').fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
