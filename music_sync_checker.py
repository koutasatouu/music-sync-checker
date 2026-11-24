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


class Theme:  # soft dark palette, inspired by music streaming apps
    BG = "#161618"
    SIDE = "#0F0F11"
    SURF = "#1F1F23"
    SURF2 = "#2A2A30"
    HOVER = "#35353C"
    BORDER = "#2C2C32"
    TEXT = "#F2F2F3"
    MUTED = "#9A9AA3"
    DIM = "#6C6C75"
    ACCENT = "#3FCF8E"
    ACCENT_H = "#35B77D"
    ACCENT_FG = "#08150F"
    ENTRY = "#17171A"
    SEL = "#2B3A33"
    OK = "#6EE7A8"
    WARN = "#F5D26B"
    ERR = "#F28B82"
    INFO = "#8AB4F8"


T = Theme


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


# ------------------------------------------------------------------ rounded "pill" button
class PillButton(tk.Canvas):
    KINDS = {  # normal bg, hover bg, text colour
        "primary": (T.ACCENT, T.ACCENT_H, T.ACCENT_FG),
        "secondary": (T.SURF2, T.HOVER, T.TEXT),
        "ghost": (None, T.SURF2, T.MUTED),
    }

    def __init__(self, parent, text, command=None, kind="secondary", bg=T.BG, padx=18, height=34, min_width=0,
                 font=("Segoe UI", 9, "bold")):
        super().__init__(parent, height=height, bg=bg, highlightthickness=0, bd=0)
        self._pb_text, self._pb_cmd, self._pb_kind, self._pb_bg = text, command, kind, bg
        self._pb_padx, self._pb_ht, self._pb_minw, self._pb_font = padx, height, min_width, font
        self._pb_state, self._pb_hover, self._pb_down = "normal", False, False
        self.bind("<Enter>", self._pb_enter)
        self.bind("<Leave>", self._pb_leave)
        self.bind("<ButtonPress-1>", self._pb_press)
        self.bind("<ButtonRelease-1>", self._pb_release)
        self._pb_draw()

    def _pb_round(self, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
               x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.create_polygon(pts, smooth=True, **kw)

    def _pb_draw(self):
        self.delete("all")
        normal, hover, fg = self.KINDS.get(self._pb_kind, self.KINDS["secondary"])
        disabled = self._pb_state == "disabled"
        if disabled:
            fill = "#26302B" if self._pb_kind == "primary" else (None if self._pb_kind == "ghost" else "#232328")
            fg = "#5F7A6C" if self._pb_kind == "primary" else "#5E5E66"
        else:
            fill = hover if self._pb_hover else normal
        probe = self.create_text(0, 0, text=self._pb_text, font=self._pb_font, anchor="nw")
        bb = self.bbox(probe) or (0, 0, 7 * len(str(self._pb_text)), 16)
        self.delete(probe)
        w = max(int(bb[2] - bb[0]) + 2 * self._pb_padx, self._pb_minw)
        tk.Canvas.configure(self, width=w)
        h = self._pb_ht
        if fill:
            self._pb_round(1, 1, w - 1, h - 1, h // 2 - 1, fill=fill, outline=fill)
        self.create_text(w // 2, h // 2, text=self._pb_text, font=self._pb_font, fill=fg)
        try:
            tk.Canvas.configure(self, cursor="arrow" if disabled else "hand2")
        except Exception:
            pass

    def configure(self, cnf=None, **kw):
        redraw = False
        if "text" in kw:
            self._pb_text = kw.pop("text")
            redraw = True
        if "state" in kw:
            self._pb_state = kw.pop("state")
            redraw = True
        if "command" in kw:
            self._pb_cmd = kw.pop("command")
        kw.pop("style", None)
        if kw:
            super().configure(**kw)
        if redraw:
            self._pb_draw()

    config = configure

    def _pb_enter(self, _e):
        self._pb_hover = True
        self._pb_draw()

    def _pb_leave(self, _e):
        self._pb_hover = self._pb_down = False
        self._pb_draw()

    def _pb_press(self, _e):
        self._pb_down = True

    def _pb_release(self, e):
        was_down, self._pb_down = self._pb_down, False
        if was_down and self._pb_state != "disabled" and self._pb_cmd:
            try:
                inside = 0 <= e.x <= int(self.winfo_width()) and 0 <= e.y <= int(self.winfo_height())
            except Exception:
                inside = True
            if inside:
                self._pb_cmd()


# ------------------------------------------------------------------ splash screen
class Splash:
    """Small borderless loading window shown while the app starts up."""

    def __init__(self, root):
        self.root = root
        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Splash.Horizontal.TProgressbar", troughcolor=T.SURF2, background=T.ACCENT,
                        bordercolor=T.SURF2, lightcolor=T.ACCENT, darkcolor=T.ACCENT, thickness=6)
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.configure(bg=T.ACCENT)
        w, h = 460, 250
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
        self.status.pack(pady=(6, 10))
        self.bar = ttk.Progressbar(inner, mode="determinate", maximum=100, length=360,
                                   style="Splash.Horizontal.TProgressbar")
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


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
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
            w, h = (int(m.group(1)), int(m.group(2))) if m else (1180, 780)
            w, h = max(900, min(w, sw - 40)), max(560, min(h, sh - 110))
            self.root.minsize(min(1000, w), min(620, h))
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
        st.configure("CardTitle.TLabel", background=T.SURF, foreground=T.TEXT, font=("Segoe UI", 11, "bold"))
        st.configure("CardMuted.TLabel", background=T.SURF, foreground=T.MUTED, font=("Segoe UI", 9))
        st.configure("Muted.TLabel", background=T.BG, foreground=T.MUTED, font=("Segoe UI", 9))
        st.configure("Hint.TLabel", background=T.BG, foreground=T.MUTED, font=("Segoe UI", 9))
        st.configure("Warn.TLabel", background=T.BG, foreground=T.WARN, font=("Segoe UI", 9))
        st.configure("Err.TLabel", background=T.BG, foreground=T.ERR, font=("Segoe UI", 9, "bold"))
        st.configure("Title.TLabel", background=T.BG, foreground=T.TEXT, font=("Segoe UI", 22, "bold"))
        st.configure("Bar.TLabel", background=T.SURF, foreground=T.MUTED, font=("Segoe UI", 9))
        st.configure("TEntry", fieldbackground=T.ENTRY, foreground=T.TEXT, insertcolor=T.TEXT, bordercolor=T.BORDER,
                     lightcolor=T.BORDER, darkcolor=T.BORDER, padding=6)
        st.map("TEntry", bordercolor=[("focus", T.ACCENT)], lightcolor=[("focus", T.ACCENT)], darkcolor=[("focus", T.ACCENT)])
        st.configure("TCombobox", fieldbackground=T.ENTRY, background=T.SURF2, foreground=T.TEXT, arrowcolor=T.TEXT,
                     bordercolor=T.BORDER, lightcolor=T.BORDER, darkcolor=T.BORDER, padding=5)
        st.map("TCombobox", fieldbackground=[("readonly", T.ENTRY)], foreground=[("readonly", T.TEXT)])
        st.configure("TCheckbutton", background=T.BG, foreground=T.TEXT, focuscolor=T.BG)
        st.map("TCheckbutton", background=[("active", T.BG)])
        st.configure("Card.TCheckbutton", background=T.SURF, foreground=T.TEXT, focuscolor=T.SURF)
        st.map("Card.TCheckbutton", background=[("active", T.SURF)])
        st.configure("Chip.Toolbutton", background=T.SURF2, foreground=T.TEXT, padding=(14, 6), relief="flat",
                     borderwidth=0, focuscolor=T.SURF2, font=("Segoe UI", 9, "bold"))
        st.map("Chip.Toolbutton", background=[("selected", T.TEXT), ("active", T.HOVER)],
               foreground=[("selected", "#121212")])
        st.configure("Soft.Horizontal.TProgressbar", troughcolor=T.SURF2, background=T.ACCENT, bordercolor=T.SURF,
                     lightcolor=T.ACCENT, darkcolor=T.ACCENT, thickness=6)
        st.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        st.configure("Treeview", background=T.BG, foreground=T.TEXT, fieldbackground=T.BG, borderwidth=0,
                     rowheight=38, font=("Segoe UI", 10))
        st.map("Treeview", background=[("selected", T.SEL)], foreground=[("selected", "#FFFFFF")])
        st.configure("Treeview.Heading", background=T.BG, foreground=T.DIM, relief="flat", borderwidth=0,
                     font=("Segoe UI", 8, "bold"), padding=(8, 6))
        st.map("Treeview.Heading", background=[("active", T.BG)], foreground=[("active", T.TEXT)])
        st.configure("Vertical.TScrollbar", background=T.SURF2, troughcolor=T.BG, bordercolor=T.BG,
                     arrowcolor=T.MUTED, lightcolor=T.SURF2, darkcolor=T.SURF2, relief="flat", gripcount=0)
        st.map("Vertical.TScrollbar", background=[("active", T.HOVER)])
        self.root.option_add("*TCombobox*Listbox.background", T.ENTRY)
        self.root.option_add("*TCombobox*Listbox.foreground", T.TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", T.ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", T.ACCENT_FG)

    # -------------------------------------------------------------------- UI helpers
    def _card(self, parent, pady=(0, 14)):
        outer = tk.Frame(parent, bg=T.SURF)
        outer.pack(fill="x", pady=pady)
        inner = ttk.Frame(outer, style="Card.TFrame", padding=(18, 14))
        inner.pack(fill="both", expand=True)
        return inner

    def _heading(self, parent, title, sub=""):
        ttk.Label(parent, text=title, style="Title.TLabel").pack(anchor="w")
        if sub:
            ttk.Label(parent, text=sub, style="Muted.TLabel", wraplength=860, justify="left").pack(anchor="w", pady=(2, 0))

    def _menu(self, parent):
        return tk.Menu(parent, tearoff=0, bg=T.SURF2, fg=T.TEXT, activebackground=T.ACCENT,
                       activeforeground=T.ACCENT_FG, relief="flat", bd=0, font=("Segoe UI", 9))

    def _make_tree(self, parent, columns, widths, height=10):
        wrap = ttk.Frame(parent)
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
        side = tk.Frame(self.root, bg=T.SIDE, width=220)
        side.grid(row=0, column=0, sticky="ns")
        side.pack_propagate(False)
        tk.Label(side, text="♫  Music Sync", bg=T.SIDE, fg=T.TEXT, font=("Segoe UI", 15, "bold"),
                 anchor="w").pack(fill="x", padx=20, pady=(24, 18))
        tk.Label(side, text="LIBRARY", bg=T.SIDE, fg=T.DIM, font=("Segoe UI", 8, "bold"),
                 anchor="w").pack(fill="x", padx=22, pady=(0, 4))
        for key, text in (("folders", "▣   Folders"), ("check", "✔   Device check"),
                          ("master", "♫   Master → device"), ("log", "≡   Activity log")):
            self._nav_item(side, key, text)

        bottom = tk.Frame(side, bg=T.SIDE)
        bottom.pack(side="bottom", fill="x", padx=18, pady=18)
        self.btn_scan = PillButton(bottom, "▶  Check Now", self.start_scan, kind="primary", bg=T.SIDE, height=42,
                                   min_width=184, font=("Segoe UI", 10, "bold"))
        self.btn_scan.pack(anchor="w")
        self.btn_report = PillButton(bottom, "Save report (.csv)", self.export_csv, kind="ghost", bg=T.SIDE, height=30,
                                     min_width=184)
        self.btn_report.pack(anchor="w", pady=(8, 0))

        # ---------- content area
        self.content = tk.Frame(self.root, bg=T.BG)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.pages["folders"] = self._build_folders_page()
        self.pages["check"] = self._build_check_page()
        self.pages["master"] = self._build_master_page()
        self.pages["log"] = self._build_log_page()

        # ---------- bottom status bar
        bar = tk.Frame(self.root, bg=T.SURF, height=52)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        bar.grid_propagate(False)
        bar.columnconfigure(0, weight=1)
        bar.rowconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="Choose your master folder and device, then click Check Now.")
        ttk.Label(bar, textvariable=self.status_var, style="Bar.TLabel").grid(row=0, column=0, sticky="w", padx=(22, 12))
        self.progress = ttk.Progressbar(bar, mode="determinate", length=240, style="Soft.Horizontal.TProgressbar")
        self.progress.grid(row=0, column=1, padx=(0, 12))
        self.btn_cancel = PillButton(bar, "Cancel", self.cancel_job, kind="secondary", bg=T.SURF, height=30, padx=14)
        self.btn_cancel.grid(row=0, column=2, padx=(0, 18))
        self.btn_cancel.configure(state="disabled")

    def _nav_item(self, parent, key, text):
        lbl = tk.Label(parent, text=text, bg=T.SIDE, fg=T.MUTED, font=("Segoe UI", 10, "bold"), anchor="w",
                       padx=22, pady=9, cursor="hand2")
        lbl.pack(fill="x")
        lbl.bind("<Button-1>", lambda e, k=key: self.show_page(k))
        lbl.bind("<Enter>", lambda e, l=lbl, k=key: l.configure(bg=T.SURF if k == self.page else T.SURF2) or None)
        lbl.bind("<Leave>", lambda e, l=lbl, k=key: l.configure(bg=T.SURF if k == self.page else T.SIDE))
        self.nav[key] = lbl

    def show_page(self, name):
        for f in self.pages.values():
            f.pack_forget()
        self.pages[name].pack(fill="both", expand=True, padx=30, pady=(26, 10))
        self.page = name
        for k, lbl in self.nav.items():
            on = k == name
            lbl.configure(bg=T.SURF if on else T.SIDE, fg=T.TEXT if on else T.MUTED)

    # -------------------------------------------------------------------- folders page
    def _build_folders_page(self):
        page = tk.Frame(self.content, bg=T.BG)
        self._heading(page, "Your folders", "Tell the app where your full collection lives and which player to check.")
        body = ttk.Frame(page)
        body.pack(fill="x", pady=(18, 0))

        c = self._card(body)
        ttk.Label(c, text="Master folder", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(c, text="Your complete collection on the HDD/SSD.", style="CardMuted.TLabel").grid(row=1, column=0, sticky="w")
        c.columnconfigure(0, weight=1)
        self.master_var = tk.StringVar()
        ttk.Entry(c, textvariable=self.master_var).grid(row=2, column=0, sticky="we", pady=(10, 0), padx=(0, 10))
        self.btn_master = PillButton(c, "Browse…", self.pick_master, bg=T.SURF)
        self.btn_master.grid(row=2, column=1, pady=(10, 0))

        c = self._card(body)
        c.columnconfigure(0, weight=1)
        ttk.Label(c, text="Device", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(c, text="Your MP3 player or SD card. Player with 2 SD cards? Add both; they count as one device.",
                  style="CardMuted.TLabel").grid(row=1, column=0, sticky="w")
        self.dev_list = tk.Listbox(c, height=3, bg=T.ENTRY, fg=T.TEXT, selectbackground=T.SEL, selectforeground="#FFFFFF",
                                   relief="flat", highlightthickness=1, highlightbackground=T.BORDER,
                                   highlightcolor=T.ACCENT, exportselection=False, font=("Segoe UI", 10), bd=0)
        self.dev_list.grid(row=2, column=0, rowspan=2, sticky="we", pady=(10, 0), padx=(0, 10))
        self.btn_add_dev = PillButton(c, "+ Add", self.add_device, bg=T.SURF, min_width=90)
        self.btn_add_dev.grid(row=2, column=1, pady=(10, 0))
        self.btn_del_dev = PillButton(c, "Remove", self.remove_device, bg=T.SURF, min_width=90)
        self.btn_del_dev.grid(row=3, column=1, pady=(6, 0), sticky="n")

        c = self._card(body)
        c.columnconfigure(0, weight=1)
        ttk.Label(c, text="Songs found only on the device", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(c, text="Where \"Copy to master\" should put them.", style="CardMuted.TLabel").grid(row=1, column=0, sticky="w")
        self.dest_master_var = tk.StringVar()
        ttk.Entry(c, textvariable=self.dest_master_var).grid(row=2, column=0, sticky="we", pady=(10, 0), padx=(0, 10))
        PillButton(c, "Browse…", self.pick_dest_master, bg=T.SURF).grid(row=2, column=1, pady=(10, 0))
        opts = ttk.Frame(c, style="Card.TFrame")
        opts.grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.keep_sub_var = tk.BooleanVar(value=False)
        self.lrc_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Keep sub-folders from the device", variable=self.keep_sub_var,
                        style="Card.TCheckbutton").pack(side="left", padx=(0, 22))
        ttk.Checkbutton(opts, text="Also copy lyrics (.lrc)", variable=self.lrc_var,
                        style="Card.TCheckbutton").pack(side="left")

        self.warn_var = tk.StringVar(value="")
        if not se.HAS_MUTAGEN:
            self.warn_var.set("The metadata reader is not installed, so matching only uses file names and sizes. "
                              + ("Run build.bat again." if getattr(sys, "frozen", False) else "Click the button to install it."))
        self.warn_row = ttk.Frame(body)
        self.warn_row.pack(fill="x")
        ttk.Label(self.warn_row, textvariable=self.warn_var, style="Warn.TLabel", wraplength=700).pack(side="left")
        if not se.HAS_MUTAGEN and not getattr(sys, "frozen", False):
            PillButton(self.warn_row, "Install mutagen", self.install_mutagen, bg=T.BG, height=30).pack(side="left", padx=10)
        return page

    # -------------------------------------------------------------------- device check page
    def _build_check_page(self):
        p = tk.Frame(self.content, bg=T.BG)
        head = ttk.Frame(p)
        head.pack(fill="x")
        self._heading(head, "Device check")

        bar = ttk.Frame(p)
        bar.pack(fill="x", pady=(14, 0))
        self.filter_var = tk.StringVar(value="all")
        self.chip_vars = {k: tk.StringVar(value=t) for k, t in
                          [("all", "All"), (se.SYNC, STATUS_TEXT[se.SYNC]), (se.RENAMED, STATUS_TEXT[se.RENAMED]),
                           (se.MISSING, STATUS_TEXT[se.MISSING]), (se.REVIEW, STATUS_TEXT[se.REVIEW])]}
        for key in ("all", se.MISSING, se.RENAMED, se.REVIEW, se.SYNC):
            ttk.Radiobutton(bar, textvariable=self.chip_vars[key], value=key, variable=self.filter_var,
                            style="Chip.Toolbutton", command=self._refresh_results).pack(side="left", padx=(0, 8))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._refresh_results())
        ttk.Entry(bar, textvariable=self.search_var, width=24).pack(side="right")
        ttk.Label(bar, text="Search", style="Muted.TLabel").pack(side="right", padx=(0, 8))

        self.hint_var = tk.StringVar(value=HINTS["all"])
        ttk.Label(p, textvariable=self.hint_var, style="Hint.TLabel", wraplength=860, justify="left").pack(anchor="w", pady=(10, 8))

        act = ttk.Frame(p)
        act.pack(fill="x", pady=(0, 8))
        self.btn_copy_top = PillButton(act, "Copy to master", self.action_copy_to_master, kind="primary")
        self.btn_copy_top.pack(side="left", padx=(0, 8))
        self.btn_copy_master = self.btn_copy_top
        self.btn_rn_master = PillButton(act, "Rename in master", lambda: self.action_rename("master"))
        self.btn_rn_dev = PillButton(act, "Rename on device", lambda: self.action_rename("device"))
        self.btn_same = PillButton(act, "Same song", lambda: self.action_decide("same"))
        self.btn_diff = PillButton(act, "Not same", lambda: self.action_decide("diff"))
        self.btn_reset = PillButton(act, "Undo", lambda: self.action_decide("reset"), kind="ghost")
        for b in (self.btn_rn_master, self.btn_rn_dev, self.btn_same, self.btn_diff, self.btn_reset):
            b.pack(side="left", padx=(0, 8))
        self.btn_more = PillButton(act, "⋯", self._more_menu, kind="secondary", padx=14)
        self.btn_more.pack(side="right")
        self.sel_var = tk.StringVar(value="0 selected")
        ttk.Label(act, textvariable=self.sel_var, style="Muted.TLabel").pack(side="right", padx=(0, 12))

        self.dest_show_var = tk.StringVar(value="")
        dest = ttk.Frame(p)
        dest.pack(fill="x", pady=(0, 6))
        ttk.Label(dest, textvariable=self.dest_show_var, style="Muted.TLabel").pack(side="left")
        PillButton(dest, "Change…", self.pick_dest_master, kind="ghost", height=26, padx=10).pack(side="left", padx=6)

        self.detail = tk.Text(p, height=3, wrap="word", state="disabled", bg=T.SURF, fg="#D4D4D8",
                              font=("Consolas", 9), relief="flat", padx=12, pady=8, bd=0)
        self.detail.tag_configure("h", foreground=T.ACCENT, font=("Consolas", 9, "bold"))
        self.detail.pack(side="bottom", fill="x", pady=(8, 0))

        cols = [("status", "Status"), ("label", "Song"), ("dev", "On device"), ("mst", "In master"),
                ("how", "Matched by"), ("note", "Note")]
        wrap, self.tree = self._make_tree(p, cols, [120, 230, 220, 200, 100, 150], height=8)
        wrap.pack(fill="both", expand=True)
        for cid, mw in (("status", 110), ("how", 90)):
            self.tree.column(cid, minwidth=mw, stretch=False)
        for s, c in STATUS_COLOR.items():
            self.tree.tag_configure(s, foreground=c)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())
        self.tree.bind("<Control-a>", lambda e: (self.select_all(), "break")[1])
        self.empty_lbl = tk.Label(self.tree, text="No results yet.\nPick your folders, then click \"Check Now\".",
                                  bg=T.BG, fg=T.DIM, font=("Segoe UI", 12))
        self.empty_lbl.place(relx=0.5, rely=0.5, anchor="center")

        # right-click menu (several bindings so it works however Tk reports the click)
        self.ctx = self._menu(self.tree)
        self.ctx_items = [("Copy to master…", self.action_copy_to_master),
                          ("Rename in master to match device…", lambda: self.action_rename("master")),
                          ("Rename on device to match master…", lambda: self.action_rename("device")),
                          None,
                          ("Same song", lambda: self.action_decide("same")),
                          ("Not the same song", lambda: self.action_decide("diff")),
                          ("Undo my decision", lambda: self.action_decide("reset")),
                          None,
                          ("Select all shown", self.select_all)]
        for it in self.ctx_items:
            if it is None:
                self.ctx.add_separator()
            else:
                self.ctx.add_command(label=it[0], command=it[1])
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

    def _popup_menu(self, event):
        try:
            self._refresh_ctx()
            x = getattr(event, "x_root", None)
            y = getattr(event, "y_root", None)
            if x is None or (x == 0 and y == 0):
                x, y = self.root.winfo_pointerx(), self.root.winfo_pointery()
            self.ctx.tk_popup(x, y)
        except Exception as e:
            self.log(f"[ERROR] menu: {e}")
        finally:
            try:
                self.ctx.grab_release()
            except Exception:
                pass

    def _more_menu(self):
        self._refresh_ctx()
        try:
            self.ctx.tk_popup(self.btn_more.winfo_rootx() - 150, self.btn_more.winfo_rooty() + self.btn_more.winfo_height())
        finally:
            try:
                self.ctx.grab_release()
            except Exception:
                pass

    def _refresh_ctx(self):
        sel = self._selected_items()
        n = {s: sum(1 for i in sel if i.status == s) for s in STATUS_TEXT}
        rules = {"Copy to master…": n[se.MISSING] > 0,
                 "Rename in master to match device…": n[se.RENAMED] > 0,
                 "Rename on device to match master…": n[se.RENAMED] > 0,
                 "Same song": n[se.REVIEW] > 0, "Not the same song": n[se.REVIEW] > 0,
                 "Undo my decision": any(i.method == "manual" or i.note.startswith("marked") for i in sel)}
        for label, ok in rules.items():
            try:
                self.ctx.entryconfigure(label, state="normal" if (ok and not self.busy) else "disabled")
            except Exception:
                pass

    # -------------------------------------------------------------------- master page
    def _build_master_page(self):
        p = tk.Frame(self.content, bg=T.BG)
        self._heading(p, "Master → device", "Pick songs from your full collection to take with you, then copy them to the player.")
        bar = ttk.Frame(p)
        bar.pack(fill="x", pady=(14, 0))
        self.mfilter_var = tk.StringVar(value="not")
        for key, text in (("not", "Not on device"), ("on", "On device"), ("all", "All")):
            ttk.Radiobutton(bar, text=text, value=key, variable=self.mfilter_var, style="Chip.Toolbutton",
                            command=self._refresh_master).pack(side="left", padx=(0, 8))
        self.msearch_var = tk.StringVar()
        self.msearch_var.trace_add("write", lambda *_: self._refresh_master())
        ttk.Entry(bar, textvariable=self.msearch_var, width=24).pack(side="right")
        ttk.Label(bar, text="Search", style="Muted.TLabel").pack(side="right", padx=(0, 8))
        self.mcount_var = tk.StringVar(value="")
        ttk.Label(p, textvariable=self.mcount_var, style="Muted.TLabel").pack(anchor="w", pady=(10, 6))

        box = tk.Frame(p, bg=T.SURF)
        box.pack(side="bottom", fill="x", pady=(10, 0))
        inner = ttk.Frame(box, style="Card.TFrame", padding=(16, 12))
        inner.pack(fill="x")
        inner.columnconfigure(3, weight=1)
        ttk.Label(inner, text="Target", style="Card.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.dev_target_var = tk.StringVar()
        self.dev_combo = ttk.Combobox(inner, textvariable=self.dev_target_var, state="readonly", width=16)
        self.dev_combo.grid(row=0, column=1, sticky="w")
        self.dev_combo.bind("<<ComboboxSelected>>", lambda e: self._update_master_summary())
        ttk.Label(inner, text="Sub-folder (optional)", style="Card.TLabel").grid(row=0, column=2, sticky="e", padx=(16, 8))
        self.dev_sub_var = tk.StringVar()
        ttk.Entry(inner, textvariable=self.dev_sub_var).grid(row=0, column=3, sticky="we")
        self.dev_sub_var.trace_add("write", lambda *_: self._update_master_summary())
        self.mlrc_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(inner, text="Also copy lyrics (.lrc)", variable=self.mlrc_var,
                        style="Card.TCheckbutton").grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.msum_var = tk.StringVar(value="No songs selected.")
        self.msum_lbl = ttk.Label(inner, textvariable=self.msum_var, style="CardMuted.TLabel")
        self.msum_lbl.grid(row=1, column=3, sticky="w", pady=(8, 0), padx=(0, 12))
        self.btn_copy_dev = PillButton(inner, "Copy to device", self.action_copy_to_device, kind="primary", bg=T.SURF)
        self.btn_copy_dev.grid(row=0, column=4, rowspan=2, padx=(12, 0))

        cols = [("status", "Status"), ("title", "Song"), ("artist", "Artist"), ("dur", "Length"),
                ("size", "Size"), ("loc", "Location in master")]
        wrap, self.mtree = self._make_tree(p, cols, [140, 260, 180, 70, 80, 260], height=10)
        wrap.pack(fill="both", expand=True)
        self.mtree.tag_configure("on", foreground=T.OK)
        self.mtree.tag_configure("maybe", foreground=T.INFO)
        self.mtree.bind("<<TreeviewSelect>>", lambda e: self._update_master_summary())
        self.mtree.bind("<Control-a>", lambda e: (self.mtree.selection_set(self.mtree.get_children()), "break")[1])
        self.mctx = self._menu(self.mtree)
        self.mctx.add_command(label="Copy to device…", command=self.action_copy_to_device)
        self.mctx.add_command(label="Select all shown",
                              command=lambda: self.mtree.selection_set(self.mtree.get_children()))
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
            self.mctx.tk_popup(event.x_root, event.y_root)
        except Exception as e:
            self.log(f"[ERROR] menu: {e}")
        finally:
            try:
                self.mctx.grab_release()
            except Exception:
                pass

    # -------------------------------------------------------------------- log page
    def _build_log_page(self):
        p = tk.Frame(self.content, bg=T.BG)
        self._heading(p, "Activity log", "Everything the app did, including warnings and errors.")
        self.logbox = tk.Text(p, wrap="word", state="disabled", bg=T.SURF, fg="#D4D4D8", insertbackground="#FFFFFF",
                              font=("Consolas", 9), relief="flat", padx=12, pady=10, bd=0)
        self.logbox.pack(fill="both", expand=True, pady=(14, 0))
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
        self.dest_show_var.set(f"Copies go to:  {d}" if d else "Copies go to:  (not set yet - click Change…)")

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
        devs = self._device_list()
        self.dev_combo.configure(values=devs)
        cur = keep or self.dev_target_var.get()
        self.dev_target_var.set(cur if cur in devs else (devs[0] if devs else ""))

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
        for b in (self.btn_scan, self.btn_master, self.btn_add_dev, self.btn_del_dev, self.btn_copy_dev):
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
        win.geometry("860x460")
        ttk.Label(win, text=title, style="Title.TLabel", font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=22, pady=(18, 2))
        ttk.Label(win, text=intro, style="Muted.TLabel", wraplength=800, justify="left").pack(anchor="w", padx=22, pady=(0, 6))
        if warn:
            ttk.Label(win, text=warn, style="Warn.TLabel", wraplength=800, justify="left").pack(anchor="w", padx=22)
        wrap, tree = self._make_tree(win, [(f"c{i}", c) for i, c in enumerate(columns)], [400, 400][:len(columns)], height=12)
        wrap.pack(fill="both", expand=True, padx=22, pady=8)
        for r in rows:
            tree.insert("", "end", values=r)
        result = {"ok": False}
        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=22, pady=(0, 16))

        def yes():
            result["ok"] = True
            win.destroy()
        PillButton(btns, ok_text, yes, kind="primary", min_width=100).pack(side="right")
        PillButton(btns, "Cancel", win.destroy, kind="secondary").pack(side="right", padx=(0, 8))
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
        self.msum_lbl.configure(style="Err.TLabel" if over else "CardMuted.TLabel")
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
