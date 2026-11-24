"""
sync_engine.py - the brain of Music Sync Checker (no GUI, testable on its own).

Flow:
  1. scan_folder()   : walk a folder, read metadata (title/artist/duration), keep it in a cache.
  2. compare()       : match every song on the DEVICE against the songs in the MASTER folder.
  3. plan_* / run_*  : safe renames and copies (never deletes, never overwrites).

Evidence used for matching, strongest first:
  metadata (title + artist)  >  identical file size  >  file name.
  Duration is a safety check: same title but a very different length = probably another version.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Callable, Dict, Iterable, List, Optional, Tuple

try:  # mutagen = audio tag reader (mp3, flac, m4a, ogg, wav, ...)
    import mutagen  # type: ignore
    HAS_MUTAGEN = True
except Exception:  # pragma: no cover
    mutagen = None
    HAS_MUTAGEN = False

AUDIO_EXT = {
    ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".wma",
    ".ape", ".alac", ".aiff", ".aif", ".wv", ".mp2", ".m4b",
}
SKIP_DIRS = {"system volume information", "$recycle.bin", "recycler", "found.000", ".trashes", ".spotlight-v100"}

# check results (from the point of view of a song on the device)
SYNC = "sync"          # in master, same name
RENAMED = "renamed"    # in master, but the file name differs
MISSING = "missing"    # not in master
REVIEW = "review"      # similar, needs a human decision

CONFIDENT = 0.90       # >= this counts as the same song
POSSIBLE = 0.60        # between POSSIBLE and CONFIDENT = needs review


# ---------------------------------------------------------------- general utilities
def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_duration(sec: Optional[float]) -> str:
    if sec is None:
        return "-"
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def app_data_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    path = os.path.join(base, "MusicSyncChecker")
    os.makedirs(path, exist_ok=True)
    return path


def load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


# ----------------------------------------------------------- text normalisation
_PAREN = re.compile(r"[\(\[\{（][^\)\]\}）]*[\)\]\}）]")
_TRACKNO = re.compile(r"^\s*\d{1,3}\s*[\.\-_)]\s*")
NOISE = {
    "remaster", "remastered", "remastering", "version", "single", "album", "original",
    "mix", "mono", "stereo", "official", "audio", "lyrics", "lyric", "feat", "ft", "featuring",
}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def tokens(s: str, strip_parens: bool = True) -> List[str]:
    """Split text into lowercase words without accents, punctuation or noise words (remaster, feat, year)."""
    s = unicodedata.normalize("NFKC", s or "")

    def run(text: str, filt: bool) -> List[str]:
        text = _strip_accents(text).casefold().replace("&", " and ")
        toks = re.findall(r"[^\W_]+", text)
        if filt:
            toks = [t for t in toks if t not in NOISE and not (len(t) == 4 and t.isdigit() and t[:2] in ("19", "20"))]
        return toks

    toks = run(_PAREN.sub(" ", s) if strip_parens else s, True)
    return toks or run(s, False)


def _overlap(a: set, b: set) -> float:
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


# ------------------------------------------------------------------ song data
@dataclass
class Track:
    path: str
    root: str
    size: int
    mtime: float
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: Optional[float] = None
    # derived (filled by prepare())
    title_bag: List[str] = field(default_factory=list, repr=False)
    artist_set: set = field(default_factory=set, repr=False)
    tag_bag: set = field(default_factory=set, repr=False)
    name_bag: set = field(default_factory=set, repr=False)

    def __post_init__(self):
        self.prepare()

    def prepare(self) -> None:
        self.title_bag = tokens(self.title) if self.title.strip() else []
        self.artist_set = set(tokens(self.artist, strip_parens=False)) if self.artist.strip() else set()
        self.tag_bag = set(self.title_bag) | self.artist_set if self.title_bag else set()
        self.name_bag = set(tokens(_TRACKNO.sub("", self.stem)))

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def stem(self) -> str:
        return os.path.splitext(self.name)[0]

    @property
    def ext(self) -> str:
        return os.path.splitext(self.name)[1].lower()

    @property
    def rel(self) -> str:
        try:
            return os.path.relpath(self.path, self.root)
        except ValueError:
            return self.path

    @property
    def has_tags(self) -> bool:
        return bool(self.title_bag)

    @property
    def label(self) -> str:
        if self.title.strip():
            return f"{self.artist.strip()} - {self.title.strip()}" if self.artist.strip() else self.title.strip()
        return self.stem

    def sig(self) -> str:
        """Stable identity used to store the user's decisions."""
        return f"{self.path.casefold()}|{self.size}"


