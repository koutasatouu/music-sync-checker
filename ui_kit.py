"""
ui_kit - smooth, anti-aliased rounded widgets for Tkinter (no third-party packages).

Tk's canvas shapes are jagged, so every rounded shape here is rendered as a small PNG with
true anti-aliasing (pure Python, cached) and drawn as an image. Icons are Font Awesome 4.7.
"""
from __future__ import annotations

import base64
import math
import os
import struct
import sys
import zlib
import tkinter as tk


class Theme:
    BG = "#161618"
    SIDE = "#0F0F11"
    SURF = "#1F1F23"
    SURF2 = "#2A2A30"
    HOVER = "#35353C"
    BORDER = "#2C2C32"
    BORDER2 = "#4A4A52"
    TEXT = "#F2F2F3"
    MUTED = "#9A9AA3"
    DIM = "#6C6C75"
    ACCENT = "#3FCF8E"
    ACCENT_H = "#35B77D"
    ACCENT_FG = "#08150F"
    BADGE = "#22332B"
    ENTRY = "#17171A"
    SEL = "#2B3A33"
    OK = "#6EE7A8"
    WARN = "#F5D26B"
    ERR = "#F28B82"
    INFO = "#8AB4F8"


T = Theme

# ---------------------------------------------------------------- scaling (high-DPI screens)
SCALE = 1.0


def init_scale(root) -> float:
    global SCALE
    try:
        SCALE = max(1.0, min(3.0, float(root.winfo_fpixels("1i")) / 96.0))
    except Exception:
        SCALE = 1.0
    return SCALE


def px(n: float) -> int:
    return max(1, int(round(n * SCALE)))


# ---------------------------------------------------------------- icons (Font Awesome 4.7)
ICONS = {
    "folder": "", "folder_open": "", "music": "", "check": "", "sync": "",
    "copy": "", "pencil": "", "search": "", "plus": "", "times": "",
    "hdd": "", "play": "", "download": "", "upload": "", "list": "",
    "check_circle": "", "warn": "", "question": "", "exchange": "",
    "headphones": "", "undo": "", "ellipsis": "", "tasks": "", "save": "",
    "info": "", "usb": "", "times_circle": "",
}
FA_FAMILY = "FontAwesome"
_FA_OK = False


def load_icon_font(path: str) -> bool:
    """Register the bundled Font Awesome file for this process (Windows). Returns True if usable."""
    global _FA_OK
    _FA_OK = False
    if os.name == "nt" and os.path.exists(path):
        try:
            import ctypes
            added = ctypes.windll.gdi32.AddFontResourceExW(ctypes.c_wchar_p(path), 0x10, None)  # FR_PRIVATE
            _FA_OK = added > 0
        except Exception:
            _FA_OK = False
    return _FA_OK


def has_icons() -> bool:
    return _FA_OK


def icon_font(size: float = 10):
    return (FA_FAMILY, int(size))


# ---------------------------------------------------------------- anti-aliased rounded shapes
_CACHE: dict = {}


def _rgb(h: str):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _cov(px_, py_, cx, cy, hw, hh, rad) -> float:
    dx = abs(px_ - cx) - (hw - rad)
    dy = abs(py_ - cy) - (hh - rad)
    d = math.hypot(max(dx, 0.0), max(dy, 0.0)) + min(max(dx, dy), 0.0) - rad
    return min(1.0, max(0.0, 0.5 - d))


