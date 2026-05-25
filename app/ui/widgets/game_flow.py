"""
Game-flow visualizations for the Analysis screen.

Two widgets read the same `flow` list (one dict per scored point — see
`app.core.analysis._compute_flow`):

* `GameFlowStrip` — compact two-row score grid for the title card.
  Renders the user's "table of running scores" idea: each column is the score
  state after one point, breaks (serving team wins) get a gold border and
  the winner's cell is tinted in that team's colour.

* `GameFlowChart` — full step-line chart for the analysis body. Cumulative
  per-team scores over points, with break-point markers, a momentum band
  (filled gap between the lines coloured by who leads), and an overtime
  divider.
"""

from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QPolygonF, QPainterPath,
)
from PyQt6.QtWidgets import QWidget, QSizePolicy


# Shared palette
TEAM_A_COLOR = QColor("#5fb3ff")
TEAM_B_COLOR = QColor("#ffa566")
BREAK_COLOR = QColor("#ffd700")
OT_TINT = QColor(220, 80, 110, 35)
OT_LINE = QColor("#c44a60")
BACKGROUND = QColor("#16162a")
PANEL_BG = QColor("#12122a")
GRID = QColor("#2a2a55")
SUB_GRID = QColor("#22224a")
TEXT_DIM = QColor("#8888aa")
TEXT = QColor("#e0e0e0")
ZERO_DIM = QColor("#555577")


def _tinted(base: QColor, alpha: int) -> QColor:
    c = QColor(base)
    c.setAlpha(alpha)
    return c


# ---------------------------------------------------------------------------
#  Compact strip (title card)
# ---------------------------------------------------------------------------


class GameFlowStrip(QWidget):
    """Two-row running-score grid. Auto-sizes cell width to the strip width.

    Each column = one game state. Column 0 shows 0-0 (kickoff), each later
    column shows the score after that point. The winning team's cell is
    tinted; break-cells (serving team won) get a gold border. OT columns
    have a subtle red wash behind both cells.
    """

    H_PADDING = 8
    V_PADDING = 4
    LABEL_W = 24
    MIN_HEIGHT = 56

    def __init__(self, flow: list[dict], parent=None):
        super().__init__(parent)
        self._flow = flow or []
        self.setMinimumHeight(self.MIN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        p.fillRect(rect, BACKGROUND)

        if not self._flow:
            p.setPen(TEXT_DIM)
            f = p.font()
            f.setPointSizeF(10)
            p.setFont(f)
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "No points played")
            return

        w = self.width()
        h = self.height()
        pad_x = self.H_PADDING
        pad_y = self.V_PADDING
        label_w = self.LABEL_W

        # Cells: [initial 0-0] + one per scored point.
        n = len(self._flow) + 1
        cells_total_w = max(1, w - label_w - pad_x * 2)
        cell_w = cells_total_w / n
        row_h = (h - pad_y * 2) / 2

        # Row labels
        label_font = QFont(self.font())
        label_font.setBold(True)
        label_font.setPointSizeF(9)
        p.setFont(label_font)
        p.setPen(TEAM_A_COLOR)
        p.drawText(
            QRectF(pad_x, pad_y, label_w - 2, row_h),
            Qt.AlignmentFlag.AlignCenter, "A",
        )
        p.setPen(TEAM_B_COLOR)
        p.drawText(
            QRectF(pad_x, pad_y + row_h, label_w - 2, row_h),
            Qt.AlignmentFlag.AlignCenter, "B",
        )

        # Centre divider for visual separation
        p.setPen(QPen(GRID, 1))
        mid_y = pad_y + row_h
        p.drawLine(
            int(pad_x + label_w), int(mid_y),
            int(w - pad_x), int(mid_y),
        )

        # Cell text font — auto-shrink for narrow cells.
        cell_font = QFont(self.font())
        cell_font.setBold(True)
        target_pt = max(7.0, min(11.0, cell_w * 0.40))
        cell_font.setPointSizeF(target_pt)
        p.setFont(cell_font)

        x0 = pad_x + label_w
        # Build a synthetic "initial" cell so the rendering loop is uniform.
        cells: list[dict] = [{
            "a": 0, "b": 0, "winner": None,
            "is_break": False, "in_ot": False, "initial": True,
        }]
        for f in self._flow:
            cells.append({
                "a": f["score_a"], "b": f["score_b"],
                "winner": f["winner"], "is_break": f["is_break"],
                "in_ot": f["in_ot"], "initial": False,
            })

        for i, c in enumerate(cells):
            cx = x0 + cell_w * i
            ra = QRectF(cx + 1, pad_y + 1, cell_w - 2, row_h - 2)
            rb = QRectF(cx + 1, pad_y + row_h + 1, cell_w - 2, row_h - 2)

            # OT tint behind both rows
            if c["in_ot"]:
                p.fillRect(QRectF(cx, pad_y, cell_w, row_h * 2), OT_TINT)

            # Winner cell tint
            if not c["initial"]:
                base = TEAM_A_COLOR if c["winner"] == "A" else TEAM_B_COLOR
                won_rect = ra if c["winner"] == "A" else rb
                p.fillRect(won_rect, _tinted(base, 70))
                if c["is_break"]:
                    pen = QPen(BREAK_COLOR, 1.4)
                    p.setPen(pen)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRect(won_rect.adjusted(0.5, 0.5, -0.5, -0.5))

            # Text — winner's score in team colour, loser's dim
            p.setPen(TEAM_A_COLOR if c["a"] > 0 else ZERO_DIM)
            p.drawText(ra, Qt.AlignmentFlag.AlignCenter, str(c["a"]))
            p.setPen(TEAM_B_COLOR if c["b"] > 0 else ZERO_DIM)
            p.drawText(rb, Qt.AlignmentFlag.AlignCenter, str(c["b"]))


