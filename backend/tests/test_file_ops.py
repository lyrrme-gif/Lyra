"""Create files/folders (default Desktop, or the place said), find them by name, open them."""
import os
import sys
import time
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.modules.setdefault("ui", types.SimpleNamespace(log=__import__("logging").getLogger("t"), trace=lambda *a, **k: None))
import importlib.util  # noqa: E402

# Loaded straight from its file: importing the `actions` package would pull in the whole voice / Windows stack.
_spec = importlib.util.spec_from_file_location("file_ops_under_test", os.path.join(os.path.dirname(__file__), "..", "actions", "file_ops.py"))
fo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fo)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    dirs = {n: tmp_path / n.capitalize() for n in ("desktop", "documents", "downloads", "pictures", "music", "videos")}
    for d in dirs.values():
        d.mkdir()
    monkeypatch.setattr(fo, "known_dir", lambda name: str(dirs.get(fo._ALIASES.get(name.lower().strip(), name.lower().strip()), "")) if
                        fo._ALIASES.get(name.lower().strip(), name.lower().strip()) in dirs else "")
    monkeypatch.setattr(fo, "_remembered", lambda alias: None)
    monkeypatch.setattr(fo, "_roots_primary", lambda: [str(d) for d in dirs.values()])
    monkeypatch.setattr(fo, "_roots_wide", lambda: [])
    monkeypatch.setattr(fo, "reveal_in_explorer", lambda p: opened.append(("reveal", p)))
    monkeypatch.setattr(fo, "_show_on_island", lambda q, rows: None)
    monkeypatch.setattr(fo, "_dismiss_island", lambda: None)
    monkeypatch.setattr(fo, "_undo_hint", lambda p: None)
    monkeypatch.setattr(fo.subprocess, "Popen", lambda cmd, **kw: opened.append(("popen", cmd)))
    monkeypatch.setattr(fo, "_last", [])
    opened.clear()
    return dirs


opened: list = []


def run(cmd):
    return fo.try_file_ops(cmd)


# ── create: no place = Desktop ──────────────────────────────────────────────────────────────

def test_bat_file_without_a_place_goes_on_the_desktop(home):
    r = run("create a bat file called backup")
    assert (home["desktop"] / "backup.bat").is_file()
    assert "backup.bat" in r and "Desktop" in r
    assert (home["desktop"] / "backup.bat").read_bytes() == b"@echo off\r\n"


def test_spoken_dot_and_extension_in_name(home):
    run("make a file called run dot bat")
    assert (home["desktop"] / "run.bat").is_file()
    run("create a file called notes.md")
    assert (home["desktop"] / "notes.md").is_file()


def test_place_said_means_that_place(home):
    r = run("create a python file called hello in documents")
    assert (home["documents"] / "hello.py").is_file() and "Documents" in r
    assert not (home["desktop"] / "hello.py").exists()
    run("make a folder called school stuff on downloads")
    assert (home["downloads"] / "school stuff").is_dir()


def test_place_before_the_name(home):
    run("create a folder in documents called Projects")
    assert (home["documents"] / "Projects").is_dir()


def test_full_path_is_created_if_missing(home, tmp_path):
    target = tmp_path / "D" / "projects" / "lab"
    r = run(f"create a folder called lab in {tmp_path / 'D' / 'projects'}")
    assert target.is_dir()
    assert any(o[0] == "reveal" for o in opened)          # the place was named: show it


def test_existing_folder_by_name_is_found(home):
    (home["documents"] / "Uni").mkdir()
    run("create a text file called todo in uni")
    assert (home["documents"] / "Uni" / "todo.txt").is_file()


def test_unknown_place_is_asked_not_guessed(home):
    r = run("create a file called x in nowhereland")
    assert "couldn't find a folder called nowhereland" in r
    assert not (home["desktop"] / "x.txt").exists()


def test_content_goes_in_the_file(home):
    run("create a text file called shopping that says buy milk")
    assert (home["desktop"] / "shopping.txt").read_text() == "buy milk\n"


