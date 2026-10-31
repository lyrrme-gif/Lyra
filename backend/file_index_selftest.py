"""Self-test for file_index.py.  Run from the backend folder:  python file_index_selftest.py
Uses a temporary data folder and a temporary document folder; touches none of your real data.
Needs pypdf, python-docx, python-pptx (reading) and reportlab (only to make the test PDF)."""
import os, sys, tempfile, time, types, shutil
tmp = tempfile.mkdtemp(prefix="lyra_fi_")
os.environ["LYRA_DATA_DIR"] = os.path.join(tmp, "data")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

pushed, activities = [], []
fake = types.ModuleType("ui_bridge")
fake.push_search_results = lambda q, rows, provider="", card=None: pushed.append((q, rows, provider))
fake.push_activity = lambda *a, **k: activities.append((a, k))
fake.push_search_dismiss = lambda: None
sys.modules["ui_bridge"] = fake

import settings_store, file_index as fi, yt_island

docs = os.path.join(tmp, "docs"); os.makedirs(os.path.join(docs, "skipme")); os.makedirs(os.path.join(docs, "node_modules"))
def w(rel, text):
    p = os.path.join(docs, rel); open(p, "w", encoding="utf-8").write(text); return p
w("fourier_notes.txt", "The Fourier transform decomposes a signal into its frequencies.\n" * 3)
w("study.md", "# Study plan\nRevise linear algebra eigenvalues before friday.")
w("llm_keys.txt", "sk-secret-123 hunter2phrase")
w("skipme/secret.txt", "excludedphrase appears here")
w("node_modules/dep.txt", "dependencyphrase appears here")
w("notes.json", '{"x":"jsonphrase"}')
from reportlab.pdfgen import canvas
c = canvas.Canvas(os.path.join(docs, "report.pdf")); c.drawString(72, 720, "Quantum entanglement experiment results"); c.save()
import docx
d = docx.Document(); d.add_paragraph("Photosynthesis efficiency in chloroplasts"); docx_path = os.path.join(docs, "thesis.docx"); d.save(docx_path)
from pptx import Presentation
pr = Presentation(); s = pr.slides.add_slide(pr.slide_layouts[1]); s.shapes.title.text = "Gradient descent"; s.placeholders[1].text = "learning rate schedule"
pr.save(os.path.join(docs, "deck.pptx"))

settings_store.set_many({"index_folders": [docs], "index_exclude": ["skipme"]})
fails = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond: fails.append(name)

calls = []
orig = fi.extract_text
fi.extract_text = lambda p: (calls.append(os.path.basename(p)), orig(p))[1]

s1 = fi.scan(throttle=False)
check("first scan indexes txt, md, pdf, docx, pptx", s1["indexed"] == 5 and s1["errors"] == 0, s1)
top = lambda q: (fi.search_files(q) or [{"title": None}])[0]["title"]
check("phrase inside PDF found", top("quantum entanglement") == "report.pdf")
check("phrase inside DOCX found", top("photosynthesis chloroplasts") == "thesis.docx")
check("phrase inside PPTX found", top("gradient descent") == "deck.pptx")
check("txt found by topic", top("fourier transform") == "fourier_notes.txt")
check("llm_keys*, excluded, node_modules, json never indexed",
      not any(fi.search_files(q) for q in ("hunter2phrase", "excludedphrase", "dependencyphrase", "jsonphrase")))

calls.clear(); time.sleep(0.05)
s2 = fi.scan(throttle=False)
check("rescan with no change reads nothing", s2["indexed"] == 0 and s2["unchanged"] == 5 and not calls, (s2, calls))

d = docx.Document(); d.add_paragraph("Mitochondria produce ATP"); d.save(docx_path)
os.utime(docx_path, (time.time() + 5, time.time() + 5))
calls.clear(); s3 = fi.scan(throttle=False)
check("after editing one file only that file is read", calls == ["thesis.docx"] and s3["indexed"] == 1 and s3["unchanged"] == 4, (calls, s3))
check("new text found, old text gone", top("mitochondria atp") == "thesis.docx" and not fi.search_files("photosynthesis"))

os.remove(os.path.join(docs, "study.md"))
s4 = fi.scan(throttle=False)
check("deleted file is dropped", s4["removed"] == 1 and not fi.search_files("eigenvalues"), s4)

settings_store.set_many({"index_exclude": ["skipme", "*.pptx"]})
s5 = fi.scan(throttle=False)
check("newly excluded file is dropped", s5["removed"] == 1 and not fi.search_files("gradient descent"), s5)

# commands
r = fi.try_file_search("Hey Lyra, find my notes about Fourier transform please")
check("find command replies and pushes provider=files rows with the path as url",
      r and r.startswith("I found 1 file") and pushed and pushed[-1][2] == "files" and pushed[-1][1][0]["url"].endswith("fourier_notes.txt"), (r, pushed[-1:] ))
check("search my files for", (fi.try_file_search("search my files for quantum") or "").startswith("I found"))
check("find X in my documents", (fi.try_file_search("find mitochondria in my documents") or "").startswith("I found"))
check("no match message", fi.try_file_search("find files about zzzzqqq") == "I couldn't find any files about zzzzqqq.")
check("unrelated command is not handled", fi.try_file_search("what time is it") is None and fi.try_file_search("open spotify") is None)
fi.try_file_search("find my notes about Fourier transform")
yt_island.remember(pushed[-1][1])
check("island 'open the first one' path would receive the file path", yt_island._last[0]["url"].endswith("fourier_notes.txt"))
opened = []
fi.subprocess.Popen = lambda a, *x, **k: opened.append(a)
check("own 'open the first one' opens the file", (fi.try_file_search("open the first one") or "").startswith("Opening") and opened and opened[0][1].endswith("fourier_notes.txt"), opened)

# answer from files (the LLM call is replaced by a stub: only retrieval + source naming are tested)
seen_prompt = {}
def stub(q, hits):
    seen_prompt["names"] = [h["name"] for h in hits]; return "Entanglement was observed."
fi._llm_complete = stub
a = fi.try_file_search("answer from my files: what happened in the quantum experiment?")
check("answer uses retrieved files and names the source", "report.pdf" in seen_prompt.get("names", []) and a.endswith("Sources: report.pdf."), (a, seen_prompt))

# endpoints
st, body = fi.http("POST", "/files/folders/add", {"path": "/"}); check("drive/root refused", st == 400, body)
st, body = fi.http("POST", "/files/folders/add", {"path": os.path.join(tmp, "nope")}); check("missing folder refused", st == 400, body)
other = os.path.join(tmp, "other"); os.makedirs(other)
st, body = fi.http("POST", "/files/folders/add", {"path": other}); check("add folder", st == 200 and other in [f["path"] for f in body["folders"]], body)
st, body = fi.http("POST", "/files/folders/remove", {"path": other}); check("remove folder", st == 200 and other not in [f["path"] for f in body["folders"]], body)
check("status", fi.http("GET", "/files/status", {})[1]["files"] == 3)
settings_store.set_many({"index_folders": []}); fi.scan(throttle=False)
check("removing every folder empties the index", fi.http("GET", "/files/status", {})[1]["files"] == 0)

shutil.rmtree(tmp, ignore_errors=True)
print("\nALL PASSED" if not fails else f"\n{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
