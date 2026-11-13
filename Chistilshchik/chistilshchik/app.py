"""Запуск программы: значок в трее, окно, слежение за дисками и нагрузкой.

Опрос дисков и нагрузки идёт в отдельном фоновом потоке, чтобы окно
не подвисало, если какой-то диск отвечает медленно (например, спящий HDD).
"""
import logging
import sys
import time
import traceback

from PyQt6.QtCore import (
    QLockFile, QMetaObject, QObject, Qt, QThread, QTimer, pyqtSignal, pyqtSlot,
)
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QHeaderView, QLabel, QMainWindow, QMenu, QMessageBox,
    QSystemTrayIcon, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import APP_NAME, __version__, config
from . import load as ld
from . import tracker as tr
from .db import Database
from .disks import DRIVE_TYPE_NAMES
from .utils import human_size

# Цвета значка в трее (раздел 6 ТЗ)
STATE_COLORS = {
    "watching": "#3fa34d",   # следит, места хватает
    "scanning": "#2f7fd8",   # места мало, ПК свободен -> проверка
    "paused": "#e0a020",     # места мало, но ПК нагружен -> пауза
}
STATE_TEXT = {
    "watching": "Слежу за дисками, места хватает",
    "scanning": "Места мало, ПК свободен: можно искать мусор",
    "paused": "Места мало, но ПК нагружен: жду, пока освободится",
}
LOW_COLOR = QColor("#c0392b")
LOAD_INTERVAL_MS = 1000


def make_icon(state):
    """Рисуем цветной кружок, чтобы не таскать с собой картинки."""
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(STATE_COLORS[state]))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(6, 6, 52, 52)
    p.end()
    return QIcon(pix)


class BackgroundWorker(QObject):
    """Живёт в отдельном потоке: раз в несколько секунд опрашивает диски,
    раз в секунду — нагрузку."""

    disks_updated = pyqtSignal(list)              # список DiskInfo
    disk_event = pyqtSignal(str, object, bool)    # вид, DiskInfo, новый ли
    load_updated = pyqtSignal(object, int)        # Sample, сколько ядер «на 100%»
    busy_changed = pyqtSignal(bool, list)         # занят ли ПК, причины

    def __init__(self, db):
        super().__init__()
        self.db = db
        self.tracker = None
        self.sampler = None
        self.monitor = ld.LoadMonitor()
        self.disk_timer = None
        self.load_timer = None

    @pyqtSlot()
    def start(self):
        self.tracker = tr.DiskTracker(self.db)
        self.sampler = ld.Sampler()
        if self.sampler.gpu_available:
            self.db.log("Видеокарта найдена", ", ".join(self.sampler.gpu_names))
        else:
            self.db.log(
                "Видеокарта NVIDIA недоступна",
                f"Условие про видеокарту отключено ({self.sampler.gpu_error})",
                level="warning",
            )

        self.disk_timer = QTimer(self)
        self.disk_timer.timeout.connect(self.tick_disks)
        self.tick_disks()
        self.disk_timer.start(self.db.get_int("disk_poll_sec") * 1000)

        self.load_timer = QTimer(self)
        self.load_timer.timeout.connect(self.tick_load)
        self.load_timer.start(LOAD_INTERVAL_MS)

    @pyqtSlot()
    def stop(self):
        for timer in (self.disk_timer, self.load_timer):
            if timer:
                timer.stop()
        if self.sampler:
            self.sampler.close()

    @pyqtSlot()
    def tick_disks(self):
        try:
            events = self.tracker.poll()
        except Exception:
            logging.exception("Ошибка при опросе дисков")
            return
        for kind, disk, is_new in events:
            self.disk_event.emit(kind, disk, is_new)
        self.disks_updated.emit(self.tracker.connected())
        # Интервал мог поменяться в настройках
        interval = max(1, self.db.get_int("disk_poll_sec")) * 1000
        if self.disk_timer.interval() != interval:
            self.disk_timer.setInterval(interval)

    @pyqtSlot()
    def tick_load(self):
        try:
            th = ld.Thresholds.from_db(self.db)
            sample = self.sampler.sample(time.monotonic())
            reasons = ld.overload_reasons(sample, th, self.sampler.physical)
            changed = self.monitor.feed(sample.t, reasons, th.pause_delay_sec, th.resume_delay_sec)
        except Exception:
            logging.exception("Ошибка при замере нагрузки")
            return
        self.load_updated.emit(sample, ld.busy_cores(sample, th, self.sampler.physical))
        if changed:
            if self.monitor.busy:
                self.db.log("ПК нагружен", ", ".join(self.monitor.reasons))
            else:
                self.db.log("ПК снова свободен")
            self.busy_changed.emit(self.monitor.busy, list(self.monitor.reasons))


