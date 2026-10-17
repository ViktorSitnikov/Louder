"""Какие программы звучат и какая у них громкость."""

import asyncio
import os
import re
import threading
import time
import ctypes
from urllib.parse import urlparse
from ctypes import byref, c_void_p
from ctypes.wintypes import BOOL, DWORD

import comtypes
import psutil
from comtypes import CLSCTX_ALL, GUID
from pycaw.api.audiopolicy import IAudioSessionControl2
from pycaw.constants import DEVICE_STATE, AudioSessionState, EDataFlow
from pycaw.pycaw import IAudioSessionManager2, IMMDeviceEnumerator
from pycaw.utils import AudioSession

from engine import ATTEN, boosters, ensure_boost, root_pids, stop_boost
from native import kernel32, psapi, user32
from store import DEFAULT_HIGH, DEFAULT_LOW, Store

_CLSID_ENUM = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
_com_state = threading.local()
state_lock = threading.Lock()
store = Store()
session_groups: dict[str, dict] = {}
seen_pids: dict[str, tuple] = {}
_browser_tabs: dict[str, tuple[float, list[dict]]] = {}
_commands: dict[str, list[dict]] = {}
_last_tab: dict[str, int] = {}


def _browser_live(name: str) -> list[dict] | None:
    entry = _browser_tabs.get(name.lower())
    if not entry:
        return None
    ts, tabs = entry
    if time.monotonic() - ts > 5.0:
        _browser_tabs.pop(name.lower(), None)
        return None
    return tabs


def bridge_debug() -> dict:
    now = time.monotonic()
    return {
        "tabs": {exe: {"age": round(now - ts, 1), "tabs": tabs} for exe, (ts, tabs) in _browser_tabs.items()},
        "commands": _commands,
        "last_tab": _last_tab,
        "sent": _sent[-20:],
    }


_sent: list = []


def tab_title(raw) -> str:
    # «(3) YouTube» и «YouTube» — одна вкладка: счётчик уведомлений меняется, настройки терять нельзя.
    return re.sub(r"^\(\d+\)\s*", "", str(raw or "").strip())[:180]


_failures: dict[str, tuple[float, str, str]] = {}


def report_failure(exe: str, tab_id, reason: str):
    tabs = _browser_live(exe) or []
    title = next((t["title"] for t in tabs if t["id"] == tab_id), "вкладка")
    _failures[(exe or "").lower()] = (time.monotonic(), title, str(reason or ""))


def _failure_note() -> str:
    for exe, (at, title, reason) in list(_failures.items()):
        if time.monotonic() - at > 8.0:
            _failures.pop(exe, None)
            continue
        if reason == "NotAllowedError":
            return f"«{title}» не запустилась: браузер ждёт клика по этой вкладке."
        return f"«{title}» не запустилась: {reason}"
    return ""


