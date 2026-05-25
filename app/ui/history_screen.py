import os

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QMessageBox,
)
from PyQt6.QtCore import pyqtSignal, Qt

from app.db import games_repo
from app.config import EXPORTS_DIR
from app.ui.widgets.game_card import GameCard, GameCardGrid


class HistoryScreen(QWidget):
    back_requested = pyqtSignal()
    edit_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._roundx_cache: dict[int, dict] = {}
        self._build_ui()
        self._load_games()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

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

        self._grid = GameCardGrid()
        layout.addWidget(self._grid, stretch=1)

    # ------------------------------------------------------------------
    # Data load / refresh
    # ------------------------------------------------------------------
    def _load_games(self):
        games = games_repo.list_games()
        if not games:
            self._grid.show_empty("No games recorded yet.")
            return

        cards = [
            GameCard(
                game,
                roundx=self._compute_roundx(game["id"]),
                on_edit=self.edit_requested.emit,
                on_export=self._export,
                on_delete=self._delete,
            )
            for game in games
        ]
        self._grid.set_cards(cards)

    def _compute_roundx(self, game_id: int) -> dict | None:
        if game_id in self._roundx_cache:
            return self._roundx_cache[game_id]
        from app.core.analysis import compute_roundx_scores
        try:
            roundx = compute_roundx_scores(game_id)
        except Exception:
            roundx = None
        self._roundx_cache[game_id] = roundx
        return roundx

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _delete(self, game_id: int) -> None:
        confirm = QMessageBox(self)
        confirm.setWindowTitle("Delete game")
        confirm.setText(
            f"Delete game #{game_id}? This removes all events for the game "
            "and cannot be undone."
        )
        confirm.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        )
        confirm.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if confirm.exec() != QMessageBox.StandardButton.Yes:
            return
        try:
            games_repo.delete_game(game_id)
        except Exception as e:
            QMessageBox.critical(self, "Delete failed", str(e))
            return
        self._roundx_cache.pop(game_id, None)
        self._load_games()

    def _export(self, game_id: int) -> None:
        from app.core.csv_export import export_game
        try:
            path = export_game(game_id)
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
