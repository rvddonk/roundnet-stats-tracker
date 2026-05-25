import re

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QSpinBox, QGroupBox, QFormLayout, QComboBox,
    QSpacerItem, QSizePolicy, QScrollArea, QFrame, QCompleter,
    QCheckBox, QMessageBox,
)
from PyQt6.QtCore import pyqtSignal, Qt

from app.db import games_repo
from app.db.players_repo import all_names
from app.config import DEFAULT_TARGET, DEFAULT_HARD_CAP
from app.ui.setup_screen import MATCH_FORMAT_PRESETS, PLAYOFF_ROUNDS


class EditGameScreen(QWidget):
    back_requested = pyqtSignal()
    saved = pyqtSignal()

    def __init__(self, game_id: int, parent=None):
        super().__init__(parent)
        self._game_id = game_id
        self._game = games_repo.get_game(game_id)
        if self._game is None:
            raise ValueError(f"Game #{game_id} not found")
        self._player_inputs: dict[str, QLineEdit] = {}
        self._build_ui()
        self._populate_from_game()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        container = QWidget()
        scroll.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(18)

        # Header
        hdr = QHBoxLayout()
        btn_back = QPushButton("← Cancel")
        btn_back.setFixedWidth(110)
        btn_back.clicked.connect(self.back_requested)
        hdr.addWidget(btn_back)
        title = QLabel(f"Edit Game #{self._game_id}")
        title.setObjectName("title_label")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr.addWidget(title, stretch=1)
        hdr.addSpacerItem(QSpacerItem(
            110, 1, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum
        ))
        layout.addLayout(hdr)

        # Players
        players_group = QGroupBox("Players")
        players_lay = QHBoxLayout()
        players_lay.setSpacing(20)
        players_group.setLayout(players_lay)

        players_lay.addWidget(self._make_team_box("Team A", "A1", "A2"))
        players_lay.addWidget(self._make_team_box("Team B", "B1", "B2"))
        layout.addWidget(players_group)

        # Tournament info
        tourn_group = QGroupBox("Tournament Info")
        tourn_lay = QVBoxLayout()
        tourn_lay.setSpacing(10)
        tourn_group.setLayout(tourn_lay)

        tourn_lay.addWidget(QLabel("Tournament"))
        self._tournament_combo = QComboBox()
        self._tournament_combo.setEditable(True)
        self._tournament_combo.setMinimumHeight(34)
        self._tournament_combo.lineEdit().setPlaceholderText("e.g. Spring Open 2026")
        self._tournament_combo.addItem("")
        for name in games_repo.list_tournaments():
            self._tournament_combo.addItem(name)
        comp = QCompleter([self._tournament_combo.itemText(i)
                           for i in range(self._tournament_combo.count())])
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        comp.setFilterMode(Qt.MatchFlag.MatchContains)
        self._tournament_combo.setCompleter(comp)
        tourn_lay.addWidget(self._tournament_combo)

        # Match format — toggle row of presets + "Other" expansion. Mirrors
        # the setup screen so the two flows look identical.
        tourn_lay.addWidget(QLabel("Match format"))
        self._format_buttons: dict[str, QPushButton] = {}
        fmt_row = QHBoxLayout()
        fmt_row.setSpacing(6)
        fmt_labels = [label for label, _t, _c in MATCH_FORMAT_PRESETS] + ["Other"]
        for label in fmt_labels:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setMinimumHeight(34)
            btn.clicked.connect(lambda _checked, key=label: self._on_format_clicked(key))
            self._format_buttons[label] = btn
            fmt_row.addWidget(btn)
        tourn_lay.addLayout(fmt_row)

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
        cap_holder = QWidget()
        cap_holder.setLayout(cap_row)
        of_form.addRow("", cap_holder)
        tourn_lay.addWidget(self._format_other_frame)

        # Stage
        tourn_lay.addWidget(QLabel("Tournament stage"))
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
        tourn_lay.addLayout(stage_row)

        # Stage part container
        self._stage_part_container = QFrame()
        self._stage_part_container.setVisible(False)
        spc = QVBoxLayout(self._stage_part_container)
        spc.setContentsMargins(0, 4, 0, 0)
        spc.setSpacing(6)

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
        tourn_lay.addWidget(self._stage_part_container)

        # Division
        tourn_lay.addWidget(QLabel("Division"))
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
        tourn_lay.addLayout(div_row)

        # Tier
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
        tourn_lay.addLayout(tier_row)

        # Team count
        tc_row = QHBoxLayout()
        tc_row.addWidget(QLabel("Team count:"))
        self._team_count_spin = QSpinBox()
        self._team_count_spin.setRange(0, 999)
        self._team_count_spin.setValue(0)
        self._team_count_spin.setSpecialValueText("(not set)")
        self._team_count_spin.setMinimumHeight(30)
        tc_row.addWidget(self._team_count_spin)
        tc_row.addStretch(1)
        tourn_lay.addLayout(tc_row)

        layout.addWidget(tourn_group)

        # Validation label
        self._val_label = QLabel("")
        self._val_label.setStyleSheet("color: #ff8888; font-size: 13px;")
        self._val_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._val_label.setWordWrap(True)
        layout.addWidget(self._val_label)

        # Save button
        self._btn_save = QPushButton("💾  Save Changes")
        self._btn_save.setObjectName("btn_primary")
        self._btn_save.setMinimumHeight(52)
        self._btn_save.clicked.connect(self._on_save)
        layout.addWidget(self._btn_save)

        layout.addSpacerItem(QSpacerItem(
            20, 20, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        ))

    def _make_team_box(self, team_label: str, slot1: str, slot2: str) -> QGroupBox:
        box = QGroupBox(team_label)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        box.setLayout(form)

        known = all_names()
        for slot, label in [(slot1, "Player 1"), (slot2, "Player 2")]:
            inp = QLineEdit()
            inp.setPlaceholderText("Enter name…")
            comp = QCompleter(known)
            comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            comp.setFilterMode(Qt.MatchFlag.MatchContains)
            inp.setCompleter(comp)
            form.addRow(label + ":", inp)
            self._player_inputs[slot] = inp
        return box

    # ------------------------------------------------------------------
    # Toggle helpers
    # ------------------------------------------------------------------
    def _on_stage_clicked(self, key: str):
        for k, btn in self._stage_buttons.items():
            btn.setChecked(k == key and btn.isChecked())
        active = self._current_toggle(self._stage_buttons)
        self._stage_part_container.setVisible(active is not None)
        self._groups_frame.setVisible(active == "Groups")
        self._playoffs_frame.setVisible(active == "Playoffs")

    def _on_bracket_clicked(self, key: str):
        for k, btn in self._bracket_buttons.items():
            btn.setChecked(k == key and btn.isChecked())

    def _on_round_clicked(self, key: str):
        for k, btn in self._round_buttons.items():
            btn.setChecked(k == key and btn.isChecked())

    def _on_division_clicked(self, key: str):
        for k, btn in self._division_buttons.items():
            btn.setChecked(k == key and btn.isChecked())

    def _on_tier_clicked(self, key: str):
        for k, btn in self._tier_buttons.items():
            btn.setChecked(k == key and btn.isChecked())

    @staticmethod
    def _current_toggle(group: dict[str, QPushButton]) -> str | None:
        for k, btn in group.items():
            if btn.isChecked():
                return k
        return None

    # ------------------------------------------------------------------
    # Match format helpers
    # ------------------------------------------------------------------
    def _on_format_clicked(self, key: str):
        # autoExclusive keeps the clicked button checked; just show/hide the
        # "Other" expansion.
        self._format_other_frame.setVisible(key == "Other")

    def _current_format_key(self) -> str:
        for k, btn in self._format_buttons.items():
            if btn.isChecked():
                return k
        return "Bo3 21(25)"

    def _resolved_match_format(self) -> str:
        key = self._current_format_key()
        if key != "Other":
            return key
        sets = self._other_sets_spin.value()
        points = self._other_points_spin.value()
        if self._other_use_cap.isChecked():
            return f"Bo{sets} {points}({self._other_cap_spin.value()})"
        return f"Bo{sets} {points}"

    @staticmethod
    def _parse_format(s: str) -> tuple[int, int, int | None] | None:
        """'Bo3 21(25)' or 'Bo3 21' → (sets, points, cap_or_None). None if unparseable."""
        m = re.match(r"^\s*Bo(\d+)\s+(\d+)(?:\((\d+)\))?\s*$", s)
        if not m:
            return None
        return int(m.group(1)), int(m.group(2)), (int(m.group(3)) if m.group(3) else None)

    # ------------------------------------------------------------------
    # Populate / collect
    # ------------------------------------------------------------------
    def _populate_from_game(self):
        g = self._game
        self._player_inputs["A1"].setText(g.get("a1_name") or "")
        self._player_inputs["A2"].setText(g.get("a2_name") or "")
        self._player_inputs["B1"].setText(g.get("b1_name") or "")
        self._player_inputs["B2"].setText(g.get("b2_name") or "")

        self._tournament_combo.setCurrentText(g.get("tournament") or "")

        # Match format: check the matching preset, else fall back to "Other"
        # and populate sets/points/cap from the parsed string.
        fmt = (g.get("match_format") or "").strip()
        preset_labels = {label for label, _t, _c in MATCH_FORMAT_PRESETS}
        if fmt in preset_labels:
            self._format_buttons[fmt].setChecked(True)
        else:
            self._format_buttons["Other"].setChecked(True)
            self._format_other_frame.setVisible(True)
            parsed = self._parse_format(fmt) if fmt else None
            if parsed:
                sets, points, cap = parsed
                self._other_sets_spin.setValue(sets)
                self._other_points_spin.setValue(points)
                if cap is None:
                    self._other_use_cap.setChecked(False)
                    self._other_cap_spin.setEnabled(False)
                else:
                    self._other_use_cap.setChecked(True)
                    self._other_cap_spin.setValue(cap)

        stage = g.get("tournament_stage")
        if stage in self._stage_buttons:
            self._stage_buttons[stage].setChecked(True)
            self._on_stage_clicked(stage)
            part = g.get("stage_part")
            if stage == "Groups" and part:
                try:
                    self._group_spin.setValue(int(part))
                except (TypeError, ValueError):
                    pass
            elif stage == "Playoffs":
                bracket = g.get("bracket_type")
                if bracket in self._bracket_buttons:
                    self._bracket_buttons[bracket].setChecked(True)
                if part in self._round_buttons:
                    self._round_buttons[part].setChecked(True)

        division = g.get("division")
        if division in self._division_buttons:
            self._division_buttons[division].setChecked(True)
        tier = g.get("division_tier")
        if tier in self._tier_buttons:
            self._tier_buttons[tier].setChecked(True)

        tc = g.get("team_count")
        if tc:
            self._team_count_spin.setValue(int(tc))

    def _collect_stage_part(self, stage: str | None) -> str | None:
        if stage == "Groups":
            return str(self._group_spin.value())
        if stage == "Playoffs":
            return self._current_toggle(self._round_buttons)
        return None

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------
    def _validate(self) -> str | None:
        names = {slot: inp.text().strip() for slot, inp in self._player_inputs.items()}
        for slot, name in names.items():
            if not name:
                return f"Please enter a name for {slot}."
        if len(set(names.values())) < 4:
            return "All four player names must be different."
        if (self._current_format_key() == "Other"
                and self._other_use_cap.isChecked()
                and self._other_cap_spin.value() <= self._other_points_spin.value()):
            return "Hard cap must be greater than points to win."
        return None

    def _on_save(self):
        error = self._validate()
        if error:
            self._val_label.setText(error)
            return
        self._val_label.setText("")

        names = {slot: inp.text().strip() for slot, inp in self._player_inputs.items()}
        stage = self._current_toggle(self._stage_buttons)
        bracket = self._current_toggle(self._bracket_buttons) if stage == "Playoffs" else None
        tc = self._team_count_spin.value() or None

        try:
            games_repo.update_metadata(
                self._game_id,
                a1_name=names["A1"], a2_name=names["A2"],
                b1_name=names["B1"], b2_name=names["B2"],
                tournament=(self._tournament_combo.currentText().strip() or None),
                tournament_stage=stage,
                stage_part=self._collect_stage_part(stage),
                bracket_type=bracket,
                match_format=self._resolved_match_format(),
                division=self._current_toggle(self._division_buttons),
                division_tier=self._current_toggle(self._tier_buttons),
                team_count=tc,
            )
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return

        self.saved.emit()
