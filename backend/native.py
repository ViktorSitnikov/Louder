"""Привязки Win32: события, окна и список DLL процесса."""

import ctypes
from ctypes import POINTER, Structure, c_void_p
from ctypes.wintypes import BOOL, DWORD, HANDLE

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateEventW.argtypes = [c_void_p, BOOL, BOOL, ctypes.c_wchar_p]
kernel32.CreateEventW.restype = HANDLE
kernel32.SetEvent.argtypes = [HANDLE]
kernel32.SetEvent.restype = BOOL
kernel32.WaitForSingleObject.argtypes = [HANDLE, DWORD]
kernel32.WaitForSingleObject.restype = DWORD
kernel32.WaitForMultipleObjects.argtypes = [DWORD, POINTER(HANDLE), BOOL, DWORD]
kernel32.WaitForMultipleObjects.restype = DWORD
kernel32.CloseHandle.argtypes = [HANDLE]
kernel32.CloseHandle.restype = BOOL
kernel32.CreateMutexW.argtypes = [c_void_p, BOOL, ctypes.c_wchar_p]
kernel32.CreateMutexW.restype = HANDLE
kernel32.ResetEvent.argtypes = [HANDLE]
kernel32.ResetEvent.restype = BOOL
kernel32.OpenProcess.argtypes = [DWORD, BOOL, DWORD]
kernel32.OpenProcess.restype = HANDLE

psapi = ctypes.WinDLL("psapi", use_last_error=True)
psapi.EnumProcessModulesEx.argtypes = [HANDLE, c_void_p, DWORD, POINTER(DWORD), DWORD]
psapi.EnumProcessModulesEx.restype = BOOL
psapi.GetModuleBaseNameW.argtypes = [HANDLE, c_void_p, ctypes.c_wchar_p, DWORD]
psapi.GetModuleBaseNameW.restype = DWORD

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.IsWindowVisible.argtypes = [c_void_p]
user32.IsWindowVisible.restype = BOOL
user32.GetWindow.argtypes = [c_void_p, ctypes.c_uint]
user32.GetWindow.restype = c_void_p
user32.GetWindowTextLengthW.argtypes = [c_void_p]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [c_void_p, ctypes.c_wchar_p, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [c_void_p, POINTER(DWORD)]
user32.GetWindowThreadProcessId.restype = DWORD
user32.EnumWindows.argtypes = [c_void_p, c_void_p]
user32.EnumWindows.restype = BOOL
user32.GetWindowLongPtrW.argtypes = [c_void_p, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [c_void_p, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowPos.argtypes = [c_void_p, c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.SetWindowPos.restype = BOOL
user32.SystemParametersInfoW.argtypes = [ctypes.c_uint, ctypes.c_uint, c_void_p, ctypes.c_uint]
user32.SystemParametersInfoW.restype = BOOL
user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
user32.FindWindowW.restype = c_void_p
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = c_void_p
user32.IsChild.argtypes = [c_void_p, c_void_p]
user32.IsChild.restype = BOOL
user32.SetWindowRgn.argtypes = [c_void_p, c_void_p, BOOL]
user32.SetWindowRgn.restype = ctypes.c_int
user32.GetDpiForWindow.argtypes = [c_void_p]
user32.GetDpiForWindow.restype = ctypes.c_uint
user32.SetForegroundWindow.argtypes = [c_void_p]
user32.SetForegroundWindow.restype = BOOL
user32.ShowWindow.argtypes = [c_void_p, ctypes.c_int]
user32.ShowWindow.restype = BOOL
user32.RegisterHotKey.argtypes = [c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
user32.RegisterHotKey.restype = BOOL
user32.UnregisterHotKey.argtypes = [c_void_p, ctypes.c_int]
user32.UnregisterHotKey.restype = BOOL
user32.GetMessageW.argtypes = [c_void_p, c_void_p, ctypes.c_uint, ctypes.c_uint]
user32.GetMessageW.restype = BOOL
user32.PeekMessageW.argtypes = [c_void_p, c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
user32.PeekMessageW.restype = BOOL
user32.PostThreadMessageW.argtypes = [DWORD, ctypes.c_uint, c_void_p, c_void_p]
user32.PostThreadMessageW.restype = BOOL
user32.SendMessageW.argtypes = [c_void_p, ctypes.c_uint, c_void_p, ctypes.c_ssize_t]
user32.SendMessageW.restype = ctypes.c_ssize_t

dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
dwmapi.DwmSetWindowAttribute.argtypes = [c_void_p, DWORD, c_void_p, DWORD]
dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = DWORD
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
gdi32.CreateRoundRectRgn.restype = c_void_p
mmdevapi = ctypes.WinDLL("mmdevapi", use_last_error=True)

avrt = ctypes.WinDLL("avrt", use_last_error=True)
avrt.AvSetMmThreadCharacteristicsW.argtypes = [ctypes.c_wchar_p, POINTER(DWORD)]
avrt.AvSetMmThreadCharacteristicsW.restype = HANDLE
avrt.AvRevertMmThreadCharacteristics.argtypes = [HANDLE]
avrt.AvRevertMmThreadCharacteristics.restype = BOOL


class _Msg(Structure):
    _fields_ = [
        ("hwnd", c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", c_void_p),
        ("lParam", c_void_p),
        ("time", DWORD),
        ("x", ctypes.c_long),
        ("y", ctypes.c_long),
    ]


class _Rect(Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
