"""
Music Sync Checker
==================
Check that the songs on your MP3 player (Snowsky etc.) are in sync with your MASTER folder on the HDD/SSD.
Songs are matched by METADATA (title + artist + duration), so a song you renamed on the
player is still paired with its original in the master folder.

Run:  double-click "Music Sync Checker.pyw"  (no console window)  or build the .exe with build.bat
"""
from __future__ import annotations

import csv
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "Music Sync Checker"


def app_dir() -> str:
    return os.path.dirname(os.path.abspath(sys.argv[0] if getattr(sys, "frozen", False) else __file__))


def resource_path(name: str) -> str:
    return os.path.join(getattr(sys, "_MEIPASS", app_dir()), name)


from ui_kit import (T, Badge, Chip, NavItem, PillButton, RoundCheck, RoundEntry, RoundFrame,  # noqa: E402
                    RoundRadio, SoftProgress, PopupMenu, has_icons, init_scale, load_icon_font, px)

load_icon_font(resource_path("fontawesome-webfont.ttf"))


def set_dark_titlebar(window) -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


def enable_dpi_awareness() -> None:
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


# ------------------------------------------------------------------ splash screen
class Splash:
    """Small borderless loading window shown while the app starts up."""

    def __init__(self, root):
        self.root = root
        init_scale(root)
        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.configure(bg=T.ACCENT)
        w, h = px(460), px(250)
        x = (root.winfo_screenwidth() - w) // 2
        y = (root.winfo_screenheight() - h) // 2
        self.win.geometry(f"{w}x{h}+{x}+{y}")
        inner = tk.Frame(self.win, bg=T.BG)
        inner.pack(fill="both", expand=True, padx=2, pady=2)
        self.img = None
        try:
            self.img = tk.PhotoImage(file=resource_path("icon.png")).subsample(5)
            tk.Label(inner, image=self.img, bg=T.BG).pack(pady=(22, 4))
        except Exception:
            pass
        tk.Label(inner, text=APP_TITLE, bg=T.BG, fg=T.TEXT, font=("Segoe UI", 18, "bold")).pack()
        self.status = tk.Label(inner, text="Starting…", bg=T.BG, fg=T.MUTED, font=("Segoe UI", 9))
        self.status.pack(pady=(6, 12))
        self.bar = SoftProgress(inner, width=360, height=8, bg=T.BG)
        self.bar.configure(maximum=100, value=0)
        self.bar.pack()
        try:
            self.win.attributes("-topmost", True)
        except Exception:
            pass
        self.win.update()

    def step(self, text: str, value: float) -> None:
        self.status.configure(text=text)
        self.bar.configure(value=value)
        self.win.update()

    def close(self) -> None:
        try:
            self.win.destroy()
        except Exception:
            pass


def _close_pyinstaller_splash() -> None:
    try:  # the native splash PyInstaller shows before Python starts (only exists in the built .exe)
        import pyi_splash  # type: ignore
        pyi_splash.close()
    except Exception:
        pass


_BOOT = None
if __name__ == "__main__":  # show the loading window BEFORE the heavy imports below
    try:
        enable_dpi_awareness()
        _boot_root = tk.Tk()
        _boot_root.withdraw()
        _BOOT = Splash(_boot_root)
        _close_pyinstaller_splash()
        _BOOT.step("Loading metadata reader…", 15)
    except Exception:
        _BOOT = None

import sync_engine as se  # noqa: E402  (imports mutagen, the slow part)

if _BOOT:
    _BOOT.step("Preparing the matching engine…", 45)

DATA_DIR = se.app_data_dir()
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
DECISIONS_PATH = os.path.join(DATA_DIR, "decisions.json")
CACHE_PATH = os.path.join(DATA_DIR, "tag_cache.json")
CRASH_PATH = os.path.join(DATA_DIR, "crash.log")


def log_crash(text: str) -> None:
    try:
        with open(CRASH_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S]\n") + text + "\n")
    except OSError:
        pass


STATUS_TEXT = {
    se.SYNC: "✔ In sync",
    se.RENAMED: "✎ Renamed",
    se.MISSING: "✖ Not in master",
    se.REVIEW: "? Needs review",
}
STATUS_COLOR = {se.SYNC: T.OK, se.RENAMED: T.WARN, se.MISSING: T.ERR, se.REVIEW: T.INFO}
STATUS_ORDER = {se.MISSING: 0, se.REVIEW: 1, se.RENAMED: 2, se.SYNC: 3}
METHOD_TEXT = {
    "metadata": "Metadata", "size": "File size", "mixed": "Metadata + name",
    "name": "File name", "manual": "Your decision", "": "-",
}
HINTS = {
    "all": "Every song on the device with its check result. Problems are listed first.",
    se.SYNC: "All good: this song is in the master folder under the same name.",
    se.RENAMED: "In the master folder, but the file name differs (usually renamed on one side). "
                "Select songs, then make the names match in the master or on the device.",
    se.MISSING: "These songs exist only on the device. Select them and click \"Copy to master\".",
    se.REVIEW: "Similar, but not certain (for example the duration differs). Check the details below, "
               "then click \"Same song\" or \"Not same\".",
}


def short_path(t: se.Track) -> str:
    drive = os.path.splitdrive(t.root)[0]
    base = drive or os.path.basename(t.root.rstrip("/\\"))
    return f"{base}{os.sep}{t.rel}"


