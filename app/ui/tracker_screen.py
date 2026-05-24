import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QBoxLayout, QLabel, QPushButton,
    QGroupBox, QGridLayout, QDialog, QMessageBox, QSpacerItem, QSizePolicy,
    QFrame, QListWidget, QListWidgetItem, QScrollArea,
    QTableWidget, QTableWidgetItem, QHeaderView,
)
from PyQt6.QtCore import pyqtSignal, Qt, QTimer
from PyQt6.QtGui import QFont, QColor, QBrush


# Width thresholds for responsive tracker layout (px).
_TRK_HIDE_LOG_BELOW = 920       # below this, point sequence log is hidden
_TRK_STACK_GROUPS_BELOW = 640   # below this, button groups stack vertically
_TRK_COMPACT_BELOW = 480        # below this, even tighter spacing/fonts

from app.config import FAULT_TYPES, EXPORTS_DIR


# Human-readable labels for every event_type the engine emits.
EVENT_LABELS: dict[str, str] = {
    "serve_ace":          "Ace",
    "serve_fault":        "Fault",
    "serve_fault_type":   "Fault type",
    "serve_double_fault": "Double Fault",
    "receive":            "Receive",
    "weak_receive":       "Weak Receive",
    "set":                "Set",
    "weak_set":           "Weak Set",
    "hit":                "Hit",
    "weak_hit":           "Weak Hit",
    "touch":              "Touch",
    "weak_touch":         "Weak Touch",
    "soft_touch":         "Soft Touch",
    "weak_soft_touch":    "Weak Soft Touch",
    "point":              "Point",
    "error":              "Error",
    "lost":               "Lost",
    "overtime_triggered": "Overtime triggered",
    "game_end":           "Game ended",
}


def _flash_button(btn: QPushButton) -> None:
    """Briefly invert the button colours so the click is visibly registered."""
    if getattr(btn, "_flashing", False):
        return
    btn._flashing = True
    btn.setStyleSheet(
        "background-color: #ffffff; color: #16162a; "
        "border: 2px solid #ffffff;"
    )

    def revert():
        btn._flashing = False
        btn.setStyleSheet("")

    QTimer.singleShot(160, revert)


class FaultTypeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Fault Type")
        self.setModal(True)
        self.setMinimumWidth(380)
        self._selected: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(14)

        prompt = QLabel("What kind of fault?")
        prompt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prompt.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #ffd6a5;"
        )
        layout.addWidget(prompt)

        # Fault-type buttons in a 2-column grid, styled like the Fault button.
        grid = QGridLayout()
        grid.setSpacing(8)
        for i, ft in enumerate(FAULT_TYPES):
            btn = QPushButton(ft)
            btn.setObjectName("btn_fault")
            btn.setMinimumHeight(56)
            btn.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, t=ft: self._on_pick(t))
            row, col = divmod(i, 2)
            grid.addWidget(btn, row, col)
        layout.addLayout(grid)

        cancel_row = QHBoxLayout()
        cancel_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("btn_reset")
        cancel_btn.setMinimumHeight(36)
        cancel_btn.setMinimumWidth(110)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        cancel_row.addWidget(cancel_btn)
        cancel_row.addStretch()
        layout.addLayout(cancel_row)

    def _on_pick(self, fault_type: str):
        self._selected = fault_type
        self.accept()

    def selected_fault(self) -> str | None:
        return self._selected


def _fmt_signed(n: int) -> str:
    return f"+{n}" if n >= 0 else str(n)


def _fmt_key_plays(breakdown: list[tuple[str, int]], limit: int = 3) -> str:
    """Render the top-N biggest swings on one line, raw event units."""
    if not breakdown:
        return "—"
    parts = [f"{lbl} {_fmt_signed(delta)}" for lbl, delta in breakdown[:limit]]
    return "  ·  ".join(parts)