def site_of(url) -> str:
    """Сайт — постоянный ключ вкладки: заголовок меняется с каждым видео, адрес сайта — нет."""
    text = str(url or "")
    if text.startswith("file:"):
        return "Файлы"
    host = (urlparse(text).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def sync_browser_tabs(exe: str, tabs: list[dict]) -> dict:
    key = (exe or "opera.exe").strip()
    with state_lock:
        for existing in session_groups:
            if existing.lower() == key.lower():
                key = existing
                break
        store.ensure(key)
        flag = f"sites:{key.lower()}"
        if not store.setting(flag):
            store.reset_parts(key)  # раньше вкладки хранились по заголовкам — эти записи не перенести
            store.set_setting(flag, "1")
        saved = store.parts(key)
        clean = []
        for t in tabs or []:
            tid = t.get("id")
            site = site_of(t.get("url"))
            if not tid or not site:
                continue
            audible = bool(t.get("audible"))
            # chosen присылает только расширение для адресов, которыми пользователь разрешил управлять.
            if audible or t.get("chosen"):
                if site not in saved:
                    store.ensure_part(key, site)
                    saved = store.parts(key)
                _last_tab[key.lower()] = int(tid)
                _last_tab[f"{key.lower()}|{site}"] = int(tid)
            if audible or site in saved:
                title = tab_title(t.get("title")) or site
                clean.append({"id": int(tid), "title": title, "site": site, "audible": audible, "blocked": bool(t.get("blocked")) and not audible})
        _browser_tabs[key.lower()] = (time.monotonic(), clean)
        info = store.app(key)
        low, high = _band(info["low"], info["high"])
        app_off = bool(info["off"]) or bool(info["hidden"])
        default = {"low": low, "high": high, "off": app_off, "boost": _boost(info["boost"])}
        bands = {}
        for t in clean:
            row = saved.get(t["site"])
            if row:
                plow, phigh = _band(row["low"], row["high"])
                poff = app_off or bool(row["off"]) or bool(row["hidden"])
                bands[str(t["id"])] = {"low": plow, "high": phigh, "off": poff, "boost": _boost(row["boost"])}
            else:
                bands[str(t["id"])] = default
        return {"default": default, "bands": bands}


_command_ready = threading.Condition()


def _queue_commands(name: str, commands: list[dict]):
    with _command_ready:
        _commands.setdefault(name.lower(), []).extend(commands)
        _command_ready.notify_all()


def wait_commands(exe: str, timeout: float = 20.0) -> list[dict]:
    """Расширение держит этот запрос открытым: команда уходит во вкладку сразу, без ожидания опроса."""
    key = (exe or "").lower()
    with _command_ready:
        _command_ready.wait_for(lambda: _commands.get(key), timeout)
        commands = _commands.pop(key, [])
    if commands:
        _sent.append({"at": time.strftime("%H:%M:%S"), "commands": commands})
        del _sent[:-20]
    return commands


# Окна оболочки Windows, у них нет своей громкости.
_SHELL = {
    "explorer.exe",
    "applicationframehost.exe",
    "textinputhost.exe",
    "systemsettings.exe",
    "shellexperiencehost.exe",
    "searchhost.exe",
    "startmenuexperiencehost.exe",
    "lockapp.exe",
    "widgetboard.exe",
    "widgets.exe",
    "dwm.exe",
    "sihost.exe",
}


def _com():
    if getattr(_com_state, "ok", False):
        return
    try:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except OSError:
        pass
    _com_state.ok = True


_name_cache: dict[int, str] = {}


def _process_name(pid: int, from_window: bool) -> str | None:
    if pid == os.getpid():
        return None
    if pid == 0:
        return None if from_window else "Системные звуки"
    name = _name_cache.get(pid)
    if name is None:
        try:
            name = psutil.Process(pid).name()
        except Exception:
            name = f"Процесс {pid}"
        _name_cache[pid] = name
    if from_window and name.lower() in _SHELL:
        return None
    return name


def _enumerator() -> IMMDeviceEnumerator:
    enum = getattr(_com_state, "enum", None)
    if enum is None:
        enum = comtypes.CoCreateInstance(_CLSID_ENUM, IMMDeviceEnumerator, CLSCTX_ALL)
        _com_state.enum = enum
    return enum


def _all_sessions():
    try:
        coll = _enumerator().EnumAudioEndpoints(EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value)
        dev_count = coll.GetCount()
    except Exception:
        return
    for dev_idx in range(dev_count):
        try:
            dev = coll.Item(dev_idx)
            mgr = dev.Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None).QueryInterface(
                IAudioSessionManager2
            )
            enumerator = mgr.GetSessionEnumerator()
            count = enumerator.GetCount()
        except Exception:
            continue
        for index in range(count):
            try:
                control = enumerator.GetSession(index).QueryInterface(IAudioSessionControl2)
            except Exception:
                continue
            yield AudioSession(control)


_wasapi_yes: set[int] = set()
_wasapi_no: set[int] = set()


