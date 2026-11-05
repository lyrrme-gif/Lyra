"""Phase 0 tests: secrets never reach the log. From the backend folder:
    python -m unittest tests.test_secrets_logging -v"""
import io
import logging
import sys
import unittest


from engine.tests.core._p0_helpers import P0Case  # noqa: E402
import engine.core.secrets_store as ss  # noqa: E402
from engine.brain import llm_store  # noqa: E402

KEY = "gsk_LEAKTEST0123456789abcdef"


class Capture:
    """A logger with the redaction filter and an in-memory stream, like ui.log with its handlers."""

    def __init__(self, name):
        self.stream = io.StringIO()
        self.logger = logging.getLogger(name)
        self.logger.handlers.clear()
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        h = logging.StreamHandler(self.stream)
        h.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        self.logger.addHandler(h)
        ss.install_log_filter(self.logger)

    def text(self):
        return self.stream.getvalue()


class RedactFunction(unittest.TestCase):
    def setUp(self):
        ss._known.clear()

    def test_known_value_is_masked(self):
        ss._remember("a-very-private-value-42")
        self.assertEqual(ss.redact("using a-very-private-value-42 now"), "using *** now")

    def test_common_formats_are_masked_without_being_known(self):
        samples = [
            "sk-or-v1-abcdef0123456789abcdef", "gsk_abcdef0123456789abcdef", "AIzaSyA-abcdefghijklmnopqrstuv",
            "Authorization: Bearer abcdefghijklmnop12345678", "api_key=hunter2hunter2",
            'headers={"token": "abcdef123456"}', "client_secret: s3cr3t-value",
        ]
        for s in samples:
            out = ss.redact(f"request failed {s} (retrying)")
            for frag in ("abcdef0123456789abcdef", "hunter2hunter2", "abcdef123456", "s3cr3t-value",
                         "abcdefghijklmnop12345678", "abcdefghijklmnopqrstuv"):
                self.assertNotIn(frag, out, s)
            self.assertIn("***", out)

    def test_ordinary_text_is_untouched(self):
        s = "Opening Spotify and setting the volume to 40 percent"
        self.assertEqual(ss.redact(s), s)
        self.assertEqual(ss.redact("ok"), "ok")

    def test_short_values_are_not_registered(self):
        ss._remember("short")
        self.assertEqual(ss.redact("a short answer"), "a short answer")


class LogFilter(P0Case):
    def test_message_args_and_exceptions_are_masked(self):
        ss.set("groq_api_key", KEY)
        cap = Capture("lyra.test.filter")
        cap.logger.info("calling groq with key %s", KEY)
        cap.logger.warning("literal %s" % KEY)
        try:
            raise RuntimeError(f"401 for {KEY}")
        except RuntimeError:
            cap.logger.exception("request failed")
        out = cap.text()
        self.assertNotIn(KEY, out)
        self.assertIn("***", out)
        self.assertIn("request failed", out)

    def test_setting_a_secret_protects_the_main_lyra_logger(self):
        lg = logging.getLogger("lyra")
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        lg.addHandler(handler)
        old_level = lg.level
        lg.setLevel(logging.DEBUG)
        try:
            ss._filter_installed = False
            ss.set("groq_api_key", KEY)                      # first secret installs the filter
            lg.info("debug dump: %s", {"groq": KEY})
        finally:
            lg.removeHandler(handler)
            lg.setLevel(old_level)
            lg.removeFilter(ss._FILTER)
        self.assertNotIn(KEY, stream.getvalue())

    def test_install_is_idempotent(self):
        lg = logging.getLogger("lyra.test.idem")
        ss.install_log_filter(lg)
        ss.install_log_filter(lg)
        self.assertEqual(lg.filters.count(ss._FILTER), 1)


class ModuleItselfNeverLogsValues(P0Case):
    def _capture_lyra(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        lg = logging.getLogger("lyra")
        lg.addHandler(handler)
        lg.setLevel(logging.DEBUG)
        self.addCleanup(lg.removeHandler, handler)
        return stream

    def test_keychain_errors_log_name_and_type_only(self):
        stream = self._capture_lyra()
        self.kr.fail_set = True                               # the fake's error message CONTAINS the value
        ss.set("groq_api_key", KEY)
        self.kr.fail_set = False
        text = stream.getvalue()
        self.assertIn("groq_api_key", text)
        self.assertIn("RuntimeError", text)
        self.assertNotIn(KEY, text)

    def test_failed_migration_never_logs_values(self):
        stream = self._capture_lyra()
        p = self.write_json("llm_keys.json", {"groq_api_key": KEY})
        self.kr.fail_set = True
        ss.migrate_json_file("llm", p, ["groq_api_key"])
        self.assertNotIn(KEY, stream.getvalue())

    def test_successful_migration_logs_names_not_values(self):
        stream = self._capture_lyra()
        p = self.write_json("llm_keys.json", {"groq_api_key": KEY})
        ss.migrate_json_file("llm", p, ["groq_api_key"])
        text = stream.getvalue()
        self.assertIn("groq_api_key", text)
        self.assertNotIn(KEY, text)

    def test_state_and_index_files_hold_no_values(self):
        p = self.write_json("llm_keys.json", {"groq_api_key": KEY})
        ss.migrate_json_file("llm", p, ["groq_api_key"])
        for name in ("secrets_migration.json", "secrets_index.json"):
            self.assertNotIn(KEY, (self.tmp / name).read_text())
        self.assertNotIn(KEY, str(ss.migration_state()))

    def test_llm_store_save_and_load_do_not_log_values(self):
        stream = self._capture_lyra()
        old = llm_store.PATH
        llm_store.PATH = self.tmp / "llm_keys.json"
        llm_store._cache = None
        try:
            llm_store.save({"groq_api_key": KEY})
            llm_store.load()
        finally:
            llm_store.PATH = old
            llm_store._cache = None
        self.assertNotIn(KEY, stream.getvalue())

    def test_cli_status_prints_no_values(self):
        ss.set("groq_api_key", KEY)
        buf = io.StringIO()
        old, sys.stdout = sys.stdout, buf
        try:
            ss._cli(["status"])
        finally:
            sys.stdout = old
        self.assertIn("groq_api_key", buf.getvalue())
        self.assertNotIn(KEY, buf.getvalue())


if __name__ == "__main__":
    unittest.main()
