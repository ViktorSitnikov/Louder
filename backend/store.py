"""Какие программы уже известны и какие у них выставлены коридор, скрытие и избранное."""

import os
import sqlite3
import tempfile
import threading
from pathlib import Path

DEFAULT_LOW, DEFAULT_HIGH = -22.0, -12.0


def _path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Gromkost"
    root.mkdir(parents=True, exist_ok=True)
    return root / "gromkost.db"


class Store:
    def __init__(self, path: Path | None = None):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path or _path(), check_same_thread=False)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS app (
                exe TEXT PRIMARY KEY,
                low REAL NOT NULL,
                high REAL NOT NULL,
                hidden INTEGER NOT NULL DEFAULT 0,
                favorite INTEGER NOT NULL DEFAULT 0,
                off INTEGER NOT NULL DEFAULT 0
            )"""
        )
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS part (
                exe TEXT NOT NULL,
                title TEXT NOT NULL,
                low REAL NOT NULL,
                high REAL NOT NULL,
                hidden INTEGER NOT NULL DEFAULT 0,
                off INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (exe, title)
            )"""
        )
        for table in ("app", "part"):
            try:
                self._db.execute(f"ALTER TABLE {table} ADD COLUMN off INTEGER NOT NULL DEFAULT 0")
                self._db.execute(
                    f"UPDATE {table} SET low = ?, high = ?, off = 1 WHERE low = -60 AND high = 0",
                    (DEFAULT_LOW, DEFAULT_HIGH),
                )
            except sqlite3.OperationalError:
                pass
            try:
                self._db.execute(f"ALTER TABLE {table} ADD COLUMN boost INTEGER NOT NULL DEFAULT 100")
            except sqlite3.OperationalError:
                pass
        self._db.execute("CREATE TABLE IF NOT EXISTS setting (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        try:
            # media = 1: у программы замечена медиасессия Windows, ей можно слать старт/стоп.
            self._db.execute("ALTER TABLE app ADD COLUMN media INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        # pos — место в списке: у программ — среди программ, у частей — внутри программы (у браузера часть — сайт).
        for table in ("app", "part"):
            try:
                self._db.execute(f"ALTER TABLE {table} ADD COLUMN pos REAL")
            except sqlite3.OperationalError:
                pass
        self._renumber()
        self._db.commit()
        if path is None:
            self._import_txt()
        self.exes = {row[0] for row in self._db.execute("SELECT exe FROM app")}

    def _renumber(self):
        # При перетаскивании место — середина между соседями; на старте сжимаем обратно в 1..n.
        for (exe,) in self._db.execute("SELECT DISTINCT exe FROM part").fetchall():
            rows = self._db.execute(
                "SELECT title FROM part WHERE exe = ? ORDER BY pos IS NULL, pos, rowid", (exe,)
            ).fetchall()
            for index, (title,) in enumerate(rows, 1):
                self._db.execute("UPDATE part SET pos = ? WHERE exe = ? AND title = ?", (index, exe, title))
        self._renumber_apps()

    def _renumber_apps(self):
        rows = self._db.execute("SELECT exe FROM app ORDER BY pos IS NULL, pos, exe").fetchall()
        for index, (exe,) in enumerate(rows, 1):
            self._db.execute("UPDATE app SET pos = ? WHERE exe = ?", (index, exe))

    def _import_txt(self):
        # Старый список известных программ. Один раз переносим, файл не трогаем.
        path = _path().with_name("audio.txt")
        try:
            names = [line.strip().lower() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except OSError:
            return
        for exe in names:
            self._db.execute(
                "INSERT OR IGNORE INTO app (exe, low, high, hidden, favorite, off) VALUES (?, ?, ?, 0, 0, 0)",
                (exe, DEFAULT_LOW, DEFAULT_HIGH),
            )
        self._db.commit()

    def has(self, exe: str) -> bool:
        return exe.lower() in self.exes

    def ensure(self, exe: str):
        exe = exe.lower()
        if not exe.endswith(".exe") or exe in self.exes:
            return
        low, high, boost = self.defaults()
        with self._lock:
            self._db.execute(
                """INSERT OR IGNORE INTO app (exe, low, high, hidden, favorite, off, boost, pos)
                   VALUES (?, ?, ?, 0, 0, 0, ?, (SELECT CAST(COALESCE(MAX(pos), 0) AS INTEGER) + 1 FROM app))""",
                (exe, low, high, boost),
            )
            self._db.commit()
            self.exes.add(exe)

    def defaults(self) -> tuple[float, float, int]:
        """Коридор и усиление для программ, которые появятся впервые (общие настройки)."""
        return (
            float(self.setting("default_low", str(DEFAULT_LOW))),
            float(self.setting("default_high", str(DEFAULT_HIGH))),
            int(self.setting("default_boost", "100")),
        )

    def app(self, exe: str) -> dict:
        row = self._db.execute(
            "SELECT low, high, hidden, favorite, off, boost, media, pos FROM app WHERE exe = ?", (exe.lower(),)
        ).fetchone()
        if not row:
            low, high, boost = self.defaults()
            return {
                "low": low, "high": high, "hidden": 0, "favorite": 0, "off": 0, "boost": boost, "media": 0, "pos": None,
            }
        keys = ("low", "high", "hidden", "favorite", "off", "boost", "media", "pos")
        return dict(zip(keys, row))

    def setting(self, key: str, default: str = "") -> str:
        row = self._db.execute("SELECT value FROM setting WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str):
        with self._lock:
            self._db.execute(
                "INSERT INTO setting (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._db.commit()

    def set_media(self, exe: str):
        exe = exe.lower()
        self.ensure(exe)
        with self._lock:
            self._db.execute("UPDATE app SET media = 1 WHERE exe = ? AND media = 0", (exe,))
            self._db.commit()

    def set_app(self, exe: str, low: float, high: float):
        exe = exe.lower()
        with self._lock:
            self._db.execute(
                """INSERT INTO app (exe, low, high, hidden, favorite, off) VALUES (?, ?, ?, 0, 0, 0)
                   ON CONFLICT(exe) DO UPDATE SET low = excluded.low, high = excluded.high""",
                (exe, low, high),
            )
            self._db.execute("UPDATE part SET low = ?, high = ? WHERE exe = ?", (low, high, exe))
            self._db.commit()
            self.exes.add(exe)

    def set_flag(
        self,
        exe: str,
        hidden: bool | None = None,
        favorite: bool | None = None,
        off: bool | None = None,
    ):
        exe = exe.lower()
        current = self.app(exe)
        if hidden is None:
            hidden = bool(current["hidden"])
        if favorite is None:
            favorite = bool(current["favorite"])
        if off is None:
            off = bool(current["off"])
        with self._lock:
            self._db.execute(
                """INSERT INTO app (exe, low, high, hidden, favorite, off) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(exe) DO UPDATE SET hidden = excluded.hidden, favorite = excluded.favorite, off = excluded.off""",
                (exe, current["low"], current["high"], int(hidden), int(favorite), int(off)),
            )
            self._db.commit()
            self.exes.add(exe)

    def parts(self, exe: str) -> dict[str, dict]:
        rows = self._db.execute(
            "SELECT title, low, high, hidden, off, boost, pos FROM part WHERE exe = ?", (exe.lower(),)
        )
        return {
            title: {"low": low, "high": high, "hidden": hidden, "off": off, "boost": boost, "pos": pos}
            for title, low, high, hidden, off, boost, pos in rows
        }

    def _insert_part(self, exe: str, title: str):
        # Новая часть наследует коридор и усиление программы и встаёт в конец целым числом.
        current = self.app(exe)
        self._db.execute(
            """INSERT OR IGNORE INTO part (exe, title, low, high, hidden, off, boost, pos)
               VALUES (?, ?, ?, ?, 0, 0, ?, (SELECT CAST(COALESCE(MAX(pos), 0) AS INTEGER) + 1 FROM part WHERE exe = ?))""",
            (exe, title[:180], current["low"], current["high"], current["boost"], exe),
        )

    def _upsert_parts(self, exe: str, titles: list[str], column: str, value):
        exe = exe.lower()
        with self._lock:
            for title in titles:
                self._insert_part(exe, title)
                self._db.execute(
                    f"UPDATE part SET {column} = ? WHERE exe = ? AND title = ?", (value, exe, title[:180])
                )
            self._db.commit()

    def ensure_part(self, exe: str, title: str):
        with self._lock:
            self._insert_part(exe.lower(), title)
            self._db.commit()

    def set_part_pos(self, exe: str, title: str, pos: float):
        self._upsert_parts(exe, [title], "pos", float(pos))

    def move_app(self, exe: str, after: str = "", before: str = ""):
        """Ставит программу между соседями (пустой сосед — край списка)."""
        with self._lock:
            self._renumber_apps()  # у программ, заведённых в обход ensure, места ещё нет
            pos = dict(self._db.execute("SELECT exe, pos FROM app").fetchall())
            prev, nxt = pos.get(after.lower()), pos.get(before.lower())
            if prev is not None and nxt is not None:
                value = (prev + nxt) / 2
            elif prev is not None:
                value = prev + 1
            elif nxt is not None:
                value = nxt - 1
            else:
                return
            self._db.execute("UPDATE app SET pos = ? WHERE exe = ?", (value, exe.lower()))
            self._db.commit()

    def reset_parts(self, exe: str):
        with self._lock:
            self._db.execute("DELETE FROM part WHERE exe = ?", (exe.lower(),))
            self._db.commit()

    def set_part(self, exe: str, titles: list[str], low: float, high: float):
        self._upsert_parts(exe, titles, "low", low)
        self._upsert_parts(exe, titles, "high", high)

    def set_part_hidden(self, exe: str, title: str, hidden: bool):
        self._upsert_parts(exe, [title], "hidden", int(hidden))

    def set_part_off(self, exe: str, titles: list[str], off: bool):
        self._upsert_parts(exe, titles, "off", int(off))

    def set_part_boost(self, exe: str, titles: list[str], boost: int):
        self._upsert_parts(exe, titles, "boost", int(boost))

    def set_boost(self, exe: str, boost: int):
        exe = exe.lower()
        self.ensure(exe)
        with self._lock:
            self._db.execute("UPDATE app SET boost = ? WHERE exe = ?", (int(boost), exe))
            self._db.execute("UPDATE part SET boost = ? WHERE exe = ?", (int(boost), exe))
            self._db.commit()


def _check():
    folder = Path(tempfile.mkdtemp())
    store = Store(folder / "t.db")
    assert not store.has("Opera.exe")
    store.ensure("Opera.exe")
    assert store.has("opera.exe")
    store.set_app("opera.exe", -21.5, -12)
    store.set_flag("opera.exe", off=True)
    assert store.app("opera.exe")["low"] == -21.5 and store.app("opera.exe")["off"] == 1
    store.set_flag("opera.exe", off=False, favorite=True)
    assert store.app("opera.exe")["favorite"] == 1 and store.app("opera.exe")["off"] == 0
    assert store.app("opera.exe")["low"] == -21.5
    store.set_part("opera.exe", ["YouTube"], -18, -10)
    store.set_part_off("opera.exe", ["YouTube"], True)
    store.set_part_hidden("opera.exe", "YouTube", True)
    assert store.parts("opera.exe")["YouTube"]["hidden"] == 1
    assert store.parts("opera.exe")["YouTube"]["off"] == 1
    assert store.parts("opera.exe")["YouTube"]["low"] == -18
    store.set_app("opera.exe", -22, -12)
    assert store.parts("opera.exe")["YouTube"]["low"] == -22
    store.ensure_part("opera.exe", "VK Видео")
    assert "VK Видео" in store.parts("opera.exe")
    store.set_boost("opera.exe", 250)
    assert store.app("opera.exe")["boost"] == 250 and store.parts("opera.exe")["YouTube"]["boost"] == 250
    assert store.setting("theme") == ""
    store.set_setting("theme", "light")
    store.set_setting("theme", "dark")
    assert store.setting("theme") == "dark"
    store.set_setting("default_low", "-30.5")
    store.set_setting("default_boost", "150")
    store.ensure("new.exe")
    assert store.app("new.exe")["low"] == -30.5 and store.app("new.exe")["boost"] == 150
    store.set_setting("default_low", str(DEFAULT_LOW))
    store.set_setting("default_boost", "100")
    assert store.app("opera.exe")["media"] == 0
    store.set_media("opera.exe")
    assert store.app("opera.exe")["media"] == 1 and store.app("opera.exe")["boost"] == 250
    store.set_part_boost("opera.exe", ["Новая"], 40)
    assert store.parts("opera.exe")["Новая"]["boost"] == 40 and store.parts("opera.exe")["Новая"]["low"] == -22
    pos = {title: row["pos"] for title, row in store.parts("opera.exe").items()}
    assert pos == {"YouTube": 1, "VK Видео": 2, "Новая": 3}
    store.set_part_pos("opera.exe", "Новая", 1.5)
    store.ensure_part("opera.exe", "Ещё")
    assert store.parts("opera.exe")["Ещё"]["pos"] == 3  # после 2 и 1.5 — следующее целое
    reopened = Store(folder / "t.db")
    pos = {title: row["pos"] for title, row in reopened.parts("opera.exe").items()}
    assert pos == {"YouTube": 1, "Новая": 2, "VK Видео": 3, "Ещё": 4}
    order = lambda s: [exe for exe, _ in sorted(s._db.execute("SELECT exe, pos FROM app"), key=lambda r: r[1])]
    assert order(reopened) == ["opera.exe", "new.exe"]  # в порядке появления
    reopened.ensure("z.exe")
    reopened.move_app("z.exe", before="opera.exe")
    assert order(reopened) == ["z.exe", "opera.exe", "new.exe"]
    reopened.move_app("z.exe", after="opera.exe", before="new.exe")
    assert order(reopened) == ["opera.exe", "z.exe", "new.exe"]
    reopened.reset_parts("opera.exe")
    assert reopened.parts("opera.exe") == {}


if __name__ == "__main__":
    _check()