def _uses_wasapi(pid: int) -> bool:
    # ponytail: audioses.dll значит, что процесс сам открывал звуковую сессию.
    # Программа из базы больше не сканируется.
    if pid in _wasapi_yes:
        return True
    if pid in _wasapi_no:
        return False
    hit = _scan_wasapi(pid)
    (_wasapi_yes if hit else _wasapi_no).add(pid)
    return hit


def _scan_wasapi(pid: int) -> bool:
    handle = kernel32.OpenProcess(0x0410, False, pid)
    if not handle:
        return False
    try:
        needed = DWORD()
        psapi.EnumProcessModulesEx(handle, None, 0, byref(needed), 3)
        count = needed.value // ctypes.sizeof(c_void_p)
        if count <= 0:
            return False
        modules = (c_void_p * count)()
        if not psapi.EnumProcessModulesEx(handle, modules, ctypes.sizeof(modules), byref(needed), 3):
            return False
        count = min(count, needed.value // ctypes.sizeof(c_void_p))
        name = ctypes.create_unicode_buffer(64)
        for index in range(count):
            if not psapi.GetModuleBaseNameW(handle, modules[index], name, 64):
                continue
            if name.value.lower() == "audioses.dll":
                return True
        return False
    finally:
        kernel32.CloseHandle(handle)


def _windows() -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(BOOL, c_void_p, c_void_p)
    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindow(hwnd, 4):  # GW_OWNER: дочерние диалоги не отдельные окна
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        pid = DWORD()
        user32.GetWindowThreadProcessId(hwnd, byref(pid))
        if not pid.value:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if title:
            found.append((int(pid.value), title[:180]))
        return True

    user32.EnumWindows(visit, 0)
    return found


def _session_groups():
    groups: dict[str, dict] = {}

    def add(name: str, pid: int, session=None):
        bucket = groups.setdefault(name, {"pids": set(), "sessions": [], "windows": [], "active": set()})
        bucket["pids"].add(pid)
        if session is not None:
            bucket["sessions"].append(session)

    for session in _all_sessions():
        try:
            state = int(session.State)
            if state == int(AudioSessionState.Expired):
                continue
            pid = int(session.ProcessId)
        except Exception:
            continue
        name = _process_name(pid, from_window=False)
        if name:
            add(name, pid, session)
            store.ensure(name)
            if state == int(AudioSessionState.Active):
                groups[name]["active"].add(pid)
    seen = {pid for group in groups.values() for pid in group["pids"]}
    for pid, title in _windows():
        seen.add(pid)
        name = _process_name(pid, from_window=True)
        if not name:
            continue
        if store.has(name) or _uses_wasapi(pid):
            add(name, pid)
            store.ensure(name)
            group = groups[name]
            if not any(item["pid"] == pid and item["title"] == title for item in group["windows"]):
                group["windows"].append({"pid": pid, "title": title})
    for pid in list(_wasapi_yes):
        if pid not in seen:
            _wasapi_yes.discard(pid)
    for pid in list(_wasapi_no):
        if pid not in seen:
            _wasapi_no.discard(pid)
    for pid in list(_name_cache):
        if pid not in seen:
            _name_cache.pop(pid, None)
    for name, group in list(groups.items()):
        btabs = _browser_live(name)
        if btabs:
            sites: dict[str, list[dict]] = {}
            for t in btabs:
                sites.setdefault(t["site"], []).append({"id": t["id"], "title": t["title"], "playing": t["audible"], "blocked": bool(t.get("blocked"))})
            group["windows"] = [
                {"pid": -tabs[0]["id"], "title": site, "playing": any(t["playing"] for t in tabs), "tabs": tabs}
                for site, tabs in sites.items()
            ]
    return groups


def _ordered(windows: list[dict], saved: dict) -> list[dict]:
    """Сохранённые места первыми, остальные — в конце в том порядке, в каком их нашли."""
    def key(item):
        pos = saved.get(item[1]["title"], {}).get("pos")
        return (pos is None, pos or 0, item[0])

    return [window for _, window in sorted(enumerate(windows), key=key)]


def _apply_native(group, percent: int):
    level = max(0.0, min(1.0, percent / 100.0))
    for session in group["sessions"]:
        volume = session.SimpleAudioVolume
        volume.SetMasterVolume(level, None)
        volume.SetMute(0 if level else 1, None)


DB_MIN, DB_MAX = -60.0, 0.0


def _band(low, high) -> tuple[float, float]:
    low = round(max(DB_MIN, min(DB_MAX, float(low))), 1)
    high = round(max(low, min(DB_MAX, float(high))), 1)
    return low, high


def _needs_capture(low: float, high: float, boost: int = 100) -> bool:
    return (low, high, boost) != FULL


FULL = (DB_MIN, DB_MAX, 100)


def _setting(row) -> tuple[float, float, int]:
    if row["off"] or row.get("hidden"):
        return FULL
    return (*_band(row["low"], row["high"]), _boost(row["boost"]))


def _boost(value) -> int:
    return max(0, min(600, int(round(float(value)))))


def _session_pid(session) -> int:
    try:
        return int(session.ProcessId)
    except Exception:
        return 0


def _attenuate(sessions):
    for session in sessions:
        volume = session.SimpleAudioVolume
        current = float(volume.GetMasterVolume())
        if bool(volume.GetMute()) or abs(current - ATTEN) > 0.002:
            volume.SetMute(0, None)
            volume.SetMasterVolume(ATTEN, None)


def _pid_bands(name: str, group: dict) -> dict[int, tuple[float, float, int]]:
    if _browser_live(name):
        return {pid: FULL for pid in group["pids"] if pid > 0}
    info = store.app(name)
    if info["off"] or info["hidden"]:
        return {pid: FULL for pid in group["pids"] if pid}
    base = _setting(info)
    saved = store.parts(name) if len(group["windows"]) > 1 else {}
    titles: dict[int, list[str]] = {}
    for window in group["windows"]:
        titles.setdefault(window["pid"], []).append(window["title"])
    bands = {}
    for pid in group["pids"]:
        if not pid:
            continue
        own = [saved[title] for title in titles.get(pid, []) if title in saved]
        eff = [_setting(item) for item in own]
        if len(set(eff)) == 1 and eff:
            bands[pid] = eff[0]
        else:
            bands[pid] = base
    return bands


def _apply(name: str) -> bool:
    group = session_groups.get(name)
    if not group:
        return False
    bands = _pid_bands(name, group)
    pids = list(bands)
    if not pids:
        return False
    sessions = group["sessions"]
    if any(_needs_capture(*bands[pid]) for pid in pids) and not sessions:
        return False
    same = len(set(bands.values())) == 1
    if same:
        setting = bands[pids[0]]
        if _needs_capture(*setting):
            roots = root_pids(pids)
            for pid in pids:
                if pid not in roots:
                    stop_boost(pid)
            _attenuate(sessions)
            for pid in roots:
                ensure_boost(pid, *setting)
        else:
            for pid in pids:
                stop_boost(pid)
            _apply_native(group, 100)
        return True
    # ponytail: у разных процессов свои коридоры. Захват родителя включает детей,
    # поэтому берём только процесс, у которого есть своя сессия.
    live = {_session_pid(session) for session in sessions}
    for pid in pids:
        own = [session for session in sessions if _session_pid(session) == pid]
        setting = bands[pid]
        if _needs_capture(*setting):
            if not own:
                stop_boost(pid)
                continue
            _attenuate(own)
            ensure_boost(pid, *setting)
        else:
            stop_boost(pid)
            if own:
                _apply_native({"sessions": own}, 100)
    for pid in list(boosters):
        if pid in group["pids"] and pid not in live:
            stop_boost(pid)
    return True


def _drifted(group: dict, bands: dict) -> bool:
    """Громкость сессии не та, что мы ставили: её сбросил кто-то другой или упала прошлая копия программы."""
    capture = any(_needs_capture(*band) for band in bands.values())
    for session in group["sessions"]:
        try:
            volume = float(session.SimpleAudioVolume.GetMasterVolume())
        except Exception:
            continue
        own = bands.get(_session_pid(session))
        need = _needs_capture(*own) if own else capture
        if need != (abs(volume - ATTEN) < 0.002):
            return True
    return False


def _signature(name: str, group: dict) -> tuple:
    return tuple(sorted(_pid_bands(name, group).items()))


def _snapshot(note: str = "") -> dict:
    global session_groups
    session_groups = _session_groups()
    live = {pid for group in session_groups.values() for pid in group["pids"]}
    for pid in list(boosters):
        if pid not in live:
            stop_boost(pid)
    for name in list(seen_pids):
        if name not in session_groups:
            seen_pids.pop(name, None)
    apps = []
    error = ""
    media = _refresh_media(session_groups)
    for name in sorted(session_groups, key=str.lower):
        if name == "Системные звуки":
            continue
        group = session_groups[name]
        info = store.app(name)
        tabs = bool(_browser_live(name))
        low, high = _band(info["low"], info["high"])
        saved = store.parts(name)
        signature = _signature(name, group)
        wanted = [pid for pid, band in signature if _needs_capture(*band)]
        if len({band for _, band in signature}) <= 1:
            wanted = root_pids(wanted)
        else:
            live_pids = {_session_pid(session) for session in group["sessions"]}
            wanted = [pid for pid in wanted if pid in live_pids]
        dead = any(pid not in boosters or not boosters[pid].alive() for pid in wanted)
        if signature != seen_pids.get(name) or dead or _drifted(group, dict(signature)):
            try:
                if _apply(name):
                    seen_pids[name] = signature
            except Exception as exc:
                error = str(exc)
                store.set_flag(name, off=True)
                for pid in group["pids"]:
                    stop_boost(pid)
                _apply_native(group, 100)
                info = store.app(name)
        parts = []
        for window in _ordered(group["windows"], saved):
            row = saved.get(window["title"])
            if row:
                plow, phigh = _band(row["low"], row["high"])
                hidden = bool(row["hidden"])
                poff = bool(row["off"])
                pboost = _boost(row["boost"])
            else:
                plow, phigh, hidden, poff, pboost = low, high, False, False, _boost(info["boost"])
            parts.append(
                {
                    "title": window["title"],
                    "pid": window["pid"],
                    "low": plow,
                    "high": phigh,
                    "boost": pboost,
                    "hidden": hidden,
                    "off": poff,
                    "playing": window.get("playing", window["pid"] in group["active"]),
                    "media": window["pid"] < 0,
                    "tabs": window.get("tabs"),
                }
            )
        label = name[:-4] if name.lower().endswith(".exe") else name
        apps.append(
            {
                "name": name,
                "title": label,
                "low": low,
                "high": high,
                "boost": _boost(info["boost"]),
                "off": bool(info["off"]),
                "hidden": bool(info["hidden"]),
                "favorite": bool(info["favorite"]),
                "pos": info["pos"],
                "playing": any(p["playing"] for p in parts) if tabs else media.get(name.lower(), False),
                "media": tabs or name.lower() in media,
                "parts": parts,
            }
        )
    for booster in boosters.values():
        if booster.error:
            error = booster.error
    return {
        "apps": apps,
        "note": error or note or _failure_note(),
        "settings": settings(),
    }


def settings() -> dict:
    low, high, boost = store.defaults()
    return {
        "theme": store.setting("theme"),
        "accent": store.setting("accent"),
        "hotkey": store.setting("hotkey", "Ctrl+Alt+G"),
        "default_low": low,
        "default_high": high,
        "default_boost": boost,
    }


def set_setting(key, value):
    if key == "theme":
        store.set_setting(key, "light" if value == "light" else "dark")
    elif key == "accent":
        text = str(value or "")
        if text and not (len(text) == 7 and text[0] == "#" and all(c in "0123456789abcdefABCDEF" for c in text[1:])):
            raise ValueError("цвет ожидается в виде #rrggbb")
        store.set_setting(key, text.lower())
    elif key == "hotkey":
        store.set_setting(key, str(value or ""))  # формат проверяет host.parse_hotkey
    elif key == "default_band":
        low, high = _band(*value)
        store.set_setting("default_low", str(low))
        store.set_setting("default_high", str(high))
    elif key == "default_boost":
        store.set_setting(key, str(_boost(value)))
    else:
        raise ValueError(f"неизвестная настройка {key}")
    return settings()


def shutdown_audio():
    with state_lock:
        _com()
        groups = _session_groups()
        for pid in list(boosters):
            stop_boost(pid)
        for name, group in groups.items():
            if name == "Системные звуки":
                continue
            bands = _pid_bands(name, group)
            if any(_needs_capture(*band) for band in bands.values()):
                _apply_native(group, 100)


def get_state():
    with state_lock:
        _com()
        return _snapshot()


def _open(name: str):
    global session_groups
    session_groups = _session_groups()
    return name in session_groups


def set_app(name, low, high):
    with state_lock:
        _com()
        try:
            if not _open(name):
                return _snapshot("Программа уже закрыта")
            band = _band(low, high)
            store.set_app(name, *band)
            seen_pids.pop(name, None)
            _apply(name)
            active = not store.app(name)["off"] and _needs_capture(*band)
            note = "Выравнивание включено, звук может чуть отставать." if active else ""
            return _snapshot(note)
        except Exception as exc:
            store.set_flag(name, off=True)
            group = session_groups.get(name)
            if group:
                for pid in group["pids"]:
                    stop_boost(pid)
                _apply_native(group, 100)
            return _snapshot(str(exc))


def set_part(name, title, low, high):
    with state_lock:
        _com()
        try:
            if not _open(name):
                return _snapshot("Программа уже закрыта")
            band = _band(low, high)
            pid = next(
                (item["pid"] for item in session_groups[name]["windows"] if item["title"] == title),
                None,
            )
            titles = [
                item["title"]
                for item in session_groups[name]["windows"]
                if pid is not None and item["pid"] == pid
            ] or [title]
            store.set_part(name, titles, *band)
            seen_pids.pop(name, None)
            _apply(name)
            note = "Выравнивание включено, звук может чуть отставать." if _needs_capture(*band) else ""
            return _snapshot(note)
        except Exception as exc:
            return _snapshot(str(exc))


def set_off(name, off, title=""):
    with state_lock:
        _com()
        try:
            if not _open(name):
                return _snapshot("Программа уже закрыта")
            if title:
                pid = next(
                    (item["pid"] for item in session_groups[name]["windows"] if item["title"] == title),
                    None,
                )
                titles = [
                    item["title"]
                    for item in session_groups[name]["windows"]
                    if pid is not None and item["pid"] == pid
                ] or [title]
                store.set_part_off(name, titles, bool(off))
            else:
                store.set_flag(name, off=bool(off))
            seen_pids.pop(name, None)
            _apply(name)
            return _snapshot()
        except Exception as exc:
            return _snapshot(str(exc))


def set_hidden(name, hidden, title=""):
    with state_lock:
        _com()
        if title:
            store.set_part_hidden(name, title, bool(hidden))
        else:
            store.set_flag(name, hidden=bool(hidden))
        return _snapshot()


def set_favorite(name, favorite):
    with state_lock:
        _com()
        store.set_flag(name, favorite=bool(favorite))
        return _snapshot()


def set_boost(name, boost, title=""):
    with state_lock:
        _com()
        try:
            if not _open(name):
                return _snapshot("Программа уже закрыта")
            value = _boost(boost)
            if title:
                pid = next((w["pid"] for w in session_groups[name]["windows"] if w["title"] == title), None)
                titles = [w["title"] for w in session_groups[name]["windows"] if pid is not None and w["pid"] == pid]
                store.set_part_boost(name, titles or [title], value)
            else:
                store.set_boost(name, value)
            seen_pids.pop(name, None)
            _apply(name)
            return _snapshot()
        except Exception as exc:
            return _snapshot(str(exc))


def _norm(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def match_media(aumid: str, exe: str) -> bool:
    # ponytail: у сессий имена вида «Telegram.TelegramDesktop», не exe; ищем название программы в имени.
    stem = _norm(exe[:-4] if exe.lower().endswith(".exe") else exe)
    return len(stem) >= 3 and stem in _norm(aumid)


PLAYING = 4  # GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING
_media = {"t": 0.0, "status": {}}


def _media_call(work):
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager

    async def run():
        return await work((await Manager.request_async()).get_sessions())

    return asyncio.run(run())


def _refresh_media(names) -> dict[str, bool]:
    """exe.lower() → играет ли сейчас. Найденные программы запоминаем в БД как управляемые."""
    if time.monotonic() - _media["t"] < 1.0:
        return _media["status"]
    _media["t"] = time.monotonic()

    async def read(sessions):
        return [(s.source_app_user_model_id, int(s.get_playback_info().playback_status)) for s in sessions]

    try:
        sessions = _media_call(read)
    except Exception:
        return _media["status"]
    status = {}
    for name in names:
        hits = [state for aumid, state in sessions if match_media(aumid, name)]
        if hits:
            status[name.lower()] = PLAYING in hits
            store.set_media(name)
    _media["status"] = status
    return status


def _toggle_media(exe: str, action: str) -> bool:
    """Play/Pause только сессии этой программы. False — у программы сейчас нет медиасессии."""

    async def send(sessions):
        found = [s for s in sessions if match_media(s.source_app_user_model_id, exe)]
        for session in found:
            if action == "play":
                await session.try_play_async()
            elif action == "pause":
                await session.try_pause_async()
            else:
                await session.try_toggle_play_pause_async()
        return bool(found)

    _media["t"] = 0.0
    return _media_call(send)


def _play_targets(tabs, last_tab, name, title, action, tab) -> list[dict]:
    lname = name.lower()
    if tab:
        return [t for t in tabs if t["id"] == tab]
    if title:
        site = [t for t in tabs if t["site"] == title]
        if action == "play":
            # Все вкладки сайта разом запускать нельзя — только ту, что звучала последней.
            return [t for t in site if t["id"] == last_tab.get(f"{lname}|{title}")] or site[:1]
        return [t for t in site if t["audible"]] or site
    last = [t for t in tabs if t["id"] == last_tab.get(lname)]
    if action == "play":
        return last
    return [t for t in tabs if t["audible"]] or last


def move_app(name, after="", before=""):
    with state_lock:
        _com()
        store.move_app(name, after, before)
        return _snapshot()


def move_part(name, title, after="", before=""):
    """Ставит часть между соседями after и before (заголовки; пустой — край списка)."""
    with state_lock:
        _com()
        if not _open(name):
            return _snapshot("Программа уже закрыта")
        saved = store.parts(name)
        for window in _ordered(session_groups[name]["windows"], saved):
            store.ensure_part(name, window["title"])
        saved = store.parts(name)
        prev = saved.get(after, {}).get("pos") if after else None
        nxt = saved.get(before, {}).get("pos") if before else None
        if prev is not None and nxt is not None:
            store.set_part_pos(name, title, (prev + nxt) / 2)
        elif prev is not None:
            store.set_part_pos(name, title, prev + 1)
        elif nxt is not None:
            store.set_part_pos(name, title, nxt - 1)
        return _snapshot()


def toggle_play(name, title="", action="", tab=0):
    """action приходит из интерфейса: флаг «звучит» у браузера отстаёт на секунду-две и путает быстрые нажатия."""
    with state_lock:
        _com()
        if not _open(name):
            return _snapshot("Программа уже закрыта")
        tabs = _browser_live(name)
        if tabs:
            targets = _play_targets(tabs, _last_tab, name, title, action, int(tab or 0))
            if not targets:
                return _snapshot("Нет вкладки, которую можно запустить")
            if action not in ("play", "pause"):
                action = "pause" if any(t["audible"] for t in targets) else "play"
            _queue_commands(name, [{"tab": t["id"], "action": action} for t in targets])
            return _snapshot()
        try:
            ok = _toggle_media(name, action)
        except Exception as exc:
            return _snapshot(f"Не удалось отправить команду: {exc}")
        return _snapshot("" if ok else f"{name}: нет медиасессии, остановить нельзя")


def _check_band():
    assert match_media("Telegram.TelegramDesktop", "Telegram.exe")
    assert match_media("OperaSoftware.OperaGXWebBrowser.1777985940", "opera.exe")
    assert match_media("Spotify.exe", "Spotify.exe")
    assert not match_media("Telegram.TelegramDesktop", "Discord.exe")
    assert not match_media("anything", "a.exe")
    assert tab_title(" (12) Hostage – YouTube ") == "Hostage – YouTube"
    assert tab_title("(Live) Stream") == "(Live) Stream"
    assert site_of("https://www.youtube.com/watch?v=1") == "youtube.com"
    assert site_of("https://music.yandex.ru/") == "music.yandex.ru"
    assert site_of("file:///C:/a.mp3") == "Файлы"

    tabs = [
        {"id": 1, "site": "youtube.com", "audible": False},
        {"id": 2, "site": "youtube.com", "audible": True},
        {"id": 3, "site": "vk.com", "audible": False},
    ]
    last = {"opera.exe": 3, "opera.exe|youtube.com": 2}
    ids = lambda *args: [t["id"] for t in _play_targets(tabs, last, "Opera.exe", *args)]
    assert ids("", "play", 0) == [3]
    assert ids("", "pause", 0) == [2]
    assert ids("youtube.com", "play", 0) == [2]  # одну вкладку сайта, а не все
    assert ids("youtube.com", "pause", 0) == [2]
    assert ids("vk.com", "pause", 0) == [3]  # флаг «звучит» отстал — пауза всё равно уходит
    assert ids("youtube.com", "play", 1) == [1]
    assert [w["title"] for w in _ordered([{"title": "a"}, {"title": "b"}, {"title": "c"}], {"c": {"pos": 1}, "a": {"pos": 2.5}})] == ["c", "a", "b"]

    class Fake:
        def __init__(self, pid, volume):
            self.ProcessId = pid
            self.SimpleAudioVolume = type("V", (), {"GetMasterVolume": lambda _self: volume})()

    band = (-22.0, -12.0, 100)
    assert _drifted({"sessions": [Fake(5, 1.0)]}, {5: band})  # обработка есть, а громкость не приглушена
    assert not _drifted({"sessions": [Fake(5, ATTEN)]}, {5: band})
    assert _drifted({"sessions": [Fake(5, ATTEN)]}, {5: FULL})  # обработки нет, а звук застрял на 1 %
    assert not _drifted({"sessions": [Fake(5, 0.7)]}, {5: FULL})
    assert _band(-21.54, -12.04) == (-21.5, -12.0)
    assert _band(-99, 3) == (DB_MIN, DB_MAX)
    assert _band(DEFAULT_LOW, DEFAULT_HIGH) == (-22.0, -12.0)
    assert _setting({"off": 0, "hidden": 1, "low": -22, "high": -12, "boost": 150}) == FULL
    assert _setting({"off": 0, "hidden": 0, "low": -22, "high": -12, "boost": 100}) == (-22.0, -12.0, 100)


if __name__ == "__main__":
    _check_band()
