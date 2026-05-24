from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
DB_PATH = APP_DIR / "roundnet.db"
EXPORTS_DIR = APP_DIR / "exports"
EXPORTS_DIR.mkdir(exist_ok=True)

FAULT_TYPES = ["Pocket", "Rim", "High serve", "Foot fault", "Miss"]

DEFAULT_TARGET = 21
DEFAULT_HARD_CAP = 25

APP_NAME = "Roundnet Stats Tracker"
