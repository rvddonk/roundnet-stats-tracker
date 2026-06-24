"""
Analysis screen — pick a finished game (or import a CSV) and render the full
per-team / per-player stats plus chain analysis.

Layout uses a global "View:" toggle that flips the core stats table and the
service-fault table between two columns (per team) and four columns (per
player). Column headers and chain-panel sub-headings always show the actual
player names instead of "Team A / Team B".
"""

import os
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QBoxLayout, QLabel, QPushButton,
    QFileDialog, QMessageBox, QScrollArea, QFrame, QButtonGroup,
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox, QStackedWidget,
)
from PyQt6.QtCore import pyqtSignal, Qt, QByteArray, QBuffer, QIODevice
from PyQt6.QtGui import QColor, QBrush

from app.config import EXPORTS_DIR, FAULT_TYPES
from app.core.analysis import (
    compute_flow, compute_game_stats, compute_match_roundx,
    compute_match_stats, compute_roundx_scores,
)
from app.core.csv_import import import_game_from_csv, CsvImportError
from app.core.html_export import render_analysis_html
from app.core.match_grouping import group_games_into_matches
from app.db import games_repo
from app.ui.widgets.game_flow import GameFlowStrip, GameFlowChart
from app.ui.widgets.game_card import GameCard, GameCardGrid
from app.ui.widgets.match_card import MatchCard, MATCH_EDGE_COLOR


# ---------------------------------------------------------------------------
#  Colour palette for the data-viz tables
# ---------------------------------------------------------------------------

LABEL_COLOR  = QColor("#aaaacc")
DIM_COLOR    = QColor("#555577")
HOT_COLOR    = QColor("#ffd6a5")
SUMMARY_COLOR = QColor("#a0c4ff")

# Heat scale for fault counts: 0 dim → 1 cream → 2-3 orange → 4-6 red → 7+ bright red
HEAT_STOPS = [
    (0, QColor("#555577")),
    (1, QColor("#ffd6a5")),
    (2, QColor("#ffb677")),
    (4, QColor("#e07b39")),
    (7, QColor("#c44a60")),
]


def _heat(value: int) -> QColor:
    if value <= 0:
        return HEAT_STOPS[0][1]
    chosen = HEAT_STOPS[0][1]
    for threshold, color in HEAT_STOPS:
        if value >= threshold:
            chosen = color
    return chosen


# ---------------------------------------------------------------------------
#  Formatters
# ---------------------------------------------------------------------------

def _fmt_pct(value, places: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.{places}f}%"


def _fmt_signed_pct(value, places: int = 1) -> str:
    if value is None:
        return "—"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value * 100:.{places}f} pp"


def _fmt_ratio(num: int, den: int) -> str:
    if den <= 0:
        return "—"
    pct = num / den
    return f"{num}/{den}\n{pct * 100:.0f}%"


def _fmt_pair(a: int, b: int) -> str:
    return f"{a}/{b}"


def _multiline_cell(text: str, color: QColor, bold: bool) -> QLabel:
    """QLabel-based cell that reliably renders embedded '\\n' as a line break.
    Used in place of QTableWidgetItem for data cells, which elided the newline."""
    lbl = QLabel(text)
    lbl.setTextFormat(Qt.TextFormat.PlainText)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    weight = "bold" if bold else "normal"
    lbl.setStyleSheet(
        f"color: {color.name()}; font-weight: {weight}; "
        "background: transparent; padding: 2px 6px;"
    )
    return lbl


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso


