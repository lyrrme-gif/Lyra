"""Tests for actions/dev.py (Step 8). Run from the backend folder:

    python -m unittest tests.test_dev -v

Uses the REAL permissions.PermissionManager (with stub `ui` and `voice` modules), real git and real
subprocesses in temporary folders. Nothing touches the real data folder or the network.
"""
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

# ── stub modules the real app provides ───────────────────────────────────────────────────────
CHAT: list = []
ACTIVITY: list = []
SPOKEN: list = []

_ui = types.ModuleType("ui")
_ui.log = logging.getLogger("lyra-test")
_ui.INPUT_MODE = "text"
_ui.trace = lambda m: None
_ui.set_state = lambda s: None
_ui.print_chat = lambda role, text, detail="": CHAT.append((role, text))
sys.modules["ui"] = _ui

_voice = types.ModuleType("voice")
_voice.speak = lambda text, *a, **k: SPOKEN.append(text)
sys.modules["voice"] = _voice

_bridge = types.ModuleType("ui_bridge")
_bridge.push_activity = lambda kind, title, **kw: ACTIVITY.append((kind, title, kw.get("phase"), kw.get("subtitle")))
sys.modules["ui_bridge"] = _bridge

_settings = types.ModuleType("settings_store")
_settings.ON = True
_settings.feature_on = lambda name: _settings.ON
sys.modules["settings_store"] = _settings

_brain = types.ModuleType("brain")
PROMPTS: list = []
_brain.ask_brain = lambda prompt, history, intent="x": (PROMPTS.append(prompt) or "1. It means X.\n2. Do Y.")
sys.modules["brain"] = _brain

# The real `actions` package pulls in Windows-only code, so load only actions/dev.py under a stub package.
_pkg = types.ModuleType("actions")
_pkg.__path__ = [str(BACKEND / "actions")]
sys.modules["actions"] = _pkg
_files = types.ModuleType("actions.files"); _files.resolve_location = lambda raw: None
_system = types.ModuleType("actions.system"); _system.get_clipboard_text = lambda: ""
sys.modules["actions.files"] = _files
sys.modules["actions.system"] = _system
_tools = types.ModuleType("tools"); _tools.__path__ = []
_registry = types.ModuleType("tools.registry"); _registry.get_tool = lambda tid: None
sys.modules["tools"] = _tools
sys.modules["tools.registry"] = _registry

from actions import dev  # noqa: E402
from permissions import PermissionManager  # noqa: E402

GIT = shutil.which("git")


