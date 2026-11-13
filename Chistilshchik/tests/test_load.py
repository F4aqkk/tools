"""Проверка монитора нагрузки на придуманных замерах.

Запуск из папки Chistilshchik:
    .venv\\Scripts\\python -m unittest discover tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chistilshchik import load as ld  # noqa: E402

TH = ld.Thresholds()  # настройки по умолчанию из ТЗ


def sample(cpu=5, gpu=0, hot_threads=0, threads=16):
    """Замер: средняя загрузка cpu, видеокарта gpu, hot_threads потоков на 100%."""
    cores = [100.0] * hot_threads + [0.0] * (threads - hot_threads)
    return ld.Sample(0, cpu, cores, gpu)


class ReasonsTest(unittest.TestCase):
    def test_calm(self):
        self.assertEqual(ld.overload_reasons(sample(), TH), [])

    def test_gpu_over_50(self):
        self.assertEqual(ld.overload_reasons(sample(gpu=50), TH), [])
        self.assertEqual(ld.overload_reasons(sample(gpu=51), TH), ["видеокарта 51%"])

    def test_cpu_from_30(self):
        self.assertEqual(ld.overload_reasons(sample(cpu=29), TH), [])
        self.assertEqual(ld.overload_reasons(sample(cpu=30), TH), ["процессор 30%"])

    def test_two_hot_cores(self):
        self.assertEqual(ld.overload_reasons(sample(hot_threads=1), TH), [])
        self.assertEqual(ld.overload_reasons(sample(hot_threads=2), TH), ["2 ядра почти на 100%"])
        self.assertEqual(ld.overload_reasons(sample(hot_threads=5), TH), ["5 ядер почти на 100%"])

    def test_no_gpu_data_ignored(self):
        self.assertEqual(ld.overload_reasons(sample(gpu=None), TH), [])

    def test_physical_cores(self):
        th = ld.Thresholds(count_logical_cores=False)
        # Два потока одного ядра на 100% -> это одно физическое ядро
        s = ld.Sample(0, 10, [100, 100] + [0] * 14, 0)
        self.assertEqual(ld.busy_cores(s, th, physical=8), 1)
        self.assertEqual(ld.busy_cores(s, TH, physical=8), 2)
        # Потоки разных ядер -> два ядра
        s = ld.Sample(0, 10, [100, 0, 100] + [0] * 13, 0)
        self.assertEqual(ld.busy_cores(s, th, physical=8), 2)

    def test_cores_word(self):
        words = {n: ld.cores_word(n) for n in (1, 2, 4, 5, 11, 12, 16, 21, 22)}
        self.assertEqual(words, {1: "ядро", 2: "ядра", 4: "ядра", 5: "ядер", 11: "ядер",
                                 12: "ядер", 16: "ядер", 21: "ядро", 22: "ядра"})


class MonitorTest(unittest.TestCase):
    def run_seconds(self, mon, start, seconds, busy):
        """Подаёт замеры раз в секунду. Возвращает секунду, когда поменялось состояние."""
        changed_at = None
        for t in range(start, start + seconds):
            reasons = ["процессор 80%"] if busy else []
            if mon.feed(t, reasons, 10, 30) and changed_at is None:
                changed_at = t
        return changed_at

    def test_pause_after_about_10_seconds(self):
        mon = ld.LoadMonitor()
        self.run_seconds(mon, 0, 40, busy=False)
        at = self.run_seconds(mon, 40, 20, busy=True)
        self.assertTrue(mon.busy)
        self.assertTrue(47 <= at <= 50, at)  # около 10 секунд нагрузки
        self.assertEqual(mon.reasons, ["процессор 80%"])

    def test_short_spike_does_not_pause(self):
        mon = ld.LoadMonitor()
        self.run_seconds(mon, 0, 40, busy=False)
        self.assertIsNone(self.run_seconds(mon, 40, 4, busy=True))
        self.assertIsNone(self.run_seconds(mon, 44, 30, busy=False))
        self.assertFalse(mon.busy)

    def test_resume_after_about_30_seconds(self):
        mon = ld.LoadMonitor()
        self.run_seconds(mon, 0, 30, busy=True)
        self.assertTrue(mon.busy)
        at = self.run_seconds(mon, 30, 40, busy=False)
        self.assertFalse(mon.busy)
        self.assertTrue(55 <= at <= 60, at)  # около 30 секунд покоя
        self.assertEqual(mon.reasons, [])

    def test_game_with_dips_stays_paused(self):
        """В игре нагрузка иногда проседает на секунду — паузу это не снимает."""
        mon = ld.LoadMonitor()
        for t in range(0, 300):
            busy = t % 7 != 0  # каждая 7-я секунда «спокойная»
            mon.feed(t, ["видеокарта 90%"] if busy else [], 10, 30)
            if t > 15:
                self.assertTrue(mon.busy, t)

    def test_history_is_trimmed(self):
        mon = ld.LoadMonitor()
        self.run_seconds(mon, 0, 1000, busy=False)
        self.assertLess(len(mon.history), 40)


if __name__ == "__main__":
    unittest.main()