class _GameOverDialog(QDialog):
    """End-of-game summary: final score + per-slot RoundX with Key Plays.

    `roundx` is the dict returned by `compute_roundx_scores(game_id)`. May
    be None if the computation failed (the dialog falls back to the score
    header + CSV message).
    """

    def __init__(self, state, roundx: dict | None, csv_msg: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Game Over")
        self.setModal(True)
        self.setMinimumWidth(720)
        self.open_exports_clicked = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(14)

        # Header: winner + final score
        if state.winner_team:
            header_txt = f"🏆 Team {state.winner_team} wins!"
        else:
            header_txt = "Game ended manually."
        header = QLabel(header_txt)
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setStyleSheet(
            "font-size: 22px; font-weight: bold; color: #ffd6a5;"
        )
        layout.addWidget(header)

        score_txt = f"{state.score_a} – {state.score_b}"
        if state.end_reason:
            score_txt += f"   ·   {state.end_reason}"
        score_lbl = QLabel(score_txt)
        score_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        score_lbl.setStyleSheet("font-size: 16px; color: #a0c4ff;")
        layout.addWidget(score_lbl)

        # RoundX table
        if roundx:
            layout.addWidget(self._build_roundx_table(roundx))

        # CSV message (smaller, dimmer)
        if csv_msg:
            csv_lbl = QLabel(csv_msg)
            csv_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            csv_lbl.setWordWrap(True)
            csv_lbl.setStyleSheet("font-size: 12px; color: #8888aa;")
            layout.addWidget(csv_lbl)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        open_btn = QPushButton("📂  Open exports folder")
        open_btn.setMinimumHeight(36)
        open_btn.setMinimumWidth(180)
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.clicked.connect(self._on_open_exports)
        btn_row.addWidget(open_btn)
        cont_btn = QPushButton("Continue →")
        cont_btn.setObjectName("btn_primary")
        cont_btn.setMinimumHeight(36)
        cont_btn.setMinimumWidth(140)
        cont_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cont_btn.setDefault(True)
        cont_btn.clicked.connect(self.accept)
        btn_row.addWidget(cont_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _on_open_exports(self):
        self.open_exports_clicked = True
        self.accept()

    @staticmethod
    def _build_roundx_table(roundx: dict) -> QWidget:
        # All four slots share the same `rated` flag (it's a game-level
        # property), so we read it off A1.
        sample = next(iter(roundx.values()), {})
        rated = sample.get("rated", True)
        rallies = sample.get("counted_rallies", 0)

        wrap = QFrame()
        wrap.setStyleSheet(
            "QFrame { background-color: #16162a; border-radius: 8px; }"
        )
        v = QVBoxLayout(wrap)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)

        title_txt = "RoundX — Match Impact"
        if not rated:
            title_txt += f"   (unrated · {rallies} rallies)"
        title = QLabel(title_txt)
        title.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: #a0c4ff;"
        )
        v.addWidget(title)

        tbl = QTableWidget()
        tbl.setColumnCount(4)
        tbl.setRowCount(4)
        tbl.setHorizontalHeaderLabels([
            "Slot", "Player", "RoundX", "Key Plays",
        ])
        tbl.verticalHeader().setVisible(False)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        tbl.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        tbl.setShowGrid(False)
        tbl.setAlternatingRowColors(True)
        tbl.setStyleSheet(
            "QTableWidget { background-color: #1a1a36; gridline-color: #2a2a55; "
            "alternate-background-color: #16162a; color: #ddddee; }"
            "QTableWidget::item { padding: 6px 10px; }"
            "QHeaderView::section { background-color: #1f1f3a; color: #a0c4ff; "
            "padding: 6px 10px; border: 0; font-weight: bold; }"
        )

        for r, slot in enumerate(("A1", "A2", "B1", "B2")):
            d = roundx.get(slot, {})
            score = d.get("score", 0)
            rel = d.get("relative_to_avg", 0)
            name = d.get("name") or slot
            bd = d.get("breakdown") or []

            slot_item = QTableWidgetItem(slot)
            slot_item.setTextAlignment(
                Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
            )
            slot_item.setForeground(QColor("#aaaacc"))
            tbl.setItem(r, 0, slot_item)

            name_item = QTableWidgetItem(name)
            name_item.setTextAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            tbl.setItem(r, 1, name_item)

            score_txt = f"{_fmt_signed(score)}  ({_fmt_signed(rel)} vs avg)"
            score_item = QTableWidgetItem(score_txt)
            score_item.setTextAlignment(
                Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
            )
            # Colour the score: green if above average, red if below, neutral
            # if at average.
            if rel > 0:
                score_item.setForeground(QColor("#7adb7a"))
            elif rel < 0:
                score_item.setForeground(QColor("#ff7070"))
            else:
                score_item.setForeground(QColor("#ffd6a5"))
            f = score_item.font()
            f.setBold(True)
            score_item.setFont(f)
            tbl.setItem(r, 2, score_item)

            kp_item = QTableWidgetItem(_fmt_key_plays(bd))
            kp_item.setTextAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            kp_item.setForeground(QColor("#aaaacc"))
            tbl.setItem(r, 3, kp_item)

        hdr = tbl.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        row_h = 34
        tbl.verticalHeader().setDefaultSectionSize(row_h)
        tbl.setFixedHeight(row_h * 5 + 4)
        v.addWidget(tbl)
        return wrap


