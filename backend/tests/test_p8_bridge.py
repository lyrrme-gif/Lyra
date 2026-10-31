"""Phase 8: the real ui_bridge.Handler serves /p8/ and keeps foreign web pages out. Offline.
Run on its own:  python -m unittest tests.test_p8_bridge
"""
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ["LYRA_DATA_DIR"] = tempfile.mkdtemp(prefix="lyra-p8br-")

try:
    import ui_bridge
    _ERR = None
except Exception as e:                                   # pragma: no cover - missing optional deps on this machine
    ui_bridge, _ERR = None, e


class _Headers(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)


def _call(method, path, origin=None, body=b"{}"):
    class Fake(ui_bridge.Handler):
        def __init__(self):
            self.path, self.command = path, method
            self.headers = _Headers({"Content-Length": str(len(body))})
            if origin:
                self.headers["Origin"] = origin
            self.rfile, self.wfile, self.codes = io.BytesIO(body), io.BytesIO(), []

        def send_response(self, code, message=None):
            self.codes.append(code)

        def send_header(self, *a):
            pass

        def end_headers(self):
            pass
    f = Fake()
    getattr(f, "do_" + method)()
    return f.codes[0], json.loads(f.wfile.getvalue() or b"{}")


@unittest.skipIf(ui_bridge is None, f"ui_bridge not importable here: {_ERR}")
class Bridge(unittest.TestCase):
    def test_p8_is_a_guarded_prefix(self):
        self.assertIn("/p8/", ui_bridge._GUARDED)

    def test_own_windows_and_curl_are_served(self):
        for origin in ("tauri://localhost", "http://tauri.localhost", "http://localhost:1420", None):
            code, obj = _call("GET", "/p8/status", origin)
            self.assertEqual(code, 200, origin)
            self.assertFalse(obj["enabled"])                 # opt-in: off by default

    def test_foreign_web_pages_are_refused_on_every_route(self):
        for origin in ("https://evil.example", "http://127.0.0.1:9999", "null"):
            self.assertEqual(_call("GET", "/p8/status", origin)[0], 403, origin)
            for route in ("config", "check", "download", "cancel", "skip", "install"):
                self.assertEqual(_call("POST", f"/p8/{route}", origin, b'{"confirm": true}')[0], 403, (origin, route))

    def test_install_route_never_runs_without_a_verified_download(self):
        code, obj = _call("POST", "/p8/install", "tauri://localhost", b'{"confirm": true, "version": "9.9.9", "current": "1.0.0"}')
        self.assertEqual((code, obj["ok"], obj["error"]["code"]), (200, False, "not_ready"))

    def test_bad_repo_is_a_400_not_a_crash(self):
        self.assertEqual(_call("POST", "/p8/config", "tauri://localhost", b'{"repo": "../../etc"}')[0], 400)


if __name__ == "__main__":
    unittest.main()
