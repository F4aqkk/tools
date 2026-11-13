"""Запуск программы: значок в трее, окно и слежение за дисками.

Опрос дисков идёт в отдельном потоке, чтобы окно не подвисало,
если какой-то диск отвечает медленно (например, спящий HDD).
"""
import logging
import sys
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
from . import tracker as tr
from .db import Database
from .disks import DRIVE_TYPE_NAMES
from .utils import human_size

# Цвета значка в трее (раздел 6 ТЗ)
STATE_COLORS = {
    "watching": "#3fa34d",   # следит
    "scanning": "#2f7fd8",   # идёт проверка
    "paused": "#e0a020",     # пауза из-за нагрузки
}
STATE_TEXT = {
    "watching": "Слежу за дисками",
    "scanning": "Идёт проверка",
    "paused": "Пауза: ПК нагружен",
}
LOW_COLOR = QColor("#c0392b")


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


class DiskWorker(QObject):
    """Живёт в отдельном потоке и каждые несколько секунд опрашивает диски."""

    disks_updated = pyqtSignal(list)              # список DiskInfo
    disk_event = pyqtSignal(str, object, bool)    # вид, DiskInfo, новый ли

    def __init__(self, db):
        super().__init__()
        self.db = db
        self.tracker = None
        self.timer = None

    @pyqtSlot()
    def start(self):
        self.tracker = tr.DiskTracker(self.db)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.tick()
        self.timer.start(self.db.get_int("disk_poll_sec") * 1000)

    @pyqtSlot()
    def stop(self):
        if self.timer:
            self.timer.stop()

    @pyqtSlot()
    def tick(self):
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
        if self.timer.interval() != interval:
            self.timer.setInterval(interval)


class MainWindow(QMainWindow):
    COLUMNS = ["Диск", "Название", "Вид", "Тип", "Всего", "Свободно", "Свободно, %"]

    def __init__(self, db):
        super().__init__()
        self.db = db
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(720, 340)

        self.status = QLabel()
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
        lay.addWidget(self.low_label)
        lay.addWidget(self.table)
        self.setCentralWidget(box)
        self.set_state("watching")

    def set_state(self, state):
        self.status.setText(f"Состояние: {STATE_TEXT[state]}")

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

        self.tray = QSystemTrayIcon(make_icon("watching"))
        self.tray.setToolTip(f"{APP_NAME}: {STATE_TEXT['watching']}")
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
        self.tray.show()

        # Слежение за дисками в отдельном потоке
        self.disk_thread = QThread()
        self.disk_worker = DiskWorker(db)
        self.disk_worker.moveToThread(self.disk_thread)
        self.disk_thread.started.connect(self.disk_worker.start)
        self.disk_worker.disks_updated.connect(self.window.show_disks)
        self.disk_worker.disk_event.connect(self.on_disk_event)
        self.disk_thread.start()

    def notify(self, title, text, warning=False):
        icon = (QSystemTrayIcon.MessageIcon.Warning if warning
                else QSystemTrayIcon.MessageIcon.Information)
        self.tray.showMessage(title, text, icon, 8000)

    def on_disk_event(self, kind, disk, is_new):
        if kind == tr.ADDED:
            title = "Подключён новый диск" if is_new else "Подключён диск"
            self.notify(title, tr.describe(disk))
        elif kind == tr.LOW:
            self.notify(
                f"Диск {disk.letter} заполняется",
                f"Свободно {disk.free_pct:.0f}% ({human_size(disk.free_bytes)}). "
                "Поищу мусор, когда ПК будет свободен.",
                warning=True,
            )
        # Отключение диска и «места снова хватает» только пишем в журнал,
        # без всплывающих сообщений.

    def on_tray_click(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_window()

    def show_window(self):
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def quit(self):
        self.db.log("Программа закрыта")
        # Останавливаем таймер в его же потоке, потом сам поток
        QMetaObject.invokeMethod(
            self.disk_worker, "stop", Qt.ConnectionType.BlockingQueuedConnection
        )
        self.disk_thread.quit()
        self.disk_thread.wait(3000)
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