def test_no_name_gets_a_free_default(home):
    run("make a new folder")
    run("make a new folder")
    assert (home["desktop"] / "New Folder").is_dir() and (home["desktop"] / "New Folder (2)").is_dir()
    run("create a bat file")
    assert (home["desktop"] / "New File.bat").is_file()


def test_never_overwrites(home):
    (home["desktop"] / "a.txt").write_text("keep me")
    r = run("create a file called a")
    assert "already" in r and (home["desktop"] / "a.txt").read_text() == "keep me"


def test_bad_names_and_system_places_refused(home):
    assert "characters" in run("create a file called a:b")
    assert "protected" in create_in_protected()


def create_in_protected():
    return fo.create_item("file", "x", r"C:\Windows")[1]


def test_word_document(home):
    pytest.importorskip("docx")
    run("create a word document called essay")
    assert (home["desktop"] / "essay.docx").is_file()


def test_coding_requests_are_left_to_the_ai(home):
    for c in ("make a python script that scrapes a website", "create a file for my homework notes",
              "create a document about climate change", "make a folder to keep my photos organised"):
        assert run(c) is None, c


# ── find ────────────────────────────────────────────────────────────────────────────────────

def test_find_by_name_and_extension(home):
    (home["documents"] / "Budget 2026.xlsx").write_text("x")
    (home["downloads"] / "budget.pdf").write_text("x")
    (home["downloads"] / "holiday.jpg").write_text("x")
    r = run("find the file called budget")
    assert "2 matches" in r
    assert {x["name"] for x in fo._last} == {"Budget 2026.xlsx", "budget.pdf"}
    r = run("find budget.pdf")
    assert "budget.pdf" in r and "Downloads" in r
    r = run("where is my budget pdf file")
    assert "budget.pdf" in r


def test_find_folder_and_misspelling(home):
    (home["pictures"] / "Holiday Photos").mkdir()
    assert "Holiday Photos" in run("find the folder holiday photos")
    assert "Holiday Photos" in run("find the folder holyday photos")        # a speech-to-text slip
    assert "couldn't find" in run("find the file called zzzqqq")


def test_content_search_is_not_stolen(home):
    assert run("find files about thermodynamics") is None
    assert run("find all files in downloads") is None


# ── open ────────────────────────────────────────────────────────────────────────────────────

def test_open_by_name_opens_the_file(home, monkeypatch):
    (home["documents"] / "thesis.docx").write_text("x")
    monkeypatch.setattr(fo.sys, "platform", "linux")
    r = run("open the file thesis")
    assert "thesis.docx" in r and opened and opened[-1][0] == "popen" and opened[-1][1][-1].endswith("thesis.docx")
    opened.clear()
    run("open thesis.docx")
    assert opened


def test_programs_and_scripts_are_shown_never_run(home):
    (home["desktop"] / "backup.bat").write_text("x")
    r = run("open backup.bat")
    assert "instead of running it" in r
    assert opened == [("reveal", str(home["desktop"] / "backup.bat"))]


def test_equal_matches_are_asked(home):
    (home["documents"] / "plan.txt").write_text("x")
    (home["downloads"] / "plan.txt").write_text("x")
    r = run("open the file plan")
    assert "equally well" in r and not opened
    r = run("open the second one")
    assert "Opening plan.txt" in r and opened


def test_open_it_after_a_single_find(home):
    (home["videos"] / "lecture.mp4").write_text("x")
    run("find the file lecture")
    assert "Opening lecture.mp4" in run("open it")


def test_open_it_with_no_recent_search_falls_through(home):
    assert run("open it") is None


def test_open_a_known_folder(home):
    r = run("open the downloads folder")
    assert "Downloads" in r and opened


def test_unknown_file_is_reported(home):
    assert "couldn't find a file called nothingthere" in run("open the file nothingthere")
    assert run("open google.com") is None


def test_top_of_another_drive_is_allowed_but_not_the_system_drive(monkeypatch):
    monkeypatch.setenv("SystemDrive", "C:")
    assert fo._protected("D:\\") is False
    assert fo._protected("C:\\") is True
    assert fo._protected(r"C:\Windows\System32") is True
