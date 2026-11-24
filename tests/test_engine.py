"""Tes mesin pencocokan. Jalankan:  python -m unittest discover -s tests -v
No mutagen/ffmpeg needed: tags come from a fake reader."""
import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import sync_engine as se  # noqa: E402

EMPTY = {"title": "", "artist": "", "album": "", "duration": None}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.master = os.path.join(self.tmp, "master")
        self.dev = os.path.join(self.tmp, "device")
        os.makedirs(self.master)
        os.makedirs(self.dev)
        self.tags = {}
        self.reads = 0

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def reader(self, path):
        self.reads += 1
        return dict(self.tags.get(path, EMPTY))

    def mk(self, root, rel, size, title="", artist="", dur=None):
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(b"x" * size)
        if title or dur is not None:
            self.tags[p] = {"title": title, "artist": artist, "album": "", "duration": dur}
        return p

    @staticmethod
    def write(path, text):
        with open(path, "w") as f:
            f.write(text)

    def run_compare(self, decisions=None):
        m, _ = se.scan_folder(self.master, reader=self.reader)
        d, _ = se.scan_folder(self.dev, reader=self.reader)
        return se.compare(d, m, decisions), m, d

    def status_of(self, res, name):
        return next(i for i in res.items if i.device.name == name)


class TestMatching(Base):
    def test_identical_name_is_sync(self):
        self.mk(self.master, "West/Rock/Hozier - Take Me To Church.flac", 1001, "Take Me To Church", "Hozier", 241)
        self.mk(self.dev, "WEST/Acoustic/Hozier - Take Me To Church.flac", 1002, "Take Me To Church", "Hozier", 241.2)
        r, _, _ = self.run_compare()
        it = r.items[0]
        self.assertEqual(it.status, se.SYNC)
        self.assertEqual(it.method, "metadata")

    def test_renamed_on_device_found_by_metadata(self):
        self.mk(self.master, "West/Hozier - Take Me To Church.flac", 1001, "Take Me To Church", "Hozier", 241)
        self.mk(self.dev, "WEST/Take Me To Church.flac", 1002, "Take Me To Church", "Hozier", 241)
        it = self.run_compare()[0].items[0]
        self.assertEqual(it.status, se.RENAMED)
        self.assertEqual(it.method, "metadata")

    def test_renamed_with_totally_different_name_but_same_tags(self):
        self.mk(self.master, "Indonesia/Pamungkas/Closure - Pamungkas.flac", 2001, "Closure", "Pamungkas", 180)
        self.mk(self.dev, "ID/Chill/track_07.flac", 2002, "Closure", "Pamungkas", 180)
        it = self.run_compare()[0].items[0]
        self.assertEqual(it.status, se.RENAMED)

    def test_word_order_swapped_no_tags(self):
        self.mk(self.master, "Indonesia/Akad - Payung Teduh.flac", 3001)
        self.mk(self.dev, "ID/Acoustic/Payung Teduh - Akad.flac", 3002)
        it = self.run_compare()[0].items[0]
        self.assertEqual(it.status, se.RENAMED)
        self.assertEqual(it.method, "name")

    def test_same_size_same_ext_catches_total_rename_without_tags(self):
        self.mk(self.master, "JP/Anime/Lemon - Kenshi Yonezu.flac", 4444)
        self.mk(self.dev, "JP/Anime/favorite song.flac", 4444)
        it = self.run_compare()[0].items[0]
        self.assertEqual(it.status, se.RENAMED)
        self.assertEqual(it.method, "size")

    def test_same_size_but_clearly_different_titles_is_not_a_match(self):
        self.mk(self.master, "West/Yellow - Coldplay.flac", 4444, "Yellow", "Coldplay", 267)
        self.mk(self.dev, "WEST/Grenade.flac", 4444, "Grenade", "Bruno Mars", 222)
        self.assertEqual(self.run_compare()[0].items[0].status, se.MISSING)

    def test_feat_and_remaster_noise_ignored(self):
        self.mk(self.master, "Indonesia/Kelelawar.flac", 5001, "Kelelawar", "Feast", 200)
        self.mk(self.dev, "ID/Rock/Kelelawar (feat. Karaeng Adjie).flac", 5002, "Kelelawar (feat. Karaeng Adjie)", "Feast", 200)
        self.mk(self.master, "West/Bad - Michael Jackson.flac", 5003)
        self.mk(self.dev, "WEST/Pop/Bad - 2012 Remaster - Michael Jackson.flac", 5004)
        r = self.run_compare()[0]
        self.assertEqual(self.status_of(r, "Kelelawar (feat. Karaeng Adjie).flac").status, se.RENAMED)
        self.assertEqual(self.status_of(r, "Bad - 2012 Remaster - Michael Jackson.flac").status, se.RENAMED)

    def test_accents_ignored(self):
        self.mk(self.master, "Game/Expedition33/Lumiere.flac", 6001, "Lumiere", "Lorien Testard", 300)
        self.mk(self.dev, "Game/Expedition33/Lumière.flac", 6002, "Lumière", "Lorien Testard", 300)
        self.assertEqual(self.run_compare()[0].items[0].status, se.RENAMED)

    def test_format_difference_is_sync_with_note(self):
        self.mk(self.master, "West/Count on Me.flac", 7001, "Count on Me", "Bruno Mars", 197)
        self.mk(self.dev, "WEST/Pop/Count on Me.m4a", 7002, "Count on Me", "Bruno Mars", 197)
        it = self.run_compare()[0].items[0]
        self.assertEqual(it.status, se.SYNC)
        self.assertIn("format differs", it.note)

    def test_missing(self):
        self.mk(self.master, "West/Yellow - Coldplay.flac", 8001, "Yellow", "Coldplay", 267)
        self.mk(self.dev, "WEST/Pop/Grenade.flac", 8002, "Grenade", "Bruno Mars", 222)
        self.assertEqual(self.run_compare()[0].items[0].status, se.MISSING)

    def test_other_version_with_different_duration_goes_to_review(self):
        self.mk(self.master, "Game/Persona 3/Color Your Night.flac", 9001, "Color Your Night", "Yumi Kawamura", 270)
        self.mk(self.dev, "Game/Persona 3/Color Your Night.flac", 9002, "Color Your Night", "Yumi Kawamura", 190)
        it = self.run_compare()[0].items[0]
        self.assertEqual(it.status, se.REVIEW)
        self.assertIn("duration", it.note)

    def test_cover_by_other_artist_is_not_matched(self):
        self.mk(self.master, "Game/Persona 3/Color Your Night.flac", 9101, "Color Your Night", "Yumi Kawamura", 270)
        self.mk(self.dev, "Game/Persona 3/Color Your Night - Chewie Melodies; Pealeaf.flac", 9102,
                "Color Your Night", "Chewie Melodies, Pealeaf", 110)
        self.assertEqual(self.run_compare()[0].items[0].status, se.MISSING)

    def test_prefers_same_filename_among_duplicates_in_master(self):
        a = self.mk(self.master, "Unsorted/Sweet.flac", 9201, "Sweet", "Cigarettes After Sex", 280)
        b = self.mk(self.master, "West/Chill/Cigarettes After Sex - Sweet.flac", 9202, "Sweet", "Cigarettes After Sex", 280)
        self.mk(self.dev, "WEST/Chill/Cigarettes After Sex - Sweet.flac", 9203, "Sweet", "Cigarettes After Sex", 280)
        it = self.run_compare()[0].items[0]
        self.assertEqual(it.status, se.SYNC)
        self.assertEqual(it.master.path, b)

    def test_user_decisions_override(self):
        self.mk(self.master, "Game/Color Your Night.flac", 9301, "Color Your Night", "Yumi Kawamura", 270)
        dp = self.mk(self.dev, "Game/Color Your Night.flac", 9302, "Color Your Night", "Yumi Kawamura", 190)
        r, m, d = self.run_compare()
        self.assertEqual(r.items[0].status, se.REVIEW)
        sig = d[0].sig()
        same = se.compare(d, m, {sig: {"v": "same", "master": m[0].path}})
        self.assertEqual(same.items[0].status, se.SYNC)
        diff = se.compare(d, m, {sig: {"v": "diff"}})
        self.assertEqual(diff.items[0].status, se.MISSING)

    def test_reverse_map_for_master_tab(self):
        mp = self.mk(self.master, "West/Yellow - Coldplay.flac", 9401, "Yellow", "Coldplay", 267)
        other = self.mk(self.master, "West/Fix You - Coldplay.flac", 9402, "Fix You", "Coldplay", 295)
        self.mk(self.dev, "WEST/Yellow.flac", 9403, "Yellow", "Coldplay", 267)
        r = self.run_compare()[0]
        self.assertIn(mp, r.on_device)
        self.assertNotIn(other, r.on_device)


