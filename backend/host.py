"""Окно у трея, автозапуск и мост к интерфейсу."""

import ctypes
import sys
import threading
import time
import traceback
import winreg
from ctypes import byref
from pathlib import Path

from engine import demo, play_tone
from mixer import get_state, set_app, set_master, shutdown_audio
from native import _Rect, kernel32, user32

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

    def set_master(self, percent):
        return _with_autostart(set_master(percent))

    def set_app(self, name, percent):
        return _with_autostart(set_app(name, percent))

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


def _message(text: str):
    ctypes.windll.user32.MessageBoxW(None, text, "Громкость", 0x10)


def _place(form):
    area = _Rect()
    if not user32.SystemParametersInfoW(0x30, 0, byref(area), 0):
        return
    form.Left = max(area.left, area.right - form.Width - 12)
    form.Top = max(area.top, area.bottom - form.Height - 12)


def _as_tool(form):
    hwnd = form.Handle.ToInt64()
    style = user32.GetWindowLongPtrW(hwnd, -20)
    style = (style | 0x80) & ~0x40000  # TOOLWINDOW, без APPWINDOW: нет панели задач и Alt+Tab
    user32.SetWindowLongPtrW(hwnd, -20, style)
    user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x27)


def run_app(background: bool):
    import webview
    import pystray

    ui = _ui_path()
    if not ui.exists():
        _message("Не найден интерфейс.\nСоберите его командой npm run build в папке frontend.")
        return

    show_event = kernel32.CreateEventW(None, True, False, "Local\\GromkostShow")
    kernel32.CreateMutexW(None, False, "Local\\GromkostApp")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
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
        background_color="#101114",
        hidden=background,
        text_select=False,
        confirm_close=False,
    )
    quitting = {"value": False}
    hidden_at = {"t": 0.0}
    shown_at = {"t": 0.0}

    def show_popup(force=False):
        if not force and time.time() - hidden_at["t"] < 0.3:
            return
        form = window.native

        def run():
            _place(form)
            shown_at["t"] = time.time()
            window.show()

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
        hidden_at["t"] = time.time()
        window.hide()
        return False

    def prepare():
        from System import Action

        form = window.native
        # ShowInTaskbar после создания окна пересоздаёт HWND и отрывает WebView — остаётся чёрный прямоугольник.
        _as_tool(form)
        _place(form)
        shown_at["t"] = time.time()

        def on_deactivate(_sender, _args):
            if quitting["value"] or time.time() - shown_at["t"] < 0.4:
                return
            hidden_at["t"] = time.time()
            form.BeginInvoke(Action(window.hide))

        form.Deactivate += on_deactivate

    window.events.closing += on_closing
    window.events.before_show += prepare

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
                if form is None or not form.Visible:
                    continue
                hwnd = form.Handle.ToInt64()
            except Exception:
                continue
            fg = user32.GetForegroundWindow()
            if fg and (int(fg) == hwnd or user32.IsChild(hwnd, fg)):
                continue
            hidden_at["t"] = time.time()
            try:
                window.hide()
            except Exception:
                pass

    threading.Thread(target=watch_focus, name="focus", daemon=True).start()

    def on_open(_icon, _item):
        form = window.native
        try:
            visible = bool(form.Visible)
        except Exception:
            visible = False
        if visible:
            hidden_at["t"] = time.time()
            window.hide()
        else:
            show_popup()

    def on_toggle_autostart(_icon, _item):
        set_autostart(not autostart_enabled())

    def on_quit(_icon, _item):
        quitting["value"] = True
        shutdown_audio()
        try:
            window.destroy()
        except Exception:
            pass
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Открыть", on_open, default=True),
        pystray.MenuItem("Автозапуск", on_toggle_autostart, checked=lambda _item: autostart_enabled()),
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

