"""
run_server.py
-------------
Production server entry point (also used by the PyInstaller build).

Starts Uvicorn with settings tuned for a Windows-service deployment:
  * binds to 127.0.0.1 only (all LAN traffic arrives through the Caddy proxy)
  * single in-process worker — the app object is passed directly so uvicorn
    never spawns a child interpreter (critical when frozen: a spawned child
    would look for the build machine's python.exe)
  * rotating file logs (inline logging dict, no external config file to load)

Run directly:      python run_server.py
Run frozen:        studysync-api.exe
"""

import multiprocessing
import os
import sys
from pathlib import Path

# Required for frozen Windows executables that may use multiprocessing.
multiprocessing.freeze_support()

from dotenv import load_dotenv

# .env sits next to the app code when running from source, and next to the
# frozen executable in production. Prefer the executable directory so the
# installer-written .env is always found.
if getattr(sys, "frozen", False):
    _here = Path(sys.executable).parent
else:
    _here = Path(__file__).parent
load_dotenv(_here / ".env")

# Matplotlib font-cache dir. In a frozen PyInstaller app a missing/writable
# cache makes matplotlib rebuild its font DB on every start, which can crash
# with a spurious KeyboardInterrupt. Point it at a persistent writable folder
# (set by WinSW too, but this covers direct `studysync-api.exe` runs).
MPLCONFIGDIR = os.getenv(
    "STUDYSYNC_MPLCONFIGDIR", r"C:\ProgramData\StudySync\data\mplcache"
)
os.environ["MPLCONFIGDIR"] = MPLCONFIGDIR
Path(MPLCONFIGDIR).mkdir(parents=True, exist_ok=True)

from main import app  # noqa: E402

import uvicorn  # noqa: E402

# Inline logging configuration — keeps the frozen build free of external
# config-file loading (uvicorn cannot reload a bundled file from _internal).
LOG_DIR = Path(
    os.getenv("STUDYSYNC_LOG_DIR", r"C:\ProgramData\StudySync\logs\api")
)
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "api_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "api.log"),
            "maxBytes": 5_242_880,
            "backupCount": 10,
            "formatter": "default",
            "encoding": "utf-8",
        },
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
        },
    },
    "root": {"level": "INFO", "handlers": ["api_file", "console"]},
    "loggers": {
        "uvicorn": {"level": "INFO", "handlers": ["api_file", "console"], "propagate": False},
        "studysync": {"level": "INFO", "handlers": ["api_file", "console"], "propagate": False},
        "gspread": {"level": "WARNING", "handlers": ["api_file"], "propagate": False},
        "urllib3": {"level": "WARNING", "handlers": ["api_file"], "propagate": False},
        "matplotlib": {"level": "WARNING", "handlers": ["api_file"], "propagate": False},
        "pyzk": {"level": "INFO", "handlers": ["api_file", "console"], "propagate": False},
    },
}


if __name__ == "__main__":
    host = os.getenv("STUDYSYNC_HOST", "127.0.0.1")
    port = int(os.getenv("STUDYSYNC_PORT", "8000"))
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_config=LOGGING_CONFIG,
        access_log=False,
    )
