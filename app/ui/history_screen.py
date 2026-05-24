import os
from datetime import datetime
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QFrame, QSpacerItem, QSizePolicy, QMessageBox
)
from PyQt6.QtCore import pyqtSignal, Qt

from app.db import games_repo
from app.config import EXPORTS_DIR


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso


class HistoryScreen(QWidget):
    back_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._roundx_cache: dict[int, dict] = {}
        self._build_ui()
        self._load_games()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # Header
        hdr = QHBoxLayout()
        btn_back = QPushButton("← Back")
        btn_back.setFixedWidth(100)
        btn_back.clicked.connect(self.back_requested)
        hdr.addWidget(btn_back)
        title = QLabel("Game History")
        title.setObjectName("title_label")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr.addWidget(title, stretch=1)
        hdr.addSpacing(100)
        layout.addLayout(hdr)

        # Main split: list + detail
        split = QHBoxLayout()
        split.setSpacing(16)

        # Left: game list
        self._list = QListWidget()
        self._list.setMinimumWidth(280)
        self._list.currentRowChanged.connect(self._on_select)
        split.addWidget(self._list)

        # Right: detail panel
        self._detail = QFrame()
        self._detail.setStyleSheet(
            "QFrame { background-color: #16162a; border-radius: 10px; }"
        )
        detail_layout = QVBoxLayout(self._detail)
        detail_layout.setContentsMargins(20, 20, 20, 20)
        detail_layout.setSpacing(10)

        self._detail_title = QLabel("Select a game")
        self._detail_title.setObjectName("team_label")
        detail_layout.addWidget(self._detail_title)

        self._detail_body = QLabel("")
        self._detail_body.setWordWrap(True)
        self._detail_body.setAlignment(Qt.AlignmentFlag.AlignTop)
        detail_layout.addWidget(self._detail_body)

        detail_layout.addSpacerItem(QSpacerItem(
            10, 10, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        ))

        self._btn_export = QPushButton("📥  Export CSV")
        self._btn_export.setObjectName("btn_primary")
        self._btn_export.setEnabled(False)
        self._btn_export.clicked.connect(self._export_selected)
        detail_layout.addWidget(self._btn_export)

        split.addWidget(self._detail, stretch=1)
        layout.addLayout(split)

        self._games: list[dict] = []
        self._selected_game_id: int | None = None

    def _load_games(self):
        self._games = games_repo.list_games()
        self._list.clear()
        if not self._games:
            self._list.addItem("No games recorded yet.")
            return
        for game in self._games:
            a_score = game["final_score_a"]
            b_score = game["final_score_b"]
            date = _fmt_date(game["created_at"])
            winner = game.get("winner_team")
            win_txt = f"  🏆 Team {winner}" if winner else ""
            label = f"{date}  |  {a_score} – {b_score}{win_txt}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, game["id"])
            self._list.addItem(item)

    def _on_select(self, row: int):
        if row < 0 or row >= len(self._games):
            self._detail_title.setText("Select a game")
            self._detail_body.setText("")
            self._btn_export.setEnabled(False)
            self._selected_game_id = None
            return

        game = self._games[row]
        self._selected_game_id = game["id"]
        self._btn_export.setEnabled(True)

        winner = game.get("winner_team")
        win_txt = f"Team {winner}" if winner else "No winner (abandoned)"
        reason = game.get("end_reason") or "—"
        dur = ""
        if game.get("created_at") and game.get("ended_at"):
            try:
                start = datetime.fromisoformat(game["created_at"])
                end = datetime.fromisoformat(game["ended_at"])
                secs = int((end - start).total_seconds())
                dur = f"{secs // 60}m {secs % 60}s"
            except Exception:
                dur = "—"

        body = (
            f"<b>Date:</b> {_fmt_date(game['created_at'])}<br>"
            f"<b>Duration:</b> {dur or '—'}<br><br>"
            f"<b>Team A:</b> {game['a1_name']} &amp; {game['a2_name']}<br>"
            f"<b>Team B:</b> {game['b1_name']} &amp; {game['b2_name']}<br><br>"
            f"<b>Final score:</b> {game['final_score_a']} – {game['final_score_b']}<br>"
            f"<b>Winner:</b> {win_txt}<br>"
            f"<b>End reason:</b> {reason}<br><br>"
            f"<b>Target:</b> {game['target_score']}  |  "
            f"<b>Hard cap:</b> {game['hard_cap']}"
        )

        roundx_line = self._roundx_line(game["id"])
        if roundx_line:
            body += f"<br><br>{roundx_line}"

        self._detail_title.setText(
            f"Game #{game['id']}  —  "
            f"{game['final_score_a']} : {game['final_score_b']}"
        )
        self._detail_body.setText(body)
        self._detail_body.setTextFormat(Qt.TextFormat.RichText)

    def _roundx_line(self, game_id: int) -> str:
        """Return the RoundX summary line for a game (cached). Empty string
        if the computation failed — never let RoundX break history loads."""
        if game_id in self._roundx_cache:
            roundx = self._roundx_cache[game_id]
        else:
            from app.core.analysis import compute_roundx_scores
            try:
                roundx = compute_roundx_scores(game_id)
            except Exception:
                roundx = None
            self._roundx_cache[game_id] = roundx
        if not roundx:
            return ""

        parts = []
        for slot in ("A1", "A2", "B1", "B2"):
            d = roundx.get(slot) or {}
            score = d.get("score", 0)
            rel = d.get("relative_to_avg", 0)
            sign = "+" if score >= 0 else ""
            rel_sign = "+" if rel >= 0 else ""
            colour = "#7adb7a" if rel > 0 else ("#ff7070" if rel < 0 else "#ffd6a5")
            parts.append(
                f"<b>{slot}</b> "
                f"<span style='color:{colour}'>{sign}{score}</span> "
                f"<span style='color:#8888aa'>({rel_sign}{rel})</span>"
            )
        sample = next(iter(roundx.values()), {})
        rated_suffix = ""
        if not sample.get("rated", True):
            rated_suffix = (
                f"  <span style='color:#8888aa'>"
                f"(unrated · {sample.get('counted_rallies', 0)} rallies)</span>"
            )
        return f"<b>RoundX:</b>  " + ",  ".join(parts) + rated_suffix

    def _export_selected(self):
        if not self._selected_game_id:
            return
        from app.core.csv_export import export_game
        try:
            path = export_game(self._selected_game_id)
            msg = QMessageBox(self)
            msg.setWindowTitle("CSV Exported")
            msg.setText(f"File saved:\n{path}")
            open_btn = msg.addButton("Open folder", QMessageBox.ButtonRole.ActionRole)
            msg.addButton("OK", QMessageBox.ButtonRole.AcceptRole)
            msg.exec()
            if msg.clickedButton() == open_btn:
                try:
                    os.startfile(str(EXPORTS_DIR))
                except Exception:
                    pass
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))
