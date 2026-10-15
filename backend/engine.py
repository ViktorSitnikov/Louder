"""Усиление звука одного процесса: захват после микшера и воспроизведение громче."""

import array
import ctypes
import math
import subprocess
import sys
import threading
import time
from ctypes import (
    POINTER,
    Structure,
    Union,
    byref,
    c_ubyte,
    c_uint32,
    c_ulonglong,
    c_void_p,
    memmove,
    sizeof,
    string_at,
)
from ctypes.wintypes import BOOL, DWORD, HANDLE, WORD
from pathlib import Path

import comtypes
import psutil
from comtypes import COMMETHOD, COMObject, GUID, HRESULT, IUnknown
from pycaw.pycaw import AudioUtilities, IAudioClient, WAVEFORMATEX

from native import kernel32, mmdevapi

RATE = 48000
CHANNELS = 2
BLOCK = 8  # float32 stereo
MAX_GAIN = 6.0
# Windows отдаёт loopback уже после ползунка программы. Держим его почти
# на нуле и усиливаем копию. Ниже 1% движок начинает отдавать тишину.
ATTEN = 0.01
# ponytail: жёсткое ограничение на 1.0. Лимитер — если на 400%+ сильно хрипит.
# ponytail: один поток захвата на процесс. Хватает на несколько программ.

AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
VT_BLOB = 65
PROCESS_LOOPBACK = "VAD\\Process_Loopback"
INCLUDE_TREE = 0
ACTIVATION_PROCESS_LOOPBACK = 1

class IAudioCaptureClient(IUnknown):
    _iid_ = GUID("{C8ADBD64-E71E-48a0-A4DE-185C395CD317}")
    _methods_ = (
        COMMETHOD(
            [],
            HRESULT,
            "GetBuffer",
            (["out"], POINTER(POINTER(c_ubyte)), "ppData"),
            (["out"], POINTER(c_uint32), "pNumFramesToRead"),
            (["out"], POINTER(DWORD), "pdwFlags"),
            (["out"], POINTER(c_ulonglong), "pu64DevicePosition"),
            (["out"], POINTER(c_ulonglong), "pu64QPCPosition"),
        ),
        COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], c_uint32, "NumFramesRead")),
        COMMETHOD(
            [],
            HRESULT,
            "GetNextPacketSize",
            (["out"], POINTER(c_uint32), "pNumFramesInNextPacket"),
        ),
    )


