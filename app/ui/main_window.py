from PyQt6.QtWidgets import QMainWindow, QStackedWidget
from PyQt6.QtCore import QSize

from app.config import APP_NAME


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(QSize(360, 420))
        self.resize(1100, 760)

        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        # Lazy-import to avoid circular imports
        self._screens: dict = {}
        self._engine = None

        self._go_home()

    def _go_home(self):
        from app.ui.home_screen import HomeScreen
        if "home" not in self._screens:
            screen = HomeScreen()
            screen.new_game_requested.connect(self._go_setup)
            screen.history_requested.connect(self._go_history)
            screen.analysis_requested.connect(self._go_analysis)
            self._screens["home"] = screen
            self._stack.addWidget(screen)
        self._stack.setCurrentWidget(self._screens["home"])

    def _go_setup(self):
        from app.ui.setup_screen import SetupScreen
        # Always create a fresh setup screen
        if "setup" in self._screens:
            self._stack.removeWidget(self._screens["setup"])
            self._screens["setup"].deleteLater()
        screen = SetupScreen()
        screen.back_requested.connect(self._go_home)
        screen.game_started.connect(self._start_game)
        self._screens["setup"] = screen
        self._stack.addWidget(screen)
        self._stack.setCurrentWidget(screen)

    def _start_game(self, config, players, first_server, first_receiver):
        from app.ui.tracker_screen import TrackerScreen
        from app.core.game_engine import GameEngine
        from app.core.models import GameConfig

        if "tracker" in self._screens:
            self._stack.removeWidget(self._screens["tracker"])
            self._screens["tracker"].deleteLater()

        tracker = TrackerScreen()
        tracker.home_requested.connect(self._go_home)
        self._screens["tracker"] = tracker
        self._stack.addWidget(tracker)

        self._engine = GameEngine(
            on_state_changed=tracker.refresh,
            on_fault_prompt=tracker.show_fault_dialog,
            on_game_ended=tracker.on_game_ended,
            on_overtime_started=tracker.on_overtime_started,
        )
        tracker.set_engine(self._engine)
        self._engine.start_game(config, players, first_server, first_receiver)
        self._stack.setCurrentWidget(tracker)

    def _go_history(self):
        from app.ui.history_screen import HistoryScreen
        if "history" in self._screens:
            self._stack.removeWidget(self._screens["history"])
            self._screens["history"].deleteLater()
        screen = HistoryScreen()
        screen.back_requested.connect(self._go_home)
        screen.edit_requested.connect(self._go_edit_game)
        self._screens["history"] = screen
        self._stack.addWidget(screen)
        self._stack.setCurrentWidget(screen)

    def _go_edit_game(self, game_id: int):
        from app.ui.edit_game_screen import EditGameScreen
        if "edit_game" in self._screens:
            self._stack.removeWidget(self._screens["edit_game"])
            self._screens["edit_game"].deleteLater()
        screen = EditGameScreen(game_id)
        screen.back_requested.connect(self._go_history)
        screen.saved.connect(self._go_history)
        self._screens["edit_game"] = screen
        self._stack.addWidget(screen)
        self._stack.setCurrentWidget(screen)

    def _go_analysis(self):
        from app.ui.analysis_screen import AnalysisScreen
        if "analysis" in self._screens:
            self._stack.removeWidget(self._screens["analysis"])
            self._screens["analysis"].deleteLater()
        screen = AnalysisScreen()
        screen.back_requested.connect(self._go_home)
        self._screens["analysis"] = screen
        self._stack.addWidget(screen)
        self._stack.setCurrentWidget(screen)
