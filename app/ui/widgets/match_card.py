"""Tournament-match title card. Shown in the Analysis picker when two or
more games share the same (tournament, stage, stage_part) metadata.

Visually distinguished from a single-game card by a gold edge — the same
accent the Game Flow chart uses for break-point markers, so the cue is
"this is a series, not a single game."
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.core.match_grouping import match_series_score
from app.ui.widgets.game_card import (
    CARD_MAX_WIDTH, DIM_COLOR, LOSER_COLOR, NEUTRAL_COLOR, WINNER_COLOR,
    fmt_date, format_tournament_meta, _roundx_row,
)


MATCH_EDGE_COLOR = "#ffd700"  # gold — same accent as the break-point marker
MATCH_EDGE_HOVER = "#ffe44d"


class MatchCard(QFrame):
    """Tournament-match card. Always clickable; emits the list of game ids
    so the picker can load the match-level analysis on click."""

    clicked = pyqtSignal(list)

    def __init__(
        self,
        match_entry: dict,
        roundx: dict | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._games: list[dict] = match_entry["games"]
        self._game_ids: list[int] = [g["id"] for g in self._games]

        self.setObjectName("match_card")
        self.setStyleSheet(
            "QFrame#match_card { "
            f"background-color: #16162a; border-radius: 8px; "
            f"border: 1.5px solid {MATCH_EDGE_COLOR}; "
            "}"
            "QFrame#match_card:hover { "
            f"background-color: #1c1c3a; border-color: {MATCH_EDGE_HOVER}; "
            "}"
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMaximumWidth(CARD_MAX_WIDTH)
        self._build(match_entry, roundx)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 — Qt naming
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(list(self._game_ids))
        super().mousePressEvent(event)

    def _build(self, match_entry: dict, roundx: dict | None) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 8, 12, 10)
        v.setSpacing(4)

        games = self._games
        # Series score — canonical team identity (anchored on the first
        # game's roster) so a pairing that swaps sides between sets is
        # still counted as the same team.
        a_wins, b_wins = match_series_score(games)
        a_won_match = a_wins > b_wins
        b_won_match = b_wins > a_wins

        # Row 1 — id list + date + game count
        ids_text = ", ".join(f"#{g['id']}" for g in games)
        if len(ids_text) > 28:
            ids_text = f"{len(games)} games"
        date_text = fmt_date(games[0].get("created_at"))
        meta = QLabel(
            f"<span style='color:{MATCH_EDGE_COLOR}; font-weight:bold;'>"
            f"MATCH</span>"
            f" <span style='color:{DIM_COLOR};'>·</span> "
            f"<span style='color:{DIM_COLOR};'>{ids_text} · {date_text} · "
            f"{len(games)} games</span>"
        )
        meta.setTextFormat(Qt.TextFormat.RichText)
        meta.setStyleSheet("font-size: 10px;")
        v.addWidget(meta)

        # Row 2 — tournament metadata (always present for matches — that's
        # the grouping key — but we re-use the same helper for consistency)
        meta_line = format_tournament_meta(games[0])
        if meta_line:
            meta_lbl = QLabel(meta_line)
            meta_lbl.setTextFormat(Qt.TextFormat.RichText)
            meta_lbl.setStyleSheet("font-size: 11px;")
            meta_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            meta_lbl.setWordWrap(True)
            v.addWidget(meta_lbl)

        # Row 3 — team matchup
        g0 = games[0]
        team_a_text = f"{g0['a1_name']} / {g0['a2_name']}"
        team_b_text = f"{g0['b1_name']} / {g0['b2_name']}"
        a_color = (
            WINNER_COLOR if a_won_match
            else (LOSER_COLOR if b_won_match else NEUTRAL_COLOR)
        )
        b_color = (
            WINNER_COLOR if b_won_match
            else (LOSER_COLOR if a_won_match else NEUTRAL_COLOR)
        )
        a_weight = "bold" if a_won_match else "normal"
        b_weight = "bold" if b_won_match else "normal"

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

        # Row 4 — series score (the big number)
        sa_color = (
            WINNER_COLOR if a_won_match
            else (LOSER_COLOR if b_won_match else NEUTRAL_COLOR)
        )
        sb_color = (
            WINNER_COLOR if b_won_match
            else (LOSER_COLOR if a_won_match else NEUTRAL_COLOR)
        )
        series = QLabel(
            f"<span style='color:{sa_color};'>{a_wins}</span>"
            f"<span style='color:{DIM_COLOR};'> : </span>"
            f"<span style='color:{sb_color};'>{b_wins}</span>"
        )
        series.setTextFormat(Qt.TextFormat.RichText)
        series.setStyleSheet("font-size: 22px; font-weight: bold;")
        series.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(series)

        # Row 5 — per-game score chips (chronological order). Scores are
        # rendered in canonical orientation (T1 on the left across every
        # game) so the colouring lines up with the matchup row.
        v.addWidget(self._per_game_scores_row(games))

        # Row 6 — aggregated RoundX line
        roundx_widget = _roundx_row(g0, roundx)
        if roundx_widget is not None:
            v.addWidget(roundx_widget)

    @staticmethod
    def _per_game_scores_row(games: list[dict]) -> QWidget:
        # Use canonical orientation so games where the pairing swapped
        # sides between sets still render with the same team on the left.
        canon_games = MatchCard._canonical_game_orientation(games)
        parts: list[str] = []
        for i, cg in enumerate(canon_games, start=1):
            sa = cg["score_a"]
            sb = cg["score_b"]
            winner = cg["winner"]
            sa_col = (
                WINNER_COLOR if winner == "A"
                else (LOSER_COLOR if winner == "B" else NEUTRAL_COLOR)
            )
            sb_col = (
                WINNER_COLOR if winner == "B"
                else (LOSER_COLOR if winner == "A" else NEUTRAL_COLOR)
            )
            parts.append(
                f"<span style='color:{DIM_COLOR};'>G{i}</span> "
                f"<span style='color:{sa_col}; font-weight:bold;'>{sa}</span>"
                f"<span style='color:{DIM_COLOR};'>-</span>"
                f"<span style='color:{sb_col}; font-weight:bold;'>{sb}</span>"
            )
        sep = f" <span style='color:{DIM_COLOR};'>·</span> "
        lbl = QLabel(sep.join(parts))
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("font-size: 11px;")
        return lbl

    @staticmethod
    def _canonical_game_orientation(games: list[dict]) -> list[dict]:
        """Per-game `{score_a, score_b, winner}` triples in canonical
        orientation.

        Anchored on the first game's roster: when a game's players are on
        the opposite side from game 1, the score pair is flipped and the
        winner letter is remapped, so callers can render every game with
        "canonical team A" always on the left. See
        `match_grouping.canonical_team_maps` for the underlying rule.
        """
        from app.core.match_grouping import (
            canonical_team_maps, _slot_names_from_game,
        )
        maps = canonical_team_maps([_slot_names_from_game(g) for g in games])
        out: list[dict] = []
        for g, m in zip(games, maps):
            sa = g.get("final_score_a", 0)
            sb = g.get("final_score_b", 0)
            if m["team_map"].get("A") == "B":
                canon_sa, canon_sb = sb, sa
            else:
                canon_sa, canon_sb = sa, sb
            w = g.get("winner_team")
            canon_w = m["team_map"].get(w, w) if w else None
            out.append({
                "score_a": canon_sa,
                "score_b": canon_sb,
                "winner": canon_w,
            })
        return out
