"""Список дисков и их свойства (ТЗ, раздел 3.3).

На Windows всё читаем напрямую через системные функции (WinAPI):
буквы дисков, тип (обычный / флешка / сетевой / CD), название,
серийный номер тома и SSD это или HDD. Права администратора не нужны.
На других системах есть простой запасной вариант через psutil — он нужен
только для проверки кода.
"""
import string
import sys
from dataclasses import dataclass

import psutil

# Виды дисков
FIXED = "fixed"          # обычный диск (SSD/HDD, в том числе внешний USB-HDD)
REMOVABLE = "removable"  # флешка, карта памяти
NETWORK = "network"      # сетевой диск
CDROM = "cdrom"          # CD/DVD
OTHER = "other"

KIND_UNKNOWN = "неизвестно"

DRIVE_TYPE_NAMES = {
    FIXED: "диск",
    REMOVABLE: "флешка",
    NETWORK: "сетевой",
    CDROM: "CD/DVD",
    OTHER: "другое",
}


@dataclass
class DiskInfo:
    volume_id: str        # постоянный номер тома: по нему узнаём «знакомый» диск
    letter: str           # "D:"
    label: str            # название диска
    fs_type: str          # NTFS, exFAT...
    drive_type: str       # fixed / removable / network / cdrom / other
    total_bytes: int
    free_bytes: int
    kind: str = ""        # SSD / HDD / неизвестно

    @property
    def free_pct(self):
        return self.free_bytes / self.total_bytes * 100 if self.total_bytes else 100.0

    @property
    def title(self):
        return f"{self.letter} «{self.label}»" if self.label else self.letter


# ---------------------------------------------------------------- Windows
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _k32.GetLogicalDrives.restype = wintypes.DWORD
    _k32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    _k32.GetDriveTypeW.restype = wintypes.UINT
    _k32.GetVolumeInformationW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD,
    ]
    _k32.GetVolumeInformationW.restype = wintypes.BOOL
    _k32.GetVolumeNameForVolumeMountPointW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
    ]
    _k32.GetVolumeNameForVolumeMountPointW.restype = wintypes.BOOL
    _k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.DeviceIoControl.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    _k32.DeviceIoControl.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.SetErrorMode.argtypes = [wintypes.UINT]
    _k32.SetErrorMode.restype = wintypes.UINT

    # Не показывать окна Windows «Вставьте диск в устройство» при опросе
    # пустого кардридера или дисковода.
    _k32.SetErrorMode(0x0001 | 0x8000)

    _WIN_TYPES = {2: REMOVABLE, 3: FIXED, 4: NETWORK, 5: CDROM}
    _INVALID_HANDLE = wintypes.HANDLE(-1).value

    class _PropertyQuery(ctypes.Structure):
        _fields_ = [
            ("PropertyId", wintypes.DWORD),
            ("QueryType", wintypes.DWORD),
            ("Extra", ctypes.c_ubyte * 1),
        ]

    class _SeekPenalty(ctypes.Structure):
        _fields_ = [
            ("Version", wintypes.DWORD),
            ("Size", wintypes.DWORD),
            ("IncursSeekPenalty", ctypes.c_ubyte),
        ]

    def _volume_info(root):
        name = ctypes.create_unicode_buffer(261)
        fs = ctypes.create_unicode_buffer(261)
        serial = wintypes.DWORD()
        maxlen = wintypes.DWORD()
        flags = wintypes.DWORD()
        ok = _k32.GetVolumeInformationW(
            root, name, 261, ctypes.byref(serial), ctypes.byref(maxlen),
            ctypes.byref(flags), fs, 261,
        )
        if not ok:
            return None  # нет носителя (пустой кардридер, нет диска в DVD)
        return name.value, fs.value, serial.value

    def _volume_guid(root):
        buf = ctypes.create_unicode_buffer(64)
        if _k32.GetVolumeNameForVolumeMountPointW(root, buf, 64):
            return buf.value  # \\?\Volume{....}\
        return ""

    def _list_windows(include):
        disks = []
        mask = _k32.GetLogicalDrives()
        for i, ch in enumerate(string.ascii_uppercase):
            if not mask & (1 << i):
                continue
            root = f"{ch}:\\"
            dtype = _WIN_TYPES.get(_k32.GetDriveTypeW(root), OTHER)
            if dtype not in include:
                continue  # даже не трогаем: сетевой диск может «думать» секундами
            info = _volume_info(root)
            if info is None:
                continue
            label, fs, serial = info
            try:
                usage = psutil.disk_usage(root)
            except OSError:
                continue
            guid = _volume_guid(root)
            vid = guid or f"{dtype}:{serial:08X}"
            disks.append(DiskInfo(vid, f"{ch}:", label, fs, dtype, usage.total, usage.free))
        return disks

    def detect_kind(letter):
        """SSD или HDD: спрашиваем у Windows, есть ли у диска «время на
        поиск головки» (seek penalty). У HDD есть, у SSD нет."""
        h = _k32.CreateFileW(f"\\\\.\\{letter}", 0, 3, None, 3, 0, None)
        if not h or h == _INVALID_HANDLE:
            return KIND_UNKNOWN
        try:
            q = _PropertyQuery(7, 0)  # StorageDeviceSeekPenaltyProperty, стандартный запрос
            d = _SeekPenalty()
            got = wintypes.DWORD()
            ok = _k32.DeviceIoControl(
                h, 0x2D1400, ctypes.byref(q), ctypes.sizeof(q),
                ctypes.byref(d), ctypes.sizeof(d), ctypes.byref(got), None,
            )
            if not ok or got.value < ctypes.sizeof(d):
                return KIND_UNKNOWN
            return "HDD" if d.IncursSeekPenalty else "SSD"
        finally:
            _k32.CloseHandle(h)


# ------------------------------------------------- запасной вариант (не Windows)
def _list_fallback(include):
    disks = []
    for part in psutil.disk_partitions(all=False):
        dtype = CDROM if "cdrom" in part.opts else (REMOVABLE if "removable" in part.opts else FIXED)
        if dtype not in include:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        disks.append(DiskInfo(part.device, part.mountpoint, "", part.fstype, dtype, usage.total, usage.free))
    return disks


def list_disks(include=(FIXED,)):
    """Подключённые диски нужных видов."""
    include = set(include)
    if sys.platform == "win32":
        return _list_windows(include)
    return _list_fallback(include)


if sys.platform != "win32":
    def detect_kind(letter):
        return KIND_UNKNOWN
