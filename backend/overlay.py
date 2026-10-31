"""Lyra — island UI matched to the connected glass mockup (dark theme)."""

from __future__ import annotations

import json
import math
import re
import sys
import ctypes
from ctypes import wintypes
from pathlib import Path

from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, pyqtSignal, QObject, QThread,
    QAbstractNativeEventFilter, pyqtSlot, QRectF, QPointF, QSize,
)
from PyQt6.QtGui import (
    QColor, QIcon, QPixmap, QAction, QPainter, QPainterPath, QFont,
    QLinearGradient, QPen,
)
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QHBoxLayout, QVBoxLayout, QSystemTrayIcon,
    QMenu, QLineEdit, QPushButton, QCheckBox, QComboBox, QGridLayout,
    QSizePolicy, QFrame, QScrollArea, QListWidget, QListWidgetItem,
    QStackedWidget, QFileDialog, QAbstractItemView,
)

import ui
import settings_store

MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
VK_C = 0x43
WM_HOTKEY = 0x0312
HOTKEY_ID = 1

SHORTCUTS_PATH = Path(__file__).resolve().parent / "shortcuts" / "shortcuts.json"

# Dark glass palette (mockup)
BG = "#12131A"
CARD = "#1A1B24"
CARD2 = "#22232E"
BORDER = "#2E303C"
TEXT = "#F0F0F5"
MUTED = "#8B8D9A"
GREEN = "#3DDC84"
BLUE = "#6B8CFF"
PURPLE = "#9B7BFF"


class GlobalHotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, notch: "AssistantNotch"):
        super().__init__()
        self._notch = notch

    def nativeEventFilter(self, eventType, message):
        if eventType == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                if self._notch.isVisible() and self._notch.windowOpacity() > 0.5:
                    self._notch.standby()
                else:
                    self._notch.display_speech(self._notch._last_text or "…")
        return False, 0


class UiBridge(QObject):
    chat = pyqtSignal(str, str, str)
    state = pyqtSignal(str)


def _install_bridge() -> UiBridge:
    bridge = UiBridge()

    def _on_event(event, **kwargs):
        if event == "chat":
            bridge.chat.emit(
                kwargs.get("role", ""),
                kwargs.get("text", ""),
                kwargs.get("detail") or "",
            )
        elif event == "state":
            bridge.state.emit(kwargs.get("state", "idle"))

    ui.add_listener(_on_event)
    return bridge


class EngineThread(QThread):
    def run(self) -> None:
        import main as engine_main
        try:
            engine_main.main()
        except SystemExit:
            pass


