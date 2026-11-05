"""Phase 2 test helper: every store and setting redirected to a temp folder / dict (no real data,
no network, no microphone). From the backend folder:  python -m unittest discover -s tests -p "test_p2_*.py" -v"""
import contextlib
import pathlib
import shutil
import tempfile
import unittest


from engine.voice import dictation_clean  # noqa: E402
from engine.voice import dictionary_store  # noqa: E402
from engine.voice import history_store  # noqa: E402


@contextlib.contextmanager
def isolated(settings=None):
    """Temp dictionary / history / snippets files and an in-memory settings dict. Yields (tmp, settings)."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="lyra-p2-"))
    sn = dictionary_store.snippets_module()
    saved = (dictionary_store._path_override, dictionary_store._cache, history_store._path_override,
             history_store._last_purge, sn._file, dictation_clean._settings_get, dictation_clean._settings_set)
    store = dict(settings or {})
    try:
        dictionary_store._path_override, dictionary_store._cache = tmp / "dictionary.json", None
        history_store._path_override, history_store._last_purge = tmp / "history.db", 0.0
        sn._file = lambda: tmp / "snippets.json"
        dictation_clean._settings_get = store.get
        dictation_clean._settings_set = store.update
        yield tmp, store
    finally:
        (dictionary_store._path_override, dictionary_store._cache, history_store._path_override,
         history_store._last_purge, sn._file, dictation_clean._settings_get, dictation_clean._settings_set) = saved
        shutil.rmtree(tmp, ignore_errors=True)


class EnvSelfTest(unittest.TestCase):
    def test_isolation_restores_everything(self):
        before = dictation_clean._settings_get
        with isolated({"p2_output": "paste"}) as (tmp, store):
            self.assertEqual(dictation_clean.pref("p2_output"), "paste")
            self.assertTrue(str(history_store._db_file()).startswith(str(tmp)))
        self.assertIs(dictation_clean._settings_get, before)


if __name__ == "__main__":
    unittest.main()
