#!/usr/bin/env python3
"""
MediaRunner GUI Version 0.3.0-beta — production hardening + concept UI port
Navigation: Dashboard | Offload | FTP (Array / Single Camera) | Metadata | Reports | Networking | Settings | Alerts | Validation (engineering-locked)

Current engine support:
- Folder/card transfer with selectable filters
- True simultaneous, primary-first, and cascading transfer strategies
- Per-destination manifests, reports, progress, and checksum verification
- FTP camera-array pull by reel and clip numbers
- RED Wireless Ingest using RCP2 diagnostics + FTPS media access
- REDline metadata scrape with master CSV output
"""
from __future__ import annotations

import csv
import os
import sys
import subprocess
import threading
import shutil
import tempfile
import time
import wave
import math
import struct
import json
import re
import zipfile
import platform
from pathlib import Path
from datetime import datetime

def format_mediarunner_clock(dt: datetime) -> str:
    """Return clock text as MM-DD-YYYY HH:MM:SS."""
    return dt.strftime("%m-%d-%Y %I:%M:%S")


def mediarunner_clock_tokens(text: str) -> list[str]:
    """Return stable MM-DD-YYYY HH:MM:SS character tokens for the segmented clock."""
    return [str(ch) for ch in str(text or "")]

from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import Qt, Signal, QObject, QSize, QUrl, QRectF, QPointF
from PySide6.QtGui import QColor, QPalette, QTextCursor, QPixmap, QPainter, QPen, QFont, QDesktopServices, QPainterPath, QLinearGradient, QBrush, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QStackedWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout, QFrame, QLabel, QLineEdit,
    QPushButton, QProgressBar, QTableWidget, QTableWidgetItem, QHeaderView,
    QFileDialog, QCheckBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit,
    QComboBox, QRadioButton, QButtonGroup, QSizePolicy, QDialog, QSplitter,
    QListWidget, QListWidgetItem, QAbstractItemView, QMessageBox
)

sys.path.insert(0, str(Path(__file__).parent))

MEDIARUNNER_BUILD_ID = "MediaRunner Version 0.3.0-beta - production-hardening-ui-port"
MEDIARUNNER_GUI_BUILD_ID = MEDIARUNNER_BUILD_ID

# Concept-port palette (design/MediaRunner_UI_Concept.html)
APP_BG = "#0B1117"
PANEL = "#121C26"
CARD = "#16222E"
INPUT = "#0A141D"
BORDER = "#233442"
ACCENT = "#5AC8E6"
TEXT = "#DCE7EF"
MUTED = "#7E93A4"
GREEN = "#35E37A"
YELLOW = "#E3B15F"
RED = "#E87979"
ORANGE = "#F5A348"

STYLE = f"""
QMainWindow, QWidget {{
    background-color: {APP_BG};
    color: {TEXT};
    font-family: "Helvetica Neue", Arial, sans-serif;
    font-size: 14px;
}}
QFrame#sidebar {{ background: #0B1117; border-right: 1px solid {BORDER}; }}
QFrame#topbar {{ background: {APP_BG}; border-bottom: 1px solid {BORDER}; }}
QFrame#panel, QFrame#card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame#card {{ background: {CARD}; }}
QLabel {{ background: transparent; }}
QWidget#selects_box {{ background: transparent; }}
QWidget#strategyRow, QWidget#strategyRow QLabel, QWidget#strategyRow QRadioButton {{ background: transparent; border: none; }}
QLabel#app_name {{ color: #F2F7FB; font-size: 18px; font-weight: 900; letter-spacing: .3px; }}
QLabel#page_title {{ background: transparent; color: #F2F7FB; font-size: 18px; font-weight: 850; }}
QLabel#clock_display {{ background: transparent; border: none; color: {MUTED}; font-family: Menlo, Monaco, "Courier New", monospace; font-size: 13px; font-weight: 700; letter-spacing: .5px; padding: 4px 0px; }}
QLabel#meta_desc {{ background: transparent; color: #AFC0CE; font-size: 13px; font-weight: 600; }}
QLabel#section_title {{ background: transparent; color: #C8D4DF; font-size: 12px; font-weight: 850; letter-spacing: 1.2px; text-transform: uppercase; }}
QLabel#muted {{ background: transparent; color: {MUTED}; font-size: 14px; }}
QLabel#stat_num {{ background: transparent; color: {ACCENT}; font-size: 34px; font-weight: 900; }}
QLabel#stat_label {{ background: transparent; color: {MUTED}; font-size: 11px; font-weight: 850; letter-spacing: 1px; text-transform: uppercase; }}
QLabel#synopsis_phase {{ background: transparent; color: #DCE7EF; font-size: 15px; font-weight: 750; }}
QLabel#synopsis_primary {{ background: transparent; color: #B7C7D6; font-size: 14px; font-weight: 700; }}
QLabel#synopsis_secondary {{ background: transparent; color: #B7C7D6; font-size: 14px; font-weight: 700; }}
QLabel#transfer_title {{ background: transparent; color: #F2F8FD; font-size: 23px; font-weight: 900; }}
QLabel#transfer_subtitle {{ background: transparent; color: #A9BAC9; font-size: 15px; font-weight: 650; }}
QLabel#status_pill {{ background: #0B1721; border: 1px solid #2B4354; border-radius: 12px; color: #DCE7EF; padding: 7px 14px; font-size: 15px; font-weight: 800; }}
QLabel#metric_value {{ background: transparent; color: #F0F7FD; font-size: 24px; font-weight: 900; }}
QLabel#metric_label {{ background: transparent; color: #9FB0C1; font-size: 13px; font-weight: 850; letter-spacing: 1px; text-transform: uppercase; }}
QLabel#metric_sub {{ background: transparent; color: #8FA3B4; font-size: 13px; font-weight: 650; }}
QFrame#metric_card {{ background: transparent; border: none; }}
QLabel#phase_sentence {{ background: transparent; color: #D8E5EE; font-size: 17px; font-weight: 800; }}
QPushButton {{
    font-size: 14px;
    background: #243444;
    border: 1px solid #3A5064;
    border-radius: 9px;
    padding: 9px 15px;
    color: #DCE7EF;
    font-weight: 750;
}}
QPushButton:hover {{ background: #2E4458; border-color: {ACCENT}; color: #FFFFFF; }}
QPushButton:pressed {{ background: #1A2734; }}
QPushButton:disabled {{ background: #18222D; border-color: #273747; color: #697887; }}
QPushButton#primary {{ background: #347694; border-color: {ACCENT}; color: #F3F9FC; font-weight: 850; }}
QPushButton#primary:hover {{ background: #3F87A8; }}
QPushButton#nav {{
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 11px 14px;
    color: {MUTED};
    text-align: left;
    font-size: 14px;
    font-weight: 700;
}}
QPushButton#nav:hover {{ background: #15202C; color: {TEXT}; }}
QPushButton#nav_active {{
    background: #173041;
    border: none;
    border-radius: 8px;
    padding: 11px 14px;
    color: {ACCENT};
    text-align: left;
    font-size: 14px;
    font-weight: 800;
}}
QPushButton#seg {{
    background: transparent;
    border: 1px solid {BORDER};
    border-radius: 0px;
    padding: 8px 16px;
    color: {MUTED};
    font-size: 12.5px;
    font-weight: 750;
}}
QPushButton#seg:hover {{ background: #15202C; color: {TEXT}; }}
QPushButton#seg:checked {{ background: #173041; color: {ACCENT}; border-color: {BORDER}; }}
QPushButton#seg_first {{ border-top-left-radius: 9px; border-bottom-left-radius: 9px; }}
QPushButton#seg_last {{ border-top-right-radius: 9px; border-bottom-right-radius: 9px; }}
QLabel#chip_ok {{ background: #0A1F15; color: {GREEN}; border: 1px solid #1E4A39; border-radius: 11px; padding: 3px 10px; font-size: 11.5px; font-weight: 850; }}
QLabel#chip_warn {{ background: #241B0C; color: {YELLOW}; border: 1px solid #4A3D1E; border-radius: 11px; padding: 3px 10px; font-size: 11.5px; font-weight: 850; }}
QLabel#chip_fail {{ background: #240F0F; color: {RED}; border: 1px solid #4A2424; border-radius: 11px; padding: 3px 10px; font-size: 11.5px; font-weight: 850; }}
QLabel#chip_run {{ background: #0C1F28; color: {ACCENT}; border: 1px solid #1E3F4A; border-radius: 11px; padding: 3px 10px; font-size: 11.5px; font-weight: 850; }}
QFrame#slot {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#destcard {{ background: #0E1620; border: 1px solid {BORDER}; border-radius: 10px; }}
QLabel#brand_name {{ background: transparent; color: #F2F7FB; font-size: 16px; font-weight: 900; letter-spacing: .2px; }}
QLabel#brand_sub {{ background: transparent; color: {MUTED}; font-size: 10.5px; font-weight: 650; }}
QLabel#brand_mark {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {ACCENT}, stop:1 #2D7FA0); color: #06121A; border-radius: 8px; font-size: 15px; font-weight: 900; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    font-size: 14px;
    background: {INPUT};
    border: 1px solid #34495C;
    border-radius: 8px;
    padding: 8px 10px;
    color: #E8F0F7;
    selection-background-color: {ACCENT};
    selection-color: #0B1117;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; background: #111A23; }}
QLineEdit::placeholder {{ color: #6F7F8F; }}
QComboBox QAbstractItemView {{ background: #101923; color: #DCE7EF; selection-background-color: #223747; selection-color: #F5FBFF; border: 1px solid #34495C; outline: none; }}
QCheckBox, QRadioButton {{ background: transparent; color: #C9D5DF; spacing: 8px; font-size: 14px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; border: 1px solid #4A5F72; background: {INPUT}; }}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QProgressBar {{ background: #0D141C; border: 1px solid {BORDER}; border-radius: 4px; height: 8px; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QProgressBar#active_progress {{
    background: transparent;
    border: none;
    min-height: 44px;
    max-height: 44px;
}}
QTableWidget {{
    background: #0E151D;
    border: 1px solid {BORDER};
    border-radius: 10px;
    gridline-color: #243241;
    alternate-background-color: #121C26;
    color: #DCE7EF;
}}
QTableWidget::item {{ padding: 7px 8px; color: #DCE7EF; font-size: 13px; }}
QTableWidget::item:selected {{ background: #284960; color: #F5FBFF; }}
QHeaderView::section {{
    background: #1B2A38;
    color: #B9C7D5;
    padding: 9px 8px;
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    font-size: 11px;
    font-weight: 850;
    letter-spacing: .9px;
    text-transform: uppercase;
}}
QPlainTextEdit {{
    background: #0B121A;
    border: 1px solid {BORDER};
    border-radius: 10px;
    color: #B8D8CB;
    font-family: Menlo, Monaco, Consolas, monospace;
    font-size: 12px;
}}
QScrollBar:vertical, QScrollBar:horizontal {{ background: #0E151D; border: none; width: 10px; height: 10px; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background: #34495C; border-radius: 5px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}
"""

class SegmentedControl(QWidget):
    """Concept-style segmented control backed by checkable buttons.

    changed(str) fires with the selected segment's label. Drop-in visual
    replacement for clusters of QRadioButtons.
    """
    changed = Signal(str)

    def __init__(self, options: list[str], current: str | None = None, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for i, text in enumerate(options):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            if i == 0:
                btn.setObjectName("seg_first")
            elif i == len(options) - 1:
                btn.setObjectName("seg_last")
            else:
                btn.setObjectName("seg")
            btn.setStyleSheet(
                f"QPushButton {{ background: transparent; border: 1px solid {BORDER}; color: {MUTED};"
                f" padding: 8px 16px; font-size: 12.5px; font-weight: 750;"
                + ("border-top-left-radius: 9px; border-bottom-left-radius: 9px;" if i == 0 else "")
                + ("border-top-right-radius: 9px; border-bottom-right-radius: 9px;" if i == len(options) - 1 else "")
                + ("border-left: none;" if i > 0 else "")
                + f" }} QPushButton:hover {{ background: #15202C; color: {TEXT}; }}"
                f" QPushButton:checked {{ background: #173041; color: {ACCENT}; }}"
            )
            self._group.addButton(btn)
            lay.addWidget(btn)
            self._buttons[text] = btn
            btn.clicked.connect(lambda _c=False, t=text: self.changed.emit(t))
        lay.addStretch()
        self.set_current(current or options[0])

    def set_current(self, text: str):
        btn = self._buttons.get(text)
        if btn is not None:
            btn.setChecked(True)

    def current(self) -> str:
        for text, btn in self._buttons.items():
            if btn.isChecked():
                return text
        return next(iter(self._buttons), "")


def make_chip(text: str, kind: str = "run") -> QLabel:
    chip = QLabel(text)
    chip.setObjectName(f"chip_{kind}")
    chip.setAlignment(Qt.AlignCenter)
    return chip


class WorkerSignals(QObject):
    log = Signal(str)
    payload_preview = Signal(str)
    progress = Signal(int, int)
    table_row = Signal(dict)
    scan_result = Signal(dict)
    master_csv = Signal(str)
    report_html = Signal(str)
    dest_status = Signal(int, str, str)  # row, available, status
    job_event = Signal(dict)
    dest_progress = Signal(dict)
    finished = Signal(bool)
    status = Signal(str)


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / name


def browse_dir(line_edit: QLineEdit):
    d = QFileDialog.getExistingDirectory(None, "Select Folder", line_edit.text() or str(Path.home()))
    if d:
        line_edit.setText(d)


def path_picker(placeholder: str = "") -> tuple[QWidget, QLineEdit]:
    w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0,0,0,0); h.setSpacing(8)
    le = QLineEdit(); le.setPlaceholderText(placeholder)
    btn = QPushButton("Browse…"); btn.clicked.connect(lambda: browse_dir(le))
    h.addWidget(le, 1); h.addWidget(btn)
    return w, le