class WaveIcon(QWidget):
    """Simple vertical bars icon (listening / waveform)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(22, 16)
        self._phase = 0.0
        self._anim = False
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)

    def set_active(self, on: bool):
        self._anim = on
        if on and not self._timer.isActive():
            self._timer.start()
        if not on:
            self._timer.stop()
            self._phase = 0.0
        self.update()

    def _tick(self):
        self._phase += 0.35
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        bars = 4
        gap = 3
        bw = 2.5
        total = bars * bw + (bars - 1) * gap
        x0 = (self.width() - total) / 2
        for i in range(bars):
            if self._anim:
                h = 4 + 8 * abs(math.sin(self._phase + i * 0.7))
            else:
                h = [6, 12, 8, 10][i]
            x = x0 + i * (bw + gap)
            y = (self.height() - h) / 2
            p.setBrush(QColor(BLUE))
            p.drawRoundedRect(QRectF(x, y, bw, h), 1.2, 1.2)


class BouncingDots(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(34, 16)
        self._phase = 0.0
        self._active = False
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def start(self):
        self._active = True
        if not self._timer.isActive():
            self._timer.start()
        self.show()

    def stop(self):
        self._active = False
        self._timer.stop()
        self.update()
        self.hide()

    def _tick(self):
        self._phase = (self._phase + 0.045) % (math.pi * 2)
        self.update()

    def paintEvent(self, event):
        if not self._active:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        for i, x in enumerate((7, 17, 27)):
            b = max(0.0, math.sin(self._phase - i * 0.55)) * 4.5
            p.setBrush(QColor(180, 190, 255, int(150 + 90 * (b / 4.5))))
            p.drawEllipse(QPointF(x, 11 - b), 2.5, 2.5)


class AssistantNotch(QWidget):
    """Top pill + optional connected settings card (mockup layout)."""

    STEM_H = 18
    PILL_H = 40
    CARD_W = 420
    CARD_H = 620

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

        self._last_text = ""
        self._state = "idle"
        self._status_hint = ""
        self._expanded = False

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 8, 16, 18)
        root.setSpacing(0)
        root.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)

        # ===== TOP PILL =====
        self.pill = QFrame()
        self.pill.setObjectName("pill")
        self.pill.setFixedHeight(self.PILL_H)
        self.pill.setStyleSheet(f"""
            #pill {{
                background-color: #000000;
                border: 1px solid {BORDER};
                border-radius: 20px;
            }}
        """)
        self.pill.setCursor(Qt.CursorShape.PointingHandCursor)
        pl = QHBoxLayout(self.pill)
        pl.setContentsMargins(14, 0, 14, 0)
        pl.setSpacing(10)

        self.dot = QLabel("●")
        self.dot.setStyleSheet(f"color: {GREEN}; font-size: 11px; background: transparent;")

        self.name_lbl = QLabel(settings_store.wake_name())
        self.name_lbl.setStyleSheet(
            f"color: {TEXT}; font-size: 13px; font-weight: 600; background: transparent;"
        )

        self.transcript_lbl = QLabel("")
        self.transcript_lbl.setStyleSheet(
            f"color: {MUTED}; font-size: 12px; background: transparent;"
        )
        self.transcript_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self.bouncy = BouncingDots()
        self.bouncy.hide()
        self.wave = WaveIcon()

        pl.addWidget(self.dot)
        pl.addWidget(self.name_lbl)
        pl.addWidget(self.transcript_lbl, 1)
        pl.addWidget(self.bouncy)
        pl.addWidget(self.wave)
        root.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignHCenter)

        # Stem spacer (drawn in paintEvent)
        self.stem_space = QWidget()
        self.stem_space.setFixedHeight(self.STEM_H)
        self.stem_space.hide()
        root.addWidget(self.stem_space)

        # ===== SETTINGS CARD =====
        self.card = QFrame()
        self.card.setObjectName("card")
        self.card.setFixedSize(self.CARD_W, self.CARD_H)
        self.card.setStyleSheet(f"""
            #card {{
                background-color: #000000;
                border: 1px solid {BORDER};
                border-radius: 22px;
            }}
            QLabel {{ background: transparent; color: {TEXT}; }}
        """)
        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(20, 16, 20, 18)
        cl.setSpacing(14)

        # Header
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        d2 = QLabel("●")
        d2.setStyleSheet(f"color: {GREEN}; font-size: 11px;")
        n2 = QLabel(settings_store.wake_name())
        n2.setObjectName("cardName")
        n2.setStyleSheet(f"color: {TEXT}; font-size: 14px; font-weight: 600;")
        self._card_name_lbl = n2
        hdr.addWidget(d2)
        hdr.addWidget(n2)
        hdr.addStretch(1)

        self.btn_settings = QPushButton("⚙  Settings")
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_settings.setStyleSheet(f"""
            QPushButton {{
                background: {CARD2}; color: {MUTED};
                border: 1px solid {BORDER}; border-radius: 14px;
                padding: 6px 12px; font-size: 12px;
            }}
            QPushButton:hover {{ color: {TEXT}; border-color: #4A4C5A; }}
        """)
        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedSize(28, 28)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setStyleSheet(f"""
            QPushButton {{
                background: {CARD2}; color: {MUTED};
                border: 1px solid {BORDER}; border-radius: 14px;
                font-size: 12px;
            }}
            QPushButton:hover {{ color: {TEXT}; }}
        """)
        self.btn_close.clicked.connect(self._collapse)
        hdr.addWidget(self.btn_settings)
        hdr.addWidget(self.btn_close)
        cl.addLayout(hdr)

        self.quote = QLabel("")
        self.quote.setStyleSheet(f"color: {MUTED}; font-size: 12px; font-style: italic;")
        self.quote.setWordWrap(True)
        cl.addWidget(self.quote)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {BORDER};")
        cl.addWidget(line)

        # Agent Name
        cl.addWidget(self._h("Agent Name"))
        row = QHBoxLayout()
        row.setSpacing(10)
        self.wake_edit = QLineEdit()
        self.wake_edit.setText(settings_store.wake_name())
        self.wake_edit.setStyleSheet(self._field_qss())
        self.save_btn = QPushButton("Save")
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.setFixedWidth(84)
        self.save_btn.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #5B8CFF, stop:1 #9B7BFF);
                color: white; border: none; border-radius: 12px;
                padding: 9px 14px; font-size: 12px; font-weight: 700;
            }}
            QPushButton:hover {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 #6C9AFF, stop:1 #AB8BFF); }}
        """)
        self.save_btn.clicked.connect(self._save_wake_name)
        row.addWidget(self.wake_edit, 1)
        row.addWidget(self.save_btn)
        cl.addLayout(row)

        # Stacked body: main settings vs shortcut editor
        self.body_stack = QStackedWidget()
        cl.addWidget(self.body_stack, 1)

        # ----- Page 0: main settings -----
        self.main_page = QWidget()
        mp = QVBoxLayout(self.main_page)
        mp.setContentsMargins(0, 0, 0, 0)
        mp.setSpacing(12)

        # Shortcuts
        sc_row = QHBoxLayout()
        sc_row.addWidget(self._h("Shortcuts"))
        sc_row.addStretch(1)
        self.add_sc = QPushButton("＋  Add New Shortcut")
        self.add_sc.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_sc.setStyleSheet(f"""
            QPushButton {{
                background: {CARD2}; color: {BLUE};
                border: 1px solid {BORDER}; border-radius: 12px;
                padding: 6px 12px; font-size: 11px; font-weight: 600;
            }}
            QPushButton:hover {{ border-color: {BLUE}; }}
        """)
        self.add_sc.clicked.connect(self._open_shortcut_editor_new)
        sc_row.addWidget(self.add_sc)
        mp.addLayout(sc_row)

        self.cards_host = QWidget()
        self.cards_grid = QGridLayout(self.cards_host)
        self.cards_grid.setContentsMargins(0, 0, 0, 0)
        self.cards_grid.setSpacing(8)
        mp.addWidget(self.cards_host)

        # Settings
        mp.addWidget(self._h("Settings"))

        self.mic_combo = QComboBox()
        self.mic_combo.addItem("Default Microphone")
        try:
            import pyaudio
            pa = pyaudio.PyAudio()
            for i in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(i)
                max_input_channels = info.get("maxInputChannels", 0)
                if isinstance(max_input_channels, (int, float)) and max_input_channels > 0:
                    n = str(info.get("name") or f"Device {i}")
                    if self.mic_combo.findText(n) < 0:
                        self.mic_combo.addItem(n)
            pa.terminate()
        except Exception:
            pass
        self.mic_combo.setStyleSheet(self._combo_qss())
        mp.addLayout(self._setting_row("🎤", "Audio Input:", self.mic_combo))

        self.model_combo = QComboBox()
        self.model_combo.addItems(["Local LLM / Speech API", "Local STT + Groq"])
        self.model_combo.setStyleSheet(self._combo_qss())
        mp.addLayout(self._setting_row("🎙", "Voice Model:", self.model_combo))

        # TTS voice (edge-tts neural voices)
        self.tts_combo = QComboBox()
        self.tts_combo.addItems([
            "en-US-AriaNeural",
            "en-US-JennyNeural",
            "en-US-AvaNeural",
            "en-US-GuyNeural",
            "en-US-ChristopherNeural",
            "en-GB-SoniaNeural",
            "en-GB-RyanNeural",
        ])
        self.tts_combo.setStyleSheet(self._combo_qss())
        self.tts_combo.currentTextChanged.connect(self._on_tts_voice)
        mp.addLayout(self._setting_row("🔊", "TTS Voice:", self.tts_combo))

        # Screen watch
        sw = QHBoxLayout()
        sw_icon = QLabel("👁")
        sw_icon.setStyleSheet(f"color: {BLUE}; font-size: 13px;")
        sw.addWidget(sw_icon)
        sw_lab = QLabel("Screen Watch:")
        sw_lab.setStyleSheet(f"color: {MUTED}; font-size: 12px; min-width: 90px;")
        sw.addWidget(sw_lab)
        self.screen_watch_cb = QCheckBox("On")
        self.screen_watch_cb.setStyleSheet(self._checkbox_qss())
        self.screen_watch_cb.stateChanged.connect(self._on_screen_watch)
        sw.addWidget(self.screen_watch_cb)
        self.watch_interval_combo = QComboBox()
        self.watch_interval_combo.addItems(["0.5s", "1s", "2s", "3s"])
        self.watch_interval_combo.setStyleSheet(self._combo_qss())
        self.watch_interval_combo.setMinimumWidth(70)
        self.watch_interval_combo.currentTextChanged.connect(self._on_watch_interval)
        sw.addWidget(self.watch_interval_combo)
        sw.addStretch(1)
        mp.addLayout(sw)

        # Confirm before click
        conf = QHBoxLayout()
        conf_icon = QLabel("✓")
        conf_icon.setStyleSheet(f"color: {GREEN}; font-size: 13px;")
        conf.addWidget(conf_icon)
        conf_lab = QLabel("Clicks:")
        conf_lab.setStyleSheet(f"color: {MUTED}; font-size: 12px; min-width: 90px;")
        conf.addWidget(conf_lab)
        self.confirm_click_cb = QCheckBox("Confirm before click")
        self.confirm_click_cb.setStyleSheet(self._checkbox_qss())
        self.confirm_click_cb.setChecked(True)
        self.confirm_click_cb.stateChanged.connect(self._on_confirm_click)
        conf.addWidget(self.confirm_click_cb)
        conf.addStretch(1)
        mp.addLayout(conf)

        # Window toggles
        dyn = QHBoxLayout()
        icon = QLabel("✦")
        icon.setStyleSheet(f"color: {PURPLE}; font-size: 14px;")
        dyn.addWidget(icon)
        lab = QLabel("Window:")
        lab.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        dyn.addWidget(lab)
        self.always_top = QCheckBox("Always on Top")
        self.auto_collapse = QCheckBox("Auto-Collapse")
        for cb in (self.always_top, self.auto_collapse):
            cb.setStyleSheet(self._checkbox_qss())
        self.always_top.setChecked(True)
        self.auto_collapse.setChecked(True)
        self.always_top.stateChanged.connect(self._on_always_top)
        self.auto_collapse.stateChanged.connect(self._on_auto_collapse)
        dyn.addWidget(self.always_top)
        dyn.addWidget(self.auto_collapse)
        dyn.addStretch(1)
        mp.addLayout(dyn)

        mp.addStretch(1)
        self.body_stack.addWidget(self.main_page)

        # ----- Page 1: shortcut editor -----
        self.editor_page = QWidget()
        ep = QVBoxLayout(self.editor_page)
        ep.setContentsMargins(0, 4, 0, 4)
        ep.setSpacing(12)

        ed_hdr = QHBoxLayout()
        ed_hdr.setContentsMargins(0, 0, 0, 4)
        self.editor_title = QLabel("New Shortcut")
        self.editor_title.setStyleSheet(f"color: {TEXT}; font-size: 13px; font-weight: 700;")
        ed_hdr.addWidget(self.editor_title)
        ed_hdr.addStretch(1)
        self.editor_back = QPushButton("← Back")
        self.editor_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.editor_back.setStyleSheet(f"""
            QPushButton {{
                background: {CARD2}; color: {MUTED};
                border: 1px solid {BORDER}; border-radius: 10px;
                padding: 6px 12px; font-size: 11px;
            }}
            QPushButton:hover {{ color: {TEXT}; }}
        """)
        self.editor_back.clicked.connect(self._close_shortcut_editor)
        ed_hdr.addWidget(self.editor_back)
        ep.addLayout(ed_hdr)

        ep.addWidget(self._h("Name"))
        self.sc_name_edit = QLineEdit()
        self.sc_name_edit.setPlaceholderText("e.g. Work Mode")
        self.sc_name_edit.setStyleSheet(self._field_qss())
        self.sc_name_edit.setMinimumHeight(36)
        ep.addWidget(self.sc_name_edit)

        ep.addSpacing(4)
        ep.addWidget(self._h("Wake / Trigger phrases"))
        self.sc_triggers_edit = QLineEdit()
        self.sc_triggers_edit.setPlaceholderText("comma-separated, e.g. work mode, start work")
        self.sc_triggers_edit.setStyleSheet(self._field_qss())
        self.sc_triggers_edit.setMinimumHeight(36)
        ep.addWidget(self.sc_triggers_edit)

        ep.addSpacing(4)
        apps_row = QHBoxLayout()
        apps_row.addWidget(self._h("Actions / apps"))
        apps_row.addStretch(1)
        ep.addLayout(apps_row)

        self.sc_apps_list = QListWidget()
        self.sc_apps_list.setMinimumHeight(80)
        self.sc_apps_list.setMaximumHeight(120)
        self.sc_apps_list.setStyleSheet(f"""
            QListWidget {{
                background: {CARD2}; color: {TEXT};
                border: 1px solid {BORDER}; border-radius: 10px;
                padding: 6px; font-size: 12px;
            }}
            QListWidget::item {{ padding: 6px 8px; }}
            QListWidget::item:selected {{ background: #2A3555; }}
        """)
        self.sc_apps_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        ep.addWidget(self.sc_apps_list)

        add_app_row = QHBoxLayout()
        add_app_row.setSpacing(8)
        add_app_row.setContentsMargins(0, 2, 0, 0)
        self.sc_app_combo = QComboBox()
        self.sc_app_combo.setStyleSheet(self._combo_qss())
        try:
            from actions.apps import LOCAL_APPS
            known = sorted(LOCAL_APPS.keys())
        except Exception:
            known = [
                "chrome", "discord", "spotify", "steam", "vscode", "notepad",
                "calculator", "explorer", "terminal", "settings",
            ]
        self.sc_app_combo.addItem("— known app —")
        for k in known:
            self.sc_app_combo.addItem(k)
        self.sc_app_combo.setMinimumWidth(140)
        self.sc_app_combo.setMinimumHeight(34)
        add_app_row.addWidget(self.sc_app_combo, 1)

        self.sc_add_known_btn = QPushButton("Add")
        self.sc_add_known_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sc_add_known_btn.setStyleSheet(self._small_btn_qss())
        self.sc_add_known_btn.setMinimumHeight(34)
        self.sc_add_known_btn.clicked.connect(self._sc_add_known_app)
        add_app_row.addWidget(self.sc_add_known_btn)
        ep.addLayout(add_app_row)

        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        path_row.setContentsMargins(0, 2, 0, 0)
        self.sc_path_edit = QLineEdit()
        self.sc_path_edit.setPlaceholderText("Custom path / command")
        self.sc_path_edit.setStyleSheet(self._field_qss())
        self.sc_path_edit.setMinimumHeight(34)
        path_row.addWidget(self.sc_path_edit, 1)
        self.sc_browse_btn = QPushButton("…")
        self.sc_browse_btn.setFixedSize(34, 34)
        self.sc_browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sc_browse_btn.setStyleSheet(self._small_btn_qss())
        self.sc_browse_btn.clicked.connect(self._sc_browse_path)
        path_row.addWidget(self.sc_browse_btn)
        self.sc_add_path_btn = QPushButton("Add path")
        self.sc_add_path_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sc_add_path_btn.setStyleSheet(self._small_btn_qss())
        self.sc_add_path_btn.setMinimumHeight(34)
        self.sc_add_path_btn.clicked.connect(self._sc_add_path_app)
        path_row.addWidget(self.sc_add_path_btn)
        ep.addLayout(path_row)

        # Phase 4 — extra action types (volume, wait, mute, lock, screenshot)
        extra_row = QHBoxLayout()
        extra_row.setSpacing(8)
        self.sc_extra_combo = QComboBox()
        self.sc_extra_combo.setStyleSheet(self._combo_qss())
        self.sc_extra_combo.addItems([
            "— extra action —",
            "mute",
            "volume up",
            "volume down",
            "wait 1s",
            "wait 2s",
            "lock screen",
            "screenshot",
        ])
        self.sc_extra_combo.setMinimumHeight(34)
        extra_row.addWidget(self.sc_extra_combo, 1)
        self.sc_add_extra_btn = QPushButton("Add action")
        self.sc_add_extra_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sc_add_extra_btn.setStyleSheet(self._small_btn_qss())
        self.sc_add_extra_btn.setMinimumHeight(34)
        self.sc_add_extra_btn.clicked.connect(self._sc_add_extra_action)
        extra_row.addWidget(self.sc_add_extra_btn)
        self.sc_move_up_btn = QPushButton("↑")
        self.sc_move_up_btn.setFixedSize(34, 34)
        self.sc_move_up_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sc_move_up_btn.setStyleSheet(self._small_btn_qss())
        self.sc_move_up_btn.clicked.connect(lambda: self._sc_move_item(-1))
        extra_row.addWidget(self.sc_move_up_btn)
        self.sc_move_down_btn = QPushButton("↓")
        self.sc_move_down_btn.setFixedSize(34, 34)
        self.sc_move_down_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sc_move_down_btn.setStyleSheet(self._small_btn_qss())
        self.sc_move_down_btn.clicked.connect(lambda: self._sc_move_item(1))
        extra_row.addWidget(self.sc_move_down_btn)
        ep.addLayout(extra_row)

        self.sc_remove_app_btn = QPushButton("Remove selected")
        self.sc_remove_app_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sc_remove_app_btn.setStyleSheet(f"""
            QPushButton {{
                background: {CARD2}; color: #E07070;
                border: 1px solid {BORDER}; border-radius: 10px;
                padding: 8px 12px; font-size: 11px;
            }}
            QPushButton:hover {{ border-color: #E07070; }}
        """)
        self.sc_remove_app_btn.setMinimumHeight(34)
        self.sc_remove_app_btn.clicked.connect(self._sc_remove_app)
        ep.addWidget(self.sc_remove_app_btn)

        ep.addSpacing(8)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.sc_delete_btn = QPushButton("Delete")
        self.sc_delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sc_delete_btn.setStyleSheet(f"""
            QPushButton {{
                background: {CARD2}; color: #E07070;
                border: 1px solid {BORDER}; border-radius: 12px;
                padding: 10px 16px; font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{ border-color: #E07070; }}
        """)
        self.sc_delete_btn.setMinimumHeight(38)
        self.sc_delete_btn.clicked.connect(self._sc_delete)
        self.sc_delete_btn.hide()
        btn_row.addWidget(self.sc_delete_btn)
        btn_row.addStretch(1)
        self.sc_save_btn = QPushButton("Save Shortcut")
        self.sc_save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sc_save_btn.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #5B8CFF, stop:1 #9B7BFF);
                color: white; border: none; border-radius: 12px;
                padding: 10px 18px; font-size: 12px; font-weight: 700;
            }}
            QPushButton:hover {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 #6C9AFF, stop:1 #AB8BFF); }}
        """)
        self.sc_save_btn.setMinimumHeight(38)
        self.sc_save_btn.clicked.connect(self._sc_save)
        btn_row.addWidget(self.sc_save_btn)
        ep.addLayout(btn_row)

        ep.addStretch(1)
        self.body_stack.addWidget(self.editor_page)

        self._editing_key: str | None = None  # None = new shortcut

        self.card.hide()
        root.addWidget(self.card, 0, Qt.AlignmentFlag.AlignHCenter)

        self.opacity_anim = QPropertyAnimation(self, b"windowOpacity")
        self.opacity_anim.setDuration(150)
        self.opacity_anim.finished.connect(self._on_fade_finished)

        self.setWindowOpacity(0.0)
        self.hide()

        self.pill.mousePressEvent = self._pill_click  # type: ignore

        self._load_ui_settings()
        self._rebuild_cards()
        self._apply_screen_watch_from_settings()

    def _apply_screen_watch_from_settings(self):
        try:
            if settings_store.get("screen_watch_enabled", False):
                import screen_watch
                interval = float(settings_store.get("screen_watch_interval", 1.0) or 1.0)
                w = screen_watch.get_watch()
                w.interval = max(0.3, interval)
                screen_watch.start_watching()
        except Exception:
            pass

    # ----- helpers -----
    def _h(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setStyleSheet(f"color: {TEXT}; font-size: 13px; font-weight: 700;")
        return lab

    def _field_qss(self) -> str:
        return f"""
            QLineEdit {{
                background: {CARD2}; color: {TEXT};
                border: 1px solid {BORDER}; border-radius: 12px;
                padding: 10px 14px; font-size: 13px;
            }}
            QLineEdit:focus {{ border: 1px solid {BLUE}; }}
        """

    def _small_btn_qss(self) -> str:
        return f"""
            QPushButton {{
                background: {CARD2}; color: {BLUE};
                border: 1px solid {BORDER}; border-radius: 10px;
                padding: 6px 10px; font-size: 11px; font-weight: 600;
            }}
            QPushButton:hover {{ border-color: {BLUE}; color: {TEXT}; }}
        """

    def _checkbox_qss(self) -> str:
        return f"""
            QCheckBox {{ color: {TEXT}; font-size: 12px; spacing: 6px; }}
            QCheckBox::indicator {{
                width: 15px; height: 15px; border-radius: 4px;
                border: 1px solid {BORDER}; background: {CARD2};
            }}
            QCheckBox::indicator:checked {{
                background: {BLUE}; border: 1px solid {BLUE};
            }}
        """

    def _combo_qss(self) -> str:
        return f"""
            QComboBox {{
                background: {CARD2}; color: {TEXT};
                border: 1px solid {BORDER}; border-radius: 12px;
                padding: 7px 12px; font-size: 12px; min-width: 180px;
            }}
            QComboBox::drop-down {{ border: none; width: 22px; }}
            QComboBox QAbstractItemView {{
                background: {CARD2}; color: {TEXT};
                selection-background-color: #2A3555;
                border: 1px solid {BORDER};
            }}
        """

    def _setting_row(self, emoji: str, label: str, widget: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        ic = QLabel(emoji)
        ic.setStyleSheet(f"color: {BLUE}; font-size: 13px;")
        row.addWidget(ic)
        lab = QLabel(label)
        lab.setStyleSheet(f"color: {MUTED}; font-size: 12px; min-width: 90px;")
        row.addWidget(lab)
        row.addWidget(widget, 1)
        return row

    def _load_ui_settings(self):
        data = settings_store.get_all()
        self.wake_edit.setText(str(data.get("wake_name") or "Lyra"))
        self.name_lbl.setText(str(data.get("wake_name") or "Lyra"))
        self._card_name_lbl.setText(str(data.get("wake_name") or "Lyra"))
        if hasattr(self, "always_top"):
            self.always_top.blockSignals(True)
            self.always_top.setChecked(bool(data.get("always_on_top", True)))
            self.always_top.blockSignals(False)
        if hasattr(self, "auto_collapse"):
            self.auto_collapse.blockSignals(True)
            self.auto_collapse.setChecked(bool(data.get("auto_collapse", True)))
            self.auto_collapse.blockSignals(False)
        if hasattr(self, "tts_combo"):
            voice = str(data.get("tts_voice") or "en-US-AriaNeural")
            idx = self.tts_combo.findText(voice)
            self.tts_combo.blockSignals(True)
            self.tts_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self.tts_combo.blockSignals(False)
        if hasattr(self, "screen_watch_cb"):
            self.screen_watch_cb.blockSignals(True)
            self.screen_watch_cb.setChecked(bool(data.get("screen_watch_enabled", False)))
            self.screen_watch_cb.blockSignals(False)
        if hasattr(self, "watch_interval_combo"):
            interval = float(data.get("screen_watch_interval", 1.0) or 1.0)
            label = {0.5: "0.5s", 1.0: "1s", 2.0: "2s", 3.0: "3s"}.get(interval, "1s")
            i = self.watch_interval_combo.findText(label)
            self.watch_interval_combo.blockSignals(True)
            self.watch_interval_combo.setCurrentIndex(i if i >= 0 else 1)
            self.watch_interval_combo.blockSignals(False)
        if hasattr(self, "confirm_click_cb"):
            self.confirm_click_cb.blockSignals(True)
            self.confirm_click_cb.setChecked(bool(data.get("confirm_before_click", True)))
            self.confirm_click_cb.blockSignals(False)

    def _on_always_top(self, state):
        on = state == Qt.CheckState.Checked.value or state is True or state == 2
        settings_store.set_many({"always_on_top": bool(on)})
        flags = self.windowFlags()
        if on:
            self.setWindowFlags(flags | Qt.WindowType.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(flags & ~Qt.WindowType.WindowStaysOnTopHint)
        self.show()

    def _on_auto_collapse(self, state):
        on = state == Qt.CheckState.Checked.value or state is True or state == 2
        settings_store.set_many({"auto_collapse": bool(on)})

    def _on_tts_voice(self, text: str):
        voice = (text or "").strip()
        if voice:
            settings_store.set_many({"tts_voice": voice})

    def _on_confirm_click(self, state):
        on = state == Qt.CheckState.Checked.value or state is True or state == 2
        settings_store.set_many({"confirm_before_click": bool(on)})

    def _on_watch_interval(self, text: str):
        try:
            sec = float(str(text).replace("s", "").strip() or "1")
        except ValueError:
            sec = 1.0
        settings_store.set_many({"screen_watch_interval": sec})
        try:
            import screen_watch
            w = screen_watch.get_watch()
            w.interval = max(0.3, sec)
        except Exception:
            pass

    def _on_screen_watch(self, state):
        on = state == Qt.CheckState.Checked.value or state is True or state == 2
        settings_store.set_many({"screen_watch_enabled": bool(on)})
        try:
            import screen_watch
            if on:
                interval = float(settings_store.get("screen_watch_interval", 1.0) or 1.0)
                w = screen_watch.get_watch()
                w.interval = max(0.3, interval)
                screen_watch.start_watching()
            else:
                screen_watch.stop_watching()
        except Exception as e:
            ui.print_chat("system", f"Screen watch error: {e}")

    def _rebuild_cards(self):
        while self.cards_grid.count():
            item = self.cards_grid.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w is not None:
                w.deleteLater()
        # Mockup-style labels + colors
        presets = [
            ("focus_mode", "❄  Chilling", "#7B9BFF"),
            ("gaming_mode", "🎮  Gaming", "#B48CFF"),
            ("work_mode", "💼  WORK", "#E0A050"),
        ]
        existing = {}
        try:
            data = json.loads(SHORTCUTS_PATH.read_text(encoding="utf-8"))
            for k, v in data.items():
                if not k.startswith("_") and isinstance(v, dict):
                    existing[k] = v
        except Exception:
            pass

        cards = []
        for key, label, color in presets:
            if key in existing:
                cards.append((key, label, color))
        for k, v in existing.items():
            if k not in {c[0] for c in cards}:
                name = v.get("name") or k
                cards.append((k, name, MUTED))

        # Show up to 6 in a 2-row grid so new ones are visible
        for i, (key, label, color) in enumerate(cards[:6]):
            btn = QPushButton(label)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip("Click to edit this shortcut")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {CARD2}; color: {color};
                    border: 1px solid {BORDER}; border-radius: 12px;
                    padding: 10px 8px; font-size: 12px; font-weight: 600;
                }}
                QPushButton:hover {{ border-color: {color}; }}
            """)
            btn.clicked.connect(lambda _=False, k=key: self._open_shortcut_editor_edit(k))
            row, col = divmod(i, 3)
            self.cards_grid.addWidget(btn, row, col)
            self.cards_grid.setColumnStretch(col, 1)

    def _save_wake_name(self):
        name = self.wake_edit.text().strip() or "Lyra"
        settings_store.set_many({"wake_name": name})
        try:
            import voice
            voice.reload_wake_pattern()
        except Exception as e:
            ui.log.warning("%s", e)
        self.name_lbl.setText(name)
        self._card_name_lbl.setText(name)
        ui.print_chat("system", f"Wake name set to “{name}”.")

    # ----- shortcut editor -----
    def _slugify(self, name: str) -> str:
        s = "".join(c if c.isalnum() or c in " _-" else "" for c in name.strip().lower())
        s = s.replace(" ", "_").strip("_")
        return s or "shortcut"

    def _load_shortcuts_dict(self) -> dict:
        try:
            data = json.loads(SHORTCUTS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {"_readme": "Lyrr user-defined shortcuts."}

    def _save_shortcuts_dict(self, data: dict) -> None:
        try:
            SHORTCUTS_PATH.parent.mkdir(parents=True, exist_ok=True)
            SHORTCUTS_PATH.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except Exception as e:
            ui.log.error("Failed to save shortcuts: %s", e)
            ui.print_chat("system", f"Could not save shortcuts: {e}")

    def _open_shortcut_editor_new(self):
        self._editing_key = None
        self.editor_title.setText("New Shortcut")
        self.sc_name_edit.clear()
        self.sc_triggers_edit.clear()
        self.sc_apps_list.clear()
        self.sc_path_edit.clear()
        self.sc_app_combo.setCurrentIndex(0)
        self.sc_delete_btn.hide()
        self.body_stack.setCurrentWidget(self.editor_page)

    def _open_shortcut_editor_edit(self, key: str):
        data = self._load_shortcuts_dict()
        sc = data.get(key)
        if not isinstance(sc, dict):
            ui.print_chat("system", f"Shortcut “{key}” not found.")
            return
        self._editing_key = key
        self.editor_title.setText(f"Edit: {sc.get('name') or key}")
        self.sc_name_edit.setText(str(sc.get("name") or key))
        triggers = sc.get("triggers") or []
        self.sc_triggers_edit.setText(", ".join(str(t) for t in triggers))
        self.sc_apps_list.clear()
        for action in sc.get("actions") or []:
            if not isinstance(action, dict):
                continue
            label = self._action_to_label(action)
            if label:
                self.sc_apps_list.addItem(label)
        self.sc_path_edit.clear()
        self.sc_app_combo.setCurrentIndex(0)
        if hasattr(self, "sc_extra_combo"):
            self.sc_extra_combo.setCurrentIndex(0)
        self.sc_delete_btn.show()
        self.body_stack.setCurrentWidget(self.editor_page)

    def _close_shortcut_editor(self):
        self.body_stack.setCurrentWidget(self.main_page)
        self._editing_key = None

    def _sc_add_known_app(self):
        idx = self.sc_app_combo.currentIndex()
        if idx <= 0:
            return
        name = self.sc_app_combo.currentText().strip()
        if not name:
            return
        # Avoid duplicates
        for i in range(self.sc_apps_list.count()):
            item = self.sc_apps_list.item(i)
            if item is not None and item.text().lower() == name.lower():
                return
        self.sc_apps_list.addItem(name)

    def _sc_browse_path(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select application",
            "",
            "Executables (*.exe *.bat *.cmd *.lnk);;All files (*.*)",
        )
        if path:
            self.sc_path_edit.setText(path)

    def _sc_add_path_app(self):
        path = self.sc_path_edit.text().strip()
        if not path:
            return
        for i in range(self.sc_apps_list.count()):
            item = self.sc_apps_list.item(i)
            if item is not None and item.text() == path:
                return
        self.sc_apps_list.addItem(path)
        self.sc_path_edit.clear()

    def _sc_remove_app(self):
        row = self.sc_apps_list.currentRow()
        if row >= 0:
            self.sc_apps_list.takeItem(row)

    def _sc_add_extra_action(self):
        idx = self.sc_extra_combo.currentIndex()
        if idx <= 0:
            return
        label = self.sc_extra_combo.currentText().strip()
        self.sc_apps_list.addItem(label)

    def _sc_move_item(self, delta: int):
        row = self.sc_apps_list.currentRow()
        if row < 0:
            return
        new_row = row + delta
        if new_row < 0 or new_row >= self.sc_apps_list.count():
            return
        item = self.sc_apps_list.takeItem(row)
        self.sc_apps_list.insertItem(new_row, item)
        self.sc_apps_list.setCurrentRow(new_row)

    def _action_to_label(self, action: dict) -> str:
        t = action.get("type")
        if t == "open_app":
            return action.get("target") or ""
        if t == "open_spotify":
            return "spotify"
        if t == "mute":
            return "mute"
        if t == "volume_up":
            return "volume up"
        if t == "volume_down":
            return "volume down"
        if t == "wait":
            sec = action.get("seconds", 1)
            return f"wait {sec}s"
        if t == "lock_screen":
            return "lock screen"
        if t == "take_screenshot":
            return "screenshot"
        if t == "set_volume":
            return f"volume {action.get('level', 50)}%"
        return t or ""

    def _label_to_action(self, label: str) -> dict | None:
        lab = (label or "").strip().lower()
        if not lab:
            return None
        if lab == "mute":
            return {"type": "mute"}
        if lab == "volume up":
            return {"type": "volume_up", "steps": 5}
        if lab == "volume down":
            return {"type": "volume_down", "steps": 5}
        if lab.startswith("wait "):
            try:
                sec = float(lab.replace("wait", "").replace("s", "").strip())
            except ValueError:
                sec = 1.0
            return {"type": "wait", "seconds": sec}
        if lab in ("lock screen", "lock"):
            return {"type": "lock_screen"}
        if lab == "screenshot":
            return {"type": "take_screenshot"}
        m = re.match(r"volume\s+(\d+)%?", lab)
        if m:
            return {"type": "set_volume", "level": int(m.group(1))}
        return {"type": "open_app", "target": label.strip()}

    def _sc_save(self):
        name = self.sc_name_edit.text().strip()
        if not name:
            ui.print_chat("system", "Give the shortcut a name.")
            return
        triggers_raw = self.sc_triggers_edit.text().strip()
        triggers = [t.strip() for t in triggers_raw.split(",") if t.strip()]
        if not triggers:
            triggers = [name.lower()]
        actions = []
        for i in range(self.sc_apps_list.count()):
            item = self.sc_apps_list.item(i)
            if item is None:
                continue
            label = item.text().strip()
            act = self._label_to_action(label)
            if act:
                actions.append(act)

        data = self._load_shortcuts_dict()
        if self._editing_key and self._editing_key in data:
            key = self._editing_key
        else:
            key = self._slugify(name)
            base = key
            n = 2
            while key in data and not str(key).startswith("_"):
                key = f"{base}_{n}"
                n += 1

        # Prefer the live ShortcutManager so voice matching updates immediately
        try:
            import shortcuts as sc_mod
            sc_mod.add_shortcut(key, name, triggers, actions)
        except Exception:
            data[key] = {
                "name": name,
                "triggers": triggers,
                "actions": actions,
            }
            self._save_shortcuts_dict(data)
        ui.print_chat("system", f"Shortcut “{name}” saved.")
        self._rebuild_cards()
        self._close_shortcut_editor()

    def _sc_delete(self):
        if not self._editing_key:
            return
        key = self._editing_key
        name = key
        data = self._load_shortcuts_dict()
        if key in data and isinstance(data[key], dict):
            name = data[key].get("name") or key
        try:
            import shortcuts as sc_mod
            sc_mod.remove_shortcut(key)
        except Exception:
            if key in data:
                del data[key]
                self._save_shortcuts_dict(data)
        ui.print_chat("system", f"Deleted shortcut “{name}”.")
        self._rebuild_cards()
        self._close_shortcut_editor()

    # ----- expand / collapse -----
    def _pill_click(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle()

    def _toggle(self):
        if self._expanded:
            self._collapse()
        else:
            self._expand()

    def _expand(self):
        self._expanded = True
        self._load_ui_settings()
        self._rebuild_cards()
        self._sync_text()
        self.body_stack.setCurrentWidget(self.main_page)
        self.stem_space.show()
        self.card.show()
        self._relayout()
        self._fade_in()

    def _collapse(self):
        self._expanded = False
        self.stem_space.hide()
        self.card.hide()
        self._relayout()

    def _sync_text(self):
        t = self._last_text or self._status_hint
        self.transcript_lbl.setText(t)
        if t:
            self.quote.setText(f'“{t}”' if not t.startswith('"') else t)
        else:
            self.quote.setText("")

    def _relayout(self):
        screen_obj = QApplication.primaryScreen() or self.screen()
        if screen_obj is None:
            return
        screen = screen_obj.availableGeometry()
        self.pill.adjustSize()
        pill_w = max(220, min(self.pill.sizeHint().width() + 8, 480))
        self.pill.setFixedWidth(pill_w)

        if self._expanded:
            w = max(self.CARD_W + 32, pill_w + 32)
            h = self.PILL_H + self.STEM_H + self.CARD_H + 28
        else:
            w = pill_w + 32
            h = self.PILL_H + 28

        w = min(w, screen.width() - 16)
        h = min(h, screen.height() - 40)
        x = screen.x() + max(0, (screen.width() - w) // 2)
        y = screen.y() + 6
        self.setFixedSize(w, h)
        self.move(x, y)
        self.update()  # repaint stem

    def paintEvent(self, event):
        """Draw soft shadows + connecting stem when expanded."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)

        # Pill shadow
        pr = self.pill.geometry()
        for s, a in ((8, 30), (4, 50)):
            p.setBrush(QColor(0, 0, 0, a))
            p.drawRoundedRect(QRectF(pr).adjusted(-s * 0.2, 1, s * 0.2, s * 0.4), 20, 20)

        if self._expanded and self.card.isVisible():
            cr = self.card.geometry()
            for s, a in ((10, 35), (5, 55)):
                p.setBrush(QColor(0, 0, 0, a))
                p.drawRoundedRect(QRectF(cr).adjusted(-s * 0.2, 1, s * 0.2, s * 0.4), 22, 22)

            # Stem path between pill bottom center and card top center
            path = QPainterPath()
            top = QPointF(pr.center().x(), pr.bottom() - 1)
            bot = QPointF(cr.center().x(), cr.top() + 1)
            neck = 14
            path.moveTo(top.x() - neck, top.y())
            path.cubicTo(
                top.x() - neck * 0.3, top.y() + self.STEM_H * 0.55,
                bot.x() - neck * 0.3, bot.y() - self.STEM_H * 0.15,
                bot.x() - 28, bot.y(),
            )
            path.lineTo(bot.x() + 28, bot.y())
            path.cubicTo(
                bot.x() + neck * 0.3, bot.y() - self.STEM_H * 0.15,
                top.x() + neck * 0.3, top.y() + self.STEM_H * 0.55,
                top.x() + neck, top.y(),
            )
            path.closeSubpath()
            # Match the pure-black pill background
            p.setBrush(QColor("#000000"))
            p.setPen(QPen(QColor(BORDER), 1))
            p.drawPath(path)

    def _fade_in(self):
        if not self.isVisible() or self.windowOpacity() < 0.95:
            self.show()
            self.raise_()
            self.opacity_anim.stop()
            self.opacity_anim.setStartValue(max(0.0, self.windowOpacity()))
            self.opacity_anim.setEndValue(1.0)
            self.opacity_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self.opacity_anim.start()

    def _fade_out(self):
        if self._expanded:
            self._collapse()
        self.opacity_anim.stop()
        self.opacity_anim.setStartValue(self.windowOpacity())
        self.opacity_anim.setEndValue(0.0)
        self.opacity_anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self.opacity_anim.start()

    def _on_fade_finished(self):
        if self.windowOpacity() < 0.01:
            self.hide()
            self.bouncy.stop()
            self.wave.set_active(False)

    # ----- public API -----
    @pyqtSlot(str)
    def display_speech(self, text: str):
        self._last_text = (text or "").strip()
        self._status_hint = ""
        self.bouncy.stop()
        self.wave.set_active(False)
        self._sync_text()
        self._relayout()
        self._fade_in()

    @pyqtSlot()
    def start_thinking(self):
        self.dot.setStyleSheet(f"color: {PURPLE}; font-size: 11px; background: transparent;")
        if not self._last_text:
            self._status_hint = "Thinking"
        self.wave.set_active(False)
        self.bouncy.start()
        self._sync_text()
        self._relayout()
        self._fade_in()

    @pyqtSlot()
    def standby(self):
        self.bouncy.stop()
        self.wave.set_active(False)
        if settings_store.get("auto_collapse", True):
            self._fade_out()
        elif self._expanded:
            self._collapse()

    def set_state(self, state: str) -> None:
        self._state = state
        if state == "standby":
            self.standby()
        elif state == "thinking":
            self.start_thinking()
        elif state == "looking":
            # Phase 2 — vision is inspecting the screen
            self.dot.setStyleSheet(f"color: {BLUE}; font-size: 11px; background: transparent;")
            self.wave.set_active(False)
            self.bouncy.start()
            self._status_hint = "Looking…"
            self._sync_text()
            self._relayout()
            self._fade_in()
        elif state == "listening":
            self.bouncy.stop()
            self.dot.setStyleSheet(f"color: {GREEN}; font-size: 11px; background: transparent;")
            self.wave.set_active(True)
            if not self._last_text:
                self._status_hint = "Listening"
            self._sync_text()
            self._relayout()
            self._fade_in()
        elif state in ("speaking", "idle"):
            self.bouncy.stop()
            self.wave.set_active(state == "speaking")
            self.dot.setStyleSheet(f"color: {GREEN}; font-size: 11px; background: transparent;")
            self._relayout()


def _make_tray_icon() -> QIcon:
    pix = QPixmap(32, 32)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(CARD))
    p.drawRoundedRect(4, 10, 24, 12, 6, 6)
    p.setBrush(QColor(GREEN))
    p.drawEllipse(13, 13, 6, 6)
    p.end()
    return QIcon(pix)


def _build_tray(app: QApplication, notch: AssistantNotch) -> QSystemTrayIcon:
    tray = QSystemTrayIcon(_make_tray_icon(), app)
    tray.setToolTip("Lyra")
    menu = QMenu()
    a = QAction("Settings…", app)
    a.triggered.connect(notch._expand)
    q = QAction("Exit Lyra", app)
    q.triggered.connect(app.quit)
    menu.addAction(a)
    menu.addSeparator()
    menu.addAction(q)
    tray.setContextMenu(menu)
    tray.show()
    return tray


def run() -> None:
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    bridge = _install_bridge()
    notch = AssistantNotch()
    _build_tray(app, notch)
    bridge.state.connect(notch.set_state)

    def _on_chat(role, text, detail):
        if role == "user":
            notch.display_speech(text)

    bridge.chat.connect(_on_chat)
    notch.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
    hwnd = int(notch.winId())
    if hwnd and not ctypes.windll.user32.RegisterHotKey(
        hwnd, HOTKEY_ID, MOD_WIN | MOD_SHIFT, VK_C
    ):
        ui.log.warning("Could not register Win+Shift+C hotkey.")
    hotkey_filter = GlobalHotkeyFilter(notch)
    app.installNativeEventFilter(hotkey_filter)

    def _cleanup_hotkey() -> None:
        # Keep a Python reference to the native event filter until shutdown.
        _ = hotkey_filter
        if hwnd:
            ctypes.windll.user32.UnregisterHotKey(hwnd, HOTKEY_ID)

    app.aboutToQuit.connect(_cleanup_hotkey)
    engine = EngineThread()
    engine.start()
    sys.exit(app.exec())


if __name__ == "__main__":
    run()
