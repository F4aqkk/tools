"""Помощник для start.bat: создаёт окружение и ставит библиотеки,
а пока ждём — крутит палочку \\ | / _ / | \\ _ в конце строки.

    python install.py venv   — создать .venv
    python install.py deps   — поставить библиотеки из requirements.txt
"""
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
LOG = BASE / "data" / "install.log"

FRAMES = ["\\", "|", "/", "_", "/", "|", "\\", "_"]
FRAME_DELAY = 0.12

# Цвета в консоли
CYAN = "\x1b[96m"
GREEN = "\x1b[92m"
RED = "\x1b[91m"
GRAY = "\x1b[90m"
RESET = "\x1b[0m"
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"


def enable_colors():
    """Включает цвета и спецсимволы в обычной консоли Windows 10/11."""
    if os.name == "nt":
        os.system("")


def pip_status(line):
    """Строка вывода pip -> короткая подпись по-русски."""
    line = line.strip()
    m = re.match(r"Collecting (\S+)", line)
    if m:
        name = re.split(r"[<>=!~\[;]", m.group(1))[0]
        return f"ищу {name}"
    m = re.match(r"Downloading (\S+?)(?:-\d[^\s]*)?\.(?:whl|tar\.gz|zip)\s*(\(.*\))?", line)
    if m:
        name = m.group(1).split("/")[-1]
        size = f" {m.group(2)}" if m.group(2) else ""
        return f"качаю {name}{size}"
    if line.startswith("Installing collected packages"):
        return "устанавливаю"
    if line.startswith("Requirement already satisfied"):
        return "проверяю, что уже стоит"
    if line.startswith("Successfully installed"):
        return "почти готово"
    return None


def run_with_spinner(cmd, parse=None):
    """Запускает команду, крутит палочку, весь вывод пишет в data/install.log.
    Возвращает код завершения."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    status = {"text": ""}
    tail = []

    with open(LOG, "a", encoding="utf-8") as log:
        log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(cmd)}\n")
        proc = subprocess.Popen(
            cmd, cwd=BASE, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )

        def reader():
            for line in proc.stdout:
                log.write(line)
                tail.append(line.rstrip())
                del tail[:-15]
                if parse:
                    s = parse(line)
                    if s:
                        status["text"] = s

        t = threading.Thread(target=reader, daemon=True)
        t.start()

        start = time.time()
        animate = sys.stdout.isatty()
        width = max(20, shutil.get_terminal_size((80, 20)).columns - 1)
        i = 0
        try:
            if animate:
                sys.stdout.write(HIDE_CURSOR)
            while proc.poll() is None:
                if animate:
                    frame = FRAMES[i % len(FRAMES)]
                    secs = int(time.time() - start)
                    plain = f"      {frame}  {secs:>3} с  {status['text']}"
                    plain = plain[:width].ljust(width)
                    # Красим только саму палочку
                    colored = plain.replace(frame, f"{CYAN}{frame}{RESET}", 1)
                    sys.stdout.write("\r" + colored)
                    sys.stdout.flush()
                    i += 1
                time.sleep(FRAME_DELAY)
            t.join(5)
        finally:
            if animate:
                sys.stdout.write("\r" + " " * width + "\r" + SHOW_CURSOR)
                sys.stdout.flush()

    secs = int(time.time() - start)
    if proc.returncode == 0:
        print(f"      {GREEN}готово{RESET} {GRAY}за {secs} с{RESET}")
    else:
        print(f"      {RED}не получилось{RESET} (подробности в data\\install.log):")
        for line in tail:
            print(f"      {GRAY}{line}{RESET}")
    return proc.returncode


def main():
    enable_colors()
    what = sys.argv[1] if len(sys.argv) > 1 else ""
    if what == "venv":
        return run_with_spinner([sys.executable, "-m", "venv", str(BASE / ".venv")])
    if what == "deps":
        return run_with_spinner(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
             "--progress-bar", "off", "-r", str(BASE / "requirements.txt")],
            parse=pip_status,
        )
    print("Использование: python install.py venv | deps")
    return 2


if __name__ == "__main__":
    sys.exit(main())