def _cell(text: str, align_center: bool = False,
          align_right: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    align = Qt.AlignmentFlag.AlignVCenter
    if align_center:
        align |= Qt.AlignmentFlag.AlignHCenter
    elif align_right:
        align |= Qt.AlignmentFlag.AlignRight
    else:
        align |= Qt.AlignmentFlag.AlignLeft
    item.setTextAlignment(align)
    return item


def _color_for_vs_baseline(vs: float | None) -> QColor:
    """Diverging colour scale around the team's baseline loss rate."""
    if vs is None:
        return HOT_COLOR
    if vs > 0.05:
        return QColor("#ff7070")    # noticeably above baseline → bad
    if vs > 0.01:
        return QColor("#ff9090")
    if vs < -0.05:
        return QColor("#7adb7a")    # noticeably below baseline → good
    if vs < -0.01:
        return QColor("#90ee90")
    return HOT_COLOR                 # roughly at baseline


# ---------------------------------------------------------------------------
#  Core-stats row definitions
#  Each row: (label, getter, is_zero, is_percent)
#    - is_zero(stats_for_subject) → drives the "dim grey" styling
#    - is_percent → informational; cells render identically
# ---------------------------------------------------------------------------

def _section_header(text: str, color: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color: {color}; font-size: 11px; font-weight: bold; "
        "margin-top: 4px;"
    )
    return lbl


def _breakdown_row(label: str, count: int, total: int, positive: bool) -> QWidget:
    """One row inside a RoundX card: 'Label ×count        +delta'.
    Count suffix is omitted when count == 1 for visual cleanliness."""
    wrap = QWidget()
    h = QHBoxLayout(wrap)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(4)
    text = label if count == 1 else f"{label} ×{count}"
    name = QLabel(text)
    name.setStyleSheet("color: #ddddee; font-size: 12px;")
    h.addWidget(name, stretch=1)
    sign = "+" if total >= 0 else ""
    delta_color = "#7adb7a" if positive else "#ff7070"
    delta = QLabel(f"{sign}{total}")
    delta.setStyleSheet(
        f"color: {delta_color}; font-size: 12px; font-weight: bold;"
    )
    delta.setAlignment(
        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
    )
    h.addWidget(delta)
    return wrap


def _aggregate_breakdown(
    breakdown: list[tuple[str, int]],
) -> tuple[list[tuple[str, int, int]], list[tuple[str, int, int]]]:
    """Group breakdown items by label, returning (positives, negatives) as
    lists of (label, count, total_delta). Each list is sorted by magnitude.

    "Ace, Ace, Ace" → ("Ace", 3, +15). Mixed-sign labels would be unusual
    (Weak hit vs Weak hit (lost) have distinct labels so they don't collide).
    """
    from collections import defaultdict
    counts: dict[str, int] = defaultdict(int)
    totals: dict[str, int] = defaultdict(int)
    for lbl, delta in breakdown:
        counts[lbl] += 1
        totals[lbl] += delta
    pos: list[tuple[str, int, int]] = []
    neg: list[tuple[str, int, int]] = []
    for lbl in counts:
        total = totals[lbl]
        if total >= 0:
            pos.append((lbl, counts[lbl], total))
        else:
            neg.append((lbl, counts[lbl], total))
    pos.sort(key=lambda x: -x[2])     # biggest positive first
    neg.sort(key=lambda x: x[2])      # most-negative first
    return pos, neg


def _stat_rows():
    return [
        ("Aces / Aced",
         lambda s: _fmt_pair(s["aces"], s["aced"]),
         lambda s: s["aces"] == 0 and s["aced"] == 0,
         False),
        ("Receive",
         lambda s: _fmt_ratio(s["good_receives"], s["total_receives"]),
         lambda s: s["good_receives"] == 0,
         True),
        ("Weak Receives",
         lambda s: _fmt_ratio(s["weak_receives"], s["total_receives"]),
         lambda s: s["weak_receives"] == 0,
         True),
        ("Clean Side-outs",
         lambda s: _fmt_ratio(s["clean_side_outs"], s["total_receives"]),
         lambda s: s["clean_side_outs"] == 0,
         True),
        ("Side-outs",
         lambda s: _fmt_ratio(s["side_outs"], s["total_receives"]),
         lambda s: s["side_outs"] == 0,
         True),
        ("Holds",
         lambda s: _fmt_ratio(s["holds"], s["total_receives"]),
         lambda s: s["holds"] == 0,
         True),
        ("Breaks/Broken",
         lambda s: _fmt_pair(s["breaks_for"], s["breaks_against"]),
         lambda s: s["breaks_for"] == 0 and s["breaks_against"] == 0,
         False),
        ("Touches",
         lambda s: _fmt_ratio(s["total_touches"], s["opponent_hits"]),
         lambda s: s["total_touches"] == 0,
         True),
        ("Weak Touches",
         lambda s: _fmt_ratio(s["weak_touches"], s["total_touches"]),
         lambda s: s["weak_touches"] == 0,
         True),
        ("Weak Sets",
         lambda s: _fmt_ratio(s["weak_sets"], s["sets"]),
         lambda s: s["weak_sets"] == 0,
         True),
        ("Weak Hits",
         lambda s: _fmt_ratio(s["weak_hits"], s["hits"]),
         lambda s: s["weak_hits"] == 0,
         True),
        # Errors — Hit / Set only (receive- and touch-sourced errors are
        # excluded from analysis by design; see analysis.ERROR_SOURCE_EVENTS).
        ("Errors",
         lambda s: str(s["total_errors"]),
         lambda s: s["total_errors"] == 0,
         False),
        ("Hit Errors",
         lambda s: _fmt_ratio(s["hit_errors_total"], s["hits"]),
         lambda s: s["hit_errors_total"] == 0,
         True),
        ("Set Errors",
         lambda s: _fmt_ratio(s["set_errors_total"], s["sets"]),
         lambda s: s["set_errors_total"] == 0,
         True),
        ("First Fault",
         lambda s: _fmt_ratio(s["single_faults_total"], s["points_served"]),
         lambda s: s["single_faults_total"] == 0,
         True),
        ("Double Faults",
         lambda s: _fmt_ratio(s["double_faults_total"], s["points_served"]),
         lambda s: s["double_faults_total"] == 0,
         True),
    ]


def _grab_widget_b64(widget: QWidget, width: int, height: int) -> str:
    """Render `widget` at the given pixel size to a base64-encoded PNG.

    Used for embedding the Game Flow chart and strip into the HTML export
    as data URIs — keeps the file self-contained. We resize the widget to
    force a layout pass, then `grab()` it (works for paint-event-based
    widgets without needing to show them on screen).
    """
    widget.resize(width, height)
    widget.adjustSize()
    widget.resize(width, height)  # second pass — adjustSize may have shrunk
    pix = widget.grab()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    pix.save(buf, "PNG")
    return bytes(ba.toBase64()).decode("ascii")


TABLE_STYLE = (
    "QTableWidget { background-color: #16162a; gridline-color: #2a2a55; "
    "alternate-background-color: #1a1a36; }"
    "QTableWidget::item { padding: 4px 10px; }"
    "QHeaderView::section { background-color: #1f1f3a; color: #a0c4ff; "
    "padding: 6px 10px; border: 0; font-weight: bold; }"
)


# ---------------------------------------------------------------------------
#  Screen
# ---------------------------------------------------------------------------

class AnalysisScreen(QWidget):
    back_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._games: list[dict] = []
        self._entries: list[dict] = []
        self._current_stats: dict | None = None
        self._view_mode: str = "team"  # 'team' or 'player'
        self._roundx_cache: dict[int, dict | None] = {}
        self._flow_cache: dict[int, list[dict] | None] = {}
        # When the user opens a match card, we cache the aggregate + per-game
        # stats so toggle switches are instant. Cleared on reload.
        self._current_match_games: list[int] | None = None
        self._current_match_tab: str | None = None  # 'match' or game id as str
        self._match_aggregate_cache: dict | None = None
        self._match_per_game_cache: dict[int, dict] = {}
        self._match_toggle_btns: dict[str, QPushButton] = {}
        self._core_container: QWidget | None = None
        self._fault_container: QWidget | None = None
        self._chain_container: QWidget | None = None
        self._core_group: QGroupBox | None = None
        self._fault_group: QGroupBox | None = None
        self._stats_row_layout: QBoxLayout | None = None
        self._toggle_btns: dict[str, QPushButton] = {}
        self._build_ui()
        self._reload_games()

    # ---------- UI construction ---------- #

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        # Top bar
        top = QHBoxLayout()
        btn_back = QPushButton("← Back")
        btn_back.setFixedWidth(100)
        btn_back.clicked.connect(self.back_requested)
        top.addWidget(btn_back)
        title = QLabel("Analysis")
        title.setObjectName("title_label")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(title, stretch=1)
        top.addSpacing(100)
        root.addLayout(top)

        # Picker bar — actions only; game picking happens via cards below.
        # "Change game" appears only while a game's analysis is on screen.
        picker = QHBoxLayout()
        picker.setSpacing(8)
        self._btn_change_game = QPushButton("← Change game")
        self._btn_change_game.setMinimumHeight(34)
        self._btn_change_game.clicked.connect(self._show_picker)
        self._btn_change_game.hide()
        picker.addWidget(self._btn_change_game)
        picker.addStretch(1)
        self._btn_export_html = QPushButton("📤  Export HTML…")
        self._btn_export_html.setMinimumHeight(34)
        self._btn_export_html.setToolTip(
            "Save the current analysis as a self-contained HTML file"
        )
        self._btn_export_html.clicked.connect(self._on_export_html_clicked)
        self._btn_export_html.hide()
        picker.addWidget(self._btn_export_html)
        self._btn_import = QPushButton("📥  Import CSV…")
        self._btn_import.setMinimumHeight(34)
        self._btn_import.clicked.connect(self._on_import_clicked)
        picker.addWidget(self._btn_import)
        self._btn_reload = QPushButton("↻")
        self._btn_reload.setFixedWidth(40)
        self._btn_reload.setMinimumHeight(34)
        self._btn_reload.setToolTip("Reload game list")
        self._btn_reload.clicked.connect(self._reload_games)
        picker.addWidget(self._btn_reload)
        root.addLayout(picker)

        # Match toggle row — visible only when a match (multi-game) is open.
        # Hosts [Match] · [Game 1] · [Game 2] ... buttons that swap which
        # set of stats the body renders.
        self._match_toggle_wrap = QFrame()
        self._match_toggle_wrap.setStyleSheet(
            "QFrame { background-color: #16162a; border-radius: 6px; "
            f"border: 1px solid {MATCH_EDGE_COLOR}; }}"
        )
        self._match_toggle_layout = QHBoxLayout(self._match_toggle_wrap)
        self._match_toggle_layout.setContentsMargins(10, 6, 10, 6)
        self._match_toggle_layout.setSpacing(8)
        self._match_toggle_wrap.hide()
        root.addWidget(self._match_toggle_wrap)

        # Stacked body: 0 = card-grid picker, 1 = analysis details
        self._stack = QStackedWidget()
        root.addWidget(self._stack, stretch=1)

        # Page 0 — card grid picker
        self._cards = GameCardGrid()
        self._stack.addWidget(self._cards)

        # Page 1 — scrollable analysis body
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(4, 4, 4, 4)
        self._body_layout.setSpacing(12)
        self._scroll.setWidget(self._body)
        self._stack.addWidget(self._scroll)

        self._show_picker()

    # ---------- Game list / picking ---------- #

    def _reload_games(self):
        self._games = games_repo.list_games()
        self._entries = group_games_into_matches(self._games)
        self._roundx_cache.clear()
        self._flow_cache.clear()
        self._match_aggregate_cache = None
        self._match_per_game_cache.clear()
        self._populate_card_grid()
        self._show_picker()

    def _populate_card_grid(self):
        if not self._entries:
            self._cards.show_empty(
                "No finished games yet. Import a CSV or play a game to get started."
            )
            return
        cards: list[QWidget] = []
        for entry in self._entries:
            if entry["type"] == "match":
                ids = [g["id"] for g in entry["games"]]
                card = MatchCard(entry, roundx=self._get_match_roundx(ids))
                card.clicked.connect(self._on_match_card_clicked)
            else:
                g = entry["game"]
                card = GameCard(
                    g,
                    roundx=self._compute_roundx(g["id"]),
                    flow=self._get_flow(g["id"]),
                    clickable=True,
                )
                card.clicked.connect(self._on_game_card_clicked)
            cards.append(card)
        self._cards.set_cards(cards)

    def _get_match_roundx(self, game_ids: list[int]) -> dict | None:
        try:
            return compute_match_roundx(game_ids)
        except Exception:
            return None

    def _compute_roundx(self, game_id: int) -> dict | None:
        if game_id in self._roundx_cache:
            return self._roundx_cache[game_id]
        try:
            roundx = compute_roundx_scores(game_id)
        except Exception:
            roundx = None
        self._roundx_cache[game_id] = roundx
        return roundx

    def _get_flow(self, game_id: int) -> list[dict] | None:
        if game_id in self._flow_cache:
            return self._flow_cache[game_id]
        try:
            flow = compute_flow(game_id)
        except Exception:
            flow = None
        self._flow_cache[game_id] = flow
        return flow

    def _on_game_card_clicked(self, game_id: int):
        try:
            stats = compute_game_stats(game_id)
        except Exception as e:
            QMessageBox.critical(self, "Analysis failed", str(e))
            return
        self._current_match_games = None
        self._current_match_tab = None
        self._match_aggregate_cache = None
        self._match_per_game_cache = {}
        self._current_stats = stats
        self._render_stats(stats)
        self._show_details()

    def _on_match_card_clicked(self, game_ids: list[int]):
        try:
            aggregate = compute_match_stats(game_ids)
        except Exception as e:
            QMessageBox.critical(self, "Analysis failed", str(e))
            return
        self._current_match_games = list(game_ids)
        self._match_aggregate_cache = aggregate
        self._match_per_game_cache = {}
        self._build_match_toggle(aggregate["game"]["game_results"])
        self._set_match_tab("match")
        self._show_details()

    def _build_match_toggle(self, game_results: list[dict]) -> None:
        """(Re)build the [Match] · [Game 1] · [Game 2] ... toggle bar."""
        while self._match_toggle_layout.count():
            item = self._match_toggle_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._match_toggle_btns = {}

        label = QLabel("Viewing:")
        label.setStyleSheet("color: #aaaacc; font-size: 13px;")
        self._match_toggle_layout.addWidget(label)

        def make_btn(key: str, text: str, tooltip: str = "") -> QPushButton:
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setMinimumHeight(28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if tooltip:
                btn.setToolTip(tooltip)
            btn.setStyleSheet(
                "QPushButton { background-color: #2d2d4e; color: #aaaacc; "
                "border: 1px solid #444466; border-radius: 4px; padding: 4px 12px; }"
                f"QPushButton:checked {{ background-color: {MATCH_EDGE_COLOR}; "
                "color: #16162a; border-color: #ffd700; font-weight: bold; }"
                "QPushButton:hover { border-color: #aabbff; }"
            )
            btn.clicked.connect(lambda _checked=False, k=key: self._set_match_tab(k))
            return btn

        btn_match = make_btn("match", "Match", "Aggregated stats across all games")
        self._match_toggle_btns["match"] = btn_match
        self._match_toggle_layout.addWidget(btn_match)

        for i, gr in enumerate(game_results, start=1):
            sa, sb = gr["final_score_a"], gr["final_score_b"]
            text = f"Game {i}  {sa}–{sb}"
            key = f"game:{gr['id']}"
            btn = make_btn(key, text, f"Stats for game #{gr['id']}")
            self._match_toggle_btns[key] = btn
            self._match_toggle_layout.addWidget(btn)

        self._match_toggle_layout.addStretch(1)

    def _set_match_tab(self, key: str) -> None:
        if not self._current_match_games or self._match_aggregate_cache is None:
            return
        self._current_match_tab = key
        for k, btn in self._match_toggle_btns.items():
            btn.setChecked(k == key)
        if key == "match":
            stats = self._match_aggregate_cache
        else:
            gid = int(key.split(":", 1)[1])
            stats = self._match_per_game_cache.get(gid)
            if stats is None:
                try:
                    stats = compute_game_stats(gid)
                except Exception as e:
                    QMessageBox.critical(self, "Analysis failed", str(e))
                    return
                self._match_per_game_cache[gid] = stats
        self._current_stats = stats
        self._render_stats(stats)

    # ---------- HTML export ---------- #

    def _on_export_html_clicked(self) -> None:
        if not self._current_stats:
            return
        # For a match the export always covers the entire match — aggregate
        # plus a panel per game — regardless of which tab the user is
        # currently viewing. The filename and document are derived from the
        # match aggregate, not the active per-game tab.
        is_match_export = (
            self._current_match_games is not None
            and self._match_aggregate_cache is not None
        )
        base_stats = (
            self._match_aggregate_cache if is_match_export
            else self._current_stats
        )
        default_name = self._default_export_filename(base_stats)
        default_path = str(EXPORTS_DIR / default_name)
        path, _ = QFileDialog.getSaveFileName(
            self, "Export analysis as HTML",
            default_path, "HTML files (*.html);;All files (*.*)"
        )
        if not path:
            return
        if not path.lower().endswith(".html"):
            path += ".html"

        try:
            if is_match_export:
                html_text = self._build_match_html_export(base_stats)
            else:
                html_text = self._build_single_game_html_export(base_stats)
            Path(path).write_text(html_text, encoding="utf-8")
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("HTML exported")
        msg.setText(f"File saved:\n{path}")
        open_btn = msg.addButton("Open folder", QMessageBox.ButtonRole.ActionRole)
        msg.addButton("OK", QMessageBox.ButtonRole.AcceptRole)
        msg.exec()
        if msg.clickedButton() == open_btn:
            try:
                os.startfile(os.path.dirname(path))
            except Exception:
                pass

    def _default_export_filename(self, stats: dict) -> str:
        g = stats["game"]
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        if g.get("is_match"):
            ids = "-".join(str(gr["id"]) for gr in g["game_results"])
            return f"match_{ids}_{stamp}.html"
        return f"game_{g['id']}_{stamp}.html"

    @staticmethod
    def _render_flow_strip_png_b64(flow: list[dict]) -> str:
        """Rasterize a fresh `GameFlowStrip` at 1100x60 — same widget as in
        the in-app title card, just larger so the export looks crisp."""
        strip = GameFlowStrip(flow)
        return _grab_widget_b64(strip, 1100, 60)

    @staticmethod
    def _render_flow_chart_png_b64(stats: dict) -> str:
        """Rasterize the same `GameFlowChart` shown in the in-app body for
        the supplied stats payload."""
        g = stats["game"]
        team_a = " & ".join(g["team_a_players"])
        team_b = " & ".join(g["team_b_players"])
        chart = GameFlowChart(stats.get("flow") or [], team_a, team_b)
        return _grab_widget_b64(chart, 1100, 380)

    def _build_single_game_html_export(self, stats: dict) -> str:
        flow = stats.get("flow") or []
        strip_b64 = self._render_flow_strip_png_b64(flow) if flow else None
        chart_b64 = self._render_flow_chart_png_b64(stats) if flow else None
        return render_analysis_html(
            stats,
            flow_strip_png_b64=strip_b64,
            flow_chart_png_b64=chart_b64,
        )

    def _build_match_html_export(self, aggregate: dict) -> str:
        """Assemble a tabbed match export: aggregate panel + one panel per
        game. Per-game stats are taken from the in-memory cache when
        available, otherwise loaded on-demand.
        """
        game_ids = list(self._current_match_games or [])
        # Look up canonical scores from the aggregate so the tab buttons
        # match the in-app match-toggle bar (which uses canonical, not raw,
        # scores for games where the pairing swapped sides).
        canonical = {
            gr["id"]: gr
            for gr in aggregate["game"].get("game_results", [])
        }
        per_game_payloads: list[dict] = []
        for i, gid in enumerate(game_ids, start=1):
            gstats = self._match_per_game_cache.get(gid)
            if gstats is None:
                gstats = compute_game_stats(gid)
                self._match_per_game_cache[gid] = gstats
            flow = gstats.get("flow") or []
            strip_b64 = self._render_flow_strip_png_b64(flow) if flow else None
            chart_b64 = self._render_flow_chart_png_b64(gstats) if flow else None
            gr = canonical.get(gid)
            if gr is not None:
                sa, sb = gr["final_score_a"], gr["final_score_b"]
            else:
                gg = gstats["game"]
                sa, sb = gg["final_score_a"], gg["final_score_b"]
            per_game_payloads.append({
                "stats": gstats,
                "flow_strip_png_b64": strip_b64,
                "flow_chart_png_b64": chart_b64,
                "label": f"Game {i}  {sa}–{sb}",
            })
        return render_analysis_html(aggregate, per_game=per_game_payloads)

    def _on_import_clicked(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import game CSV", "", "CSV files (*.csv);;All files (*.*)"
        )
        if not path:
            return
        try:
            new_id = import_game_from_csv(path)
        except CsvImportError as e:
            QMessageBox.critical(self, "Import failed", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "Import failed", f"Unexpected error: {e}")
            return

        QMessageBox.information(
            self, "Import successful",
            f"Imported as game #{new_id}. It now appears in history and "
            "has been opened for analysis."
        )
        self._games = games_repo.list_games()
        self._entries = group_games_into_matches(self._games)
        self._roundx_cache.clear()
        self._flow_cache.clear()
        self._match_aggregate_cache = None
        self._match_per_game_cache.clear()
        self._populate_card_grid()
        self._on_game_card_clicked(new_id)

    # ---------- Body lifecycle ---------- #

    def _clear_body(self):
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _show_picker(self):
        """Return to the card-grid picker view."""
        self._current_stats = None
        self._current_match_games = None
        self._current_match_tab = None
        self._match_aggregate_cache = None
        self._match_per_game_cache = {}
        self._core_container = None
        self._fault_container = None
        self._chain_container = None
        self._core_group = None
        self._fault_group = None
        self._stats_row_layout = None
        self._clear_body()
        self._btn_change_game.hide()
        self._btn_export_html.hide()
        self._match_toggle_wrap.hide()
        self._stack.setCurrentIndex(0)

    def _show_details(self):
        """Switch to the rendered-analysis view."""
        self._btn_change_game.show()
        self._btn_export_html.show()
        self._match_toggle_wrap.setVisible(self._current_match_games is not None)
        self._stack.setCurrentIndex(1)

    def _render_stats(self, stats: dict):
        self._clear_body()
        self._body_layout.addWidget(self._build_game_header(stats))
        # Per-point flow visualization only makes sense for one game at a
        # time; the match aggregate view passes an empty flow list.
        if stats.get("flow"):
            self._body_layout.addWidget(self._build_flow_panel(stats))
        self._body_layout.addWidget(self._build_roundx_panel(stats))
        self._body_layout.addWidget(self._build_view_toggle())

        # Core + Fault groups share a wrapper whose layout direction is flipped
        # between LeftToRight and TopToBottom by resizeEvent → side-by-side when
        # there's room, stacked when the window is narrow.
        stats_row = QWidget()
        self._stats_row_layout = QBoxLayout(
            QBoxLayout.Direction.TopToBottom, stats_row
        )
        self._stats_row_layout.setContentsMargins(0, 0, 0, 0)
        self._stats_row_layout.setSpacing(12)

        core_group = QGroupBox("Core stats")
        cg = QVBoxLayout(core_group)
        cg.setContentsMargins(10, 18, 10, 10)
        self._core_container = QWidget()
        QVBoxLayout(self._core_container).setContentsMargins(0, 0, 0, 0)
        cg.addWidget(self._core_container)
        self._stats_row_layout.addWidget(core_group, stretch=1)
        self._core_group = core_group

        fault_group = QGroupBox("Service faults by type")
        fg = QVBoxLayout(fault_group)
        fg.setContentsMargins(10, 18, 10, 10)
        self._fault_container = QWidget()
        QVBoxLayout(self._fault_container).setContentsMargins(0, 0, 0, 0)
        fg.addWidget(self._fault_container)
        self._stats_row_layout.addWidget(fault_group, stretch=1)
        self._fault_group = fault_group

        self._body_layout.addWidget(stats_row)

        chain_group = QGroupBox("Chain analysis — weak actions and point loss")
        cgc = QVBoxLayout(chain_group)
        cgc.setContentsMargins(10, 18, 10, 10)
        cgc.setSpacing(6)
        intro = QLabel(
            "Each cell shows the <b>loss rate</b> when the pattern occurred, "
            "with the point count below. "
            "<span style='color:#ff7070'>Red</span> = above team baseline "
            "(weak action correlates with losing). "
            "<span style='color:#7adb7a'>Green</span> = below baseline. "
            "Dim grey = pattern didn't occur."
        )
        intro.setTextFormat(Qt.TextFormat.RichText)
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #aaaacc; font-size: 12px;")
        cgc.addWidget(intro)
        self._chain_container = QWidget()
        QVBoxLayout(self._chain_container).setContentsMargins(0, 0, 0, 0)
        cgc.addWidget(self._chain_container)
        self._body_layout.addWidget(chain_group)

        self._body_layout.addStretch()
        self._refresh_data_tables()
        self._update_stats_row_orientation()

    # ---------- Toggle ---------- #

    def _build_view_toggle(self) -> QWidget:
        wrap = QFrame()
        wrap.setStyleSheet(
            "QFrame { background-color: #16162a; border-radius: 6px; }"
        )
        h = QHBoxLayout(wrap)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(8)
        lbl = QLabel("View:")
        lbl.setStyleSheet("color: #aaaacc; font-size: 13px;")
        h.addWidget(lbl)

        grp = QButtonGroup(wrap)
        grp.setExclusive(True)
        self._toggle_btns = {}
        for mode, label in (("team", "Per team"), ("player", "Per player")):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(self._view_mode == mode)
            btn.setMinimumHeight(28)
            btn.setMinimumWidth(110)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton { background-color: #2d2d4e; color: #aaaacc; "
                "border: 1px solid #444466; border-radius: 4px; padding: 4px 12px; }"
                "QPushButton:checked { background-color: #4a90d9; color: #ffffff; "
                "border-color: #5aa0e9; }"
                "QPushButton:hover { border-color: #aabbff; }"
            )
            btn.clicked.connect(lambda _checked=False, m=mode: self._set_view_mode(m))
            grp.addButton(btn)
            h.addWidget(btn)
            self._toggle_btns[mode] = btn
        h.addStretch()
        return wrap

    def _set_view_mode(self, mode: str):
        if mode not in ("team", "player") or mode == self._view_mode:
            self._toggle_btns.get(self._view_mode, QPushButton()).setChecked(True)
            return
        self._view_mode = mode
        self._refresh_data_tables()
        # Player view has twice as many data columns per table — re-evaluate
        # whether they still fit side-by-side at the current width.
        self._update_stats_row_orientation()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_stats_row_orientation()

    def _update_stats_row_orientation(self):
        """Side-by-side when the window is wide enough; stacked when narrow.

        Team view (2 data columns per table) fits side-by-side at fairly modest
        widths; player view (4 data columns per table) needs significantly
        more room before stacking would just shrink the cells.
        """
        if self._stats_row_layout is None:
            return
        threshold = 1300 if self._view_mode == "player" else 900
        side_by_side = self.width() >= threshold
        direction = (
            QBoxLayout.Direction.LeftToRight if side_by_side
            else QBoxLayout.Direction.TopToBottom
        )
        if self._stats_row_layout.direction() != direction:
            self._stats_row_layout.setDirection(direction)

    def _refresh_data_tables(self):
        if self._core_container is not None and self._current_stats is not None:
            self._replace_contents(
                self._core_container,
                self._build_core_stats_table(self._current_stats),
            )
        if self._fault_container is not None and self._current_stats is not None:
            self._replace_contents(
                self._fault_container,
                self._build_fault_table(self._current_stats),
            )
        if self._chain_container is not None and self._current_stats is not None:
            self._replace_contents(
                self._chain_container,
                self._build_chain_content(self._current_stats),
            )

    @staticmethod
    def _replace_contents(container: QWidget, new_widget: QWidget):
        layout = container.layout()
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        layout.addWidget(new_widget)

    # ---------- RoundX panel ---------- #

    def _build_roundx_panel(self, stats: dict) -> QWidget:
        """Top-of-screen panel: per-player RoundX score with aggregated
        Key Plays breakdown. Always rendered with all four slots since
        RoundX is a per-player metric (no team aggregation)."""
        roundx = stats.get("roundx") or {}
        sample = next(iter(roundx.values()), {}) if roundx else {}
        rated = sample.get("rated", True)
        rallies = sample.get("counted_rallies", 0)
        scalar = sample.get("scalar", 1.0)

        group = QGroupBox("RoundX — Match Impact")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(10, 18, 10, 10)
        layout.setSpacing(8)

        # Subtitle: how the scaling is being applied to this game.
        sub_parts = [f"{rallies} rallies", f"scaling ×{scalar:.2f}"]
        if not rated:
            sub_parts.append(
                f"<span style='color:#ff9090'>unrated "
                f"(&lt; {sample.get('counted_rallies', 0) or 20} rallies)</span>"
            )
        sub_parts.append(
            "<span style='color:#8888aa'>· benchmark is a 40-rally game "
            "(typical 21-point match)</span>"
        )
        sub = QLabel(" · ".join(sub_parts))
        sub.setTextFormat(Qt.TextFormat.RichText)
        sub.setStyleSheet("color: #aaaacc; font-size: 12px;")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        # 4 player cards in a row
        cards_row = QHBoxLayout()
        cards_row.setSpacing(10)
        names = stats["game"]["names"]
        for slot in ("A1", "A2", "B1", "B2"):
            cards_row.addWidget(
                self._build_roundx_card(slot, names[slot], roundx.get(slot)),
                stretch=1,
            )
        layout.addLayout(cards_row)
        return group

    @staticmethod
    def _build_roundx_card(slot: str, name: str, d: dict | None) -> QWidget:
        card = QFrame()
        card.setStyleSheet(
            "QFrame { background-color: #1a1a36; border-radius: 8px; "
            "border: 1px solid #2a2a55; }"
        )
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(6)

        # Header — slot + name
        hdr = QLabel(f"<b>{slot}</b>  {name}")
        hdr.setTextFormat(Qt.TextFormat.RichText)
        hdr.setStyleSheet("color: #a0c4ff; font-size: 13px;")
        v.addWidget(hdr)

        if not d:
            placeholder = QLabel("—")
            placeholder.setStyleSheet("color: #555577; font-size: 24px;")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v.addWidget(placeholder)
            v.addStretch()
            return card

        score = d.get("score", 0)
        rel = d.get("relative_to_avg", 0)

        # Score block — big number + relative-to-avg subtitle
        score_color = (
            "#7adb7a" if rel > 0 else ("#ff7070" if rel < 0 else "#ffd6a5")
        )
        sign = "+" if score >= 0 else ""
        score_lbl = QLabel(f"{sign}{score}")
        score_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        score_lbl.setStyleSheet(
            f"color: {score_color}; font-size: 32px; font-weight: bold;"
        )
        v.addWidget(score_lbl)

        rel_sign = "+" if rel >= 0 else ""
        rel_lbl = QLabel(f"({rel_sign}{rel} vs game avg)")
        rel_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rel_lbl.setStyleSheet("color: #aaaacc; font-size: 11px;")
        v.addWidget(rel_lbl)

        # Divider
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("color: #2a2a55; background-color: #2a2a55;")
        div.setFixedHeight(1)
        v.addWidget(div)

        # Aggregated breakdown — positives and negatives in separate blocks
        breakdown = d.get("breakdown") or []
        pos, neg = _aggregate_breakdown(breakdown)

        if pos:
            v.addWidget(_section_header("Boosted score", "#7adb7a"))
            for lbl, count, total in pos:
                v.addWidget(_breakdown_row(lbl, count, total, positive=True))
        if neg:
            v.addWidget(_section_header("Hurt score", "#ff7070"))
            for lbl, count, total in neg:
                v.addWidget(_breakdown_row(lbl, count, total, positive=False))

        if not pos and not neg:
            empty = QLabel("No scored events")
            empty.setStyleSheet("color: #555577; font-size: 11px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v.addWidget(empty)

        v.addStretch()
        return card

    # ---------- Game header ---------- #

    def _build_game_header(self, stats: dict) -> QWidget:
        game = stats["game"]
        is_match = bool(game.get("is_match"))
        flow = stats.get("flow") or []
        frame = QFrame()
        border = f"border: 1px solid {MATCH_EDGE_COLOR};" if is_match else ""
        frame.setStyleSheet(
            f"QFrame {{ background-color: #16162a; border-radius: 10px; {border} }}"
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        if is_match:
            tournament = (game.get("tournament") or "Match")
            n_games = len(game["game_results"])
            title_text = (
                f"Match  —  {n_games} games  ·  starting "
                f"{_fmt_date(game['created_at'])}"
            )
            title_color = MATCH_EDGE_COLOR
        else:
            title_text = f"Game #{game['id']}  —  {_fmt_date(game['created_at'])}"
            title_color = "#a0c4ff"
        title = QLabel(title_text)
        title.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {title_color};"
        )
        layout.addWidget(title)

        team_a = " & ".join(game["team_a_players"])
        team_b = " & ".join(game["team_b_players"])
        winner_txt = (
            f"{(team_a if game['winner'] == 'A' else team_b)} won"
            if game["winner"] else "No declared winner"
        )

        if is_match:
            a_wins, b_wins = game["series_score"]
            results_bits = []
            for i, gr in enumerate(game["game_results"], start=1):
                w = gr["winner"]
                a_col = "#7adb7a" if w == "A" else ("#8888aa" if w == "B" else "#e0e0e0")
                b_col = "#7adb7a" if w == "B" else ("#8888aa" if w == "A" else "#e0e0e0")
                results_bits.append(
                    f"<span style='color:#aaaacc;'>G{i}</span> "
                    f"<span style='color:{a_col}; font-weight:bold;'>{gr['final_score_a']}</span>"
                    f"<span style='color:#8888aa;'>-</span>"
                    f"<span style='color:{b_col}; font-weight:bold;'>{gr['final_score_b']}</span>"
                )
            results_line = " &nbsp;·&nbsp; ".join(results_bits)
            sub = QLabel(
                f"<b>{team_a}</b>  vs  <b>{team_b}</b>"
                f"<br><b>Series:</b> {a_wins} – {b_wins}  "
                f"({winner_txt}, {game['total_points']} points played across "
                f"{n_games} games)"
                f"<br><span style='color:#aaaacc; font-size:12px;'>{results_line}</span>"
            )
        else:
            sub = QLabel(
                f"<b>{team_a}</b>  vs  <b>{team_b}</b>"
                f"<br><b>Final score:</b> "
                f"{game['final_score_a']} – {game['final_score_b']}  "
                f"({winner_txt}, {game['total_points']} points played)"
            )
        sub.setTextFormat(Qt.TextFormat.RichText)
        sub.setStyleSheet("color: #ddddee;")
        layout.addWidget(sub)

        # Compact flow strip — only meaningful for a single game.
        if flow:
            strip = GameFlowStrip(flow)
            strip.setStyleSheet(
                "background-color: #12122a; border-radius: 6px;"
            )
            layout.addWidget(strip)
        return frame

    # ---------- Game-flow panel ---------- #

    def _build_flow_panel(self, stats: dict) -> QWidget:
        flow = stats.get("flow") or []
        game = stats["game"]
        team_a = " & ".join(game["team_a_players"])
        team_b = " & ".join(game["team_b_players"])

        group = QGroupBox("Game Flow")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(10, 18, 10, 10)
        layout.setSpacing(6)

        intro = QLabel(
            "Cumulative score per team across every point. "
            "Filled <b>circles</b> mark <span style='color:#ffd700'>breaks</span> — "
            "points where the serving team scored. The shaded band between "
            "the lines is tinted in the leader's colour, so runs of one team "
            "show as a stretch of their colour."
        )
        intro.setTextFormat(Qt.TextFormat.RichText)
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #aaaacc; font-size: 12px;")
        layout.addWidget(intro)

        chart = GameFlowChart(flow, team_a, team_b)
        layout.addWidget(chart)
        return group

    # ---------- Column resolution ---------- #

    def _columns_for_mode(self, stats: dict) -> list[tuple[str, dict]]:
        """Return [(column_header, source_stats_dict), ...] for the current mode."""
        game = stats["game"]
        names = game["names"]
        if self._view_mode == "team":
            return [
                (" & ".join(game["team_a_players"]), stats["teams"]["A"]),
                (" & ".join(game["team_b_players"]), stats["teams"]["B"]),
            ]
        return [
            (f"{names[slot]}  ({slot})", stats["players"][slot])
            for slot in ("A1", "A2", "B1", "B2")
        ]

    # ---------- Core stats table ---------- #

    def _build_core_stats_table(self, stats: dict) -> QTableWidget:
        columns = self._columns_for_mode(stats)
        # Drop rows tagged player_only when we're rendering teams.
        rows = [
            row for row in _stat_rows()
            if not (len(row) >= 5 and row[4] and self._view_mode != "player")
        ]
        row_h = 44

        tbl = QTableWidget()
        tbl.setColumnCount(1 + len(columns))
        tbl.setRowCount(len(rows))
        tbl.setHorizontalHeaderLabels(["Stat", *(c[0] for c in columns)])
        tbl.verticalHeader().setVisible(False)
        tbl.verticalHeader().setDefaultSectionSize(row_h)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        tbl.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        tbl.setAlternatingRowColors(True)
        tbl.setShowGrid(False)
        tbl.setStyleSheet(TABLE_STYLE)

        for r, row in enumerate(rows):
            label, getter, is_zero, _is_pct = row[0], row[1], row[2], row[3]
            tbl.setRowHeight(r, row_h)
            lbl_item = _cell(label)
            lbl_item.setForeground(QBrush(LABEL_COLOR))
            tbl.setItem(r, 0, lbl_item)
            for c, (_, src) in enumerate(columns, start=1):
                color = DIM_COLOR if is_zero(src) else HOT_COLOR
                tbl.setCellWidget(
                    r, c,
                    _multiline_cell(getter(src), color, bold=not is_zero(src)),
                )

        hdr = tbl.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setMinimumSectionSize(80)
        for c in range(1, 1 + len(columns)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
        tbl.setMinimumHeight(row_h * (len(rows) + 1) + 4)
        tbl.setMaximumHeight(row_h * (len(rows) + 1) + 20)
        return tbl

    # ---------- Fault table ---------- #

    def _build_fault_table(self, stats: dict) -> QTableWidget:
        columns = self._columns_for_mode(stats)

        # Union of declared fault types + any unexpected ones in the data.
        fault_types: list[str] = list(FAULT_TYPES)
        for src_dict in stats["teams"].values():
            for k in src_dict["all_faults_by_type"]:
                if k not in fault_types:
                    fault_types.append(k)

        SUMMARY_LABELS = ("First Faults", "Double Faults")
        row_labels = fault_types + list(SUMMARY_LABELS)

        tbl = QTableWidget()
        tbl.setColumnCount(1 + len(columns))
        tbl.setRowCount(len(row_labels))
        tbl.setHorizontalHeaderLabels(["Fault type", *(c[0] for c in columns)])
        tbl.verticalHeader().setVisible(False)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        tbl.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        tbl.setAlternatingRowColors(True)
        tbl.setShowGrid(False)
        tbl.setStyleSheet(TABLE_STYLE)

        for r, label in enumerate(row_labels):
            is_summary = label in SUMMARY_LABELS
            lbl_item = _cell(label)
            lbl_font = lbl_item.font()
            if is_summary:
                lbl_font.setBold(True)
                lbl_item.setForeground(QBrush(SUMMARY_COLOR))
            else:
                lbl_item.setForeground(QBrush(LABEL_COLOR))
            lbl_item.setFont(lbl_font)
            tbl.setItem(r, 0, lbl_item)

            for c, (_, src) in enumerate(columns, start=1):
                if label == "First Faults":
                    value = src["single_faults_total"]
                elif label == "Double Faults":
                    value = src["double_faults_total"]
                else:
                    value = src["all_faults_by_type"].get(label, 0)

                color = DIM_COLOR if value == 0 else _heat(value)
                bold = is_summary or value != 0
                tbl.setCellWidget(
                    r, c, _multiline_cell(str(value), color, bold=bold)
                )

        hdr = tbl.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setMinimumSectionSize(70)
        for c in range(1, 1 + len(columns)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
        row_h = 30
        tbl.verticalHeader().setDefaultSectionSize(row_h)
        tbl.setMinimumHeight(row_h * (len(row_labels) + 1) + 4)
        tbl.setMaximumHeight(row_h * (len(row_labels) + 1) + 20)
        return tbl

    # ---------- Chain analysis (toggle-driven) ---------- #

    def _chain_subjects(self, stats: dict) -> list[tuple[str, float, list[dict]]]:
        """
        Return [(header, baseline_loss_rate, pattern_dicts), ...] matching the
        current view mode.

        In team mode, two subjects (teams) with the 8 team-level chain
        patterns. In player mode, four subjects (one per slot) with the
        4 per-player patterns; each player's baseline is their team's.
        """
        game = stats["game"]
        names = game["names"]
        chains = stats["chains"]
        if self._view_mode == "team":
            return [
                (" & ".join(game["team_a_players"]),
                 chains["A"]["baseline_loss_rate"],
                 chains["A"]["team_patterns"]),
                (" & ".join(game["team_b_players"]),
                 chains["B"]["baseline_loss_rate"],
                 chains["B"]["team_patterns"]),
            ]
        out: list[tuple[str, float, list[dict]]] = []
        for team in ("A", "B"):
            base = chains[team]["baseline_loss_rate"]
            for slot in (f"{team}1", f"{team}2"):
                pp = chains[team]["per_player"][slot]
                out.append((f"{names[slot]}  ({slot})", base, pp["patterns"]))
        return out

    def _build_chain_content(self, stats: dict) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        subjects = self._chain_subjects(stats)
        if not subjects:
            layout.addWidget(QLabel("No data."))
            return wrap

        # Baseline summary strip
        parts = [
            f"<b style='color:#a0c4ff'>{name}</b> "
            f"<span style='color:#ffd6a5'>{_fmt_pct(base)}</span>"
            for (name, base, _) in subjects
        ]
        bl = QLabel("Baseline loss rate · " + "   |   ".join(parts))
        bl.setTextFormat(Qt.TextFormat.RichText)
        bl.setWordWrap(True)
        bl.setStyleSheet(
            "color: #aaaacc; font-size: 12px; padding: 6px 10px; "
            "background-color: #12122a; border-radius: 6px;"
        )
        layout.addWidget(bl)

        layout.addWidget(self._build_chain_table(subjects))
        return wrap

    def _build_chain_table(
        self, subjects: list[tuple[str, float, list[dict]]]
    ) -> QTableWidget:
        # Pattern set is the same across subjects within a single mode;
        # take labels from the first subject.
        pattern_labels = [p["pattern"] for p in subjects[0][2]]

        tbl = QTableWidget()
        tbl.setColumnCount(1 + len(subjects))
        tbl.setRowCount(len(pattern_labels))
        tbl.setHorizontalHeaderLabels(["Pattern", *(s[0] for s in subjects)])
        tbl.verticalHeader().setVisible(False)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        tbl.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        tbl.setAlternatingRowColors(True)
        tbl.setShowGrid(False)
        tbl.setWordWrap(True)
        tbl.setStyleSheet(TABLE_STYLE)

        for r, pat_label in enumerate(pattern_labels):
            lbl = _cell(pat_label)
            lbl.setForeground(QBrush(LABEL_COLOR))
            tbl.setItem(r, 0, lbl)
            for c, (_name, _baseline, patterns) in enumerate(subjects, start=1):
                pat = next(
                    (p for p in patterns if p["pattern"] == pat_label), None
                )
                tbl.setCellWidget(r, c, self._chain_cell(pat))

        hdr = tbl.horizontalHeader()
        # Pattern labels are long sentences — let that column take the room
        # it needs (capped), and stretch the data columns into what's left.
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setMinimumSectionSize(90)
        for c in range(1, 1 + len(subjects)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)

        # Two lines per cell (rate + count) → taller rows.
        row_h = 44
        tbl.verticalHeader().setDefaultSectionSize(row_h)
        tbl.setMinimumHeight(row_h * (len(pattern_labels) + 1) + 4)
        tbl.setMaximumHeight(row_h * (len(pattern_labels) + 1) + 20)
        return tbl

    @staticmethod
    def _chain_cell(pat: dict | None) -> QLabel:
        """Render one chain cell: 'loss% \\n n=count', coloured vs baseline."""
        if pat is None or pat["count"] == 0:
            return _multiline_cell("—", DIM_COLOR, bold=False)

        rate = _fmt_pct(pat["loss_rate"])
        count = pat["count"]
        vs = pat["vs_baseline"]
        vs_suffix = ""
        if vs is not None and abs(vs) > 0.001:
            sign = "+" if vs >= 0 else ""
            vs_suffix = f"  {sign}{vs * 100:.0f}pp"
        text = f"{rate}\nn={count}{vs_suffix}"
        return _multiline_cell(text, _color_for_vs_baseline(vs), bold=True)