class TestScan(Base):
    def test_cache_avoids_rereading_and_skips_system_dirs(self):
        self.mk(self.master, "a.flac", 100, "A", "X", 10)
        self.mk(self.master, "System Volume Information/junk.flac", 100, "J", "X", 10)
        cache = se.TagCache(os.path.join(self.tmp, "cache.json"))
        t1, _ = se.scan_folder(self.master, cache=cache, reader=self.reader)
        cache.save()
        self.assertEqual([t.name for t in t1], ["a.flac"])
        self.assertEqual(self.reads, 1)
        cache2 = se.TagCache(os.path.join(self.tmp, "cache.json"))
        t2, _ = se.scan_folder(self.master, cache=cache2, reader=self.reader)
        self.assertEqual(self.reads, 1)  # dari cache
        self.assertEqual(t2[0].title, "A")

    def test_missing_root_does_not_crash(self):
        tracks, errors = se.scan_folder(os.path.join(self.tmp, "does-not-exist"), reader=self.reader)
        self.assertEqual(tracks, [])
        self.assertTrue(errors)

    def test_cancel(self):
        self.mk(self.master, "a.flac", 100)
        ev = threading.Event()
        ev.set()
        with self.assertRaises(se.Cancelled):
            se.scan_folder(self.master, reader=self.reader, cancel=ev)