class IAudioRenderClient(IUnknown):
    _iid_ = GUID("{F294ACFC-3146-4483-A7BF-ADDCA7C260E2}")
    _methods_ = (
        COMMETHOD(
            [],
            HRESULT,
            "GetBuffer",
            (["in"], c_uint32, "NumFramesRequested"),
            (["out"], POINTER(POINTER(c_ubyte)), "ppData"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "ReleaseBuffer",
            (["in"], c_uint32, "NumFramesWritten"),
            (["in"], DWORD, "dwFlags"),
        ),
    )


class IActivateAudioInterfaceAsyncOperation(IUnknown):
    _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
    _methods_ = (
        COMMETHOD(
            [],
            HRESULT,
            "GetActivateResult",
            (["out"], POINTER(ctypes.c_long), "activateResult"),
            (["out"], POINTER(POINTER(IUnknown)), "activatedInterface"),
        ),
    )


class IActivateAudioInterfaceCompletionHandler(IUnknown):
    _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
    _methods_ = (
        COMMETHOD(
            [],
            HRESULT,
            "ActivateCompleted",
            (["in"], POINTER(IActivateAudioInterfaceAsyncOperation), "activateOperation"),
        ),
    )


class IAgileObject(IUnknown):
    """Маркер: колбэк можно звать с потока MTA, иначе Windows отвечает 0x8000000E."""

    _iid_ = GUID("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}")
    _methods_ = ()


mmdevapi.ActivateAudioInterfaceAsync.argtypes = [
    ctypes.c_wchar_p,
    POINTER(GUID),
    c_void_p,
    POINTER(IActivateAudioInterfaceCompletionHandler),
    POINTER(POINTER(IActivateAudioInterfaceAsyncOperation)),
]
mmdevapi.ActivateAudioInterfaceAsync.restype = HRESULT


class _ActivateHandler(COMObject):
    _com_interfaces_ = [IActivateAudioInterfaceCompletionHandler, IAgileObject]

    def __init__(self):
        super().__init__()
        self.done = threading.Event()
        self.client = None
        self.error = None

    def ActivateCompleted(self, operation):
        try:
            hr, punk = operation.GetActivateResult()
            code = int(hr) & 0xFFFFFFFF
            if code >= 0x80000000:
                self.error = f"код 0x{code:08X}"
            else:
                self.client = punk.QueryInterface(IAudioClient)
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.done.set()


class _Blob(Structure):
    _fields_ = [("cbSize", DWORD), ("pBlobData", c_void_p)]


class _PropVariant(Structure):
    class _U(Union):
        _fields_ = [("blob", _Blob)]

    _fields_ = [
        ("vt", WORD),
        ("r1", WORD),
        ("r2", WORD),
        ("r3", WORD),
        ("u", _U),
    ]


class _Activation(Structure):
    # ActivationType, TargetProcessId, ProcessLoopbackMode — как в audioclientactivationparams.h
    _fields_ = [("ActivationType", DWORD), ("TargetProcessId", DWORD), ("ProcessLoopbackMode", DWORD)]


def scale(data: bytes, gain: float) -> bytes:
    if gain == 1.0 or not data:
        return data
    samples = array.array("f")
    samples.frombytes(data[: len(data) - (len(data) % 4)])
    for i, sample in enumerate(samples):
        value = sample * gain
        if value > 1.0:
            value = 1.0
        elif value < -1.0:
            value = -1.0
        samples[i] = value
    return samples.tobytes()


def pcm_peak(data: bytes) -> float:
    samples = array.array("f")
    samples.frombytes(data[: len(data) - (len(data) % 4)])
    return max((abs(sample) for sample in samples), default=0.0)


def _pcm_format() -> WAVEFORMATEX:
    fmt = WAVEFORMATEX()
    fmt.wFormatTag = 3  # IEEE float
    fmt.nChannels = CHANNELS
    fmt.nSamplesPerSec = RATE
    fmt.wBitsPerSample = 32
    fmt.nBlockAlign = BLOCK
    fmt.nAvgBytesPerSec = RATE * BLOCK
    fmt.cbSize = 0
    return fmt


def _addr(ptr) -> int:
    if not ptr:
        return 0
    if isinstance(ptr, int):
        return ptr
    value = getattr(ptr, "value", None)
    if isinstance(value, int):
        return value
    return int(ctypes.cast(ptr, c_void_p).value or 0)


def _open_process_client(pid: int) -> IAudioClient:
    if sizeof(_PropVariant) != 24 or _Blob.pBlobData.offset != 8:
        raise RuntimeError("неверная раскладка PROPVARIANT")
    params = _Activation()
    params.ActivationType = ACTIVATION_PROCESS_LOOPBACK
    params.TargetProcessId = pid
    params.ProcessLoopbackMode = INCLUDE_TREE
    prop = _PropVariant()
    prop.vt = VT_BLOB
    prop.u.blob.cbSize = sizeof(params)
    prop.u.blob.pBlobData = ctypes.addressof(params)
    handler = _ActivateHandler()
    op = POINTER(IActivateAudioInterfaceAsyncOperation)()
    mmdevapi.ActivateAudioInterfaceAsync(
        PROCESS_LOOPBACK,
        byref(IAudioClient._iid_),
        byref(prop),
        handler,
        byref(op),
    )
    if not handler.done.wait(4):
        raise RuntimeError("аудиосистема не ответила")
    if handler.error:
        raise RuntimeError(handler.error)
    if handler.client is None:
        raise RuntimeError("пустой аудиоклиент")
    # params и handler должны жить до возврата из ActivateAudioInterfaceAsync и колбэка
    handler.client._keep = (params, prop, handler, op)
    return handler.client


class Capture:
    def __init__(self, pid: int):
        self.event = kernel32.CreateEventW(None, False, False, None)
        self.client = _open_process_client(pid)
        self.fmt = _pcm_format()
        flags = (
            AUDCLNT_STREAMFLAGS_LOOPBACK
            | AUDCLNT_STREAMFLAGS_EVENTCALLBACK
            | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM
            | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY
        )
        self.client.Initialize(0, flags, 200000, 0, self.fmt, None)
        self.client.SetEventHandle(self.event)
        self.capture = self.client.GetService(IAudioCaptureClient._iid_).QueryInterface(
            IAudioCaptureClient
        )
        self.client.Start()

    def read(self) -> bytes:
        chunks = []
        while True:
            frames = int(self.capture.GetNextPacketSize() or 0)
            if frames <= 0:
                break
            ptr, got, flags, _pos, _qpc = self.capture.GetBuffer()
            got = int(got)
            try:
                if not (int(flags) & AUDCLNT_BUFFERFLAGS_SILENT):
                    chunks.append(string_at(_addr(ptr), got * BLOCK))
            finally:
                self.capture.ReleaseBuffer(got)
        return b"".join(chunks)

    def close(self):
        if self.client is not None:
            try:
                self.client.Stop()
            except Exception:
                pass
            self.client = None
            self.capture = None
        if self.event:
            kernel32.CloseHandle(self.event)
            self.event = None


class Render:
    def __init__(self):
        speakers = AudioUtilities.GetSpeakers()
        if speakers is None:
            raise RuntimeError("нет устройства вывода")
        raw = speakers._dev.Activate(IAudioClient._iid_, comtypes.CLSCTX_ALL, None)
        self.client = raw.QueryInterface(IAudioClient)
        self.fmt = _pcm_format()
        flags = AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY
        self.client.Initialize(0, flags, 200000, 0, self.fmt, None)
        self.buffer_frames = int(self.client.GetBufferSize())
        self.render = self.client.GetService(IAudioRenderClient._iid_).QueryInterface(
            IAudioRenderClient
        )
        self.pending = bytearray()
        self.client.Start()

    def write(self, data: bytes):
        self.pending.extend(data)
        # ponytail: если поток не успевает, старое выбрасывается — задержка не копится.
        cap = RATE * BLOCK * 200 // 1000
        keep = RATE * BLOCK * 60 // 1000
        if len(self.pending) > cap:
            drop = len(self.pending) - keep
            del self.pending[: drop - (drop % BLOCK)]
        avail = self.buffer_frames - int(self.client.GetCurrentPadding())
        have = len(self.pending) // BLOCK
        frames = min(avail, have)
        if frames <= 0:
            return
        nbytes = frames * BLOCK
        chunk = bytes(self.pending[:nbytes])
        del self.pending[:nbytes]
        ptr = self.render.GetBuffer(frames)
        memmove(_addr(ptr), chunk, nbytes)
        self.render.ReleaseBuffer(frames, 0)

    def close(self):
        if self.client is not None:
            try:
                self.client.Stop()
            except Exception:
                pass
            self.client = None
            self.render = None


class Booster:
    def __init__(self, pid: int, gain: float):
        self.pid = pid
        self.gain = gain
        self.error = None
        self.ready = threading.Event()
        self.t0 = 0.0
        self._stop = threading.Event()
        self.stop_event = kernel32.CreateEventW(None, True, False, None)
        self.thread = threading.Thread(target=self._run, name=f"boost-{pid}", daemon=True)

    def start(self):
        self.thread.start()
        self.ready.wait(5)
        if self.error:
            self.stop()
            raise RuntimeError(self.error)
        if not self.thread.is_alive():
            self.stop()
            raise RuntimeError(self.error or "усиление остановилось")

    def stop(self):
        self._stop.set()
        if self.stop_event:
            kernel32.SetEvent(self.stop_event)
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=2)
        if self.stop_event:
            kernel32.CloseHandle(self.stop_event)
            self.stop_event = None

    def _run(self):
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        cap = ren = None
        try:
            cap = Capture(self.pid)
            ren = Render()
            self.t0 = time.time()
            self.ready.set()
            handles = (HANDLE * 2)(self.stop_event, cap.event)
            while not self._stop.is_set():
                rc = kernel32.WaitForMultipleObjects(2, handles, False, 200)
                if rc == 0:
                    break
                data = cap.read()
                if data:
                    ren.write(scale(data, self._factor()))
        except Exception as exc:
            self.error = str(exc)
        finally:
            if ren is not None:
                ren.close()
            if cap is not None:
                cap.close()
            self.ready.set()
            comtypes.CoUninitialize()

    def _factor(self) -> float:
        factor = self.gain / ATTEN
        # Громкость в микшере меняется не мгновенно — иначе первый момент орёт.
        warm = time.time() - self.t0
        if warm < 0.15:
            factor *= warm / 0.15
        return factor


