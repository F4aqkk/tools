"""Пути проекта и настройки по умолчанию (ТЗ, разделы 3, 4, 5, 12)."""
import os
from pathlib import Path

# Папка проекта (там, где лежит start.bat)
BASE_DIR = Path(__file__).resolve().parent.parent
# Рабочие данные программы: база и лог. В git не попадают.
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "chistilshchik.db"
LOG_PATH = DATA_DIR / "chistilshchik.log"
LOCK_PATH = DATA_DIR / "app.lock"

# Настройки по умолчанию. Все меняются в окне «Настройки» (этап 6).
DEFAULT_SETTINGS = {
    # 3.1: проверка стартует, если свободно меньше этого процента
    "free_threshold_pct": 15,
    # 3.2: пороги паузы
    "gpu_load_pct": 50,          # видеокарта больше 50%
    "cpu_avg_pct": 30,           # процессор от 30% в среднем
    "busy_cores_count": 2,       # сколько ядер «почти на 100%»
    "busy_core_pct": 95,         # что считать «почти 100%»
    "count_logical_cores": 1,    # 1 = считать потоки (16), 0 = физические ядра (8)
    "pause_delay_sec": 10,       # нагрузка держится столько секунд -> пауза
    "resume_delay_sec": 30,      # ПК спокоен столько секунд -> продолжаем
    # 3.3: диски
    "disk_poll_sec": 5,          # как часто сверять список дисков
    "watch_removable": 0,        # флешки
    "watch_network": 0,          # сетевые диски
    "watch_cdrom": 0,            # CD/DVD
    # 4: что считать мусором
    "old_logs_days": 30,
    "big_file_gb": 1,
    # 9: автозапуск вместе с Windows
    "autostart": 0,
}

# Виды исключений
EXCL_NEVER = "never"            # не трогать и не показывать никогда
EXCL_REPORT_ONLY = "report"     # только отчёт о дубликатах, без удаления
EXCL_NAME = "name"              # любая папка с таким именем (например .git)


def builtin_exclusions():
    """Встроенные исключения из раздела 5 ТЗ: (путь или имя, вид)."""
    env = os.environ
    home = Path.home()
    items = [
        (r"D:\repositori", EXCL_NEVER),
        (".git", EXCL_NAME),
        (env.get("SystemRoot", r"C:\Windows"), EXCL_NEVER),
        (env.get("ProgramFiles", r"C:\Program Files"), EXCL_NEVER),
        (env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), EXCL_NEVER),
    ]
    for name in ("Documents", "Desktop", "Pictures", "Music", "Videos"):
        items.append((str(home / name), EXCL_REPORT_ONLY))
    return items
