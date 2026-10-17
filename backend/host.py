"""Окно у трея, автозапуск и мост к интерфейсу."""

import ctypes
import json
import subprocess
import sys
import threading
import time
import traceback
import winreg
from ctypes import byref
from ctypes.wintypes import BOOL, DWORD
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from pathlib import Path

import psutil

from engine import demo, play_tone
from mixer import (
    get_state,
    set_app,
    set_favorite,
    set_hidden,
    set_off,
    set_part,
    shutdown_audio,
    bridge_debug,
    move_app,
    move_part,
    report_failure,
    set_setting,
    settings,
    sync_browser_tabs,
    wait_commands,
    toggle_play,
    set_boost,
)
from native import _Msg, _Rect, dwmapi, gdi32, kernel32, user32

BRIDGE_PORT = 47921


class _BridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _reply(self, data):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except OSError:
            pass

    def _peer_exe(self) -> str:
        """Чей процесс открыл это соединение. User-Agent не годится: Brave, Vivaldi и Arc выдают себя за Chrome."""
        port = self.client_address[1]
        for conn in psutil.net_connections("tcp4"):
            if conn.laddr and conn.laddr.port == port and conn.raddr and conn.raddr.port == BRIDGE_PORT and conn.pid:
                return psutil.Process(conn.pid).name()
        return ""

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/debug":
            self._reply(bridge_debug())
        elif url.path == "/whoami":
            try:
                self._reply({"exe": self._peer_exe()})
            except Exception:
                self._reply({"exe": ""})
        elif url.path == "/wait":
            self._reply({"commands": wait_commands(parse_qs(url.query).get("exe", [""])[0])})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/sync":
                self._reply(sync_browser_tabs(body.get("exe", "opera.exe"), body.get("tabs", [])))
            elif self.path == "/failed":
                report_failure(body.get("exe", ""), body.get("tab"), body.get("reason", ""))
                self._reply({})
            elif self.path == "/poke":
                _poke_browser(body.get("exe", ""))
                self._reply({})
            else:
                self.send_response(404)
                self.end_headers()
        except Exception:
            self.send_response(400)
            self.end_headers()


def _poke_browser(exe: str):
    """Клавиша «играть» в окно браузера. Синтетический клик на странице браузер за жест не считает."""
    target = (exe or "").lower()
    if not target:
        return
    found = []

    @ctypes.WINFUNCTYPE(BOOL, c_void_p, c_void_p)
    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):
            return True
        pid = DWORD()
        user32.GetWindowThreadProcessId(hwnd, byref(pid))
        if not pid.value:
            return True
        try:
            name = psutil.Process(int(pid.value)).name().lower()
        except Exception:
            return True
        if name == target:
            found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    if found:
        user32.SendMessageW(found[0], 0x0319, found[0], 46 << 16)  # WM_APPCOMMAND, APPCOMMAND_MEDIA_PLAY


def _start_bridge():
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", BRIDGE_PORT), _BridgeHandler)
        threading.Thread(target=srv.serve_forever, name="browser-bridge", daemon=True).start()
    except OSError:
        pass

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_KEY = "Gromkost"
ERROR_ALREADY_EXISTS = 183

def launch_command() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        sibling = exe.with_name("pythonw.exe")
        if sibling.exists():
            exe = sibling
    return f'"{exe}" "{Path(__file__).resolve().parent / "volume.py"}" --background'


def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_KEY)
            return True
    except OSError:
        return False


def set_autostart(enabled: bool):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_KEY, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(key, APP_KEY)
            except OSError:
                pass


def _with_autostart(state):
    state["autostart"] = autostart_enabled()
    return state