boosters: dict[int, Booster] = {}


def root_pids(pids) -> list[int]:
    """Корень дерева процессов: захват родителя уже включает детей."""
    alive = {pid for pid in pids if pid}
    roots = []
    for pid in alive:
        try:
            parents = {parent.pid for parent in psutil.Process(pid).parents()}
        except Exception:
            parents = set()
        if parents.isdisjoint(alive):
            roots.append(pid)
    return roots


def ensure_boost(pid: int, gain: float):
    gain = max(0.0, min(MAX_GAIN, gain))
    current = boosters.get(pid)
    if current and current.thread.is_alive() and not current.error:
        current.gain = gain
        return
    if current:
        stop_boost(pid)
    booster = Booster(pid, gain)
    boosters[pid] = booster
    try:
        booster.start()
    except Exception:
        boosters.pop(pid, None)
        raise


def stop_boost(pid: int):
    booster = boosters.pop(pid, None)
    if booster:
        booster.stop()


def capture_peak(pid: int, seconds: float) -> float:
    box: dict = {}

    def work():
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
            cap = Capture(pid)
            try:
                peak = 0.0
                deadline = time.time() + seconds
                while time.time() < deadline:
                    kernel32.WaitForSingleObject(cap.event, 50)
                    data = cap.read()
                    if data:
                        peak = max(peak, pcm_peak(data))
                box["peak"] = peak
            finally:
                cap.close()
                comtypes.CoUninitialize()
        except Exception as exc:
            box["error"] = exc

    thread = threading.Thread(target=work)
    thread.start()
    thread.join(seconds + 6)
    if "error" in box:
        raise box["error"]
    if thread.is_alive():
        raise RuntimeError("захват не завершился")
    return box.get("peak", 0.0)