# ------------------------------------------------------------------ device list (rounded rows)
class DeviceList:
    """List of device folders shown as rounded rows. Mimics the tiny part of Listbox the app needs."""

    def __init__(self, parent, on_change=None, bg=T.SURF):
        self.items: list = []
        self.on_change = on_change
        self.bg = bg
        self.frame = tk.Frame(parent, bg=bg)
        self._render()

    def _render(self):
        for w in (self.frame.winfo_children() or []):
            w.destroy()
        if not self.items:
            tk.Label(self.frame, text="No device added yet.", bg=self.bg, fg=T.DIM,
                     font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 6))
        for i, path in enumerate(self.items):
            row = RoundFrame(self.frame, bg=self.bg, fill=T.SURF2, radius=14, pad=(14, 6))
            row.pack(fill="x", pady=(0, 6))
            inn = row.inner
            PillButton(inn, "", lambda i=i: self.delete(i), kind="ghost", icon="times", height=28, padx=10,
                       bg=T.SURF2).pack(side="right")
            if has_icons():
                from ui_kit import ICONS, icon_font
                tk.Label(inn, text=ICONS["hdd"], font=icon_font(11), bg=T.SURF2, fg=T.ACCENT).pack(side="left", padx=(0, 10))
            tk.Label(inn, text=path, bg=T.SURF2, fg=T.TEXT, font=("Segoe UI", 10), anchor="w").pack(side="left")

    def insert(self, idx, value):
        self.items.append(value)
        self._render()

    def get(self, a=0, b=None):
        return tuple(self.items)

    def delete(self, i, j=None):
        if 0 <= i < len(self.items):
            del self.items[i]
        self._render()
        if self.on_change:
            self.on_change()

    def curselection(self):
        return ()


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        init_scale(root)
        self.cfg = se.load_json(SETTINGS_PATH, {})
        self.decisions = se.load_json(DECISIONS_PATH, {})
        self._fit_window()
        self.root.report_callback_exception = self._on_tk_error

        self.q: "queue.Queue" = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.master_tracks: list = []
        self.device_tracks: list = []
        self.result = None
        self.row_items: dict = {}
        self.row_master: dict = {}
        self.pages: dict = {}
        self.nav: dict = {}
        self.page = ""

        self._setup_styles()
        self._build_ui()
        self._load_cfg()
        self.show_page("check" if (self.master_var.get() and self.dev_list.get(0, "end")) else "folders")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self._poll)
        set_dark_titlebar(self.root)
        if not se.HAS_MUTAGEN:
            self.log("[WARNING] The metadata reader (mutagen) is not installed. Matching only uses file names and sizes.")

    def _fit_window(self):
        """Never let the window be bigger than the screen (otherwise the bottom is cut off)."""
        try:
            sw, sh = int(self.root.winfo_screenwidth()), int(self.root.winfo_screenheight())
            m = re.match(r"(\d+)x(\d+)", str(self.cfg.get("geometry", "")))
            w, h = (int(m.group(1)), int(m.group(2))) if m else (px(1200), px(800))
            w, h = max(900, min(w, sw - 40)), max(560, min(h, sh - 110))
            self.root.minsize(min(px(1020), w), min(px(660), h))
            self.root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 20)}")
        except Exception:
            self.root.geometry("1100x700")

    def _on_tk_error(self, exc, val, tb):
        text = "".join(traceback.format_exception(exc, val, tb))
        log_crash(text)
        self.log(f"[ERROR] {val}  (details saved to {CRASH_PATH})")
        self.status_var.set(f"Something went wrong: {val}")

    # ----------------------------------------------------------------- styles
    def _setup_styles(self):
        self.root.configure(bg=T.BG)
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure(".", background=T.BG, foreground=T.TEXT, font=("Segoe UI", 9), borderwidth=0, focuscolor=T.BG)
        st.configure("TFrame", background=T.BG)
        st.configure("Card.TFrame", background=T.SURF)
        st.configure("TLabel", background=T.BG, foreground=T.TEXT)
        st.configure("Card.TLabel", background=T.SURF, foreground=T.TEXT)
        st.configure("CardTitle.TLabel", background=T.SURF, foreground=T.TEXT, font=("Segoe UI", 12, "bold"))
        st.configure("CardMuted.TLabel", background=T.SURF, foreground=T.MUTED, font=("Segoe UI", 9))
        st.configure("CardErr.TLabel", background=T.SURF, foreground=T.ERR, font=("Segoe UI", 9, "bold"))
        st.configure("Muted.TLabel", background=T.BG, foreground=T.MUTED, font=("Segoe UI", 9))
        st.configure("Hint.TLabel", background=T.BG, foreground=T.MUTED, font=("Segoe UI", 9))
        st.configure("Warn.TLabel", background=T.BG, foreground=T.WARN, font=("Segoe UI", 9))
        st.configure("Title.TLabel", background=T.BG, foreground=T.TEXT, font=("Segoe UI", 22, "bold"))
        st.configure("Bar.TLabel", background=T.SURF, foreground=T.MUTED, font=("Segoe UI", 9))
        st.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        st.configure("Treeview", background=T.SURF, foreground=T.TEXT, fieldbackground=T.SURF, borderwidth=0,
                     rowheight=px(38), font=("Segoe UI", 10))
        st.map("Treeview", background=[("selected", T.SEL)], foreground=[("selected", "#FFFFFF")])
        st.configure("Treeview.Heading", background=T.SURF, foreground=T.DIM, relief="flat", borderwidth=0,
                     font=("Segoe UI", 8, "bold"), padding=(8, 6))
        st.map("Treeview.Heading", background=[("active", T.SURF)], foreground=[("active", T.TEXT)])
        st.configure("Vertical.TScrollbar", background=T.SURF2, troughcolor=T.SURF, bordercolor=T.SURF,
                     arrowcolor=T.MUTED, lightcolor=T.SURF2, darkcolor=T.SURF2, relief="flat", gripcount=0)
        st.map("Vertical.TScrollbar", background=[("active", T.HOVER)])

    # -------------------------------------------------------------------- UI helpers
    def _card(self, parent, pady=(0, 14)):
        rf = RoundFrame(parent, bg=T.BG, fill=T.SURF, radius=20, pad=(22, 18))
        rf.pack(fill="x", pady=pady)
        return rf.inner

    def _card_head(self, parent, icon, title, sub):
        head = tk.Frame(parent, bg=T.SURF)
        head.pack(fill="x")
        Badge(head, icon, bg=T.SURF).pack(side="left")
        txt = tk.Frame(head, bg=T.SURF)
        txt.pack(side="left", padx=14)
        ttk.Label(txt, text=title, style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(txt, text=sub, style="CardMuted.TLabel").pack(anchor="w")

    def _heading(self, parent, title, sub=""):
        ttk.Label(parent, text=title, style="Title.TLabel").pack(anchor="w")
        if sub:
            ttk.Label(parent, text=sub, style="Muted.TLabel", wraplength=px(860), justify="left").pack(anchor="w", pady=(2, 0))

    def _make_tree(self, parent, columns, widths, height=10):
        wrap = tk.Frame(parent, bg=T.SURF)
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        tree = ttk.Treeview(wrap, columns=[c[0] for c in columns], show="headings", selectmode="extended", height=height)
        for (cid, title), w in zip(columns, widths):
            tree.heading(cid, text=title.upper(), anchor="w", command=lambda c=cid, t=tree: self._sort_tree(t, c))
            tree.column(cid, width=w, minwidth=60, anchor="w", stretch=True)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        return wrap, tree

    # -------------------------------------------------------------------- main layout
    def _build_ui(self):
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        # ---------- sidebar
        side = tk.Frame(self.root, bg=T.SIDE, width=px(236))
        side.grid(row=0, column=0, sticky="ns")
        side.pack_propagate(False)
        brand = tk.Frame(side, bg=T.SIDE)
        brand.pack(fill="x", padx=px(22), pady=(px(26), px(20)))
        Badge(brand, "music", bg=T.SIDE, size=40).pack(side="left")
        tk.Label(brand, text="Music Sync", bg=T.SIDE, fg=T.TEXT, font=("Segoe UI", 15, "bold")).pack(side="left", padx=12)
        tk.Label(side, text="LIBRARY", bg=T.SIDE, fg=T.DIM, font=("Segoe UI", 8, "bold"),
                 anchor="w").pack(fill="x", padx=px(26), pady=(0, 6))
        for key, text, icon in (("folders", "Folders", "folder"), ("check", "Device check", "check_circle"),
                                ("master", "Master → device", "headphones"), ("log", "Activity log", "list")):
            item = NavItem(side, text, icon, lambda k=key: self.show_page(k), bg=T.SIDE)
            item.pack(fill="x", padx=px(14), pady=2)
            self.nav[key] = item

        bottom = tk.Frame(side, bg=T.SIDE)
        bottom.pack(side="bottom", fill="x", padx=px(18), pady=px(20))
        self.btn_scan = PillButton(bottom, "Check Now", self.start_scan, kind="primary", icon="sync", bg=T.SIDE,
                                   height=44, min_width=196, size=10)
        self.btn_scan.pack(anchor="w")
        self.btn_report = PillButton(bottom, "Save report (.csv)", self.export_csv, kind="ghost", icon="save",
                                     bg=T.SIDE, height=34, min_width=196)
        self.btn_report.pack(anchor="w", pady=(10, 0))

        # ---------- content area
        self.content = tk.Frame(self.root, bg=T.BG)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.pages["folders"] = self._build_folders_page()
        self.pages["check"] = self._build_check_page()
        self.pages["master"] = self._build_master_page()
        self.pages["log"] = self._build_log_page()

        # ---------- bottom status bar
        bar = tk.Frame(self.root, bg=T.SURF, height=px(56))
        bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        bar.grid_propagate(False)
        bar.columnconfigure(0, weight=1)
        bar.rowconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="Choose your master folder and device, then click Check Now.")
        ttk.Label(bar, textvariable=self.status_var, style="Bar.TLabel").grid(row=0, column=0, sticky="w", padx=(24, 12))
        self.progress = SoftProgress(bar, width=240, height=8, bg=T.SURF)
        self.progress.grid(row=0, column=1, padx=(0, 14))
        self.btn_cancel = PillButton(bar, "Cancel", self.cancel_job, kind="secondary", icon="times", bg=T.SURF,
                                     height=32, padx=14)
        self.btn_cancel.grid(row=0, column=2, padx=(0, 20))
        self.btn_cancel.configure(state="disabled")

    def show_page(self, name):
        for f in self.pages.values():
            f.pack_forget()
        self.pages[name].pack(fill="both", expand=True, padx=px(32), pady=(px(28), px(12)))
        self.page = name
        for k, item in self.nav.items():
            item.set_active(k == name)

    # -------------------------------------------------------------------- folders page
    def _build_folders_page(self):
        page = tk.Frame(self.content, bg=T.BG)
        self._heading(page, "Your folders", "Tell the app where your full collection lives and which player to check.")
        body = tk.Frame(page, bg=T.BG)
        body.pack(fill="x", pady=(20, 0))

        c = self._card(body)
        self._card_head(c, "folder", "Master folder", "Your complete collection on the HDD/SSD.")
        row = tk.Frame(c, bg=T.SURF)
        row.pack(fill="x", pady=(16, 0))
        self.btn_master = PillButton(row, "Browse", self.pick_master, icon="folder_open", bg=T.SURF)
        self.btn_master.pack(side="right", padx=(10, 0))
        self.master_var = tk.StringVar()
        RoundEntry(row, self.master_var, icon="folder", bg=T.SURF).pack(side="left", fill="x", expand=True)

        c = self._card(body)
        self._card_head(c, "hdd", "Device", "Your MP3 player or SD card. Two SD cards? Add both; they count as one device.")
        self.dev_list = DeviceList(c, on_change=self._sync_dev_combo, bg=T.SURF)
        self.dev_list.frame.pack(fill="x", pady=(16, 6))
        self.btn_add_dev = PillButton(c, "Add device", self.add_device, icon="plus", bg=T.SURF)
        self.btn_add_dev.pack(anchor="w")

        c = self._card(body)
        self._card_head(c, "download", "Songs found only on the device", "Where \"Copy to master\" puts them.")
        row = tk.Frame(c, bg=T.SURF)
        row.pack(fill="x", pady=(16, 0))
        PillButton(row, "Browse", self.pick_dest_master, icon="folder_open", bg=T.SURF).pack(side="right", padx=(10, 0))
        self.dest_master_var = tk.StringVar()
        RoundEntry(row, self.dest_master_var, icon="folder", bg=T.SURF).pack(side="left", fill="x", expand=True)
        self.keep_sub_var = tk.BooleanVar(value=False)
        self.lrc_var = tk.BooleanVar(value=True)
        opts = tk.Frame(c, bg=T.SURF)
        opts.pack(fill="x", pady=(14, 0))
        RoundRadio(opts, "Put all songs in one folder", self.keep_sub_var, False, bg=T.SURF).pack(side="left", padx=(0, 28))
        RoundRadio(opts, "Keep the device's sub-folders", self.keep_sub_var, True, bg=T.SURF).pack(side="left")
        RoundCheck(c, "Also copy lyrics (.lrc)", self.lrc_var, bg=T.SURF).pack(anchor="w", pady=(6, 0))

        self.warn_var = tk.StringVar(value="")
        if not se.HAS_MUTAGEN:
            self.warn_var.set("The metadata reader is not installed, so matching only uses file names and sizes. "
                              + ("Run build.bat again." if getattr(sys, "frozen", False) else "Click the button to install it."))
        self.warn_row = tk.Frame(body, bg=T.BG)
        self.warn_row.pack(fill="x")
        ttk.Label(self.warn_row, textvariable=self.warn_var, style="Warn.TLabel", wraplength=px(700)).pack(side="left")
        if not se.HAS_MUTAGEN and not getattr(sys, "frozen", False):
            PillButton(self.warn_row, "Install mutagen", self.install_mutagen, icon="download", bg=T.BG,
                       height=32).pack(side="left", padx=10)
        return page

    # -------------------------------------------------------------------- device check page
    def _build_check_page(self):
        p = tk.Frame(self.content, bg=T.BG)
        head = tk.Frame(p, bg=T.BG)
        head.pack(fill="x")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._refresh_results())
        RoundEntry(head, self.search_var, icon="search", bg=T.BG, width=250).pack(side="right")
        self._heading(head, "Device check")

        bar = tk.Frame(p, bg=T.BG)
        bar.pack(fill="x", pady=(16, 0))
        self.filter_var = tk.StringVar(value="all")
        self.chip_vars = {k: tk.StringVar(value=t) for k, t in
                          [("all", "All"), (se.SYNC, STATUS_TEXT[se.SYNC]), (se.RENAMED, STATUS_TEXT[se.RENAMED]),
                           (se.MISSING, STATUS_TEXT[se.MISSING]), (se.REVIEW, STATUS_TEXT[se.REVIEW])]}
        for key in ("all", se.MISSING, se.RENAMED, se.REVIEW, se.SYNC):
            Chip(bar, self.filter_var, key, textvariable=self.chip_vars[key], command=self._refresh_results,
                 bg=T.BG).pack(side="left", padx=(0, 8))

        self.hint_var = tk.StringVar(value=HINTS["all"])
        ttk.Label(p, textvariable=self.hint_var, style="Hint.TLabel", wraplength=px(860), justify="left").pack(anchor="w", pady=(12, 10))

        act = tk.Frame(p, bg=T.BG)
        act.pack(fill="x", pady=(0, 8))
        self.btn_copy_top = PillButton(act, "Copy to master", self.action_copy_to_master, kind="primary", icon="copy")
        self.btn_copy_top.pack(side="left", padx=(0, 8))
        self.btn_copy_master = self.btn_copy_top
        self.btn_rn_master = PillButton(act, "Rename in master", lambda: self.action_rename("master"), icon="pencil")
        self.btn_rn_dev = PillButton(act, "Rename on device", lambda: self.action_rename("device"), icon="pencil")
        self.btn_same = PillButton(act, "Same song", lambda: self.action_decide("same"), icon="check")
        self.btn_diff = PillButton(act, "Not same", lambda: self.action_decide("diff"), icon="times")
        self.btn_reset = PillButton(act, "Undo", lambda: self.action_decide("reset"), kind="ghost", icon="undo")
        for b in (self.btn_rn_master, self.btn_rn_dev, self.btn_same, self.btn_diff, self.btn_reset):
            b.pack(side="left", padx=(0, 8))
        self.btn_more = PillButton(act, "", self._more_menu, icon="ellipsis", padx=14)
        self.btn_more.pack(side="right")
        self.sel_var = tk.StringVar(value="0 selected")
        ttk.Label(act, textvariable=self.sel_var, style="Muted.TLabel").pack(side="right", padx=(0, 14))

        self.dest_show_var = tk.StringVar(value="")
        dest = tk.Frame(p, bg=T.BG)
        dest.pack(fill="x", pady=(0, 8))
        ttk.Label(dest, textvariable=self.dest_show_var, style="Muted.TLabel").pack(side="left")
        PillButton(dest, "Change", self.pick_dest_master, kind="ghost", icon="folder_open", height=28, padx=12).pack(side="left", padx=8)

        det = RoundFrame(p, bg=T.BG, fill=T.SURF, radius=16, pad=(16, 10))
        det.pack(side="bottom", fill="x", pady=(10, 0))
        self.detail = tk.Text(det.inner, height=3, wrap="word", state="disabled", bg=T.SURF, fg="#D4D4D8",
                              font=("Consolas", 9), relief="flat", bd=0, highlightthickness=0)
        self.detail.tag_configure("h", foreground=T.ACCENT, font=("Consolas", 9, "bold"))
        self.detail.pack(fill="x")

        card = RoundFrame(p, bg=T.BG, fill=T.SURF, radius=18, pad=(10, 10), stretch=True)
        card.pack(fill="both", expand=True)
        cols = [("status", "Status"), ("label", "Song"), ("dev", "On device"), ("mst", "In master"),
                ("how", "Matched by"), ("note", "Note")]
        wrap, self.tree = self._make_tree(card.inner, cols, [120, 230, 220, 200, 100, 150], height=8)
        wrap.pack(fill="both", expand=True)
        for cid, mw in (("status", 110), ("how", 90)):
            self.tree.column(cid, minwidth=mw, stretch=False)
        for s, c in STATUS_COLOR.items():
            self.tree.tag_configure(s, foreground=c)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())
        self.tree.bind("<Control-a>", lambda e: (self.select_all(), "break")[1])
        self.empty_lbl = tk.Label(self.tree, text="No results yet.\nPick your folders, then click \"Check Now\".",
                                  bg=T.SURF, fg=T.DIM, font=("Segoe UI", 12))
        self.empty_lbl.place(relx=0.5, rely=0.5, anchor="center")

        # right-click menu: custom rounded popup
        self._popup = None
        self.tree.bind("<Button-3>", self._rclick_select)
        self.tree.bind("<ButtonRelease-3>", self._popup_menu)
        self.tree.bind("<Shift-F10>", self._popup_menu)
        return p

    def _rclick_select(self, event):
        try:
            row = self.tree.identify_row(event.y)
            if row and row not in self.tree.selection():
                self.tree.selection_set(row)
        except Exception as e:
            self.log(f"[ERROR] right-click: {e}")

    def _show_popup(self, entries, x, y):
        if self._popup is not None:
            self._popup.close()
        self._popup = PopupMenu(self.root, entries)
        self._popup.show(x, y)

    def _popup_menu(self, event):
        try:
            x = getattr(event, "x_root", None)
            y = getattr(event, "y_root", None)
            if x is None or (x == 0 and y == 0):
                x, y = self.root.winfo_pointerx(), self.root.winfo_pointery()
            self._show_popup(self._ctx_entries(), x, y)
        except Exception as e:
            self.log(f"[ERROR] menu: {e}")

    def _more_menu(self):
        try:
            self._show_popup(self._ctx_entries(), self.btn_more.winfo_rootx() - 190,
                             self.btn_more.winfo_rooty() + self.btn_more.winfo_height() + 4)
        except Exception as e:
            self.log(f"[ERROR] menu: {e}")

    def _ctx_entries(self):
        sel = self._selected_items()
        n = {s: sum(1 for i in sel if i.status == s) for s in STATUS_TEXT}
        free = not self.busy
        return [
            {"label": "Copy to master", "icon": "copy", "command": self.action_copy_to_master, "enabled": free and n[se.MISSING] > 0},
            {"label": "Rename in master to match device", "icon": "pencil",
             "command": lambda: self.action_rename("master"), "enabled": free and n[se.RENAMED] > 0},
            {"label": "Rename on device to match master", "icon": "pencil",
             "command": lambda: self.action_rename("device"), "enabled": free and n[se.RENAMED] > 0},
            None,
            {"label": "Same song", "icon": "check", "command": lambda: self.action_decide("same"),
             "enabled": free and n[se.REVIEW] > 0},
            {"label": "Not the same song", "icon": "times", "command": lambda: self.action_decide("diff"),
             "enabled": free and n[se.REVIEW] > 0},
            {"label": "Undo my decision", "icon": "undo", "command": lambda: self.action_decide("reset"),
             "enabled": free and any(i.method == "manual" or i.note.startswith("marked") for i in sel)},
            None,
            {"label": "Select all shown", "icon": "tasks", "command": self.select_all, "enabled": True},
        ]

    # -------------------------------------------------------------------- master page
    def _build_master_page(self):
        p = tk.Frame(self.content, bg=T.BG)
        head = tk.Frame(p, bg=T.BG)
        head.pack(fill="x")
        self.msearch_var = tk.StringVar()
        self.msearch_var.trace_add("write", lambda *_: self._refresh_master())
        RoundEntry(head, self.msearch_var, icon="search", bg=T.BG, width=250).pack(side="right")
        self._heading(head, "Master → device", "Pick songs from your full collection to take with you.")
        bar = tk.Frame(p, bg=T.BG)
        bar.pack(fill="x", pady=(16, 0))
        self.mfilter_var = tk.StringVar(value="not")
        for key, text in (("not", "Not on device"), ("on", "On device"), ("all", "All")):
            Chip(bar, self.mfilter_var, key, text=text, command=self._refresh_master, bg=T.BG).pack(side="left", padx=(0, 8))
        self.mcount_var = tk.StringVar(value="")
        ttk.Label(p, textvariable=self.mcount_var, style="Muted.TLabel").pack(anchor="w", pady=(12, 8))

        send = RoundFrame(p, bg=T.BG, fill=T.SURF, radius=20, pad=(22, 16))
        send.pack(side="bottom", fill="x", pady=(10, 0))
        inner = send.inner
        top = tk.Frame(inner, bg=T.SURF)
        top.pack(fill="x")
        ttk.Label(top, text="Send to", style="CardTitle.TLabel").pack(side="left", padx=(0, 14))
        self.dev_target_var = tk.StringVar()
        self.target_box = tk.Frame(top, bg=T.SURF)
        self.target_box.pack(side="left")
        self.dev_sub_var = tk.StringVar()
        RoundEntry(top, self.dev_sub_var, icon="folder", bg=T.SURF, width=220).pack(side="right")
        ttk.Label(top, text="Sub-folder (optional)", style="CardMuted.TLabel").pack(side="right", padx=(0, 10))
        self.dev_sub_var.trace_add("write", lambda *_: self._update_master_summary())
        low = tk.Frame(inner, bg=T.SURF)
        low.pack(fill="x", pady=(12, 0))
        self.btn_copy_dev = PillButton(low, "Copy to device", self.action_copy_to_device, kind="primary", icon="upload",
                                       bg=T.SURF)
        self.btn_copy_dev.pack(side="right")
        self.mlrc_var = tk.BooleanVar(value=True)
        RoundCheck(low, "Also copy lyrics (.lrc)", self.mlrc_var, bg=T.SURF).pack(side="left", padx=(0, 20))
        self.msum_var = tk.StringVar(value="No songs selected.")
        self.msum_lbl = ttk.Label(low, textvariable=self.msum_var, style="CardMuted.TLabel")
        self.msum_lbl.pack(side="left")

        card = RoundFrame(p, bg=T.BG, fill=T.SURF, radius=18, pad=(10, 10), stretch=True)
        card.pack(fill="both", expand=True)
        cols = [("status", "Status"), ("title", "Song"), ("artist", "Artist"), ("dur", "Length"),
                ("size", "Size"), ("loc", "Location in master")]
        wrap, self.mtree = self._make_tree(card.inner, cols, [140, 260, 180, 70, 80, 260], height=10)
        wrap.pack(fill="both", expand=True)
        self.mtree.tag_configure("on", foreground=T.OK)
        self.mtree.tag_configure("maybe", foreground=T.INFO)
        self.mtree.bind("<<TreeviewSelect>>", lambda e: self._update_master_summary())
        self.mtree.bind("<Control-a>", lambda e: (self.mtree.selection_set(self.mtree.get_children()), "break")[1])
        self.mtree.bind("<Button-3>", self._mrclick_select)
        self.mtree.bind("<ButtonRelease-3>", self._mpopup)
        return p

    def _mrclick_select(self, event):
        try:
            row = self.mtree.identify_row(event.y)
            if row and row not in self.mtree.selection():
                self.mtree.selection_set(row)
        except Exception as e:
            self.log(f"[ERROR] right-click: {e}")

    def _mpopup(self, event):
        try:
            sel = [i for i in self.mtree.selection() if i in self.row_master]
            self._show_popup([
                {"label": "Copy to device", "icon": "upload", "command": self.action_copy_to_device,
                 "enabled": (not self.busy) and bool(sel)},
                None,
                {"label": "Select all shown", "icon": "tasks",
                 "command": lambda: self.mtree.selection_set(self.mtree.get_children()), "enabled": True},
            ], event.x_root, event.y_root)
        except Exception as e:
            self.log(f"[ERROR] menu: {e}")

    # -------------------------------------------------------------------- log page
    def _build_log_page(self):
        p = tk.Frame(self.content, bg=T.BG)
        self._heading(p, "Activity log", "Everything the app did, including warnings and errors.")
        card = RoundFrame(p, bg=T.BG, fill=T.SURF, radius=18, pad=(16, 12), stretch=True)
        card.pack(fill="both", expand=True, pady=(16, 0))
        self.logbox = tk.Text(card.inner, wrap="word", state="disabled", bg=T.SURF, fg="#D4D4D8",
                              insertbackground="#FFFFFF", font=("Consolas", 9), relief="flat", bd=0, highlightthickness=0)
        self.logbox.pack(fill="both", expand=True)
        return p

    # ------------------------------------------------------ settings & helpers
    def _load_cfg(self):
        self.master_var.set(self.cfg.get("master", ""))
        for d in self.cfg.get("devices", []):
            self.dev_list.insert("end", d)
        self.dest_master_var.set(self.cfg.get("dest_master", ""))
        self.dest_master_var.trace_add("write", lambda *_: self._update_dest_label())
        self.keep_sub_var.set(self.cfg.get("keep_sub", False))
        self.lrc_var.set(self.cfg.get("lrc", True))
        self.mlrc_var.set(self.cfg.get("mlrc", True))
        self.dev_sub_var.set(self.cfg.get("dev_sub", ""))
        self._sync_dev_combo(self.cfg.get("dev_target", ""))
        self._update_dest_label()
        self._show_empty_hint()

    def _update_dest_label(self):
        d = self.dest_master_var.get().strip()
        self.dest_show_var.set(f"Copies go to:  {d}" if d else "Copies go to:  (not set yet)")

    def startup_check(self):
        """Tell the user right away if the saved folders (or the player) are not reachable."""
        master = self.master_var.get().strip()
        devs = self._device_list()
        if not master and not devs:
            return
        problems = []
        if master and not os.path.isdir(master):
            problems.append(f"master folder not found ({master})")
        problems += [f"device folder not found ({d})" for d in devs if not os.path.isdir(d)]
        if problems:
            msg = "Heads up: " + "; ".join(problems) + ". Is your player connected?"
            self.status_var.set(msg)
            self.log("[WARNING] " + msg)
        elif master and devs:
            self.status_var.set("Ready. Your saved folders were found. Click Check Now.")

    def _save_cfg(self):
        self.cfg.update({
            "geometry": self.root.geometry(), "master": self.master_var.get().strip(),
            "devices": self._device_list(), "dest_master": self.dest_master_var.get().strip(),
            "keep_sub": self.keep_sub_var.get(), "lrc": self.lrc_var.get(), "mlrc": self.mlrc_var.get(),
            "dev_sub": self.dev_sub_var.get().strip(), "dev_target": self.dev_target_var.get(),
        })
        try:
            se.save_json(SETTINGS_PATH, self.cfg)
        except OSError:
            pass

    def _device_list(self) -> list:
        return list(self.dev_list.get(0, "end"))

    def _sync_dev_combo(self, keep: str = ""):
        """Keep the 'Send to' radio chips in line with the device list."""
        devs = self._device_list()
        cur = keep or self.dev_target_var.get()
        self.dev_target_var.set(cur if cur in devs else (devs[0] if devs else ""))
        for w in (self.target_box.winfo_children() or []):
            w.destroy()
        for d in devs:
            Chip(self.target_box, self.dev_target_var, d, text=d, command=self._update_master_summary,
                 bg=T.SURF, icon="hdd", height=32).pack(side="left", padx=(0, 8))
        if not devs:
            ttk.Label(self.target_box, text="Add a device first", style="CardMuted.TLabel").pack(side="left")

    def log(self, text: str):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", time.strftime("%H:%M:%S  ") + text + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def pick_master(self):
        d = filedialog.askdirectory(title="Choose your master folder (full collection)", mustexist=True)
        if d:
            d = os.path.normpath(d)
            self.master_var.set(d)
            if not self.dest_master_var.get().strip():
                uns = os.path.join(d, "Unsorted")
                self.dest_master_var.set(uns if os.path.isdir(uns) else os.path.join(d, "_From Device"))

    def add_device(self):
        d = filedialog.askdirectory(title="Choose the MP3 player / SD card folder or drive", mustexist=True)
        if d:
            d = os.path.normpath(d)
            if d not in self._device_list():
                self.dev_list.insert("end", d)
                self._sync_dev_combo()

    def remove_device(self):
        for i in reversed(self.dev_list.curselection()):
            self.dev_list.delete(i)
        self._sync_dev_combo()

    def pick_dest_master(self):
        d = filedialog.askdirectory(title="Which master folder should songs from the device be saved to?",
                                    initialdir=self.master_var.get() or None)
        if d:
            self.dest_master_var.set(os.path.normpath(d))

    def install_mutagen(self):
        self.log("Installing mutagen…")
        self.warn_var.set("Installing mutagen, please wait…")

        def work():
            try:
                flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW: no console pops up
                r = subprocess.run([sys.executable.replace("pythonw.exe", "python.exe"), "-m", "pip", "install", "mutagen"],
                                   capture_output=True, text=True, creationflags=flags)
                self.q.put(("mutagen", r.returncode == 0, (r.stdout + r.stderr)[-400:]))
            except Exception as e:
                self.q.put(("mutagen", False, str(e)))
        threading.Thread(target=work, daemon=True).start()

    def on_close(self):
        self._save_cfg()
        self.cancel.set()
        self.root.destroy()

    # ------------------------------------------------------- background jobs
    def _set_busy(self, busy: bool, status: str = ""):
        self.busy = busy
        st = "disabled" if busy else "normal"
        for b in (self.btn_scan, self.btn_master, self.btn_add_dev, self.btn_copy_dev):
            b.configure(state=st)
        self.btn_cancel.configure(state="normal" if busy else "disabled")
        if busy:
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
        if status:
            self.status_var.set(status)
        self._update_actions()

    def _run_bg(self, fn, status: str):
        if self.busy:
            return
        self.cancel.clear()
        self._set_busy(True, status)

        def target():
            try:
                fn()
            except se.Cancelled:
                self.q.put(("cancelled",))
            except Exception:
                self.q.put(("error", traceback.format_exc()))
        threading.Thread(target=target, daemon=True).start()

    def cancel_job(self):
        self.cancel.set()
        self.status_var.set("Cancelling…")

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, done, total, text = msg
                    if str(self.progress.cget("mode")) == "indeterminate":
                        self.progress.stop()
                        self.progress.configure(mode="determinate")
                    self.progress.configure(maximum=max(total, 1), value=done)
                    self.status_var.set(text)
                elif kind == "log":
                    self.log(msg[1])
                elif kind == "scanned":
                    self._on_scanned(*msg[1:])
                elif kind == "done":
                    self.log(msg[1])
                    self._set_busy(False, msg[1])
                    if msg[2]:
                        self.start_scan(quiet=True)
                elif kind == "cancelled":
                    self._set_busy(False, "Cancelled.")
                    self.log("Cancelled.")
                elif kind == "error":
                    self._set_busy(False, "Something went wrong. See the activity log.")
                    log_crash(msg[1])
                    self.log("[ERROR] " + msg[1])
                    messagebox.showerror(APP_TITLE, "An unexpected error occurred. The details are in the activity log "
                                                    f"and in:\n{CRASH_PATH}")
                elif kind == "mutagen":
                    if msg[1]:
                        self.warn_var.set("mutagen installed. Close and reopen the app so tags can be read.")
                        self.log("mutagen installed. Please reopen the app.")
                    else:
                        self.warn_var.set("Could not install mutagen. Run:  pip install mutagen")
                        self.log("[FAILED] installing mutagen: " + msg[2])
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    # ------------------------------------------------------------------ scan
    def start_scan(self, quiet: bool = False):
        if self.busy:
            return
        master = self.master_var.get().strip()
        if not os.path.isdir(master):
            self.show_page("folders")
            messagebox.showwarning(APP_TITLE, "The master folder is not selected or was not found.")
            return
        devs = self._device_list()
        live = [d for d in devs if os.path.isdir(d)]
        missing = [d for d in devs if d not in live]
        if not live:
            self.show_page("folders")
            messagebox.showwarning(APP_TITLE, "No device folder can be read.\n"
                                              "Make sure your MP3 player / SD card is connected, then add its folder.")
            return
        self._save_cfg()

        def work():
            cache = se.TagCache(CACHE_PATH)
            errors: list = []

            def cb(phase):
                return lambda done, total, p: self.q.put(("progress", done, total, f"{phase}: {done}/{total} songs"))
            mt, e = se.scan_folder(master, cache, progress=cb("Reading master"), cancel=self.cancel)
            errors += e
            dts: list = []
            for d in live:
                t, e = se.scan_folder(d, cache, progress=cb(f"Reading device {d}"), cancel=self.cancel)
                dts += t
                errors += e
            cache.save(prune=not missing)
            self.q.put(("scanned", mt, dts, errors, missing))
        self._run_bg(work, "Reading folders…")

    def _on_scanned(self, master_tracks, device_tracks, errors, missing):
        self.master_tracks, self.device_tracks = master_tracks, device_tracks
        self._recompare()
        self._set_busy(False)
        if self.page == "folders":
            self.show_page("check")
        c = self.result.counts()
        summary = (f"Done: {len(device_tracks)} songs on the device vs {len(master_tracks)} in the master. "
                   f"In sync {c[se.SYNC]}, renamed {c[se.RENAMED]}, not in master {c[se.MISSING]}, "
                   f"needs review {c[se.REVIEW]}.")
        self.status_var.set(summary)
        self.log(summary)
        for d in missing:
            self.log(f"[WARNING] device folder not found (unplugged?): {d}")
        for e in errors[:15]:
            self.log(f"[WARNING] could not read: {e}")
        if len(errors) > 15:
            self.log(f"[WARNING] ... and {len(errors) - 15} more read problems.")
        if not se.HAS_MUTAGEN:
            self.log("[WARNING] without mutagen, tags and durations are not read, so results are less accurate.")

    def _recompare(self):
        self.result = se.compare(self.device_tracks, self.master_tracks, self.decisions)
        self._refresh_results()
        self._refresh_master()

    # ------------------------------------------------------- device check tab
    def _show_empty_hint(self):
        if self.tree.get_children():
            self.empty_lbl.place_forget()
        else:
            self.empty_lbl.place(relx=0.5, rely=0.5, anchor="center")

    def _item_row(self, it: se.Item) -> tuple:
        return (STATUS_TEXT[it.status], it.device.label, short_path(it.device),
                it.master.rel if it.master else "-", METHOD_TEXT.get(it.method, it.method or "-"), it.note)

    def _refresh_results(self):
        res = self.result
        self.tree.delete(*self.tree.get_children())
        self.row_items.clear()
        counts = res.counts() if res else {s: 0 for s in STATUS_TEXT}
        total = len(res.items) if res else 0
        self.chip_vars["all"].set(f"All ({total})")
        for s in STATUS_TEXT:
            self.chip_vars[s].set(f"{STATUS_TEXT[s]} ({counts[s]})")
        flt = self.filter_var.get()
        q = self.search_var.get().strip().casefold()
        items = sorted(res.items, key=lambda i: (STATUS_ORDER[i.status], i.device.label.casefold())) if res else []
        for it in items:
            if flt != "all" and it.status != flt:
                continue
            row = self._item_row(it)
            if q and q not in " ".join(row).casefold():
                continue
            iid = self.tree.insert("", "end", values=row, tags=(it.status,))
            self.row_items[iid] = it
        self.hint_var.set(HINTS.get(flt, ""))
        self._show_empty_hint()
        self._on_select()

    def _selected_items(self, *statuses) -> list:
        out = [self.row_items[i] for i in self.tree.selection() if i in self.row_items]
        return [it for it in out if not statuses or it.status in statuses]

    def select_all(self):
        self.tree.selection_set(self.tree.get_children())

    def _on_select(self):
        sel = self._selected_items()
        self.sel_var.set(f"{len(sel)} selected")
        self._update_actions()
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        if len(sel) == 1:
            it = sel[0]
            self._detail_track("DEVICE", it.device)
            if it.master:
                self._detail_track("MASTER", it.master)
                self.detail.insert("end", f"Matched by {METHOD_TEXT.get(it.method, it.method)} "
                                          f"(confidence {it.score * 100:.0f}%)" + (f", {it.note}" if it.note else "") + "\n")
            else:
                self.detail.insert("end", "No counterpart in the master folder." + (f" {it.note}" if it.note else "") + "\n")
        elif not sel:
            self.detail.insert("end", "Click a song to compare it with the master. Hold Ctrl / Shift to select several, "
                                      "or right-click for more actions.")
        else:
            self.detail.insert("end", f"{len(sel)} songs selected. Select a single song to see its details.")
        self.detail.configure(state="disabled")

    def _detail_track(self, head: str, t: se.Track):
        self.detail.insert("end", f"{head}  ", "h")
        self.detail.insert("end", f"{t.path}   ·   {t.title or '(no tag)'} - {t.artist or '-'}   ·   "
                                  f"{se.fmt_duration(t.duration)}   ·   {se.fmt_size(t.size)}\n")

    def _update_actions(self):
        sel = self._selected_items()
        states = {s: sum(1 for i in sel if i.status == s) for s in STATUS_TEXT}
        can = not self.busy

        def st(b, ok):
            b.configure(state="normal" if (ok and can) else "disabled")
        st(self.btn_rn_master, states[se.RENAMED] > 0)
        st(self.btn_rn_dev, states[se.RENAMED] > 0)
        st(self.btn_same, states[se.REVIEW] > 0)
        st(self.btn_diff, states[se.REVIEW] > 0)
        st(self.btn_reset, any(i.method == "manual" or i.note.startswith("marked") for i in sel))
        # "Copy to master" stays clickable; the action explains what to select
        self.btn_copy_top.configure(text=f"Copy to master ({states[se.MISSING]})" if states[se.MISSING] else "Copy to master",
                                    state="normal" if can else "disabled")
        self.btn_more.configure(state="normal" if can else "disabled")
        self._update_master_summary()

    def _sort_tree(self, tree, col):
        rows = [(tree.set(k, col), k) for k in tree.get_children("")]
        rev = getattr(tree, "_sort_rev", {}).get(col, False)
        rows.sort(key=lambda r: r[0].casefold(), reverse=rev)
        for i, (_, k) in enumerate(rows):
            tree.move(k, "", i)
        if not hasattr(tree, "_sort_rev"):
            tree._sort_rev = {}
        tree._sort_rev[col] = not rev

    # ------------------------------------------------------------- dialogs
    def _confirm(self, title, intro, columns, rows, ok_text, warn: str = "") -> bool:
        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=T.BG)
        win.transient(self.root)
        win.geometry(f"{px(880)}x{px(500)}")
        ttk.Label(win, text=title, style="Title.TLabel", font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=24, pady=(20, 2))
        ttk.Label(win, text=intro, style="Muted.TLabel", wraplength=px(820), justify="left").pack(anchor="w", padx=24, pady=(0, 6))
        if warn:
            ttk.Label(win, text=warn, style="Warn.TLabel", wraplength=px(820), justify="left").pack(anchor="w", padx=24)
        btns = tk.Frame(win, bg=T.BG)
        btns.pack(side="bottom", fill="x", padx=24, pady=(0, 18))
        card = RoundFrame(win, bg=T.BG, fill=T.SURF, radius=16, pad=(10, 10), stretch=True)
        card.pack(fill="both", expand=True, padx=24, pady=10)
        wrap, tree = self._make_tree(card.inner, [(f"c{i}", c) for i, c in enumerate(columns)], [400, 400][:len(columns)], height=12)
        wrap.pack(fill="both", expand=True)
        for r in rows:
            tree.insert("", "end", values=r)
        result = {"ok": False}

        def yes():
            result["ok"] = True
            win.destroy()
        PillButton(btns, ok_text, yes, kind="primary", icon="check", min_width=120).pack(side="right")
        PillButton(btns, "Cancel", win.destroy, kind="secondary", icon="times").pack(side="right", padx=(0, 8))
        set_dark_titlebar(win)
        try:
            win.grab_set()
        except tk.TclError:
            pass
        win.wait_window()
        return result["ok"]

    # ------------------------------------------------------- check-tab actions
    def action_rename(self, target: str):
        items = self._selected_items(se.RENAMED)
        if not items:
            messagebox.showinfo(APP_TITLE, "Select songs with the status \"Renamed\" first.")
            return
        ops = se.plan_renames(items, target)
        good = [o for o in ops if not o.skip_reason]
        if not good:
            messagebox.showinfo(APP_TITLE, "Nothing can be renamed:\n" + "\n".join(
                f"- {os.path.basename(o.src)}: {o.skip_reason}" for o in ops[:8]))
            return
        where = "MASTER" if target == "master" else "DEVICE"
        rows = [(os.path.basename(o.src), os.path.basename(o.dst) if not o.skip_reason
                 else f"(skipped: {o.skip_reason})") for o in ops]
        skipped = len(ops) - len(good)
        if not self._confirm(f"Rename in {where}",
                             f"{len(good)} files will be renamed in the {where}. The songs themselves do not change, "
                             f"matching lyric files (.lrc) are renamed too, and nothing is overwritten.",
                             ("Current name", "New name"), rows, "Rename",
                             warn=f"{skipped} files will be skipped." if skipped else ""):
            return

        def work():
            ok, bad = se.run_renames(good + [o for o in ops if o.skip_reason], lambda s: self.q.put(("log", s)))
            self.q.put(("done", f"Rename finished: {ok} done, {bad} skipped/failed.", True))
        self._run_bg(work, "Renaming…")

    def action_decide(self, verdict: str):
        sel = self._selected_items()
        n = 0
        for it in sel:
            sig = it.device.sig()
            if verdict == "reset":
                if self.decisions.pop(sig, None) is not None:
                    n += 1
            elif it.status == se.REVIEW and it.master is not None:
                self.decisions[sig] = {"v": "same", "master": it.master.path} if verdict == "same" else {"v": "diff"}
                n += 1
        try:
            se.save_json(DECISIONS_PATH, self.decisions)
        except OSError:
            pass
        self.log(f"Decision saved for {n} songs.")
        self._recompare()

    def action_copy_to_master(self):
        items = self._selected_items(se.MISSING)
        if not items:
            messagebox.showinfo(APP_TITLE, "Select songs with the status \"Not in master\" first.\n"
                                           "Tip: click the \"Not in master\" filter, then press Ctrl+A in the list.")
            return
        dest = self.dest_master_var.get().strip()
        if not dest:
            self.show_page("folders")
            messagebox.showwarning(APP_TITLE, "Choose where the songs should be saved in the master folder first "
                                              "(\"Songs found only on the device\").")
            return
        self._copy_flow([it.device for it in items], dest, self.keep_sub_var.get(), self.lrc_var.get(),
                        "Copy to master", "master")

    # --------------------------------------------------------- master tab
    def _refresh_master(self):
        self.mtree.delete(*self.mtree.get_children())
        self.row_master.clear()
        res = self.result
        flt = self.mfilter_var.get()
        q = self.msearch_var.get().strip().casefold()
        shown = on = 0
        for t in sorted(self.master_tracks, key=lambda t: t.label.casefold()):
            if res and t.path in res.on_device:
                status, tag = "✔ On device", "on"
            elif res and t.path in res.maybe_on_device:
                status, tag = "? Maybe on device", "maybe"
            else:
                status, tag = "—  Not yet", ""
            on += 1 if tag == "on" else 0
            if flt == "not" and tag == "on":
                continue
            if flt == "on" and tag != "on":
                continue
            row = (status, t.title or t.stem, t.artist or "-", se.fmt_duration(t.duration), se.fmt_size(t.size), t.rel)
            if q and q not in " ".join(row).casefold():
                continue
            iid = self.mtree.insert("", "end", values=row, tags=(tag,) if tag else ())
            self.row_master[iid] = t
            shown += 1
        total = len(self.master_tracks)
        self.mcount_var.set(f"{shown} songs shown • {total} in master • {on} already on the device" if total
                            else "No data yet. Click \"Check Now\" first.")
        self._update_master_summary()

    def _update_master_summary(self):
        sel = [self.row_master[i] for i in self.mtree.selection() if i in self.row_master]
        total = sum(t.size for t in sel)
        dest = self._device_dest()
        free = se.free_space(dest) if dest else None
        text = "No songs selected."
        over = False
        if sel:
            text = f"{len(sel)} songs selected • {se.fmt_size(total)}"
            if free is not None:
                text += f" • {se.fmt_size(free)} free"
                if total > free:
                    over = True
                    text += "  ⚠ WON'T FIT"
        self.msum_var.set(text)
        self.msum_lbl.configure(style="CardErr.TLabel" if over else "CardMuted.TLabel")
        self.btn_copy_dev.configure(state="normal" if (not self.busy and not over) else "disabled")

    def _device_dest(self) -> str:
        root = self.dev_target_var.get().strip()
        if not root:
            return ""
        sub = self.dev_sub_var.get().strip().strip("/\\")
        return os.path.join(root, sub) if sub else root

    def action_copy_to_device(self):
        sel = [self.row_master[i] for i in self.mtree.selection() if i in self.row_master]
        if not sel:
            messagebox.showinfo(APP_TITLE, "Select songs from the master list first.")
            return
        dest = self._device_dest()
        if not dest or not os.path.isdir(self.dev_target_var.get().strip()):
            messagebox.showwarning(APP_TITLE, "The target folder on the device was not found. Make sure the player is connected.")
            return
        self._copy_flow(sel, dest, False, self.mlrc_var.get(), "Copy to device", "device")

    # ------------------------------------------------------------ shared copy flow
    def _copy_flow(self, tracks, dest, keep_sub, lrc, ok_text, label):
        ops = se.plan_copy(tracks, dest, keep_sub, lrc)
        good = [o for o in ops if not o.skip_reason]
        if not good:
            messagebox.showinfo(APP_TITLE, "Nothing to copy (everything already exists at the destination).")
            return
        total = sum(o.size for o in good)
        free = se.free_space(dest)
        if free is not None and total > free:
            messagebox.showerror(APP_TITLE, f"Not enough space at the destination.\nNeeded {se.fmt_size(total)}, "
                                            f"available {se.fmt_size(free)}.")
            return
        rows = [(os.path.basename(o.src), o.dst if not o.skip_reason else f"(skipped: {o.skip_reason})") for o in ops]
        skipped = len(ops) - len(good)
        warn = f"{skipped} files will be skipped (already there)." if skipped else ""
        if not self._confirm(ok_text, f"{len(good)} files ({se.fmt_size(total)}) will be copied to the {label}. "
                                      f"Nothing is deleted or overwritten.",
                             ("File", "Destination"), rows, "Copy", warn=warn):
            return

        def work():
            def prog(i, n, p):
                self.q.put(("progress", i, n, f"Copying {i}/{n}: {os.path.basename(p)}"))
            ok, bad, sk = se.run_copy(ops, prog, lambda s: self.q.put(("log", s)), self.cancel)
            self.q.put(("done", f"Copy to {label} finished: {ok} done, {bad} failed, {sk} skipped.", True))
        self._run_bg(work, "Copying…")

    # ----------------------------------------------------------------- report
    def export_csv(self):
        if not self.result:
            messagebox.showinfo(APP_TITLE, "No results yet. Click Check Now first.")
            return
        path = filedialog.asksaveasfilename(title="Save report", defaultextension=".csv",
                                            initialfile="sync_report.csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["Status", "Song", "On device", "In master", "Matched by", "Note"])
                for it in sorted(self.result.items, key=lambda i: (STATUS_ORDER[i.status], i.device.label.casefold())):
                    w.writerow([STATUS_TEXT[it.status], it.device.label, it.device.path,
                                it.master.path if it.master else "", METHOD_TEXT.get(it.method, it.method), it.note])
            self.log(f"Report saved: {path}")
            self.status_var.set(f"Report saved: {path}")
        except OSError as e:
            messagebox.showerror(APP_TITLE, f"Could not save the report: {e}")


def main():
    boot = _BOOT
    if boot:
        root = boot.root
    else:
        enable_dpi_awareness()
        root = tk.Tk()
        root.withdraw()
    try:
        ico, png = resource_path("icon.ico"), resource_path("icon.png")
        if os.path.exists(ico) and os.name == "nt":
            root.iconbitmap(default=ico)
        elif os.path.exists(png):
            root.iconphoto(True, tk.PhotoImage(file=png))
    except Exception:
        pass
    try:
        if boot:
            boot.step("Building the interface…", 70)
        app = App(root)
        if boot:
            boot.step("Checking your saved folders…", 90)
        app.startup_check()
        if boot:
            boot.step("Ready", 100)
            root.update()
            time.sleep(0.25)
            boot.close()
        root.deiconify()
        root.lift()
        set_dark_titlebar(root)
        root.mainloop()
    except Exception:
        text = traceback.format_exc()
        log_crash(text)
        if boot:
            boot.close()
        try:
            messagebox.showerror(APP_TITLE, f"The app could not start.\nDetails were saved to:\n{CRASH_PATH}")
        except Exception:
            pass


if __name__ == "__main__":
    main()
