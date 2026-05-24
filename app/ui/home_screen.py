from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSpacerItem,
    QSizePolicy
)
from PyQt6.QtCore import pyqtSignal, Qt


class HomeScreen(QWidget):
    new_game_requested = pyqtSignal()
    history_requested = pyqtSignal()
    analysis_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(60, 60, 60, 60)
        layout.setSpacing(24)

        # Title
        title = QLabel("🏐 Roundnet Stats")
        title.setObjectName("title_label")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("Track your game stats point by point")
        subtitle.setObjectName("subtitle_label")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        layout.addSpacerItem(QSpacerItem(
            20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        ))

        # Buttons
        btn_new = QPushButton("▶  New Game")
        btn_new.setObjectName("btn_home_new")
        btn_new.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_new.clicked.connect(self.new_game_requested)
        layout.addWidget(btn_new)

        btn_history = QPushButton("📋  View Game History")
        btn_history.setObjectName("btn_home_history")
        btn_history.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_history.clicked.connect(self.history_requested)
        layout.addWidget(btn_history)

        btn_analysis = QPushButton("📊  Analysis")
        btn_analysis.setObjectName("btn_home_history")
        btn_analysis.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_analysis.clicked.connect(self.analysis_requested)
        layout.addWidget(btn_analysis)

        layout.addSpacerItem(QSpacerItem(
            20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        ))