class TrackerScreen(QWidget):
    home_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._engine = None
        self._action_btns: list[QPushButton] = []
        self._layout_mode: str | None = None
        self._score_font_size = 56
        self._build_ui()

    def set_engine(self, engine):
        self._engine = engine

    # ------------------------------------------------------------------ #
    #  UI Construction                                                      #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        self._outer = QHBoxLayout(self)
        self._outer.setContentsMargins(10, 10, 10, 10)
        self._outer.setSpacing(10)

        # ── LEFT: full-height point sequence log ─────────────────────
        self._log_group = QGroupBox("Point sequence")
        self._log_group.setMinimumWidth(220)
        self._log_group.setMaximumWidth(320)
        self._log_group.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        log_layout = QVBoxLayout(self._log_group)
        log_layout.setContentsMargins(6, 8, 6, 6)
        self._point_log = QListWidget()
        self._point_log.setObjectName("point_log")
        self._point_log.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._point_log.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._point_log.setWordWrap(True)
        log_layout.addWidget(self._point_log)
        self._outer.addWidget(self._log_group)

        # ── RIGHT: main tracker content (wrapped in a scroll area so
        #     content can overflow vertically without buttons overlapping
        #     when the window is short) ────────────────────────────────
        self._right_scroll = QScrollArea()
        self._right_scroll.setWidgetResizable(True)
        self._right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._right_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        right_container = QWidget()
        self._right_scroll.setWidget(right_container)
        self._right = QVBoxLayout(right_container)
        self._right.setContentsMargins(0, 0, 0, 0)
        self._right.setSpacing(8)
        self._outer.addWidget(self._right_scroll, stretch=1)

        # Top bar
        top_bar = QHBoxLayout()
        self._btn_home = QPushButton("← Home")
        self._btn_home.setFixedWidth(90)
        self._btn_home.setMinimumHeight(34)
        self._btn_home.clicked.connect(self._confirm_home)
        top_bar.addWidget(self._btn_home)
        top_bar.addStretch()
        self._ot_badge = QLabel("OVERTIME")
        self._ot_badge.setObjectName("ot_badge")
        self._ot_badge.setVisible(False)
        top_bar.addWidget(self._ot_badge)
        self._right.addLayout(top_bar)

        # Score panel — holds five labels (team A name, team A score,
        # separator, team B score, team B name) that get re-arranged into
        # either a stacked layout (name above score, two columns) or an
        # inline layout (name | score | : | score | name) per mode.
        self._score_frame = QFrame()
        self._score_frame.setStyleSheet(
            "QFrame { background-color: #16162a; border-radius: 12px; }"
        )
        self._score_layout = QHBoxLayout(self._score_frame)
        self._score_layout.setContentsMargins(18, 10, 18, 10)

        self._team_a_name = QLabel("Team A")
        self._team_a_name.setObjectName("team_label")
        self._team_a_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._team_a_name.setWordWrap(True)

        self._team_a_score = QLabel("0")
        self._team_a_score.setObjectName("score_label")
        self._team_a_score.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._score_sep = QLabel(":")
        self._score_sep.setObjectName("score_label")
        self._score_sep.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._team_b_score = QLabel("0")
        self._team_b_score.setObjectName("score_label")
        self._team_b_score.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._team_b_name = QLabel("Team B")
        self._team_b_name.setObjectName("team_label")
        self._team_b_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._team_b_name.setWordWrap(True)

        self._score_inline: bool | None = None
        self._right.addWidget(self._score_frame)

        # Serve/Receive info
        self._serve_frame = QFrame()
        self._serve_frame.setStyleSheet(
            "QFrame { background-color: #1e1e38; border-radius: 8px; }"
        )
        serve_layout = QVBoxLayout(self._serve_frame)
        serve_layout.setContentsMargins(12, 6, 12, 6)
        serve_layout.setSpacing(2)
        self._serve_label = QLabel("Server: —  →  Receiver: —")
        self._serve_label.setObjectName("serve_label")
        self._serve_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._serve_label.setWordWrap(True)
        serve_layout.addWidget(self._serve_label)
        self._possession_label = QLabel("Possession: —")
        self._possession_label.setObjectName("possession_label")
        self._possession_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        serve_layout.addWidget(self._possession_label)
        self._right.addWidget(self._serve_frame)

        # Button area — QBoxLayout so direction flips at narrow widths.
        # Play gets double width when horizontal (it has ~2× buttons).
        self._btn_area = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._btn_area.setSpacing(8)
        self._serve_group = self._make_serve_buttons()
        self._play_group = self._make_play_buttons()
        self._other_group = self._make_other_buttons()
        self._btn_area.addWidget(self._serve_group, stretch=1)
        self._btn_area.addWidget(self._play_group, stretch=2)
        self._btn_area.addWidget(self._other_group, stretch=1)
        self._right.addLayout(self._btn_area, stretch=1)

        # Status bar
        self._status_label = QLabel("")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setStyleSheet("color: #888899; font-size: 13px;")
        self._status_label.setWordWrap(True)
        self._right.addWidget(self._status_label)

        # Apply initial responsive sizing.
        self._apply_responsive_layout(self.width())

    def _layout_score_panel(self, inline: bool) -> None:
        """Re-arrange the score panel into stacked or inline form.

        Stacked: two columns (name above score) with the separator between.
        Inline:  one row — name_a | score_a | : | score_b | name_b.
        """
        if self._score_inline == inline:
            return
        self._score_inline = inline

        # Detach all current items (widgets, spacers, nested column layouts)
        # from the parent score layout. Widgets need to be re-parented so
        # they can be re-added by the new arrangement. Spacers and the
        # discarded nested column layouts are released to Python GC.
        while self._score_layout.count():
            item = self._score_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                continue
            child = item.layout()
            if child is not None:
                while child.count():
                    sub = child.takeAt(0)
                    sw = sub.widget()
                    if sw is not None:
                        sw.setParent(None)

        if inline:
            # name | score | : | score | name — clustered in the centre.
            self._score_layout.addStretch(1)
            self._score_layout.addWidget(self._team_a_name)
            self._score_layout.addSpacing(8)
            self._score_layout.addWidget(self._team_a_score)
            self._score_layout.addSpacing(6)
            self._score_layout.addWidget(self._score_sep)
            self._score_layout.addSpacing(6)
            self._score_layout.addWidget(self._team_b_score)
            self._score_layout.addSpacing(8)
            self._score_layout.addWidget(self._team_b_name)
            self._score_layout.addStretch(1)
        else:
            col_a = QVBoxLayout()
            col_a.setSpacing(2)
            col_a.setAlignment(Qt.AlignmentFlag.AlignCenter)
            col_a.addWidget(self._team_a_name)
            col_a.addWidget(self._team_a_score)
            self._score_layout.addLayout(col_a, stretch=1)
            self._score_layout.addWidget(self._score_sep, stretch=0)
            col_b = QVBoxLayout()
            col_b.setSpacing(2)
            col_b.setAlignment(Qt.AlignmentFlag.AlignCenter)
            col_b.addWidget(self._team_b_name)
            col_b.addWidget(self._team_b_score)
            self._score_layout.addLayout(col_b, stretch=1)

        # Force the score frame's size hints to refresh.
        self._score_layout.invalidate()

    def _make_serve_buttons(self) -> QGroupBox:
        group = QGroupBox("Serve")
        grid = QGridLayout()
        grid.setSpacing(6)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        group.setLayout(grid)

        self._btn_ace = self._action_btn("Ace", "btn_ace", lambda: self._engine.on_serve_action("ace"))
        self._btn_fault = self._action_btn("Fault", "btn_fault", lambda: self._engine.on_serve_action("fault"))
        self._btn_receive = self._action_btn("Receive", "btn_receive", lambda: self._engine.on_serve_action("receive"))
        self._btn_weak_receive = self._action_btn("Weak Receive", "btn_receive", lambda: self._engine.on_serve_action("weak_receive"))

        grid.addWidget(self._btn_ace, 0, 0)
        grid.addWidget(self._btn_fault, 0, 1)
        grid.addWidget(self._btn_receive, 1, 0)
        grid.addWidget(self._btn_weak_receive, 1, 1)
        grid.setRowStretch(2, 1)

        return group

    def _make_play_buttons(self) -> QGroupBox:
        group = QGroupBox("Play")
        grid = QGridLayout()
        grid.setSpacing(6)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        group.setLayout(grid)

        # Simple actions: one button each, engine picks the slot.
        simple_actions = [
            ("Set",             "set",            "btn_receive"),
            ("Weak Set",        "weak_set",       "btn_receive"),
            ("Hit",             "hit",            "btn_hit"),
            ("Weak Hit",        "weak_hit",       "btn_hit"),
        ]
        self._play_btns: dict[str, QPushButton] = {}
        for i, (label, action, style) in enumerate(simple_actions):
            btn = self._action_btn(
                label, style,
                lambda a=action: self._engine.on_play_action(a),
            )
            row, col = divmod(i, 2)
            grid.addWidget(btn, row, col)
            self._play_btns[action] = btn

        # Touch / Weak Touch — split per opposing-team player. Labels are
        # filled in dynamically by refresh() based on state.possession_team.
        # Each touch button is tied to a slot *position* (1 or 2) within the
        # currently-in-possession team.
        self._touch_btns: dict[tuple[str, int], QPushButton] = {}
        for col, position in enumerate((1, 2)):
            btn = self._action_btn(
                "Touch", "btn_receive",
                lambda p=position: self._on_touch_click("touch", p),
            )
            grid.addWidget(btn, 2, col)
            self._touch_btns[("touch", position)] = btn

            wbtn = self._action_btn(
                "Weak Touch", "btn_receive",
                lambda p=position: self._on_touch_click("weak_touch", p),
            )
            grid.addWidget(wbtn, 3, col)
            self._touch_btns[("weak_touch", position)] = wbtn

        # Soft touch variants — same player as last touch, engine picks slot.
        soft_actions = [
            ("Soft Touch",      "soft_touch",     "btn_receive"),
            ("Weak Soft Touch", "weak_soft_touch","btn_receive"),
        ]
        for col, (label, action, style) in enumerate(soft_actions):
            btn = self._action_btn(
                label, style,
                lambda a=action: self._engine.on_play_action(a),
            )
            grid.addWidget(btn, 4, col)
            self._play_btns[action] = btn

        grid.setRowStretch(5, 1)
        return group

    def _on_touch_click(self, kind: str, position: int) -> None:
        """Dispatch a touch click with the chosen slot from possession team."""
        if not self._engine or not self._engine.state:
            return
        team = self._engine.state.possession_team
        if not team:
            return
        slot = f"{team}{position}"
        self._engine.on_play_action(kind, slot)

    def _make_other_buttons(self) -> QGroupBox:
        group = QGroupBox("Other")
        grid = QGridLayout()
        grid.setSpacing(6)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        group.setLayout(grid)

        self._btn_point = self._action_btn("Point", "btn_point",
                                           lambda: self._engine.on_other_action("point"))
        self._btn_error = self._action_btn("Error", "btn_error",
                                           lambda: self._engine.on_other_action("error"))
        self._btn_lost = self._action_btn("Lost", "btn_error",
                                          lambda: self._engine.on_other_action("lost"))
        self._btn_overtime = self._action_btn("Overtime", "btn_fault",
                                              lambda: self._engine.on_other_action("overtime"))
        self._btn_game_end = self._action_btn("Game End", "btn_game_end",
                                              lambda: self._engine.on_other_action("game_end"))
        self._btn_undo = self._action_btn("↩ Undo", "btn_undo",
                                          lambda: self._engine.undo())
        self._btn_reset = self._action_btn("↺ Reset Point", "btn_reset",
                                           lambda: self._engine.reset_point())
        self._btn_delete_last = self._action_btn(
            "✖ Delete Last Point", "btn_reset",
            self._confirm_delete_last_point,
        )

        grid.addWidget(self._btn_point, 0, 0)
        grid.addWidget(self._btn_error, 0, 1)
        grid.addWidget(self._btn_lost, 1, 0, 1, 2)
        grid.addWidget(self._btn_overtime, 2, 0)
        grid.addWidget(self._btn_game_end, 2, 1)
        grid.addWidget(self._btn_undo, 3, 0)
        grid.addWidget(self._btn_reset, 3, 1)
        grid.addWidget(self._btn_delete_last, 4, 0, 1, 2)
        grid.setRowStretch(5, 1)

        return group

    def _confirm_delete_last_point(self):
        if not self._engine or not self._engine.state:
            return
        reply = QMessageBox.question(
            self,
            "Delete last point?",
            "This will erase the most recently awarded point and any actions "
            "logged in the point currently in progress. The deleted point will "
            "be reopened so you can re-track it.\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._engine.delete_last_point()

    @staticmethod
    def _format_event(ev: dict, players: dict) -> str:
        # Accepts both DB row shape (`event_type`) and the engine's in-memory
        # `_point_events` shape (`type`).
        event_type = ev.get("event_type") or ev.get("type")
        slot = ev.get("player")
        fault = ev.get("fault_type")

        action = EVENT_LABELS.get(event_type, event_type)
        if slot:
            name = players.get(slot, slot)
            who = f"{name} ({slot})"
        else:
            who = None

        if event_type == "serve_fault_type" and fault:
            return f"   ↳ {fault}"
        if who:
            return f"{who}  —  {action}"
        return action

    def _render_point_log(self, s, players: dict) -> None:
        """Rebuild the point sequence log from all DB events for this game.

        Most-recent action on top. A score divider is inserted between each
        pair of completed points (showing the score at the end of that point).
        """
        from app.db import events_repo

        self._point_log.clear()
        all_events = events_repo.list_for_game(s.game_id)
        if not all_events:
            return

        # Group events by point, preserving order.
        groups: list[tuple[int, list[dict]]] = []
        for ev in all_events:
            if not groups or groups[-1][0] != ev["point"]:
                groups.append((ev["point"], []))
            groups[-1][1].append(ev)

        # Build chronological lines: each event, then a score divider after
        # each completed point. The score AFTER a completed point equals the
        # `score_a`/`score_b` columns of the first event in the next point
        # group; for the most recent completed point with no following events
        # yet (i.e. the new point hasn't recorded anything), fall back to the
        # live state score.
        lines: list[tuple[str, str]] = []  # ("event"|"score"|"latest_event", text)
        latest_marked = False
        for i, (pt_num, evs) in enumerate(groups):
            for ev in evs:
                lines.append(("event", self._format_event(ev, players)))
            completed = pt_num < s.point_number
            if completed:
                if i + 1 < len(groups):
                    nxt = groups[i + 1][1][0]
                    sa, sb = nxt["score_a"], nxt["score_b"]
                else:
                    sa, sb = s.score_a, s.score_b
                lines.append(("score", f"── {sa} : {sb}  (Point {pt_num} ended) ──"))

        # Reverse so the newest entry is on top.
        for kind, text in reversed(lines):
            item = QListWidgetItem(text)
            if kind == "score":
                item.setForeground(QBrush(QColor("#90ee90")))
                f = item.font()
                f.setBold(True)
                item.setFont(f)
            elif not latest_marked:
                # First event in reversed order is the latest action.
                item.setForeground(QBrush(QColor("#ffd6a5")))
                f = item.font()
                f.setBold(True)
                item.setFont(f)
                latest_marked = True
            self._point_log.addItem(item)
        self._point_log.scrollToTop()

    def _action_btn(self, label: str, obj_name: str, handler) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName(obj_name)
        btn.setFixedHeight(46)
        btn.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        btn.setCursor(Qt.CursorShape.PointingHandCursor)

        # clicked emits a bool; absorb it and ignore so we can call handler
        # with no args, then trigger a brief flash so the click is visible.
        def on_click(*_args):
            _flash_button(btn)
            handler()

        btn.clicked.connect(on_click)
        self._action_btns.append(btn)
        return btn

    # ------------------------------------------------------------------ #
    #  Responsive layout                                                    #
    # ------------------------------------------------------------------ #

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_responsive_layout(event.size().width())

    def _apply_responsive_layout(self, width: int) -> None:
        if width < _TRK_COMPACT_BELOW:
            mode = "compact"
        elif width < _TRK_STACK_GROUPS_BELOW:
            mode = "narrow"
        elif width < _TRK_HIDE_LOG_BELOW:
            mode = "medium"
        else:
            mode = "wide"
        if mode == self._layout_mode:
            return
        self._layout_mode = mode

        if mode == "wide":
            self._log_group.setVisible(True)
            self._set_btn_area_direction(QBoxLayout.Direction.LeftToRight)
            self._score_font_size = 56
            btn_h = 46
            outer_margin, outer_spacing = 10, 10
            right_spacing = 8
            score_margins = (18, 10, 18, 10)
            btn_area_spacing = 8
        elif mode == "medium":
            self._log_group.setVisible(False)
            self._set_btn_area_direction(QBoxLayout.Direction.LeftToRight)
            self._score_font_size = 48
            btn_h = 42
            outer_margin, outer_spacing = 8, 8
            right_spacing = 6
            score_margins = (14, 8, 14, 8)
            btn_area_spacing = 6
        elif mode == "narrow":
            self._log_group.setVisible(False)
            self._set_btn_area_direction(QBoxLayout.Direction.TopToBottom)
            self._score_font_size = 40
            btn_h = 40
            outer_margin, outer_spacing = 6, 6
            right_spacing = 5
            score_margins = (10, 6, 10, 6)
            btn_area_spacing = 6
        else:  # compact
            self._log_group.setVisible(False)
            self._set_btn_area_direction(QBoxLayout.Direction.TopToBottom)
            self._score_font_size = 32
            btn_h = 36
            outer_margin, outer_spacing = 4, 4
            right_spacing = 4
            score_margins = (6, 4, 6, 4)
            btn_area_spacing = 4

        self._outer.setContentsMargins(
            outer_margin, outer_margin, outer_margin, outer_margin
        )
        self._outer.setSpacing(outer_spacing)
        self._right.setSpacing(right_spacing)
        self._score_layout.setContentsMargins(*score_margins)
        self._btn_area.setSpacing(btn_area_spacing)

        for btn in self._action_btns:
            btn.setFixedHeight(btn_h)

        # Score separator and (when no engine state) the team score labels —
        # refresh() re-applies team score sizing/colour when engine is live.
        neutral_css = (
            f"font-size:{self._score_font_size}px;"
            f"font-weight:bold;color:#ffffff;"
        )
        self._score_sep.setStyleSheet(neutral_css)
        # Tune the team-name label size to the score font size so the
        # composite "P1 / P2" header doesn't dwarf or get dwarfed by the
        # score digits at different breakpoints.
        if self._score_font_size >= 56:
            name_size = 18
        elif self._score_font_size >= 48:
            name_size = 16
        elif self._score_font_size >= 40:
            name_size = 14
        else:
            name_size = 13
        name_css = (
            f"color:#a0c4ff;font-size:{name_size}px;font-weight:bold;"
        )
        self._team_a_name.setStyleSheet(name_css)
        self._team_b_name.setStyleSheet(name_css)

        # Inline horizontal score layout below the stack-groups breakpoint
        # — "<name>  <score>  :  <score>  <name>" on a single row.
        self._layout_score_panel(inline=mode in ("narrow", "compact"))

        if not (self._engine and self._engine.state):
            self._team_a_score.setStyleSheet(neutral_css)
            self._team_b_score.setStyleSheet(neutral_css)
        else:
            self.refresh()

    def _set_btn_area_direction(self, direction: "QBoxLayout.Direction") -> None:
        if self._btn_area.direction() == direction:
            return
        self._btn_area.setDirection(direction)
        # Re-tune stretch factors per orientation.
        if direction == QBoxLayout.Direction.LeftToRight:
            # Play gets 2× width (~2× as many buttons).
            self._btn_area.setStretchFactor(self._serve_group, 1)
            self._btn_area.setStretchFactor(self._play_group, 2)
            self._btn_area.setStretchFactor(self._other_group, 1)
        else:
            # Stacked vertically: split height proportionally to button rows.
            self._btn_area.setStretchFactor(self._serve_group, 2)
            self._btn_area.setStretchFactor(self._play_group, 5)
            self._btn_area.setStretchFactor(self._other_group, 4)
        # Direction changes don't auto-invalidate the cached minimum size
        # hint, so the window can stay stuck at the horizontal-mode minimum
        # even after switching to vertical. Force a recalculation.
        self._btn_area.invalidate()
        self._right.invalidate()
        self._outer.invalidate()
        self.updateGeometry()

    # ------------------------------------------------------------------ #
    #  State refresh                                                        #
    # ------------------------------------------------------------------ #

    def refresh(self):
        if not self._engine or not self._engine.state:
            return
        s = self._engine.state
        players = s.players

        # Score & team display — team header is just "P1 / P2".
        self._team_a_score.setText(str(s.score_a))
        self._team_b_score.setText(str(s.score_b))
        self._team_a_name.setText(
            f"{players.get('A1', 'A1')} / {players.get('A2', 'A2')}"
        )
        self._team_b_name.setText(
            f"{players.get('B1', 'B1')} / {players.get('B2', 'B2')}"
        )

        # Score colour highlight (winning team). Font size is mode-dependent
        # (see _apply_responsive_layout); colour reflects the leading team.
        sz = self._score_font_size
        win_css = f"font-size:{sz}px;font-weight:bold;color:#90ee90;"
        neutral_css = f"font-size:{sz}px;font-weight:bold;color:#ffffff;"
        if s.score_a > s.score_b:
            self._team_a_score.setStyleSheet(win_css)
            self._team_b_score.setStyleSheet(neutral_css)
        elif s.score_b > s.score_a:
            self._team_a_score.setStyleSheet(neutral_css)
            self._team_b_score.setStyleSheet(win_css)
        else:
            self._team_a_score.setStyleSheet(neutral_css)
            self._team_b_score.setStyleSheet(neutral_css)

        # Serve/receive info
        srv_name = players.get(s.current_server, s.current_server)
        rcv_name = players.get(s.current_receiver, s.current_receiver)
        fault_txt = ""
        if s.fault_count == 1:
            fault_txt = "  ⚠ 1 fault"
        self._serve_label.setText(
            f"Server: {srv_name} ({s.current_server})  →  "
            f"Receiver: {rcv_name} ({s.current_receiver}){fault_txt}"
        )

        # Possession
        if s.possession_team:
            team_label = f"Team {s.possession_team}"
            clr = "#a0c4ff" if s.possession_team == "A" else "#ffd6a5"
            self._possession_label.setText(f"Ball: {team_label}")
            self._possession_label.setStyleSheet(
                f"font-size:15px;font-weight:bold;padding:6px 16px;"
                f"border-radius:12px;background-color:#2d2d4e;color:{clr};"
            )
        else:
            self._possession_label.setText("Ball: —")

        # OT badge
        self._ot_badge.setVisible(s.in_overtime)

        # Enable/disable buttons
        avail = self._engine.available_actions()

        self._btn_ace.setEnabled(avail.get("ace", False))
        self._btn_fault.setEnabled(avail.get("fault", False))
        self._btn_receive.setEnabled(avail.get("receive", False))
        self._btn_weak_receive.setEnabled(avail.get("weak_receive", False))

        for action, btn in self._play_btns.items():
            btn.setEnabled(avail.get(action, False))

        # Touch / Weak Touch — dynamic labels for both opposing-team players.
        touch_team = s.possession_team
        for (kind, position), btn in self._touch_btns.items():
            kind_label = "Touch" if kind == "touch" else "Weak Touch"
            if touch_team:
                slot = f"{touch_team}{position}"
                name = players.get(slot, slot)
                btn.setText(f"{kind_label}\n{name}")
                btn.setEnabled(avail.get(kind, False))
            else:
                btn.setText(kind_label)
                btn.setEnabled(False)

        self._btn_point.setEnabled(avail.get("point", False))
        self._btn_error.setEnabled(avail.get("error", False))
        self._btn_lost.setEnabled(avail.get("lost", False))
        self._btn_overtime.setEnabled(avail.get("overtime", False))
        self._btn_game_end.setEnabled(avail.get("game_end", False))
        self._btn_undo.setEnabled(avail.get("undo", False))
        self._btn_reset.setEnabled(avail.get("reset_point", False))
        self._btn_delete_last.setEnabled(avail.get("delete_last_point", False))

        # Point sequence log — full game history, newest on top, with a
        # score divider between completed points. Fetched from DB each
        # refresh; the engine's per-point in-memory list is used only for
        # undo/reset bookkeeping.
        self._render_point_log(s, players)

        # Status bar: who gets credit for next action
        if s.segment == "play" and s.possession_team:
            tracker = self._engine._tracker
            last = tracker.last_for_team(s.possession_team)
            if last:
                from app.core.rotation import partner_slot
                next_slot = partner_slot(last)
                next_name = players.get(next_slot, next_slot)
                self._status_label.setText(
                    f"Next action credited to: {next_name} ({next_slot})"
                )
            else:
                self._status_label.setText("")
        elif s.segment == "serve":
            self._status_label.setText(
                f"Point {s.point_number}  —  Awaiting serve"
            )
        else:
            self._status_label.setText("")

    # ------------------------------------------------------------------ #
    #  Callbacks from GameEngine                                            #
    # ------------------------------------------------------------------ #

    def show_fault_dialog(self, callback):
        """Show fault type picker and call callback(fault_type).

        Passes `None` to the callback when the user cancels, so the engine
        can roll back the pending fault.
        """
        dlg = FaultTypeDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            callback(dlg.selected_fault())
        else:
            callback(None)

    def on_overtime_started(self):
        msg = QMessageBox(self)
        msg.setWindowTitle("Overtime!")
        msg.setText("Overtime has been triggered.\nServes now alternate every point.")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.exec()

    def on_game_ended(self, state):
        from app.core.csv_export import export_game
        from app.core.analysis import compute_roundx_scores

        try:
            csv_path = export_game(state.game_id)
            csv_msg = f"CSV exported to:\n{csv_path}"
        except Exception as e:
            csv_path = None
            csv_msg = f"(CSV export failed: {e})"

        try:
            roundx = compute_roundx_scores(state.game_id)
        except Exception as e:
            roundx = None
            csv_msg += f"\n(RoundX failed: {e})"

        dlg = _GameOverDialog(state, roundx, csv_msg, self)
        dlg.exec()

        if dlg.open_exports_clicked:
            try:
                os.startfile(str(EXPORTS_DIR))
            except Exception:
                pass

        self.home_requested.emit()

    # ------------------------------------------------------------------ #
    #  Navigation guard                                                     #
    # ------------------------------------------------------------------ #

    def _confirm_home(self):
        if not self._engine or not self._engine.state or self._engine.state.ended:
            self.home_requested.emit()
            return
        msg = QMessageBox(self)
        msg.setWindowTitle("Leave game?")
        msg.setText("The current game will be abandoned.\nAre you sure you want to go home?")
        msg.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        msg.setDefaultButton(QMessageBox.StandardButton.No)
        if msg.exec() == QMessageBox.StandardButton.Yes:
            self.home_requested.emit()
