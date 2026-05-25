from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QBoxLayout, QLabel, QPushButton,
    QLineEdit, QSpinBox, QGroupBox, QFormLayout, QComboBox, QSpacerItem,
    QSizePolicy, QScrollArea, QFrame, QCompleter, QCheckBox
)
from PyQt6.QtCore import pyqtSignal, Qt

from app.core.models import GameConfig
from app.core.rotation import all_valid_pairs
from app.db.players_repo import all_names
from app.db.games_repo import list_tournaments
from app.config import DEFAULT_TARGET, DEFAULT_HARD_CAP


# Match-format presets: label -> (target, hard_cap). Hard cap is required at
# runtime (the engine needs one) so every preset specifies it.
MATCH_FORMAT_PRESETS: list[tuple[str, int, int]] = [
    ("Bo1 21(25)", 21, 25),
    ("Bo2 15(17)", 15, 17),
    ("Bo3 15(19)", 15, 19),
    ("Bo3 21(25)", 21, 25),
]

PLAYOFF_ROUNDS = ["Ro32", "Ro16", "Ro8", "Semi", "Final"]


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

        # Tournament Info (collapsible, hidden by default)
        self._build_tournament_section()

        # Match Format — replaces the old Game Settings (target/cap are
        # derived from the selected format)
        self._build_match_format_section()

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

    # ------------------------------------------------------------------
    #  Tournament Info section
    # ------------------------------------------------------------------
    def _build_tournament_section(self):
        # Header button that toggles visibility of the body frame.
        self._tournament_toggle = QPushButton("▶  Tournament Info  (optional)")
        self._tournament_toggle.setCheckable(True)
        self._tournament_toggle.setChecked(False)
        self._tournament_toggle.setStyleSheet(
            "QPushButton {"
            "  background-color: #2d2d4e; color: #a0c4ff;"
            "  border: 1px solid #444466; border-radius: 6px;"
            "  padding: 8px 12px; text-align: left; font-weight: bold;"
            "}"
            "QPushButton:hover { background-color: #3d3d6e; }"
            "QPushButton:checked { background-color: #3d3d6e; }"
        )
        self._tournament_toggle.clicked.connect(self._on_tournament_toggle)
        self._content_layout.addWidget(self._tournament_toggle)

        # Body frame — hidden by default.
        self._tournament_body = QFrame()
        self._tournament_body.setVisible(False)
        self._tournament_body.setStyleSheet(
            "QFrame { background-color: #16162a; border: 1px solid #2a2a55;"
            " border-radius: 6px; }"
        )
        body_layout = QVBoxLayout(self._tournament_body)
        body_layout.setContentsMargins(14, 12, 14, 12)
        body_layout.setSpacing(10)

        # Tournament name (editable combo with autocomplete from past entries)
        body_layout.addWidget(QLabel("Tournament"))
        self._tournament_combo = QComboBox()
        self._tournament_combo.setEditable(True)
        self._tournament_combo.setMinimumHeight(34)
        self._tournament_combo.lineEdit().setPlaceholderText("e.g. Spring Open 2026")
        self._tournament_combo.addItem("")
        for name in list_tournaments():
            self._tournament_combo.addItem(name)
        self._tournament_combo.setCurrentIndex(0)
        comp = QCompleter([self._tournament_combo.itemText(i)
                           for i in range(self._tournament_combo.count())])
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        comp.setFilterMode(Qt.MatchFlag.MatchContains)
        self._tournament_combo.setCompleter(comp)
        body_layout.addWidget(self._tournament_combo)

        # Tournament stage toggle
        body_layout.addWidget(QLabel("Tournament stage"))
        self._stage_buttons: dict[str, QPushButton] = {}
        stage_row = QHBoxLayout()
        stage_row.setSpacing(6)
        for label in ("Groups", "Playoffs"):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setMinimumHeight(32)
            btn.clicked.connect(lambda _checked, key=label: self._on_stage_clicked(key))
            self._stage_buttons[label] = btn
            stage_row.addWidget(btn)
        stage_row.addStretch(1)
        body_layout.addLayout(stage_row)

        # Stage part container — swapped based on stage
        self._stage_part_container = QFrame()
        self._stage_part_container.setVisible(False)
        spc = QVBoxLayout(self._stage_part_container)
        spc.setContentsMargins(0, 4, 0, 0)
        spc.setSpacing(6)

        # Groups part: spinbox
        self._groups_frame = QFrame()
        gf_lay = QHBoxLayout(self._groups_frame)
        gf_lay.setContentsMargins(0, 0, 0, 0)
        gf_lay.addWidget(QLabel("Group #:"))
        self._group_spin = QSpinBox()
        self._group_spin.setRange(1, 99)
        self._group_spin.setMinimumHeight(30)
        gf_lay.addWidget(self._group_spin)
        gf_lay.addStretch(1)
        spc.addWidget(self._groups_frame)

        # Playoffs part: bracket toggle (above) + round toggle (below)
        self._playoffs_frame = QFrame()
        pf_lay = QVBoxLayout(self._playoffs_frame)
        pf_lay.setContentsMargins(0, 0, 0, 0)
        pf_lay.setSpacing(6)

        pf_lay.addWidget(QLabel("Bracket"))
        self._bracket_buttons: dict[str, QPushButton] = {}
        bracket_row = QHBoxLayout()
        bracket_row.setSpacing(6)
        for label in ("Upper", "Consolation"):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setMinimumHeight(30)
            btn.clicked.connect(lambda _checked, key=label: self._on_bracket_clicked(key))
            self._bracket_buttons[label] = btn
            bracket_row.addWidget(btn)
        bracket_row.addStretch(1)
        pf_lay.addLayout(bracket_row)

        pf_lay.addWidget(QLabel("Round"))
        self._round_buttons: dict[str, QPushButton] = {}
        round_row = QHBoxLayout()
        round_row.setSpacing(6)
        for label in PLAYOFF_ROUNDS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setMinimumHeight(30)
            btn.clicked.connect(lambda _checked, key=label: self._on_round_clicked(key))
            self._round_buttons[label] = btn
            round_row.addWidget(btn)
        round_row.addStretch(1)
        pf_lay.addLayout(round_row)

        spc.addWidget(self._playoffs_frame)
        self._groups_frame.setVisible(False)
        self._playoffs_frame.setVisible(False)
        body_layout.addWidget(self._stage_part_container)

        # Division — two toggle rows (gender division + skill tier)
        body_layout.addWidget(QLabel("Division"))
        self._division_buttons: dict[str, QPushButton] = {}
        div_row = QHBoxLayout()
        div_row.setSpacing(6)
        for label in ("Open", "Women", "Mix"):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setMinimumHeight(30)
            btn.clicked.connect(lambda _checked, key=label: self._on_division_clicked(key))
            self._division_buttons[label] = btn
            div_row.addWidget(btn)
        div_row.addStretch(1)
        body_layout.addLayout(div_row)

        self._tier_buttons: dict[str, QPushButton] = {}
        tier_row = QHBoxLayout()
        tier_row.setSpacing(6)
        for label in ("Pro", "Contender", "Other"):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setMinimumHeight(30)
            btn.clicked.connect(lambda _checked, key=label: self._on_tier_clicked(key))
            self._tier_buttons[label] = btn
            tier_row.addWidget(btn)
        tier_row.addStretch(1)
        body_layout.addLayout(tier_row)

        # Team count — optional integer, autofilled from past (tournament,
        # division) combinations. 0 = "not set".
        tc_row = QHBoxLayout()
        tc_row.addWidget(QLabel("Team count:"))
        self._team_count_spin = QSpinBox()
        self._team_count_spin.setRange(0, 999)
        self._team_count_spin.setValue(0)
        self._team_count_spin.setSpecialValueText("(not set)")
        self._team_count_spin.setMinimumHeight(30)
        tc_row.addWidget(self._team_count_spin)
        tc_row.addStretch(1)
        body_layout.addLayout(tc_row)

        # Trigger autofill whenever the (tournament, division) pair changes.
        # editTextChanged covers the editable combo's free-text entry.
        self._tournament_combo.editTextChanged.connect(self._maybe_autofill_team_count)
        self._tournament_combo.currentIndexChanged.connect(
            lambda _i: self._maybe_autofill_team_count()
        )

        self._content_layout.addWidget(self._tournament_body)

    def _wrap_layout(self, lay) -> QWidget:
        w = QWidget()
        w.setLayout(lay)
        return w

    def _on_tournament_toggle(self):
        expanded = self._tournament_toggle.isChecked()
        self._tournament_toggle.setText(
            ("▼  " if expanded else "▶  ") + "Tournament Info  (optional)"
        )
        self._tournament_body.setVisible(expanded)

    def _on_stage_clicked(self, key: str):
        # Allow deselecting by clicking the active button again.
        for k, btn in self._stage_buttons.items():
            btn.setChecked(k == key and btn.isChecked())
        active = self._current_stage()
        self._stage_part_container.setVisible(active is not None)
        self._groups_frame.setVisible(active == "Groups")
        self._playoffs_frame.setVisible(active == "Playoffs")

    def _on_bracket_clicked(self, key: str):
        for k, btn in self._bracket_buttons.items():
            btn.setChecked(k == key and btn.isChecked())

    def _on_round_clicked(self, key: str):
        for k, btn in self._round_buttons.items():
            btn.setChecked(k == key and btn.isChecked())

    def _current_stage(self) -> str | None:
        for k, btn in self._stage_buttons.items():
            if btn.isChecked():
                return k
        return None

    def _current_bracket(self) -> str | None:
        for k, btn in self._bracket_buttons.items():
            if btn.isChecked():
                return k
        return None

    def _current_round(self) -> str | None:
        for k, btn in self._round_buttons.items():
            if btn.isChecked():
                return k
        return None

    # ------------------------------------------------------------------
    #  Match Format section
    # ------------------------------------------------------------------
    def _build_match_format_section(self):
        group = QGroupBox("Match Format")
        layout = QVBoxLayout()
        layout.setSpacing(8)
        group.setLayout(layout)

        # Toggle row — exactly one selection at all times (autoExclusive),
        # since target/hard_cap must always resolve to a value.
        self._format_buttons: dict[str, QPushButton] = {}
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(6)
        labels = [label for label, _t, _c in MATCH_FORMAT_PRESETS] + ["Other"]
        for label in labels:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setMinimumHeight(34)
            btn.clicked.connect(lambda _checked, key=label: self._on_format_clicked(key))
            self._format_buttons[label] = btn
            toggle_row.addWidget(btn)
        layout.addLayout(toggle_row)

        # "Other" expansion frame — sets / points / optional hard cap.
        self._format_other_frame = QFrame()
        self._format_other_frame.setVisible(False)
        of_form = QFormLayout(self._format_other_frame)
        of_form.setContentsMargins(0, 4, 0, 0)

        self._other_sets_spin = QSpinBox()
        self._other_sets_spin.setRange(1, 15)
        self._other_sets_spin.setValue(3)
        of_form.addRow("Number of sets (Bo…):", self._other_sets_spin)

        self._other_points_spin = QSpinBox()
        self._other_points_spin.setRange(1, 100)
        self._other_points_spin.setValue(DEFAULT_TARGET)
        of_form.addRow("Points to win:", self._other_points_spin)

        cap_row = QHBoxLayout()
        self._other_use_cap = QCheckBox("Hard cap")
        self._other_use_cap.setChecked(True)
        self._other_cap_spin = QSpinBox()
        self._other_cap_spin.setRange(1, 200)
        self._other_cap_spin.setValue(DEFAULT_HARD_CAP)
        self._other_use_cap.toggled.connect(self._other_cap_spin.setEnabled)
        cap_row.addWidget(self._other_use_cap)
        cap_row.addWidget(self._other_cap_spin)
        cap_row.addStretch(1)
        of_form.addRow("", self._wrap_layout(cap_row))

        layout.addWidget(self._format_other_frame)
        self._content_layout.addWidget(group)

        # Default selection: Bo3 21(25)
        self._format_buttons["Bo3 21(25)"].setChecked(True)

    def _on_format_clicked(self, key: str):
        # autoExclusive guarantees the clicked button stays checked.
        self._format_other_frame.setVisible(key == "Other")

    def _current_format_key(self) -> str:
        for k, btn in self._format_buttons.items():
            if btn.isChecked():
                return k
        return "Bo3 21(25)"

    def _resolved_match_format(self) -> str:
        """The match_format string to persist."""
        key = self._current_format_key()
        if key != "Other":
            return key
        sets = self._other_sets_spin.value()
        points = self._other_points_spin.value()
        if self._other_use_cap.isChecked():
            return f"Bo{sets} {points}({self._other_cap_spin.value()})"
        return f"Bo{sets} {points}"

    def _resolved_target_and_cap(self) -> tuple[int, int]:
        """Derive (target_score, hard_cap) from the current match-format choice."""
        key = self._current_format_key()
        if key != "Other":
            for label, target, cap in MATCH_FORMAT_PRESETS:
                if label == key:
                    return target, cap
        points = self._other_points_spin.value()
        if self._other_use_cap.isChecked():
            return points, self._other_cap_spin.value()
        # No explicit cap — fall back to target + 4 so the engine still has one.
        return points, points + 4

    # ------------------------------------------------------------------
    #  Division / Team count
    # ------------------------------------------------------------------
    def _on_division_clicked(self, key: str):
        for k, btn in self._division_buttons.items():
            btn.setChecked(k == key and btn.isChecked())
        self._maybe_autofill_team_count()

    def _on_tier_clicked(self, key: str):
        for k, btn in self._tier_buttons.items():
            btn.setChecked(k == key and btn.isChecked())

    def _current_division(self) -> str | None:
        for k, btn in self._division_buttons.items():
            if btn.isChecked():
                return k
        return None

    def _current_tier(self) -> str | None:
        for k, btn in self._tier_buttons.items():
            if btn.isChecked():
                return k
        return None

    def _maybe_autofill_team_count(self):
        """Look up prior (tournament, division) and populate team_count if the
        spin still shows the 'not set' sentinel (0). Doesn't overwrite a user
        edit."""
        if self._team_count_spin.value() != 0:
            return
        tournament = self._tournament_combo.currentText().strip()
        if not tournament:
            return
        from app.db.games_repo import find_team_count
        prior = find_team_count(tournament, self._current_division())
        if prior is not None and prior > 0:
            blocker = self._team_count_spin.blockSignals(True)
            self._team_count_spin.setValue(prior)
            self._team_count_spin.blockSignals(blocker)

    def _resolved_stage_part(self) -> str | None:
        stage = self._current_stage()
        if stage == "Groups":
            return str(self._group_spin.value())
        if stage == "Playoffs":
            return self._current_round()
        return None

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
        if (self._current_format_key() == "Other"
                and self._other_use_cap.isChecked()
                and self._other_cap_spin.value() <= self._other_points_spin.value()):
            return "Hard cap must be greater than points to win."
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
        stage = self._current_stage()
        target, cap = self._resolved_target_and_cap()
        team_count = self._team_count_spin.value() or None
        config = GameConfig(
            target_score=target,
            hard_cap=cap,
            tournament=(self._tournament_combo.currentText().strip() or None),
            tournament_stage=stage,
            stage_part=self._resolved_stage_part(),
            bracket_type=(self._current_bracket() if stage == "Playoffs" else None),
            match_format=self._resolved_match_format(),
            division=self._current_division(),
            division_tier=self._current_tier(),
            team_count=team_count,
        )
        server_slot, receiver_slot = self._first_serve_combo.currentData()
        self.game_started.emit(config, names, server_slot, receiver_slot)
