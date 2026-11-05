"""Shared fakes for the Phase 0 tests (no real keychain, no network, no microphone)."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path


from engine.core import secrets_store  # noqa: E402


class FakeKeyring:
    """In-memory stand-in for the keyring adapter. `fail_set` / `drop_writes` simulate trouble."""

    def __init__(self):
        self.d = {}
        self.fail_set = False
        self.fail_get = False
        self.drop_writes = False        # accepts the write but stores nothing (verification must catch it)
        self.max_len = None             # like Credential Manager's blob limit

    def get(self, service, user):
        if self.fail_get:
            raise RuntimeError("locked")
        return self.d.get((service, user))

    def set(self, service, user, value):
        if self.fail_set:
            raise RuntimeError("boom " + value)      # the message holds the secret on purpose
        if self.max_len and len(value) > self.max_len:
            raise ValueError("too long")
        if not self.drop_writes:
            self.d[(service, user)] = value

    def delete(self, service, user):
        self.d.pop((service, user), None)

    def values(self):
        return list(self.d.values())


class P0Case(unittest.TestCase):
    """Temp data dir + fake keychain for every test; restores everything afterwards."""

    use_keychain = True

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lyra-p0-"))
        self._env = os.environ.get("LYRA_DATA_DIR")
        os.environ["LYRA_DATA_DIR"] = str(self.tmp)
        self.kr = FakeKeyring()
        secrets_store._use_adapter_for_tests(self.kr if self.use_keychain else None)
        secrets_store._BOOT = "test-boot-1"

    def tearDown(self):
        secrets_store._reset_for_tests()
        if self._env is None:
            os.environ.pop("LYRA_DATA_DIR", None)
        else:
            os.environ["LYRA_DATA_DIR"] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)

    # helpers
    def write_json(self, name, obj):
        p = self.tmp / name
        p.write_text(json.dumps(obj, indent=2), encoding="utf-8")
        return p

    def read_json(self, path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def all_disk_text(self):
        """Everything that is on disk in the data dir, as one string."""
        out = []
        for p in self.tmp.rglob("*"):
            if p.is_file():
                out.append(p.read_text(encoding="utf-8", errors="ignore"))
        return "\n".join(out)
