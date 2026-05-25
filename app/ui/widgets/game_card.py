"""Reusable history-style "title card" for a finished game.

Used by `HistoryScreen` (full feature set — edit/export/delete buttons) and by
`AnalysisScreen` (clickable picker — no action buttons). The card itself is
identical between the two; callers wire up whichever interactions they need.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from app.ui.widgets.game_flow import GameFlowStrip


CARD_MIN_WIDTH = 280
CARD_MAX_WIDTH = 380
CARD_H_GAP = 12
CARD_V_GAP = 10

WINNER_COLOR = "#7adb7a"
LOSER_COLOR = "#8888aa"
NEUTRAL_COLOR = "#e0e0e0"
DIM_COLOR = "#8888aa"
POS_COLOR = "#7adb7a"
NEG_COLOR = "#ff7070"
ZERO_COLOR = "#ffd6a5"


def fmt_date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso


def format_tournament_meta(game: dict) -> str | None:
    """Compact tournament-info line. Returns None when no metadata is present."""
    tournament = (game.get("tournament") or "").strip()
    stage = (game.get("tournament_stage") or "").strip()
    part = (game.get("stage_part") or "").strip()
    bracket = (game.get("bracket_type") or "").strip()
    fmt = (game.get("match_format") or "").strip()
    division = (game.get("division") or "").strip()
    tier = (game.get("division_tier") or "").strip()
    team_count = game.get("team_count")

    if not (tournament or stage or part or bracket or fmt
            or division or tier or team_count):
        return None

    bits: list[str] = []
    if tournament:
        bits.append(f"<b style='color:#a0c4ff;'>{tournament}</b>")
    div_bits = " ".join(b for b in (division, tier) if b)
    if div_bits:
        suffix = f" · {team_count} teams" if team_count else ""
        bits.append(f"<span style='color:#b7e4c7;'>{div_bits}{suffix}</span>")
    elif team_count:
        bits.append(f"<span style='color:#b7e4c7;'>{team_count} teams</span>")
    stage_bits: list[str] = []
    if stage == "Groups" and part:
        stage_bits.append(f"Group {part}")
    elif stage == "Playoffs":
        sub = []
        if bracket:
            sub.append(bracket)
        if part:
            sub.append(part)
        stage_bits.append(" · ".join(sub) if sub else "Playoffs")
    elif stage:
        stage_bits.append(stage)
    if stage_bits:
        bits.append(f"<span style='color:#ffd6a5;'>{' · '.join(stage_bits)}</span>")
    if fmt:
        bits.append(f"<span style='color:#c8c8e0;'>{fmt}</span>")
    return f" <span style='color:{DIM_COLOR};'>·</span> ".join(bits)


def _duration(game: dict) -> str:
    if not (game.get("created_at") and game.get("ended_at")):
        return ""
    try:
        start = datetime.fromisoformat(game["created_at"])
        end = datetime.fromisoformat(game["ended_at"])
        secs = int((end - start).total_seconds())
        return f"{secs // 60}m {secs % 60}s"
    except Exception:
        return ""


def _roundx_row(game: dict, roundx: dict | None) -> QWidget | None:
    """RoundX summary line. Returns None when no RoundX data is available."""
    if not roundx:
        return None

    names = {
        "A1": game["a1_name"], "A2": game["a2_name"],
        "B1": game["b1_name"], "B2": game["b2_name"],
    }

    def cell(slot: str) -> str:
        d = roundx.get(slot) or {}
        score = d.get("score", 0)
        sign = "+" if score >= 0 else ""
        colour = POS_COLOR if score > 0 else (NEG_COLOR if score < 0 else ZERO_COLOR)
        return (
            f"<span style='color:#c8c8e0;'>{names[slot]}:</span> "
            f"<span style='color:{colour}; font-weight:bold;'>{sign}{score}</span>"
        )

    sep = f"<span style='color:{DIM_COLOR};'> · </span>"
    line = (
        cell("A1") + sep + cell("A2")
        + f"<span style='color:{DIM_COLOR};'>  |  </span>"
        + cell("B1") + sep + cell("B2")
    )

    sample = next(iter(roundx.values()), {})
    if not sample.get("rated", True):
        line += (
            f" <span style='color:{DIM_COLOR}; font-size:9px;'>"
            f"(unrated · {sample.get('counted_rallies', 0)})</span>"
        )

    lbl = QLabel(line)
    lbl.setTextFormat(Qt.TextFormat.RichText)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet("font-size: 11px;")
    return lbl


class GameCard(QFrame):
    """History-style title card. Optionally clickable; optionally hosts the
    edit/export/delete actions in the top-right corner."""

    clicked = pyqtSignal(int)

    def __init__(
        self,
        game: dict,
        roundx: dict | None = None,
        flow: list[dict] | None = None,
        *,
        clickable: bool = False,
        on_edit: Callable[[int], None] | None = None,
        on_export: Callable[[int], None] | None = None,
        on_delete: Callable[[int], None] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._game_id: int = game["id"]
        self._clickable = clickable
        # Use an objectName-scoped selector so the background only applies to
        # this card and not to any QFrame descendants it might contain.
        self.setObjectName("game_card")
        if clickable:
            self.setStyleSheet(
                "QFrame#game_card { background-color: #16162a; border-radius: 8px; }"
                "QFrame#game_card:hover { background-color: #1c1c3a; }"
            )
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setStyleSheet(
                "QFrame#game_card { background-color: #16162a; border-radius: 8px; }"
            )
        self.setMaximumWidth(CARD_MAX_WIDTH)
        self._build(game, roundx, flow, on_edit, on_export, on_delete)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 — Qt naming
        if self._clickable and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._game_id)
        super().mousePressEvent(event)

    def _build(
        self,
        game: dict,
        roundx: dict | None,
        flow: list[dict] | None,
        on_edit: Callable[[int], None] | None,
        on_export: Callable[[int], None] | None,
        on_delete: Callable[[int], None] | None,
    ) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 8, 12, 10)
        v.setSpacing(4)

        winner = game.get("winner_team")
        a_won = winner == "A"
        b_won = winner == "B"

        # Row 1 — metadata + optional action buttons
        top = QHBoxLayout()
        top.setSpacing(4)
        meta_bits = [f"#{game['id']}", fmt_date(game["created_at"])]
        dur = _duration(game)
        if dur:
            meta_bits.append(dur)
        if game.get("date_updated"):
            meta_bits.append(f"edited {fmt_date(game['date_updated'])}")
        meta = QLabel(" · ".join(meta_bits))
        meta.setStyleSheet(f"color: {DIM_COLOR}; font-size: 10px;")
        top.addWidget(meta)
        top.addStretch(1)

        gid = game["id"]
        if on_edit is not None:
            btn = QPushButton("✏")
            btn.setToolTip("Edit metadata")
            btn.setFixedSize(26, 22)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, g=gid: on_edit(g))
            top.addWidget(btn)
        if on_export is not None:
            btn = QPushButton("📥")
            btn.setToolTip("Export CSV")
            btn.setFixedSize(26, 22)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, g=gid: on_export(g))
            top.addWidget(btn)
        if on_delete is not None:
            btn = QPushButton("🗑")
            btn.setObjectName("btn_reset")
            btn.setToolTip("Delete game")
            btn.setFixedSize(26, 22)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, g=gid: on_delete(g))
            top.addWidget(btn)
        v.addLayout(top)

        # Row 1b — tournament metadata (only shown when present)
        meta_line = format_tournament_meta(game)
        if meta_line:
            meta_lbl = QLabel(meta_line)
            meta_lbl.setTextFormat(Qt.TextFormat.RichText)
            meta_lbl.setStyleSheet("font-size: 11px;")
            meta_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            meta_lbl.setWordWrap(True)
            v.addWidget(meta_lbl)

        # Row 2 — team matchup
        team_a_text = f"{game['a1_name']} / {game['a2_name']}"
        team_b_text = f"{game['b1_name']} / {game['b2_name']}"
        a_color = WINNER_COLOR if a_won else (LOSER_COLOR if b_won else NEUTRAL_COLOR)
        b_color = WINNER_COLOR if b_won else (LOSER_COLOR if a_won else NEUTRAL_COLOR)
        a_weight = "bold" if a_won else "normal"
        b_weight = "bold" if b_won else "normal"

        matchup = QLabel(
            f"<span style='color:{a_color}; font-weight:{a_weight};'>{team_a_text}</span>"
            f"  <span style='color:{DIM_COLOR};'>vs</span>  "
            f"<span style='color:{b_color}; font-weight:{b_weight};'>{team_b_text}</span>"
        )
        matchup.setTextFormat(Qt.TextFormat.RichText)
        matchup.setStyleSheet("font-size: 13px;")
        matchup.setAlignment(Qt.AlignmentFlag.AlignCenter)
        matchup.setWordWrap(True)
        v.addWidget(matchup)

        # Row 3 — score
        sa, sb = game["final_score_a"], game["final_score_b"]
        sa_color = WINNER_COLOR if a_won else (LOSER_COLOR if b_won else NEUTRAL_COLOR)
        sb_color = WINNER_COLOR if b_won else (LOSER_COLOR if a_won else NEUTRAL_COLOR)
        score = QLabel(
            f"<span style='color:{sa_color};'>{sa}</span>"
            f"<span style='color:{DIM_COLOR};'> : </span>"
            f"<span style='color:{sb_color};'>{sb}</span>"
        )
        score.setTextFormat(Qt.TextFormat.RichText)
        score.setStyleSheet("font-size: 22px; font-weight: bold;")
        score.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(score)

        # Row 4 — per-point running-score strip with break cells highlighted
        if flow:
            strip = GameFlowStrip(flow)
            strip.setStyleSheet("background-color: #12122a; border-radius: 4px;")
            v.addWidget(strip)

        # Row 5 — RoundX line
        roundx_widget = _roundx_row(game, roundx)
        if roundx_widget is not None:
            v.addWidget(roundx_widget)


class GameCardGrid(QScrollArea):
    """Scrollable, resize-aware grid of `GameCard`s. Card cells are placed
    into as many columns as fit at `CARD_MIN_WIDTH`."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._cards: list[QFrame] = []

        host = QWidget()
        outer = QVBoxLayout(host)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(CARD_H_GAP)
        self._grid.setVerticalSpacing(CARD_V_GAP)
        outer.addLayout(self._grid)
        outer.addStretch(1)
        self.setWidget(host)

    def set_cards(self, cards: list[QFrame]) -> None:
        """Replace the current set of cards. Caller owns the card widgets."""
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._cards = list(cards)
        self._regrid()

    def show_empty(self, message: str) -> None:
        """Replace cards with a centered empty-state label."""
        for c in self._cards:
            c.deleteLater()
        self._cards.clear()
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        empty = QLabel(message)
        empty.setObjectName("subtitle_label")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._grid.addWidget(empty, 0, 0)

    def _regrid(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

        viewport_w = max(self.viewport().width(), CARD_MIN_WIDTH)
        cols = max(1, (viewport_w + CARD_H_GAP) // (CARD_MIN_WIDTH + CARD_H_GAP))
        cols = int(cols)

        for i, card in enumerate(self._cards):
            r, c = divmod(i, cols)
            self._grid.addWidget(card, r, c)
        for c in range(cols):
            self._grid.setColumnStretch(c, 1)

    def resizeEvent(self, ev):  # noqa: N802 — Qt naming
        super().resizeEvent(ev)
        if self._cards:
            self._regrid()
