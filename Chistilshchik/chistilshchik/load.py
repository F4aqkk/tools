"""Монитор нагрузки (ТЗ, раздел 3.2).

ПК считается занятым, если выполняется ЛЮБОЕ условие:
  - видеокарта загружена больше чем на 50%;
  - процессор в среднем по всем ядрам загружен от 30%;
  - хотя бы 1 ядро почти на 100% (по умолчанию «почти» = от 95%).

Чтобы программа не дёргалась:
  - пауза включается, когда нагрузка была ~80% времени за последние 10 секунд;
  - проверка продолжается, когда ПК был спокоен ~90% времени за последние 30 секунд.
Все числа меняются в настройках.
"""
from collections import deque
from dataclasses import dataclass, field

import psutil

PAUSE_SHARE = 0.8    # какая доля «нагруженных» замеров нужна для паузы
RESUME_SHARE = 0.9   # какая доля «спокойных» замеров нужна, чтобы продолжить


@dataclass
class Sample:
    t: float                  # время замера (секунды)
    cpu_avg: float            # средняя загрузка процессора, %
    cores: list               # загрузка каждого потока, %
    gpu: float = None         # загрузка видеокарты, % (None — нет данных)


@dataclass
class Thresholds:
    gpu_load_pct: float = 50
    cpu_avg_pct: float = 30
    busy_cores_count: int = 1
    busy_core_pct: float = 95
    count_logical_cores: bool = True
    pause_delay_sec: float = 10
    resume_delay_sec: float = 30

    @classmethod
    def from_db(cls, db):
        return cls(
            gpu_load_pct=db.get_int("gpu_load_pct"),
            cpu_avg_pct=db.get_int("cpu_avg_pct"),
            busy_cores_count=max(1, db.get_int("busy_cores_count")),
            busy_core_pct=db.get_int("busy_core_pct"),
            count_logical_cores=bool(db.get_int("count_logical_cores")),
            pause_delay_sec=max(1, db.get_int("pause_delay_sec")),
            resume_delay_sec=max(1, db.get_int("resume_delay_sec")),
        )


def cores_word(n):
    """1 ядро, 2 ядра, 5 ядер."""
    if n % 10 == 1 and n % 100 != 11:
        return "ядро"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "ядра"
    return "ядер"


def group_physical(values, physical):
    """Потоки -> физические ядра. Windows нумерует потоки одного ядра
    подряд (0 и 1 — ядро 0, 2 и 3 — ядро 1...). Ядро считаем загруженным
    так же, как самый загруженный из его потоков."""
    if not physical or physical >= len(values):
        return list(values)
    per = len(values) // physical
    return [max(values[i * per:(i + 1) * per]) for i in range(physical)]


def busy_cores(sample, th, physical=None):
    cores = sample.cores if th.count_logical_cores else group_physical(sample.cores, physical)
    return sum(1 for v in cores if v >= th.busy_core_pct)


def overload_reasons(sample, th, physical=None):
    """Почему ПК сейчас занят. Пустой список — ПК спокоен."""
    reasons = []
    if sample.gpu is not None and sample.gpu > th.gpu_load_pct:
        reasons.append(f"видеокарта {sample.gpu:.0f}%")
    if sample.cpu_avg >= th.cpu_avg_pct:
        reasons.append(f"процессор {sample.cpu_avg:.0f}%")
    n = busy_cores(sample, th, physical)
    if n >= th.busy_cores_count:
        reasons.append(f"{n} {cores_word(n)} почти на 100%")
    return reasons


@dataclass
class _Point:
    t: float
    busy: bool
    reasons: list = field(default_factory=list)


class LoadMonitor:
    """Решает, занят ли ПК, с учётом задержек. Без окна и потоков — легко проверять."""

    def __init__(self):
        self.history = deque()
        self.busy = False
        self.reasons = []      # почему занят (последние причины)

    def _window(self, now, seconds):
        """Замеры за последние seconds секунд, если наблюдаем уже достаточно долго."""
        if not self.history or now - self.history[0].t < seconds - 1:
            return None
        return [p for p in self.history if p.t > now - seconds]

    def feed(self, t, reasons, pause_delay, resume_delay):
        """Добавить замер. Возвращает True, если состояние поменялось."""
        self.history.append(_Point(t, bool(reasons), list(reasons)))
        keep = max(pause_delay, resume_delay) + 2
        while self.history and self.history[0].t < t - keep:
            self.history.popleft()

        if reasons:
            self.reasons = list(reasons)

        if not self.busy:
            win = self._window(t, pause_delay)
            if win and sum(p.busy for p in win) >= PAUSE_SHARE * len(win):
                self.busy = True
                return True
        else:
            win = self._window(t, resume_delay)
            if win and sum(not p.busy for p in win) >= RESUME_SHARE * len(win):
                self.busy = False
                self.reasons = []
                return True
        return False


class Sampler:
    """Берёт замеры: процессор и ядра через psutil, видеокарту через NVIDIA (NVML)."""

    def __init__(self):
        psutil.cpu_percent(percpu=True)  # первый вызов всегда 0 — «прогреваем»
        self.physical = psutil.cpu_count(logical=False) or None
        self.logical = psutil.cpu_count(logical=True) or 0
        self.gpu_names = []
        self.gpu_error = None
        self._nvml = None
        self._handles = []
        try:
            import pynvml
            pynvml.nvmlInit()
            for i in range(pynvml.nvmlDeviceGetCount()):
                h = pynvml.nvmlDeviceGetHandleByIndex(i)
                name = pynvml.nvmlDeviceGetName(h)
                self.gpu_names.append(name.decode() if isinstance(name, bytes) else name)
                self._handles.append(h)
            self._nvml = pynvml
            if not self._handles:
                self.gpu_error = "видеокарта NVIDIA не найдена"
        except Exception as e:  # нет драйвера NVIDIA или нет видеокарты
            self.gpu_error = str(e) or e.__class__.__name__

    @property
    def gpu_available(self):
        return bool(self._handles)

    def sample(self, t):
        cores = psutil.cpu_percent(percpu=True)
        avg = sum(cores) / len(cores) if cores else 0.0
        gpu = None
        if self._handles:
            try:
                gpu = max(self._nvml.nvmlDeviceGetUtilizationRates(h).gpu for h in self._handles)
            except Exception:
                gpu = None
        return Sample(t, avg, cores, gpu)

    def close(self):
        if self._nvml:
            try:
                self._nvml.nvmlShutdown()
            except Exception:
                pass
            self._nvml = None
            self._handles = []
