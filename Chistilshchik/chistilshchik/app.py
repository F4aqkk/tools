"""Запуск программы: значок в трее и простое окно.

Этап 1: только скелет. Слежение за дисками, монитор нагрузки и сканер
появятся на следующих этапах.
"""
import logging
import sys
import traceback

import psutil
from PyQt6.QtCore import QLockFile, Qt, QTimer
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QLabel, QMainWindow, QMenu, QMessageBox, QSystemTrayIcon,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import APP_NAME, __version__, config
from .db import Database

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


def human_size(n):
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if n < 1024 or unit == "ТБ":
            return f"{n:.0f} {unit}" if unit in ("Б", "КБ") else f"{n:.1f} {unit}"
        n /= 1024


class MainWindow(QMainWindow):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(560, 320)

        self.status = QLabel()
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Диск", "Всего", "Свободно", "Свободно, %"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)

        box = QWidget()
        lay = QVBoxLayout(box)
        lay.addWidget(self.status)
        lay.addWidget(self.table)
        self.setCentralWidget(box)

        self.set_state("watching")
        self.refresh_disks()

    def set_state(self, state):
        self.status.setText(f"Состояние: {STATE_TEXT[state]}")

    def refresh_disks(self):
        threshold = self.db.get_int("free_threshold_pct")
        rows = []
        for part in psutil.disk_partitions(all=False):
            if "cdrom" in part.opts or not part.fstype:
                continue
            try:
                u = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            free_pct = u.free / u.total * 100 if u.total else 0
            rows.append((part.device, u.total, u.free, free_pct))

        self.table.setRowCount(len(rows))
        for r, (dev, total, free, pct) in enumerate(rows):
            cells = [dev, human_size(total), human_size(free), f"{pct:.0f}%"]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if pct < threshold:
                    item.setForeground(QColor("#c0392b"))
                self.table.setItem(r, c, item)

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

        # Пока просто обновляем таблицу дисков. На этапе 2 здесь будет
        # настоящее слежение с автообнаружением новых дисков.
        self.timer = QTimer()
        self.timer.timeout.connect(self.window.refresh_disks)
        self.timer.start(self.db.get_int("disk_poll_sec") * 1000)

    def on_tray_click(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_window()

    def show_window(self):
        self.window.refresh_disks()
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def quit(self):
        self.db.log("Программа закрыта")
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