def _chunk(tag: bytes, data: bytes) -> bytes:
    body = tag + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def render_png(w: int, h: int, r: int, fill: str, border: str | None = None, bw: int = 1) -> bytes:
    """Rounded rectangle (or circle) as an RGBA PNG with smooth edges."""
    w, h = max(1, int(w)), max(1, int(h))
    r = max(0, min(int(r), w // 2, h // 2))
    bw = max(1, int(bw))
    fr = _rgb(fill)
    br = _rgb(border) if border else None

    def pix(x: int, y: int) -> bytes:
        a_o = _cov(x + 0.5, y + 0.5, w / 2, h / 2, w / 2, h / 2, r)
        if a_o <= 0.0:
            return b"\x00\x00\x00\x00"
        if br is None:
            col = fr
        else:
            a_i = _cov(x + 0.5, y + 0.5, w / 2, h / 2, w / 2 - bw, h / 2 - bw, max(r - bw, 0))
            t = min(1.0, a_i / a_o)
            col = tuple(int(fr[i] * t + br[i] * (1 - t) + 0.5) for i in range(3))
        return bytes((col[0], col[1], col[2], int(a_o * 255 + 0.5)))

    edge = max(r, bw if br else 0, 1)
    rows: dict = {}

    def row_bytes(yy: int) -> bytes:
        if yy in rows:
            return rows[yy]
        if w <= 2 * edge:
            data = b"".join(pix(x, yy) for x in range(w))
        else:
            left = [pix(x, yy) for x in range(edge)]
            mid = pix(edge, yy)
            data = b"".join(left) + mid * (w - 2 * edge) + b"".join(reversed(left))
        rows[yy] = b"\x00" + data
        return rows[yy]

    out = []
    for y in range(h):
        if h <= 2 * edge:
            yy = y
        elif y < edge:
            yy = y
        elif y >= h - edge:
            yy = h - 1 - y
        else:
            yy = edge
        if h > 2 * edge and y >= h - edge:
            out.append(row_bytes(yy))      # mirrored row (symmetric shape)
        else:
            out.append(row_bytes(yy))
    raw = b"".join(out)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 6)) + _chunk(b"IEND", b""))


def shape_image(w: int, h: int, r: int, fill: str, border: str | None = None, bw: int = 1):
    key = (int(w), int(h), int(r), fill, border, bw)
    img = _CACHE.get(key)
    if img is None:
        img = tk.PhotoImage(data=base64.b64encode(render_png(w, h, r, fill, border, bw)))
        if w * h <= 40000:                      # keep small shapes; big ones are owned by their widget
            if len(_CACHE) > 400:
                _CACHE.clear()
            _CACHE[key] = img
    return img


def _measure(canvas, text, font) -> int:
    tid = canvas.create_text(0, 0, text=text, font=font, anchor="nw")
    bb = canvas.bbox(tid)
    canvas.delete(tid)
    if not bb:
        return int(7 * len(str(text)))
    return int(bb[2] - bb[0])


