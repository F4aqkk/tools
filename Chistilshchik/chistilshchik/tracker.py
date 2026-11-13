"""Слежение за дисками: что подключили, что отключили, где мало места.

Здесь нет окна и потоков — только логика. Поэтому её легко проверить
тестами на «поддельных» дисках.
"""
from . import disks as disks_mod
from .utils import human_size

# События, которые получает окно
ADDED = "added"        # диск подключён (is_new=True — видим его впервые)
REMOVED = "removed"    # диск отключён
LOW = "low"            # свободного места стало меньше порога
OK = "ok"              # места снова достаточно

# Запас, чтобы не дёргаться на границе: «мало» при 14.9%,
# снова «нормально» только от 16%.
HYSTERESIS_PCT = 1.0


class DiskTracker:
    def __init__(self, db, lister=disks_mod.list_disks, kind_detector=None):
        self.db = db
        self.lister = lister
        self.kind_detector = kind_detector or disks_mod.detect_kind
        self.current = {}       # volume_id -> DiskInfo (подключены сейчас)
        self.low = set()        # volume_id дисков, где мало места
        self.first_poll = True
        # Если база пустая, это самый первый запуск: все диски «новые»,
        # но засыпать уведомлениями не будем.
        self.db_had_disks = db.disk_count() > 0
        db.mark_all_disconnected()

    def include_types(self):
        types = {disks_mod.FIXED}
        if self.db.get_int("watch_removable"):
            types.add(disks_mod.REMOVABLE)
        if self.db.get_int("watch_network"):
            types.add(disks_mod.NETWORK)
        if self.db.get_int("watch_cdrom"):
            types.add(disks_mod.CDROM)
        return types

    def poll(self):
        """Один круг проверки. Возвращает список событий:
        (вид, DiskInfo, is_new)."""
        events = []
        threshold = self.db.get_int("free_threshold_pct")
        found = {d.volume_id: d for d in self.lister(self.include_types())}

        # Подключённые и уже знакомые
        for vid, d in found.items():
            old = self.current.get(vid)
            if old is not None:
                d.kind = old.kind
                if (abs(d.free_bytes - old.free_bytes) > 100 * 1024 * 1024
                        or d.letter != old.letter or d.label != old.label):
                    self.db.upsert_disk(d)
                continue

            row = self.db.get_disk(vid)
            is_new = row is None
            if row is not None and row["kind"] and row["kind"] != disks_mod.KIND_UNKNOWN:
                d.kind = row["kind"]
            else:
                d.kind = self.kind_detector(d.letter)
            self.db.upsert_disk(d)

            what = "Подключён новый диск" if is_new else "Подключён диск"
            self.db.log(what, describe(d))
            if not self.first_poll or (is_new and self.db_had_disks):
                events.append((ADDED, d, is_new))

        # Отключённые
        for vid in list(self.current):
            if vid not in found:
                d = self.current.pop(vid)
                self.low.discard(vid)
                self.db.set_connected(vid, False)
                self.db.log("Диск отключён", d.title)
                events.append((REMOVED, d, False))

        self.current.update(found)

        # Заполненность
        for vid, d in found.items():
            pct = d.free_pct
            if pct < threshold and vid not in self.low:
                self.low.add(vid)
                self.db.log("Диск заполняется", describe(d), level="warning")
                events.append((LOW, d, False))
            elif pct >= threshold + HYSTERESIS_PCT and vid in self.low:
                self.low.discard(vid)
                self.db.log("Места снова достаточно", describe(d))
                events.append((OK, d, False))

        self.first_poll = False
        return events

    def connected(self):
        return sorted(self.current.values(), key=lambda d: d.letter)


def describe(d):
    """D: «Запасной» — HDD, 465.8 ГБ, свободно 40% (186.3 ГБ)"""
    kind = d.kind or disks_mod.KIND_UNKNOWN
    return (f"{d.title} — {kind}, {human_size(d.total_bytes)}, "
            f"свободно {d.free_pct:.0f}% ({human_size(d.free_bytes)})")
