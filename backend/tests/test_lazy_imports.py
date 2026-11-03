"""openai (and friends) are imported on first use, not at launch."""
import sys
import types
import unittest

import lazy_imports


class Lazy(unittest.TestCase):
    def setUp(self):
        self._saved = sys.modules.pop("openai", None)
        self.made = []
        outer = self

        class FakeOpenAI:
            def __init__(self, **kw):
                outer.made.append(kw)
                self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=lambda **k: ("ok", k)))

        sys.modules["openai"] = types.SimpleNamespace(OpenAI=FakeOpenAI)

    def tearDown(self):
        sys.modules.pop("openai", None)
        if self._saved is not None:
            sys.modules["openai"] = self._saved

    def test_openai_shim_builds_the_real_client_when_called(self):
        c = lazy_imports.OpenAI(base_url="http://x", api_key="k")
        self.assertEqual(self.made, [{"base_url": "http://x", "api_key": "k"}])
        self.assertEqual(c.chat.completions.create(model="m")[0], "ok")

    def test_lazy_client_waits_for_first_use_and_builds_once(self):
        lc = lazy_imports.LazyClient(lambda: lazy_imports.OpenAI(api_key="k"))
        self.assertEqual(self.made, [])                       # nothing built, nothing imported yet
        self.assertEqual(lc.chat.completions.create(model="a")[0], "ok")
        lc.chat.completions.create(model="b")
        self.assertEqual(len(self.made), 1)                   # one real client, reused


if __name__ == "__main__":
    unittest.main()