# -------------------------------------------------------------- reading metadata
def _tag_text(tags, keys, join: bool = False) -> str:
    for key in keys:
        try:
            v = tags.get(key)
        except Exception:
            continue
        if v is None:
            continue
        if hasattr(v, "text"):
            v = v.text
        if isinstance(v, (list, tuple)):
            v = [str(x).strip() for x in v if str(x).strip()]
            if not v:
                continue
            return ", ".join(v) if join else v[0]
        v = str(v).strip()
        if v:
            return v
    return ""


def read_tags_mutagen(path: str) -> dict:
    out = {"title": "", "artist": "", "album": "", "duration": None}
    if not HAS_MUTAGEN:
        return out
    try:
        f = mutagen.File(path, easy=True)
    except Exception:
        return out
    if f is None:
        return out
    try:
        out["duration"] = float(f.info.length)
    except Exception:
        pass
    tags = getattr(f, "tags", None)
    if tags:
        out["title"] = _tag_text(tags, ("title", "TIT2", "\xa9nam"))
        out["artist"] = _tag_text(tags, ("artist", "TPE1", "\xa9ART", "albumartist", "TPE2"), join=True)
        out["album"] = _tag_text(tags, ("album", "TALB", "\xa9alb"))
    return out


Reader = Callable[[str], dict]


class TagCache:
    """On-disk tag cache so re-scans (especially on HDD/SD cards) are fast."""

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self.data: Dict[str, list] = load_json(path, {}) if path else {}
        self.seen: set = set()

    def get(self, p: str, size: int, mtime: float) -> Optional[dict]:
        self.seen.add(p)
        e = self.data.get(p)
        if e and e[0] == size and e[1] == int(mtime):
            return {"title": e[2], "artist": e[3], "album": e[4], "duration": e[5]}
        return None

    def put(self, p: str, size: int, mtime: float, tags: dict) -> None:
        self.data[p] = [size, int(mtime), tags["title"], tags["artist"], tags["album"], tags["duration"]]

    def save(self, prune: bool = True) -> None:
        if not self.path:
            return
        if prune and self.seen:
            self.data = {k: v for k, v in self.data.items() if k in self.seen}
        try:
            save_json(self.path, self.data)
        except OSError:
            pass


class Cancelled(Exception):
    pass


def scan_folder(
    root: str,
    cache: Optional[TagCache] = None,
    reader: Optional[Reader] = None,
    progress: Optional[Callable[[int, int, str], None]] = None,
    cancel: Optional[threading.Event] = None,
    workers: int = 4,
) -> Tuple[List[Track], List[str]]:
    """Return (tracks, errors). Problem folders are listed in errors; the scan carries on."""
    reader = reader or read_tags_mutagen
    cache = cache or TagCache(None)
    errors: List[str] = []
    found: List[Tuple[str, int, float]] = []

    def on_err(e):  # unreadable folder (I/O error, permissions, ...)
        errors.append(f"{e.filename}: {e.strerror or e}")

    for dirpath, dirnames, filenames in os.walk(root, onerror=on_err):
        dirnames[:] = [d for d in dirnames if d.casefold() not in SKIP_DIRS]
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in AUDIO_EXT:
                p = os.path.join(dirpath, fn)
                try:
                    st = os.stat(p)
                except OSError as e:
                    errors.append(f"{p}: {e.strerror or e}")
                    continue
                found.append((p, st.st_size, st.st_mtime))

    total = len(found)
    tracks: List[Optional[Track]] = [None] * total
    done = 0
    lock = threading.Lock()

    def work(i: int):
        nonlocal done
        p, size, mtime = found[i]
        if cancel is not None and cancel.is_set():
            return
        tags = cache.get(p, size, mtime)
        if tags is None:
            try:
                tags = reader(p)
            except Exception as e:
                errors.append(f"{p}: could not read tags ({e})")
                tags = {"title": "", "artist": "", "album": "", "duration": None}
            cache.put(p, size, mtime, tags)
        tracks[i] = Track(p, root, size, mtime, tags.get("title", "") or "", tags.get("artist", "") or "",
                          tags.get("album", "") or "", tags.get("duration"))
        with lock:
            done += 1
            if progress and (done % 10 == 0 or done == total):
                progress(done, total, p)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, range(total)))
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    return [t for t in tracks if t is not None], errors


