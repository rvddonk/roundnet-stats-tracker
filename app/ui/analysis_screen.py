"""
Analysis screen — pick a finished game (or import a CSV) and render the full
per-team / per-player stats plus chain analysis.

Layout uses a global "View:" toggle that flips the core stats table and the
service-fault table between two columns (per team) and four columns (per
player). Column headers and chain-panel sub-headings always show the actual
player names instead of "Team A / Team B".
"""

from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QBoxLayout, QLabel, QPushButton,
    QComboBox, QFileDialog, QMessageBox, QScrollArea, QFrame, QButtonGroup,
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox,
)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QColor, QBrush

from app.config import FAULT_TYPES
from app.core.analysis import compute_game_stats
from app.core.csv_import import import_game_from_csv, CsvImportError
from app.db import games_repo


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
         lambda s: _fmt_ratio(s["faults_total"], s["total_serves"]),
         lambda s: s["faults_total"] == 0,
         True),
        ("Double Faults",
         lambda s: _fmt_ratio(s["double_faults_total"], s["total_serves"]),
         lambda s: s["double_faults_total"] == 0,
         True),
    ]


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
        self._current_stats: dict | None = None
        self._view_mode: str = "team"  # 'team' or 'player'
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

        # Picker bar
        picker = QHBoxLayout()
        picker.setSpacing(8)
        picker.addWidget(QLabel("Game:"))
        self._combo = QComboBox()
        self._combo.setMinimumWidth(420)
        self._combo.currentIndexChanged.connect(self._on_game_picked)
        picker.addWidget(self._combo, stretch=1)
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

        # Body — scrollable
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(self._scroll, stretch=1)

        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(4, 4, 4, 4)
        self._body_layout.setSpacing(12)
        self._scroll.setWidget(self._body)

        self._show_empty()

    # ---------- Game list / picking ---------- #

    def _reload_games(self):
        self._games = games_repo.list_games()
        self._combo.blockSignals(True)
        self._combo.clear()
        self._combo.addItem("— Select a game —", None)
        for g in self._games:
            self._combo.addItem(self._game_label(g), g["id"])
        self._combo.blockSignals(False)
        self._combo.setCurrentIndex(0)
        self._show_empty()

    @staticmethod
    def _game_label(g: dict) -> str:
        date = _fmt_date(g.get("created_at"))
        a = f"{g.get('a1_name', 'A1')} & {g.get('a2_name', 'A2')}"
        b = f"{g.get('b1_name', 'B1')} & {g.get('b2_name', 'B2')}"
        return (
            f"#{g['id']}  {date}  |  {a}  {g['final_score_a']}–"
            f"{g['final_score_b']}  {b}"
        )

    def _on_game_picked(self, idx: int):
        if idx <= 0:
            self._show_empty()
            return
        game_id = self._combo.itemData(idx)
        if game_id is None:
            self._show_empty()
            return
        try:
            stats = compute_game_stats(game_id)
        except Exception as e:
            QMessageBox.critical(self, "Analysis failed", str(e))
            return
        self._current_stats = stats
        self._render_stats(stats)

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
            "has been selected for analysis."
        )
        self._reload_games()
        for i in range(self._combo.count()):
            if self._combo.itemData(i) == new_id:
                self._combo.setCurrentIndex(i)
                break

    # ---------- Body lifecycle ---------- #

    def _clear_body(self):
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _show_empty(self):
        self._current_stats = None
        self._core_container = None
        self._fault_container = None
        self._chain_container = None
        self._core_group = None
        self._fault_group = None
        self._stats_row_layout = None
        self._clear_body()
        msg = QLabel(
            "Pick a finished game from the dropdown, or import a CSV "
            "export to analyse."
        )
        msg.setStyleSheet("color: #8888aa; font-size: 15px;")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._body_layout.addWidget(msg)
        self._body_layout.addStretch()

    def _render_stats(self, stats: dict):
        self._clear_body()
        self._body_layout.addWidget(self._build_game_header(stats["game"]))
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

    def _build_game_header(self, game: dict) -> QWidget:
        frame = QFrame()
        frame.setStyleSheet(
            "QFrame { background-color: #16162a; border-radius: 10px; }"
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)

        title = QLabel(
            f"Game #{game['id']}  —  {_fmt_date(game['created_at'])}"
        )
        title.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #a0c4ff;"
        )
        layout.addWidget(title)

        team_a = " & ".join(game["team_a_players"])
        team_b = " & ".join(game["team_b_players"])
        winner_txt = (
            f"{(team_a if game['winner'] == 'A' else team_b)} won"
            if game["winner"] else "No declared winner"
        )
        sub = QLabel(
            f"<b>{team_a}</b>  vs  <b>{team_b}</b>"
            f"<br><b>Final score:</b> "
            f"{game['final_score_a']} – {game['final_score_b']}  "
            f"({winner_txt}, {game['total_points']} points played)"
        )
        sub.setTextFormat(Qt.TextFormat.RichText)
        sub.setStyleSheet("color: #ddddee;")
        layout.addWidget(sub)
        return frame

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
                    value = src["faults_total"]
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