def play_tone():
    ren = Render()
    try:
        step = RATE // 100
        left = RATE * 3
        phase = 0.0
        delta = 2 * math.pi * 440 / RATE
        while left > 0:
            count = step if left > step else left
            samples = array.array("f")
            for _ in range(count):
                value = 0.25 * math.sin(phase)
                phase += delta
                samples.append(value)
                samples.append(value)
            ren.write(samples.tobytes())
            left -= count
            time.sleep(0.008)
        time.sleep(0.3)
    finally:
        ren.close()


def _set_session_volume(pid: int, level: float):
    for session in AudioUtilities.GetAllSessions():
        if session.ProcessId == pid:
            session.SimpleAudioVolume.SetMute(0, None)
            session.SimpleAudioVolume.SetMasterVolume(level, None)


def demo():
    loud = array.array("f", [0.8, -0.8]).tobytes()
    got = array.array("f")
    got.frombytes(scale(loud, 2))
    assert math.isclose(got[0], 1.0) and math.isclose(got[1], -1.0), list(got)
    quiet = array.array("f", [0.1, -0.1]).tobytes()
    got = array.array("f")
    got.frombytes(scale(quiet, 6))
    assert math.isclose(got[0], 0.6, rel_tol=1e-5) and math.isclose(got[1], -0.6, rel_tol=1e-5), list(got)

    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve().parent / "volume.py"), "--tone"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        time.sleep(0.6)
        peak_full = capture_peak(proc.pid, 0.35)
        _set_session_volume(proc.pid, 0.25)
        time.sleep(0.2)
        peak_low = capture_peak(proc.pid, 0.35)
        print(f"peak full={peak_full:.4f} quarter={peak_low:.4f}")
        if peak_full < 0.05:
            raise SystemExit(f"захват пустой: {peak_full}")
        ratio = peak_low / peak_full
        if not 0.1 < ratio < 0.5:
            raise SystemExit(f"громкость сессии не попала в захват: {ratio:.3f}")
    finally:
        try:
            _set_session_volume(proc.pid, 1.0)
        except Exception:
            pass
        proc.kill()
        proc.wait(timeout=3)
    print("ok")