def _wait_idle(timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if dev._running is None:
            time.sleep(0.1)
            return True
        time.sleep(0.05)
    return False


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lyra-dev-test-")).resolve()
        self.cfg = self.tmp / "dev_config.json"
        self._orig_cfg = dev._cfg_path
        dev._cfg_path = lambda: self.cfg
        dev._last_output = None
        dev._running = None
        _settings.ON = True
        CHAT.clear(); ACTIVITY.clear(); SPOKEN.clear(); PROMPTS.clear()
        import permissions
        permissions._pending_text_confirmation = None
        self.pm = PermissionManager()
        self.asked: list = []
        orig = dev._confirm
        self._orig_confirm = orig

    def tearDown(self):
        dev._cfg_path = self._orig_cfg
        dev._confirm = self._orig_confirm
        shutil.rmtree(self.tmp, ignore_errors=True)

    # typed flow exactly like main.py: first pass queues, the typed "yes" re-runs with confirmed=True
    def typed(self, command):
        ctx = {"confirm_callback": lambda cmd=command: dev.try_dev(cmd, {"confirmed": True})}
        return dev.try_dev(command, ctx)

    def say_yes(self):
        return PermissionManager.resolve_text_confirmation("yes")

    def say_no(self):
        return PermissionManager.resolve_text_confirmation("no")


class Routing(Base):
    def test_not_ours_returns_none(self):
        for phrase in ["open chrome", "open notes.txt in code", "what is the error rate of this model",
                       "run macro hello", "run routine morning", "hello there", "play some music",
                       "explain this screen", "delete the file test.txt"]:
            self.assertIsNone(dev.try_dev(phrase, {}), phrase)

    def test_feature_switch_off(self):
        _settings.ON = False
        self.assertIsNone(dev.try_dev("git status", {}))
        self.assertIsNone(dev.try_dev("explain this error", {}))

    def test_push_is_refused(self):
        calls = []
        orig = dev._git
        dev._git = lambda *a, **k: (calls.append(a) or (0, ""))
        try:
            for p in ["git push", "push my changes", "push to origin", "git push origin main"]:
                r = dev.try_dev(p, {})
                self.assertIn("never push", r, p)
            self.assertEqual(calls, [])
        finally:
            dev._git = orig

    def test_explain_phrases_match(self):
        for p in ["explain this error", "what does this error mean", "explain the last error",
                  "can you explain this traceback", "explain this python exception", "what is this error message",
                  "explain the error in the terminal", "Hey Lyra, explain this error."]:
            self.assertTrue(dev._EXPLAIN.match(dev._clean(p)), p)


class Projects(Base):
    def test_add_list_remove_open(self):
        proj = self.tmp / "My Project"; proj.mkdir()
        r = dev.try_dev(f"add project lyra at {proj}", {})
        self.assertIn("Added the project lyra", r)
        self.assertIn("lyra", dev.try_dev("list my projects", {}))
        launched = []
        orig_popen, orig_find = subprocess.Popen, dev._find_vscode
        subprocess.Popen = lambda argv, **kw: launched.append(argv) or types.SimpleNamespace()
        dev._find_vscode = lambda: "/fake/Code.exe"
        try:
            for phrase in ["open project lyra in VS Code", "open lyra in vscode", "Open the Lyra project in visual studio code",
                           "open project lyr in vs code"]:
                launched.clear()
                self.assertEqual(dev.try_dev(phrase, {}), "Opening lyra in VS Code.", phrase)
                self.assertEqual(launched, [["/fake/Code.exe", str(proj)]], phrase)
            self.assertIn("don't have a project called zzz", dev.try_dev("open project zzz in vs code", {}))
            self.assertIsNone(dev.try_dev("open zzz in vs code", {}))     # not a known project, no "project" word
        finally:
            subprocess.Popen, dev._find_vscode = orig_popen, orig_find
        self.assertIn("Removed the project lyra", dev.try_dev("remove project lyra", {}))
        self.assertIsNone(dev.try_dev("delete project folder old stuff", {}))
        self.assertIn("haven't added", dev.try_dev("list my projects", {}))

    def test_add_project_bad_folder(self):
        self.assertIn("can't find the folder", dev.try_dev("add project x at /no/such/folder", {}))


class AllowedFolders(Base):
    def test_allow_needs_confirmation(self):
        d = self.tmp / "scripts"; d.mkdir()
        first = self.typed(f"allow scripts in {d}")
        self.assertEqual(first, "Awaiting typed confirmation.")
        self.assertEqual(dev._load()["allowed_folders"], [])          # nothing changed before the yes
        self.assertIn("now allowed", self.say_yes())
        self.assertEqual(dev._load()["allowed_folders"], [str(d)])
        self.assertIn("already allowed", dev.try_dev(f"allow scripts in {d}", {}))

    def test_allow_denied_adds_nothing(self):
        d = self.tmp / "scripts"; d.mkdir()
        self.typed(f"allow scripts in {d}")
        self.assertEqual(self.say_no(), "Okay, cancelled.")
        self.assertEqual(dev._load()["allowed_folders"], [])

    def test_unsafe_folders_refused(self):
        root = os.path.abspath(os.sep)
        self.assertIn("whole drive", dev.try_dev(f"allow scripts in {root}", {}))
        self.assertIn("whole user folder", dev.try_dev(f"allow scripts in {Path.home()}", {}))

    def test_disallow_and_list(self):
        d = self.tmp / "s"; d.mkdir()
        dev._update(lambda c: c["allowed_folders"].append(str(d)))
        self.assertIn(str(d), dev.try_dev("list script folders", {}))
        self.assertIn("won't run scripts", dev.try_dev(f"stop allowing scripts in {d}", {}))
        self.assertIn("No folders are allowed", dev.try_dev("list script folders", {}))


class RunScript(Base):
    def setUp(self):
        super().setUp()
        self.allowed = self.tmp / "allowed"; self.allowed.mkdir()
        self.other = self.tmp / "other"; self.other.mkdir()
        (self.allowed / "hello.py").write_text("print('one')\nprint('two')\nprint('last line here')\n")
        (self.allowed / "boom.py").write_text("import sys\nprint('about to fail')\nraise SystemExit(3)\n")
        (self.allowed / "slow.py").write_text("import time\nprint('start', flush=True)\ntime.sleep(30)\n")
        (self.other / "evil.py").write_text("print('should never run')\n")
        dev._update(lambda c: c["allowed_folders"].append(str(self.allowed)))

    def test_run_asks_then_runs_and_streams(self):
        self.assertEqual(self.typed("run hello.py"), "Awaiting typed confirmation.")
        self.assertIsNone(dev._running)                                # nothing started before the yes
        self.assertEqual(ACTIVITY, [])
        reply = self.say_yes()
        self.assertIn("Running hello.py", reply)
        self.assertTrue(_wait_idle())
        titles = [a[1] for a in ACTIVITY]
        self.assertIn("Running hello.py", titles)
        self.assertIn("hello.py finished", titles)
        self.assertEqual(ACTIVITY[-1][2], "success")
        self.assertEqual(ACTIVITY[-1][3], "last line here")
        self.assertTrue(dev._last_output["ok"])
        self.assertTrue(any("finished" in t for r, t in CHAT))

    def test_run_declined_never_starts(self):
        self.typed("run hello.py")
        self.assertEqual(self.say_no(), "Okay, cancelled.")
        self.assertIsNone(dev._running)
        self.assertEqual(ACTIVITY, [])

    def test_spoken_name_forms(self):
        self.assertEqual(self.typed("run the script hello dot py"), "Awaiting typed confirmation.")
        self.say_no()
        self.assertEqual(self.typed("run hello"), "Awaiting typed confirmation.")   # known stem in the allowed folder
        self.say_no()
        self.assertEqual(self.typed("run script hello"), "Awaiting typed confirmation.")
        self.say_no()

    def test_failure_exit_code_reported(self):
        self.typed("run boom.py"); self.say_yes()
        self.assertTrue(_wait_idle())
        self.assertEqual(ACTIVITY[-1][2], "fail")
        self.assertFalse(dev._last_output["ok"])
        self.assertIn("about to fail", dev._last_output["text"])
        self.assertTrue(any("exit code 3" in t for r, t in CHAT))

    def test_outside_allowed_folder_refused(self):
        r = dev.try_dev(f"run {self.other / 'evil.py'}", {})
        self.assertIn("couldn't find", r)
        self.assertIsNone(dev._running)
        r = dev.try_dev("run evil.py", {})
        self.assertIn("couldn't find", r)

    def test_symlink_escape_refused(self):
        link = self.allowed / "link.py"
        try:
            os.symlink(self.other / "evil.py", link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available")
        r = dev.try_dev("run link.py", {})
        self.assertIn("couldn't find", r)

    def test_dotdot_escape_refused(self):
        r = dev.try_dev(f"run {self.allowed}{os.sep}..{os.sep}other{os.sep}evil.py", {})
        self.assertIn("couldn't find", r)

    def test_not_a_script_extension_is_not_run(self):
        (self.allowed / "notes.txt").write_text("hi")
        self.assertIsNone(dev.try_dev("run notes", {}))
        self.assertIn("couldn't find", dev.try_dev("run notes.txt script", {}))

    def test_no_allowed_folders(self):
        dev._update(lambda c: c.__setitem__("allowed_folders", []))
        self.assertIn("No folders are allowed", dev.try_dev("run hello.py", {}))
        self.assertIsNone(dev.try_dev("run macro hello", {}))

    def test_ambiguous_name(self):
        sub = self.allowed / "sub"; sub.mkdir()
        (sub / "hello.py").write_text("print(1)\n")
        r = dev.try_dev("run hello.py", {})
        self.assertIn("found 2 scripts", r)

    def test_timeout_kills_script(self):
        dev._update(lambda c: c.__setitem__("run_timeout_s", 1))
        self.typed("run slow.py"); self.say_yes()
        self.assertTrue(_wait_idle(20))
        self.assertTrue(any("stopped" in t for r, t in CHAT))
        self.assertFalse(dev._last_output["ok"])

    def test_one_at_a_time(self):
        self.typed("run slow.py"); self.say_yes()
        self.assertIsNotNone(dev._running)
        self.typed("run hello.py")
        r = self.say_yes()
        self.assertIn("still running", r)
        dev._kill_tree(dev._running["proc"])
        self.assertTrue(_wait_idle(20))


@unittest.skipUnless(GIT, "git not installed")
class Git(Base):
    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "repo"; self.repo.mkdir()
        self.g("init", "-q", "-b", "main")
        self.g("config", "user.email", "t@example.com")
        self.g("config", "user.name", "Test")
        self.g("config", "commit.gpgsign", "false")
        (self.repo / "a.txt").write_text("a\n")
        self.g("add", "-A"); self.g("commit", "-q", "-m", "init")
        dev._update(lambda c: c["projects"].__setitem__("demo", str(self.repo)))
        self.git_calls = []
        self._orig_git = dev._git

        def spy(repo, *args, **kw):
            self.git_calls.append(args)
            return self._orig_git(repo, *args, **kw)
        dev._git = spy

    def tearDown(self):
        dev._git = self._orig_git
        super().tearDown()

    def g(self, *a):
        return subprocess.run([GIT, "-C", str(self.repo), *a], capture_output=True, text=True, check=True).stdout.strip()

    def head(self):
        return self.g("rev-parse", "HEAD")

    def test_status_clean_modified_untracked(self):
        r = dev.try_dev("git status", {})
        self.assertIn("demo is on branch main", r); self.assertIn("clean", r)
        (self.repo / "a.txt").write_text("changed\n")
        (self.repo / "new.txt").write_text("n\n")
        r = dev.try_dev("what's the git status for demo", {})
        self.assertIn("1 modified", r); self.assertIn("1 untracked", r); self.assertIn("a.txt", r)

    def test_status_not_a_repo_and_unknown_project(self):
        plain = self.tmp / "plain"; plain.mkdir()
        dev._update(lambda c: c["projects"].__setitem__("plain", str(plain)))
        self.assertIn("isn't a git repository", dev.try_dev("git status for plain", {}))
        self.assertIn("don't have a project called nope", dev.try_dev("git status for nope", {}))

    def test_commit_asks_first_and_commits_tracked_only(self):
        (self.repo / "a.txt").write_text("changed\n")
        (self.repo / "secret.env").write_text("TOKEN=1\n")             # untracked: must stay out
        before = self.head()
        self.assertEqual(self.typed("commit with message fix the thing"), "Awaiting typed confirmation.")
        self.assertEqual(self.head(), before)                          # nothing committed before the yes
        self.assertFalse(any(a and a[0] == "commit" for a in self.git_calls))
        reply = self.say_yes()
        self.assertIn("Committed 1 file in demo", reply); self.assertIn("haven't pushed", reply)
        self.assertNotEqual(self.head(), before)
        self.assertEqual(self.g("log", "-1", "--format=%s"), "fix the thing")
        self.assertNotIn("secret.env", self.g("show", "--name-only", "--format=", "HEAD"))
        self.assertEqual(self.g("status", "--porcelain"), "?? secret.env")

    def test_commit_declined(self):
        (self.repo / "a.txt").write_text("changed\n")
        before = self.head()
        self.typed("git commit with message nope")
        self.assertEqual(self.say_no(), "Okay, cancelled.")
        self.assertEqual(self.head(), before)

    def test_commit_nothing_to_commit_does_not_ask(self):
        (self.repo / "only_untracked.txt").write_text("x\n")
        r = dev.try_dev("commit with message hello", {})
        self.assertIn("Nothing to commit", r); self.assertIn("untracked", r)

    def test_commit_message_edge_cases(self):
        (self.repo / "a.txt").write_text("changed\n")
        self.assertIn("What should the commit message say", dev.try_dev("commit with message", {}))
        before = self.head()
        self.typed('commit with message "--amend; echo pwned"')
        self.say_yes()
        self.assertEqual(self.g("log", "-1", "--format=%s"), "--amend; echo pwned")   # taken literally, no shell
        self.assertNotEqual(self.head(), before)

    def test_never_calls_push(self):
        (self.repo / "a.txt").write_text("changed\n")
        self.typed("commit with message x y z"); self.say_yes()
        dev.try_dev("git status", {}); dev.try_dev("git push", {})
        for a in self.git_calls:
            self.assertNotIn("push", a)


class ExplainError(Base):
    def test_clipboard_error_is_sent_tail_first(self):
        tb = "x" * 9000 + "\nTraceback (most recent call last):\nValueError: bad thing\n"
        orig = dev._clipboard_text
        dev._clipboard_text = lambda: tb
        try:
            r = dev.try_dev("explain this error", {})
        finally:
            dev._clipboard_text = orig
        self.assertIn("1. It means X.", r)
        self.assertIn("ValueError: bad thing", PROMPTS[-1])
        self.assertLessEqual(len(PROMPTS[-1]), dev._ERR_TAIL_CHARS + 700)

    def test_empty_everything(self):
        orig = dev._clipboard_text
        dev._clipboard_text = lambda: ""
        try:
            r = dev.try_dev("explain this error", {})
        finally:
            dev._clipboard_text = orig
        self.assertIn("don't see an error", r)
        self.assertEqual(PROMPTS, [])

    def test_auto_prefers_failed_run_over_unrelated_clipboard(self):
        dev._last_output = {"source": "boom.py", "ok": False, "text": "about to fail\nSystemExit: 3", "ts": time.time()}
        orig = dev._clipboard_text
        dev._clipboard_text = lambda: "https://example.com/some/link"
        try:
            dev.try_dev("explain this error", {})
        finally:
            dev._clipboard_text = orig
        self.assertIn("SystemExit: 3", PROMPTS[-1])

    def test_explicit_clipboard_wins(self):
        dev._last_output = {"source": "boom.py", "ok": False, "text": "RUN OUTPUT", "ts": time.time()}
        orig = dev._clipboard_text
        dev._clipboard_text = lambda: "CLIPBOARD ERROR text"
        try:
            dev.try_dev("explain the error i copied", {})
        finally:
            dev._clipboard_text = orig
        self.assertIn("CLIPBOARD ERROR", PROMPTS[-1]); self.assertNotIn("RUN OUTPUT", PROMPTS[-1])

    def test_ansi_stripped(self):
        orig = dev._clipboard_text
        dev._clipboard_text = lambda: "\x1b[31mError:\x1b[0m boom"
        try:
            dev.try_dev("explain this error", {})
        finally:
            dev._clipboard_text = orig
        self.assertNotIn("\x1b", PROMPTS[-1]); self.assertIn("Error: boom", PROMPTS[-1])


class PermissionIds(Base):
    def test_dev_ids_are_confirm_tier(self):
        pm = PermissionManager()
        for pid in ("dev_run_script", "dev_git_commit", "dev_allow_folder"):
            self.assertNotIn(pid, pm._rules)       # not listed -> falls through to CONFIRM
            ok, why = pm.check_permission(pid, "do a thing", {"confirm_callback": lambda: "x"})
            self.assertFalse(ok); self.assertEqual(why, "Awaiting typed confirmation.")
            self.assertTrue(PermissionManager.pending_text_confirmation())
            PermissionManager.resolve_text_confirmation("no")


if __name__ == "__main__":
    unittest.main(verbosity=2)