# ---------------------------------------------------------------- matching
@dataclass
class Match:
    score: float
    method: str       # "metadata" | "size" | "mixed" | "name"
    note: str = ""


def score_pair(d: Track, m: Track) -> Optional[Match]:
    """How sure we are that d (device) and m (master) are the same song. None = clearly different."""
    cands: List[Match] = []

    # 1) metadata: title + artist (strongest evidence)
    if d.has_tags and m.has_tags:
        a, b = " ".join(d.title_bag), " ".join(m.title_bag)
        t_sim = 1.0 if a == b else SequenceMatcher(None, a, b).ratio()
        a_rel = _overlap(d.artist_set, m.artist_set) if d.artist_set and m.artist_set else None
        artist_ok = a_rel is None or a_rel >= 0.5
        if t_sim >= 0.999:
            cands.append(Match(1.0 if artist_ok else 0.75, "metadata",
                               "" if artist_ok else "same title, different artist"))
        elif t_sim >= 0.85:
            cands.append(Match(t_sim * (1.0 if artist_ok else 0.8) * 0.95, "metadata", "similar title"))

    # 2) identical file size + same format (a plain copy)
    if d.size == m.size and d.size > 0 and d.ext == m.ext:
        # veto: if both files have a title tag and the titles clearly differ, it is just a coincidence
        titles_clash = (d.has_tags and m.has_tags and
                        SequenceMatcher(None, " ".join(d.title_bag), " ".join(m.title_bag)).ratio() < 0.6)
        if not titles_clash:
            cands.append(Match(0.97, "size", "identical file size"))

    # 3) file name (or mixed: tag on one side, file name on the other)
    j_name = _jaccard(d.name_bag, m.name_bag)
    j_cross = max(_jaccard(d.tag_bag, m.name_bag), _jaccard(d.name_bag, m.tag_bag)) \
        if (d.has_tags or m.has_tags) else 0.0
    j, method = (j_cross, "mixed") if j_cross > j_name else (j_name, "name")
    if j >= 0.6:
        cands.append(Match(0.97 * j, method, "" if j >= 0.999 else "similar name"))

    if not cands:
        return None
    best = max(cands, key=lambda c: c.score)

    # duration as a safety check: same title but a very different length = probably another version
    if d.duration is not None and m.duration is not None:
        diff = abs(d.duration - m.duration)
        if diff > 10:
            best = Match(best.score * 0.6, best.method, f"duration differs by {diff:.0f}s (other version?)")
        elif diff > 3:
            best = Match(best.score * 0.85, best.method, f"duration differs by {diff:.0f}s")
    return best


@dataclass
class Item:
    device: Track
    status: str
    master: Optional[Track] = None
    score: float = 0.0
    method: str = ""
    note: str = ""


@dataclass
class CompareResult:
    items: List[Item]
    on_device: Dict[str, Item]    # master path -> item (confident match)
    maybe_on_device: Dict[str, Item]  # master path -> item (needs review)

    def counts(self) -> Dict[str, int]:
        c = Counter(i.status for i in self.items)
        return {s: c.get(s, 0) for s in (SYNC, RENAMED, MISSING, REVIEW)}


def _same_name(a: Track, b: Track) -> bool:
    return a.stem.casefold() == b.stem.casefold()


