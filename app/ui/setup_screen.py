from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QBoxLayout, QLabel, QPushButton,
    QLineEdit, QSpinBox, QGroupBox, QFormLayout, QComboBox, QSpacerItem,
    QSizePolicy, QScrollArea, QFrame, QCompleter
)
from PyQt6.QtCore import pyqtSignal, Qt

from app.core.models import GameConfig
from app.core.rotation import all_valid_pairs
from app.db.players_repo import all_names
from app.config import DEFAULT_TARGET, DEFAULT_HARD_CAP


# Width thresholds for responsive layout (px, measured on the screen widget)
_STACK_TEAMS_BELOW = 640
_COMPACT_BELOW = 520


class SetupScreen(QWidget):
    back_requested = pyqtSignal()
    # Emits (GameConfig, players_dict, first_server_slot, first_receiver_slot)
    game_started = pyqtSignal(object, dict, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._player_inputs: dict[str, QLineEdit] = {}
        self._last_layout_mode: str | None = None
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # Scroll area so it works on small screens
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        container = QWidget()
        scroll.setWidget(container)
        self._content_layout = QVBoxLayout(container)
        self._content_layout.setContentsMargins(50, 40, 50, 40)
        self._content_layout.setSpacing(20)

        # Header
        hdr = QHBoxLayout()
        self._btn_back = QPushButton("← Back")
        self._btn_back.setFixedWidth(100)
        self._btn_back.clicked.connect(self.back_requested)
        hdr.addWidget(self._btn_back)
        self._title = QLabel("New Game Setup")
        self._title.setObjectName("title_label")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr.addWidget(self._title, stretch=1)
        self._hdr_balance = QSpacerItem(
            100, 1, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum
        )
        hdr.addSpacerItem(self._hdr_balance)
        self._content_layout.addLayout(hdr)

        # Players — QBoxLayout so direction can flip at narrow widths
        players_group = QGroupBox("Players")
        self._players_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._players_layout.setSpacing(20)
        players_group.setLayout(self._players_layout)

        self._team_a_group = self._make_team_box("Team A", "A1", "A2")
        self._team_b_group = self._make_team_box("Team B", "B1", "B2")
        self._players_layout.addWidget(self._team_a_group)
        self._players_layout.addWidget(self._team_b_group)
        self._content_layout.addWidget(players_group)

        # Game settings
        settings_group = QGroupBox("Game Settings")
        sform = QFormLayout()
        sform.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        settings_group.setLayout(sform)

        self._target_spin = QSpinBox()
        self._target_spin.setRange(1, 100)
        self._target_spin.setValue(DEFAULT_TARGET)
        sform.addRow("Play to (target score):", self._target_spin)

        self._cap_spin = QSpinBox()
        self._cap_spin.setRange(1, 200)
        self._cap_spin.setValue(DEFAULT_HARD_CAP)
        sform.addRow("Hard cap (max score):", self._cap_spin)

        self._target_spin.valueChanged.connect(self._sync_cap_min)
        self._content_layout.addWidget(settings_group)

        # First serve picker
        serve_group = QGroupBox("Who serves first?")
        svbox = QVBoxLayout()
        serve_group.setLayout(svbox)

        self._first_serve_combo = QComboBox()
        self._first_serve_combo.setMinimumHeight(40)
        svbox.addWidget(self._first_serve_combo)
        self._content_layout.addWidget(serve_group)

        # Wire name changes → update serve picker labels
        for inp in self._player_inputs.values():
            inp.textChanged.connect(self._refresh_serve_options)
        self._refresh_serve_options()

        # Validation label
        self._val_label = QLabel("")
        self._val_label.setStyleSheet("color: #ff8888; font-size: 13px;")
        self._val_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._val_label.setWordWrap(True)
        self._content_layout.addWidget(self._val_label)

        # Start button
        self._btn_start = QPushButton("▶  Start Game")
        self._btn_start.setObjectName("btn_primary")
        self._btn_start.setMinimumHeight(56)
        self._btn_start.clicked.connect(self._on_start)
        self._content_layout.addWidget(self._btn_start)

        self._content_layout.addSpacerItem(QSpacerItem(
            20, 20, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        ))

        self._apply_responsive_layout(self.width())

    def _make_team_box(self, team_label: str, slot1: str, slot2: str) -> QGroupBox:
        box = QGroupBox(team_label)
        form = QFormLayout()
        # Lets label+field stack vertically when the box is too narrow
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        box.setLayout(form)

        known = all_names()
        for slot, label in [(slot1, "Player 1 (serves first)"),
                            (slot2, "Player 2")]:
            inp = QLineEdit()
            inp.setPlaceholderText("Enter name…")
            inp.setMinimumWidth(0)
            comp = QCompleter(known)
            comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            comp.setFilterMode(Qt.MatchFlag.MatchContains)
            inp.setCompleter(comp)
            form.addRow(label + ":", inp)
            self._player_inputs[slot] = inp

        return box

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_responsive_layout(event.size().width())

    def _apply_responsive_layout(self, width: int):
        if width < _COMPACT_BELOW:
            mode = "compact"
        elif width < _STACK_TEAMS_BELOW:
            mode = "stacked"
        else:
            mode = "wide"
        if mode == self._last_layout_mode:
            return
        self._last_layout_mode = mode

        if mode == "wide":
            margin_h, margin_v = 50, 40
            spacing = 20
            self._players_layout.setDirection(QBoxLayout.Direction.LeftToRight)
            self._players_layout.setSpacing(20)
            self._btn_back.setFixedWidth(100)
            self._hdr_balance.changeSize(
                100, 1, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum
            )
            self._title.setStyleSheet("")
            self._btn_start.setMinimumHeight(56)
        elif mode == "stacked":
            margin_h, margin_v = 24, 22
            spacing = 14
            self._players_layout.setDirection(QBoxLayout.Direction.TopToBottom)
            self._players_layout.setSpacing(12)
            self._btn_back.setFixedWidth(80)
            self._hdr_balance.changeSize(
                80, 1, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum
            )
            self._title.setStyleSheet("font-size: 26px;")
            self._btn_start.setMinimumHeight(52)
        else:  # compact
            margin_h, margin_v = 12, 14
            spacing = 10
            self._players_layout.setDirection(QBoxLayout.Direction.TopToBottom)
            self._players_layout.setSpacing(10)
            self._btn_back.setFixedWidth(64)
            self._hdr_balance.changeSize(
                0, 0, QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum
            )
            self._title.setStyleSheet("font-size: 20px;")
            self._btn_start.setMinimumHeight(48)

        self._content_layout.setContentsMargins(
            margin_h, margin_v, margin_h, margin_v
        )
        self._content_layout.setSpacing(spacing)
        self._content_layout.invalidate()

    def _sync_cap_min(self):
        self._cap_spin.setMinimum(self._target_spin.value() + 1)
        if self._cap_spin.value() <= self._target_spin.value():
            self._cap_spin.setValue(self._target_spin.value() + 4)

    def _get_names(self) -> dict:
        return {slot: inp.text().strip() for slot, inp in self._player_inputs.items()}

    def _refresh_serve_options(self):
        names = self._get_names()
        pairs = all_valid_pairs()
        current_data = self._first_serve_combo.currentData()
        self._first_serve_combo.clear()
        for pair in pairs:
            srv_name = names.get(pair.server) or pair.server
            rcv_name = names.get(pair.receiver) or pair.receiver
            label = f"{srv_name} serves  →  {rcv_name} receives"
            self._first_serve_combo.addItem(label, userData=(pair.server, pair.receiver))
        # Restore previous selection if possible
        if current_data:
            for i in range(self._first_serve_combo.count()):
                if self._first_serve_combo.itemData(i) == current_data:
                    self._first_serve_combo.setCurrentIndex(i)
                    break

    def _validate(self) -> str | None:
        """Returns an error message or None if valid."""
        names = self._get_names()
        for slot, name in names.items():
            if not name:
                return f"Please enter a name for {slot}."
        if len(set(names.values())) < 4:
            return "All four player names must be different."
        if self._cap_spin.value() <= self._target_spin.value():
            return "Hard cap must be greater than target score."
        if self._first_serve_combo.count() == 0:
            return "Please select who serves first."
        return None

    def _on_start(self):
        error = self._validate()
        if error:
            self._val_label.setText(error)
            return
        self._val_label.setText("")

        names = self._get_names()
        config = GameConfig(
            target_score=self._target_spin.value(),
            hard_cap=self._cap_spin.value(),
        )
        server_slot, receiver_slot = self._first_serve_combo.currentData()
        self.game_started.emit(config, names, server_slot, receiver_slot)