class MainWindow(QMainWindow):
    COLUMNS = ["Диск", "Название", "Вид", "Тип", "Всего", "Свободно", "Свободно, %"]

    def __init__(self, db):
        super().__init__()
        self.db = db
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(720, 380)

        self.status = QLabel()
        self.status.setStyleSheet("font-weight: bold;")
        self.load_label = QLabel("Нагрузка: замеряю...")
        self.busy_label = QLabel()
        self.low_label = QLabel()
        self.low_label.setStyleSheet("color: #c0392b;")
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        box = QWidget()
        lay = QVBoxLayout(box)
        lay.addWidget(self.status)
        lay.addWidget(self.load_label)
        lay.addWidget(self.busy_label)
        lay.addWidget(self.low_label)
        lay.addWidget(self.table)
        self.setCentralWidget(box)
        self.set_state("watching")
        self.set_busy(False, [])

    def set_state(self, state):
        self.status.setText(f"Состояние: {STATE_TEXT[state]}")

    def set_busy(self, busy, reasons):
        if busy:
            self.busy_label.setText(f"ПК занят: {', '.join(reasons)}")
            self.busy_label.setStyleSheet("color: #b9770e;")
        else:
            self.busy_label.setText("ПК свободен")
            self.busy_label.setStyleSheet("color: #3fa34d;")

    def show_load(self, sample, busy_cores):
        gpu = "нет данных" if sample.gpu is None else f"{sample.gpu:.0f}%"
        self.load_label.setText(
            f"Процессор: {sample.cpu_avg:.0f}%   ·   "
            f"ядер почти на 100%: {busy_cores}   ·   "
            f"видеокарта: {gpu}"
        )

    def show_disks(self, disks):
        threshold = self.db.get_int("free_threshold_pct")
        self.table.setRowCount(len(disks))
        low = []
        for r, d in enumerate(disks):
            is_low = d.free_pct < threshold
            if is_low:
                low.append(d.letter)
            cells = [
                d.letter, d.label, DRIVE_TYPE_NAMES.get(d.drive_type, d.drive_type),
                d.kind, human_size(d.total_bytes), human_size(d.free_bytes),
                f"{d.free_pct:.0f}%",
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if is_low:
                    item.setForeground(LOW_COLOR)
                self.table.setItem(r, c, item)
        if low:
            self.low_label.setText(f"Мало места (меньше {threshold}%): {', '.join(low)}")
        else:
            self.low_label.setText("")

    def closeEvent(self, event):
        # Крестик не закрывает программу, а прячет окно в трей
        event.ignore()
        self.hide()


class App:
    def __init__(self, qt_app, db):
        self.qt_app = qt_app
        self.db = db
        self.window = MainWindow(db)
        self.low_disks = set()   # volume_id дисков, где мало места
        self.busy = False
        self.state = None

        self.tray = QSystemTrayIcon(make_icon("watching"))
        menu = QMenu()
        act_open = QAction("Открыть", menu)
        act_open.triggered.connect(self.show_window)
        act_quit = QAction("Выход", menu)
        act_quit.triggered.connect(self.quit)
        menu.addAction(act_open)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.on_tray_click)
        self.update_state()
        self.tray.show()

        # Фоновый поток: диски + нагрузка
        self.bg_thread = QThread()
        self.worker = BackgroundWorker(db)
        self.worker.moveToThread(self.bg_thread)
        self.bg_thread.started.connect(self.worker.start)
        self.worker.disks_updated.connect(self.window.show_disks)
        self.worker.disk_event.connect(self.on_disk_event)
        self.worker.load_updated.connect(self.window.show_load)
        self.worker.busy_changed.connect(self.on_busy_changed)
        self.bg_thread.start()

    def update_state(self):
        if not self.low_disks:
            state = "watching"
        elif self.busy:
            state = "paused"
        else:
            state = "scanning"
        if state != self.state:
            self.state = state
            self.tray.setIcon(make_icon(state))
            self.tray.setToolTip(f"{APP_NAME}: {STATE_TEXT[state]}")
            self.window.set_state(state)

    def notify(self, title, text, warning=False):
        icon = (QSystemTrayIcon.MessageIcon.Warning if warning
                else QSystemTrayIcon.MessageIcon.Information)
        self.tray.showMessage(title, text, icon, 8000)

    def on_disk_event(self, kind, disk, is_new):
        if kind == tr.ADDED:
            title = "Подключён новый диск" if is_new else "Подключён диск"
            self.notify(title, tr.describe(disk))
        elif kind == tr.LOW:
            self.low_disks.add(disk.volume_id)
            self.notify(
                f"Диск {disk.letter} заполняется",
                f"Свободно {disk.free_pct:.0f}% ({human_size(disk.free_bytes)}). "
                "Поищу мусор, когда ПК будет свободен.",
                warning=True,
            )
        elif kind in (tr.OK, tr.REMOVED):
            # Отключение и «места снова хватает» только пишем в журнал
            self.low_disks.discard(disk.volume_id)
        self.update_state()

    def on_busy_changed(self, busy, reasons):
        self.busy = busy
        self.window.set_busy(busy, reasons)
        self.update_state()

    def on_tray_click(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_window()

    def show_window(self):
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def quit(self):
        self.db.log("Программа закрыта")
        # Останавливаем таймеры в их же потоке, потом сам поток
        QMetaObject.invokeMethod(
            self.worker, "stop", Qt.ConnectionType.BlockingQueuedConnection
        )
        self.bg_thread.quit()
        self.bg_thread.wait(3000)
        self.tray.hide()
        self.qt_app.quit()


def _setup_logging():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(config.LOG_PATH),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
    )

    def on_error(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        logging.error("Необработанная ошибка:\n%s", text)
        if QApplication.instance():
            QMessageBox.critical(None, APP_NAME, f"Ошибка в программе:\n\n{exc}\n\nПодробности в {config.LOG_PATH}")

    sys.excepthook = on_error


def run():
    _setup_logging()
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName(APP_NAME)
    qt_app.setQuitOnLastWindowClosed(False)

    # Не даём запустить вторую копию
    lock = QLockFile(str(config.LOCK_PATH))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, APP_NAME, "Программа уже запущена. Ищи значок у часов.")
        return 0

    db = Database()
    db.log("Программа запущена", f"версия {__version__}")
    app = App(qt_app, db)
    app.show_window()

    code = qt_app.exec()
    db.close()
    lock.unlock()
    return code