def play_finish_chime():
    """Play a short generated completion chime. No bundled copyrighted audio."""
    try:
        sr = 44100
        total_seconds = 2.15
        n_total = int(sr * total_seconds)
        samples = [0.0] * n_total

        # A public-domain-style original: low boom + bell arpeggio + soft final shimmer.
        events = [
            # freq, start, duration, amp, decay
            (130.81, 0.00, 1.05, 0.46, 2.7),   # C3 boom
            (196.00, 0.04, 0.90, 0.18, 3.1),   # G3 body
            (523.25, 0.20, 0.85, 0.24, 3.2),   # C5 bell
            (659.25, 0.38, 0.80, 0.22, 3.0),   # E5 bell
            (783.99, 0.56, 0.88, 0.21, 2.9),   # G5 bell
            (1046.50, 0.86, 0.95, 0.20, 2.6),  # C6 lift
            (1318.51, 1.12, 0.75, 0.12, 3.0),  # E6 sparkle
        ]

        for freq, start_s, dur_s, amp, decay in events:
            start_i = int(start_s * sr)
            n = int(dur_s * sr)
            for i in range(n):
                idx = start_i + i
                if idx >= n_total:
                    break
                t = i / sr
                attack = min(1.0, i / max(1, int(sr * 0.018)))
                env = attack * math.exp(-decay * t)
                # Bell-like tone: fundamental plus quiet inharmonic partials.
                tone = math.sin(2 * math.pi * freq * t)
                tone += 0.42 * math.sin(2 * math.pi * freq * 2.01 * t)
                tone += 0.18 * math.sin(2 * math.pi * freq * 3.98 * t)
                samples[idx] += amp * env * tone

        # Very short low thump at the start, intentionally subtle.
        for i in range(int(sr * 0.24)):
            t = i / sr
            env = math.exp(-13.0 * t)
            samples[i] += 0.26 * env * math.sin(2 * math.pi * 58.0 * t)

        # Normalize gently to avoid clipping.
        peak = max(0.001, max(abs(s) for s in samples))
        scale = min(0.82 / peak, 1.0)
        pcm = []
        for s in samples:
            pcm.append(int(max(-1, min(1, s * scale)) * 32767))

        out = Path(tempfile.gettempdir()) / "mediarunner_finish_chime.wav"
        with wave.open(str(out), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(b"".join(struct.pack("<h", s) for s in pcm))

        if sys.platform == "darwin":
            subprocess.Popen(["afplay", str(out)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform.startswith("win"):
            import winsound
            winsound.PlaySound(str(out), winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            player = shutil.which("paplay") or shutil.which("aplay")
            if player:
                subprocess.Popen([player, str(out)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def dispatch_completion_alert(summary: dict, log_callback=None) -> None:
    """Send configured alerts in the background; never affect transfer status."""
    payload = dict(summary or {})

    def emit(message: str):
        if callable(log_callback):
            try:
                log_callback(message)
            except Exception:
                pass

    def work():
        try:
            from mediarunner_core import load_network_config
            from mediarunner_notifications import send_alerts

            results = send_alerts(load_network_config(), payload)
            for result in results:
                provider = result.get("provider", "Alert")
                if result.get("status") == "sent":
                    emit(f"Alert sent: {provider}")
                else:
                    emit(f"Alert failed: {provider}: {result.get('message', '')}")
        except Exception as exc:
            emit(f"Alert failed: {exc}")

    threading.Thread(target=work, daemon=True, name="mediarunner-alerts").start()


def human_bytes(num: int | float) -> str:
    try:
        num = float(num)
    except Exception:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024:
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} {unit}"
        num /= 1024
    return f"{num:.1f} PB"


def disk_free_bytes(path: Path) -> int:
    """Return free bytes for path, walking up to an existing parent when needed."""
    p = Path(path).expanduser()
    probe = p if p.exists() else p.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if not probe.exists():
        raise FileNotFoundError(f"No existing parent for {path}")
    return shutil.disk_usage(probe).free


def label(text: str, obj: str | None = None) -> QLabel:
    l = QLabel(text)
    if obj:
        l.setObjectName(obj)
    return l


def panel(title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    f = QFrame(); f.setObjectName("panel")
    v = QVBoxLayout(f); v.setContentsMargins(18,16,18,16); v.setSpacing(12)
    v.setAlignment(Qt.AlignTop)
    if title:
        title_label = label(title, "section_title")
        title_label.setMaximumHeight(18)
        v.addWidget(title_label)
    return f, v


def make_table(columns: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(columns))
    t.setHorizontalHeaderLabels(columns)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    t.setAlternatingRowColors(True)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectRows)
    return t


def status_color(status: str) -> str:
    return {
        "ONLINE": GREEN, "FTP+RCP2": GREEN, "FTP": GREEN, "Verified": GREEN, "Verified via ASC MHL": GREEN, "Skipped Existing Verified": GREEN, "Complete": GREEN,
        "Ready": ACCENT, "Running": YELLOW, "Copied": YELLOW, "Downloaded": YELLOW, "Downloaded / local-checksummed": YELLOW,
        "RCP2": ACCENT, "RCP2 DISCOVERED": ACCENT, "Skipped Existing Unverified": ORANGE, "Pending": MUTED, "Waiting": MUTED, "DISCOVERED": ACCENT,
        "RCP2 ONLY": YELLOW,
        "OFFLINE": ORANGE, "Cancelled": ORANGE,
        "FAIL": RED, "Failed": RED, "MISMATCH": RED, "Mismatch": RED, "MHL Missing": RED, "MHL Mismatch": RED, "Partial": RED, "PARTIAL": RED,
        "MISSING": RED, "Missing": RED, "Insufficient": RED, "ERROR": RED, "Errors": RED, "NOT FOUND": YELLOW,
    }.get(status, TEXT)


MAX_TABLE_ROWS = 5000  # audit fix #17: cap result tables so huge jobs don't bloat the UI


def add_row(table: QTableWidget, values: list, status: str | None = None):
    # Drop oldest rows beyond the cap; the CSV manifest remains the full record.
    while table.rowCount() >= MAX_TABLE_ROWS:
        table.removeRow(0)
    r = table.rowCount(); table.insertRow(r)
    for c, v in enumerate(values):
        item = QTableWidgetItem(str(v))
        if c == 0:
            item.setTextAlignment(Qt.AlignCenter)
            item.setForeground(QColor(status_color(str(v))))
        table.setItem(r, c, item)
    table.scrollToBottom()


def make_console() -> QPlainTextEdit:
    c = QPlainTextEdit(); c.setReadOnly(True); c.setMaximumBlockCount(3000)
    return c


def log_to(console: QPlainTextEdit | None, text: str, color: str = "#B8D8CB"):
    if console is None:
        return
    ts = datetime.now().strftime("%H:%M:%S")
    safe = str(text).replace("<", "&lt;").replace(">", "&gt;")
    console.appendHtml(f'<span style="color:#7F91A3">[{ts}]</span> <span style="color:{color}">{safe}</span>')
    console.moveCursor(QTextCursor.End)


def build_filter_tokens(camera: str, reel: str, clips: str) -> list[str]:
    tokens = []
    if camera.strip() and camera.strip().upper() != "ANY":
        tokens.append(camera.strip().upper())
    if reel.strip():
        tokens.append(reel.strip().upper())
    if clips.strip():
        raw = clips.replace(",", " ").split()
        for part in raw:
            if "-" in part:
                try:
                    a,b = part.split("-",1)
                    for n in range(int(a), int(b)+1):
                        tokens.append(f"{n:03d}")
                except Exception:
                    tokens.append(part.upper())
            else:
                try:
                    tokens.append(f"{int(part):03d}")
                except Exception:
                    tokens.append(part.upper())
    return tokens



class BlockClockLabel(QLabel):
    """MediaRunner segmented clock.

    The date/time uses orange segmented digits without an AM/PM suffix.
    Tokens are laid out as fixed slots so MM-DD-YYYY always renders with
    both dashes visible: M M - D D - Y Y Y Y.
    """

    SEGMENTS = {
        "0": "ABCDEF",
        "1": "BC",
        "2": "ABGED",
        "3": "ABGCD",
        "4": "FBGC",
        "5": "AFGCD",
        "6": "AFGECD",
        "7": "ABC",
        "8": "ABCDEFG",
        "9": "ABFGCD",
        "-": "G",
        " ": "",
    }

    def __init__(self, text: str = ""):
        super().__init__(text)
        self.setMinimumWidth(760)
        self.setMinimumHeight(86)
        self.setAlignment(Qt.AlignCenter)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def _segment_polys(self, x: float, y: float, scale: float, slant: float):
        w = 42.0 * scale
        h = 72.0 * scale
        t = 8.8 * scale
        m = 3.6 * scale

        def transform(points):
            return [QPointF(px + ((py - y) * slant), py) for px, py in points]

        def horiz(x0, y0, x1, y1):
            cham = t * 0.45
            mid = (y0 + y1) / 2.0
            return transform([
                (x0 + cham, y0), (x1 - cham, y0), (x1, mid),
                (x1 - cham, y1), (x0 + cham, y1), (x0, mid),
            ])

        def vert(x0, y0, x1, y1):
            cham = t * 0.45
            mid = (x0 + x1) / 2.0
            return transform([
                (x0, y0 + cham), (mid, y0), (x1, y0 + cham),
                (x1, y1 - cham), (mid, y1), (x0, y1 - cham),
            ])

        return {
            "A": horiz(x + m, y, x + w - m, y + t),
            "G": horiz(x + m, y + h / 2.0 - t / 2.0, x + w - m, y + h / 2.0 + t / 2.0),
            "D": horiz(x + m, y + h - t, x + w - m, y + h),
            "F": vert(x, y + m, x + t, y + h / 2.0 - m),
            "B": vert(x + w - t, y + m, x + w, y + h / 2.0 - m),
            "E": vert(x, y + h / 2.0 + m, x + t, y + h - m),
            "C": vert(x + w - t, y + h / 2.0 + m, x + w, y + h - m),
        }, w, h

    def _draw_digit(self, painter: QPainter, x: float, y: float, ch: str, scale: float, slant: float, glow: bool):
        on = self.SEGMENTS.get(ch, "")
        polys, w, h = self._segment_polys(x, y, scale, slant)

        active = QColor(ORANGE)
        glow_col = QColor(255, 148, 34, 22 if glow else 0)

        painter.setPen(Qt.NoPen)

        if glow:
            painter.setBrush(glow_col)
            for key in on:
                pts = polys.get(key)
                if pts:
                    glow_pts = []
                    cx = sum(p.x() for p in pts) / len(pts)
                    cy = sum(p.y() for p in pts) / len(pts)
                    for p in pts:
                        glow_pts.append(QPointF(cx + (p.x() - cx) * 1.10, cy + (p.y() - cy) * 1.12))
                    painter.drawPolygon(QPolygonF(glow_pts))

        painter.setBrush(active)
        for key in on:
            pts = polys.get(key)
            if pts:
                painter.drawPolygon(QPolygonF(pts))

        return w + (5.0 * scale)

    def _draw_dash(self, painter: QPainter, x: float, y: float, slot_w: float, scale: float, slant: float):
        glyph_w = 42.0 * scale
        inset = max(0.0, (slot_w - glyph_w) / 2.0)
        self._draw_digit(painter, x + inset, y, "-", scale, slant, glow=False)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = QRectF(self.rect()).adjusted(10, 4, -10, -4)
        tokens = mediarunner_clock_tokens(self.text())

        digit_w = 42.0
        gap = 4.0
        colon_advance = 9.0
        space_advance = 54.0
        hyphen_advance = 22.0
        total_units = 0.0

        for ch in tokens:
            if ch == ":":
                total_units += colon_advance
            elif ch == " ":
                total_units += space_advance
            elif ch == "-":
                total_units += hyphen_advance
            else:
                total_units += digit_w + gap

        total_units = max(1.0, total_units - gap)

        scale_x = rect.width() / total_units
        scale_y = rect.height() / 72.0
        scale = max(0.38, min(scale_x, scale_y, 1.14)) * 0.499

        slant = 0.075
        digit_h = 72.0 * scale
        total_w = total_units * scale + digit_h * slant
        right_margin = 6.0
        x = rect.right() - total_w - right_margin
        y = rect.top() + (rect.height() - digit_h) / 2.0

        active = QColor(ORANGE)
        painter.setPen(Qt.NoPen)

        for ch in tokens:
            if ch == ":":
                r = max(2.2, 4.0 * scale)
                center_x = x + 4.2 * scale
                for dy in (23.0, 49.0):
                    painter.setBrush(active)
                    painter.drawEllipse(QPointF(center_x, y + dy * scale), r, r)
                x += colon_advance * scale
            elif ch == " ":
                x += space_advance * scale
            elif ch == "-":
                slot_w = hyphen_advance * scale
                self._draw_dash(painter, x, y, slot_w, scale, slant)
                x += slot_w
            else:
                x += self._draw_digit(painter, x, y, ch.upper(), scale, slant, glow=False)






class ActivityLogMixin:

    def add_activity_log(self, root_layout: QVBoxLayout):
        """Add a compact Activity Log button backed by a detached details window.

        The backing console is intentionally not inserted into the page layout;
        pages should keep their working space for transfer controls and tables.
        """
        row = QHBoxLayout()
        self.btn_activity_log = QPushButton("Activity Log")
        self.btn_activity_log.clicked.connect(self.toggle_activity_log)
        row.addWidget(self.btn_activity_log)
        row.addStretch()
        root_layout.addLayout(row)
        self.console = make_console()
        self.console.setMaximumBlockCount(12000)
        self.console.hide()
        self.details_window = None

    def toggle_activity_log(self):
        if getattr(self, "details_window", None) is None or not self.details_window.isVisible():
            self.details_window = ActivityDetailsDialog(self.window())
            self.details_window.set_text_from(self.console)
            self.details_window.show()
            self.details_window.raise_()
            self.details_window.activateWindow()
        else:
            self.details_window.raise_()
            self.details_window.activateWindow()


class MediaRunnerProgressBar(QProgressBar):
    """Stable approved pill progress bar.

    No custom end caps, no arrow geometry, no experimental painter artifacts.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTextVisible(False)
        self.setMinimumHeight(84)
        self.setMaximumHeight(92)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet("""
            QProgressBar {
                background: #071018;
                border: 2px solid #33576D;
                border-radius: 21px;
                min-height: 42px;
                max-height: 42px;
                padding: 5px;
            }
            QProgressBar::chunk {
                border-radius: 15px;
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0AA1B7,
                    stop:0.42 #21C1D7,
                    stop:0.72 #62DDEA,
                    stop:1 #B5F6FF
                );
            }
        """)


class ProgressMetricsWidget(QWidget):
    """Painter-based metric strip with exact horizontal centers at 25%, 50%, 75%."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = [
            {"icon": "◷", "label": "ETA", "value": "--", "sub": ""},
            {"icon": "◌", "label": "PROGRESS", "value": "--", "sub": "Files: --"},
            {"icon": "◴", "label": "ELAPSED", "value": "--", "sub": ""},
        ]
        self.setMinimumHeight(145)
        self.setMaximumHeight(155)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_values(self, eta: str, progress: str, elapsed: str, files: str):
        self.items[0]["value"] = eta
        self.items[1]["value"] = progress
        self.items[1]["sub"] = files
        self.items[2]["value"] = elapsed
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        w = max(1, self.width())
        centers = [w * 0.25, w * 0.50, w * 0.75]

        y_icon = 24
        y_label = 62
        y_value = 94
        y_sub = 122

        icon_font = QFont()
        icon_font.setPointSize(34)
        icon_font.setBold(True)

        label_font = QFont()
        label_font.setPointSize(13)
        label_font.setBold(True)
        label_font.setLetterSpacing(QFont.PercentageSpacing, 116)

        value_font = QFont()
        value_font.setPointSize(27)
        value_font.setBold(True)

        sub_font = QFont()
        sub_font.setPointSize(13)
        sub_font.setBold(True)

        for cx, item in zip(centers, self.items):
            painter.setFont(icon_font)
            painter.setPen(QColor("#5BCDF1"))
            painter.drawText(QRectF(cx - 115, y_icon - 30, 230, 52), Qt.AlignCenter, item["icon"])

            painter.setFont(label_font)
            painter.setPen(QColor("#9FB0C1"))
            painter.drawText(QRectF(cx - 150, y_label - 16, 300, 30), Qt.AlignCenter, item["label"])

            painter.setFont(value_font)
            painter.setPen(QColor("#F0F7FD"))
            painter.drawText(QRectF(cx - 150, y_value - 26, 300, 44), Qt.AlignCenter, item["value"])

            painter.setFont(sub_font)
            painter.setPen(QColor("#8FA3B4"))
            painter.drawText(QRectF(cx - 170, y_sub - 14, 340, 26), Qt.AlignCenter, item.get("sub", ""))






class ActivityDetailsDialog(QDialog):
    """Detached activity log window so details never compress the Dashboard."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MediaRunner Activity Monitor")
        self.resize(880, 420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel("Activity Monitor")
        title.setObjectName("section_title")
        layout.addWidget(title)

        self.console = make_console()
        self.console.setMinimumHeight(320)
        self.console.setMaximumBlockCount(12000)
        layout.addWidget(self.console, 1)

        row = QHBoxLayout()
        row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setMaximumWidth(120)
        close_btn.clicked.connect(self.close)
        row.addWidget(close_btn)
        layout.addLayout(row)

    def set_text_from(self, other_console: QPlainTextEdit):
        self.console.setPlainText(other_console.toPlainText())
        self.console.moveCursor(QTextCursor.End)

    def append_plain(self, text: str, color: str = MUTED):
        log_to(self.console, text, color)




# Future monitor direction:
# - The Dashboard currently treats the progress monitor as a focused current-job view.
# - For true simultaneous long transfers, the correct UX is multiple stacked TransferProgressCard
#   widgets, one per active job/destination group, rather than splitting one progress bar.
# - This mirrors offload-manager behavior and avoids ambiguous combined progress.

class DashboardPage(QWidget):

    def __init__(self):
        super().__init__()
        self.job_started_at = None
        self.current_phase = "Ready"
        self.last_done = 0
        self.last_total = 0
        self.latest_report_path = ""
        self.current_job = "No active transfer"
        self.current_source = ""
        self.current_destination = ""
        self.stop_callback = None
        # Concept-port layout: hero Active Job card (left) + KPI cards and a
        # live activity feed (right), with the Active Transfers table below.
        root = QVBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(14)

        top_grid = QHBoxLayout(); top_grid.setSpacing(14)

        # ── Left: Active Job hero ──
        progress_panel, pv = panel("Active Job")
        title_row = QHBoxLayout()
        title_stack = QVBoxLayout()
        self.transfer_title = label("No active job", "transfer_title")
        self.transfer_subtitle = label("Start an offload to see it here.", "transfer_subtitle")
        title_stack.addWidget(self.transfer_title)
        title_stack.addWidget(self.transfer_subtitle)
        title_row.addLayout(title_stack, 1)
        self.status_pill = label("● Idle", "status_pill")
        self.status_pill.setAlignment(Qt.AlignCenter)
        title_row.addWidget(self.status_pill, 0, Qt.AlignTop)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setMaximumWidth(110)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.request_stop)
        title_row.addWidget(self.stop_btn, 0, Qt.AlignTop)
        pv.addLayout(title_row)

        self.active_progress = MediaRunnerProgressBar()
        self.active_progress.setObjectName("active_progress")
        self.active_progress.setRange(0, 1)
        self.active_progress.setValue(0)

        progress_row = QHBoxLayout()
        progress_row.setSpacing(18)
        progress_row.addWidget(self.active_progress, 1)
        pct_stack = QVBoxLayout()
        pct_stack.setSpacing(2)
        self.percent_label = label("0%", "stat_num")
        self.percent_label.setAlignment(Qt.AlignCenter)
        self.percent_label.setStyleSheet("background: transparent; color: #71D9EE; font-size: 26px; font-weight: 900;")
        self.bytes_label = label("", "metric_sub")
        self.bytes_label.setAlignment(Qt.AlignCenter)
        self.bytes_label.setStyleSheet("background: transparent; color: #A2B9C9; font-size: 14px; font-weight: 700;")
        pct_stack.addStretch()
        pct_stack.addWidget(self.percent_label)
        pct_stack.addWidget(self.bytes_label)
        pct_stack.addStretch()
        progress_row.addLayout(pct_stack)
        pv.addLayout(progress_row)

        self.destination_progress_box = QWidget()
        self.destination_progress_layout = QVBoxLayout(self.destination_progress_box)
        self.destination_progress_layout.setContentsMargins(0, 2, 0, 0)
        self.destination_progress_layout.setSpacing(7)
        self.destination_progress_rows = {}
        self.destination_progress_box.hide()
        pv.addWidget(self.destination_progress_box)

        self.metrics_widget = ProgressMetricsWidget()
        pv.addWidget(self.metrics_widget)

        self.phase_label = label("Ready", "phase_sentence")
        self.phase_label.setWordWrap(True)
        pv.addWidget(self.phase_label)
        pv.addStretch()

        btn_row = QHBoxLayout()
        self.details_btn = QPushButton("Activity Details")
        self.details_btn.setMaximumWidth(185)
        self.details_btn.clicked.connect(self.toggle_details)
        self.open_report_btn = QPushButton("Open Report")
        self.open_report_btn.setMaximumWidth(150)
        self.open_report_btn.setEnabled(False)
        self.open_report_btn.clicked.connect(self.open_latest_report)
        btn_row.addWidget(self.details_btn)
        btn_row.addWidget(self.open_report_btn)
        btn_row.addStretch()
        pv.addLayout(btn_row)
        top_grid.addWidget(progress_panel, 5)

        # ── Right: KPI cards + live activity feed ──
        right_col = QVBoxLayout(); right_col.setSpacing(14)
        kpi_grid = QGridLayout(); kpi_grid.setSpacing(12)
        self.stat_labels = {}
        for i, (title, num) in enumerate([("Active Jobs", "0"), ("Outputs", "—"), ("FTP Cameras", "No scan"), ("Verification", "XXH128")]):
            f, v = panel()
            n = label(num, "stat_num"); l = label(title, "stat_label")
            n.setStyleSheet(f"background: transparent; color: {ACCENT}; font-size: 22px; font-weight: 950;")
            self.stat_labels[title] = n
            v.addWidget(n); v.addWidget(l)
            kpi_grid.addWidget(f, i // 2, i % 2)
        right_col.addLayout(kpi_grid)

        feed_panel, fv = panel("Activity")
        self.activity = make_console()
        self.activity.setMaximumBlockCount(12000)
        self.activity.setMinimumHeight(140)
        fv.addWidget(self.activity, 1)
        right_col.addWidget(feed_panel, 1)
        top_grid.addLayout(right_col, 3)

        root.addLayout(top_grid, 3)

        # ── Below: Active Transfers table ──
        active_panel, av = panel("Active Transfers")
        self.active_table = make_table(["Status","Job","Source","Destination","Progress"])
        self.active_table.setMinimumHeight(82)
        self.active_table.setMaximumHeight(140)
        av.addWidget(self.active_table)
        root.addWidget(active_panel, 0)

        self.details_window = None

    def _metric(self, parent_layout: QHBoxLayout, icon: str, label_text: str, value: str, sub: str):
        f = QFrame(); f.setObjectName("metric_card")
        row = QVBoxLayout(f); row.setContentsMargins(8, 2, 8, 2); row.setSpacing(3)
        ic = label(icon, "stat_num")
        ic.setAlignment(Qt.AlignCenter)
        ic.setStyleSheet("background: transparent; color: #5BCDF1; font-size: 25px; font-weight: 900;")
        lab = label(label_text, "metric_label")
        lab.setAlignment(Qt.AlignCenter)
        val = label(value, "metric_value")
        val.setAlignment(Qt.AlignCenter)
        sublab = label(sub, "metric_sub")
        sublab.setAlignment(Qt.AlignCenter)
        row.addStretch()
        row.addWidget(ic)
        row.addWidget(lab)
        row.addWidget(val)
        row.addWidget(sublab)
        row.addStretch()
        parent_layout.addWidget(f, 1)
        return val, sublab

    def toggle_details(self):
        if self.details_window is None or not self.details_window.isVisible():
            self.details_window = ActivityDetailsDialog(self.window())
            self.details_window.set_text_from(self.activity)
            self.details_window.show()
            self.details_window.raise_()
            self.details_window.activateWindow()
        else:
            self.details_window.raise_()
            self.details_window.activateWindow()

    def open_latest_report(self):
        if self.latest_report_path and Path(self.latest_report_path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.latest_report_path))

    def request_stop(self):
        if callable(self.stop_callback):
            self.stop_callback()
            self.append_activity("Stop requested. Current file may finish before workers exit.", ORANGE)

    def _fmt_duration(self, seconds: float | int | None) -> str:
        if seconds is None:
            return "--"
        try:
            seconds = max(0, int(seconds))
        except Exception:
            return "--"
        minutes, secs = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _fmt_throughput(self, bytes_per_second: float | int | None) -> str:
        try:
            bps = float(bytes_per_second or 0)
        except Exception:
            bps = 0.0
        if bps <= 0:
            return "Avg copy rate: --"
        gbps = (bps * 8.0) / 1_000_000_000.0
        mbps = (bps * 8.0) / 1_000_000.0
        if gbps >= 1.0:
            return f"Avg copy rate: {gbps:.1f} Gb/s"
        return f"Avg copy rate: {mbps:.0f} Mb/s"

    def set_transfer_rate(self, bytes_per_second: float | int | None, phase: str = "Transfer"):
        text = self._fmt_throughput(bytes_per_second)
        if phase:
            text = f"{phase} • {text}"
        self.bytes_label.setText(text)

    def _clean_path_tail(self, text: str) -> str:
        value = str(text or "").strip()
        if "/" in value:
            try:
                return Path(value).name or value
            except Exception:
                return value
        return value

    def friendly_phase(self, text: str) -> str:
        s = str(text or "").strip()
        low = s.lower()
        if not s:
            return self.current_phase
        if low.startswith("preflight payload:"):
            return s.replace("Preflight payload:", "Preparing:")
        if "available:" in low:
            return "Checking destination space"
        if low.startswith("destination:"):
            role = s.split("→", 1)[0].replace("Destination:", "").strip()
            return f"Copying to {role}" if role else "Copying to destination"
        if low.startswith("manifest:"):
            return "Writing checksum manifest"
        if low.startswith("report:"):
            return "Writing transfer report"
        if low.startswith("csv manifest ready:"):
            return "Checksum manifest complete"
        if low.startswith("output ready:"):
            return f"Output ready: {self._clean_path_tail(s.split(':', 1)[-1])}"
        if "verified " in low:
            try:
                role, rest = s.split(":", 1)
                file_name = rest.split()[-1]
                return f"Checksum complete on {role.strip()}: {file_name}"
            except Exception:
                return "Checksum complete"
        if "copied " in low:
            try:
                role, rest = s.split(":", 1)
                file_name = rest.split()[-1]
                return f"Copy complete on {role.strip()}: {file_name}"
            except Exception:
                return "Copying files"
        if low.startswith("complete"):
            return "Complete"
        if low.startswith("running:"):
            return "Preparing transfer…"
        return s if len(s) <= 120 else s[:119] + "…"

    def _update_synopsis(self, done: int | None = None, total: int | None = None):
        if done is not None:
            self.last_done = done
        if total is not None:
            self.last_total = total

        elapsed = None
        eta = None
        if self.job_started_at:
            elapsed = (datetime.now() - self.job_started_at).total_seconds()
            if self.last_done and self.last_total and self.last_done > 0 and self.last_total >= self.last_done:
                if elapsed >= 3 and self.last_done < self.last_total:
                    rate = elapsed / self.last_done
                    eta = max(0, (self.last_total - self.last_done) * rate)

        pct = 0
        if self.last_total:
            pct = int(round((self.last_done / max(1, self.last_total)) * 100))
        if self.current_phase == "Complete":
            self.phase_label.clear()
            self.phase_label.setVisible(False)
        else:
            self.phase_label.setVisible(True)
            self.phase_label.setText(self.current_phase)
        self.percent_label.setText(f"{pct}%" if self.last_total else "")
        eta_text = self._fmt_duration(eta)
        elapsed_text = self._fmt_duration(elapsed)
        progress_text = f"{pct}%" if self.last_total else "--"
        files_text = f"Files: {self.last_done} / {self.last_total}" if self.last_total else "Files: --"
        if hasattr(self, "metrics_widget"):
            self.metrics_widget.set_values(eta_text, progress_text, elapsed_text, files_text)

    def append_activity(self, text: str, color: str = MUTED):
        if not text:
            return
        log_to(self.activity, text, color)
        if getattr(self, "details_window", None) is not None and self.details_window.isVisible():
            self.details_window.append_plain(text, color)
        stripped = str(text).strip()
        if stripped and not stripped.lower().startswith(("output ready:",)):
            self.current_phase = self.friendly_phase(stripped)
            self._update_synopsis()

    def update_ftp_camera_count(self, result: dict):
        if isinstance(result, dict) and result.get("mode") == "network_scan":
            configured = dict(result.get("configured") or {})
            online = sum(1 for v in configured.values() if isinstance(v, dict) and v.get("transfer_ready"))
            rcp2 = sum(1 for v in configured.values() if isinstance(v, dict) and v.get("rcp2_online")) + len(result.get("discovered") or [])
            total = len(configured)
            txt = f"{online}/{total}" if total else "No scan"
            self.stat_labels.get("FTP Cameras", QLabel()).setText(txt)
            self.append_activity(f"Camera scan: {txt} FTP-ready, {rcp2} RCP2-visible", ACCENT)
            return
        if isinstance(result, dict) and result.get("mode") == "ftp_array_scan":
            statuses = dict(result.get("results") or {})
            online = sum(1 for v in statuses.values() if v)
            checked = int(result.get("checked") or len(statuses))
            txt = f"{online}/{checked}" if checked else "No scan"
            self.stat_labels.get("FTP Cameras", QLabel()).setText(txt)
            self.append_activity(f"Camera scan: {online} online ({checked} saved checked)", ACCENT)
            return
        online = sum(1 for v in result.values() if v)
        total = len(result)
        txt = f"{online}/{total}" if total else "No scan"
        self.stat_labels.get("FTP Cameras", QLabel()).setText(txt)
        self.append_activity(f"Camera scan: {txt} online", ACCENT)

    def set_active_job(self, status: str, job: str, source: str, destination: str, progress: str):
        self.active_table.setRowCount(0)
        add_row(self.active_table, [status, job or "Untitled", source, destination, progress], status=status)
        self.current_job = job or "Untitled"
        self.current_source = source or ""
        self.current_destination = destination or ""
        # Hero shows the job name as the headline, route underneath (concept).
        self.transfer_title.setText(self.current_job)
        src_tail = self._clean_path_tail(source)
        dst_tail = self._clean_path_tail(destination)
        if src_tail or dst_tail:
            self.transfer_subtitle.setText(f"{src_tail[:44]}  →  {dst_tail[:44]}")
        else:
            self.transfer_subtitle.setText("—")

        if status == "Running":
            if hasattr(self, "stop_btn"):
                self.stop_btn.setEnabled(True)
            self.job_started_at = datetime.now()
            self.last_done = 0
            self.last_total = 0
            self.current_phase = "Preparing transfer…"
            self.active_progress.setRange(0, 1)
            self.active_progress.setValue(0)
            self.phase_label.setVisible(True)
            self.phase_label.setStyleSheet("background: transparent; color: #D8E5EE; font-size: 17px; font-weight: 800;")
            self.status_pill.setText("● Active")
            self.status_pill.setStyleSheet("background: #0B1721; border: 1px solid #2B4354; border-radius: 12px; color: #DCE7EF; padding: 7px 14px; font-size: 15px; font-weight: 800;")
        elif status in ("Complete", "Finished with errors"):
            if hasattr(self, "stop_btn"):
                self.stop_btn.setEnabled(False)
            self.current_phase = status
            if self.active_progress.maximum() > 0:
                self.active_progress.setValue(self.active_progress.maximum())
            self.status_pill.setText("● Complete" if status == "Complete" else "● Error")
            if status == "Complete":
                self.status_pill.setStyleSheet("background: #071B14; border: 1px solid #238B55; border-radius: 12px; color: #35E37A; padding: 7px 14px; font-size: 15px; font-weight: 800;")
                self.phase_label.clear()
                self.phase_label.setVisible(False)
            else:
                self.status_pill.setStyleSheet("background: #1B1114; border: 1px solid #5A2D34; border-radius: 12px; color: #FFD3D8; padding: 7px 14px; font-size: 15px; font-weight: 800;")
                self.phase_label.setVisible(True)
                self.phase_label.setStyleSheet("background: transparent; color: #FFD3D8; font-size: 17px; font-weight: 850;")

        active_jobs = self.stat_labels.get("Active Jobs", QLabel())
        if status not in ("Complete", "Finished with errors"):
            active_jobs.setText("1")
            active_jobs.setStyleSheet("background: transparent; color: #24D16B; font-size: 28px; font-weight: 950;")
        else:
            active_jobs.setText("0")
            active_jobs.setStyleSheet("background: transparent; color: #5AC8E6; font-size: 28px; font-weight: 950;")
        self.append_activity(f"{status}: {job or 'Untitled'}", GREEN if status == "Complete" else ACCENT)
        self._update_synopsis()

    def update_active_progress(self, progress: str, done: int | None = None, total: int | None = None):
        if self.active_table.rowCount():
            item = QTableWidgetItem(progress)
            item.setText(progress)
            self.active_table.setItem(0, 4, item)
        if done is not None and total:
            self.active_progress.setMaximum(max(total, 1))
            self.active_progress.setValue(done)
        self._update_synopsis(done, total)

    def reset_destination_progress(self, destinations: list):
        if not hasattr(self, "destination_progress_layout"):
            return
        while self.destination_progress_layout.count():
            item = self.destination_progress_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.destination_progress_rows = {}
        for dest in destinations or []:
            role = str(dest.get("role", "Destination")).strip() or "Destination"
            total = int(dest.get("total", 0) or 0)
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(10)

            title = label(f"{role} — Waiting", "metric_sub")
            title.setMinimumWidth(150)
            title.setStyleSheet("background: transparent; color: #B9C7D5; font-size: 13px; font-weight: 800;")
            bar = QProgressBar()
            bar.setFixedHeight(8)
            bar.setRange(0, max(total, 1))
            bar.setValue(0)
            pct = label("0%", "metric_sub")
            pct.setMinimumWidth(48)
            pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            pct.setStyleSheet("background: transparent; color: #A2B9C9; font-size: 13px; font-weight: 800;")

            row_layout.addWidget(title)
            row_layout.addWidget(bar, 1)
            row_layout.addWidget(pct)
            self.destination_progress_layout.addWidget(row_widget)
            self.destination_progress_rows[role] = {"title": title, "bar": bar, "pct": pct, "total": max(total, 1)}
        self.destination_progress_box.setVisible(bool(destinations))

    def update_destination_progress(self, event: dict):
        role = str(event.get("role", "Destination")).strip() or "Destination"
        if not hasattr(self, "destination_progress_rows"):
            return
        if role not in self.destination_progress_rows:
            self.reset_destination_progress([{"role": role, "total": int(event.get("total", 1) or 1)}])
        row = self.destination_progress_rows.get(role)
        if not row:
            return
        done = int(event.get("done", 0) or 0)
        total = max(1, int(event.get("total", row.get("total", 1)) or 1))
        status = str(event.get("status", "Running") or "Running")
        row["total"] = total
        row["bar"].setMaximum(total)
        row["bar"].setValue(max(0, min(done, total)))
        pct = int(round((done / total) * 100)) if total else 0
        row["pct"].setText(f"{pct}%")
        row["title"].setText(f"{role} — {status}")
        row["title"].setStyleSheet(f"background: transparent; color: {status_color(status)}; font-size: 13px; font-weight: 800;")
        self.destination_progress_box.setVisible(True)

    def add_recent_output(self, path: str):
        p = Path(path)
        if p.exists():
            log_to(self.activity, f"Output ready: {p}", GREEN)
            if p.suffix.lower() == ".html":
                self.latest_report_path = str(p)
                self.open_report_btn.setEnabled(True)
            cur = self.stat_labels.get("Outputs")
            if cur:
                try:
                    n = int(cur.text()) if cur.text().isdigit() else 0
                    cur.setText(str(n + 1))
                except Exception:
                    cur.setText("1")

    def refresh_reports(self, root_path: Path):
        return



class TransferPage(QWidget, ActivityLogMixin):
    def __init__(self, dashboard=None, nav_callback=None, settings_page=None):
        super().__init__()
        self.dashboard = dashboard
        self.nav_callback = nav_callback
        self.settings_page = settings_page
        self._current_job_name = ""
        self._current_source = ""
        self._current_destinations = ""
        self._current_reports = []
        self.signals = WorkerSignals(); self.thread = None
        # Audit fix #4: local transfers are now cancellable.
        self.cancel_event = threading.Event()
        self._build_ui()
        self.signals.log.connect(self.handle_log)
        self.signals.status.connect(self.status_label.setText)
        self.signals.progress.connect(self.on_progress)
        self.signals.table_row.connect(self.on_table_row)
        self.signals.dest_status.connect(self.update_destination_status)
        self.signals.dest_progress.connect(self.on_dest_progress)
        self.signals.job_event.connect(self.on_job_event)
        self.signals.finished.connect(self.on_finished)

    def maybe_play_finish_sound(self, ok: bool):
        sound_page = getattr(self, "sound_settings_page", None) or self.settings_page
        if ok and sound_page and hasattr(sound_page, "finish_sound_enabled") and sound_page.finish_sound_enabled():
            play_finish_chime()

    def handle_log(self, text: str):
        if hasattr(self, "console"):
            log_to(self.console, text)
        if getattr(self, "details_window", None) is not None and self.details_window.isVisible():
            self.details_window.append_plain(text)
        if self.dashboard:
            self.dashboard.append_activity(text)

    def _build_ui(self):
        root = QVBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(14)
        top_splitter = QSplitter(Qt.Horizontal)
        top_splitter.setChildrenCollapsible(False)
        top_splitter.setHandleWidth(8)
        src_panel, sv = panel("Source")
        grid = QGridLayout(); grid.setSpacing(10)
        grid.addWidget(label("Job Name"), 0, 0)
        self.job_name = QLineEdit(); self.job_name.setPlaceholderText("Optional")
        grid.addWidget(self.job_name, 0, 1)
        sv.addLayout(grid)

        mode_row = QHBoxLayout()
        mode_row.addWidget(label("Source Mode"))
        self.source_mode = SegmentedControl(["Single Source", "Multi-Mag"])
        mode_row.addWidget(self.source_mode, 1)
        sv.addLayout(mode_row)

        self.single_source_box = QWidget()
        single_grid = QGridLayout(self.single_source_box); single_grid.setContentsMargins(0,0,0,0); single_grid.setSpacing(10)
        single_grid.addWidget(label("Source"), 0, 0)
        w,self.source_path = path_picker("/media/card/A001")
        single_grid.addWidget(w, 0, 1)
        sv.addWidget(self.single_source_box)

        self.multi_source_box = QWidget()
        multi_v = QVBoxLayout(self.multi_source_box); multi_v.setContentsMargins(0,0,0,0); multi_v.setSpacing(8)
        self.mag_table = QTableWidget(0, 2)
        self.mag_table.setHorizontalHeaderLabels(["Magazine", "Path"])
        self.mag_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.mag_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.mag_table.setAlternatingRowColors(True)
        self.mag_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.mag_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.mag_table.setMinimumHeight(120)
        self.mag_table.setMaximumHeight(190)
        multi_v.addWidget(self.mag_table)
        mag_buttons = QHBoxLayout()
        add_mag = QPushButton("Add Magazine...")
        add_mag.clicked.connect(self.select_magazine_source)
        detect_mag = QPushButton("Detect Mounted")
        detect_mag.clicked.connect(self.detect_magazine_sources)
        remove_mag = QPushButton("Remove Selected")
        remove_mag.clicked.connect(self.remove_magazine_sources)
        mag_buttons.addWidget(add_mag)
        mag_buttons.addWidget(detect_mag)
        mag_buttons.addWidget(remove_mag)
        mag_buttons.addStretch()
        multi_v.addLayout(mag_buttons)
        sv.addWidget(self.multi_source_box)
        self.source_mode.changed.connect(lambda _t: self.update_source_mode_visibility())
        self.update_source_mode_visibility()

        # Payload preview: show file count + total size as soon as a source is
        # picked, scanned on a background thread so the UI never blocks.
        self.payload_label = label("", "muted")
        sv.addWidget(self.payload_label)
        self._payload_scan_generation = 0
        self.signals.payload_preview.connect(self.payload_label.setText)
        from PySide6.QtCore import QTimer as _QTimer
        self._payload_scan_timer = _QTimer(self)
        self._payload_scan_timer.setSingleShot(True)
        self._payload_scan_timer.setInterval(450)  # debounce typing
        self._payload_scan_timer.timeout.connect(self._start_payload_scan)
        self.source_path.textChanged.connect(lambda _t: self._payload_scan_timer.start())

        scope_row = QHBoxLayout()
        scope_row.addWidget(label("Scope"))
        self.scope_seg = SegmentedControl(["Entire folder", "Selects"])
        scope_row.addWidget(self.scope_seg, 1)
        sv.addLayout(scope_row)

        self.selects_box = QWidget()
        self.selects_box.setObjectName("selects_box")
        selects = QGridLayout(self.selects_box); selects.setContentsMargins(0,0,0,0); selects.setSpacing(8)
        self.camera_filter = QLineEdit(); self.camera_filter.setPlaceholderText("Any")
        self.reel_filter = QLineEdit(); self.reel_filter.setPlaceholderText("007")
        self.clip_filter = QLineEdit(); self.clip_filter.setPlaceholderText("60-64, 71, 83")
        selects.addWidget(label("Camera"),0,0); selects.addWidget(self.camera_filter,0,1)
        selects.addWidget(label("Reel"),0,2); selects.addWidget(self.reel_filter,0,3)
        selects.addWidget(label("Clips"),1,0); selects.addWidget(self.clip_filter,1,1,1,3)
        sv.addWidget(self.selects_box)
        self.scope_seg.changed.connect(lambda _t: self.update_selects_visibility())
        self.update_selects_visibility()
        src_panel.setMinimumWidth(360)
        top_splitter.addWidget(src_panel)

        # Lane arrow (concept port): Source → Destinations reads as a flow.
        arrow = QLabel("→")
        arrow.setAlignment(Qt.AlignCenter)
        arrow.setStyleSheet(f"background: transparent; color: {MUTED}; font-size: 26px; font-weight: 900;")
        arrow.setFixedWidth(40)
        top_splitter.addWidget(arrow)

        dest_panel, dv = panel("Destinations")
        self.dest_table = QTableWidget(0, 4)
        self.dest_table.setHorizontalHeaderLabels(["Role", "Destination", "Available", "Status"])
        self.dest_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.dest_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.dest_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.dest_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.dest_table.setAlternatingRowColors(True)
        self.dest_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.dest_table.setMinimumHeight(148)
        self.dest_table.setMaximumHeight(206)
        dv.addWidget(self.dest_table)
        row = QHBoxLayout()
        select_dest = QPushButton("Select Destination…"); select_dest.setObjectName("primary"); select_dest.clicked.connect(self.select_destination)
        rem = QPushButton("Remove Selected"); rem.clicked.connect(self.remove_destination)
        row.addWidget(select_dest); row.addWidget(rem); row.addStretch()
        dv.addLayout(row)
        dest_panel.setMinimumWidth(360)
        top_splitter.addWidget(dest_panel)
        top_splitter.setStretchFactor(0, 2)
        top_splitter.setStretchFactor(1, 0)
        top_splitter.setStretchFactor(2, 3)
        top_splitter.setSizes([620, 40, 760])
        root.addWidget(top_splitter, 2)

        opts = QSplitter(Qt.Horizontal)
        opts.setChildrenCollapsible(False)
        opts.setHandleWidth(8)
        strat_panel, stv = panel("Strategy")
        self.strategy_seg = SegmentedControl(["Primary First", "Simultaneous", "Cascading"])
        stv.addWidget(self.strategy_seg)
        self._strategy_notes = {
            "Primary First": "Primary verifies first; remaining destinations copy after.",
            "Simultaneous": "All destinations copy in parallel from the source.",
            "Cascading": "Source → Primary → Secondary; each leg verifies before the next.",
        }
        self.strategy_note = label(self._strategy_notes["Primary First"], "muted")
        self.strategy_note.setWordWrap(True)
        stv.addWidget(self.strategy_note)
        stv.addStretch()
        self.strategy_seg.changed.connect(lambda t: self.strategy_note.setText(self._strategy_notes.get(t, "")))
        strat_panel.setMinimumWidth(360)
        opts.addWidget(strat_panel)

        ver_panel, vv = panel("Verification")
        self.verify_seg = SegmentedControl(["Inline", "Deferred pass", "Off"])
        vv.addWidget(self.verify_seg)
        ver_note = label("xxh128 + sha256 · hash computed during the copy read", "muted")
        ver_note.setWordWrap(True)
        vv.addWidget(ver_note)
        self.report_html = QCheckBox("HTML Report"); self.report_html.setChecked(True)
        self.report_csv = QCheckBox("CSV Manifest"); self.report_csv.setChecked(True)
        rep_row = QHBoxLayout(); rep_row.addWidget(self.report_html); rep_row.addWidget(self.report_csv); rep_row.addStretch()
        vv.addLayout(rep_row)
        vv.addStretch()
        ver_panel.setMinimumWidth(300)
        opts.addWidget(ver_panel)

        run_panel, rv = panel("Run")
        self.threads_spin = QComboBox(); self.threads_spin.addItems(["4", "8", "12", "16"])
        self.threads_label = label("Threads")
        rgrid = QGridLayout(); rgrid.addWidget(self.threads_label,0,0); rgrid.addWidget(self.threads_spin,0,1)
        rv.addLayout(rgrid)
        self.run_note = label("", "muted")
        self.run_note.setWordWrap(True)
        rv.addWidget(self.run_note)
        self.start_btn = QPushButton("Start Transfer"); self.start_btn.setObjectName("primary"); self.start_btn.clicked.connect(self.start_transfer)
        rv.addWidget(self.start_btn)
        self.stop_btn = QPushButton("Stop"); self.stop_btn.setEnabled(False); self.stop_btn.clicked.connect(self.request_stop)
        rv.addWidget(self.stop_btn)
        self.resume_btn = QPushButton("Resume"); self.resume_btn.setEnabled(False); self.resume_btn.clicked.connect(self.resume_transfer)
        rv.addWidget(self.resume_btn)
        self.status_label = label("Ready", "muted"); rv.addWidget(self.status_label)
        run_panel.setMinimumWidth(220)
        opts.addWidget(run_panel)
        opts.setStretchFactor(0, 3)
        opts.setStretchFactor(1, 2)
        opts.setStretchFactor(2, 1)
        opts.setSizes([620, 360, 260])
        root.addWidget(opts)

        self.progress_label = label("Ready", "muted")
        self.progress = QProgressBar(); self.progress.setFixedHeight(8)
        self.progress.setVisible(False)
        root.addWidget(self.progress_label)
        self.console = make_console()
        self.console.hide()

    def update_selects_visibility(self):
        show = self.scope_seg.current() == "Selects"
        if hasattr(self, "selects_box"):
            self.selects_box.setVisible(show)
        if not show:
            self.camera_filter.clear()
            self.reel_filter.clear()
            self.clip_filter.clear()

    def update_source_mode_visibility(self):
        multi = self.source_mode.current() == "Multi-Mag"
        if hasattr(self, "single_source_box"):
            self.single_source_box.setVisible(not multi)
        if hasattr(self, "multi_source_box"):
            self.multi_source_box.setVisible(multi)
        if hasattr(self, "threads_spin"):
            self.threads_spin.setEnabled(not multi)
        if hasattr(self, "threads_label"):
            self.threads_label.setText("Threads" if not multi else "Single-source threads")
        if hasattr(self, "run_note"):
            self.run_note.setText("Multi-Mag uses Settings > Linux Ingest for magazine concurrency and per-magazine threads." if multi else "")
            if multi:
                self._refresh_destination_profile_note()
        if hasattr(self, "_payload_scan_timer"):
            self._payload_scan_timer.start()

    def add_magazine_source(self, path: str | Path):
        path_obj = Path(path).expanduser()
        if not str(path_obj).strip():
            return
        existing = {str(self.mag_table.item(r, 1).data(Qt.UserRole)) for r in range(self.mag_table.rowCount()) if self.mag_table.item(r, 1)}
        if str(path_obj) in existing:
            return
        row = self.mag_table.rowCount()
        self.mag_table.insertRow(row)
        name_item = QTableWidgetItem(path_obj.name or f"Magazine {row + 1}")
        path_item = QTableWidgetItem(str(path_obj))
        path_item.setData(Qt.UserRole, str(path_obj))
        self.mag_table.setItem(row, 0, name_item)
        self.mag_table.setItem(row, 1, path_item)
        if hasattr(self, "_payload_scan_timer"):
            self._payload_scan_timer.start()

    def select_magazine_source(self):
        d = QFileDialog.getExistingDirectory(self, "Select Magazine Source", str(Path("/media") if Path("/media").exists() else Path.home()))
        if d:
            self.add_magazine_source(d)

    def remove_magazine_sources(self):
        rows = sorted({i.row() for i in self.mag_table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.mag_table.removeRow(r)
        if hasattr(self, "_payload_scan_timer"):
            self._payload_scan_timer.start()

    def detect_magazine_sources(self):
        try:
            from mediarunner_linux_ingest import discover_mounted_magazines
            found = discover_mounted_magazines()
            for source in found:
                self.add_magazine_source(source)
            self.payload_label.setText(f"Detected {len(found)} magazine source{'s' if len(found) != 1 else ''}")
        except Exception as exc:
            self.payload_label.setText(f"Magazine detection failed: {exc}")

    def sources(self) -> list[Path]:
        if self.source_mode.current() != "Multi-Mag":
            text = self.source_path.text().strip()
            return [Path(text).expanduser()] if text else []
        out = []
        for r in range(self.mag_table.rowCount()):
            item = self.mag_table.item(r, 1)
            path_text = item.data(Qt.UserRole) if item else ""
            if path_text:
                out.append(Path(str(path_text)).expanduser())
        return out

    def next_destination_role(self) -> str:
        roles = ["Primary", "Secondary", "Third", "Fourth", "Fifth", "Sixth"]
        n = self.dest_table.rowCount()
        return roles[n] if n < len(roles) else f"Destination {n + 1}"

    def add_destination(self, path: str):
        path_obj = Path(path).expanduser()
        row = self.dest_table.rowCount()
        role = self.next_destination_role()
        self.dest_table.insertRow(row)

        role_item = QTableWidgetItem(role)
        dest_item = QTableWidgetItem(path_obj.name or str(path_obj))
        dest_item.setData(Qt.UserRole, str(path_obj))
        avail_item = QTableWidgetItem("—")
        status_item = QTableWidgetItem("Ready")
        status_item.setForeground(QColor(status_color("Ready")))

        self.dest_table.setItem(row, 0, role_item)
        self.dest_table.setItem(row, 1, dest_item)
        self.dest_table.setItem(row, 2, avail_item)
        self.dest_table.setItem(row, 3, status_item)
        self.update_single_destination_space(row)
        self._refresh_destination_profile_note()

    def select_destination(self):
        d = QFileDialog.getExistingDirectory(self, "Select Destination", str(Path.home()))
        if d:
            self.add_destination(d)

    def remove_destination(self):
        rows = sorted({i.row() for i in self.dest_table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.dest_table.removeRow(r)
        self.renumber_destination_roles()
        self._refresh_destination_profile_note()

    def renumber_destination_roles(self):
        roles = ["Primary", "Secondary", "Third", "Fourth", "Fifth", "Sixth"]
        for r in range(self.dest_table.rowCount()):
            role = roles[r] if r < len(roles) else f"Destination {r + 1}"
            item = self.dest_table.item(r, 0) or QTableWidgetItem()
            item.setText(role)
            self.dest_table.setItem(r, 0, item)

    def update_destination_status(self, row: int, available: str, status: str):
        if 0 <= row < self.dest_table.rowCount():
            avail_item = QTableWidgetItem(str(available))
            self.dest_table.setItem(row, 2, avail_item)
            status_item = QTableWidgetItem(str(status))
            status_item.setForeground(QColor(status_color(status)))
            self.dest_table.setItem(row, 3, status_item)

    def update_single_destination_space(self, row: int):
        item = self.dest_table.item(row, 1)
        path_text = item.data(Qt.UserRole) if item else ""
        if not path_text:
            self.update_destination_status(row, "—", "Missing")
            return
        try:
            free = disk_free_bytes(Path(path_text))
            self.update_destination_status(row, human_bytes(free), "Ready")
        except Exception:
            self.update_destination_status(row, "—", "Missing")

    def refresh_destination_space(self):
        for r in range(self.dest_table.rowCount()):
            self.update_single_destination_space(r)

    def destinations(self) -> list[tuple[str, Path, int]]:
        self.refresh_destination_space()
        out = []
        for r in range(self.dest_table.rowCount()):
            dest_item = self.dest_table.item(r, 1)
            role_item = self.dest_table.item(r, 0)
            path_text = dest_item.data(Qt.UserRole) if dest_item else ""
            if path_text:
                out.append((role_item.text().strip() if role_item else f"Destination {r + 1}", Path(str(path_text)).expanduser(), r))
        return out

    def _refresh_destination_profile_note(self):
        if not hasattr(self, "run_note") or self.source_mode.current() != "Multi-Mag":
            return
        dests = []
        for r in range(self.dest_table.rowCount()):
            dest_item = self.dest_table.item(r, 1)
            path_text = dest_item.data(Qt.UserRole) if dest_item else ""
            if path_text:
                dests.append(Path(str(path_text)).expanduser())
        if not dests:
            self.run_note.setText("Multi-Mag uses Settings > Linux Ingest for magazine concurrency and per-magazine threads.")
            return
        try:
            from mediarunner_linux_ingest import derive_ingest_settings_for_destinations
            cfg = self.settings_page.get_config() if self.settings_page and hasattr(self.settings_page, "get_config") else {}
            max_magazines, threads_per_magazine, profiles = derive_ingest_settings_for_destinations(cfg, dests)
            if profiles:
                labels = ", ".join(str(p.get("label") or Path(str(p.get("path", ""))).name or "Destination") for p in profiles)
                self.run_note.setText(f"Destination profile default: {max_magazines} magazines, {threads_per_magazine} thread/mag from {labels}.")
            else:
                self.run_note.setText("No destination profile matched. Multi-Mag will use Settings > Linux Ingest defaults.")
        except Exception:
            self.run_note.setText("Multi-Mag uses Settings > Linux Ingest for magazine concurrency and per-magazine threads.")

    PROGRESS_SCALE = 10000  # fine-grained bar units so byte progress is smooth

    def _start_payload_scan(self):
        """Scan the selected source on a daemon thread and show files + size.

        A generation counter discards results from stale scans (user re-picked
        the source mid-scan). The scan walks the same discovery rules as the
        transfer, so the number shown is the number that will move.
        """
        self._payload_scan_generation += 1
        generation = self._payload_scan_generation
        roots = self.sources()
        if not roots:
            self.signals.payload_preview.emit("")
            return
        valid_roots = [root for root in roots if root.exists() and root.is_dir()]
        if not valid_roots:
            self.signals.payload_preview.emit("")
            return
        self.signals.payload_preview.emit("Payload: scanning…")
        sig = self.signals
        multi_mode = self.source_mode.current() == "Multi-Mag"

        def scan():
            try:
                from mediarunner_transfer import discover_files
                total = 0
                count = 0
                source_count = 0
                for root in valid_roots:
                    source_count += 1
                    for f, _cam, _reel, _clip in discover_files(root, []):
                        if generation != self._payload_scan_generation:
                            return  # superseded by a newer selection
                        try:
                            total += f.stat().st_size
                        except OSError:
                            continue
                        count += 1
                if generation != self._payload_scan_generation:
                    return
                if count:
                    prefix = f"{source_count} magazine{'s' if source_count != 1 else ''} · " if multi_mode else ""
                    sig.payload_preview.emit(f"Payload: {prefix}{count} file{'s' if count != 1 else ''} · {human_bytes(total)}")
                else:
                    sig.payload_preview.emit("Payload: no media files found")
            except Exception as exc:
                if generation == self._payload_scan_generation:
                    sig.payload_preview.emit(f"Payload: scan failed ({exc})")

        threading.Thread(target=scan, daemon=True, name="payload-scan").start()

    def on_progress(self, done:int, total:int):
        self._file_fraction = (done / total) if total else 0.0
        self._progress_text = f"{done} / {total}"
        self._apply_progress()

    def _apply_progress(self):
        # The bar shows whichever is further along: byte progress (smooth,
        # during copy) or file-completion progress (covers verify passes).
        # max() keeps it monotonic across both signals.
        frac = max(getattr(self, "_file_fraction", 0.0), getattr(self, "_byte_fraction", 0.0))
        value = int(min(1.0, frac) * self.PROGRESS_SCALE)
        self.progress.setMaximum(self.PROGRESS_SCALE); self.progress.setValue(value)
        text = getattr(self, "_progress_text", "")
        if text:
            self.progress_label.setText(text)
        if self.dashboard:
            self.dashboard.update_active_progress(text, value, self.PROGRESS_SCALE)

    def on_table_row(self, d: dict):
        if self.dashboard:
            self.dashboard.append_activity(
                f"{d.get('destination','')}: {d.get('status','')} {d.get('file','')}",
                status_color(d.get("status", ""))
            )

    def on_dest_progress(self, event: dict):
        if self.dashboard:
            self.dashboard.update_destination_progress(event)


    def on_job_event(self, event: dict):
        if event.get("type") == "byte_progress":
            self._byte_fraction = float(event.get("fraction", 0.0) or 0.0)
            self._apply_progress()
            return
        if not self.dashboard:
            return
        kind = event.get("type", "")
        if kind == "start":
            self.dashboard.set_active_job("Running", event.get("job", ""), event.get("source", ""), event.get("destination", ""), "0 / 0")
            self.dashboard.reset_destination_progress(event.get("destinations", []))
        elif kind == "report":
            path = str(event.get("path", "") or "").strip()
            if path:
                self._current_reports.append(path)
            self.dashboard.add_recent_output(path)
        elif kind == "throughput":
            self.dashboard.set_transfer_rate(event.get("bytes_per_sec", 0), event.get("phase", "Transfer"))
        elif kind == "complete":
            self.dashboard.set_active_job(event.get("status", "Complete"), event.get("job", ""), event.get("source", ""), event.get("destination", ""), event.get("progress", "Complete"))

    def request_stop(self):
        """Audit fix #4: signal all copy workers to stop at the next chunk."""
        self.cancel_event.set()
        self.status_label.setText("Stopping…")
        log_to(self.console, "Stop requested. Workers exit at the next chunk; partial files stay as .part.", ORANGE)

    def resume_transfer(self):
        """Re-run the job after a stop or errors.

        Resume is a re-run with the same selections: files already verified at
        the destination are skipped by the existing skip-existing-verified
        policy (re-hashed, never trusted blindly), and anything unfinished or
        failed is copied again. This keeps resume checksum-honest — there is
        no state file that could disagree with the disk.
        """
        log_to(self.console, "Resuming job — verified files will be skipped after re-hash; unfinished files re-copy.", ACCENT)
        self.start_transfer()

    def on_finished(self, ok: bool):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        was_cancelled = self.cancel_event.is_set()
        self.maybe_play_finish_sound(ok)
        msg = "Cancelled" if was_cancelled and not ok else ("Complete" if ok else "Finished with errors")
        # Offer Resume after a stop or a run with failures; a clean Complete
        # leaves nothing to resume.
        self.resume_btn.setEnabled(not ok)
        if not ok:
            self.resume_btn.setText("Resume" if was_cancelled else "Retry Failed")
        self.status_label.setText(msg)
        log_to(self.console, msg, GREEN if ok else RED)
        self.signals.job_event.emit({
            "type": "complete",
            "status": msg,
            "job": self._current_job_name,
            "source": self._current_source,
            "destination": self._current_destinations,
            "progress": self.progress_label.text(),
        })
        dispatch_completion_alert({
            "status": msg,
            "workflow": "Offload",
            "job": self._current_job_name,
            "source": self._current_source,
            "destinations": self._current_destinations,
            "progress": self.progress_label.text(),
            "reports": list(dict.fromkeys(self._current_reports)),
        }, self.signals.log.emit)

    def start_multi_magazine_transfer(self, sources: list[Path], dests: list[tuple[str, Path, int]]):
        sources = [Path(p).expanduser() for p in sources if str(p).strip()]
        if not sources or not dests:
            self.status_label.setText("Magazine source and destination required")
            log_to(self.console, "At least one magazine source and one destination are required", RED)
            return
        self.start_btn.setEnabled(False); self.progress.setValue(0)
        self._file_fraction = 0.0
        self._byte_fraction = 0.0
        self._progress_text = ""
        self.cancel_event.clear()
        self.stop_btn.setEnabled(True)
        self.resume_btn.setEnabled(False)
        self.resume_btn.setText("Resume")
        self.status_label.setText("Running")

        sig = self.signals
        cancel_event = self.cancel_event
        job_name = self.job_name.text().strip() or "Multi-Mag Ingest"
        verify_mode = self.verify_seg.current()
        verify = verify_mode != "Off"
        tokens = [] if self.scope_seg.current() == "Entire folder" else build_filter_tokens(self.camera_filter.text(), self.reel_filter.text(), self.clip_filter.text())
        strategy = self.strategy_seg.current()
        if strategy not in ("Cascading", "Simultaneous", "Primary First"):
            strategy = "Primary First"
        cfg = self.settings_page.get_config() if self.settings_page and hasattr(self.settings_page, "get_config") else {}
        try:
            from mediarunner_linux_ingest import derive_ingest_settings_for_destinations
            max_magazines, threads_per_magazine, matched_profiles = derive_ingest_settings_for_destinations(cfg, [dst for _role, dst, _row in dests])
        except Exception:
            max_magazines = max(1, min(24, int(cfg.get("linux_max_simultaneous_magazines", 6) or 6)))
            threads_per_magazine = max(1, min(8, int(cfg.get("linux_threads_per_magazine", 1) or 1)))
            matched_profiles = []
        stage_subfolders = bool(cfg.get("linux_stage_magazine_subfolders", True))
        if matched_profiles:
            profile_names = ", ".join(str(p.get("label") or Path(str(p.get("path", ""))).name or "Destination") for p in matched_profiles)
            log_to(self.console, f"Using destination profile defaults: {max_magazines} magazines, {threads_per_magazine} thread/mag ({profile_names})", ACCENT)
        if len(sources) > 1 and not stage_subfolders:
            log_to(self.console, "Warning: multi-magazine ingest without destination subfolders can collide if card paths match.", YELLOW)

        self._current_job_name = job_name
        self._current_source = f"{len(sources)} magazine source{'s' if len(sources) != 1 else ''}"
        self._current_destinations = ", ".join(str(d[1]) for d in dests)
        self._current_reports = []
        self.signals.job_event.emit({
            "type": "start",
            "job": self._current_job_name,
            "source": self._current_source,
            "destination": self._current_destinations,
            "destinations": [{"role": role, "total": 0} for role, _dst, _row in dests],
        })
        if callable(self.nav_callback):
            self.nav_callback()

        def work():
            try:
                from mediarunner_core import (
                    Manifest,
                    write_html_report,
                    TransferStatus,
                    cleanup_stale_parts,
                    FatalTransferError,
                    TransferCancelledError,
                )
                from mediarunner_transfer import discover_files, transfer_file
                import threading as _t

                def cancelled() -> bool:
                    return cancel_event.is_set()

                def abort_job(reason: str):
                    if not cancel_event.is_set():
                        sig.log.emit(f"FATAL: {reason} - aborting job")
                        cancel_event.set()

                def safe_token(value: str, fallback: str) -> str:
                    value = (value or fallback).strip()
                    cleaned = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in value)
                    return cleaned or fallback

                class ContextManifest:
                    def __init__(self, manifest, method: str, source: Path, destination: Path):
                        self.manifest = manifest
                        self.path = manifest.path
                        self.method = method
                        self.source = str(source)
                        self.destination = str(destination)
                    def write(self, **kwargs):
                        kwargs.setdefault("method", self.method)
                        kwargs.setdefault("source_path", self.source)
                        kwargs.setdefault("destination_path", self.destination)
                        self.manifest.write(**kwargs)

                plans = []
                name_counts: dict[str, int] = {}
                for idx, source in enumerate(sources, 1):
                    if not source.exists() or not source.is_dir():
                        sig.log.emit(f"Skipping missing magazine source: {source}")
                        continue
                    files_meta = discover_files(source, tokens)
                    if not files_meta:
                        sig.log.emit(f"No media files found on magazine source: {source}")
                        continue
                    try:
                        payload = sum(f.stat().st_size for f, _cam, _reel, _clip in files_meta)
                    except Exception:
                        payload = 0
                    base_name = safe_token(source.name or f"magazine_{idx}", f"magazine_{idx}")
                    name_counts[base_name] = name_counts.get(base_name, 0) + 1
                    safe_name = base_name if name_counts[base_name] == 1 else f"{base_name}_{name_counts[base_name]}"
                    plans.append({
                        "source": source,
                        "display_name": source.name or safe_name,
                        "safe_name": safe_name,
                        "files_meta": files_meta,
                        "payload": payload,
                    })

                if not plans:
                    sig.log.emit("No usable magazine sources found.")
                    sig.finished.emit(False)
                    return

                total_files = sum(len(plan["files_meta"]) for plan in plans)
                payload_bytes = sum(int(plan["payload"]) for plan in plans)
                total_work = max(1, total_files * max(1, len(dests)))
                sig.log.emit(f"Multi-magazine ingest: {len(plans)} magazine(s) · {total_files} files · {human_bytes(payload_bytes)}")
                sig.log.emit(f"Magazine concurrency: {max_magazines} · threads per magazine: {threads_per_magazine}")
                if matched_profiles:
                    profile_names = ", ".join(str(p.get("label") or Path(str(p.get("path", ""))).name or "Destination") for p in matched_profiles)
                    sig.log.emit(f"Destination profiles applied: {profile_names}")
                if verify_mode == "Deferred pass":
                    sig.log.emit("Multi-magazine mode uses inline checksum verification to avoid an extra source read.")
                else:
                    sig.log.emit("Verification timing: inline with copy" if verify else "Verification: OFF")

                HEADROOM = 0.02
                available_by_row: dict[int, str] = {}
                volume_required: dict = {}
                volume_free: dict = {}
                volume_rows: dict = {}
                for role, dst, table_row in dests:
                    try:
                        dst.mkdir(parents=True, exist_ok=True)
                        free = disk_free_bytes(dst)
                        free_h = human_bytes(free)
                        available_by_row[table_row] = free_h
                        sig.dest_status.emit(table_row, free_h, "Ready")
                        try:
                            volume_key = os.stat(dst).st_dev
                        except Exception:
                            volume_key = str(dst)
                        volume_required[volume_key] = volume_required.get(volume_key, 0) + payload_bytes
                        volume_free[volume_key] = free
                        volume_rows.setdefault(volume_key, []).append((role, table_row))
                    except Exception as exc:
                        sig.dest_status.emit(table_row, "-", "Missing")
                        sig.log.emit(f"Cannot read available space for {role}: {exc}")
                        sig.finished.emit(False)
                        return

                for volume_key, required in volume_required.items():
                    needed = int(required * (1 + HEADROOM))
                    free = volume_free.get(volume_key, 0)
                    if needed > free:
                        roles_text = ", ".join(role for role, _row in volume_rows.get(volume_key, []))
                        sig.log.emit(
                            f"Insufficient space on volume holding {roles_text}: need {human_bytes(needed)}, available {human_bytes(free)}"
                        )
                        for _role, table_row in volume_rows.get(volume_key, []):
                            sig.dest_status.emit(table_row, human_bytes(free), "Insufficient")
                        sig.finished.emit(False)
                        return

                for _role, dst, _table_row in dests:
                    removed = cleanup_stale_parts(dst)
                    if removed:
                        sig.log.emit(f"Removed {removed} stale .part file(s) under {dst}")

                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_project = safe_token(job_name, "Multi_Mag_Ingest")
                method_label = "Linux Multi-Magazine Ingest"
                progress_roles = []
                for plan in plans:
                    plan_contexts = []
                    for idx, (role, dst_path, table_row) in enumerate(dests, 1):
                        target_root = dst_path / plan["safe_name"] if stage_subfolders else dst_path
                        checksums = dst_path / "_checksums"
                        checksums.mkdir(parents=True, exist_ok=True)
                        target_root.mkdir(parents=True, exist_ok=True)
                        safe_role = safe_token(role, f"destination_{idx}")
                        manifest_csv = checksums / f"MediaRunner_Manifest_{safe_project}_{plan['safe_name']}_{safe_role}_{ts}.csv"
                        base_manifest = Manifest(manifest_csv)
                        context = {
                            "role": role,
                            "progress_role": f"{plan['display_name']} / {role}",
                            "dst": target_root,
                            "root_dst": dst_path,
                            "table_row": table_row,
                            "checksums": checksums,
                            "manifest_csv": manifest_csv,
                            "manifest": ContextManifest(base_manifest, method_label, plan["source"], target_root),
                            "lock": _t.Lock(),
                            "safe_role": safe_role,
                            "source_root": plan["source"],
                        }
                        plan_contexts.append(context)
                        progress_roles.append({"role": context["progress_role"], "total": len(plan["files_meta"])})
                        sig.log.emit(f"{context['progress_role']} manifest: {manifest_csv}")
                    plan["contexts"] = plan_contexts

                sig.job_event.emit({
                    "type": "start",
                    "job": self._current_job_name,
                    "source": self._current_source,
                    "destination": self._current_destinations,
                    "destinations": progress_roles,
                })
                sig.progress.emit(0, total_work)

                aggregate_done = 0
                aggregate_lock = _t.Lock()
                progress_lock = _t.Lock()
                throughput_lock = _t.Lock()
                fail_lock = _t.Lock()
                done_by_role = {item["role"]: 0 for item in progress_roles}
                fail_by_row = {table_row: 0 for _role, _dst, table_row in dests}
                copy_started_at = None
                copied_bytes_done = 0
                last_throughput_emit = [0.0]
                total_payload_all = max(1, payload_bytes * max(1, len(dests)))

                def emit_role_progress(progress_role: str, total: int, status: str = "Running", increment: bool = False):
                    nonlocal aggregate_done
                    with progress_lock:
                        if increment:
                            done_by_role[progress_role] = done_by_role.get(progress_role, 0) + 1
                        done_for_role = done_by_role.get(progress_role, 0)
                    sig.dest_progress.emit({"role": progress_role, "done": done_for_role, "total": total, "status": status})
                    if increment:
                        with aggregate_lock:
                            aggregate_done += 1
                            sig.progress.emit(aggregate_done, total_work)

                def emit_throughput(byte_count: int = 0, phase: str = "Multi-Mag Copy"):
                    nonlocal copied_bytes_done, copy_started_at
                    amount = max(0, int(byte_count or 0))
                    if amount <= 0:
                        return
                    with throughput_lock:
                        now = time.time()
                        if copy_started_at is None:
                            copy_started_at = now
                        copied_bytes_done += amount
                        elapsed = max(0.001, now - copy_started_at)
                        bytes_per_sec = copied_bytes_done / elapsed
                        if (now - last_throughput_emit[0]) < 0.2:
                            return
                        last_throughput_emit[0] = now
                        bytes_snapshot = copied_bytes_done
                    sig.job_event.emit({
                        "type": "throughput",
                        "bytes_per_sec": bytes_per_sec,
                        "bytes_done": bytes_snapshot,
                        "total_bytes": total_payload_all,
                        "phase": phase,
                    })
                    sig.job_event.emit({
                        "type": "byte_progress",
                        "fraction": min(1.0, bytes_snapshot / total_payload_all),
                    })

                def emit_table(role: str, status: str, src_file: Path, cam: str, reel: str, clip: str):
                    try:
                        size = src_file.stat().st_size
                    except Exception:
                        size = 0
                    sig.table_row.emit({
                        "status": status,
                        "destination": role,
                        "camera": cam,
                        "reel": reel,
                        "clip": clip,
                        "file": src_file.name,
                        "size_bytes": size,
                        "src_hash": "See manifest",
                    })

                def write_reports(plan: dict, ctx: dict):
                    manifest_csv = ctx["manifest_csv"]
                    if self.report_html.isChecked():
                        report_name = f"MediaRunner_Report_{safe_project}_{plan['safe_name']}_{ctx['safe_role']}_{ts}.html"
                        report = ctx["checksums"] / report_name
                        try:
                            ok_c, fail_c = write_html_report(
                                manifest_csv,
                                f"{job_name} - {plan['display_name']}",
                                report,
                                source_path=str(ctx["source_root"]),
                                destination_path=str(ctx["dst"]),
                                method_label=method_label,
                            )
                            sig.log.emit(f"Report: {report} ({ok_c} OK / {fail_c} FAIL)")
                            sig.job_event.emit({"type": "report", "path": str(report)})
                        except Exception as exc:
                            sig.log.emit(f"Report failed for {ctx['progress_role']}: {exc}")
                    if self.report_csv.isChecked():
                        sig.log.emit(f"CSV manifest ready: {manifest_csv}")
                        sig.job_event.emit({"type": "report", "path": str(manifest_csv)})

                def copy_stage(plan: dict, ctx: dict, source_root: Path, files_meta: list, stage_threads: int) -> bool:
                    progress_role = ctx["progress_role"]
                    total_for_role = len(files_meta)
                    dst_root = ctx["dst"]
                    ctx["source_root"] = source_root
                    ctx["manifest"].source = str(source_root)
                    ctx["manifest"].destination = str(dst_root)
                    emit_role_progress(progress_role, total_for_role, "Running", increment=False)
                    sig.dest_status.emit(ctx["table_row"], available_by_row.get(ctx["table_row"], "-"), "Running")
                    sig.log.emit(f"{progress_role}: {source_root} -> {dst_root}")
                    files = [(f, dst_root / f.relative_to(source_root), cam, reel, clip) for f, cam, reel, clip in files_meta]
                    fail_count = 0

                    with ThreadPoolExecutor(max_workers=max(1, int(stage_threads))) as pool:
                        futures = {
                            pool.submit(
                                transfer_file,
                                s,
                                d,
                                ctx["manifest"],
                                cam,
                                reel,
                                clip,
                                verify,
                                ctx["lock"],
                                progress_callback=lambda n: emit_throughput(n, "Multi-Mag Copy"),
                                cancel_check=cancelled,
                            ): (s, d, cam, reel, clip)
                            for s, d, cam, reel, clip in files
                            if not cancelled()
                        }
                        for fut in as_completed(futures):
                            s, _d, cam, reel, clip = futures[fut]
                            was_cancelled = False
                            try:
                                ok = bool(fut.result())
                            except TransferCancelledError as exc:
                                ok = False
                                was_cancelled = True
                                with ctx["lock"]:
                                    ctx["manifest"].write(
                                        camera=cam, reel=reel, clip=clip, file=s.name,
                                        size_bytes=s.stat().st_size if s.exists() else 0,
                                        size_human=human_bytes(s.stat().st_size) if s.exists() else "0 B",
                                        status=TransferStatus.CANCELLED,
                                        verification_status=TransferStatus.CANCELLED,
                                        error=str(exc), note="Cancelled during multi-magazine copy",
                                    )
                            except FatalTransferError as exc:
                                ok = False
                                abort_job(str(exc))
                                with ctx["lock"]:
                                    ctx["manifest"].write(
                                        camera=cam, reel=reel, clip=clip, file=s.name,
                                        size_bytes=s.stat().st_size if s.exists() else 0,
                                        size_human=human_bytes(s.stat().st_size) if s.exists() else "0 B",
                                        status="ERROR", note=str(exc),
                                    )
                                sig.log.emit(f"{progress_role} {s.name}: {exc}")
                            except Exception as exc:
                                ok = False
                                with ctx["lock"]:
                                    ctx["manifest"].write(
                                        camera=cam, reel=reel, clip=clip, file=s.name,
                                        size_bytes=s.stat().st_size if s.exists() else 0,
                                        size_human=human_bytes(s.stat().st_size) if s.exists() else "0 B",
                                        status="ERROR", note=str(exc),
                                    )
                                sig.log.emit(f"{progress_role} {s.name}: {exc}")
                            status = "Verified" if ok and verify else ("Copied" if ok else ("Cancelled" if was_cancelled else "FAIL"))
                            if not ok:
                                fail_count += 1
                            emit_table(progress_role, status, s, cam, reel, clip)
                            emit_role_progress(progress_role, total_for_role, "Running", increment=True)
                            if cancelled() and not was_cancelled:
                                was_cancelled = True
                    if fail_count:
                        with fail_lock:
                            fail_by_row[ctx["table_row"]] = fail_by_row.get(ctx["table_row"], 0) + fail_count
                    final_status = "Cancelled" if cancelled() else ("Complete" if fail_count == 0 else "Errors")
                    emit_role_progress(progress_role, total_for_role, final_status, increment=False)
                    write_reports(plan, ctx)
                    return fail_count == 0 and not cancelled()

                def copy_magazine(plan: dict) -> bool:
                    contexts = list(plan.get("contexts") or [])
                    if not contexts:
                        return True
                    sig.log.emit(f"Magazine {plan['display_name']}: starting")
                    if strategy == "Simultaneous" and len(contexts) > 1:
                        per_dest_threads = max(1, int(threads_per_magazine) // len(contexts))
                        ok_map = {}
                        with ThreadPoolExecutor(max_workers=len(contexts)) as pool:
                            futures = {
                                pool.submit(copy_stage, plan, ctx, plan["source"], plan["files_meta"], per_dest_threads): ctx
                                for ctx in contexts
                            }
                            for future in as_completed(futures):
                                ctx = futures[future]
                                try:
                                    ok_map[ctx["progress_role"]] = bool(future.result())
                                except Exception as exc:
                                    ok_map[ctx["progress_role"]] = False
                                    sig.log.emit(f"{ctx['progress_role']} failed: {exc}")
                        return all(ok_map.values()) if ok_map else True

                    current_source_root = plan["source"]
                    current_meta = list(plan["files_meta"])
                    magazine_ok = True
                    for ctx in contexts:
                        if cancelled():
                            magazine_ok = False
                            break
                        ok = copy_stage(plan, ctx, current_source_root, current_meta, threads_per_magazine)
                        magazine_ok = magazine_ok and ok
                        if strategy == "Cascading" and ok:
                            next_meta = [
                                (ctx["dst"] / f.relative_to(current_source_root), cam, reel, clip)
                                for f, cam, reel, clip in current_meta
                            ]
                            current_meta = next_meta
                            current_source_root = ctx["dst"]
                        elif strategy == "Cascading" and not ok:
                            break
                    return magazine_ok

                overall_ok = True
                with ThreadPoolExecutor(max_workers=max(1, min(max_magazines, len(plans)))) as pool:
                    futures = {pool.submit(copy_magazine, plan): plan for plan in plans}
                    for future in as_completed(futures):
                        plan = futures[future]
                        try:
                            plan_ok = bool(future.result())
                        except Exception as exc:
                            plan_ok = False
                            sig.log.emit(f"Magazine {plan.get('display_name', '')} failed: {exc}")
                        overall_ok = overall_ok and plan_ok
                        if cancelled():
                            overall_ok = False

                for role, _dst, table_row in dests:
                    if cancelled():
                        status = "Cancelled"
                    else:
                        status = "Complete" if fail_by_row.get(table_row, 0) == 0 and overall_ok else "Errors"
                    sig.dest_status.emit(table_row, available_by_row.get(table_row, "-"), status)

                sig.finished.emit(bool(overall_ok and not cancelled()))
            except Exception as exc:
                sig.log.emit(f"ERROR: {exc}")
                sig.finished.emit(False)

        self.thread = threading.Thread(target=work, daemon=True); self.thread.start()

    def start_transfer(self):
        source_paths = self.sources()
        dests = self.destinations()
        if not source_paths or not dests:
            self.status_label.setText("Source and destination required")
            log_to(self.console, "Source and at least one destination are required", RED)
            return
        if self.source_mode.current() == "Multi-Mag":
            self.start_multi_magazine_transfer(source_paths, dests)
            return
        src = str(source_paths[0])
        self.start_btn.setEnabled(False); self.progress.setValue(0)
        self._file_fraction = 0.0
        self._byte_fraction = 0.0
        self._progress_text = ""
        self.cancel_event.clear()
        self.stop_btn.setEnabled(True)
        self.resume_btn.setEnabled(False)
        self.resume_btn.setText("Resume")
        self.status_label.setText("Running")
        sig = self.signals
        cancel_event = self.cancel_event
        src_path = Path(src).expanduser()
        job_name = self.job_name.text().strip()
        verify_mode = self.verify_seg.current()
        verify = verify_mode != "Off"
        verify_deferred = verify_mode == "Deferred pass"
        threads = int(self.threads_spin.currentText())
        tokens = [] if self.scope_seg.current() == "Entire folder" else build_filter_tokens(self.camera_filter.text(), self.reel_filter.text(), self.clip_filter.text())
        strategy = self.strategy_seg.current()
        if strategy not in ("Cascading", "Simultaneous", "Primary First"):
            strategy = "Primary First"
        strategy_label = {
            "Simultaneous": "Simultaneous Copy",
            "Primary First": "Primary First Copy",
            "Cascading": "Cascading Copy",
        }.get(strategy, "Transfer")
        self._current_job_name = job_name or "Untitled"
        self._current_source = str(src_path)
        self._current_destinations = ", ".join(str(d[1]) for d in dests)
        self._current_reports = []
        self.signals.job_event.emit({
            "type": "start",
            "job": self._current_job_name,
            "source": self._current_source,
            "destination": self._current_destinations,
            "destinations": [{"role": role, "total": 0} for role, _dst, _row in dests],
        })
        if callable(self.nav_callback):
            self.nav_callback()

        def work():
            try:
                from mediarunner_core import (
                    Manifest,
                    write_html_report,
                    xxh128,
                    TransferStatus,
                    assess_existing_destination,
                    verify_file_pair,
                    verification_result_to_manifest_kwargs,
                    cleanup_stale_parts,
                    FatalTransferError,
                    TransferCancelledError,
                )
                from mediarunner_transfer import discover_files, transfer_file, copy2_with_progress
                import threading as _t

                def cancelled() -> bool:
                    return cancel_event.is_set()

                def abort_job(reason: str):
                    # Fatal conditions (disk full) stop the whole job instead of
                    # failing every remaining file one by one (audit fix #8).
                    if not cancel_event.is_set():
                        sig.log.emit(f"FATAL: {reason} — aborting job")
                        cancel_event.set()

                first_meta = discover_files(src_path, tokens)
                if not first_meta:
                    sig.log.emit("No files found. Check source path or selects.")
                    sig.finished.emit(False)
                    return

                try:
                    payload_bytes = sum(f.stat().st_size for f, _cam, _reel, _clip in first_meta)
                except Exception:
                    payload_bytes = 0

                total_files = len(first_meta)
                work_units_per_file = 2 if verify_deferred else 1
                total_work = max(1, total_files * max(1, len(dests)) * work_units_per_file)
                aggregate_done = 0
                aggregate_lock = _t.Lock()
                progress_lock = _t.Lock()
                throughput_lock = _t.Lock()
                destination_done = {role: 0 for role, _dst, _row in dests}
                copy_started_at = None
                copied_bytes_done = 0

                sig.log.emit(f"Preflight payload: {total_files} files · {human_bytes(payload_bytes)}")
                sig.log.emit(f"Copy strategy: {strategy_label}")
                sig.log.emit("Verification timing: checksum second pass" if verify_deferred else ("Verification timing: inline with copy" if verify else "Verification: OFF"))
                sig.job_event.emit({
                    "type": "start",
                    "job": self._current_job_name,
                    "source": self._current_source,
                    "destination": self._current_destinations,
                    "destinations": [{"role": role, "total": total_files * work_units_per_file} for role, _dst, _row in dests],
                })
                sig.progress.emit(0, total_work)
                for role, _dst, _row in dests:
                    sig.dest_progress.emit({"role": role, "done": 0, "total": total_files * work_units_per_file, "status": "Waiting"})

                # Capacity check (audit fix #8): aggregate the payload per
                # physical volume so two destinations on the same disk are not
                # each checked against the full free space independently, and
                # require a small headroom margin beyond the raw payload.
                HEADROOM = 0.02  # 2% margin for filesystem overhead
                available_by_role = {}
                volume_required: dict = {}
                volume_free: dict = {}
                volume_roles: dict = {}
                for role, dst, table_row in dests:
                    try:
                        dst.mkdir(parents=True, exist_ok=True)
                        free = disk_free_bytes(dst)
                        free_h = human_bytes(free)
                        available_by_role[role] = free_h
                        sig.dest_status.emit(table_row, free_h, "Ready")
                        sig.log.emit(f"{role} available: {free_h}")
                        try:
                            volume_key = os.stat(dst).st_dev
                        except Exception:
                            volume_key = str(dst)
                        volume_required[volume_key] = volume_required.get(volume_key, 0) + payload_bytes
                        volume_free[volume_key] = free
                        volume_roles.setdefault(volume_key, []).append(role)
                    except Exception as exc:
                        sig.dest_status.emit(table_row, "—", "Missing")
                        sig.log.emit(f"Cannot read available space for {role}: {exc}")
                        sig.dest_progress.emit({"role": role, "done": 0, "total": total_files * work_units_per_file, "status": "ERROR"})
                        sig.finished.emit(False)
                        return
                for volume_key, required in volume_required.items():
                    needed = int(required * (1 + HEADROOM))
                    free = volume_free.get(volume_key, 0)
                    if needed > free:
                        roles_text = ", ".join(volume_roles.get(volume_key, []))
                        sig.log.emit(
                            f"Insufficient space on volume holding {roles_text}: "
                            f"need {human_bytes(needed)} (payload + {int(HEADROOM*100)}% headroom"
                            f"{' × ' + str(len(volume_roles.get(volume_key, []))) + ' destinations' if len(volume_roles.get(volume_key, [])) > 1 else ''}), "
                            f"available {human_bytes(free)}"
                        )
                        for role, _dst, table_row in dests:
                            if role in volume_roles.get(volume_key, []):
                                sig.dest_status.emit(table_row, human_bytes(free), "Insufficient")
                                sig.dest_progress.emit({"role": role, "done": 0, "total": total_files * work_units_per_file, "status": "ERROR"})
                        sig.finished.emit(False)
                        return

                # Sweep orphaned .part files from previous cancelled/crashed
                # jobs before writing into the destinations (audit fix #9).
                for _role, dst, _table_row in dests:
                    removed = cleanup_stale_parts(dst)
                    if removed:
                        sig.log.emit(f"Removed {removed} stale .part file(s) under {dst}")

                def safe_token(value: str, fallback: str) -> str:
                    value = (value or fallback).strip()
                    cleaned = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in value)
                    return cleaned or fallback

                class ContextManifest:
                    def __init__(self, manifest, method: str, source: Path, destination: Path):
                        self.manifest = manifest
                        self.path = manifest.path
                        self.method = method
                        self.source = str(source)
                        self.destination = str(destination)
                    def write(self, **kwargs):
                        kwargs.setdefault("method", self.method)
                        kwargs.setdefault("source_path", self.source)
                        kwargs.setdefault("destination_path", self.destination)
                        self.manifest.write(**kwargs)

                def emit_role_progress(role: str, status: str = "Running", increment: bool = False):
                    nonlocal aggregate_done
                    with progress_lock:
                        if increment:
                            destination_done[role] = destination_done.get(role, 0) + 1
                        done_for_role = destination_done.get(role, 0)
                    sig.dest_progress.emit({"role": role, "done": done_for_role, "total": total_files * work_units_per_file, "status": status})
                    if increment:
                        with aggregate_lock:
                            aggregate_done += 1
                            sig.progress.emit(aggregate_done, total_work)

                # Byte-based progress: copy bytes account for this share of the
                # whole job, so the bar moves smoothly during large files
                # instead of jumping once per completed file. File-completion
                # progress fills the remainder (verification passes).
                if verify_deferred:
                    byte_weight = 0.5      # copy pass is half the work units
                elif verify:
                    byte_weight = 0.6      # inline verify still re-reads after copy
                else:
                    byte_weight = 1.0
                total_payload_all = max(1, payload_bytes * max(1, len(dests)))
                last_throughput_emit = [0.0]

                def emit_throughput(byte_count: int = 0, phase: str = "Copy"):
                    # Measures actual bytes written by MediaRunner during copy passes.
                    # Skipped files and checksum-only reads are deliberately excluded.
                    nonlocal copied_bytes_done, copy_started_at
                    amount = max(0, int(byte_count or 0))
                    if amount <= 0:
                        return
                    with throughput_lock:
                        now = time.time()
                        if copy_started_at is None:
                            copy_started_at = now
                        copied_bytes_done += amount
                        elapsed = max(0.001, now - copy_started_at)
                        bytes_per_sec = copied_bytes_done / elapsed
                        # Throttle UI events to ~5/s; every 8 MB chunk firing two
                        # queued Qt events floods the event loop on fast media.
                        if (now - last_throughput_emit[0]) < 0.2:
                            return
                        last_throughput_emit[0] = now
                        bytes_snapshot = copied_bytes_done
                    sig.job_event.emit({
                        "type": "throughput",
                        "bytes_per_sec": bytes_per_sec,
                        "bytes_done": bytes_snapshot,
                        "total_bytes": total_payload_all,
                        "phase": phase,
                    })
                    sig.job_event.emit({
                        "type": "byte_progress",
                        "fraction": min(1.0, (bytes_snapshot / total_payload_all) * byte_weight),
                    })

                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_project = safe_token(job_name, "No_Project")
                contexts = []
                for idx, (role, dst_path, table_row) in enumerate(dests, 1):
                    dst_path.mkdir(parents=True, exist_ok=True)
                    checksums = dst_path / "_checksums"
                    checksums.mkdir(parents=True, exist_ok=True)
                    safe_role = safe_token(role, f"destination_{idx}")
                    manifest_csv = checksums / f"MediaRunner_Manifest_{safe_project}_{safe_role}_{ts}.csv"
                    planned_source = src_path
                    if strategy == "Cascading" and idx > 1:
                        planned_source = dests[idx - 2][1]
                    base_manifest = Manifest(manifest_csv)
                    context = ContextManifest(base_manifest, strategy_label, planned_source, dst_path)
                    contexts.append({
                        "idx": idx,
                        "role": role,
                        "dst": dst_path,
                        "table_row": table_row,
                        "checksums": checksums,
                        "manifest_csv": manifest_csv,
                        "manifest": context,
                        "lock": _t.Lock(),
                        "method": strategy_label,
                        "source_root": planned_source,
                        "safe_role": safe_role,
                    })
                    sig.log.emit(f"{role} manifest: {manifest_csv}")

                def emit_table(role: str, status: str, src_file: Path, cam: str, reel: str, clip: str):
                    try:
                        size = src_file.stat().st_size
                    except Exception:
                        size = 0
                    sig.table_row.emit({
                        "status": status,
                        "destination": role,
                        "camera": cam,
                        "reel": reel,
                        "clip": clip,
                        "file": src_file.name,
                        "size_bytes": size,
                        "src_hash": "See manifest",
                    })

                def write_not_attempted(ctx: dict, file_path: Path, cam: str, reel: str, clip: str, note: str):
                    role = ctx["role"]
                    try:
                        size = file_path.stat().st_size
                    except Exception:
                        size = 0
                    with ctx["lock"]:
                        ctx["manifest"].write(
                            camera=cam, reel=reel, clip=clip, file=file_path.name,
                            size_bytes=size, size_human=human_bytes(size),
                            status="ERROR", note=note,
                        )
                    emit_table(role, "ERROR", file_path, cam, reel, clip)
                    emit_role_progress(role, "ERROR", increment=True)

                def write_reports(ctx: dict):
                    role = ctx["role"]
                    manifest_csv = ctx["manifest_csv"]
                    if self.report_html.isChecked():
                        report_name = f"MediaRunner_Report_{safe_project}_{ctx['safe_role']}_{ts}.html"
                        report = ctx["checksums"] / report_name
                        try:
                            ok_c, fail_c = write_html_report(
                                manifest_csv, job_name or role or "No Project", report,
                                source_path=str(ctx["source_root"]),
                                destination_path=str(ctx["dst"]),
                                method_label=ctx["method"],
                            )
                            sig.log.emit(f"Report: {report} ({ok_c} OK / {fail_c} FAIL)")
                            sig.job_event.emit({"type": "report", "path": str(report)})
                        except Exception as exc:
                            sig.log.emit(f"Report failed for {role}: {exc}")
                    if self.report_csv.isChecked():
                        sig.log.emit(f"CSV manifest ready: {manifest_csv}")
                        sig.job_event.emit({"type": "report", "path": str(manifest_csv)})

                def copy_stage(ctx: dict, source_root: Path, files_meta: list, stage_threads: int) -> bool:
                    role = ctx["role"]
                    dst_path = ctx["dst"]
                    ctx["source_root"] = source_root
                    ctx["manifest"].source = str(source_root)
                    ctx["manifest"].destination = str(dst_path)
                    emit_role_progress(role, "Running", increment=False)
                    sig.status.emit(f"{role}: running")
                    sig.log.emit(f"{role}: {source_root} → {dst_path}")
                    files = [(f, dst_path / f.relative_to(source_root), cam, reel, clip) for f, cam, reel, clip in files_meta]
                    fail_count = 0

                    def copy_payload_only(src_file: Path, dst_file: Path):
                        if cancelled():
                            return TransferCancelledError(f"Cancelled before copying {src_file.name}")
                        dst_file.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            if dst_file.exists():
                                existing = assess_existing_destination(src_file, dst_file)
                                if existing.status == TransferStatus.SKIPPED_EXISTING_VERIFIED:
                                    return existing
                            copy2_with_progress(src_file, dst_file, progress_callback=lambda n: emit_throughput(n, "Copy"), cancel_check=cancelled)
                            return None
                        except FatalTransferError as exc:
                            abort_job(str(exc))
                            return exc
                        except Exception as exc:
                            return exc

                    def verify_payload_pair(src_file: Path, dst_file: Path, cam: str, reel: str, clip: str) -> bool:
                        try:
                            size = src_file.stat().st_size
                            size_h = human_bytes(size)
                        except Exception:
                            size = 0
                            size_h = "0 B"
                        try:
                            if not dst_file.exists():
                                raise FileNotFoundError(f"Missing destination file: {dst_file}")
                            compare = verify_file_pair(src_file, dst_file)
                            with ctx["lock"]:
                                ctx["manifest"].write(**verification_result_to_manifest_kwargs(
                                    compare,
                                    camera=cam,
                                    reel=reel,
                                    clip=clip,
                                    file=src_file.name,
                                    size_bytes=size,
                                    size_human=size_h,
                                    note="Deferred verification pass" if compare.status == TransferStatus.VERIFIED else compare.note,
                                ))
                            return compare.status == TransferStatus.VERIFIED
                        except Exception as exc:
                            with ctx["lock"]:
                                ctx["manifest"].write(
                                    camera=cam, reel=reel, clip=clip, file=src_file.name,
                                    size_bytes=size, size_human=size_h,
                                    status=TransferStatus.FAILED, verification_status=TransferStatus.FAILED, error=str(exc), note=str(exc),
                                )
                            return False

                    if verify_deferred:
                        # First pass: copy only, so hashing does not compete with the media transfer.
                        sig.log.emit(f"{role}: copy pass")
                        copied_ok: set[Path] = set()
                        with ThreadPoolExecutor(max_workers=max(1, int(stage_threads))) as pool:
                            futures = {
                                pool.submit(copy_payload_only, s, d): (s, d, cam, reel, clip)
                                for s, d, cam, reel, clip in files
                            }
                            for fut in as_completed(futures):
                                s, _d, cam, reel, clip = futures[fut]
                                try:
                                    result = fut.result()
                                except Exception as exc:
                                    result = exc
                                if isinstance(result, TransferCancelledError):
                                    fail_count += 1
                                    with ctx["lock"]:
                                        ctx["manifest"].write(
                                            camera=cam, reel=reel, clip=clip, file=s.name,
                                            size_bytes=s.stat().st_size if s.exists() else 0,
                                            size_human=human_bytes(s.stat().st_size) if s.exists() else "0 B",
                                            status=TransferStatus.CANCELLED,
                                            verification_status=TransferStatus.CANCELLED,
                                            error=str(result),
                                            note="Cancelled during copy pass",
                                        )
                                    emit_table(role, TransferStatus.CANCELLED, s, cam, reel, clip)
                                elif isinstance(result, Exception):
                                    fail_count += 1
                                    with ctx["lock"]:
                                        ctx["manifest"].write(
                                            camera=cam, reel=reel, clip=clip, file=s.name,
                                            size_bytes=s.stat().st_size if s.exists() else 0,
                                            size_human=human_bytes(s.stat().st_size) if s.exists() else "0 B",
                                            status=TransferStatus.FAILED,
                                            verification_status=TransferStatus.FAILED,
                                            error=str(result),
                                            note=f"Copy failed before deferred verification: {result}",
                                        )
                                    emit_table(role, TransferStatus.FAILED, s, cam, reel, clip)
                                elif result is not None and result.status == TransferStatus.SKIPPED_EXISTING_VERIFIED:
                                    with ctx["lock"]:
                                        ctx["manifest"].write(**verification_result_to_manifest_kwargs(
                                            result,
                                            camera=cam,
                                            reel=reel,
                                            clip=clip,
                                            file=s.name,
                                        ))
                                    emit_table(role, result.status, s, cam, reel, clip)
                                else:
                                    copied_ok.add(s)
                                    emit_table(role, TransferStatus.COPIED, s, cam, reel, clip)
                                emit_role_progress(role, "Copying", increment=True)

                        # Second pass: checksum source/destination pairs and write the manifest rows.
                        sig.log.emit(f"{role}: checksum verification pass")
                        with ThreadPoolExecutor(max_workers=max(1, int(stage_threads))) as pool:
                            futures = {
                                pool.submit(verify_payload_pair, s, d, cam, reel, clip): (s, d, cam, reel, clip)
                                for s, d, cam, reel, clip in files
                                if s in copied_ok and not cancelled()
                            }
                            for fut in as_completed(futures):
                                s, _d, cam, reel, clip = futures[fut]
                                try:
                                    ok = bool(fut.result())
                                except Exception as exc:
                                    ok = False
                                    sig.log.emit(f"{role} deferred verify error for {s.name}: {exc}")
                                status = TransferStatus.VERIFIED if ok else TransferStatus.MISMATCH
                                if not ok:
                                    fail_count += 1
                                emit_table(role, status, s, cam, reel, clip)
                                emit_role_progress(role, "Verifying", increment=True)
                    else:
                        with ThreadPoolExecutor(max_workers=max(1, int(stage_threads))) as pool:
                            futures = {
                                pool.submit(transfer_file, s, d, ctx["manifest"], cam, reel, clip, verify, ctx["lock"], progress_callback=lambda n: emit_throughput(n, "Copy"), cancel_check=cancelled): (s, d, cam, reel, clip)
                                for s, d, cam, reel, clip in files
                            }
                            for fut in as_completed(futures):
                                s, _d, cam, reel, clip = futures[fut]
                                was_cancelled = False
                                try:
                                    ok = bool(fut.result())
                                except TransferCancelledError as exc:
                                    ok = False
                                    was_cancelled = True
                                    with ctx["lock"]:
                                        ctx["manifest"].write(
                                            camera=cam, reel=reel, clip=clip, file=s.name,
                                            size_bytes=s.stat().st_size if s.exists() else 0,
                                            size_human=human_bytes(s.stat().st_size) if s.exists() else "0 B",
                                            status=TransferStatus.CANCELLED,
                                            verification_status=TransferStatus.CANCELLED,
                                            error=str(exc), note="Cancelled during copy",
                                        )
                                except FatalTransferError as exc:
                                    ok = False
                                    abort_job(str(exc))
                                    with ctx["lock"]:
                                        ctx["manifest"].write(
                                            camera=cam, reel=reel, clip=clip, file=s.name,
                                            size_bytes=s.stat().st_size if s.exists() else 0,
                                            size_human=human_bytes(s.stat().st_size) if s.exists() else "0 B",
                                            status="ERROR", note=str(exc),
                                        )
                                    sig.log.emit(f"{role} {s.name}: {exc}")
                                except Exception as exc:
                                    ok = False
                                    with ctx["lock"]:
                                        ctx["manifest"].write(
                                            camera=cam, reel=reel, clip=clip, file=s.name,
                                            size_bytes=s.stat().st_size if s.exists() else 0,
                                            size_human=human_bytes(s.stat().st_size) if s.exists() else "0 B",
                                            status="ERROR", note=str(exc),
                                        )
                                    sig.log.emit(f"{role} {s.name}: {exc}")
                                status = "Verified" if ok and verify else ("Copied" if ok else ("Cancelled" if was_cancelled else "FAIL"))
                                if not ok:
                                    fail_count += 1
                                emit_table(role, status, s, cam, reel, clip)
                                emit_role_progress(role, "Running", increment=True)
                    final_status = "Complete" if fail_count == 0 else "Errors"
                    sig.dest_status.emit(ctx["table_row"], available_by_role.get(role, "—"), final_status)
                    emit_role_progress(role, final_status, increment=False)
                    write_reports(ctx)
                    return fail_count == 0

                def run_parallel(ctx_subset: list[dict], source_root: Path, files_meta: list) -> bool:
                    if not ctx_subset:
                        return True
                    per_dest_threads = max(1, int(threads) // max(1, len(ctx_subset)))
                    sig.log.emit(f"Concurrent outputs: {len(ctx_subset)} · {per_dest_threads} copy thread(s) per destination")
                    ok_map = {}
                    with ThreadPoolExecutor(max_workers=len(ctx_subset)) as pool:
                        futures = {pool.submit(copy_stage, ctx, source_root, files_meta, per_dest_threads): ctx for ctx in ctx_subset}
                        for fut in as_completed(futures):
                            ctx = futures[fut]
                            try:
                                ok_map[ctx["role"]] = bool(fut.result())
                            except Exception as exc:
                                ok_map[ctx["role"]] = False
                                sig.log.emit(f"{ctx['role']} failed: {exc}")
                                emit_role_progress(ctx["role"], "ERROR", increment=False)
                    return all(ok_map.values()) if ok_map else True

                overall_ok = True

                if strategy == "Simultaneous":
                    # Source verifies independently against every selected destination.
                    overall_ok = run_parallel(contexts, src_path, first_meta)

                elif strategy == "Primary First":
                    # Primary is verified first. Remaining destinations then copy from the source.
                    primary_ok = copy_stage(contexts[0], src_path, first_meta, threads)
                    overall_ok = primary_ok
                    if primary_ok and len(contexts) > 1:
                        overall_ok = run_parallel(contexts[1:], src_path, first_meta) and overall_ok
                    elif not primary_ok and len(contexts) > 1:
                        sig.log.emit("Primary failed verification; remaining destinations were not attempted.")
                        for ctx in contexts[1:]:
                            emit_role_progress(ctx["role"], "ERROR", increment=False)
                            for f, cam, reel, clip in first_meta:
                                write_not_attempted(ctx, f, cam, reel, clip, "Primary failed verification; copy not attempted")
                            write_reports(ctx)

                else:
                    sig.log.emit("Cascading verification chain: Source → Primary → Secondary → Third as selected")
                    for ctx in contexts:
                        emit_role_progress(ctx["role"], "Waiting", increment=False)

                    if verify_deferred:
                        # With deferred verification, cascading runs safely as leg-by-leg copy+verify.
                        # This preserves checksum truth: Source→Primary verifies before Primary→Secondary starts.
                        sig.log.emit("Deferred checksum selected: cascading will verify each leg before starting the next leg.")
                        current_source_root = src_path
                        current_meta = list(first_meta)
                        remaining_contexts = list(contexts)
                        for ctx in contexts:
                            if not overall_ok:
                                remaining_contexts.remove(ctx)
                                emit_role_progress(ctx["role"], "ERROR", increment=False)
                                for f, cam, reel, clip in current_meta:
                                    write_not_attempted(ctx, f, cam, reel, clip, "Previous cascade leg failed verification; copy not attempted")
                                write_reports(ctx)
                                continue
                            leg_ok = copy_stage(ctx, current_source_root, current_meta, threads)
                            overall_ok = overall_ok and leg_ok
                            if leg_ok:
                                current_meta = [
                                    (ctx["dst"] / f.relative_to(current_source_root), cam, reel, clip)
                                    for f, cam, reel, clip in current_meta
                                ]
                                current_source_root = ctx["dst"]
                    else:
                        # Inline verification keeps the faster per-file pipeline: a verified upstream file unlocks the next leg.
                        def cascade_one(meta: tuple[Path, str, str, str]) -> bool:
                            f, cam, reel, clip = meta
                            rel = f.relative_to(src_path)
                            upstream_file = f
                            file_ok = True
                            for ctx in contexts:
                                role = ctx["role"]
                                dst_file = ctx["dst"] / rel
                                if not file_ok:
                                    write_not_attempted(ctx, upstream_file, cam, reel, clip, "Previous cascade leg failed verification; copy not attempted")
                                    continue
                                if cancelled():
                                    with ctx["lock"]:
                                        ctx["manifest"].write(
                                            camera=cam, reel=reel, clip=clip, file=upstream_file.name,
                                            status=TransferStatus.CANCELLED,
                                            verification_status=TransferStatus.CANCELLED,
                                            note="Cancelled before cascade leg",
                                        )
                                    file_ok = False
                                    continue
                                emit_role_progress(role, "Running", increment=False)
                                try:
                                    ok = bool(transfer_file(upstream_file, dst_file, ctx["manifest"], cam, reel, clip, verify, ctx["lock"], progress_callback=lambda n: emit_throughput(n, "Copy"), cancel_check=cancelled))
                                except TransferCancelledError as exc:
                                    ok = False
                                    with ctx["lock"]:
                                        ctx["manifest"].write(
                                            camera=cam, reel=reel, clip=clip, file=upstream_file.name,
                                            size_bytes=upstream_file.stat().st_size if upstream_file.exists() else 0,
                                            size_human=human_bytes(upstream_file.stat().st_size) if upstream_file.exists() else "0 B",
                                            status=TransferStatus.CANCELLED,
                                            verification_status=TransferStatus.CANCELLED,
                                            error=str(exc), note="Cancelled during cascade copy",
                                        )
                                except FatalTransferError as exc:
                                    ok = False
                                    abort_job(str(exc))
                                    with ctx["lock"]:
                                        ctx["manifest"].write(
                                            camera=cam, reel=reel, clip=clip, file=upstream_file.name,
                                            size_bytes=upstream_file.stat().st_size if upstream_file.exists() else 0,
                                            size_human=human_bytes(upstream_file.stat().st_size) if upstream_file.exists() else "0 B",
                                            status="ERROR", note=str(exc),
                                        )
                                    sig.log.emit(f"{role} {upstream_file.name}: {exc}")
                                except Exception as exc:
                                    ok = False
                                    with ctx["lock"]:
                                        ctx["manifest"].write(
                                            camera=cam, reel=reel, clip=clip, file=upstream_file.name,
                                            size_bytes=upstream_file.stat().st_size if upstream_file.exists() else 0,
                                            size_human=human_bytes(upstream_file.stat().st_size) if upstream_file.exists() else "0 B",
                                            status="ERROR", note=str(exc),
                                        )
                                    sig.log.emit(f"{role} {upstream_file.name}: {exc}")
                                status = "Verified" if ok and verify else ("Copied" if ok else "FAIL")
                                emit_table(role, status, upstream_file, cam, reel, clip)
                                emit_role_progress(role, "Running", increment=True)
                                if not ok:
                                    file_ok = False
                                else:
                                    upstream_file = dst_file
                            return file_ok

                        cascade_fail = 0
                        with ThreadPoolExecutor(max_workers=max(1, threads)) as pool:
                            futures = {pool.submit(cascade_one, meta): meta[0] for meta in first_meta}
                            for fut in as_completed(futures):
                                try:
                                    if not bool(fut.result()):
                                        cascade_fail += 1
                                except Exception as exc:
                                    cascade_fail += 1
                                    sig.log.emit(f"Cascade error: {exc}")
                        overall_ok = cascade_fail == 0
                        for ctx in contexts:
                            role = ctx["role"]
                            # Any role with less than full completion means an upstream leg prevented some files.
                            expected = total_files * work_units_per_file
                            final_status = "Complete" if destination_done.get(role, 0) == expected and overall_ok else ("Complete" if destination_done.get(role, 0) == expected else "Errors")
                            if final_status == "Complete" and cascade_fail:
                                final_status = "Errors"
                            sig.dest_status.emit(ctx["table_row"], available_by_role.get(role, "—"), final_status)
                            emit_role_progress(role, final_status, increment=False)
                            write_reports(ctx)

                sig.finished.emit(overall_ok)
            except Exception as exc:
                sig.log.emit(f"ERROR: {exc}")
                sig.finished.emit(False)

        self.thread = threading.Thread(target=work, daemon=True); self.thread.start()


class FTPPage(QWidget, ActivityLogMixin):
    def __init__(self, settings_page=None, dashboard=None, nav_callback=None, sound_settings_page=None):
        super().__init__(); self.settings_page = settings_page; self.sound_settings_page = sound_settings_page; self.dashboard = dashboard; self.nav_callback = nav_callback; self.signals = WorkerSignals(); self.thread = None; self.detected = {}; self.last_scan_checked = 0; self._scan_running = False; self.cancel_event = threading.Event()
        self._current_job_name = ""
        self._current_source = ""
        self._current_destinations = ""
        self._current_reports = []
        self._build_ui()
        self.signals.log.connect(self.handle_log)
        self.signals.status.connect(self.status_label.setText)
        self.signals.progress.connect(self.on_progress)
        self.signals.scan_result.connect(self.on_scan_result)
        self.signals.table_row.connect(lambda d: add_row(self.table, [d.get(k,"") for k in ["status","destination","camera","ip","reel","clip","file","note"]]))
        self.signals.dest_status.connect(self.update_destination_status)
        self.signals.job_event.connect(self.on_job_event)
        self.signals.finished.connect(self.on_finished)

    def maybe_play_finish_sound(self, ok: bool):
        sound_page = getattr(self, "sound_settings_page", None) or self.settings_page
        if ok and sound_page and hasattr(sound_page, "finish_sound_enabled") and sound_page.finish_sound_enabled():
            play_finish_chime()

    def handle_log(self, text: str):
        if hasattr(self, "console"):
            log_to(self.console, text)
        if getattr(self, "details_window", None) is not None and self.details_window.isVisible():
            self.details_window.append_plain(text)
        if self.dashboard:
            self.dashboard.append_activity(text)

    def _build_ui(self):
        root = QVBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(14)

        # Resizable top boxes: the user can drag the handle between FTP Download
        # and Destinations. Avoid hard minimums that can push the right edge offscreen.
        top_splitter = QSplitter(Qt.Horizontal)
        top_splitter.setChildrenCollapsible(False)
        top_splitter.setHandleWidth(8)

        f,v = panel("FTP Download")
        f.setMinimumWidth(300)
        f.setMaximumHeight(250)
        f.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        g = QGridLayout(); g.setHorizontalSpacing(10); g.setVerticalSpacing(8)
        g.setColumnStretch(1, 0)
        g.setColumnStretch(3, 1)
        self.reel = QLineEdit(); self.reel.setPlaceholderText("007"); self.reel.setFixedWidth(116)
        self.clips = QLineEdit(); self.clips.setPlaceholderText("60-64, 71, 83"); self.clips.setMinimumWidth(170)
        self.entire_reel = QCheckBox("Entire reel")
        self.skip_offline = QCheckBox("Detect online cameras before download"); self.skip_offline.setChecked(True)
        self.use_last = QCheckBox("Use previous camera scan"); self.use_last.setChecked(True)
        self.verify_with_mhl = QCheckBox("Verify FTP with ASC MHL when available"); self.verify_with_mhl.setChecked(True)
        self.verify_with_mhl.setToolTip("Uses camera-generated ASC MHL files inside .RDC folders to verify downloaded FTP media. If no MHL is found, FTP files remain Downloaded/local-checksummed unless strict mode is enabled.")
        self.require_mhl = QCheckBox("Require ASC MHL for FTP verification"); self.require_mhl.setChecked(False)
        self.require_mhl.setToolTip("If enabled, missing MHL files or missing MHL entries are treated as verification failures instead of local-only downloads.")
        g.addWidget(label("Reel"),0,0); g.addWidget(self.reel,0,1)
        g.addWidget(label("Clips"),0,2); g.addWidget(self.clips,0,3)
        checks = QVBoxLayout(); checks.setSpacing(5); checks.setContentsMargins(0, 4, 0, 0)
        checks.addWidget(self.entire_reel)
        checks.addWidget(self.skip_offline)
        checks.addWidget(self.use_last)
        checks.addWidget(self.verify_with_mhl)
        checks.addWidget(self.require_mhl)
        g.addLayout(checks,1,0,1,4)
        self.verify_with_mhl.toggled.connect(self._update_mhl_controls)
        self._update_mhl_controls(self.verify_with_mhl.isChecked())
        v.addLayout(g)
        top_splitter.addWidget(f)

        dest_panel, dv = panel("Destinations")
        dest_panel.setMinimumWidth(360)
        dest_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.dest_table = QTableWidget(0, 4)
        self.dest_table.setHorizontalHeaderLabels(["Role", "Destination", "Available", "Status"])
        self.dest_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.dest_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.dest_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.dest_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.dest_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.dest_table.setAlternatingRowColors(True)
        self.dest_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.dest_table.setMinimumHeight(132)
        self.dest_table.setMaximumHeight(190)
        dv.addWidget(self.dest_table)
        drow = QHBoxLayout()
        select_dest = QPushButton("Select Destination…"); select_dest.setObjectName("primary"); select_dest.clicked.connect(self.select_destination)
        rem = QPushButton("Remove Selected"); rem.clicked.connect(self.remove_destination)
        drow.addWidget(select_dest); drow.addWidget(rem); drow.addStretch()
        dv.addLayout(drow)
        top_splitter.addWidget(dest_panel)
        top_splitter.setStretchFactor(0, 1)
        top_splitter.setStretchFactor(1, 2)
        top_splitter.setSizes([520, 820])

        root.addWidget(top_splitter, 0)

        ctrl = QHBoxLayout()
        self.detect_btn = QPushButton("Detect Cameras"); self.detect_btn.clicked.connect(self.scan)
        self.run_btn = QPushButton("Start Download"); self.run_btn.setObjectName("primary"); self.run_btn.clicked.connect(self.run)
        self.stop_btn = QPushButton("Stop"); self.stop_btn.setEnabled(False); self.stop_btn.clicked.connect(self.request_stop)
        self.status_label = label("Ready", "muted")
        self.progress_label = label("—", "muted"); self.progress = QProgressBar(); self.progress.setFixedHeight(8)
        ctrl.addWidget(self.detect_btn); ctrl.addWidget(self.run_btn); ctrl.addWidget(self.stop_btn); ctrl.addWidget(self.status_label); ctrl.addStretch(); ctrl.addWidget(self.progress_label); ctrl.addWidget(self.progress,1)
        root.addLayout(ctrl)
        self.table = make_table(["Status","Destination","Camera","IP","Reel","Clip","File","Note"]); root.addWidget(self.table,1)
        self.add_activity_log(root)

    def update_selects_visibility(self):
        show = self.scope_selects.isChecked()
        if hasattr(self, "selects_box"):
            self.selects_box.setVisible(show)
        if not show:
            self.camera_filter.clear()
            self.reel_filter.clear()
            self.clip_filter.clear()

    def _update_mhl_controls(self, enabled: bool):
        self.require_mhl.setEnabled(bool(enabled))

    def next_destination_role(self) -> str:
        roles = ["Primary", "Secondary", "Third", "Fourth", "Fifth", "Sixth"]
        n = self.dest_table.rowCount()
        return roles[n] if n < len(roles) else f"Destination {n + 1}"

    def add_destination(self, path: str):
        path_obj = Path(path).expanduser()
        row = self.dest_table.rowCount()
        role = self.next_destination_role()
        self.dest_table.insertRow(row)

        role_item = QTableWidgetItem(role)
        dest_item = QTableWidgetItem(path_obj.name or str(path_obj))
        dest_item.setData(Qt.UserRole, str(path_obj))
        avail_item = QTableWidgetItem("—")
        status_item = QTableWidgetItem("Ready")
        status_item.setForeground(QColor(status_color("Ready")))

        self.dest_table.setItem(row, 0, role_item)
        self.dest_table.setItem(row, 1, dest_item)
        self.dest_table.setItem(row, 2, avail_item)
        self.dest_table.setItem(row, 3, status_item)
        self.update_single_destination_space(row)

    def select_destination(self):
        d = QFileDialog.getExistingDirectory(self, "Select FTP Destination", str(Path.home()))
        if d:
            self.add_destination(d)

    def remove_destination(self):
        rows = sorted({i.row() for i in self.dest_table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.dest_table.removeRow(r)
        self.renumber_destination_roles()

    def renumber_destination_roles(self):
        roles = ["Primary", "Secondary", "Third", "Fourth", "Fifth", "Sixth"]
        for r in range(self.dest_table.rowCount()):
            role = roles[r] if r < len(roles) else f"Destination {r + 1}"
            item = self.dest_table.item(r, 0) or QTableWidgetItem()
            item.setText(role)
            self.dest_table.setItem(r, 0, item)

    def update_destination_status(self, row: int, available: str, status: str):
        if 0 <= row < self.dest_table.rowCount():
            avail_item = QTableWidgetItem(available)
            self.dest_table.setItem(row, 2, avail_item)
            status_item = QTableWidgetItem(status)
            status_item.setForeground(QColor(status_color(status)))
            self.dest_table.setItem(row, 3, status_item)

    def update_single_destination_space(self, row: int):
        item = self.dest_table.item(row, 1)
        path_text = item.data(Qt.UserRole) if item else ""
        if not path_text:
            self.update_destination_status(row, "—", "Missing")
            return
        try:
            free = disk_free_bytes(Path(path_text))
            self.update_destination_status(row, human_bytes(free), "Ready")
        except Exception:
            self.update_destination_status(row, "—", "Missing")

    def refresh_destination_space(self):
        for r in range(self.dest_table.rowCount()):
            self.update_single_destination_space(r)

    def destinations(self) -> list[tuple[str, Path, int]]:
        self.refresh_destination_space()
        out = []
        for r in range(self.dest_table.rowCount()):
            dest_item = self.dest_table.item(r, 1)
            role_item = self.dest_table.item(r, 0)
            path_text = dest_item.data(Qt.UserRole) if dest_item else ""
            if path_text:
                out.append((role_item.text().strip() if role_item else f"Destination {r + 1}", Path(str(path_text)).expanduser(), r))
        return out

    def cfg(self):
        from mediarunner_core import load_network_config
        if self.settings_page and hasattr(self.settings_page, "get_config"):
            return self.settings_page.get_config()
        return load_network_config()

    def on_progress(self,d,t):
        self.progress.setMaximum(max(t,1)); self.progress.setValue(d); self.progress_label.setText(f"{d} / {t}")
        if self.dashboard:
            self.dashboard.update_active_progress(f"{d} / {t}", d, t)

    def on_finished(self, ok):
        self.detect_btn.setEnabled(True); self.run_btn.setEnabled(True); self.stop_btn.setEnabled(False)
        if self._scan_running:
            self._scan_running = False
            if not ok:
                self.status_label.setText("Scan failed")
            return
        was_cancelled = self.cancel_event.is_set()
        msg = "Cancelled" if was_cancelled and not ok else ("Complete" if ok else "Finished with errors")
        self.status_label.setText(msg)
        self.maybe_play_finish_sound(ok)
        if self.dashboard:
            self.dashboard.set_active_job(msg, "FTP Download", "Camera Array", "", self.progress_label.text())
        dispatch_completion_alert({
            "status": msg,
            "workflow": "FTP Camera Array",
            "job": self._current_job_name,
            "source": self._current_source,
            "destinations": self._current_destinations,
            "progress": self.progress_label.text(),
            "reports": list(dict.fromkeys(self._current_reports)),
        }, self.signals.log.emit)

    def on_job_event(self, event: dict):
        kind = str(event.get("type", "") or "")
        if kind == "ftp_file_progress":
            text = str(event.get("text", "") or "").strip()
            if text:
                self.status_label.setText(text)
                if self.dashboard:
                    self.dashboard.update_active_progress(text)
        elif kind == "report":
            path = str(event.get("path", "") or "").strip()
            if path:
                self._current_reports.append(path)
            if path and self.dashboard:
                self.dashboard.add_recent_output(path)

    def on_scan_result(self, res):
        if isinstance(res, dict) and res.get("mode") == "ftp_array_scan":
            statuses = dict(res.get("results") or {})
            cameras = dict(res.get("cameras") or {})
            checked = int(res.get("checked") or len(statuses))
        else:
            statuses = dict(res or {})
            cameras = dict(self.cfg().get("cameras", {}))
            checked = len(statuses)

        online_cameras = {cam: ip for cam, ip in cameras.items() if statuses.get(cam)}
        self.detected = {cam: True for cam in online_cameras}
        self.last_scan_checked = checked
        self.table.setRowCount(0)
        online = len(online_cameras)
        self.status_label.setText(f"{online} online" + (f" / {checked} checked" if checked else ""))
        for cam, ip in sorted(online_cameras.items()):
            self.signals.table_row.emit({"status": "ONLINE", "camera": cam, "ip": ip, "note": "Live scan"})
        if checked and not online:
            self.handle_log("Camera scan found no online cameras; saved offline IP addresses were not shown.")

    def scan(self):
        cfg = self.cfg(); cams = cfg.get("cameras",{})
        self._scan_running = True
        self.detect_btn.setEnabled(False); self.table.setRowCount(0); self.status_label.setText("Scanning")
        sig = self.signals
        def work():
            try:
                from mediarunner_core import scan_cameras
                res = scan_cameras(cams, port=cfg.get("ftp_port",21), timeout=cfg.get("ftp_timeout",2.0), max_workers=cfg.get("scan_threads",24))
                sig.scan_result.emit({"mode": "ftp_array_scan", "results": res, "cameras": cams, "checked": len(cams)})
                sig.finished.emit(True)
            except Exception as exc:
                sig.log.emit(f"ERROR: {exc}"); sig.finished.emit(False)
        self.thread = threading.Thread(target=work, daemon=True); self.thread.start()

    def request_stop(self):
        self.cancel_event.set()
        self.status_label.setText("Stopping…")
        self.signals.log.emit("Stop requested. Current file may finish before workers exit.")

    def run(self):
        self.cancel_event.clear()
        self.stop_btn.setEnabled(True)
        dests = self.destinations()
        reel = self.reel.text().strip(); clips = self.clips.text().strip()
        if self.entire_reel.isChecked() and not clips:
            clips = "0-999"  # broad match pattern for selected reel; camera may not have all clips
        if not dests or not reel or not clips:
            self.status_label.setText("Destination, reel, and clips required"); return

        # Validate clip selection up front (audit fix #14) instead of letting
        # a malformed range raise inside a worker thread.
        try:
            from mediarunner_core import parse_clip_numbers
            parse_clip_numbers(clips)
        except ValueError as exc:
            self.status_label.setText(str(exc))
            log_to(self.console, str(exc), RED)
            return

        for role, dst, row in dests:
            try:
                free = disk_free_bytes(dst)
                self.signals.dest_status.emit(row, human_bytes(free), "Ready" if free > 0 else "Unavailable")
                if free <= 0:
                    self.status_label.setText(f"{role} unavailable")
                    return
                if free < 1024**3:
                    log_to(self.console, f"Warning: {role} has only {human_bytes(free)} available", YELLOW)
            except Exception as exc:
                self.signals.dest_status.emit(row, "—", "Missing")
                self.status_label.setText(f"{role} unavailable")
                log_to(self.console, f"{role} unavailable: {exc}", RED)
                return

        cfg = self.cfg(); cams = dict(cfg.get("cameras",{}))
        use_previous_scan = self.use_last.isChecked() and self.last_scan_checked > 0
        if use_previous_scan:
            cams = {c:ip for c,ip in cams.items() if self.detected.get(c)}
            if not cams:
                self.stop_btn.setEnabled(False)
                self.status_label.setText("No online cameras in previous scan")
                log_to(self.console, "No online cameras in previous scan. Run Detect Cameras again after updating the camera network.", YELLOW)
                return
        online_only = self.skip_offline.isChecked() and not use_previous_scan
        self._current_job_name = f"FTP Reel {reel}"
        self._current_source = "Camera Array"
        self._current_destinations = ", ".join(str(d[1]) for d in dests)
        self._current_reports = []
        self.run_btn.setEnabled(False); self.detect_btn.setEnabled(False); self.table.setRowCount(0); self.status_label.setText("Running")
        if self.dashboard:
            self.dashboard.set_active_job("Running", f"FTP Reel {reel}", "Camera Array", ", ".join(str(d[1]) for d in dests), "0 / 0")
        if callable(self.nav_callback):
            self.nav_callback()
        sig = self.signals
        def safe_token(value: str, fallback: str) -> str:
            value = (value or fallback).strip()
            cleaned = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in value)
            return cleaned or fallback

        def work():
            try:
                from mediarunner_core import Manifest
                from mediarunner_ftp import pull_reel_clips
                overall_fail = 0
                total_dest = len(dests)
                for dest_idx, (role, out_path, row) in enumerate(dests, 1):
                    sig.status.emit(f"{role}: running")
                    sig.log.emit(f"Destination: {role} → {out_path}")
                    out_path.mkdir(parents=True, exist_ok=True)
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    manifest_csv = out_path / "_manifests" / f"MediaRunner_FTP_{safe_token(role, 'Destination')}_{ts}.csv"
                    manifest = Manifest(manifest_csv)
                    ok, fail = pull_reel_clips(
                        reel, clips, out_path, manifest,
                        cameras=cams, online_only=online_only,
                        port=cfg.get("ftp_port",21),
                        timeout=cfg.get("ftp_timeout",2.0),
                        scan_threads=cfg.get("scan_threads",24),
                        progress_callback=lambda d,t,di=dest_idx: sig.progress.emit((di-1)*max(t,1)+d, max(total_dest*max(t,1), 1)),
                        cancel_event=self.cancel_event,
                        log_callback=sig.log.emit,
                        file_progress_callback=lambda event, role_name=role: sig.job_event.emit({
                            "type": "ftp_file_progress",
                            "text": f"{role_name}: {event.get('file', 'Downloading')} {int(round((float(event.get('done', 0) or 0) / max(float(event.get('total', 0) or 0), 1.0)) * 100)) if float(event.get('total', 0) or 0) > 0 else 0}%",
                        }),
                        destination_role=role,
                        report_callback=lambda report_path: sig.job_event.emit({"type": "report", "path": str(report_path)}),
                        verify_with_mhl=self.verify_with_mhl.isChecked(),
                        require_mhl=self.verify_with_mhl.isChecked() and self.require_mhl.isChecked(),
                    )
                    if manifest_csv.exists():
                        with open(manifest_csv, newline="") as f:
                            for csv_row in csv.DictReader(f):
                                if (csv_row.get("method") or csv_row.get("stage")) == "FTP":
                                    csv_row["ip"] = cams.get(csv_row.get("camera",""), "")
                                    csv_row["destination"] = role
                                    sig.table_row.emit(csv_row)
                    sig.log.emit(f"{role} manifest: {manifest_csv}")
                    sig.log.emit(f"{role} summary: {ok} downloaded/skipped, {fail} failed/missing")
                    overall_fail += fail
                sig.finished.emit(overall_fail == 0)
            except Exception as exc:
                sig.log.emit(f"ERROR: {exc}"); sig.finished.emit(False)
        self.thread = threading.Thread(target=work, daemon=True); self.thread.start()



class RedWirelessPage(QWidget, ActivityLogMixin):
    """RED Wireless Ingest: RCP2 diagnostics + FTPS media download."""
    def __init__(self, settings_page=None, dashboard=None, nav_callback=None, sound_settings_page=None):
        super().__init__()
        self.settings_page = settings_page
        self.sound_settings_page = sound_settings_page
        self.dashboard = dashboard
        self.nav_callback = nav_callback
        self.signals = WorkerSignals()
        self.thread = None
        self.identity = None
        self.discovery = None
        self._current_job_name = ""
        self._current_source = ""
        self._current_destinations = ""
        self._current_reports = []
        self._build_ui()
        self.signals.log.connect(self.handle_log)
        self.signals.status.connect(self.status_label.setText)
        self.signals.progress.connect(self.on_progress)
        self.signals.scan_result.connect(self.on_scan_result)
        self.signals.table_row.connect(lambda d: add_row(self.table, [d.get(k,"") for k in ["status","destination","camera","ip","reel","clip","file","note"]]))
        self.signals.dest_status.connect(self.update_destination_status)
        self.signals.dest_progress.connect(lambda d: self.dashboard.update_destination_progress(d) if self.dashboard else None)
        self.signals.job_event.connect(self.on_job_event)
        self.signals.finished.connect(self.on_finished)

    def maybe_play_finish_sound(self, ok: bool):
        sound_page = getattr(self, "sound_settings_page", None) or self.settings_page
        if ok and sound_page and hasattr(sound_page, "finish_sound_enabled") and sound_page.finish_sound_enabled():
            play_finish_chime()

    def handle_log(self, text: str):
        if hasattr(self, "console"):
            log_to(self.console, text)
        if getattr(self, "details_window", None) is not None and self.details_window.isVisible():
            self.details_window.append_plain(text)
        if self.dashboard:
            self.dashboard.append_activity(text)

    def cfg(self):
        from mediarunner_core import load_network_config
        if self.settings_page and hasattr(self.settings_page, "get_config"):
            return self.settings_page.get_config()
        return load_network_config()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        intro = label("RCP2 scans. FTPS/FTP transfers. Wired Ethernet is preferred for critical offloads.", "muted")
        intro.setWordWrap(True)
        intro.setMaximumHeight(24)
        root.addWidget(intro)

        top_row = QHBoxLayout()
        top_row.setSpacing(12)

        camera_panel, camv = panel("Camera & Discovery")
        camera_panel.setMinimumWidth(440)
        camera_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        main_camera_row = QHBoxLayout()
        main_camera_row.setSpacing(14)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        form.setColumnMinimumWidth(0, 82)
        form.setColumnMinimumWidth(2, 54)
        form.setColumnStretch(1, 2)
        form.setColumnStretch(3, 3)

        self.camera_ip = QLineEdit()
        self.camera_ip.setPlaceholderText("Scan if blank")
        self.camera_ip.setMinimumWidth(160)
        self.reel = QLineEdit()
        self.reel.setPlaceholderText("007")
        self.reel.setMaximumWidth(128)
        self.clips = QLineEdit()
        self.clips.setPlaceholderText("60-64, 71, 83")
        self.clips.setMinimumWidth(170)
        self.entire_reel = QCheckBox("Entire reel")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(21)
        self.port.setMaximumWidth(110)
        self.protocol = QComboBox()
        self.protocol.addItems(["FTPS Explicit TLS", "Plain FTP"])
        self.protocol.setMinimumWidth(170)
        self.verify = QCheckBox("Verify XXH128")
        self.verify.setChecked(True)
        self.second_pass = QCheckBox("Second-pass checksum")
        self.second_pass.setChecked(True)
        self.strict_wifi = QCheckBox("Strict wireless workflow")
        self.strict_wifi.setChecked(True)

        form.addWidget(label("Camera IP"), 0, 0)
        form.addWidget(self.camera_ip, 0, 1, 1, 3)
        form.addWidget(label("Reel"), 1, 0)
        form.addWidget(self.reel, 1, 1)
        form.addWidget(label("Clips"), 1, 2)
        form.addWidget(self.clips, 1, 3)
        form.addWidget(label("Port"), 2, 0)
        form.addWidget(self.port, 2, 1)
        form.addWidget(label("Protocol"), 2, 2)
        form.addWidget(self.protocol, 2, 3)
        form.addWidget(self.entire_reel, 3, 0, 1, 2)
        form.addWidget(self.verify, 3, 2, 1, 2)
        form.addWidget(self.second_pass, 4, 0, 1, 2)
        form.addWidget(self.strict_wifi, 4, 2, 1, 2)
        main_camera_row.addLayout(form, 1)

        actions = QVBoxLayout()
        actions.setSpacing(10)
        self.detect_btn = QPushButton("Detect Camera")
        self.detect_btn.setMinimumHeight(40)
        self.detect_btn.setMinimumWidth(150)
        self.detect_btn.setMaximumWidth(185)
        self.detect_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.detect_btn.setToolTip("Leave Camera IP blank to scan active Wi‑Fi/local networks for RED cameras.")
        self.detect_btn.clicked.connect(self.detect_camera)
        self.discover_btn = QPushButton("Discover Clips")
        self.discover_btn.setMinimumHeight(40)
        self.discover_btn.setMinimumWidth(150)
        self.discover_btn.setMaximumWidth(185)
        self.discover_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.discover_btn.clicked.connect(self.discover_clips)
        actions.addWidget(self.detect_btn)
        actions.addWidget(self.discover_btn)
        actions.addStretch()
        main_camera_row.addLayout(actions)
        camv.addLayout(main_camera_row)

        note = label("Enable RED Media Access in-camera. Blank IP scans Wi‑Fi/local networks.", "muted")
        note.setWordWrap(True)
        note.setMaximumHeight(28)
        camv.addWidget(note)

        diag_title = label("Diagnostics", "section_title")
        diag_title.setMaximumHeight(18)
        camv.addWidget(diag_title)
        diag_grid = QGridLayout()
        diag_grid.setHorizontalSpacing(16)
        diag_grid.setVerticalSpacing(4)
        diag_grid.setColumnStretch(0, 1)
        diag_grid.setColumnStretch(1, 1)
        self.rcp_status = label("RCP2: Not tested", "muted")
        self.ftps_status = label("Media: Not tested", "muted")
        self.camera_summary = label("Camera: —", "muted")
        self.discovery_summary = label("Matched: —", "muted")
        for w in (self.rcp_status, self.ftps_status, self.camera_summary, self.discovery_summary):
            w.setWordWrap(True)
            w.setMinimumHeight(20)
            w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        diag_grid.addWidget(self.rcp_status, 0, 0)
        diag_grid.addWidget(self.ftps_status, 0, 1)
        diag_grid.addWidget(self.camera_summary, 1, 0)
        diag_grid.addWidget(self.discovery_summary, 1, 1)
        camv.addLayout(diag_grid)
        top_row.addWidget(camera_panel, 5)

        dest_panel, dstv = panel("Destinations")
        dest_panel.setMinimumWidth(300)
        dest_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.dest_table = QTableWidget(0, 4)
        self.dest_table.setHorizontalHeaderLabels(["Role", "Destination", "Available", "Status"])
        self.dest_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.dest_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.dest_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.dest_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.dest_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.dest_table.setMinimumHeight(150)
        self.dest_table.setMaximumHeight(225)
        dstv.addWidget(self.dest_table)
        drow = QHBoxLayout()
        select_dest = QPushButton("Select Destination…")
        select_dest.setObjectName("primary")
        select_dest.setMinimumHeight(38)
        select_dest.setMaximumWidth(205)
        select_dest.clicked.connect(self.select_destination)
        rem = QPushButton("Remove")
        rem.setMinimumHeight(38)
        rem.setMaximumWidth(120)
        rem.clicked.connect(self.remove_destination)
        drow.addWidget(select_dest)
        drow.addWidget(rem)
        drow.addStretch()
        dstv.addLayout(drow)
        top_row.addWidget(dest_panel, 3)
        root.addLayout(top_row)

        ctrl = QHBoxLayout()
        self.run_btn = QPushButton("Start Wireless Ingest")
        self.run_btn.setObjectName("primary")
        self.run_btn.setMinimumHeight(42)
        self.run_btn.setMaximumWidth(220)
        self.run_btn.clicked.connect(self.run)
        self.run_btn.setEnabled(False)
        self.status_label = label("Ready", "muted")
        self.progress_label = label("—", "muted")
        self.progress = QProgressBar()
        self.progress.setFixedHeight(8)
        ctrl.addWidget(self.run_btn)
        ctrl.addWidget(self.status_label)
        ctrl.addStretch()
        ctrl.addWidget(self.progress_label)
        ctrl.addWidget(self.progress, 1)
        root.addLayout(ctrl)

        self.table = make_table(["Status", "Destination", "Camera", "IP", "Reel", "Clip", "File", "Note"])
        self.table.setMinimumHeight(190)
        root.addWidget(self.table, 1)
        self.add_activity_log(root)

    def next_destination_role(self) -> str:
        roles = ["Primary", "Secondary", "Third", "Fourth", "Fifth", "Sixth"]
        n = self.dest_table.rowCount()
        return roles[n] if n < len(roles) else f"Destination {n + 1}"

    def add_destination(self, path: str):
        path_obj = Path(path).expanduser()
        row = self.dest_table.rowCount()
        role = self.next_destination_role()
        self.dest_table.insertRow(row)
        role_item = QTableWidgetItem(role)
        dest_item = QTableWidgetItem(path_obj.name or str(path_obj)); dest_item.setData(Qt.UserRole, str(path_obj))
        avail_item = QTableWidgetItem("—")
        status_item = QTableWidgetItem("Ready"); status_item.setForeground(QColor(status_color("Ready")))
        self.dest_table.setItem(row, 0, role_item)
        self.dest_table.setItem(row, 1, dest_item)
        self.dest_table.setItem(row, 2, avail_item)
        self.dest_table.setItem(row, 3, status_item)
        self.update_single_destination_space(row)

    def select_destination(self):
        d = QFileDialog.getExistingDirectory(self, "Select RED Wireless Destination", str(Path.home()))
        if d:
            self.add_destination(d)

    def remove_destination(self):
        rows = sorted({i.row() for i in self.dest_table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.dest_table.removeRow(r)
        for r in range(self.dest_table.rowCount()):
            role = ["Primary", "Secondary", "Third", "Fourth", "Fifth", "Sixth"][r] if r < 6 else f"Destination {r + 1}"
            self.dest_table.setItem(r, 0, QTableWidgetItem(role))

    def update_destination_status(self, row: int, available: str, status: str):
        if row < 0 or row >= self.dest_table.rowCount():
            return
        self.dest_table.setItem(row, 2, QTableWidgetItem(str(available)))
        item = QTableWidgetItem(str(status)); item.setForeground(QColor(status_color(str(status))))
        self.dest_table.setItem(row, 3, item)

    def update_single_destination_space(self, row: int):
        item = self.dest_table.item(row, 1)
        path_text = item.data(Qt.UserRole) if item else ""
        if not path_text:
            self.update_destination_status(row, "—", "Missing"); return
        try:
            free = disk_free_bytes(Path(path_text))
            self.update_destination_status(row, human_bytes(free), "Ready")
        except Exception:
            self.update_destination_status(row, "—", "Missing")

    def refresh_destination_space(self):
        for r in range(self.dest_table.rowCount()):
            self.update_single_destination_space(r)

    def destinations(self) -> list[tuple[str, Path, int]]:
        self.refresh_destination_space()
        out = []
        for r in range(self.dest_table.rowCount()):
            dest_item = self.dest_table.item(r, 1)
            role_item = self.dest_table.item(r, 0)
            path_text = dest_item.data(Qt.UserRole) if dest_item else ""
            if path_text:
                out.append((role_item.text().strip() if role_item else f"Destination {r + 1}", Path(str(path_text)).expanduser(), r))
        return out

    def on_progress(self, d, t):
        self.progress.setMaximum(max(t,1)); self.progress.setValue(d); self.progress_label.setText(f"{d} / {t}")
        if self.dashboard:
            self.dashboard.update_active_progress(f"{d} / {t}", d, t)

    def on_job_event(self, event: dict):
        if not self.dashboard:
            event = dict(event or {})
        kind = str(event.get("type") or "")
        if kind == "detect_complete":
            self.detect_btn.setEnabled(True)
            return
        if kind == "discovery_complete":
            self.discover_btn.setEnabled(True)
            return
        if not self.dashboard:
            return
        if "bytes_per_sec" in event:
            self.dashboard.set_transfer_rate(event.get("bytes_per_sec", 0), event.get("phase", "RED Wireless"))
        if "report_path" in event:
            path = str(event.get("report_path") or "").strip()
            if path:
                self._current_reports.append(path)
            self.dashboard.latest_report_path = path
            self.dashboard.open_report_btn.setEnabled(bool(self.dashboard.latest_report_path))

    def on_finished(self, ok):
        self.detect_btn.setEnabled(True); self.discover_btn.setEnabled(True); self.run_btn.setEnabled(bool(self.discovery and getattr(self.discovery, "ok", False)))
        msg = "Complete" if ok else "Finished with errors"
        self.status_label.setText(msg)
        self.maybe_play_finish_sound(ok)
        if self.dashboard:
            self.dashboard.set_active_job(msg, "RED Wireless Ingest", self.camera_ip.text().strip(), ", ".join(str(d[1]) for d in self.destinations()), self.progress_label.text())
        dispatch_completion_alert({
            "status": msg,
            "workflow": "RED Wireless Ingest",
            "job": self._current_job_name,
            "source": self._current_source,
            "destinations": self._current_destinations,
            "progress": self.progress_label.text(),
            "reports": list(dict.fromkeys(self._current_reports)),
        }, self.signals.log.emit)

    def on_scan_result(self, payload: dict):
        mode = str(payload.get("mode") or "")
        if mode in {"identity", "identity_scan"}:
            result = payload.get("result")
            candidates = list(payload.get("candidates") or [])
            self.identity = result
            if result and getattr(result, "ok", False):
                self.camera_ip.setText(str(getattr(result, "host", "") or self.camera_ip.text()).strip())
                summary = f"Camera: {getattr(result, 'camera_name', '') or 'RED camera'}"
                serial = getattr(result, "serial_number", "")
                version = getattr(result, "camera_version", "")
                extras = "  ".join(x for x in [f"Serial {serial}" if serial else "", version] if x)
                self.rcp_status.setText("RCP2: Connected")
                self.camera_summary.setText(summary + (f" — {extras}" if extras else ""))
                if mode == "identity_scan":
                    extra = f" ({len(candidates)} found)" if len(candidates) > 1 else ""
                    self.status_label.setText(f"Camera detected{extra}")
                    self.table.setRowCount(0)
                    for cam in candidates:
                        self.signals.table_row.emit({
                            "status": "DISCOVERED",
                            "destination": "RCP2",
                            "camera": getattr(cam, "camera_name", "") or "RED camera",
                            "ip": getattr(cam, "host", ""),
                            "reel": "",
                            "clip": "",
                            "file": getattr(cam, "serial_number", ""),
                            "note": getattr(cam, "camera_version", "")
                        })
                    if len(candidates) > 1:
                        self.handle_log("Multiple RED cameras found; populated detected camera list. Using first detected camera unless another IP is selected manually.")
                else:
                    self.status_label.setText("Camera detected")
                self.handle_log(self.camera_summary.text())
            else:
                err = getattr(result, "error", "No RED camera found") if result else "No RED camera found"
                self.rcp_status.setText(f"RCP2: Failed — {err}")
                self.status_label.setText("No RED cameras found" if mode == "identity_scan" else "RCP2 detection failed")
        elif mode == "discovery":
            result = payload.get("result")
            self.discovery = result
            self.table.setRowCount(0)
            if result and getattr(result, "ok", False):
                self.ftps_status.setText(f"Media Access: Passed via {getattr(result, 'protocol', 'FTPS')}")
                self.discovery_summary.setText(f"Matched media: {result.file_count} file(s), {human_bytes(result.total_bytes)}")
                self.status_label.setText("Discovery passed")
                self.run_btn.setEnabled(True)
                for f in (result.files or [])[:500]:
                    self.signals.table_row.emit({"status":"DISCOVERED", "destination":"—", "camera":f.camera, "ip":result.host, "reel":f.reel, "clip":f.clip, "file":f.file_name, "note":human_bytes(f.size_bytes)})
                if result.file_count > 500:
                    self.handle_log(f"Showing first 500 of {result.file_count} discovered files.")
            else:
                err = getattr(result, "error", "Discovery failed") if result else "Discovery failed"
                self.ftps_status.setText(f"Media Access: Failed — {err}")
                self.discovery_summary.setText("Matched media: none")
                self.status_label.setText("Discovery failed")
                self.run_btn.setEnabled(False)

    def _clip_spec_for_ui(self) -> str:
        clips = self.clips.text().strip()
        if self.entire_reel.isChecked() and not clips:
            return "ALL"
        return clips

    def _credentials(self):
        cfg = self.cfg()
        return str(cfg.get("ftp_user", "ftp1") or "ftp1"), str(cfg.get("ftp_pass", "12345678") or "12345678")

    def detect_camera(self):
        host = self.camera_ip.text().strip()
        self.detect_btn.setEnabled(False)
        self.run_btn.setEnabled(False)
        self.rcp_status.setText("RCP2: Scanning…" if not host else "RCP2: Connecting…")
        self.status_label.setText("Scanning for RED cameras" if not host else "Detecting camera")
        sig = self.signals
        def work():
            try:
                cfg = self.cfg()
                timeout = float(cfg.get("ftp_timeout", 6.0) or 6.0)
                rcp2_port = int(cfg.get("rcp2_port", 9998) or 9998)
                if host:
                    from mediarunner_red_wireless import detect_red_camera_identity
                    result = detect_red_camera_identity(host, port=rcp2_port, timeout=timeout)
                    sig.scan_result.emit({"mode":"identity", "result":result})
                else:
                    from mediarunner_red_wireless import scan_red_cameras
                    sig.log.emit(f"Camera IP blank; scanning active Wi-Fi/local networks for RED RCP2 cameras on port {rcp2_port}.")
                    candidates = scan_red_cameras(port=rcp2_port, identity_timeout=min(2.0, max(1.0, timeout / 3.0)), log_callback=lambda m: sig.log.emit(m))
                    result = candidates[0] if candidates else None
                    sig.scan_result.emit({"mode":"identity_scan", "result":result, "candidates":candidates})
            except Exception as exc:
                sig.log.emit(f"RCP2 SCAN ERROR: {exc}")
                sig.scan_result.emit({"mode":"identity_scan", "result":None, "candidates":[]})
            finally:
                sig.job_event.emit({"type": "detect_complete"})
        self.thread = threading.Thread(target=work, daemon=True); self.thread.start()

    def discover_clips(self):
        host = self.camera_ip.text().strip(); reel = self.reel.text().strip(); clips = self._clip_spec_for_ui()
        if not host or not reel or not clips:
            self.status_label.setText("Camera IP, reel, and clips required"); return
        self.discover_btn.setEnabled(False); self.run_btn.setEnabled(False); self.status_label.setText("Discovering media"); self.table.setRowCount(0)
        user, password = self._credentials(); use_ftps = self.protocol.currentIndex() == 0; port = int(self.port.value())
        sig = self.signals
        def work():
            try:
                from mediarunner_red_wireless import discover_red_wireless_media
                result = discover_red_wireless_media(host=host, reel=reel, clip_spec=clips, username=user, password=password, port=port, timeout=float(self.cfg().get("ftp_timeout", 6.0) or 6.0), use_ftps=use_ftps, log_callback=lambda m: sig.log.emit(m))
                sig.scan_result.emit({"mode":"discovery", "result":result})
            except Exception as exc:
                sig.log.emit(f"DISCOVERY ERROR: {exc}")
            finally:
                sig.job_event.emit({"type": "discovery_complete"})
        self.thread = threading.Thread(target=work, daemon=True); self.thread.start()

    def run(self):
        if not self.discovery or not getattr(self.discovery, "ok", False):
            self.status_label.setText("Run Discover Clips first"); return
        dests = self.destinations()
        if not dests:
            self.status_label.setText("Destination required"); return
        required = int(getattr(self.discovery, "total_bytes", 0) or 0)
        for role, dst, row in dests:
            try:
                free = disk_free_bytes(dst)
                self.signals.dest_status.emit(row, human_bytes(free), "Ready" if free >= required else "Insufficient")
                if free < required:
                    self.status_label.setText(f"{role} has insufficient space")
                    return
            except Exception as exc:
                self.signals.dest_status.emit(row, "—", "Missing"); self.status_label.setText(f"{role} unavailable"); self.handle_log(str(exc)); return
        self.run_btn.setEnabled(False); self.detect_btn.setEnabled(False); self.discover_btn.setEnabled(False); self.table.setRowCount(0); self.status_label.setText("Running")
        host = self.camera_ip.text().strip(); user, password = self._credentials(); use_ftps = self.protocol.currentIndex() == 0; port = int(self.port.value())
        self._current_job_name = f"RED Wireless Reel {getattr(self.discovery, 'reel', '')}"
        self._current_source = host
        self._current_destinations = ", ".join(str(d[1]) for d in dests)
        self._current_reports = []
        if self.dashboard:
            self.dashboard.set_active_job("Running", f"RED Wireless Reel {getattr(self.discovery, 'reel', '')}", host, ", ".join(str(d[1]) for d in dests), "0 / 0")
            self.dashboard.reset_destination_progress([{"role": role, "total": max(1, self.discovery.file_count)} for role, _dst, _row in dests])
        if callable(self.nav_callback):
            self.nav_callback()
        sig = self.signals
        def work():
            try:
                from mediarunner_red_wireless import run_red_wireless_ingest
                result = run_red_wireless_ingest(
                    host=host,
                    discovery=self.discovery,
                    destinations=dests,
                    username=user,
                    password=password,
                    port=port,
                    timeout=float(self.cfg().get("ftp_timeout", 6.0) or 6.0),
                    use_ftps=use_ftps,
                    verify=self.verify.isChecked(),
                    second_pass=self.second_pass.isChecked(),
                    progress_callback=lambda d,t,name: sig.progress.emit(d,t),
                    rate_callback=lambda bps,phase: sig.job_event.emit({"bytes_per_sec":bps,"phase":phase}),
                    row_callback=lambda row: sig.table_row.emit(row),
                    dest_progress_callback=lambda event: sig.dest_progress.emit(event),
                    log_callback=lambda m: sig.log.emit(m),
                )
                if result.report_paths:
                    sig.job_event.emit({"report_path": result.report_paths[0]})
                sig.log.emit(f"RED Wireless summary: {result.ok_count} OK / {result.fail_count} failed")
                sig.finished.emit(result.ok)
            except Exception as exc:
                sig.log.emit(f"ERROR: {exc}"); sig.finished.emit(False)
        self.thread = threading.Thread(target=work, daemon=True); self.thread.start()


class MetadataPage(QWidget, ActivityLogMixin):
    def __init__(self):
        super().__init__(); self.signals = WorkerSignals(); self.thread = None; self.master_csv_path = None; self.report_html_path = None
        self._build_ui()
        self.signals.log.connect(lambda t: log_to(self.console, t))
        self.signals.status.connect(self.status_label.setText)
        self.signals.progress.connect(self.on_progress)
        self.signals.table_row.connect(lambda d: add_row(self.table, [d.get(k,"") for k in ["status","camera_family","file","ltc_in","ltc_out","fps","resolution","tool","warnings"]]))
        self.signals.master_csv.connect(self.on_master_csv)
        self.signals.report_html.connect(self.on_report_html)
        self.signals.finished.connect(self.on_finished)

    def _build_ui(self):
        root = QVBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(14)
        root.addWidget(label("Extracts REDline, FFmpeg/ffprobe, and ExifTool metadata into a normalized MediaRunner CSV/report.", "meta_desc"))
        f,v = panel("Metadata Extraction")
        g = QGridLayout(); g.setSpacing(10)
        w,self.source_root = path_picker("Source folder or clip")
        w2,self.output_dir = path_picker(str(Path.home()/"Desktop"/"MediaRunner_Metadata"))
        self.source_type = QComboBox(); self.source_type.addItems(["Auto Detect", "RED / R3D", "Generic MOV / MP4 / MXF"])
        self.metadata_type = QComboBox(); self.metadata_type.addItems(["Timecode Summary", "Clip Metadata", "RED Per-Frame / Lens Metadata", "RED Gyro / IMU Metadata", "Raw Metadata Export"])
        self.keep_per_clip = QCheckBox("Keep RED per-clip sidecars")
        self.save_raw = QCheckBox("Save raw ffprobe / ExifTool / REDline outputs")
        self.keep_per_clip.setChecked(False); self.save_raw.setChecked(False)
        help_text = label("Timecode Summary is the fast default. Clip Metadata adds broader fields. RED Per-Frame / Lens, RED Gyro / IMU, and Raw Export can create larger sidecars.", "muted")
        help_text.setWordWrap(True)
        g.addWidget(label("Source"),0,0); g.addWidget(w,0,1,1,3)
        g.addWidget(label("Output Folder"),1,0); g.addWidget(w2,1,1,1,3)
        g.addWidget(label("Source Type"),2,0); g.addWidget(self.source_type,2,1)
        g.addWidget(label("Metadata Type"),2,2); g.addWidget(self.metadata_type,2,3)
        g.addWidget(self.keep_per_clip,3,1); g.addWidget(self.save_raw,3,2,1,2)
        g.addWidget(help_text,4,0,1,4)
        v.addLayout(g)
        page_splitter = QSplitter(Qt.Vertical)
        page_splitter.setChildrenCollapsible(False)
        page_splitter.setHandleWidth(8)
        page_splitter.addWidget(f)
        ctrl = QHBoxLayout()
        self.run_btn = QPushButton("Extract Metadata"); self.run_btn.setObjectName("primary"); self.run_btn.clicked.connect(self.run)
        self.open_btn = QPushButton("Open Master CSV"); self.open_btn.setEnabled(False); self.open_btn.clicked.connect(self.open_csv)
        self.open_report_btn = QPushButton("Open Report"); self.open_report_btn.setEnabled(False); self.open_report_btn.clicked.connect(self.open_report)
        self.status_label = label("Ready", "muted")
        self.progress_label = label("—", "muted"); self.progress = QProgressBar(); self.progress.setFixedHeight(8)
        ctrl.addWidget(self.run_btn); ctrl.addWidget(self.open_btn); ctrl.addWidget(self.open_report_btn); ctrl.addWidget(self.status_label); ctrl.addStretch(); ctrl.addWidget(self.progress_label); ctrl.addWidget(self.progress,1)
        root.addLayout(ctrl)
        self.table = make_table(["Status","Family","File","TC In","TC Out","FPS","Resolution","Tool","Warnings"])
        page_splitter.addWidget(self.table)
        page_splitter.setStretchFactor(0, 0)
        page_splitter.setStretchFactor(1, 1)
        page_splitter.setSizes([260, 520])
        root.addWidget(page_splitter, 1)
        self.add_activity_log(root)

    def on_progress(self,d,t): self.progress.setMaximum(max(t,1)); self.progress.setValue(d); self.progress_label.setText(f"{d} / {t}")
    def on_master_csv(self,p): self.master_csv_path = Path(p); self.open_btn.setEnabled(self.master_csv_path.exists())
    def on_report_html(self,p): self.report_html_path = Path(p); self.open_report_btn.setEnabled(self.report_html_path.exists())
    def on_finished(self, ok): self.run_btn.setEnabled(True); self.status_label.setText("Complete" if ok else "Finished with errors")
    def _open_path(self, p: Path | None):
        if p and p.exists():
            if sys.platform == "darwin": subprocess.Popen(["open", str(p)])
            elif sys.platform == "win32": subprocess.Popen(["cmd","/c","start","",str(p)], shell=False)
            else: subprocess.Popen(["xdg-open", str(p)])
    def open_csv(self): self._open_path(self.master_csv_path)
    def open_report(self): self._open_path(self.report_html_path)

    def run(self):
        source = self.source_root.text().strip(); out = self.output_dir.text().strip()
        if not source or not out: self.status_label.setText("Source and output required"); return
        self.run_btn.setEnabled(False); self.open_btn.setEnabled(False); self.open_report_btn.setEnabled(False); self.table.setRowCount(0); self.status_label.setText("Running")
        source_path = Path(source).expanduser(); out_path = Path(out).expanduser()
        source_type = self.source_type.currentText(); metadata_type = self.metadata_type.currentText()
        keep_per_clip = self.keep_per_clip.isChecked(); save_raw = self.save_raw.isChecked()
        sig = self.signals
        def work():
            try:
                from mediarunner_core import Manifest, load_network_config
                from mediarunner_meta import process_metadata
                cfg = load_network_config()
                manifest_csv = out_path / "_manifests" / "MediaRunner_Metadata_Session.csv"
                manifest = Manifest(manifest_csv)
                sig.log.emit(f"Source type: {source_type}")
                sig.log.emit(f"Metadata type: {metadata_type}")
                result = process_metadata(
                    source_path,
                    out_path,
                    source_type=source_type,
                    metadata_type=metadata_type,
                    keep_sidecars=keep_per_clip,
                    save_raw=save_raw,
                    tool_config=cfg,
                    progress_callback=lambda done,total,path,row: (sig.table_row.emit(row), sig.progress.emit(done,total)),
                    log_callback=lambda msg: sig.log.emit(msg),
                )
                for row in result.rows:
                    manifest.write(
                        method="Metadata",
                        source_path=row.get("source_file", ""),
                        destination_path=str(result.master_csv.parent),
                        camera=row.get("camera", ""),
                        reel=row.get("reel", ""),
                        clip=row.get("clip_id", ""),
                        file=row.get("file", ""),
                        status=row.get("status", ""),
                        note=f"{row.get('metadata_type','')} · {row.get('tool','')} · {row.get('warnings','')}",
                    )
                sig.master_csv.emit(str(result.master_csv))
                sig.report_html.emit(str(result.report_html))
                sig.log.emit(f"Master CSV: {result.master_csv}")
                sig.log.emit(f"Metadata report: {result.report_html}")
                if result.raw_dir:
                    sig.log.emit(f"Raw metadata sidecars: {result.raw_dir}")
                sig.finished.emit(all(row.get("status") == "OK" for row in result.rows))
            except Exception as exc:
                sig.log.emit(f"ERROR: {exc}"); sig.finished.emit(False)
        self.thread = threading.Thread(target=work, daemon=True); self.thread.start()


class ValidationPage(QWidget, ActivityLogMixin):
    """Run local MediaRunner validation checks from the app UI."""
    def __init__(self):
        super().__init__()
        self.signals = WorkerSignals()
        self.thread = None
        self.current_report_path: Path | None = None
        self.current_results_path: Path | None = None
        self._build_ui()
        self.signals.log.connect(lambda txt: log_to(self.console, txt))
        self.signals.status.connect(self.status.setText)
        self.signals.scan_result.connect(self.on_validation_result)
        self.signals.finished.connect(self.on_finished)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        intro, iv = panel("Validation")
        note = label(
            "Run deterministic local tests that prove copy, checksum, manifest, report, cascading, simultaneous, corruption-detection, and edge-case behavior. "
            "Extended and stress profiles help qualify release candidates before real-drive and RED-camera field testing.",
            "muted",
        )
        note.setWordWrap(True)
        iv.addWidget(note)
        controls = QHBoxLayout()
        self.profile = QComboBox()
        self.profile.addItems(["quick", "extended", "stress"])
        self.profile.setCurrentText("quick")
        self.profile.setToolTip("quick = core invariants, extended = edge cases, stress = repeated extended runs")
        self.runs = QSpinBox()
        self.runs.setRange(1, 100)
        self.runs.setValue(10)
        self.runs.setPrefix("Runs ")
        self.run_btn = QPushButton("Run Validation")
        self.run_btn.setObjectName("primary")
        self.run_btn.clicked.connect(self.run_validation)
        self.open_btn = QPushButton("Open Report")
        self.open_btn.clicked.connect(self.open_report)
        self.reveal_btn = QPushButton("Reveal Run Folder")
        self.reveal_btn.clicked.connect(self.reveal_run_folder)
        self.status = label("Ready", "muted")
        controls.addWidget(label("Profile"))
        controls.addWidget(self.profile)
        controls.addWidget(self.runs)
        controls.addWidget(self.run_btn)
        controls.addWidget(self.open_btn)
        controls.addWidget(self.reveal_btn)
        controls.addWidget(self.status, 1)
        iv.addLayout(controls)

        page_splitter = QSplitter(Qt.Vertical)
        page_splitter.setChildrenCollapsible(False)
        page_splitter.setHandleWidth(8)
        page_splitter.addWidget(intro)

        results_panel, rv = panel("Latest Results")
        self.table = make_table(["Scenario", "Status", "Pass", "Fail", "Seconds", "Note"])
        rv.addWidget(self.table)
        self.add_activity_log(rv)
        page_splitter.addWidget(results_panel)
        page_splitter.setStretchFactor(0, 0)
        page_splitter.setStretchFactor(1, 1)
        page_splitter.setSizes([170, 620])
        root.addWidget(page_splitter, 1)

    def _latest_report(self) -> Path | None:
        package_root = Path(__file__).resolve().parent
        runs = package_root / "validation_runs"
        reports = sorted(runs.glob("*/validation_report.html"), key=lambda p: p.stat().st_mtime, reverse=True) if runs.exists() else []
        return reports[0] if reports else None

    def run_validation(self):
        if self.thread and self.thread.is_alive():
            self.status.setText("Validation already running")
            return
        package_root = Path(__file__).resolve().parent
        script = package_root / "validation" / "run_validation_suite.py"
        if not script.exists():
            self.status.setText("Validation script missing")
            return
        profile = self.profile.currentText().strip() or "quick"
        run_count = self.runs.value()
        run_name = f"{profile}_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        work_dir = package_root / "validation_runs" / run_name
        self.current_report_path = work_dir / "validation_report.html"
        self.current_results_path = work_dir / "validation_results.json"
        self.table.setRowCount(0)
        self.run_btn.setEnabled(False)
        self.status.setText(f"Running {profile} validation…")
        log_to(self.console, f"Starting {profile} validation: {work_dir}")
        sig = self.signals

        def work():
            payload = {"ok": False, "work_dir": str(work_dir), "report": str(self.current_report_path), "results": []}
            try:
                # Important: do not call sys.executable here. In a PyInstaller .app,
                # sys.executable is MediaRunner itself, so subprocess validation would
                # launch a second copy of the application instead of the validator.
                rc, stdout_text, stderr_text = self._run_validation_script(script, package_root, work_dir, profile, run_count)
                if stdout_text:
                    for line in stdout_text.splitlines():
                        sig.log.emit(line)
                if stderr_text:
                    for line in stderr_text.splitlines():
                        sig.log.emit("STDERR: " + line)
                payload["ok"] = rc == 0
                if self.current_results_path and self.current_results_path.exists():
                    data = json.loads(self.current_results_path.read_text(encoding="utf-8"))
                    payload["results"] = data.get("results", [])
                sig.scan_result.emit(payload)
                sig.finished.emit(rc == 0)
            except Exception as exc:
                sig.log.emit(f"ERROR: {exc}")
                payload["error"] = str(exc)
                sig.scan_result.emit(payload)
                sig.finished.emit(False)

        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()

    def _run_validation_script(self, script: Path, package_root: Path, work_dir: Path, profile: str, run_count: int):
        """Run validation in-process so packaged apps do not relaunch themselves."""
        import contextlib
        import importlib.util
        import io

        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        argv_before = sys.argv[:]
        path_added = False
        root_text = str(package_root)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
            path_added = True
        module_name = f"mediarunner_validation_runtime_{int(time.time() * 1000)}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(script))
            if spec is None or spec.loader is None:
                raise RuntimeError(f"Could not load validation script: {script}")
            module = importlib.util.module_from_spec(spec)
            # Dataclasses with postponed annotations expect their defining module
            # to be present in sys.modules during import. Without this, the
            # in-process validator can fail with: 'NoneType' object has no
            # attribute '__dict__'.
            previous_module = sys.modules.get(module_name)
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
            finally:
                if previous_module is not None:
                    sys.modules[module_name] = previous_module
                else:
                    sys.modules.pop(module_name, None)
            if not hasattr(module, "main"):
                raise RuntimeError("Validation script does not expose main()")
            sys.argv = [str(script), "--work-dir", str(work_dir), "--profile", str(profile), "--runs", str(int(run_count))]
            with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
                rc = module.main()
            return int(rc or 0), stdout_buffer.getvalue(), stderr_buffer.getvalue()
        finally:
            sys.argv = argv_before
            if path_added:
                try:
                    sys.path.remove(root_text)
                except ValueError:
                    pass

    def on_validation_result(self, payload: dict):
        self.table.setRowCount(0)
        for item in payload.get("results", []):
            add_row(self.table, [
                item.get("display_name") or item.get("name", ""),
                item.get("status", ""),
                item.get("display_ok", item.get("ok_rows", 0)),
                item.get("display_fail", item.get("fail_rows", 0)),
                f"{float(item.get('seconds', 0) or 0):.2f}",
                item.get("display_note") or item.get("note", ""),
            ], status=item.get("status", ""))
        if payload.get("error"):
            self.status.setText("Validation failed to run")

    def on_finished(self, ok: bool):
        self.run_btn.setEnabled(True)
        self.status.setText("Validation PASS" if ok else "Validation FAIL")
        log_to(self.console, "Validation PASS" if ok else "Validation FAIL", GREEN if ok else RED)

    def open_report(self):
        report = self.current_report_path if self.current_report_path and self.current_report_path.exists() else self._latest_report()
        if report and report.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(report)))
            self.status.setText(f"Opened: {report.name}")
        else:
            self.status.setText("No validation report found")

    def reveal_run_folder(self):
        report = self.current_report_path if self.current_report_path and self.current_report_path.exists() else self._latest_report()
        if report and report.exists():
            folder = report.parent
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            elif sys.platform == "win32":
                subprocess.Popen(["explorer", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
            self.status.setText(f"Revealed: {folder.name}")
        else:
            self.status.setText("No validation run found")



class CustomReportDesignerDialog(QDialog):
    """Click-based custom report template designer.

    Left side = fields that can be added. Right side = selected report columns in
    top-to-bottom output order. The selected list also supports drag/drop reorder.
    """

    def __init__(self, columns: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Design Custom Report Template")
        self.resize(980, 620)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        title = label("Design Custom Template", "section_title")
        root.addWidget(title)
        help_line = label(
            "Click fields to add them, then drag or use Move Up / Move Down to set the column order. "
            "The selected list is the exact left-to-right order of the exported report.",
            "muted",
        )
        help_line.setWordWrap(True)
        root.addWidget(help_line)

        lists = QHBoxLayout()
        lists.setSpacing(12)

        left = QVBoxLayout()
        left.addWidget(label("Available Fields", "section_title"))
        self.available_list = QListWidget()
        self.available_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.available_list.itemDoubleClicked.connect(lambda _item: self.add_selected())
        left.addWidget(self.available_list, 1)

        middle = QVBoxLayout()
        middle.setSpacing(8)
        middle.addStretch(1)
        self.add_btn = QPushButton("Add →")
        self.add_btn.clicked.connect(self.add_selected)
        self.add_all_btn = QPushButton("Add All")
        self.add_all_btn.clicked.connect(self.add_all)
        self.remove_btn = QPushButton("← Remove")
        self.remove_btn.clicked.connect(self.remove_selected)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear_selected)
        for btn in (self.add_btn, self.add_all_btn, self.remove_btn, self.clear_btn):
            middle.addWidget(btn)
        middle.addStretch(1)

        right = QVBoxLayout()
        right.addWidget(label("Selected Columns / Output Order", "section_title"))
        self.selected_list = QListWidget()
        self.selected_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.selected_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.selected_list.setDefaultDropAction(Qt.MoveAction)
        self.selected_list.itemDoubleClicked.connect(lambda _item: self.remove_selected())
        right.addWidget(self.selected_list, 1)

        order = QHBoxLayout()
        self.up_btn = QPushButton("Move Up")
        self.up_btn.clicked.connect(lambda: self.move_selected(-1))
        self.down_btn = QPushButton("Move Down")
        self.down_btn.clicked.connect(lambda: self.move_selected(1))
        order.addWidget(self.up_btn)
        order.addWidget(self.down_btn)
        right.addLayout(order)

        lists.addLayout(left, 5)
        lists.addLayout(middle, 1)
        lists.addLayout(right, 5)
        root.addLayout(lists, 1)

        self.preview = label("", "muted")
        self.preview.setWordWrap(True)
        root.addWidget(self.preview)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        apply = QPushButton("Apply Columns")
        apply.setObjectName("primary")
        apply.clicked.connect(self.accept)
        actions.addWidget(cancel)
        actions.addWidget(apply)
        root.addLayout(actions)

        self.populate_selected(columns or [])
        self.refresh_available()
        self.selected_list.model().rowsMoved.connect(lambda *_args: self.update_preview())
        self.selected_list.model().rowsInserted.connect(lambda *_args: self.update_preview())
        self.selected_list.model().rowsRemoved.connect(lambda *_args: self.update_preview())
        self.update_preview()

    def _field_for_key(self, key: str):
        from mediarunner_reports import FIELD_BY_KEY
        return FIELD_BY_KEY.get(key)

    def _make_item(self, key: str) -> QListWidgetItem:
        from mediarunner_reports import label_for
        field = self._field_for_key(key)
        text = f"{label_for(key)}    ({key})"
        item = QListWidgetItem(text)
        item.setData(Qt.UserRole, key)
        if field and getattr(field, "description", ""):
            item.setToolTip(field.description)
        return item

    def selected_keys(self) -> list[str]:
        keys: list[str] = []
        for i in range(self.selected_list.count()):
            key = self.selected_list.item(i).data(Qt.UserRole)
            if key and key not in keys:
                keys.append(str(key))
        return keys

    def populate_selected(self, columns: list[str] | str):
        from mediarunner_reports import parse_column_list
        self.selected_list.clear()
        for key in parse_column_list(columns):
            self.selected_list.addItem(self._make_item(key))

    def refresh_available(self):
        from mediarunner_reports import FIELD_REGISTRY
        selected = set(self.selected_keys())
        self.available_list.clear()
        for field in FIELD_REGISTRY:
            if field.key not in selected:
                self.available_list.addItem(self._make_item(field.key))
        self.update_preview()

    def add_selected(self):
        items = list(self.available_list.selectedItems())
        if not items and self.available_list.currentItem():
            items = [self.available_list.currentItem()]
        existing = set(self.selected_keys())
        for item in items:
            key = item.data(Qt.UserRole)
            if key and key not in existing:
                self.selected_list.addItem(self._make_item(str(key)))
                existing.add(str(key))
        self.refresh_available()

    def add_all(self):
        from mediarunner_reports import FIELD_REGISTRY
        existing = set(self.selected_keys())
        for field in FIELD_REGISTRY:
            if field.key not in existing:
                self.selected_list.addItem(self._make_item(field.key))
                existing.add(field.key)
        self.refresh_available()

    def remove_selected(self):
        rows = sorted({self.selected_list.row(i) for i in self.selected_list.selectedItems()}, reverse=True)
        if not rows and self.selected_list.currentRow() >= 0:
            rows = [self.selected_list.currentRow()]
        for row in rows:
            self.selected_list.takeItem(row)
        self.refresh_available()

    def clear_selected(self):
        self.selected_list.clear()
        self.refresh_available()

    def move_selected(self, direction: int):
        selected_rows = sorted({self.selected_list.row(i) for i in self.selected_list.selectedItems()})
        if not selected_rows and self.selected_list.currentRow() >= 0:
            selected_rows = [self.selected_list.currentRow()]
        if not selected_rows:
            return
        if direction < 0:
            iterable = selected_rows
            if selected_rows[0] == 0:
                return
        else:
            iterable = list(reversed(selected_rows))
            if selected_rows[-1] >= self.selected_list.count() - 1:
                return
        new_rows = []
        for row in iterable:
            item = self.selected_list.takeItem(row)
            new_row = row + direction
            self.selected_list.insertItem(new_row, item)
            new_rows.append(new_row)
        self.selected_list.clearSelection()
        for row in new_rows:
            item = self.selected_list.item(row)
            if item:
                item.setSelected(True)
        self.update_preview()

    def update_preview(self):
        from mediarunner_reports import label_for
        keys = self.selected_keys()
        if not keys:
            self.preview.setText("No columns selected yet. Add at least one field before applying or saving a template.")
            return
        labels = " → ".join(label_for(k) for k in keys)
        self.preview.setText(f"Selected {len(keys)} column{'s' if len(keys) != 1 else ''}: {labels}")


class ReportsPage(QWidget):

    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        builder, bv = panel("Custom Report Builder")
        builder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        builder.setMinimumHeight(410)
        builder.setMaximumHeight(430)

        # Unified source + template workspace.  The fixed-height panel prevents the
        # upper controls from being compressed when the source table has many rows;
        # only the report/source table below should consume extra vertical space.
        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        w, self.root_path = path_picker(str(Path.home() / "Desktop"))
        self.root_path.setText(str(Path.home() / "Desktop"))
        self.scan_btn = QPushButton("Refresh")
        self.scan_btn.clicked.connect(self.scan)
        self.latest_btn = QPushButton("Use Latest Transfer")
        self.latest_btn.clicked.connect(self.select_latest_transfer_source)
        self.open_btn = QPushButton("Open Selected")
        self.open_btn.clicked.connect(self.open_selected)
        self.reveal_btn = QPushButton("Reveal Selected")
        self.reveal_btn.clicked.connect(self.reveal_selected)
        search_row.addWidget(label("Search Folder", "muted"))
        search_row.addWidget(w, 1)
        search_row.addWidget(self.scan_btn)
        search_row.addWidget(self.latest_btn)
        search_row.addWidget(self.open_btn)
        search_row.addWidget(self.reveal_btn)
        bv.addLayout(search_row)

        intro = label(
            "Choose a transfer source, design or load a template, then export a media-only custom HTML + CSV report. "
            "Transfer HTML reports resolve to their sibling manifest CSV; sidecars/control rows are hidden before REDline, ffprobe, and ExifTool enrichment.",
            "muted",
        )
        intro.setWordWrap(True)
        intro.setMaximumHeight(48)
        bv.addWidget(intro)

        controls = QGridLayout()
        controls.setHorizontalSpacing(10)
        controls.setVerticalSpacing(8)
        self.template_combo = QComboBox()
        self.reload_templates()
        self.template_combo.currentTextChanged.connect(self.load_template_columns)
        self.template_name = QLineEdit()
        self.template_name.setPlaceholderText("Optional saved template name")
        self.columns_edit = QPlainTextEdit()
        self.columns_edit.setPlaceholderText("Selected field keys appear here after using Design Custom Template. Power users can also edit this list directly.")
        self.columns_edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.columns_edit.setFixedHeight(46)

        load_btn = QPushButton("Load Template")
        load_btn.clicked.connect(self.load_template_columns)
        design_btn = QPushButton("Design Custom Template…")
        design_btn.clicked.connect(self.open_template_designer)
        self.generate_btn = QPushButton("Generate from Selected Source")
        self.generate_btn.setObjectName("primary")
        self.generate_btn.clicked.connect(self.generate_custom_report)
        self.generate_latest_btn = QPushButton("Generate from Latest Transfer")
        self.generate_latest_btn.clicked.connect(self.generate_latest_transfer_report)
        self.save_template_btn = QPushButton("Save Template")
        self.save_template_btn.clicked.connect(self.save_template)
        self.source_status = label("Source: none selected yet. Click Use Latest Transfer for the newest transfer manifest.", "muted")
        self.source_status.setWordWrap(True)
        self.source_status.setMaximumHeight(44)
        self.report_status = label("Ready. Custom reports default to media files only; sidecar/control rows are hidden before metadata enrichment.", "muted")
        self.report_status.setWordWrap(True)
        self.report_status.setMaximumHeight(44)

        controls.addWidget(label("Template", "muted"), 0, 0)
        controls.addWidget(self.template_combo, 0, 1)
        controls.addWidget(load_btn, 0, 2)
        controls.addWidget(design_btn, 0, 3)
        controls.addWidget(label("Save As", "muted"), 1, 0)
        controls.addWidget(self.template_name, 1, 1, 1, 2)
        controls.addWidget(self.save_template_btn, 1, 3)
        controls.addWidget(label("Selected Fields", "muted"), 2, 0)
        controls.addWidget(self.columns_edit, 2, 1, 1, 3)
        controls.addWidget(label("Source CSV", "muted"), 3, 0)
        controls.addWidget(self.source_status, 3, 1, 1, 3)
        controls.addWidget(self.report_status, 4, 1, 1, 1)
        controls.addWidget(self.generate_latest_btn, 4, 2)
        controls.addWidget(self.generate_btn, 4, 3)
        controls.setColumnMinimumWidth(0, 120)
        controls.setColumnStretch(1, 1)
        bv.addLayout(controls)

        try:
            from mediarunner_reports import field_help_text
            help_text = label("Available fields: " + field_help_text(), "muted")
            help_text.setWordWrap(True)
            help_text.setMaximumHeight(48)
            bv.addWidget(help_text)
        except Exception:
            pass

        self.table = make_table(["Report / Source", "Type", "Modified", "Path"])
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table.setMinimumHeight(260)
        self.table.itemSelectionChanged.connect(self.update_selected_source_status)
        self.table.itemDoubleClicked.connect(lambda _item: self.generate_custom_report())

        root.addWidget(builder, 0)
        root.addWidget(self.table, 1)
        self.load_template_columns()
        self.scan()

    def reload_templates(self):
        try:
            from mediarunner_reports import all_templates
            current = self.template_combo.currentText() if hasattr(self, "template_combo") else ""
            self.template_combo.blockSignals(True)
            self.template_combo.clear()
            for name in all_templates().keys():
                self.template_combo.addItem(name)
            if current:
                ix = self.template_combo.findText(current)
                if ix >= 0: self.template_combo.setCurrentIndex(ix)
            self.template_combo.blockSignals(False)
        except Exception:
            self.template_combo.clear()
            self.template_combo.addItems(["Standard Verification Report", "Camera Department Report", "Timecode Report"])

    def load_template_columns(self):
        try:
            from mediarunner_reports import all_templates
            templates = all_templates()
            name = self.template_combo.currentText() or "Standard Verification Report"
            columns = templates.get(name, [])
            self.columns_edit.setPlainText(", ".join(columns))
            self.template_name.setPlaceholderText(f"Save edited columns as a new template")
        except Exception as exc:
            self.report_status.setText(f"Template load failed: {exc}")

    def _file_type(self, p: Path) -> str:
        name = p.name.lower()
        if p.suffix.lower() == ".csv":
            if name.startswith("mediarunner_manifest_"):
                return "Transfer Manifest CSV"
            if name.startswith("mediarunner_ftp_"):
                return "FTP Manifest CSV"
            if name.startswith("mediarunner_red_wireless_"):
                return "RED Wireless Manifest CSV"
            if "metadata_summary" in name or "master_ltc" in name:
                return "Metadata CSV"
            if "custom_report" in name:
                return "Custom CSV"
            if "manifest" in name:
                return "Manifest CSV"
            return "CSV"
        if name.startswith("mediarunner_report_"):
            return "Transfer HTML"
        if "validation_report" in name:
            return "Validation HTML"
        if "metadata_report" in name:
            return "Metadata HTML"
        if "custom_report" in name:
            return "Custom HTML"
        return "HTML"

    def _transfer_source_patterns(self) -> list[str]:
        return [
            "MediaRunner_Manifest_*.csv",
            "MediaRunner_FTP_*.csv",
            "MediaRunner_RED_Wireless_*.csv",
        ]

    def _source_patterns(self) -> list[str]:
        return [
            *self._transfer_source_patterns(),
            "MediaRunner_Metadata_Summary_*.csv",
            "master_ltc_*.csv",
        ]

    def _scan_patterns(self) -> list[str]:
        return [
            "MediaRunner_Report_*.html",
            "MediaRunner_Metadata_Report_*.html",
            "MediaRunner_Custom_Report_*.html",
            "validation_report.html",
            *self._source_patterns(),
            "MediaRunner_Custom_Report_*.csv",
        ]

    def _transfer_source_candidates(self, base: Path | None = None) -> list[Path]:
        base = Path(base or self.root_path.text()).expanduser()
        if not base.exists():
            return []
        files: list[Path] = []
        try:
            for pattern in self._transfer_source_patterns():
                files.extend(base.rglob(pattern))
        except Exception:
            pass
        unique = {str(p.resolve()): p for p in files if p.exists() and p.is_file()}
        return sorted(unique.values(), key=lambda x: x.stat().st_mtime, reverse=True)

    def _latest_transfer_source(self) -> Path | None:
        candidates = self._transfer_source_candidates()
        return candidates[0] if candidates else None

    def _csv_for_report_html(self, p: Path) -> Path | None:
        """Resolve a MediaRunner HTML report back to the CSV source that can feed custom reports."""
        p = Path(p)
        if not p.exists() or p.suffix.lower() != ".html":
            return None
        parent = p.parent
        low = p.name.lower()
        csv_patterns: list[str] = []
        if low.startswith("mediarunner_report_"):
            match = re.search(r"_(\d{8}_\d{6})\.html$", p.name)
            if match:
                csv_patterns.append(f"MediaRunner_Manifest_*_{match.group(1)}.csv")
            csv_patterns.append("MediaRunner_Manifest_*.csv")
        elif "metadata_report" in low:
            csv_patterns.extend(["MediaRunner_Metadata_Summary_*.csv", "master_ltc_*.csv"])
        elif "custom_report" in low:
            csv_patterns.append("MediaRunner_Custom_Report_*.csv")
        candidates: list[Path] = []
        for pattern in csv_patterns:
            candidates.extend(parent.glob(pattern))
        candidates = [c for c in candidates if c.exists() and c.is_file()]
        if not candidates and parent.name.lower() == "custom_reports":
            candidates.extend(parent.glob("*.csv"))
        if not candidates:
            return None
        # Prefer the CSV closest in time to the HTML report, then newest.
        report_time = p.stat().st_mtime
        candidates.sort(key=lambda c: (abs(c.stat().st_mtime - report_time), -c.stat().st_mtime))
        return candidates[0]

    def _resolved_source_from_path(self, p: Path | None) -> Path | None:
        if not p or not p.exists():
            return None
        if p.suffix.lower() == ".csv":
            return p
        if p.suffix.lower() == ".html":
            return self._csv_for_report_html(p)
        return None

    def _select_table_path(self, path: Path | None, *, quiet: bool = False) -> bool:
        if not path:
            return False
        target = str(Path(path).resolve())
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 3)
            if item and str(Path(item.text()).expanduser().resolve()) == target:
                self.table.selectRow(row)
                self.table.scrollToItem(item)
                if not quiet:
                    self.update_selected_source_status()
                return True
        return False

    def scan(self):
        current = self.selected_path()
        self.table.setRowCount(0); base = Path(self.root_path.text()).expanduser()
        if not base.exists():
            self.source_status.setText("Source: search folder does not exist.")
            return
        files = []
        try:
            for pattern in self._scan_patterns():
                files.extend(base.rglob(pattern))
        except Exception:
            pass
        unique = {str(p.resolve()): p for p in files if p.exists() and p.is_file()}
        ordered = sorted(unique.values(), key=lambda x: x.stat().st_mtime, reverse=True)[:250]
        for p in ordered:
            add_row(self.table, [p.name, self._file_type(p), datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"), str(p)])
        if current and self._select_table_path(current, quiet=True):
            pass
        else:
            self._select_table_path(self._latest_transfer_source(), quiet=True)
        self.update_selected_source_status()

    def selected_path(self) -> Path | None:
        items = self.table.selectedItems()
        if not items: return None
        row = items[0].row(); item = self.table.item(row,3)
        return Path(item.text()) if item else None

    def selected_source_path(self) -> Path | None:
        return self._resolved_source_from_path(self.selected_path())

    def update_selected_source_status(self):
        p = self.selected_path()
        src = self._resolved_source_from_path(p)
        if src and src.exists():
            if p and p.suffix.lower() == ".html" and src != p:
                self.source_status.setText(f"Source: {src.name} (resolved from selected HTML report)")
            else:
                self.source_status.setText(f"Source: {src.name}")
            return
        if p and p.exists() and p.suffix.lower() == ".html":
            self.source_status.setText("Source: selected HTML report has no matching CSV manifest next to it. Select a manifest CSV or click Use Latest Transfer.")
        else:
            self.source_status.setText("Source: none selected yet. Click Use Latest Transfer for the newest transfer manifest.")

    def select_latest_transfer_source(self):
        self.scan()
        latest = self._latest_transfer_source()
        if latest and self._select_table_path(latest):
            self.report_status.setText(f"Latest transfer source selected: {latest.name}")
        else:
            self.report_status.setText("No transfer manifest CSV found under the current search folder. Try browsing to the transfer destination or Desktop and refresh.")

    def generate_latest_transfer_report(self):
        latest = self._latest_transfer_source()
        if not latest:
            self.scan()
            latest = self._latest_transfer_source()
        if latest and self._select_table_path(latest):
            self.generate_custom_report()
        else:
            self.report_status.setText("No transfer manifest CSV found under the current search folder. Try browsing to the transfer destination or Desktop and refresh.")

    def open_template_designer(self):
        try:
            from mediarunner_reports import parse_column_list, label_for
            current = parse_column_list(self.columns_edit.toPlainText())
            dialog = CustomReportDesignerDialog(current, self)
            if dialog.exec() == QDialog.Accepted:
                columns = dialog.selected_keys()
                if not columns:
                    self.report_status.setText("No fields selected. Choose at least one field for the custom report.")
                    return
                self.columns_edit.setPlainText(", ".join(columns))
                labels = ", ".join(label_for(c) for c in columns)
                self.report_status.setText(f"Template design applied: {len(columns)} columns selected — {labels}")
        except Exception as exc:
            self.report_status.setText(f"Template designer failed: {exc}")

    def generate_custom_report(self):
        selected = self.selected_path()
        p = self.selected_source_path()
        if not p or not p.exists():
            self.report_status.setText("Select a transfer manifest CSV, metadata summary CSV, or transfer HTML report first. Use Latest Transfer is the fastest path after a transfer completes.")
            return
        if p.suffix.lower() != ".csv":
            self.report_status.setText("Custom reports are generated from CSV sources. Select a manifest CSV or metadata summary CSV.")
            return
        try:
            from mediarunner_reports import generate_custom_report, parse_column_list, label_for
            template = self.template_combo.currentText() or "Custom Report"
            columns = parse_column_list(self.columns_edit.toPlainText())
            result = generate_custom_report(p, template_name=template, columns=columns)
            labels = ", ".join(label_for(c) for c in result.get("columns", []))
            resolved = " from resolved manifest" if selected and selected.suffix.lower() == ".html" else ""
            meta = result.get("metadata_summary", {}) if isinstance(result, dict) else {}
            filt = result.get("filter_summary", {}) if isinstance(result, dict) else {}
            meta_note = ""
            if isinstance(meta, dict) and meta.get("enabled"):
                meta_note = f" · Metadata enriched: {meta.get('media_targets', 0)} media target(s), {meta.get('external_tool_reads', 0)} tool read(s)"
            filter_note = ""
            if isinstance(filt, dict) and filt.get("enabled"):
                filter_note = f" · Media filter: {filt.get('media_rows', result.get('rows', 0))}/{filt.get('source_rows', result.get('rows', 0))} reported, {filt.get('hidden_rows', 0)} hidden"
            self.report_status.setText(f"Custom report created{resolved}: {result['html']} · {result['rows']} media rows · Columns: {labels}{filter_note}{meta_note}")
            self.scan()
            if sys.platform == "darwin": subprocess.Popen(["open", str(result["html"])])
            elif sys.platform == "win32": subprocess.Popen(["cmd","/c","start","",str(result["html"])], shell=False)
            else: subprocess.Popen(["xdg-open", str(result["html"])])
        except Exception as exc:
            self.report_status.setText(f"Custom report failed: {exc}")

    def save_template(self):
        name = self.template_name.text().strip()
        if not name:
            self.report_status.setText("Enter a template name in Save As before saving.")
            return
        try:
            from mediarunner_reports import save_user_template, parse_column_list
            columns = parse_column_list(self.columns_edit.toPlainText())
            path = save_user_template(name, columns)
            self.reload_templates()
            ix = self.template_combo.findText(name)
            if ix >= 0: self.template_combo.setCurrentIndex(ix)
            self.report_status.setText(f"Saved custom template: {name} ({path})")
        except Exception as exc:
            self.report_status.setText(f"Save template failed: {exc}")

    def open_selected(self):
        p = self.selected_path()
        if p and p.exists():
            if sys.platform == "darwin": subprocess.Popen(["open", str(p)])
            elif sys.platform == "win32": subprocess.Popen(["cmd","/c","start","",str(p)], shell=False)
            else: subprocess.Popen(["xdg-open", str(p)])
    def reveal_selected(self):
        p = self.selected_path()
        if p and p.exists():
            if sys.platform == "darwin": subprocess.Popen(["open", "-R", str(p)])
            elif sys.platform == "win32": subprocess.Popen(["explorer", "/select,", str(p)])
            else: subprocess.Popen(["xdg-open", str(p.parent)])


class AppSettingsPage(QWidget):
    """General application preferences plus metadata tool discovery."""
    def __init__(self):
        super().__init__()
        self.ingest_signals = WorkerSignals()
        self.ingest_signals.log.connect(lambda text: log_to(self.throughput_console, text))
        self.ingest_signals.status.connect(self._set_throughput_status)
        self.ingest_signals.finished.connect(self._on_throughput_finished)
        self._throughput_cancel_event = threading.Event()
        self._build_ui()
        self.load_config()

    def _build_tool_path_row(self, grid: QGridLayout, row: int, title: str):
        edit = QLineEdit()
        edit.setClearButtonEnabled(True)
        edit.setPlaceholderText("Auto-detect if blank")
        browse = QPushButton("Browse…")
        browse.clicked.connect(lambda: self.browse_executable(edit))
        status = label("Not checked", "muted")
        status.setWordWrap(True)
        grid.addWidget(label(title), row, 0)
        grid.addWidget(edit, row, 1)
        grid.addWidget(browse, row, 2)
        grid.addWidget(status, row, 3)
        return edit, status

    def _open_url(self, url: str):
        QDesktopServices.openUrl(QUrl(url))

    def _copy_to_clipboard(self, text: str):
        QApplication.clipboard().setText(text)
        self.status.setText(f"Copied: {text}")

    def _redact_for_support(self, value):
        if isinstance(value, dict):
            out = {}
            for key, item in value.items():
                key_text = str(key).lower()
                if any(token in key_text for token in ("password", "secret", "token", "api_key", "apikey", "credential", "webhook")):
                    out[key] = "<redacted>"
                else:
                    out[key] = self._redact_for_support(item)
            return out
        if isinstance(value, list):
            return [self._redact_for_support(item) for item in value]
        return value

    def _diagnostic_text(self) -> str:
        lines = [
            "MediaRunner Diagnostics",
            f"Build: {MEDIARUNNER_BUILD_ID}",
            f"Generated: {datetime.now().isoformat(timespec='seconds')}",
            f"Python: {sys.version.replace(chr(10), ' ')}",
            f"Platform: {platform.platform()}",
            f"Executable: {sys.executable}",
            f"Working directory: {Path.cwd()}",
            "",
            "Notes for production validation:",
            "- This bundle is meant to accompany a bug report or failed transfer investigation.",
            "- It intentionally redacts saved credentials and does not include copied media.",
            "- Recent validation JSON/HTML artifacts are bundled when available.",
        ]
        try:
            from mediarunner_meta import probe_metadata_tools
            probes = probe_metadata_tools(self._merged_config())
            lines.append("")
            lines.append("Metadata tool status:")
            for key in ("redline", "ffmpeg", "ffprobe", "exiftool"):
                probe = probes.get(key)
                if probe:
                    lines.append(f"- {probe.label}: {'OK' if probe.ok else 'MISSING'} — {probe.path or probe.message}")
        except Exception as exc:
            lines.append(f"Metadata tool probe failed: {exc}")
        return "\n".join(lines) + "\n"

    def copy_bug_report_template(self):
        template = f"""MediaRunner Bug Report

Build: {MEDIARUNNER_BUILD_ID}
macOS version:
Machine / CPU:
Source media type and size:
Transfer mode / metadata mode:
Expected result:
Actual result:
Steps to reproduce:
Did it create a report or manifest? Attach paths/screenshots:
Diagnostics bundle attached: yes/no

Please do not attach camera originals unless explicitly requested.
"""
        QApplication.clipboard().setText(template)
        self.status.setText("Bug report template copied")

    def create_diagnostics_bundle(self):
        default_name = f"MediaRunner_Diagnostics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        start = str(Path.home() / "Desktop" / default_name)
        path, _ = QFileDialog.getSaveFileName(self, "Save Diagnostics Bundle", start, "ZIP files (*.zip)")
        if not path:
            return
        out_path = Path(path).expanduser()
        if out_path.suffix.lower() != ".zip":
            out_path = out_path.with_suffix(".zip")
        try:
            from mediarunner_core import load_network_config
            cfg = self._redact_for_support(load_network_config())
            package_root = Path(__file__).resolve().parent
            with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("diagnostics/app_info.txt", self._diagnostic_text())
                zf.writestr("diagnostics/settings_redacted.json", json.dumps(cfg, indent=2, sort_keys=True))
                for name in ("README.md", "PACKAGE_NOTES.md", "requirements.txt", "verify_install.py", "build_mac_safe.sh"):
                    f = package_root / name
                    if f.exists() and f.is_file():
                        zf.write(f, f"package/{name}")
                validation_root = package_root / "validation_runs"
                if validation_root.exists():
                    candidates = sorted(
                        [p for p in validation_root.iterdir() if p.is_dir()],
                        key=lambda p: p.stat().st_mtime,
                        reverse=True,
                    )[:3]
                    for folder in candidates:
                        for item_name in ("validation_results.json", "validation_report.html"):
                            item = folder / item_name
                            if item.exists() and item.is_file():
                                zf.write(item, f"validation_runs/{folder.name}/{item_name}")
                        manifests_dir = folder / "manifests"
                        if manifests_dir.exists():
                            for manifest in sorted(manifests_dir.glob("*.csv"))[:20]:
                                zf.write(manifest, f"validation_runs/{folder.name}/manifests/{manifest.name}")
                zf.writestr("diagnostics/bug_report_template.txt", f"MediaRunner Bug Report\n\nBuild: {MEDIARUNNER_BUILD_ID}\nExpected result:\nActual result:\nSteps to reproduce:\nDiagnostics bundle attached: yes\n")
            self.status.setText(f"Diagnostics bundle saved: {out_path}")
            if sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(out_path)])
        except Exception as exc:
            self.status.setText(f"Diagnostics bundle failed: {exc}")

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            child_layout = item.layout()
            widget = item.widget()
            if child_layout is not None:
                self._clear_layout(child_layout)
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _add_dependency_help_row(self, title: str, body: str, *, url: str = "", url_label: str = "Open Download Page", command: str = ""):
        row = QFrame()
        row.setObjectName("card")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(12, 10, 12, 10)
        row_layout.setSpacing(12)

        text_box = QVBoxLayout()
        title_label = label(title)
        title_label.setStyleSheet(f"color: {TEXT}; font-weight: 850; background: transparent;")
        body_label = label(body, "muted")
        body_label.setWordWrap(True)
        text_box.addWidget(title_label)
        text_box.addWidget(body_label)
        row_layout.addLayout(text_box, 1)

        if command:
            copy_btn = QPushButton("Copy Homebrew Command")
            copy_btn.clicked.connect(lambda _=False, cmd=command: self._copy_to_clipboard(cmd))
            row_layout.addWidget(copy_btn)
        if url:
            open_btn = QPushButton(url_label)
            open_btn.clicked.connect(lambda _=False, link=url: self._open_url(link))
            row_layout.addWidget(open_btn)

        self.tool_help_rows.addWidget(row)

    def _update_dependency_help(self, probes: dict):
        self._clear_layout(self.tool_help_rows)
        help_intro = label(
            "Only missing tools are shown here. Links open official or highly reputable project pages; MediaRunner will not auto-install dependencies.",
            "muted",
        )
        help_intro.setWordWrap(True)
        self.tool_help_rows.addWidget(help_intro)
        missing = []

        redline = probes.get("redline")
        ffmpeg = probes.get("ffmpeg")
        ffprobe = probes.get("ffprobe")
        exiftool = probes.get("exiftool")

        if redline and not redline.ok:
            missing.append("redline")
            self._add_dependency_help_row(
                "REDline not found",
                "REDline is installed with REDCINE-X PRO. After installing, click Detect Tools or browse to REDCINE-X PRO.app/Contents/MacOS/REDline.",
                url="https://www.red.com/download/redcine-x-pro-mac",
                url_label="Open RED Download",
            )

        if (ffmpeg and not ffmpeg.ok) or (ffprobe and not ffprobe.ok):
            missing.append("ffmpeg")
            self._add_dependency_help_row(
                "FFmpeg / FFprobe not found",
                "FFprobe powers generic MOV / MP4 / MXF metadata detection, and FFmpeg supports generic media workflows. The ffmpeg package installs both tools.",
                url="https://ffmpeg.org/download.html",
                url_label="Open FFmpeg Download",
                command="brew install ffmpeg",
            )

        if exiftool and not exiftool.ok:
            missing.append("exiftool")
            self._add_dependency_help_row(
                "ExifTool not found",
                "ExifTool is optional, but it can enrich metadata reports with additional embedded camera/container fields when available.",
                url="https://exiftool.org/",
                url_label="Open ExifTool Website",
                command="brew install exiftool",
            )

        self.tool_help_panel.setVisible(bool(missing))

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        general, gv = panel("General")
        self.finish_sound = QCheckBox("Play finish sound")
        self.finish_sound.setChecked(True)
        gv.addWidget(self.finish_sound)

        # Application log location (audit follow-up): show where the log
        # lives, allow a custom folder, and open it in one click.
        log_row = QHBoxLayout()
        log_row.addWidget(label("Log folder"))
        self.log_dir_edit = QLineEdit()
        self.log_dir_edit.setClearButtonEnabled(True)
        from mediarunner_logging import log_dir as _current_log_dir
        self.log_dir_edit.setPlaceholderText(str(_current_log_dir()))
        log_row.addWidget(self.log_dir_edit, 1)
        log_browse = QPushButton("Browse…")
        log_browse.clicked.connect(self.browse_log_dir)
        log_row.addWidget(log_browse)
        open_logs = QPushButton("Open Logs")
        open_logs.clicked.connect(self.open_log_dir)
        log_row.addWidget(open_logs)
        gv.addLayout(log_row)
        log_hint = label("Blank = default. A custom folder takes effect on the next launch.", "muted")
        gv.addWidget(log_hint)

        # Engineering gate: the Validation page is hidden until unlocked with
        # a password, so stress tooling never tempts a non-engineer mid-show.
        eng_row = QHBoxLayout()
        eng_row.addWidget(label("Engineering tools"))
        self.eng_status = label("Locked", "muted")
        eng_row.addWidget(self.eng_status)
        self.eng_btn = QPushButton("Unlock…")
        self.eng_btn.clicked.connect(self.toggle_engineering)
        eng_row.addWidget(self.eng_btn)
        eng_row.addStretch()
        gv.addLayout(eng_row)
        gv.addWidget(label("Unlocking shows the Validation page (integrity + field-stress suites). Locks again on relaunch.", "muted"))

        page_splitter = QSplitter(Qt.Vertical)
        page_splitter.setChildrenCollapsible(False)
        page_splitter.setHandleWidth(8)
        page_splitter.addWidget(general)

        linux_panel, linux_v = panel("Linux Ingest")
        ingest_hint = label(
            "Tune multi-magazine CFexpress ingest. Destination throughput test writes temporary files only, fsyncs them, then deletes the test folder.",
            "muted",
        )
        ingest_hint.setWordWrap(True)
        linux_v.addWidget(ingest_hint)
        ingest_grid = QGridLayout()
        ingest_grid.setSpacing(10)
        ingest_grid.addWidget(label("Max simultaneous magazines"), 0, 0)
        self.max_magazines = QSpinBox()
        self.max_magazines.setRange(1, 24)
        ingest_grid.addWidget(self.max_magazines, 0, 1)
        ingest_grid.addWidget(label("Threads per magazine"), 0, 2)
        self.threads_per_magazine = QSpinBox()
        self.threads_per_magazine.setRange(1, 8)
        ingest_grid.addWidget(self.threads_per_magazine, 0, 3)
        self.magazine_subfolders = QCheckBox("Create one destination subfolder per magazine")
        self.magazine_subfolders.setChecked(True)
        ingest_grid.addWidget(self.magazine_subfolders, 1, 0, 1, 4)
        linux_v.addLayout(ingest_grid)

        throughput_panel, throughput_v = panel("Destination Throughput Test")
        throughput_grid = QGridLayout()
        throughput_grid.setSpacing(10)
        throughput_grid.addWidget(label("Destination"), 0, 0)
        dest_widget, self.throughput_dest = path_picker("")
        throughput_grid.addWidget(dest_widget, 0, 1, 1, 3)
        self.throughput_dest.textChanged.connect(lambda _text: self.refresh_throughput_profile_status())
        throughput_grid.addWidget(label("GiB per stream"), 1, 0)
        self.throughput_size = QDoubleSpinBox()
        self.throughput_size.setRange(0.1, 64.0)
        self.throughput_size.setSingleStep(0.5)
        self.throughput_size.setDecimals(1)
        throughput_grid.addWidget(self.throughput_size, 1, 1)
        throughput_grid.addWidget(label("Streams"), 1, 2)
        self.throughput_counts = QLineEdit()
        self.throughput_counts.setPlaceholderText("1,2,4,6,8,12")
        throughput_grid.addWidget(self.throughput_counts, 1, 3)
        throughput_v.addLayout(throughput_grid)
        self.throughput_profile_status = label("No destination profile selected", "muted")
        self.throughput_profile_status.setWordWrap(True)
        throughput_v.addWidget(self.throughput_profile_status)
        throughput_buttons = QHBoxLayout()
        self.run_throughput_btn = QPushButton("Run Throughput Test")
        self.run_throughput_btn.setObjectName("primary")
        self.run_throughput_btn.clicked.connect(self.run_throughput_test)
        self.cancel_throughput_btn = QPushButton("Stop Test")
        self.cancel_throughput_btn.setEnabled(False)
        self.cancel_throughput_btn.clicked.connect(self.stop_throughput_test)
        self.throughput_status = label("Ready", "muted")
        throughput_buttons.addWidget(self.run_throughput_btn)
        throughput_buttons.addWidget(self.cancel_throughput_btn)
        throughput_buttons.addWidget(self.throughput_status)
        throughput_buttons.addStretch()
        throughput_v.addLayout(throughput_buttons)
        self.throughput_console = make_console()
        self.throughput_console.setMinimumHeight(120)
        self.throughput_console.setMaximumHeight(180)
        throughput_v.addWidget(self.throughput_console)
        linux_v.addWidget(throughput_panel)
        page_splitter.addWidget(linux_panel)

        tools_panel, tools_v = panel("Metadata Tools")
        tool_hint = label(
            "Configure optional command-line tools used by the Metadata page. "
            "Leave fields blank to auto-detect from PATH, /opt/homebrew/bin, and /usr/local/bin.",
            "muted",
        )
        tool_hint.setWordWrap(True)
        tools_v.addWidget(tool_hint)
        tg = QGridLayout()
        tg.setSpacing(10)
        tg.setColumnStretch(1, 1)
        tg.setColumnStretch(3, 1)
        self.redline_path, self.redline_status = self._build_tool_path_row(tg, 0, "REDline")
        self.ffmpeg_path, self.ffmpeg_status = self._build_tool_path_row(tg, 1, "FFmpeg")
        self.ffprobe_path, self.ffprobe_status = self._build_tool_path_row(tg, 2, "FFprobe")
        self.exiftool_path, self.exiftool_status = self._build_tool_path_row(tg, 3, "ExifTool")
        tools_v.addLayout(tg)

        self.tool_help_panel, self.tool_help_rows = panel("Missing Dependencies")
        self.tool_help_panel.setVisible(False)
        help_intro = label(
            "Only missing tools are shown here. Links open official or highly reputable project pages; MediaRunner will not auto-install dependencies.",
            "muted",
        )
        help_intro.setWordWrap(True)
        self.tool_help_rows.addWidget(help_intro)
        tools_v.addWidget(self.tool_help_panel)

        tool_row = QHBoxLayout()
        self.detect_tools_btn = QPushButton("Detect Tools")
        self.detect_tools_btn.setObjectName("primary")
        self.detect_tools_btn.clicked.connect(self.refresh_tool_status)
        save = QPushButton("Save Settings")
        save.clicked.connect(self.save)
        defaults = QPushButton("Restore Defaults")
        defaults.clicked.connect(self.restore_defaults)
        self.status = label("Ready", "muted")
        tool_row.addWidget(self.detect_tools_btn)
        tool_row.addWidget(save)
        tool_row.addWidget(self.status)
        tool_row.addStretch()
        tool_row.addWidget(defaults)
        tools_v.addLayout(tool_row)
        page_splitter.addWidget(tools_panel)

        diag_panel, diag_v = panel("Diagnostics & Bug Reports")
        diag_note = label(
            "Create a local diagnostics bundle for failed transfers, metadata issues, or crash investigation. "
            "The bundle redacts saved credentials and does not include source media.",
            "muted",
        )
        diag_note.setWordWrap(True)
        diag_v.addWidget(diag_note)
        diag_row = QHBoxLayout()
        make_diag = QPushButton("Create Diagnostics Bundle…")
        make_diag.clicked.connect(self.create_diagnostics_bundle)
        copy_template = QPushButton("Copy Bug Report Template")
        copy_template.clicked.connect(self.copy_bug_report_template)
        diag_row.addWidget(make_diag)
        diag_row.addWidget(copy_template)
        diag_row.addStretch()
        diag_v.addLayout(diag_row)
        page_splitter.addWidget(diag_panel)

        page_splitter.setStretchFactor(0, 0)
        page_splitter.setStretchFactor(1, 1)
        page_splitter.setStretchFactor(2, 1)
        page_splitter.setStretchFactor(3, 0)
        page_splitter.setSizes([110, 360, 440, 180])
        root.addWidget(page_splitter, 1)

    def browse_executable(self, edit: QLineEdit):
        start = edit.text().strip() or ("/Applications" if sys.platform == "darwin" else str(Path.home()))
        path, _ = QFileDialog.getOpenFileName(self, "Choose executable", start)
        if path:
            edit.setText(path)
            self.refresh_tool_status()

    @staticmethod
    def _engineering_hash(password: str, salt: str) -> str:
        import hashlib
        return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()

    def toggle_engineering(self):
        """Unlock (password) or re-lock the engineering-only Validation page."""
        from PySide6.QtWidgets import QInputDialog
        from mediarunner_core import load_network_config, save_network_config
        if getattr(self, "_engineering_unlocked", False):
            self._engineering_unlocked = False
            self.eng_status.setText("Locked")
            self.eng_btn.setText("Unlock…")
            if callable(getattr(self, "engineering_unlock_callback", None)):
                self.engineering_unlock_callback(False)
            return
        cfg = load_network_config()
        stored = str(cfg.get("engineering_password_hash", "") or "")
        if not stored:
            pw, ok = QInputDialog.getText(self, "Set engineering password",
                                          "No engineering password is set yet.\nCreate one to protect the Validation page:",
                                          QLineEdit.Password)
            if not ok or not pw:
                return
            confirm, ok2 = QInputDialog.getText(self, "Confirm password", "Re-enter the password:", QLineEdit.Password)
            if not ok2 or confirm != pw:
                self.status.setText("Passwords did not match — engineering tools remain locked")
                return
            salt = os.urandom(8).hex()
            cfg["engineering_password_hash"] = f"{salt}${self._engineering_hash(pw, salt)}"
            save_network_config(cfg)
        else:
            pw, ok = QInputDialog.getText(self, "Engineering unlock", "Engineering password:", QLineEdit.Password)
            if not ok:
                return
            salt, _, want = stored.partition("$")
            if self._engineering_hash(pw, salt) != want:
                self.status.setText("Incorrect engineering password")
                return
        self._engineering_unlocked = True
        self.eng_status.setText("Unlocked — Validation page visible")
        self.eng_btn.setText("Lock")
        if callable(getattr(self, "engineering_unlock_callback", None)):
            self.engineering_unlock_callback(True)

    def browse_log_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Choose log folder")
        if path:
            self.log_dir_edit.setText(path)

    def open_log_dir(self):
        from mediarunner_logging import log_dir
        target = Path(self.log_dir_edit.text().strip() or log_dir()).expanduser()
        target.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _profile_summary_text(self, profile: dict | None) -> str:
        if not profile:
            return "No saved profile for this destination."
        peak = self._throughput_rate_text(profile.get("peak_bytes_per_second", 0))
        updated = str(profile.get("updated_at", "") or "unknown time")
        return (
            f"Saved profile: {profile.get('label', 'Destination')} - "
            f"{profile.get('max_simultaneous_magazines', '?')} magazines, "
            f"{profile.get('threads_per_magazine', '?')} thread/mag, "
            f"peak {peak}, updated {updated}."
        )

    def refresh_throughput_profile_status(self):
        if not hasattr(self, "throughput_profile_status"):
            return
        text = self.throughput_dest.text().strip()
        if not text:
            self.throughput_profile_status.setText("No destination profile selected")
            return
        try:
            from mediarunner_core import load_network_config
            from mediarunner_linux_ingest import profile_for_destination
            profile = profile_for_destination(load_network_config(), Path(text).expanduser())
            self.throughput_profile_status.setText(self._profile_summary_text(profile))
        except Exception as exc:
            self.throughput_profile_status.setText(f"Profile lookup failed: {exc}")

    def _set_throughput_status(self, text: str):
        self.throughput_status.setText(str(text or ""))

    def _on_throughput_finished(self, ok: bool):
        self.run_throughput_btn.setEnabled(True)
        self.cancel_throughput_btn.setEnabled(False)
        recommendation = int(getattr(self, "_last_throughput_recommendation", 0) or 0)
        if ok and recommendation:
            self.max_magazines.setValue(recommendation)
            self.throughput_status.setText(f"Saved profile: {recommendation} magazine(s)")
            self.refresh_throughput_profile_status()
        elif self._throughput_cancel_event.is_set():
            self.throughput_status.setText("Stopped")
        elif not ok:
            self.throughput_status.setText("Test failed")
        else:
            self.throughput_status.setText("Complete")

    @staticmethod
    def _throughput_rate_text(bytes_per_second: float | int) -> str:
        try:
            bps = float(bytes_per_second or 0)
        except Exception:
            bps = 0.0
        if bps <= 0:
            return "0 MB/s"
        mib = bps / (1024 * 1024)
        gib = bps / (1024 * 1024 * 1024)
        if gib >= 1.0:
            return f"{gib:.2f} GiB/s"
        return f"{mib:.0f} MiB/s"

    def run_throughput_test(self):
        destination = Path(self.throughput_dest.text().strip()).expanduser()
        if not str(destination).strip():
            self.throughput_status.setText("Destination required")
            return
        try:
            counts_text = self.throughput_counts.text().strip()
            from mediarunner_linux_ingest import parse_worker_counts
            counts = parse_worker_counts(counts_text)
        except Exception as exc:
            self.throughput_status.setText(f"Invalid streams: {exc}")
            return
        gib_per_worker = float(self.throughput_size.value() or 1.0)
        bytes_per_worker = int(gib_per_worker * 1024 * 1024 * 1024)
        threads_for_profile = int(self.threads_per_magazine.value() or 1)
        self._last_throughput_recommendation = 0
        self._throughput_cancel_event.clear()
        self.run_throughput_btn.setEnabled(False)
        self.cancel_throughput_btn.setEnabled(True)
        self.throughput_console.clear()
        self.throughput_status.setText("Running")
        sig = self.ingest_signals

        def work():
            ok = False
            try:
                from mediarunner_core import load_network_config, save_network_config
                from mediarunner_linux_ingest import (
                    run_destination_throughput_test,
                    recommend_worker_count,
                    build_destination_profile,
                )
                sig.log.emit(f"Destination: {destination}")
                sig.log.emit(f"Streams: {', '.join(str(c) for c in counts)}")
                sig.log.emit(f"Test size: {gib_per_worker:.1f} GiB per stream")

                def on_result(result):
                    if result.error:
                        sig.log.emit(f"{result.workers} stream(s): ERROR - {result.error}")
                        return
                    per_stream = result.bytes_per_second / max(1, result.workers)
                    sig.log.emit(
                        f"{result.workers} stream(s): {self._throughput_rate_text(result.bytes_per_second)} aggregate "
                        f"({self._throughput_rate_text(per_stream)} per stream, {result.elapsed_seconds:.1f}s)"
                    )

                results = run_destination_throughput_test(
                    destination,
                    worker_counts=counts,
                    bytes_per_worker=bytes_per_worker,
                    progress_callback=on_result,
                    status_callback=sig.status.emit,
                    cancel_check=self._throughput_cancel_event.is_set,
                )
                good = [r for r in results if not r.error and r.bytes_per_second > 0]
                if good:
                    rec = recommend_worker_count(good)
                    best = max(good, key=lambda r: r.bytes_per_second)
                    self._last_throughput_recommendation = rec
                    profile = build_destination_profile(
                        destination,
                        results,
                        recommended_workers=rec,
                        threads_per_magazine=threads_for_profile,
                        throughput_gib_per_worker=gib_per_worker,
                        worker_counts=counts,
                    )
                    cfg = load_network_config()
                    profiles = dict(cfg.get("linux_destination_profiles") or {})
                    profiles[profile["key"]] = profile
                    cfg["linux_destination_profiles"] = profiles
                    cfg["linux_max_simultaneous_magazines"] = rec
                    cfg["linux_threads_per_magazine"] = profile["threads_per_magazine"]
                    cfg["linux_throughput_worker_counts"] = ",".join(str(c) for c in counts)
                    cfg["linux_throughput_gib_per_worker"] = gib_per_worker
                    save_network_config(cfg)
                    sig.log.emit(f"Peak observed: {best.workers} stream(s) at {self._throughput_rate_text(best.bytes_per_second)}")
                    sig.log.emit(f"Recommended max simultaneous magazines: {rec}")
                    sig.log.emit(f"Saved destination profile: {profile['label']}")
                    ok = True
                elif self._throughput_cancel_event.is_set():
                    sig.log.emit("Throughput test stopped.")
                else:
                    sig.log.emit("No successful throughput samples.")
            except Exception as exc:
                sig.log.emit(f"Throughput test failed: {exc}")
            finally:
                sig.finished.emit(ok)

        threading.Thread(target=work, daemon=True, name="mediarunner-throughput-test").start()

    def stop_throughput_test(self):
        self._throughput_cancel_event.set()
        self.throughput_status.setText("Stopping...")

    def load_config(self):
        from mediarunner_core import load_network_config
        cfg = load_network_config()
        self.finish_sound.setChecked(bool(cfg.get("finish_sound", True)))
        self.log_dir_edit.setText(str(cfg.get("log_dir", "") or ""))
        self.max_magazines.setValue(int(cfg.get("linux_max_simultaneous_magazines", 6) or 6))
        self.threads_per_magazine.setValue(int(cfg.get("linux_threads_per_magazine", 1) or 1))
        self.magazine_subfolders.setChecked(bool(cfg.get("linux_stage_magazine_subfolders", True)))
        self.throughput_counts.setText(str(cfg.get("linux_throughput_worker_counts", "1,2,4,6,8,12") or "1,2,4,6,8,12"))
        self.throughput_size.setValue(float(cfg.get("linux_throughput_gib_per_worker", 1.0) or 1.0))
        self.redline_path.setText(str(cfg.get("redline_path", "") or ""))
        self.ffmpeg_path.setText(str(cfg.get("ffmpeg_path", "") or ""))
        self.ffprobe_path.setText(str(cfg.get("ffprobe_path", "") or ""))
        self.exiftool_path.setText(str(cfg.get("exiftool_path", "") or ""))
        self.refresh_throughput_profile_status()
        self.refresh_tool_status()

    def _merged_config(self) -> dict:
        from mediarunner_core import load_network_config, apply_network_config
        cfg = load_network_config()
        cfg.update({
            "finish_sound": self.finish_sound.isChecked(),
            "log_dir": self.log_dir_edit.text().strip(),
            "linux_max_simultaneous_magazines": self.max_magazines.value(),
            "linux_threads_per_magazine": self.threads_per_magazine.value(),
            "linux_stage_magazine_subfolders": self.magazine_subfolders.isChecked(),
            "linux_throughput_worker_counts": self.throughput_counts.text().strip() or "1,2,4,6,8,12",
            "linux_throughput_gib_per_worker": float(self.throughput_size.value() or 1.0),
            "redline_path": self.redline_path.text().strip(),
            "ffmpeg_path": self.ffmpeg_path.text().strip(),
            "ffprobe_path": self.ffprobe_path.text().strip(),
            "exiftool_path": self.exiftool_path.text().strip(),
        })
        return apply_network_config(cfg)

    def get_config(self):
        return self._merged_config()

    def save(self):
        from mediarunner_core import save_network_config
        path = save_network_config(self._merged_config())
        self.status.setText(f"Saved: {path}")
        self.refresh_tool_status()

    def restore_defaults(self):
        self.finish_sound.setChecked(True)
        self.max_magazines.setValue(6)
        self.threads_per_magazine.setValue(1)
        self.magazine_subfolders.setChecked(True)
        self.throughput_counts.setText("1,2,4,6,8,12")
        self.throughput_size.setValue(1.0)
        self.redline_path.clear()
        self.ffmpeg_path.clear()
        self.ffprobe_path.clear()
        self.exiftool_path.clear()
        self.status.setText("Defaults loaded")
        self.refresh_tool_status()

    def refresh_tool_status(self):
        try:
            from mediarunner_meta import probe_metadata_tools
            probes = probe_metadata_tools(self._merged_config())
            pairs = {
                "redline": self.redline_status,
                "ffmpeg": self.ffmpeg_status,
                "ffprobe": self.ffprobe_status,
                "exiftool": self.exiftool_status,
            }
            for key, status_label in pairs.items():
                probe = probes.get(key)
                if not probe:
                    status_label.setText("Not checked")
                    continue
                status_label.setText(("✓ " if probe.ok else "! ") + (probe.path or probe.message))
                status_label.setStyleSheet(f"color: {GREEN if probe.ok else YELLOW}; background: transparent;")
            self._update_dependency_help(probes)
        except Exception as exc:
            for status_label in [self.redline_status, self.ffmpeg_status, self.ffprobe_status, self.exiftool_status]:
                status_label.setText(f"Tool check failed: {exc}")
                status_label.setStyleSheet(f"color: {RED}; background: transparent;")
            if hasattr(self, "tool_help_panel"):
                self.tool_help_panel.setVisible(False)

    def finish_sound_enabled(self):
        return bool(self.finish_sound.isChecked())


class AlertsPage(QWidget):
    """Email and Google Chat completion alerts."""
    def __init__(self):
        super().__init__()
        self.signals = WorkerSignals()
        self.signals.status.connect(self._set_status)
        self.signals.finished.connect(self._on_test_finished)
        self._build_ui()
        self.load_config()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)

        triggers_panel, triggers = panel("Alert Triggers")
        self.notify_success = QCheckBox("Transfer completes successfully")
        self.notify_failure = QCheckBox("Transfer finishes with errors")
        self.notify_cancelled = QCheckBox("Transfer is cancelled")
        for checkbox in (self.notify_success, self.notify_failure, self.notify_cancelled):
            checkbox.setChecked(True)
            triggers.addWidget(checkbox)
        triggers.addWidget(label("Alerts are sent after Offload, FTP Camera Array, and RED Wireless jobs finish. Alert failures are logged but never change transfer results.", "muted"))
        splitter.addWidget(triggers_panel)

        email_panel, email = panel("Email")
        self.email_enabled = QCheckBox("Enable email alerts")
        email.addWidget(self.email_enabled)
        eg = QGridLayout()
        eg.setHorizontalSpacing(10)
        eg.setVerticalSpacing(8)
        eg.setColumnStretch(1, 1)
        eg.setColumnStretch(3, 1)
        self.smtp_host = QLineEdit()
        self.smtp_host.setPlaceholderText("smtp.example.com")
        self.smtp_port = QSpinBox()
        self.smtp_port.setRange(1, 65535)
        self.smtp_port.setValue(587)
        self.smtp_security = QComboBox()
        self.smtp_security.addItems(["STARTTLS", "SSL/TLS", "None"])
        self.smtp_username = QLineEdit()
        self.smtp_username.setPlaceholderText("Optional")
        self.smtp_password = QLineEdit()
        self.smtp_password.setEchoMode(QLineEdit.Password)
        self.smtp_password.setPlaceholderText("Optional app password")
        self.email_from = QLineEdit()
        self.email_from.setPlaceholderText("alerts@example.com")
        self.email_to = QLineEdit()
        self.email_to.setPlaceholderText("recipient@example.com, another@example.com")
        self.email_subject_prefix = QLineEdit()
        self.email_subject_prefix.setPlaceholderText("MediaRunner")
        eg.addWidget(label("SMTP host"), 0, 0); eg.addWidget(self.smtp_host, 0, 1)
        eg.addWidget(label("Port"), 0, 2); eg.addWidget(self.smtp_port, 0, 3)
        eg.addWidget(label("Security"), 1, 0); eg.addWidget(self.smtp_security, 1, 1)
        eg.addWidget(label("Username"), 1, 2); eg.addWidget(self.smtp_username, 1, 3)
        eg.addWidget(label("Password"), 2, 0); eg.addWidget(self.smtp_password, 2, 1)
        eg.addWidget(label("From"), 2, 2); eg.addWidget(self.email_from, 2, 3)
        eg.addWidget(label("To"), 3, 0); eg.addWidget(self.email_to, 3, 1, 1, 3)
        eg.addWidget(label("Subject prefix"), 4, 0); eg.addWidget(self.email_subject_prefix, 4, 1, 1, 3)
        email.addLayout(eg)
        email.addWidget(label("SMTP credentials are saved in the local MediaRunner config. Use a dedicated app password where possible.", "muted"))
        splitter.addWidget(email_panel)

        chat_panel, chat = panel("Google Chat")
        self.gchat_enabled = QCheckBox("Enable Google Chat alerts")
        chat.addWidget(self.gchat_enabled)
        chat_row = QHBoxLayout()
        chat_row.addWidget(label("Webhook URL"))
        self.gchat_webhook = QLineEdit()
        self.gchat_webhook.setEchoMode(QLineEdit.Password)
        self.gchat_webhook.setPlaceholderText("Google Chat incoming webhook URL")
        chat_row.addWidget(self.gchat_webhook, 1)
        chat.addLayout(chat_row)
        chat.addWidget(label("Create an incoming webhook in the target Google Chat space and paste it here. The URL is treated as a secret.", "muted"))
        splitter.addWidget(chat_panel)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([140, 410, 160])
        root.addWidget(splitter, 1)

        buttons = QHBoxLayout()
        self.test_btn = QPushButton("Send Test")
        self.test_btn.setObjectName("primary")
        self.test_btn.clicked.connect(self.send_test)
        save = QPushButton("Save Alerts")
        save.clicked.connect(self.save)
        defaults = QPushButton("Restore Defaults")
        defaults.clicked.connect(self.restore_defaults)
        self.status = label("Ready", "muted")
        buttons.addWidget(self.test_btn)
        buttons.addWidget(save)
        buttons.addWidget(self.status)
        buttons.addStretch()
        buttons.addWidget(defaults)
        root.addLayout(buttons)

    def _set_status(self, text: str):
        self.status.setText(str(text or ""))

    def _on_test_finished(self, _ok: bool):
        self.test_btn.setEnabled(True)

    def load_config(self):
        from mediarunner_core import load_network_config
        cfg = load_network_config()
        self.notify_success.setChecked(bool(cfg.get("alerts_notify_success", True)))
        self.notify_failure.setChecked(bool(cfg.get("alerts_notify_failure", True)))
        self.notify_cancelled.setChecked(bool(cfg.get("alerts_notify_cancelled", True)))
        self.email_enabled.setChecked(bool(cfg.get("alerts_email_enabled", False)))
        self.smtp_host.setText(str(cfg.get("alerts_smtp_host", "") or ""))
        self.smtp_port.setValue(int(cfg.get("alerts_smtp_port", 587) or 587))
        security = str(cfg.get("alerts_smtp_security", "STARTTLS") or "STARTTLS")
        ix = self.smtp_security.findText(security)
        self.smtp_security.setCurrentIndex(ix if ix >= 0 else 0)
        self.smtp_username.setText(str(cfg.get("alerts_smtp_username", "") or ""))
        self.smtp_password.setText(str(cfg.get("alerts_smtp_password", "") or ""))
        self.email_from.setText(str(cfg.get("alerts_email_from", "") or ""))
        self.email_to.setText(str(cfg.get("alerts_email_to", "") or ""))
        self.email_subject_prefix.setText(str(cfg.get("alerts_email_subject_prefix", "MediaRunner") or "MediaRunner"))
        self.gchat_enabled.setChecked(bool(cfg.get("alerts_gchat_enabled", False)))
        self.gchat_webhook.setText(str(cfg.get("alerts_gchat_webhook_url", "") or ""))

    def _merged_config(self) -> dict:
        from mediarunner_core import load_network_config, apply_network_config
        cfg = load_network_config()
        cfg.update({
            "alerts_notify_success": self.notify_success.isChecked(),
            "alerts_notify_failure": self.notify_failure.isChecked(),
            "alerts_notify_cancelled": self.notify_cancelled.isChecked(),
            "alerts_email_enabled": self.email_enabled.isChecked(),
            "alerts_smtp_host": self.smtp_host.text().strip(),
            "alerts_smtp_port": self.smtp_port.value(),
            "alerts_smtp_security": self.smtp_security.currentText(),
            "alerts_smtp_username": self.smtp_username.text().strip(),
            "alerts_smtp_password": self.smtp_password.text(),
            "alerts_email_from": self.email_from.text().strip(),
            "alerts_email_to": self.email_to.text().strip(),
            "alerts_email_subject_prefix": self.email_subject_prefix.text().strip() or "MediaRunner",
            "alerts_gchat_enabled": self.gchat_enabled.isChecked(),
            "alerts_gchat_webhook_url": self.gchat_webhook.text().strip(),
        })
        return apply_network_config(cfg)

    def get_config(self):
        return self._merged_config()

    def save(self):
        from mediarunner_core import save_network_config
        path = save_network_config(self._merged_config())
        self.status.setText(f"Saved: {path}")

    def restore_defaults(self):
        self.notify_success.setChecked(True)
        self.notify_failure.setChecked(True)
        self.notify_cancelled.setChecked(True)
        self.email_enabled.setChecked(False)
        self.smtp_host.clear()
        self.smtp_port.setValue(587)
        self.smtp_security.setCurrentIndex(0)
        self.smtp_username.clear()
        self.smtp_password.clear()
        self.email_from.clear()
        self.email_to.clear()
        self.email_subject_prefix.setText("MediaRunner")
        self.gchat_enabled.setChecked(False)
        self.gchat_webhook.clear()
        self.status.setText("Defaults loaded")

    def send_test(self):
        cfg = self._merged_config()
        if not cfg.get("alerts_email_enabled") and not cfg.get("alerts_gchat_enabled"):
            self.status.setText("Enable Email or Google Chat before sending a test")
            return
        self.test_btn.setEnabled(False)
        self.status.setText("Sending test alert…")
        sig = self.signals

        def work():
            try:
                from mediarunner_notifications import send_test_alerts
                results = send_test_alerts(cfg)
                if not results:
                    sig.status.emit("No alert provider enabled")
                    sig.finished.emit(False)
                    return
                failures = [r for r in results if r.get("status") != "sent"]
                if failures:
                    detail = "; ".join(f"{r.get('provider')}: {r.get('message')}" for r in failures)
                    sig.status.emit(f"Test alert failed: {detail}")
                    sig.finished.emit(False)
                    return
                providers = ", ".join(r.get("provider", "Alert") for r in results)
                sig.status.emit(f"Test alert sent: {providers}")
                sig.finished.emit(True)
            except Exception as exc:
                sig.status.emit(f"Test alert failed: {exc}")
                sig.finished.emit(False)

        threading.Thread(target=work, daemon=True, name="mediarunner-alert-test").start()


class FTPSettingsPage(QWidget):
    """FTP credentials, scan behavior, and camera map."""
    def __init__(self):
        super().__init__()
        self.signals = WorkerSignals()
        self.detected_status = {}
        self.thread = None
        self._build_ui()
        self.load_config()
        self.signals.scan_result.connect(self.on_scan_result)
        self.signals.finished.connect(lambda ok: self.detect_btn.setEnabled(True))

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        f, v = panel("FTP / RCP2")
        g = QGridLayout()
        g.setSpacing(10)
        self.user = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.rcp2_port = QSpinBox()
        self.rcp2_port.setRange(1, 65535)
        self.rcp2_port.setToolTip("RCP2 WebSocket control port. RED cameras default to 9998.")
        self.udp_port = QSpinBox()
        self.udp_port.setRange(1, 65535)
        self.udp_port.setToolTip("RED SDK UDP discovery port. The public SDK examples use UDP 1112.")
        self.timeout = QDoubleSpinBox()
        self.timeout.setRange(.25, 30)
        self.timeout.setSingleStep(.25)
        self.timeout.setSuffix(" sec")
        self.threads = QComboBox()
        self.threads.addItems(["4", "8", "12", "16", "24", "32"])
        self.skip_offline = QCheckBox("Skip offline cameras")
        g.addWidget(label("Username"), 0, 0)
        g.addWidget(self.user, 0, 1)
        g.addWidget(label("Password"), 0, 2)
        g.addWidget(self.password, 0, 3)
        g.addWidget(label("Port"), 1, 0)
        g.addWidget(self.port, 1, 1)
        g.addWidget(label("Timeout"), 1, 2)
        g.addWidget(self.timeout, 1, 3)
        g.addWidget(label("RCP2 Port"), 2, 0)
        g.addWidget(self.rcp2_port, 2, 1)
        g.addWidget(label("UDP Port"), 2, 2)
        g.addWidget(self.udp_port, 2, 3)
        g.addWidget(label("Scan Threads"), 3, 0)
        g.addWidget(self.threads, 3, 1)
        g.addWidget(self.skip_offline, 3, 2, 1, 2)
        v.addLayout(g)
        discovery_note = label("UDP discovery uses the RED RCP SDK packet flow on port 1112; this page also probes RCP2 WebSocket identity on configured IPs and active local subnets.", "muted")
        discovery_note.setWordWrap(True)
        v.addWidget(discovery_note)
        page_splitter = QSplitter(Qt.Vertical)
        page_splitter.setChildrenCollapsible(False)
        page_splitter.setHandleWidth(8)
        page_splitter.addWidget(f)

        f2, v2 = panel("Camera Map")
        self.camera_table = QTableWidget(0, 4)
        self.camera_table.setHorizontalHeaderLabels(["Camera", "FTP IP Address", "Status", "Details"])
        self.camera_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.camera_table.setAlternatingRowColors(True)
        v2.addWidget(self.camera_table)
        row = QHBoxLayout()
        self.detect_btn = QPushButton("Detect Cameras")
        self.detect_btn.setObjectName("primary")
        self.detect_btn.clicked.connect(self.detect)
        save = QPushButton("Save Settings")
        save.clicked.connect(self.save)
        add = QPushButton("Add Camera")
        add.clicked.connect(lambda: self.add_camera_row("", "", "—"))
        rem = QPushButton("Remove Selected")
        rem.clicked.connect(self.remove_selected)
        defaults = QPushButton("Restore Defaults")
        defaults.clicked.connect(self.restore_defaults)
        self.status = label("Ready", "muted")
        for b in [self.detect_btn, save, add, rem]:
            row.addWidget(b)
        row.addWidget(self.status)
        row.addStretch()
        row.addWidget(defaults)
        v2.addLayout(row)
        page_splitter.addWidget(f2)
        page_splitter.setStretchFactor(0, 0)
        page_splitter.setStretchFactor(1, 1)
        page_splitter.setSizes([280, 520])
        root.addWidget(page_splitter, 1)

    def add_camera_row(self, cam, ip, status="—", details=""):
        r = self.camera_table.rowCount()
        self.camera_table.insertRow(r)
        self.camera_table.setItem(r, 0, QTableWidgetItem(cam))
        self.camera_table.setItem(r, 1, QTableWidgetItem(ip))
        item = QTableWidgetItem(status)
        item.setForeground(QColor(status_color(status)))
        item.setTextAlignment(Qt.AlignCenter)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        self.camera_table.setItem(r, 2, item)
        detail_item = QTableWidgetItem(details)
        detail_item.setFlags(detail_item.flags() & ~Qt.ItemIsEditable)
        self.camera_table.setItem(r, 3, detail_item)

    def remove_selected(self):
        rows = sorted({i.row() for i in self.camera_table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.camera_table.removeRow(r)

    def load_config(self):
        from mediarunner_core import load_network_config
        cfg = load_network_config()
        self.user.setText(str(cfg.get("ftp_user", "ftp1")))
        self.password.setText(str(cfg.get("ftp_pass", "")))
        self.port.setValue(int(cfg.get("ftp_port", 21)))
        self.rcp2_port.setValue(int(cfg.get("rcp2_port", 9998)))
        self.udp_port.setValue(int(cfg.get("rcp2_udp_port", 1112)))
        self.timeout.setValue(float(cfg.get("ftp_timeout", 2.0)))
        thread_val = str(int(cfg.get("scan_threads", 24)))
        idx = self.threads.findText(thread_val)
        self.threads.setCurrentIndex(idx if idx >= 0 else self.threads.findText("24"))
        self.skip_offline.setChecked(bool(cfg.get("skip_offline", True)))
        self.camera_table.setRowCount(0)
        for cam, ip in sorted(cfg.get("cameras", {}).items()):
            self.add_camera_row(cam, ip, "—")

    def get_config(self):
        from mediarunner_core import load_network_config, apply_network_config
        cfg = load_network_config()
        cams = {}
        for r in range(self.camera_table.rowCount()):
            c = self.camera_table.item(r, 0)
            ip = self.camera_table.item(r, 1)
            if c and ip and c.text().strip() and ip.text().strip():
                cams[c.text().strip().upper()] = ip.text().strip()
        cfg.update({
            "ftp_user": self.user.text().strip() or "ftp1",
            "ftp_pass": self.password.text(),
            "ftp_port": self.port.value(),
            "rcp2_port": self.rcp2_port.value(),
            "rcp2_udp_port": self.udp_port.value(),
            "ftp_timeout": self.timeout.value(),
            "scan_threads": int(self.threads.currentText()),
            "skip_offline": self.skip_offline.isChecked(),
            "cameras": cams,
        })
        return apply_network_config(cfg)

    def save(self):
        from mediarunner_core import save_network_config
        path = save_network_config(self.get_config())
        self.status.setText(f"Saved: {path}")

    def restore_defaults(self):
        from mediarunner_core import default_network_config, load_network_config
        defaults = default_network_config()
        current = load_network_config()
        # Preserve non-networking app preferences from Settings/Alerts.
        networking_keys = {
            "ftp_user", "ftp_pass", "ftp_port", "rcp2_port", "rcp2_udp_port",
            "ftp_timeout", "scan_threads", "skip_offline", "cameras",
        }
        for key, value in current.items():
            if key not in networking_keys:
                defaults[key] = value
        self.user.setText(str(defaults.get("ftp_user", "ftp1")))
        self.password.setText(str(defaults.get("ftp_pass", "")))
        self.port.setValue(int(defaults.get("ftp_port", 21)))
        self.rcp2_port.setValue(int(defaults.get("rcp2_port", 9998)))
        self.udp_port.setValue(int(defaults.get("rcp2_udp_port", 1112)))
        self.timeout.setValue(float(defaults.get("ftp_timeout", 2.0)))
        idx = self.threads.findText(str(int(defaults.get("scan_threads", 24))))
        self.threads.setCurrentIndex(idx if idx >= 0 else self.threads.findText("24"))
        self.skip_offline.setChecked(bool(defaults.get("skip_offline", True)))
        self.camera_table.setRowCount(0)
        for cam, ip in sorted(defaults.get("cameras", {}).items()):
            self.add_camera_row(cam, ip, "—")
        self.status.setText("Defaults loaded")

    def detect(self):
        cfg = self.get_config()
        cams = cfg.get("cameras", {})
        self.detect_btn.setEnabled(False)
        self.status.setText("Scanning FTP/RCP2")
        sig = self.signals
        def work():
            try:
                from mediarunner_core import scan_cameras_detailed
                res = scan_cameras_detailed(
                    cams,
                    ftp_port=cfg.get("ftp_port", 21),
                    rcp2_port=cfg.get("rcp2_port", 9998),
                    timeout=cfg.get("ftp_timeout", 2.0),
                    max_workers=cfg.get("scan_threads", 24),
                    include_rcp2=True,
                )
                discovered = []
                discovery_error = ""
                try:
                    from mediarunner_red_wireless import scan_red_cameras
                    candidates = scan_red_cameras(
                        port=int(cfg.get("rcp2_port", 9998)),
                        identity_timeout=min(2.0, max(1.0, float(cfg.get("ftp_timeout", 2.0)) / 2.0)),
                        max_workers=max(8, int(cfg.get("scan_threads", 24))),
                    )
                    discovered = [
                        {
                            "host": str(getattr(cam, "host", "") or ""),
                            "port": int(getattr(cam, "port", cfg.get("rcp2_port", 9998)) or cfg.get("rcp2_port", 9998)),
                            "camera_name": str(getattr(cam, "camera_name", "") or ""),
                            "serial_number": str(getattr(cam, "serial_number", "") or ""),
                            "camera_version": str(getattr(cam, "camera_version", "") or ""),
                        }
                        for cam in candidates
                        if getattr(cam, "ok", False)
                    ]
                except Exception as exc:
                    discovery_error = str(exc)
                sig.scan_result.emit({
                    "mode": "network_scan",
                    "configured": res,
                    "discovered": discovered,
                    "udp_port": int(cfg.get("rcp2_udp_port", 1112)),
                    "udp_note": "RED SDK UDP discovery uses generated packets on UDP 1112; RCP2 WebSocket scan is used here for pure-Python discovery.",
                    "discovery_error": discovery_error,
                })
                sig.finished.emit(True)
            except Exception:
                sig.finished.emit(False)
        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()

    def on_scan_result(self, res):
        if not isinstance(res, dict) or res.get("mode") != "network_scan":
            self.detected_status = res
            online = sum(1 for v in res.values() if v)
            self.status.setText(f"{online} online / {len(res)} configured")
            for r in range(self.camera_table.rowCount()):
                cam = self.camera_table.item(r, 0).text().strip().upper() if self.camera_table.item(r, 0) else ""
                if cam in res:
                    st = "ONLINE" if res[cam] else "OFFLINE"
                    item = QTableWidgetItem(st)
                    item.setForeground(QColor(status_color(st)))
                    item.setTextAlignment(Qt.AlignCenter)
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    self.camera_table.setItem(r, 2, item)
            return

        configured = dict(res.get("configured") or {})
        discovered = list(res.get("discovered") or [])
        self.detected_status = {
            label: bool(detail.get("transfer_ready"))
            for label, detail in configured.items()
            if isinstance(detail, dict)
        }
        ftp_ready = sum(1 for detail in configured.values() if isinstance(detail, dict) and detail.get("transfer_ready"))
        rcp2_seen = sum(1 for detail in configured.values() if isinstance(detail, dict) and detail.get("rcp2_online"))
        known_ips = {
            str(detail.get("ip") or "").strip()
            for detail in configured.values()
            if isinstance(detail, dict) and str(detail.get("ip") or "").strip()
        }
        discovered_new = 0
        for r in range(self.camera_table.rowCount()):
            cam = self.camera_table.item(r, 0).text().strip().upper() if self.camera_table.item(r, 0) else ""
            if cam in configured:
                detail = configured[cam]
                st = "OFFLINE"
                if detail.get("ftp_online") and detail.get("rcp2_online"):
                    st = "FTP+RCP2"
                elif detail.get("ftp_online"):
                    st = "FTP"
                elif detail.get("rcp2_online"):
                    st = "RCP2 ONLY"
                detail_text = self._network_detail_text(detail)
                item = QTableWidgetItem(st)
                item.setForeground(QColor(status_color(st)))
                item.setTextAlignment(Qt.AlignCenter)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                item.setToolTip(detail_text)
                self.camera_table.setItem(r, 2, item)
                detail_item = QTableWidgetItem(detail_text)
                detail_item.setFlags(detail_item.flags() & ~Qt.ItemIsEditable)
                detail_item.setToolTip(detail_text)
                self.camera_table.setItem(r, 3, detail_item)
        existing_labels = {
            self.camera_table.item(r, 0).text().strip().upper()
            for r in range(self.camera_table.rowCount())
            if self.camera_table.item(r, 0)
        }
        existing_ips = {
            self.camera_table.item(r, 1).text().strip()
            for r in range(self.camera_table.rowCount())
            if self.camera_table.item(r, 1)
        }
        existing_ips.update(known_ips)
        for cam in discovered:
            host = str(cam.get("host") or "").strip()
            if not host or host in existing_ips:
                continue
            label_name = self._next_discovered_label(existing_labels)
            existing_labels.add(label_name)
            existing_ips.add(host)
            detail_text = self._network_detail_text(cam)
            self.add_camera_row(label_name, host, "RCP2 DISCOVERED", detail_text)
            discovered_new += 1
        status_parts = [f"{ftp_ready} FTP-ready / {len(configured)} configured", f"{rcp2_seen + discovered_new} RCP2 detected"]
        if discovered_new:
            status_parts.append(f"{discovered_new} added")
        if res.get("discovery_error"):
            status_parts.append("subnet scan warning")
        self.status.setText(" | ".join(status_parts))

    def _network_detail_text(self, detail: dict) -> str:
        parts = [
            str(detail.get("camera_name") or "").strip(),
            f"Serial {str(detail.get('serial_number') or '').strip()}" if str(detail.get("serial_number") or "").strip() else "",
            str(detail.get("camera_version") or "").strip(),
        ]
        text = "  ".join(part for part in parts if part)
        if not text and detail.get("error"):
            text = str(detail.get("error") or "")[:120]
        return text or "—"

    def _next_discovered_label(self, existing: set[str]) -> str:
        index = 1
        while True:
            candidate = f"RED{index}"
            if candidate not in existing:
                return candidate
            index += 1

    def online_cameras(self):
        return dict(self.detected_status)


class FTPUnifiedPage(QWidget):
    """Unified FTP pulls (concept port): one page, transport is a segmented
    mode — the 42-camera wired array or a single camera over Wi-Fi/Ethernet.
    Wraps the existing battle-tested FTPPage and RedWirelessPage unchanged."""

    def __init__(self, array_page: QWidget, wireless_page: QWidget):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        row = QHBoxLayout()
        mode_label = label("Pull from", "muted")
        row.addWidget(mode_label)
        self.mode = SegmentedControl(["Camera Array", "Single Camera"])
        row.addWidget(self.mode, 1)
        v.addLayout(row)
        self.mode_stack = QStackedWidget()
        self.mode_stack.addWidget(array_page)
        self.mode_stack.addWidget(wireless_page)
        v.addWidget(self.mode_stack, 1)
        self.mode.changed.connect(
            lambda t: self.mode_stack.setCurrentIndex(0 if t == "Camera Array" else 1)
        )


class MediaRunnerWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("MediaRunner"); self.setMinimumSize(1180,760); self.resize(1360,860)
        central=QWidget(); self.setCentralWidget(central); root=QHBoxLayout(central); root.setContentsMargins(0,0,0,0); root.setSpacing(0)
        sidebar=QFrame(); sidebar.setObjectName("sidebar"); sidebar.setFixedWidth(232); sv=QVBoxLayout(sidebar); sv.setContentsMargins(14,18,14,18); sv.setSpacing(6)
        # Brand header: existing MediaRunner logo (unchanged — it must stay
        # consistent across all artifacts) + wordmark + version, concept-style.
        brand_row = QHBoxLayout(); brand_row.setSpacing(10); brand_row.setContentsMargins(8, 4, 8, 12)
        # 0.3 brand: MR gradient mark everywhere — sidebar, HTML reports, FTP
        # reports, custom reports all render the same mark.
        mark = QLabel("MR"); mark.setObjectName("brand_mark"); mark.setFixedSize(34, 34); mark.setAlignment(Qt.AlignCenter)
        brand_row.addWidget(mark)
        brand_text = QVBoxLayout(); brand_text.setSpacing(0)
        name_lbl = QLabel("MediaRunner"); name_lbl.setObjectName("brand_name")
        sub_lbl = QLabel("v0.3.0-beta"); sub_lbl.setObjectName("brand_sub")
        brand_text.addWidget(name_lbl); brand_text.addWidget(sub_lbl)
        brand_row.addLayout(brand_text); brand_row.addStretch()
        sv.addLayout(brand_row)
        self.nav_buttons=[]; self.stack=QStackedWidget()
        self.dashboard=DashboardPage(); self.app_settings=AppSettingsPage(); self.alerts=AlertsPage(); self.settings=FTPSettingsPage(); self.transfer=TransferPage(self.dashboard, lambda: self.show_page(0), self.app_settings); self.ftp=FTPPage(self.settings, self.dashboard, lambda: self.show_page(0), self.app_settings); self.red_wireless=RedWirelessPage(self.settings, self.dashboard, lambda: self.show_page(0), self.app_settings); self.metadata=MetadataPage(); self.reports=ReportsPage(); self.validation=ValidationPage()
        self.ftp_unified = FTPUnifiedPage(self.ftp, self.red_wireless)
        self.dashboard.stop_callback = self.request_stop
        self.settings.signals.scan_result.connect(self.dashboard.update_ftp_camera_count)
        self.ftp.signals.scan_result.connect(self.dashboard.update_ftp_camera_count)
        pages=[("Dashboard", self.dashboard), ("Offload", self.transfer), ("FTP", self.ftp_unified), ("Metadata", self.metadata), ("Reports", self.reports), ("Networking", self.settings), ("Settings", self.app_settings), ("Alerts", self.alerts), ("Validation", self.validation)]
        for idx,(title,page) in enumerate(pages):
            btn=QPushButton(title); btn.setObjectName("nav_active" if idx==0 else "nav"); btn.clicked.connect(lambda checked=False, i=idx: self.show_page(i)); sv.addWidget(btn); self.nav_buttons.append(btn); self.stack.addWidget(page)
            if title == "Validation":
                # Engineering-only page: hidden until unlocked in Settings.
                btn.setVisible(False)
                self.validation_nav_btn = btn
        self.app_settings.engineering_unlock_callback = self.set_engineering_unlocked
        sv.addStretch(); root.addWidget(sidebar)
        main=QWidget(); mv=QVBoxLayout(main); mv.setContentsMargins(24,20,24,20); mv.setSpacing(12)
        top=QFrame(); top.setObjectName("topbar"); th=QHBoxLayout(top); th.setContentsMargins(0,0,0,12)
        # Concept clock: compact monospace, replacing the segmented LCD display.
        self.header=label("Dashboard","page_title"); self.clock=QLabel(""); self.clock.setObjectName("clock_display"); th.addWidget(self.header); th.addStretch(); th.addWidget(self.clock); mv.addWidget(top)
        mv.addWidget(self.stack,1); root.addWidget(main,1)
        from PySide6.QtCore import QTimer
        timer=QTimer(self); timer.timeout.connect(self.tick); timer.start(1000); self.tick()

    def set_engineering_unlocked(self, unlocked: bool):
        btn = getattr(self, "validation_nav_btn", None)
        if btn is not None:
            btn.setVisible(bool(unlocked))
        if not unlocked and self.stack.currentWidget() is getattr(self, "validation", None):
            self.show_page(0)

    def _pages_with_active_workers(self):
        pages = []
        for page in (getattr(self, "transfer", None), getattr(self, "ftp", None),
                     getattr(self, "red_wireless", None), getattr(self, "metadata", None),
                     getattr(self, "validation", None)):
            if page is None:
                continue
            worker = getattr(page, "thread", None)
            if worker is not None and getattr(worker, "is_alive", lambda: False)():
                pages.append(page)
        return pages

    def closeEvent(self, event):
        """Audit fix #5: never silently kill workers mid-write on quit.

        Warn when a job is running; on confirmed quit, request cancellation and
        give workers a few seconds to land cleanly (.part files stay safe)."""
        active = self._pages_with_active_workers()
        if not active:
            event.accept()
            return
        reply = QMessageBox.question(
            self,
            "Transfer in progress",
            "A transfer is still running.\n\nStop it and quit? In-progress files remain as .part "
            "and will be cleaned up or resumed on the next run.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            event.ignore()
            return
        for page in active:
            cancel = getattr(page, "cancel_event", None)
            if cancel is not None:
                try:
                    cancel.set()
                except Exception:
                    pass
        deadline = time.time() + 5.0
        for page in active:
            worker = getattr(page, "thread", None)
            if worker is not None and getattr(worker, "is_alive", lambda: False)():
                worker.join(timeout=max(0.1, deadline - time.time()))
        event.accept()

    def request_stop(self):
        current = self.stack.currentWidget() if hasattr(self, "stack") else None
        candidates = []
        for page in (current, getattr(self, "ftp", None), getattr(self, "transfer", None), getattr(self, "red_wireless", None)):
            if page is None or page is getattr(self, "dashboard", None) or page in candidates:
                continue
            candidates.append(page)
        for page in candidates:
            stop = getattr(page, "request_stop", None)
            stop_btn = getattr(page, "stop_btn", None)
            page_is_stoppable = page is current or (stop_btn is not None and stop_btn.isEnabled())
            if callable(stop) and page_is_stoppable:
                try:
                    stop()
                except Exception as exc:
                    if getattr(self, "dashboard", None):
                        self.dashboard.append_activity(f"Stop request failed: {exc}", RED)
                return
        if getattr(self, "dashboard", None):
            self.dashboard.append_activity("Stop requested, but the active page does not support cancellation.", ORANGE)

    def show_page(self, idx:int):
        self.stack.setCurrentIndex(idx)
        for i,b in enumerate(self.nav_buttons): b.setObjectName("nav_active" if i==idx else "nav"); b.style().unpolish(b); b.style().polish(b)
        self.header.setText(self.nav_buttons[idx].text())
    def tick(self): self.clock.setText(format_mediarunner_clock(datetime.now()))


def main():
    from mediarunner_logging import setup_logging
    setup_logging()
    app=QApplication(sys.argv); app.setApplicationName("MediaRunner"); app.setStyle("Fusion")
    pal=QPalette(); pal.setColor(QPalette.Window,QColor(APP_BG)); pal.setColor(QPalette.WindowText,QColor(TEXT)); pal.setColor(QPalette.Base,QColor(INPUT)); pal.setColor(QPalette.AlternateBase,QColor("#121C26")); pal.setColor(QPalette.Text,QColor("#E8F0F7")); pal.setColor(QPalette.Button,QColor("#243444")); pal.setColor(QPalette.ButtonText,QColor("#DCE7EF")); pal.setColor(QPalette.Highlight,QColor(ACCENT)); pal.setColor(QPalette.HighlightedText,QColor("#0B1117")); app.setPalette(pal); app.setStyleSheet(STYLE)
    win=MediaRunnerWindow(); win.show(); sys.exit(app.exec())

if __name__ == "__main__":
    main()