class Api:
    def get_state(self):
        return _with_autostart(get_state())

    def set_app(self, name, low, high):
        return _with_autostart(set_app(name, low, high))

    def set_part(self, name, title, low, high):
        return _with_autostart(set_part(name, title, low, high))

    def set_off(self, name, off, title=""):
        return _with_autostart(set_off(name, off, title or ""))

    def set_hidden(self, name, hidden, title=""):
        return _with_autostart(set_hidden(name, hidden, title or ""))

    def set_boost(self, name, boost, title=""):
        return _with_autostart(set_boost(name, boost, title or ""))

    def toggle_play(self, name, title="", action="", tab=0):
        return _with_autostart(toggle_play(name, title or "", action or "", tab or 0))

    def move_app(self, name, after="", before=""):
        return _with_autostart(move_app(name, after or "", before or ""))

    def move_part(self, name, title, after="", before=""):
        return _with_autostart(move_part(name, title, after or "", before or ""))

    def set_favorite(self, name, favorite):
        return _with_autostart(set_favorite(name, favorite))

    # Только с подчёркиванием: pywebview обходит публичные атрибуты js_api, а обход окна WinForms из чужого потока вешает интерфейс.
    _hotkey = None
    _form = None

    def suspend_hotkey(self, paused):
        if self._hotkey:
            self._hotkey.set("" if paused else settings()["hotkey"])

    def set_setting(self, key, value):
        if key == "hotkey":
            value = parse_hotkey(value)[0] if value else ""
            if self._hotkey and not self._hotkey.set(value):
                self._hotkey.set(settings()["hotkey"])
                raise ValueError(f"{value} уже занято другой программой")
        result = set_setting(key, value)
        if key == "theme" and self._form is not None:
            _set_backdrop(self._form, result["theme"])
        return result

    def set_autostart(self, enabled):
        set_autostart(bool(enabled))
        return self.get_state()


def _ui_path() -> Path:
    return Path(__file__).resolve().parent.parent / "frontend" / "dist" / "index.html"


def _tray_image():
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, 61, 61), radius=16, fill=(26, 28, 34, 255))
    draw.rounded_rectangle((16, 26, 28, 38), radius=2, fill=(232, 168, 124, 255))
    draw.polygon([(28, 22), (40, 14), (40, 50), (28, 42)], fill=(232, 168, 124, 255))
    draw.arc((40, 22, 54, 42), start=300, end=60, fill=(232, 168, 124, 255), width=3)
    return image


def _spawn_self():
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        sibling = exe.with_name("pythonw.exe")
        if sibling.exists():
            exe = sibling
    args = [str(exe), str(Path(__file__).resolve().parent / "volume.py")]
    if "--background" in sys.argv:
        args.append("--background")
    # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: новая копия живёт после выхода этой.
    subprocess.Popen(
        args,
        cwd=str(Path(__file__).resolve().parent.parent),
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )


def _message(text: str):
    ctypes.windll.user32.MessageBoxW(None, text, "Громкость", 0x10)


def _work_area() -> _Rect:
    area = _Rect()
    user32.SystemParametersInfoW(0x30, 0, byref(area), 0)
    return area


CORNER = 18  # px при 100 %; угол DWM в Windows 11 фиксирован (~8 px) и не увеличивается


def _place(hwnd, left, top, w, h):
    # Регион до смены размера и без лишней перерисовки, иначе кадр стирается фоном.
    if getattr(_place, "size", None) != (w, h):
        _place.size = (w, h)
        # ponytail: регион без сглаживания — на краю угла видна лесенка; гладко только через прозрачное окно.
        r = round(CORNER * 2 * (user32.GetDpiForWindow(hwnd) or 96) / 96)
        user32.SetWindowRgn(hwnd, gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, r, r), False)
    # 0x0014 = SWP_NOZORDER | SWP_NOACTIVATE
    user32.SetWindowPos(hwnd, None, left, top, w, h, 0x0014)


# Совпадает с --ink в index.html: этим цветом окно залито, пока страница не дорисовала новый кадр.
BACKDROP = {"dark": "#101114", "light": "#f3f1ec"}


def _paint_backdrop(form, theme: str):
    from System.Drawing import ColorTranslator

    color = ColorTranslator.FromHtml(BACKDROP.get(theme, BACKDROP["dark"]))
    form.BackColor = color
    form.webview.DefaultBackgroundColor = color


def _set_backdrop(form, theme: str):
    from System import Action

    def run():
        _paint_backdrop(form, theme)

    try:
        form.BeginInvoke(Action(run))
    except Exception:
        pass


_MODS = {"ctrl": 0x2, "alt": 0x1, "shift": 0x4, "win": 0x8}
_KEY_NAMES = {"space": 0x20, "tab": 0x09, "enter": 0x0D, "esc": 0x1B, "backspace": 0x08}
WM_HOTKEY, WM_APP = 0x0312, 0x8000


