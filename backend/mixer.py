"""Какие программы звучат и какая у них громкость."""

import os
import threading
import ctypes
from ctypes import byref, c_void_p
from ctypes.wintypes import BOOL, DWORD
from pathlib import Path

import comtypes
import psutil
from pycaw.api.audiopolicy import IAudioSessionControl2
from pycaw.constants import DEVICE_STATE, AudioSessionState, EDataFlow
from pycaw.pycaw import AudioUtilities
from pycaw.utils import AudioSession

from engine import ATTEN, boosters, ensure_boost, root_pids, stop_boost
from native import kernel32, psapi, user32

_com_state = threading.local()
state_lock = threading.Lock()
levels: dict[str, int] = {}
chosen: dict[str, int] = {}
session_groups: dict[str, dict] = {}
seen_pids: dict[str, set] = {}
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


def _process_name(pid: int, from_window: bool) -> str | None:
    if pid == os.getpid():
        return None
    if pid == 0:
        return None if from_window else "Системные звуки"
    try:
        name = psutil.Process(pid).name()
    except Exception:
        name = f"Процесс {pid}"
    if from_window and name.lower() in _SHELL:
        return None
    return name


def _all_sessions():
    devices = AudioUtilities.GetAllDevices(EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value)
    for device in devices:
        try:
            enumerator = device.AudioSessionManager.GetSessionEnumerator()
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
known_audio: set[str] = set()


def _known_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Gromkost"
    return root / "audio.txt"


def _load_known():
    try:
        known_audio.update(
            line.strip().lower()
            for line in _known_path().read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    except OSError:
        pass


def _remember(name: str):
    key = name.lower()
    if not key.endswith(".exe") or key in known_audio:
        return
    known_audio.add(key)
    path = _known_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(sorted(known_audio)) + "\n", encoding="utf-8")


_load_known()


def _uses_wasapi(pid: int) -> bool:
    # ponytail: audioses.dll значит, что процесс сам открывал звуковую сессию.
    # Уже известные программы читаются из файла и больше не сканируются.
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


def _window_pids() -> set[int]:
    found: set[int] = set()

    @ctypes.WINFUNCTYPE(BOOL, c_void_p, c_void_p)
    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindow(hwnd, 4):  # GW_OWNER: дочерние диалоги не отдельные программы
            return True
        if user32.GetWindowTextLengthW(hwnd) <= 0:
            return True
        pid = DWORD()
        user32.GetWindowThreadProcessId(hwnd, byref(pid))
        if pid.value:
            found.add(int(pid.value))
        return True

    user32.EnumWindows(visit, 0)
    return found


def _session_groups():
    groups: dict[str, dict] = {}

    def add(name: str, pid: int, session=None):
        bucket = groups.setdefault(name, {"pids": set(), "sessions": []})
        bucket["pids"].add(pid)
        if session is not None:
            bucket["sessions"].append(session)

    for session in _all_sessions():
        try:
            if int(session.State) == int(AudioSessionState.Expired):
                continue
            pid = int(session.ProcessId)
        except Exception:
            continue
        name = _process_name(pid, from_window=False)
        if name:
            add(name, pid, session)
            _remember(name)
    seen = {pid for group in groups.values() for pid in group["pids"]}
    for pid in _window_pids():
        seen.add(pid)
        name = _process_name(pid, from_window=True)
        if not name:
            continue
        if name.lower() in known_audio or _uses_wasapi(pid):
            add(name, pid)
            _remember(name)
    for pid in list(_wasapi_yes):
        if pid not in seen:
            _wasapi_yes.discard(pid)
    for pid in list(_wasapi_no):
        if pid not in seen:
            _wasapi_no.discard(pid)
    return groups


def _level_of(sessions) -> int:
    found = []
    for session in sessions:
        volume = session.SimpleAudioVolume
        if bool(volume.GetMute()):
            found.append(0)
        else:
            found.append(round(float(volume.GetMasterVolume()) * 100))
    return max(found) if found else 100


def _master() -> int:
    device = AudioUtilities.GetSpeakers()
    if device is None:
        return 0
    return int(round(device.volume_percent))


def _apply_native(group, percent: int):
    level = max(0.0, min(1.0, percent / 100.0))
    for session in group["sessions"]:
        volume = session.SimpleAudioVolume
        volume.SetMasterVolume(level, None)
        volume.SetMute(0 if level else 1, None)