def compare(
    device_tracks: List[Track],
    master_tracks: List[Track],
    decisions: Optional[Dict[str, dict]] = None,
) -> CompareResult:
    decisions = decisions or {}
    by_path = {m.path: m for m in master_tracks}
    postings: Dict[str, List[int]] = {}
    by_size: Dict[int, List[int]] = {}
    for i, m in enumerate(master_tracks):
        for t in m.tag_bag | m.name_bag:
            postings.setdefault(t, []).append(i)
        by_size.setdefault(m.size, []).append(i)
    big = max(150, len(master_tracks) // 20)  # ignore words that are too common

    items: List[Item] = []
    on_dev: Dict[str, Item] = {}
    maybe: Dict[str, Item] = {}

    for d in device_tracks:
        dec = decisions.get(d.sig())
        if dec and dec.get("v") == "diff":
            items.append(Item(d, MISSING, note="marked: not the same song"))
            continue

        cand_idx: set = set(by_size.get(d.size, [])) if d.size else set()
        counter: Counter = Counter()
        toks = d.tag_bag | d.name_bag
        usable = [t for t in toks if len(postings.get(t, ())) <= big] or list(toks)
        for t in usable:
            counter.update(postings.get(t, ()))
        cand_idx |= {i for i, _ in counter.most_common(40)}

        best_m: Optional[Track] = None
        best_mt: Optional[Match] = None
        best_key: Optional[tuple] = None
        if dec and dec.get("v") == "same" and dec.get("master") in by_path:
            best_m = by_path[dec["master"]]
            best_mt = Match(1.0, "manual", "you marked: same song")
        else:
            d_parent = os.path.basename(os.path.dirname(d.path)).casefold()
            for i in cand_idx:
                m = master_tracks[i]
                mt = score_pair(d, m)
                if mt is None:
                    continue
                # tie-break: highest score, then same file name, then same parent folder name
                key = (round(mt.score, 3), _same_name(d, m),
                       os.path.basename(os.path.dirname(m.path)).casefold() == d_parent)
                if best_key is None or key > best_key:
                    best_m, best_mt, best_key = m, mt, key

        if best_m is None or best_mt is None or best_mt.score < POSSIBLE:
            items.append(Item(d, MISSING))
            continue
        m, mt = best_m, best_mt
        if mt.score >= CONFIDENT:
            status = SYNC if _same_name(d, m) else RENAMED
            note = mt.note
            if status == SYNC and d.ext != m.ext:
                note = (note + "; " if note else "") + f"format differs ({d.ext} vs {m.ext})"
            it = Item(d, status, m, mt.score, mt.method, note)
            on_dev.setdefault(m.path, it)
        else:
            it = Item(d, REVIEW, m, mt.score, mt.method, mt.note or "similar, not certain")
            maybe.setdefault(m.path, it)
        items.append(it)
    return CompareResult(items, on_dev, maybe)


# ------------------------------------------------------------------ file actions
def _split(path: str) -> Tuple[str, str, str]:
    d, n = os.path.split(path)
    s, e = os.path.splitext(n)
    return d, s, e


def unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    d, s, e = _split(path)
    n = 2
    while True:
        p = os.path.join(d, f"{s} ({n}){e}")
        if not os.path.exists(p):
            return p
        n += 1


_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_stem(s: str) -> str:
    return _BAD.sub("_", s).strip().rstrip(".") or "track"


@dataclass
class RenameOp:
    src: str
    dst: str
    skip_reason: str = ""


def plan_renames(items: Iterable[Item], target: str) -> List[RenameOp]:
    """
    target = "master": rename files in the MASTER to match the names on the device.
    target = "device": rename files on the DEVICE to match the names in the master.
    Each file keeps its own extension. A lyrics file (.lrc) with the same name is renamed too.
    """
    ops: List[RenameOp] = []
    used_src: set = set()
    used_dst: set = set()
    for it in items:
        if it.status != RENAMED or it.master is None:
            continue
        if target == "master":
            src_t, new_stem = it.master, it.device.stem
        else:
            src_t, new_stem = it.device, it.master.stem
        new_stem = safe_stem(new_stem)
        d, s, e = _split(src_t.path)
        dst = os.path.join(d, new_stem + e)
        pairs = [(src_t.path, dst)]
        lrc_src, lrc_dst = os.path.join(d, s + ".lrc"), os.path.join(d, new_stem + ".lrc")
        if os.path.exists(lrc_src):
            pairs.append((lrc_src, lrc_dst))
        for a, b in pairs:
            reason = ""
            if a.casefold() in used_src:
                reason = "already planned for another song"
            elif a.casefold() == b.casefold() and a == b:
                reason = "name is already the same"
            elif b.casefold() in used_dst or (os.path.exists(b) and a.casefold() != b.casefold()):
                reason = "target name is used by another file"
            ops.append(RenameOp(a, b, reason))
            if not reason:
                used_src.add(a.casefold())
                used_dst.add(b.casefold())
    return ops


def run_renames(ops: List[RenameOp], log: Callable[[str], None] = lambda s: None) -> Tuple[int, int]:
    ok = bad = 0
    for op in ops:
        if op.skip_reason:
            bad += 1
            log(f"[SKIP] {os.path.basename(op.src)}: {op.skip_reason}")
            continue
        try:
            os.rename(op.src, op.dst)
            ok += 1
            log(f"[RENAME] {os.path.basename(op.src)}  →  {os.path.basename(op.dst)}")
        except OSError as e:
            bad += 1
            log(f"[FAILED] {os.path.basename(op.src)}: {e.strerror or e}")
    return ok, bad


@dataclass
class CopyOp:
    src: str
    dst: str
    size: int
    skip_reason: str = ""


def plan_copy(
    tracks: Iterable[Track],
    dest_dir: str,
    keep_subfolders: bool = False,
    include_lrc: bool = True,
) -> List[CopyOp]:
    """Copy plan. Never overwrites: if the name exists, same size = skipped, different size = gets a (2) suffix."""
    ops: List[CopyOp] = []
    taken: set = set()
    for t in tracks:
        sub = os.path.dirname(t.rel) if keep_subfolders else ""
        folder = os.path.join(dest_dir, sub) if sub else dest_dir
        pairs = [(t.path, os.path.join(folder, t.name), t.size)]
        lrc = os.path.splitext(t.path)[0] + ".lrc"
        if include_lrc and os.path.exists(lrc):
            try:
                pairs.append((lrc, os.path.join(folder, os.path.basename(lrc)), os.path.getsize(lrc)))
            except OSError:
                pass
        for src, dst, size in pairs:
            if dst.casefold() in taken:
                ops.append(CopyOp(src, dst, size, "target name clashes with another song in the list"))
                continue
            if os.path.exists(dst):
                try:
                    same = os.path.getsize(dst) == size
                except OSError:
                    same = False
                if same:
                    ops.append(CopyOp(src, dst, size, "already exists at the destination"))
                    continue
                dst = unique_path(dst)
            taken.add(dst.casefold())
            ops.append(CopyOp(src, dst, size))
    return ops


def free_space(path: str) -> Optional[int]:
    p = path
    while p and not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return None


def run_copy(
    ops: List[CopyOp],
    progress: Optional[Callable[[int, int, str], None]] = None,
    log: Callable[[str], None] = lambda s: None,
    cancel: Optional[threading.Event] = None,
) -> Tuple[int, int, int]:
    """Copy via a .part file then rename, so an unplugged SD card never leaves a half-written file."""
    todo = [o for o in ops if not o.skip_reason]
    skipped = len(ops) - len(todo)
    for o in ops:
        if o.skip_reason:
            log(f"[SKIP] {os.path.basename(o.src)}: {o.skip_reason}")
    ok = bad = 0
    for i, o in enumerate(todo, 1):
        if cancel is not None and cancel.is_set():
            log("[STOPPED] cancelled by user")
            break
        tmp = o.dst + ".part"
        try:
            os.makedirs(os.path.dirname(o.dst), exist_ok=True)
            shutil.copyfile(o.src, tmp)
            try:
                shutil.copystat(o.src, tmp)
            except OSError:
                pass
            os.replace(tmp, o.dst)
            ok += 1
            log(f"[COPY] {os.path.basename(o.src)}  →  {os.path.dirname(o.dst)}")
        except OSError as e:
            bad += 1
            log(f"[FAILED] {os.path.basename(o.src)}: {e.strerror or e}")
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
        if progress:
            progress(i, len(todo), o.src)
    return ok, bad, skipped
