"""Проверка слежения за дисками на «поддельных» дисках.

Запуск из папки Chistilshchik:
    .venv\\Scripts\\python -m unittest discover tests
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chistilshchik import tracker as tr  # noqa: E402
from chistilshchik.db import Database  # noqa: E402
from chistilshchik.disks import FIXED, REMOVABLE, DiskInfo  # noqa: E402

GB = 1024 ** 3


def disk(vid, letter, total_gb, free_gb, dtype=FIXED, label=""):
    return DiskInfo(vid, letter, label, "NTFS", dtype, total_gb * GB, free_gb * GB)


class FakeLister:
    """Подменяет настоящий список дисков."""

    def __init__(self, disks):
        self.disks = list(disks)
        self.calls = []

    def __call__(self, include):
        self.calls.append(set(include))
        return [replace(d) for d in self.disks if d.drive_type in include]


class TrackerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "t.db")
        self.db = Database(self.path)
        self.c = disk("vol-C", "C:", 195, 60)
        self.d = disk("vol-D", "D:", 800, 400)
        self.hdd = disk("vol-HDD", "E:", 465, 200, label="Запасной")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def make(self, lister):
        return tr.DiskTracker(self.db, lister=lister, kind_detector=lambda letter: "SSD")

    def kinds(self, events):
        return [(k, d.letter, new) for k, d, new in events]

    def test_first_run_is_quiet(self):
        t = self.make(FakeLister([self.c, self.d]))
        self.assertEqual(t.poll(), [])
        self.assertEqual(self.db.disk_count(), 2)

    def test_new_disk_plugged_in(self):
        lister = FakeLister([self.c, self.d])
        t = self.make(lister)
        t.poll()
        lister.disks.append(self.hdd)
        self.assertEqual(self.kinds(t.poll()), [(tr.ADDED, "E:", True)])
        self.assertEqual(self.db.get_disk("vol-HDD")["label"], "Запасной")
        self.assertEqual(t.poll(), [])  # второй раз не сообщаем

    def test_known_disk_reconnected(self):
        lister = FakeLister([self.c, self.d, self.hdd])
        t = self.make(lister)
        t.poll()
        lister.disks.remove(self.hdd)
        self.assertEqual(self.kinds(t.poll()), [(tr.REMOVED, "E:", False)])
        self.assertEqual(self.db.get_disk("vol-HDD")["connected"], 0)
        # Подключили снова, но уже под другой буквой — это тот же диск
        lister.disks.append(replace(self.hdd, letter="F:"))
        self.assertEqual(self.kinds(t.poll()), [(tr.ADDED, "F:", False)])
        self.assertEqual(self.db.get_disk("vol-HDD")["letter"], "F:")
        self.assertEqual(self.db.disk_count(), 3)

    def test_disk_plugged_while_program_was_off(self):
        t = self.make(FakeLister([self.c, self.d]))
        t.poll()
        # «Перезапуск»: новый трекер, а HDD подключили, пока программа была закрыта
        t2 = self.make(FakeLister([self.c, self.d, self.hdd]))
        self.assertEqual(self.kinds(t2.poll()), [(tr.ADDED, "E:", True)])

    def test_low_space_once_and_hysteresis(self):
        lister = FakeLister([self.c])
        t = self.make(lister)
        lister.disks[0] = replace(self.c, free_bytes=int(195 * GB * 0.10))
        self.assertEqual(self.kinds(t.poll()), [(tr.LOW, "C:", False)])
        self.assertEqual(t.poll(), [])  # не повторяем каждые 5 секунд
        lister.disks[0] = replace(self.c, free_bytes=int(195 * GB * 0.155))
        self.assertEqual(t.poll(), [])  # 15.5% — ещё в «запасе», молчим
        lister.disks[0] = replace(self.c, free_bytes=int(195 * GB * 0.20))
        self.assertEqual(self.kinds(t.poll()), [(tr.OK, "C:", False)])
        lister.disks[0] = replace(self.c, free_bytes=int(195 * GB * 0.05))
        self.assertEqual(self.kinds(t.poll()), [(tr.LOW, "C:", False)])

    def test_new_full_disk_reports_both(self):
        lister = FakeLister([self.c])
        t = self.make(lister)
        t.poll()
        lister.disks.append(replace(self.hdd, free_bytes=10 * GB))
        self.assertEqual(self.kinds(t.poll()),
                         [(tr.ADDED, "E:", True), (tr.LOW, "E:", False)])

    def test_threshold_from_settings(self):
        self.db.set_setting("free_threshold_pct", 60)
        t = self.make(FakeLister([self.d]))  # свободно 50%
        self.assertEqual(self.kinds(t.poll()), [(tr.LOW, "D:", False)])

    def test_flash_drives_off_by_default(self):
        flash = disk("vol-USB", "G:", 32, 1, dtype=REMOVABLE)
        lister = FakeLister([self.c, flash])
        t = self.make(lister)
        t.poll()
        self.assertNotIn("vol-USB", t.current)
        self.db.set_setting("watch_removable", 1)
        events = self.kinds(t.poll())
        self.assertIn((tr.ADDED, "G:", True), events)

    def test_kind_detected_once(self):
        calls = []
        lister = FakeLister([self.c])
        t = tr.DiskTracker(self.db, lister=lister,
                           kind_detector=lambda letter: calls.append(letter) or "HDD")
        t.poll()
        t.poll()
        self.assertEqual(calls, ["C:"])
        self.assertEqual(self.db.get_disk("vol-C")["kind"], "HDD")

    def test_old_database_is_upgraded(self):
        """База от этапа 1 (без новых столбцов) должна открываться."""
        self.db.close()
        old = os.path.join(self.tmp.name, "old.db")
        con = sqlite3.connect(old)
        con.execute("CREATE TABLE disks (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "volume_id TEXT NOT NULL UNIQUE, letter TEXT, label TEXT, fs_type TEXT, "
                    "kind TEXT, total_bytes INTEGER, free_bytes INTEGER, "
                    "first_seen TEXT NOT NULL, last_seen TEXT NOT NULL)")
        con.commit()
        con.close()
        self.db = Database(old)
        t = self.make(FakeLister([self.c]))
        t.poll()
        self.assertEqual(self.db.get_disk("vol-C")["connected"], 1)


if __name__ == "__main__":
    unittest.main()