# ---------------------------------------------------------------------------
#  Full chart (analysis body)
# ---------------------------------------------------------------------------


class GameFlowChart(QWidget):
    """Step-line chart of cumulative per-team scores over points.

    Y axis = score, X axis = point number. Two stepped lines (one per team)
    are drawn from (0, 0) outwards; the gap between them is filled with the
    leader's tinted colour, producing a momentum band that visually shows
    runs. Break points (serving team won) get a gold-outlined marker on the
    winner's line. A vertical dashed line marks the start of overtime.
    """

    PAD_LEFT = 38
    PAD_RIGHT = 16
    PAD_TOP = 28        # leaves room for legend
    PAD_BOTTOM = 32
    MARKER_R = 4.0
    BREAK_R = 5.5
    LINE_WIDTH = 2.2

    def __init__(self, flow: list[dict],
                 team_a_label: str, team_b_label: str,
                 parent=None):
        super().__init__(parent)
        self._flow = flow or []
        self._team_a = team_a_label
        self._team_b = team_b_label
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ---- layout helpers -------------------------------------------------- #

    def _plot_rect(self) -> QRectF:
        return QRectF(
            self.PAD_LEFT,
            self.PAD_TOP,
            max(1.0, self.width() - self.PAD_LEFT - self.PAD_RIGHT),
            max(1.0, self.height() - self.PAD_TOP - self.PAD_BOTTOM),
        )

    def _max_score(self) -> int:
        if not self._flow:
            return 1
        last = self._flow[-1]
        return max(1, last["score_a"], last["score_b"])

    def _x_for(self, point_idx: int, rect: QRectF) -> float:
        # point_idx 0 = pre-game (0-0); 1..n = after that point.
        n = len(self._flow)
        if n == 0:
            return rect.left()
        return rect.left() + (point_idx / n) * rect.width()

    def _y_for(self, score: int, rect: QRectF, max_score: int) -> float:
        return rect.bottom() - (score / max_score) * rect.height()

    # ---- paint ----------------------------------------------------------- #

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        full = self.rect()
        p.fillRect(full, PANEL_BG)

        if not self._flow:
            p.setPen(TEXT_DIM)
            f = p.font(); f.setPointSizeF(11); p.setFont(f)
            p.drawText(full, Qt.AlignmentFlag.AlignCenter, "No points played")
            return

        rect = self._plot_rect()
        max_score = self._max_score()

        self._draw_grid(p, rect, max_score)
        self._draw_ot_region(p, rect)
        self._draw_momentum_band(p, rect, max_score)
        self._draw_team_lines(p, rect, max_score)
        self._draw_break_markers(p, rect, max_score)
        self._draw_axes(p, rect, max_score)
        self._draw_legend(p, rect)
        self._draw_final_callout(p, rect, max_score)

    def _draw_grid(self, p: QPainter, rect: QRectF, max_score: int) -> None:
        # Horizontal score gridlines.
        step = self._tick_step(max_score)
        pen_major = QPen(GRID, 1, Qt.PenStyle.SolidLine)
        pen_minor = QPen(SUB_GRID, 1, Qt.PenStyle.DotLine)
        s = 0
        while s <= max_score:
            y = self._y_for(s, rect, max_score)
            p.setPen(pen_major if s % step == 0 else pen_minor)
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            s += 1
        # Left border
        p.setPen(QPen(GRID, 1))
        p.drawLine(QPointF(rect.left(), rect.top()),
                   QPointF(rect.left(), rect.bottom()))
        p.drawLine(QPointF(rect.left(), rect.bottom()),
                   QPointF(rect.right(), rect.bottom()))

    @staticmethod
    def _tick_step(max_score: int) -> int:
        if max_score <= 10:
            return 2
        if max_score <= 20:
            return 5
        return 10

    def _draw_ot_region(self, p: QPainter, rect: QRectF) -> None:
        """Tint the region where any flow row has in_ot=True."""
        # Find first OT index, if any.
        first_ot = next(
            (i for i, f in enumerate(self._flow) if f["in_ot"]), None,
        )
        if first_ot is None:
            return
        # OT starts right after the trigger point — first_ot is the 0-based
        # index into flow of the first OT point; render the band from there.
        x0 = self._x_for(first_ot, rect)
        p.fillRect(QRectF(x0, rect.top(), rect.right() - x0, rect.height()),
                   OT_TINT)
        # Divider
        pen = QPen(OT_LINE, 1.2, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawLine(QPointF(x0, rect.top()), QPointF(x0, rect.bottom()))
        # Label
        font = QFont(self.font()); font.setPointSizeF(8.5); font.setBold(True)
        p.setFont(font)
        p.setPen(OT_LINE)
        p.drawText(
            QRectF(x0 + 4, rect.top() + 2, 60, 14),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "OT",
        )

    def _step_path(self, rect: QRectF, max_score: int,
                   key: str) -> QPainterPath:
        """Build a stepped path (right-then-up) for one team's score line."""
        path = QPainterPath()
        path.moveTo(self._x_for(0, rect),
                    self._y_for(0, rect, max_score))
        prev_score = 0
        for i, f in enumerate(self._flow, start=1):
            x = self._x_for(i, rect)
            new_score = f[key]
            # Horizontal segment to new x at old score, then vertical to new.
            path.lineTo(x, self._y_for(prev_score, rect, max_score))
            path.lineTo(x, self._y_for(new_score, rect, max_score))
            prev_score = new_score
        return path

    def _draw_momentum_band(self, p: QPainter, rect: QRectF,
                            max_score: int) -> None:
        """Fill between the two stepped lines, coloured by the leader.

        Sample at every point — when the leader flips, close the polygon and
        start a new one in the other colour."""
        if not self._flow:
            return
        polys: list[tuple[str, QPolygonF]] = []
        # We walk point-by-point, accumulating a polygon per "lead" segment.
        leader = "tie"  # before any point, scores are tied at 0
        current: QPolygonF = QPolygonF()
        current.append(QPointF(self._x_for(0, rect),
                               self._y_for(0, rect, max_score)))

        def y(team_key: str, score: int) -> float:
            return self._y_for(score, rect, max_score)

        # Maintain "top" (higher-on-screen / leader) and "bot" lines and shade.
        # Simplification: for each step at point i, we generate the rectangle
        # between the two lines from x_{i-1} to x_i at the *prior* scores,
        # then the vertical change of the winner from prior_score to new_score
        # (zero-width band collapses there). To keep this readable we use a
        # cleaner approach: build the A polygon and B polygon as a fill
        # against the opposite line.
        a_path = self._step_path(rect, max_score, "score_a")
        b_path = self._step_path(rect, max_score, "score_b")

        # Build a closed fill region: top = team_a path forward, bottom =
        # team_b path reversed. We need to clip the colour by who's ahead.
        # Simpler & visually effective: shade A's area under A line and above
        # B line in A's colour when A is ahead, and vice versa. We do per-step
        # rectangles using prior_score values (the score during the segment
        # between point i-1 and i, before the awarded point).

        prev_a, prev_b = 0, 0
        for i, f in enumerate(self._flow, start=1):
            x_left = self._x_for(i - 1, rect)
            x_right = self._x_for(i, rect)
            # During the segment between points i-1 and i the on-screen score
            # is (prev_a, prev_b). The point i then increments one of them.
            if prev_a > prev_b:
                top_y = y("a", prev_a)
                bot_y = y("b", prev_b)
                colour = _tinted(TEAM_A_COLOR, 50)
                p.fillRect(QRectF(x_left, top_y, x_right - x_left, bot_y - top_y), colour)
            elif prev_b > prev_a:
                top_y = y("b", prev_b)
                bot_y = y("a", prev_a)
                colour = _tinted(TEAM_B_COLOR, 50)
                p.fillRect(QRectF(x_left, top_y, x_right - x_left, bot_y - top_y), colour)
            # tied → no band

            prev_a = f["score_a"]
            prev_b = f["score_b"]

    def _draw_team_lines(self, p: QPainter, rect: QRectF,
                         max_score: int) -> None:
        a_path = self._step_path(rect, max_score, "score_a")
        b_path = self._step_path(rect, max_score, "score_b")
        pen_a = QPen(TEAM_A_COLOR, self.LINE_WIDTH)
        pen_a.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen_a.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen_b = QPen(TEAM_B_COLOR, self.LINE_WIDTH)
        pen_b.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen_b.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(pen_a)
        p.drawPath(a_path)
        p.setPen(pen_b)
        p.drawPath(b_path)

    def _draw_break_markers(self, p: QPainter, rect: QRectF,
                            max_score: int) -> None:
        for i, f in enumerate(self._flow, start=1):
            if not f["is_break"]:
                continue
            score = f["score_a"] if f["winner"] == "A" else f["score_b"]
            colour = TEAM_A_COLOR if f["winner"] == "A" else TEAM_B_COLOR
            cx = self._x_for(i, rect)
            cy = self._y_for(score, rect, max_score)
            # Halo
            p.setPen(QPen(BREAK_COLOR, 1.6))
            p.setBrush(QBrush(colour))
            p.drawEllipse(QPointF(cx, cy), self.BREAK_R, self.BREAK_R)

    def _draw_axes(self, p: QPainter, rect: QRectF, max_score: int) -> None:
        font = QFont(self.font())
        font.setPointSizeF(8.5)
        p.setFont(font)
        p.setPen(TEXT_DIM)
        # Y ticks
        step = self._tick_step(max_score)
        s = 0
        while s <= max_score:
            y = self._y_for(s, rect, max_score)
            p.drawText(
                QRectF(0, y - 8, rect.left() - 4, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                str(s),
            )
            s += step
        # X ticks: a handful at sensible intervals (1, every 5 or 10, last)
        n = len(self._flow)
        if n == 0:
            return
        x_step = 1 if n <= 10 else (5 if n <= 30 else 10)
        ticks = [1]
        x = x_step
        while x < n:
            ticks.append(x)
            x += x_step
        if ticks[-1] != n:
            ticks.append(n)
        for t in ticks:
            cx = self._x_for(t, rect)
            p.drawLine(QPointF(cx, rect.bottom()),
                       QPointF(cx, rect.bottom() + 3))
            p.drawText(
                QRectF(cx - 16, rect.bottom() + 4, 32, 14),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                str(t),
            )
        # X label
        p.drawText(
            QRectF(rect.left(), rect.bottom() + 16, rect.width(), 14),
            Qt.AlignmentFlag.AlignHCenter,
            "Point",
        )

    def _draw_legend(self, p: QPainter, rect: QRectF) -> None:
        font = QFont(self.font())
        font.setPointSizeF(9)
        font.setBold(True)
        p.setFont(font)
        y = self.PAD_TOP - 18
        x = rect.left()

        def chip(colour: QColor, label: str, x_pos: float) -> float:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(colour))
            p.drawRect(QRectF(x_pos, y + 4, 14, 10))
            p.setPen(TEXT)
            p.drawText(
                QRectF(x_pos + 18, y, 240, 18),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                label,
            )
            metrics = p.fontMetrics()
            return x_pos + 18 + metrics.horizontalAdvance(label) + 14

        x = chip(TEAM_A_COLOR, self._team_a, x)
        x = chip(TEAM_B_COLOR, self._team_b, x)
        # Break legend (ring)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(BREAK_COLOR, 1.6))
        p.drawEllipse(QPointF(x + 7, y + 9), self.BREAK_R, self.BREAK_R)
        p.setPen(TEXT)
        p.drawText(
            QRectF(x + 18, y, 240, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "Break (serving team scored)",
        )

    def _draw_final_callout(self, p: QPainter, rect: QRectF,
                            max_score: int) -> None:
        if not self._flow:
            return
        last = self._flow[-1]
        font = QFont(self.font())
        font.setPointSizeF(10.5)
        font.setBold(True)
        p.setFont(font)
        # A score
        p.setPen(TEAM_A_COLOR)
        ya = self._y_for(last["score_a"], rect, max_score)
        p.drawText(
            QRectF(rect.right() - 36, ya - 14, 40, 16),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            str(last["score_a"]),
        )
        # B score
        p.setPen(TEAM_B_COLOR)
        yb = self._y_for(last["score_b"], rect, max_score)
        p.drawText(
            QRectF(rect.right() - 36, yb - 2, 40, 16),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            str(last["score_b"]),
        )
