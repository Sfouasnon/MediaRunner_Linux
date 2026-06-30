#!/usr/bin/env python3
from __future__ import annotations

"""
MediaRunner Logging — rotating file log, crash diagnostics, exception hooks.

Audit fix #6: the packaged app previously had no log file, no faulthandler,
and no excepthook, so field failures left nothing to inspect.

Usage (entry points call setup_logging once; modules call get_logger):

    from mediarunner_logging import setup_logging, get_logger
    setup_logging()
    log = get_logger(__name__)
"""

import faulthandler
import logging
import logging.handlers
import os
import sys
import threading
from pathlib import Path

_LOG_DIR_ENV = "MEDIARUNNER_LOG_DIR"
_LOG_LEVEL_ENV = "MEDIARUNNER_LOG_LEVEL"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 5

_setup_lock = threading.Lock()
_configured = False
_crash_handle = None  # keep the faulthandler file object alive


def log_dir() -> Path:
    """Resolve the log folder: env override > Settings (network_config.json) > default."""
    override = os.environ.get(_LOG_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    try:
        from mediarunner_core import CONFIG_DIR, NETWORK_CONFIG_PATH
    except Exception:
        return Path.home() / ".mediarunner" / "logs"
    try:
        import json
        if NETWORK_CONFIG_PATH.exists():
            configured = str(json.loads(NETWORK_CONFIG_PATH.read_text(encoding="utf-8")).get("log_dir", "") or "").strip()
            if configured:
                return Path(configured).expanduser()
    except Exception:
        pass
    return Path(CONFIG_DIR) / "logs"


def get_logger(name: str = "mediarunner") -> logging.Logger:
    return logging.getLogger(name if name.startswith("mediarunner") else f"mediarunner.{name}")


def setup_logging(level: int | None = None) -> Path | None:
    """Configure rotating file logging, faulthandler, and exception hooks.

    Safe to call more than once; only the first call configures handlers.
    Never raises: a read-only disk must not stop the tool from launching.
    """
    global _configured, _crash_handle
    with _setup_lock:
        if _configured:
            return None
        _configured = True

        if level is None:
            level_name = os.environ.get(_LOG_LEVEL_ENV, "INFO").strip().upper()
            level = getattr(logging, level_name, logging.INFO)

        root = logging.getLogger("mediarunner")
        root.setLevel(level)

        directory = None
        try:
            directory = log_dir()
            directory.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                directory / "mediarunner.log",
                maxBytes=_MAX_BYTES,
                backupCount=_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"
            ))
            root.addHandler(file_handler)
        except Exception:
            directory = None

        console = logging.StreamHandler(sys.stderr)
        console.setLevel(logging.WARNING)
        console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(console)

        # Native-crash tracebacks (segfaults in Qt/xxhash/etc.).
        try:
            if directory is not None:
                _crash_handle = open(directory / "crash.log", "a", encoding="utf-8")
                faulthandler.enable(file=_crash_handle)
            else:
                faulthandler.enable()
        except Exception:
            pass

        _install_exception_hooks(root)
        root.info("Logging initialised (dir=%s)", directory or "console-only")
        return directory


def _install_exception_hooks(root: logging.Logger) -> None:
    previous_hook = sys.excepthook

    def hook(exc_type, exc_value, exc_tb):
        try:
            root.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_tb))
        except Exception:
            pass
        if previous_hook not in (None, hook):
            try:
                previous_hook(exc_type, exc_value, exc_tb)
            except Exception:
                pass

    sys.excepthook = hook

    def thread_hook(args):
        try:
            root.critical(
                "Unhandled exception in thread %s",
                getattr(args.thread, "name", "?"),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
        except Exception:
            pass

    try:
        threading.excepthook = thread_hook
    except Exception:
        pass