def parse_hotkey(text: str) -> tuple[str, int, int]:
    """«ctrl + alt+g» → ("Ctrl+Alt+G", модификаторы, код клавиши). Без модификатора не принимаем."""
    parts = [p.strip().lower() for p in str(text or "").split("+") if p.strip()]
    mods = [p for p in parts if p in _MODS]
    keys = [p for p in parts if p not in _MODS]
    if not mods or len(keys) != 1:
        raise ValueError("Нужно сочетание вида Ctrl+Alt+G")
    key = keys[0]
    if len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key.upper())
    elif key[0] == "f" and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x6F + int(key[1:])
    elif key in _KEY_NAMES:
        vk = _KEY_NAMES[key]
    else:
        raise ValueError(f"Клавиша «{key}» не поддерживается")
    order = [m for m in ("ctrl", "alt", "shift", "win") if m in mods]
    name = key.upper() if len(key) == 1 or key[1:].isdigit() else key.capitalize()
    label = "+".join([m.capitalize() for m in order] + [name])
    return label, sum(_MODS[m] for m in order), vk


class Hotkey:
    """RegisterHotKey живёт в потоке, который его зарегистрировал, — держим отдельный поток с очередью сообщений."""

    def __init__(self, on_press):
        self.on_press = on_press
        self.wanted = None
        self.ok = True
        self.done = threading.Event()
        self.lock = threading.Lock()
        ready = threading.Event()

        def run():
            msg = _Msg()
            user32.PeekMessageW(byref(msg), None, 0, 0, 0)  # создаёт очередь потока
            self.tid = kernel32.GetCurrentThreadId()
            ready.set()
            while user32.GetMessageW(byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    self.on_press()
                elif msg.message == WM_APP:
                    user32.UnregisterHotKey(None, 1)
                    self.ok = True
                    if self.wanted:
                        # После перезапуска прошлое окно ещё мгновение держит то же сочетание.
                        self.ok = False
                        for _ in range(25):
                            if user32.RegisterHotKey(None, 1, self.wanted[0] | 0x4000, self.wanted[1]):
                                self.ok = True
                                break
                            time.sleep(0.04)
                    self.done.set()

        threading.Thread(target=run, name="hotkey", daemon=True).start()
        ready.wait(2)

    def set(self, text: str) -> bool:
        """Пустая строка — выключить. False — сочетание уже заняла другая программа."""
        with self.lock:
            self.wanted = parse_hotkey(text)[1:] if text else None
            self.done.clear()
            user32.PostThreadMessageW(self.tid, WM_APP, None, None)
            self.done.wait(2)
            return self.ok


def _as_tool(form):
    hwnd = form.Handle.ToInt64()
    style = user32.GetWindowLongPtrW(hwnd, -20)
    style = (style | 0x80) & ~0x40000  # TOOLWINDOW, без APPWINDOW: нет панели задач и Alt+Tab
    user32.SetWindowLongPtrW(hwnd, -20, style)
    user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x27)
    # Win11 сама скругляет окно и в углах нашего региона оставляет белые треугольники.
    pref = ctypes.c_int(1)  # DWMWCP_DONOTROUND
    dwmapi.DwmSetWindowAttribute(hwnd, 33, byref(pref), 4)


