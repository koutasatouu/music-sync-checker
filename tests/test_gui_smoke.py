"""
Uji asap GUI memakai Tkinter PALSU (jadi bisa jalan tanpa layar/Tkinter).
Checks app logic (scan, filter, rename, copy, decisions, CSV), NOT the visuals.
Jalankan:  python -m unittest discover -s tests -v
"""
import os
import shutil
import sys
import tempfile
import time
import types
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)


# ------------------------------------------------------------- tkinter palsu
class Var:
    def __init__(self, master=None, value="", **k):
        self._v, self._tr = value, []

    def get(self):
        return self._v

    def set(self, v):
        self._v = v
        for cb in self._tr:
            cb()

    def trace_add(self, mode, cb):
        self._tr.append(lambda: cb())


class Widget:
    def __init__(self, *a, **k):
        self.kw = dict(k)
        self.bound = {}

    def __getattr__(self, name):
        return lambda *a, **k: None

    def configure(self, *a, **k):
        self.kw.update(k)

    config = configure

    def cget(self, k):
        return self.kw.get(k)

    def geometry(self, g=None):
        return "1180x840"

    def bind(self, ev, fn):
        self.bound[ev] = fn


class Tree(Widget):
    def __init__(self, *a, columns=(), **k):
        super().__init__(*a, **k)
        self.cols, self.rows, self._sel, self.n = list(columns), {}, (), 0

    def insert(self, parent, index, values=(), tags=()):
        self.n += 1
        iid = f"I{self.n}"
        self.rows[iid] = (tuple(values), tuple(tags))
        return iid

    def delete(self, *ids):
        for i in ids:
            self.rows.pop(i, None)

    def get_children(self, parent=""):
        return tuple(self.rows)

    def selection(self):
        return self._sel

    def selection_set(self, items):
        self._sel = tuple(items) if not isinstance(items, str) else (items,)

    def set(self, iid, col):
        return self.rows[iid][0][self.cols.index(col)]

    def move(self, *a):
        pass


class Listbox(Widget):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.items = []

    def insert(self, idx, v):
        self.items.append(v)

    def get(self, a, b=None):
        return tuple(self.items)

    def delete(self, i, j=None):
        del self.items[i]

    def curselection(self):
        return ()


CALLS = {"info": [], "warn": [], "error": [], "save": None, "dir": None}
tk = types.ModuleType("tkinter")
ttk = types.ModuleType("tkinter.ttk")
filedialog = types.ModuleType("tkinter.filedialog")
messagebox = types.ModuleType("tkinter.messagebox")
for n in ("Tk", "Toplevel", "Text", "Label", "PhotoImage"):
    setattr(tk, n, Widget)
class Menu(Widget):
    def add_command(self, *a, **k): pass
    def add_separator(self, *a, **k): pass
tk.Menu = Menu
tk.Canvas = Widget
tk.Frame = Widget
tk.Listbox, tk.StringVar, tk.BooleanVar, tk.TclError = Listbox, Var, Var, Exception
for n in ("Frame", "Label", "Button", "Entry", "Combobox", "Notebook", "Scrollbar", "Progressbar",
          "Radiobutton", "Checkbutton", "LabelFrame", "Style"):
    setattr(ttk, n, Widget)
ttk.Treeview = Tree
filedialog.askdirectory = lambda **k: CALLS["dir"]
filedialog.asksaveasfilename = lambda **k: CALLS["save"]
messagebox.showinfo = lambda *a, **k: CALLS["info"].append(a)
messagebox.showwarning = lambda *a, **k: CALLS["warn"].append(a)
messagebox.showerror = lambda *a, **k: CALLS["error"].append(a)
tk.ttk, tk.filedialog, tk.messagebox = ttk, filedialog, messagebox
sys.modules.update({"tkinter": tk, "tkinter.ttk": ttk, "tkinter.filedialog": filedialog,
                    "tkinter.messagebox": messagebox})
os.environ["APPDATA"] = tempfile.mkdtemp()

import sync_engine as se  # noqa: E402
import music_sync_checker as app_mod  # noqa: E402

TAGS = {}
se.read_tags_mutagen = lambda p: dict(TAGS.get(p, {"title": "", "artist": "", "album": "", "duration": None}))


class SmokeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.m = os.path.join(self.tmp, "master")
        self.d = os.path.join(self.tmp, "player")
        os.makedirs(self.m)
        os.makedirs(self.d)
        TAGS.clear()
        CALLS.update(info=[], warn=[], error=[], save=None, dir=None)
        app_mod.se.save_json(app_mod.DECISIONS_PATH, {})
        app_mod.se.save_json(app_mod.SETTINGS_PATH, {})
        self.app = app_mod.App(Widget())
        self.app._confirm = lambda *a, **k: True

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def mk(self, root, rel, size, title="", artist="", dur=None):
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(b"x" * size)
        if title:
            TAGS[p] = {"title": title, "artist": artist, "album": "", "duration": dur}
        return p

    def wait(self, timeout=10):
        t = time.time()
        while time.time() - t < timeout:
            self.app._poll()
            if not self.app.busy:
                return
            time.sleep(0.02)
        self.fail("operation did not finish")

    def scan(self):
        self.app.start_scan()
        self.wait()

    def rows(self):
        return list(self.app.tree.rows.values())

    def setup_library(self):
        self.mk(self.m, "West/Hozier - Take Me To Church.flac", 1001, "Take Me To Church", "Hozier", 241)
        self.mk(self.m, "West/Yellow - Coldplay.flac", 1002, "Yellow", "Coldplay", 267)
        self.mk(self.m, "West/Fix You - Coldplay.flac", 1003, "Fix You", "Coldplay", 295)
        self.mk(self.m, "Game/Color Your Night.flac", 1004, "Color Your Night", "Yumi Kawamura", 270)
        self.mk(self.d, "WEST/Yellow - Coldplay.flac", 2002, "Yellow", "Coldplay", 267)
        self.mk(self.d, "WEST/Take Me To Church.flac", 2001, "Take Me To Church", "Hozier", 241)
        self.mk(self.d, "WEST/Grenade.flac", 2005, "Grenade", "Bruno Mars", 222)
        self.mk(self.d, "Game/Color Your Night.flac", 2004, "Color Your Night", "Yumi Kawamura", 190)
        self.app.master_var.set(self.m)
        self.app.dev_list.insert("end", self.d)
        self.app._sync_dev_combo()

    def test_full_flow(self):
        self.setup_library()
        self.scan()
        a = self.app
        self.assertEqual(len(self.rows()), 4)
        self.assertEqual(a.result.counts(), {se.SYNC: 1, se.RENAMED: 1, se.MISSING: 1, se.REVIEW: 1})
        self.assertIn("(4)", a.chip_vars["all"].get())
        self.assertFalse(a.empty_lbl.kw.get("x"))  # no error

        # filter + pencarian
        a.filter_var.set(se.RENAMED)
        a._refresh_results()
        self.assertEqual(len(self.rows()), 1)
        a.filter_var.set("all")
        a.search_var.set("grenade")
        self.assertEqual(len(self.rows()), 1)
        a.search_var.set("")

        # decision for a song that needs review
        a.filter_var.set(se.REVIEW)
        a._refresh_results()
        a.select_all()
        a._on_select()
        a.action_decide("same")
        self.assertEqual(a.result.counts()[se.REVIEW], 0)
        self.assertEqual(a.result.counts()[se.SYNC], 2)
        a.filter_var.set("all")
        a._refresh_results()
        a.select_all()
        a.action_decide("reset")
        self.assertEqual(a.result.counts()[se.REVIEW], 1)

        # rename in master to match device
        a.filter_var.set(se.RENAMED)
        a._refresh_results()
        a.select_all()
        a.action_rename("master")
        self.wait()
        self.assertTrue(os.path.exists(os.path.join(self.m, "West", "Take Me To Church.flac")))
        self.assertEqual(a.result.counts()[se.SYNC], 2)   # setelah scan ulang otomatis

        # copy songs that only exist on the device into the master
        a.dest_master_var.set(os.path.join(self.m, "Unsorted"))
        a.filter_var.set(se.MISSING)
        a._refresh_results()
        a.select_all()
        a.action_copy_to_master()
        self.wait()
        self.assertTrue(os.path.exists(os.path.join(self.m, "Unsorted", "Grenade.flac")))
        self.assertEqual(a.result.counts()[se.MISSING], 0)

        # tab koleksi induk -> perangkat
        a.mfilter_var.set("not")
        a._refresh_master()
        titles = [r[0][1] for r in a.mtree.rows.values()]
        self.assertIn("Fix You", titles)
        self.assertNotIn("Yellow", titles)
        iid = next(i for i, r in a.mtree.rows.items() if r[0][1] == "Fix You")
        a.mtree.selection_set([iid])
        a._update_master_summary()
        a.dev_sub_var.set("Sad")
        a.action_copy_to_device()
        self.wait()
        self.assertTrue(os.path.exists(os.path.join(self.d, "Sad", "Fix You - Coldplay.flac")))

        # ekspor CSV
        CALLS["save"] = os.path.join(self.tmp, "laporan.csv")
        a.export_csv()
        with open(CALLS["save"], encoding="utf-8-sig") as f:
            self.assertGreater(len(f.read().splitlines()), 3)

    def test_validation_messages(self):
        self.app.start_scan()
        self.assertTrue(CALLS["warn"])
        self.app.action_rename("master")
        self.app.action_copy_to_master()
        self.app.action_copy_to_device()
        self.assertGreaterEqual(len(CALLS["info"]), 3)

    def test_settings_roundtrip(self):
        self.app.master_var.set(self.m)
        self.app.dev_list.insert("end", self.d)
        self.app._save_cfg()
        cfg = se.load_json(app_mod.SETTINGS_PATH, {})
        self.assertEqual(cfg["master"], self.m)
        self.assertEqual(cfg["devices"], [self.d])


if __name__ == "__main__":
    unittest.main()