# ---------------------------------------------------------------- buttons
class PillButton(tk.Canvas):
    """Fully rounded button with an optional Font Awesome icon."""
    KINDS = {
        "primary": (T.ACCENT, T.ACCENT_H, T.ACCENT_FG),
        "secondary": (T.SURF2, T.HOVER, T.TEXT),
        "ghost": (None, T.SURF2, T.MUTED),
        "danger": (T.SURF2, "#4A2B2B", T.ERR),
    }

    def __init__(self, parent, text="", command=None, kind="secondary", icon=None, bg=T.BG, height=36,
                 padx=18, min_width=0, size=9):
        super().__init__(parent, height=px(height), bg=bg, highlightthickness=0, bd=0)
        self._pb_text, self._pb_cmd, self._pb_kind, self._pb_icon = text, command, kind, icon
        self._pb_ht, self._pb_padx, self._pb_minw, self._pb_size = px(height), px(padx), px(min_width), size
        self._pb_state, self._pb_hover, self._pb_down, self._pb_img = "normal", False, False, None
        self.bind("<Enter>", self._pb_enter)
        self.bind("<Leave>", self._pb_leave)
        self.bind("<ButtonPress-1>", self._pb_press)
        self.bind("<ButtonRelease-1>", self._pb_release)
        self._pb_draw()

    # hooks that Chip overrides
    def _pb_label(self):
        return self._pb_text

    def _pb_colors(self):
        normal, hover, fg = self.KINDS.get(self._pb_kind, self.KINDS["secondary"])
        if self._pb_state == "disabled":
            if self._pb_kind == "primary":
                return "#26302B", "#5F7A6C"
            return (None if self._pb_kind == "ghost" else "#232328"), "#5E5E66"
        return (hover if self._pb_hover else normal), fg

    def _pb_draw(self):
        self.delete("all")
        fill, fg = self._pb_colors()
        label = str(self._pb_label() or "")
        lfont = ("Segoe UI", self._pb_size, "bold")
        use_icon = bool(self._pb_icon) and has_icons()
        iw = _measure(self, ICONS.get(self._pb_icon, ""), icon_font(self._pb_size + 1)) if use_icon else 0
        tw = _measure(self, label, lfont) if label else 0
        gap = px(8) if (iw and tw) else 0
        total = iw + gap + tw
        w = max(total + 2 * self._pb_padx, self._pb_minw)
        h = self._pb_ht
        tk.Canvas.configure(self, width=w)
        if fill:
            self._pb_img = shape_image(w, h, h // 2, fill)
            self.create_image(0, 0, image=self._pb_img, anchor="nw")
        x = (w - total) // 2
        if iw:
            self.create_text(x, h // 2, text=ICONS[self._pb_icon], font=icon_font(self._pb_size + 1), fill=fg, anchor="w")
            x += iw + gap
        if tw:
            self.create_text(x, h // 2, text=label, font=lfont, fill=fg, anchor="w")
        try:
            tk.Canvas.configure(self, cursor="arrow" if self._pb_state == "disabled" else "hand2")
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


class Chip(PillButton):
    """One option of a radio group, drawn as a rounded segment. Selected = light pill."""

    def __init__(self, parent, variable, value, text=None, textvariable=None, command=None, bg=T.BG,
                 icon=None, height=34):
        self._ch_var, self._ch_val, self._ch_tv, self._ch_cmd = variable, value, textvariable, command
        super().__init__(parent, text=text or "", command=self._ch_click, kind="secondary", icon=icon, bg=bg,
                         height=height, padx=16)
        self._ch_ids = []
        for var in (variable, textvariable):
            if var is not None:
                self._ch_ids.append((var, var.trace_add("write", lambda *a: self._ch_refresh())))
        self.bind("<Destroy>", self._ch_destroy)

    def _pb_label(self):
        return self._ch_tv.get() if self._ch_tv is not None else self._pb_text

    def _pb_colors(self):
        on = self._ch_var.get() == self._ch_val
        if on:
            return (T.TEXT, "#121212")
        return (T.HOVER if self._pb_hover else T.SURF2), T.TEXT

    def _ch_click(self):
        self._ch_var.set(self._ch_val)
        if self._ch_cmd:
            self._ch_cmd()

    def _ch_refresh(self):
        try:
            self._pb_draw()
        except tk.TclError:
            pass

    def _ch_destroy(self, e):
        for var, tid in self._ch_ids:
            try:
                var.trace_remove("write", tid)
            except Exception:
                pass


# ---------------------------------------------------------------- checkbox + radio
class _Choice(tk.Canvas):
    IND = 22
    SHAPE = "check"

    def __init__(self, parent, text, variable, value=None, command=None, bg=T.BG):
        ind = px(self.IND)
        super().__init__(parent, height=ind + px(8), bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self._c_text, self._c_var, self._c_val, self._c_cmd = text, variable, value, command
        self._c_hover, self._c_imgs = False, []
        self._c_tid = variable.trace_add("write", lambda *a: self._c_refresh())
        self.bind("<Destroy>", self._c_destroy)
        self.bind("<Enter>", lambda e: self._c_set_hover(True))
        self.bind("<Leave>", lambda e: self._c_set_hover(False))
        self.bind("<ButtonRelease-1>", self._c_click)
        self._c_draw()

    def _c_on(self) -> bool:
        raise NotImplementedError

    def _c_toggle(self):
        raise NotImplementedError

    def _c_set_hover(self, v):
        self._c_hover = v
        self._c_refresh()

    def _c_click(self, _e):
        self._c_toggle()
        if self._c_cmd:
            self._c_cmd()

    def _c_refresh(self):
        try:
            self._c_draw()
        except tk.TclError:
            pass

    def _c_destroy(self, e):
        try:
            self._c_var.trace_remove("write", self._c_tid)
        except Exception:
            pass

    def _c_draw(self):
        self.delete("all")
        ind = px(self.IND)
        h = ind + px(8)
        y0 = (h - ind) // 2
        on = self._c_on()
        edge = T.ACCENT if self._c_hover else T.BORDER2
        if self.SHAPE == "check":
            img = shape_image(ind, ind, px(7), T.ACCENT) if on else shape_image(ind, ind, px(7), T.ENTRY, edge, px(2))
        else:
            img = shape_image(ind, ind, ind // 2, T.ENTRY, T.ACCENT if on else edge, px(2))
        self._c_imgs = [img]
        self.create_image(0, y0, image=img, anchor="nw")
        if on and self.SHAPE == "check":
            if has_icons():
                self.create_text(ind // 2, y0 + ind // 2, text=ICONS["check"], font=icon_font(9), fill=T.ACCENT_FG)
            else:
                pts = (ind * 0.26, y0 + ind * 0.54, ind * 0.43, y0 + ind * 0.70, ind * 0.75, y0 + ind * 0.32)
                self.create_line(*pts, fill=T.ACCENT_FG, width=px(2), capstyle="round", joinstyle="round")
        if on and self.SHAPE == "radio":
            d = max(4, ind // 2)
            dot = shape_image(d, d, d // 2, T.ACCENT)
            self._c_imgs.append(dot)
            self.create_image(ind // 2, y0 + ind // 2, image=dot, anchor="center")
        label = self.create_text(ind + px(10), h // 2, text=self._c_text, anchor="w", fill=T.TEXT, font=("Segoe UI", 10))
        bb = self.bbox(label)
        tw = int(bb[2] - bb[0]) if bb else 7 * len(self._c_text)
        tk.Canvas.configure(self, width=ind + px(10) + tw + px(4))


class RoundCheck(_Choice):
    SHAPE = "check"

    def _c_on(self):
        return bool(self._c_var.get())

    def _c_toggle(self):
        self._c_var.set(not bool(self._c_var.get()))


class RoundRadio(_Choice):
    SHAPE = "radio"

    def _c_on(self):
        return self._c_var.get() == self._c_val

    def _c_toggle(self):
        self._c_var.set(self._c_val)


# ---------------------------------------------------------------- text entry
class RoundEntry(tk.Canvas):
    def __init__(self, parent, textvariable, icon=None, bg=T.BG, width=200, height=38):
        super().__init__(parent, width=px(width), height=px(height), bg=bg, highlightthickness=0, bd=0)
        self._re_icon, self._re_h, self._re_w, self._re_focus, self._re_img = icon, px(height), 0, False, None
        self.entry = tk.Entry(self, textvariable=textvariable, bg=T.ENTRY, fg=T.TEXT, insertbackground=T.TEXT,
                              relief="flat", bd=0, highlightthickness=0, font=("Segoe UI", 10),
                              selectbackground="#3A5A4A", selectforeground="#FFFFFF")
        self._re_win = self.create_window(0, self._re_h // 2, window=self.entry, anchor="w")
        self.bind("<Configure>", self._re_cfg)
        self.entry.bind("<FocusIn>", lambda e: self._re_set_focus(True))
        self.entry.bind("<FocusOut>", lambda e: self._re_set_focus(False))

    def _re_set_focus(self, v):
        self._re_focus = v
        self._re_draw()

    def _re_cfg(self, e):
        if e.width != self._re_w and e.width > 1:
            self._re_w = e.width
            self._re_draw()

    def _re_draw(self):
        w, h = self._re_w, self._re_h
        if w <= 1:
            return
        self.delete("bg")
        self.delete("ic")
        self._re_img = shape_image(w, h, h // 2, T.ENTRY, T.ACCENT if self._re_focus else T.BORDER, px(2) if self._re_focus else px(1))
        self.create_image(0, 0, image=self._re_img, anchor="nw", tags="bg")
        self.tag_lower("bg")
        left = px(18)
        if self._re_icon and has_icons():
            self.create_text(px(18), h // 2, text=ICONS[self._re_icon], font=icon_font(10),
                             fill=T.ACCENT if self._re_focus else T.MUTED, anchor="w", tags="ic")
            left = px(44)
        self.coords(self._re_win, left, h // 2)
        self.itemconfigure(self._re_win, width=max(20, w - left - px(18)))


# ---------------------------------------------------------------- rounded container
class RoundFrame(tk.Canvas):
    """Card with smooth rounded corners. Put children into `.inner`."""

    def __init__(self, parent, bg=T.BG, fill=T.SURF, radius=16, pad=(18, 14), stretch=False):
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0, height=px(20))
        self._rf_fill, self._rf_r = fill, px(radius)
        self._rf_px, self._rf_py = px(pad[0]), px(pad[1])
        self._rf_stretch = stretch
        self._rf_w = self._rf_h = 0
        self._rf_img, self._rf_job, self._rf_done = None, None, False
        self.inner = tk.Frame(self, bg=fill)
        self._rf_win = self.create_window(self._rf_px, self._rf_py, window=self.inner, anchor="nw")
        self.bind("<Configure>", self._rf_on_canvas)
        if not stretch:
            self.inner.bind("<Configure>", self._rf_on_inner)

    def _rf_on_canvas(self, e):
        if e.width <= 1:
            return
        if e.width == self._rf_w and (not self._rf_stretch or e.height == self._rf_h):
            return
        self._rf_w = e.width
        if self._rf_stretch:
            self._rf_h = e.height
        self.itemconfigure(self._rf_win, width=max(1, self._rf_w - 2 * self._rf_px))
        if self._rf_stretch:
            self.itemconfigure(self._rf_win, height=max(1, self._rf_h - 2 * self._rf_py))
        self._rf_schedule()

    def _rf_on_inner(self, e):
        need = self.inner.winfo_reqheight() + 2 * self._rf_py
        if need != self._rf_h:
            self._rf_h = need
            tk.Canvas.configure(self, height=need)
            self._rf_schedule()

    def _rf_schedule(self):
        if not self._rf_done:                      # first paint: immediately
            self._rf_paint()
            return
        if self._rf_job is None:                   # later resizes: debounce
            self._rf_job = self.after(30, self._rf_paint)

    def _rf_paint(self):
        self._rf_job = None
        w, h = self._rf_w, self._rf_h
        if w <= 1 or h <= 1:
            return
        self._rf_img = shape_image(w, h, self._rf_r, self._rf_fill)
        self.delete("bg")
        self.create_image(0, 0, image=self._rf_img, anchor="nw", tags="bg")
        self.tag_lower("bg")
        self._rf_done = True


# ---------------------------------------------------------------- sidebar item, badge, progress
class NavItem(tk.Canvas):
    def __init__(self, parent, text, icon, command, bg=T.SIDE):
        super().__init__(parent, height=px(42), bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self._n_text, self._n_icon, self._n_cmd = text, icon, command
        self._n_active = self._n_hover = False
        self._n_w, self._n_img = 0, None
        self.bind("<Configure>", self._n_cfg)
        self.bind("<Enter>", lambda e: self._n_set(hover=True))
        self.bind("<Leave>", lambda e: self._n_set(hover=False))
        self.bind("<ButtonRelease-1>", lambda e: self._n_cmd())
        self._n_draw()

    def _n_cfg(self, e):
        if e.width != self._n_w:
            self._n_w = e.width
            self._n_draw()

    def _n_set(self, hover):
        self._n_hover = hover
        self._n_draw()

    def set_active(self, on: bool):
        self._n_active = on
        self._n_draw()

    def _n_draw(self):
        self.delete("all")
        h, w = px(42), self._n_w
        if w > 1 and (self._n_active or self._n_hover):
            self._n_img = shape_image(w, h, px(12), "#26262C" if self._n_active else "#1A1A1E")
            self.create_image(0, 0, image=self._n_img, anchor="nw")
        fg = T.TEXT if self._n_active else T.MUTED
        x = px(16)
        if has_icons():
            self.create_text(x, h // 2, text=ICONS.get(self._n_icon, ""), font=icon_font(11),
                             fill=T.ACCENT if self._n_active else fg, anchor="w")
            x += px(32)
        self.create_text(x, h // 2, text=self._n_text, font=("Segoe UI", 10, "bold"), fill=fg, anchor="w")


class Badge(tk.Canvas):
    """Round icon badge used as a card header."""

    def __init__(self, parent, icon, bg=T.SURF, size=44):
        s = px(size)
        super().__init__(parent, width=s, height=s, bg=bg, highlightthickness=0, bd=0)
        self._b_img = shape_image(s, s, s // 2, T.BADGE)
        self.create_image(0, 0, image=self._b_img, anchor="nw")
        self.create_text(s // 2, s // 2, text=ICONS.get(icon, "") if has_icons() else "♪",
                         font=icon_font(14) if has_icons() else ("Segoe UI", 14), fill=T.ACCENT)


class SoftProgress(tk.Canvas):
    """Thin rounded progress bar. Same API subset as ttk.Progressbar."""

    def __init__(self, parent, width=240, height=8, bg=T.SURF):
        super().__init__(parent, width=px(width), height=px(height), bg=bg, highlightthickness=0, bd=0)
        self._sp_w, self._sp_h = px(width), px(height)
        self._sp_mode, self._sp_max, self._sp_val = "determinate", 100.0, 0.0
        self._sp_job, self._sp_pos, self._sp_dir, self._sp_interval = None, 0, 1, 12
        self._sp_imgs = []
        self._sp_render()

    def _sp_render(self):
        self.delete("all")
        w, h = self._sp_w, self._sp_h
        trough = shape_image(w, h, h // 2, T.SURF2)
        self._sp_imgs = [trough]
        self.create_image(0, 0, image=trough, anchor="nw")
        if self._sp_mode == "determinate":
            fw = int(w * (self._sp_val / self._sp_max)) if self._sp_max else 0
            if fw >= h:
                fill = shape_image(min(fw, w), h, h // 2, T.ACCENT)
                self._sp_imgs.append(fill)
                self.create_image(0, 0, image=fill, anchor="nw")
        else:
            seg = int(w * 0.3)
            fill = shape_image(seg, h, h // 2, T.ACCENT)
            self._sp_imgs.append(fill)
            self.create_image(self._sp_pos, 0, image=fill, anchor="nw")

    def configure(self, cnf=None, **kw):
        changed = False
        if "mode" in kw:
            self._sp_mode = str(kw.pop("mode"))
            changed = True
        if "maximum" in kw:
            self._sp_max = float(kw.pop("maximum"))
            changed = True
        if "value" in kw:
            self._sp_val = float(kw.pop("value"))
            changed = True
        kw.pop("style", None)
        if kw:
            super().configure(**kw)
        if changed:
            self._sp_render()

    config = configure

    def cget(self, key):
        if key == "mode":
            return self._sp_mode
        return super().cget(key)

    def start(self, interval=12):
        self._sp_interval = max(10, int(interval) * 2)
        if self._sp_job is None:
            self._sp_tick()

    def _sp_tick(self):
        seg = int(self._sp_w * 0.3)
        self._sp_pos += self._sp_dir * max(2, self._sp_w // 60)
        if self._sp_pos >= self._sp_w - seg:
            self._sp_pos, self._sp_dir = self._sp_w - seg, -1
        elif self._sp_pos <= 0:
            self._sp_pos, self._sp_dir = 0, 1
        if self._sp_mode == "indeterminate":
            self._sp_render()
        self._sp_job = self.after(self._sp_interval, self._sp_tick)

    def stop(self):
        if self._sp_job is not None:
            try:
                self.after_cancel(self._sp_job)
            except Exception:
                pass
            self._sp_job = None


# ---------------------------------------------------------------- popup (right-click) menu
class PopupMenu:
    """Rounded dark context menu. entries: dicts {label, icon, command, enabled} or None for a divider."""
    KEY = "#010101"          # window colour that Windows treats as transparent

    def __init__(self, root, entries):
        self.root, self.entries = root, entries
        self.win = None

    def close(self, _e=None):
        win, self.win = self.win, None
        if win is not None:
            try:
                win.grab_release()
            except Exception:
                pass
            try:
                win.destroy()
            except Exception:
                pass

    def show(self, x, y):
        root = self.root
        win = self.win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.configure(bg=self.KEY)
        try:
            win.attributes("-transparentcolor", self.KEY)
            win.attributes("-topmost", True)
        except Exception:
            pass
        cv = tk.Canvas(win, bg=self.KEY, highlightthickness=0, bd=0)
        cv.pack()
        font = ("Segoe UI", 10)
        pad, rowh, divh = px(8), px(34), px(11)
        iconw = px(28) if has_icons() else 0
        labels = [e["label"] for e in self.entries if e]
        textw = max([_measure(cv, t, font) for t in labels] or [px(80)])
        W = 2 * pad + px(14) + iconw + textw + px(22)
        pos, yy = [], pad
        for e in self.entries:
            h = divh if e is None else rowh
            pos.append((yy, yy + h))
            yy += h
        H = yy + pad
        cv.configure(width=W, height=H)
        self._bg = shape_image(W, H, px(16), "#25252B", "#3A3A42", 1)
        cv.create_image(0, 0, image=self._bg, anchor="nw")
        self._hl = shape_image(W - 2 * pad, rowh - px(4), px(10), T.HOVER)
        hl = cv.create_image(pad, 0, image=self._hl, anchor="nw", state="hidden")
        for e, (y0, y1) in zip(self.entries, pos):
            if e is None:
                cv.create_line(pad + px(12), (y0 + y1) // 2, W - pad - px(12), (y0 + y1) // 2, fill="#3A3A42")
                continue
            ok = e.get("enabled", True)
            fg = T.TEXT if ok else "#5E5E66"
            tx = pad + px(14)
            if iconw and e.get("icon"):
                cv.create_text(tx, (y0 + y1) // 2, text=ICONS.get(e["icon"], ""), font=icon_font(10),
                               fill=T.ACCENT if ok else "#5E5E66", anchor="w")
            cv.create_text(tx + iconw, (y0 + y1) // 2, text=e["label"], font=font, fill=fg, anchor="w")

        def row_at(ry):
            for i, (e, (y0, y1)) in enumerate(zip(self.entries, pos)):
                if e is not None and y0 <= ry < y1:
                    return i
            return None

        def on_motion(ev):
            i = row_at(ev.y)
            if i is not None and self.entries[i].get("enabled", True):
                cv.coords(hl, pad, pos[i][0] + px(2))
                cv.itemconfigure(hl, state="normal")
                cv.tag_raise(hl)
                cv.configure(cursor="hand2")
            else:
                cv.itemconfigure(hl, state="hidden")
                cv.configure(cursor="arrow")

        def on_press(ev):
            wx, wy = win.winfo_rootx(), win.winfo_rooty()
            inside = wx <= ev.x_root < wx + W and wy <= ev.y_root < wy + H
            if not inside:
                self.close()
                return
            i = row_at(ev.y_root - wy)
            if i is None or not self.entries[i].get("enabled", True):
                return
            cmd = self.entries[i]["command"]
            self.close()
            root.after(20, cmd)

        cv.bind("<Motion>", on_motion)
        cv.bind("<Leave>", lambda ev: cv.itemconfigure(hl, state="hidden"))
        win.bind("<ButtonPress-1>", on_press)
        win.bind("<ButtonPress-3>", on_press)
        win.bind("<Escape>", self.close)
        try:
            sw, sh = int(win.winfo_screenwidth()), int(win.winfo_screenheight())
            x = max(0, min(int(x), sw - W - 4))
            y = int(y) if int(y) + H + 4 <= sh else max(0, int(y) - H)
        except Exception:
            x, y = int(x), int(y)
        win.geometry(f"{W}x{H}+{x}+{y}")
        win.update_idletasks()
        try:
            win.focus_force()
            win.grab_set()
        except Exception:
            pass