class TestActions(Base):
    def test_rename_master_follows_device_and_moves_lrc(self):
        mp = self.mk(self.master, "West/Hozier - Take Me To Church.flac", 1001, "Take Me To Church", "Hozier", 241)
        self.write(os.path.splitext(mp)[0] + ".lrc", "lirik")
        self.mk(self.dev, "WEST/Take Me To Church.flac", 1002, "Take Me To Church", "Hozier", 241)
        r = self.run_compare()[0]
        ops = se.plan_renames(r.items, "master")
        self.assertEqual(len(ops), 2)
        ok, bad = se.run_renames(ops)
        self.assertEqual((ok, bad), (2, 0))
        d = os.path.join(self.master, "West")
        self.assertEqual(sorted(os.listdir(d)), ["Take Me To Church.flac", "Take Me To Church.lrc"])

    def test_rename_device_follows_master(self):
        self.mk(self.master, "West/Hozier - Take Me To Church.flac", 1001, "Take Me To Church", "Hozier", 241)
        dp = self.mk(self.dev, "WEST/track01.flac", 1002, "Take Me To Church", "Hozier", 241)
        r = self.run_compare()[0]
        se.run_renames(se.plan_renames(r.items, "device"))
        self.assertTrue(os.path.exists(os.path.join(os.path.dirname(dp), "Hozier - Take Me To Church.flac")))

    def test_rename_never_overwrites(self):
        self.mk(self.master, "A/Hozier - Take Me To Church.flac", 1001, "Take Me To Church", "Hozier", 241)
        blocker = self.mk(self.master, "A/Take Me To Church.flac", 1003, "Other", "Z", 50)
        self.mk(self.dev, "WEST/Take Me To Church.flac", 1002, "Take Me To Church", "Hozier", 241)
        r = self.run_compare()[0]
        ops = se.plan_renames(r.items, "master")
        self.assertTrue(ops[0].skip_reason)
        se.run_renames(ops)
        self.assertEqual(os.path.getsize(blocker), 1003)

    def test_copy_is_safe(self):
        s1 = self.mk(self.master, "x/a.flac", 500)
        s2 = self.mk(self.master, "x/b.flac", 600)
        self.write(os.path.splitext(s1)[0] + ".lrc", "l")
        dest = os.path.join(self.dev, "Pop")
        os.makedirs(dest)
        with open(os.path.join(dest, "a.flac"), "wb") as f:   # already exists, same size
            f.write(b"x" * 500)
        with open(os.path.join(dest, "b.flac"), "wb") as f:   # nama sama tapi beda isi
            f.write(b"y" * 10)
        tracks, _ = se.scan_folder(self.master, reader=self.reader)
        ops = se.plan_copy(tracks, dest)
        ok, bad, skipped = se.run_copy(ops)
        self.assertEqual(bad, 0)
        self.assertEqual(skipped, 1)                             # a.flac dilewati
        self.assertEqual(os.path.getsize(os.path.join(dest, "b.flac")), 10)   # not overwritten
        self.assertEqual(os.path.getsize(os.path.join(dest, "b (2).flac")), 600)
        self.assertTrue(os.path.exists(os.path.join(dest, "a.lrc")))          # lirik ikut
        self.assertFalse([f for f in os.listdir(dest) if f.endswith(".part")])

    def test_free_space(self):
        self.assertGreater(se.free_space(os.path.join(self.tmp, "not", "yet")), 0)


class TestTokens(unittest.TestCase):
    def test_tokens(self):
        self.assertEqual(se.tokens("Don’t Stop (Remastered 2011)"), ["don", "t", "stop"])
        self.assertEqual(se.tokens("Tom's Diner"), se.tokens("Tom s Diner"))
        self.assertTrue(se.tokens("01. ∞"))  # must not be completely empty


if __name__ == "__main__":
    unittest.main()