def _apply(name: str, percent: int):
    group = session_groups.get(name)
    if not group:
        return
    pids = [pid for pid in group["pids"] if pid]
    if percent > 100 and pids and not group["sessions"]:
        return
    if percent > 100 and pids:
        roots = root_pids(pids)
        for pid in pids:
            if pid not in roots:
                stop_boost(pid)
        for session in group["sessions"]:
            volume = session.SimpleAudioVolume
            current = float(volume.GetMasterVolume())
            if bool(volume.GetMute()) or abs(current - ATTEN) > 0.002:
                volume.SetMute(0, None)
                volume.SetMasterVolume(ATTEN, None)
        try:
            for pid in roots:
                ensure_boost(pid, percent / 100.0)
        except Exception:
            for pid in roots:
                stop_boost(pid)
            _apply_native(group, min(percent, 100))
            raise
        seen_pids[name] = set(group["pids"])
        return
    for pid in pids:
        stop_boost(pid)
    seen_pids.pop(name, None)
    _apply_native(group, percent)


def _snapshot(note: str = "") -> dict:
    global session_groups
    session_groups = _session_groups()
    live = {pid for group in session_groups.values() for pid in group["pids"]}
    for pid in list(boosters):
        if pid not in live:
            stop_boost(pid)
    for name in list(levels):
        if name not in session_groups:
            levels.pop(name, None)
            chosen.pop(name, None)
            seen_pids.pop(name, None)
    apps = []
    error = ""
    for name in sorted(session_groups, key=str.lower):
        if name == "Системные звуки":
            continue
        group = session_groups[name]
        limit = 100 if group["pids"] == {0} else 600
        boosted = any(pid in boosters for pid in group["pids"])
        if name in chosen:
            levels[name] = min(chosen[name], limit)
        elif group["sessions"] and not boosted:
            levels[name] = min(_level_of(group["sessions"]), limit)
        else:
            levels[name] = min(max(0, levels.get(name, 100)), limit)
        if levels[name] > 100 and group["sessions"] and (
            not boosted or group["pids"] != seen_pids.get(name)
        ):
            try:
                _apply(name, levels[name])
            except Exception as exc:
                error = str(exc)
                levels[name] = min(levels[name], 100)
                chosen[name] = levels[name]
                _apply_native(group, levels[name])
        elif name in chosen and group["sessions"] and levels[name] <= 100:
            if _level_of(group["sessions"]) != levels[name]:
                _apply_native(group, levels[name])
        label = name[:-4] if name.lower().endswith(".exe") else name
        title = label if len(group["pids"]) == 1 else f"{label}  ({len(group['pids'])})"
        apps.append(
            {"name": name, "title": title, "percent": int(levels[name]), "limit": limit}
        )
    for booster in boosters.values():
        if booster.error:
            error = booster.error
    return {
        "master": _master(),
        "apps": apps,
        "note": error or note,
    }


def shutdown_audio():
    with state_lock:
        _com()
        groups = _session_groups()
        for pid in list(boosters):
            stop_boost(pid)
        for name, level in list(levels.items()):
            if level > 100 and name in groups:
                _apply_native(groups[name], 100)


def get_state():
    with state_lock:
        _com()
        return _snapshot()

def set_master(percent):
    with state_lock:
        _com()
        try:
            device = AudioUtilities.GetSpeakers()
            if device is not None:
                device.volume_percent = max(0, min(100, int(float(percent))))
            return _snapshot()
        except Exception as exc:
            return _snapshot(str(exc))

def set_app(name, percent):
    with state_lock:
        _com()
        try:
            global session_groups
            session_groups = _session_groups()
            if name not in session_groups:
                return _snapshot("Программа уже закрыта")
            limit = 100 if session_groups[name]["pids"] == {0} else 600
            value = max(0, min(limit, int(float(percent))))
            levels[name] = value
            chosen[name] = value
            _apply(name, value)
            note = "Усиление включено, звук может чуть отставать." if value > 100 else ""
            return _snapshot(note)
        except Exception as exc:
            levels[name] = min(levels.get(name, 100), 100)
            chosen[name] = levels[name]
            group = session_groups.get(name)
            if group:
                _apply_native(group, levels[name])
            return _snapshot(str(exc))