def run_app(background: bool):
    import webview
    import pystray

    ui = _ui_path()
    if not ui.exists():
        _message("Не найден интерфейс.\nСоберите его командой npm run build в папке frontend.")
        return

    show_event = kernel32.CreateEventW(None, True, False, "Local\\GromkostShow")
    mutex = kernel32.CreateMutexW(None, False, "Local\\GromkostApp")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(mutex)
        if not background:
            kernel32.SetEvent(show_event)
        return

    api = Api()
    window = webview.create_window(
        "Громкость",
        ui.as_uri(),
        js_api=api,
        width=380,
        height=560,
        frameless=True,
        easy_drag=False,
        on_top=True,
        resizable=False,
        shadow=True,
        background_color=BACKDROP.get(settings()["theme"], BACKDROP["dark"]),
        hidden=background,
        text_select=False,
        confirm_close=False,
    )
    quitting = {"value": False}
    hidden_at = {"t": 0.0}
    hiding = {"value": False}
    shown_at = {"t": 0.0}
    shown = {"value": not background}
    geom = {"w": 0, "h": 0, "seq": 0}
    loaded = threading.Event()
    _start_bridge()
    if background:
        threading.Thread(target=get_state, name="init-audio", daemon=True).start()

    def notify_ui(event_name: str):
        def send():
            if not loaded.wait(3):
                return
            try:
                window.evaluate_js(f'window.dispatchEvent(new Event("{event_name}"))')
            except Exception:
                pass

        threading.Thread(target=send, daemon=True).start()

    def _capture_geom(form):
        if not geom["h"]:
            geom["w"] = int(form.Width)
            geom["h"] = int(form.Height)

    def _frame_at(progress: float) -> tuple[int, int, int, int]:
        area = _work_area()
        w, full_h = geom["w"], geom["h"]
        left = max(area.left, area.right - w - 12)
        target_top = max(area.top, area.bottom - full_h - 12)
        start_h = max(140, int(full_h * 0.38))
        h = int(round(start_h + (full_h - start_h) * progress))
        bottom = int(round(target_top + full_h + 28 * (1.0 - progress)))
        return left, bottom - h, w, h

    def _set_bounds(form, progress: float):
        _capture_geom(form)
        hwnd = form.Handle.ToInt64()
        _place(hwnd, *_frame_at(progress))

    def hide_popup():
        if hiding["value"]:
            return
        geom["seq"] += 1
        seq = geom["seq"]
        hidden_at["t"] = time.time()
        notify_ui("popup:close")
        form = window.native

        def run():
            hiding["value"] = True
            try:
                if geom["h"]:
                    animate_slide(form, seq, closing=True)
            except Exception:
                pass
            finally:
                hiding["value"] = False
                if geom["seq"] != seq:
                    return
                # window.hide() сам зовёт Invoke и иногда не доходит: окно остаётся тёмным прямоугольником.
                user32.ShowWindow(form.Handle.ToInt64(), 0)
                shown["value"] = False

                def run():
                    form.Hide()

                try:
                    from System import Action

                    if form.InvokeRequired:
                        form.BeginInvoke(Action(run))
                    else:
                        run()
                except Exception:
                    pass

        threading.Thread(target=run, name="hide", daemon=True).start()

    def animate_slide(form, seq: int, closing: bool = False) -> bool:
        """True — анимация дошла до конца, её не перебили новым открытием или закрытием."""
        hwnd = form.Handle.ToInt64()
        duration = 0.18 if closing else 0.24
        if closing:
            time.sleep(0.24)  # список гаснет по opacity и уходит из вёрстки, пока высота окна ещё полная
        t0 = time.perf_counter()
        while not quitting["value"] and geom["seq"] == seq:
            elapsed = time.perf_counter() - t0
            t = min(1.0, elapsed / duration)
            ease = 1.0 - t**3 if closing else 1.0 - (1.0 - t) ** 3
            _place(hwnd, *_frame_at(ease))
            if t >= 1.0:
                if not closing:
                    notify_ui("popup:settled")
                return True
            time.sleep(0.008)
        return False

    def show_popup(force=False):
        if not force and time.time() - hidden_at["t"] < 0.3:
            return
        form = window.native

        def run():
            geom["seq"] += 1
            seq = geom["seq"]
            _paint_backdrop(form, settings()["theme"])
            _set_bounds(form, 0.0)
            shown["value"] = True
            shown_at["t"] = time.time()
            notify_ui("popup:open")
            _as_tool(form)
            window.show()
            _as_tool(form)
            user32.SetForegroundWindow(form.Handle.ToInt64())  # по горячей клавише фокус остаётся у прежней программы
            threading.Thread(target=animate_slide, args=(form, seq), daemon=True).start()

        try:
            if form.InvokeRequired:
                from System import Action

                form.BeginInvoke(Action(run))
            else:
                run()
        except Exception:
            pass

    def on_closing():
        if quitting["value"]:
            return True
        hide_popup()
        return False

    def on_loaded():
        loaded.set()
        if not background:
            notify_ui("popup:open")

    def prepare():
        from System import Action

        form = window.native
        api._form = form
        # ShowInTaskbar после создания окна пересоздаёт HWND и отрывает WebView — остаётся чёрный прямоугольник.
        _as_tool(form)
        geom["seq"] += 1
        seq = geom["seq"]
        _paint_backdrop(form, settings()["theme"])
        _set_bounds(form, 0.0)
        shown_at["t"] = time.time()
        threading.Thread(target=animate_slide, args=(form, seq), daemon=True).start()

        def on_deactivate(_sender, _args):
            if quitting["value"] or time.time() - shown_at["t"] < 0.4:
                return
            form.BeginInvoke(Action(hide_popup))

        form.Deactivate += on_deactivate

    window.events.closing += on_closing
    window.events.before_show += prepare
    window.events.loaded += on_loaded

    def watch_show():
        while not quitting["value"]:
            if kernel32.WaitForSingleObject(show_event, 400) == 0:
                kernel32.ResetEvent(show_event)
                show_popup(force=True)

    threading.Thread(target=watch_show, name="show", daemon=True).start()

    def watch_focus():
        # ponytail: опрос 5 раз/с. Если клик снаружи случился до первого фокуса,
        # Deactivate больше не придёт — тогда прячем отсюда.
        while not quitting["value"]:
            time.sleep(0.2)
            if time.time() - shown_at["t"] < 0.4:
                continue
            form = window.native
            try:
                if form is None or not shown["value"]:
                    continue
                hwnd = form.Handle.ToInt64()
            except Exception:
                continue
            fg = user32.GetForegroundWindow()
            if fg and (int(fg) == hwnd or user32.IsChild(hwnd, fg)):
                continue
            hide_popup()

    threading.Thread(target=watch_focus, name="focus", daemon=True).start()

    def on_open(_icon, _item):
        if shown["value"]:
            hide_popup()
        else:
            show_popup()

    api._hotkey = Hotkey(lambda: on_open(None, None))
    try:
        api._hotkey.set(settings()["hotkey"])
    except ValueError:
        pass

    def on_toggle_autostart(_icon, _item):
        set_autostart(not autostart_enabled())

    def on_quit(_icon, _item):
        quitting["value"] = True
        if not quitting.get("stopped"):
            shutdown_audio()
        try:
            window.destroy()
        except Exception:
            pass
        icon.stop()

    def on_restart(_icon, _item):
        # Имя мьютекса и хоткей держат этот процесс. Отпустить до запуска, иначе новая копия не откроется по сочетанию.
        try:
            api._hotkey.set("")
        except Exception:
            pass
        kernel32.CloseHandle(mutex)
        shutdown_audio()
        quitting["stopped"] = True
        _spawn_self()
        on_quit(_icon, _item)

    menu = pystray.Menu(
        pystray.MenuItem("Открыть", on_open, default=True),
        pystray.MenuItem("Автозапуск", on_toggle_autostart, checked=lambda _item: autostart_enabled()),
        pystray.MenuItem("Перезапуск", on_restart),
        pystray.MenuItem("Выход", on_quit),
    )
    icon = pystray.Icon("gromkost", _tray_image(), "Громкость", menu)
    # ponytail: один поток трея на всё приложение.
    threading.Thread(target=icon.run, name="tray", daemon=True).start()
    try:
        webview.start()
    finally:
        quitting["value"] = True
        if not quitting.get("stopped"):
            shutdown_audio()
        try:
            icon.stop()
        except Exception:
            pass


def main():
    if "--tone" in sys.argv:
        play_tone()
        return
    if "--check" in sys.argv:
        demo()
        return
    if "--check-hotkey" in sys.argv:
        assert parse_hotkey(" ctrl + alt+g") == ("Ctrl+Alt+G", 0x3, 0x47)
        assert parse_hotkey("Shift+Win+F12") == ("Shift+Win+F12", 0xC, 0x7B)
        assert parse_hotkey("Ctrl+Space")[1:] == (0x2, 0x20)
        for bad in ("G", "Ctrl+Alt", "Ctrl+Ж", "Ctrl+A+B"):
            try:
                parse_hotkey(bad)
                raise AssertionError(bad)
            except ValueError:
                pass
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    try:
        run_app(background="--background" in sys.argv)
    except Exception:
        log = Path(__file__).resolve().with_name("volume.log")
        log.write_text(traceback.format_exc(), encoding="utf-8")
        _message(f"Не удалось запустить программу.\nПодробности: {log}")

