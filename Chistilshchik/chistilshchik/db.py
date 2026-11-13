"""База SQLite: настройки, диски, место остановки, исключения, находки, журнал."""
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Запомненные диски (3.3). volume_id = серийный номер тома,
-- по нему отличаем новый диск от знакомого, даже если буква поменялась.
CREATE TABLE IF NOT EXISTS disks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    volume_id   TEXT NOT NULL UNIQUE,
    letter      TEXT,
    label       TEXT,
    fs_type     TEXT,
    kind        TEXT,              -- SSD / HDD / неизвестно
    total_bytes INTEGER,
    free_bytes  INTEGER,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);

-- Место остановки проверки (раздел 8)
CREATE TABLE IF NOT EXISTS scan_state (
    disk_id    INTEGER PRIMARY KEY REFERENCES disks(id) ON DELETE CASCADE,
    stage      TEXT NOT NULL,      -- этап проверки
    last_dir   TEXT,               -- последняя обработанная папка
    finished   INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

-- Исключения (раздел 5)
CREATE TABLE IF NOT EXISTS exclusions (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    path    TEXT NOT NULL UNIQUE COLLATE NOCASE,
    kind    TEXT NOT NULL,         -- never / report / name
    builtin INTEGER NOT NULL DEFAULT 0
);

-- «Не показывать этот файл больше»
CREATE TABLE IF NOT EXISTS ignored_files (
    path     TEXT PRIMARY KEY COLLATE NOCASE,
    added_at TEXT NOT NULL
);

-- Что нашла проверка
CREATE TABLE IF NOT EXISTS findings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    disk_id    INTEGER REFERENCES disks(id) ON DELETE CASCADE,
    category   TEXT NOT NULL,      -- temp / browser_cache / logs / ...
    path       TEXT NOT NULL COLLATE NOCASE,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    found_at   TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'found',  -- found / deleted / ignored
    UNIQUE (category, path)
);
CREATE INDEX IF NOT EXISTS idx_findings_status ON findings(status, category);

-- Журнал: что нашла, что удалила, когда
CREATE TABLE IF NOT EXISTS journal (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    level   TEXT NOT NULL,         -- info / warning / error
    event   TEXT NOT NULL,
    details TEXT
);
"""


def _now():
    return datetime.now().isoformat(timespec="seconds")


class Database:
    """Одно соединение на всю программу. Замок нужен, потому что
    проверка идёт в отдельном потоке, а окно работает в главном."""

    def __init__(self, path=None):
        self.path = Path(path) if path else config.DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._init()

    def _init(self):
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)
            # Настройки по умолчанию: добавляем только те, которых ещё нет
            self._conn.executemany(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                [(k, str(v)) for k, v in config.DEFAULT_SETTINGS.items()],
            )
            self._conn.executemany(
                "INSERT OR IGNORE INTO exclusions (path, kind, builtin) VALUES (?, ?, 1)",
                config.builtin_exclusions(),
            )

    def close(self):
        with self._lock:
            self._conn.close()

    # ---------- общие помощники ----------
    def execute(self, sql, params=()):
        with self._lock, self._conn:
            return self._conn.execute(sql, params)

    def query(self, sql, params=()):
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # ---------- настройки ----------
    def get_setting(self, key):
        rows = self.query("SELECT value FROM settings WHERE key = ?", (key,))
        if rows:
            return rows[0]["value"]
        return str(config.DEFAULT_SETTINGS[key])

    def get_int(self, key):
        return int(float(self.get_setting(key)))

    def set_setting(self, key, value):
        self.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    def all_settings(self):
        return {r["key"]: r["value"] for r in self.query("SELECT key, value FROM settings")}

    # ---------- журнал ----------
    def log(self, event, details="", level="info"):
        self.execute(
            "INSERT INTO journal (ts, level, event, details) VALUES (?, ?, ?, ?)",
            (_now(), level, event, details),
        )

    def journal(self, limit=200):
        return self.query("SELECT * FROM journal ORDER BY id DESC LIMIT ?", (limit,))

    # ---------- исключения ----------
    def exclusions(self):
        return self.query("SELECT * FROM exclusions ORDER BY builtin DESC, path")

    def add_exclusion(self, path, kind=config.EXCL_NEVER):
        self.execute(
            "INSERT OR IGNORE INTO exclusions (path, kind, builtin) VALUES (?, ?, 0)",
            (str(path), kind),
        )

    def remove_exclusion(self, path):
        """Встроенные исключения удалить нельзя."""
        self.execute("DELETE FROM exclusions WHERE path = ? AND builtin = 0", (str(path),))

    # ---------- место остановки ----------
    def save_scan_state(self, disk_id, stage, last_dir, finished=False):
        self.execute(
            "INSERT INTO scan_state (disk_id, stage, last_dir, finished, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(disk_id) DO UPDATE SET stage = excluded.stage, "
            "last_dir = excluded.last_dir, finished = excluded.finished, "
            "updated_at = excluded.updated_at",
            (disk_id, stage, last_dir, int(finished), _now()),
        )

    def load_scan_state(self, disk_id):
        rows = self.query("SELECT * FROM scan_state WHERE disk_id = ?", (disk_id,))
        return rows[0] if rows else None

    def clear_scan_state(self, disk_id):
        self.execute("DELETE FROM scan_state WHERE disk_id = ?", (disk_id,))
