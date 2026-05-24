from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GameConfig:
    target_score: int
    hard_cap: int
    win_by: int = 2


@dataclass
class GameState:
    game_id: int
    config: GameConfig
    players: dict  # {"A1": "Anna", "A2": "Bob", "B1": "Carla", "B2": "Dan"}

    score_a: int = 0
    score_b: int = 0
    point_number: int = 1

    # Serve rotation
    rotation_index: int = 0         # 0..7, advances after each point
    current_server: str = ""        # slot e.g. "A1"
    current_receiver: str = ""      # slot e.g. "B1"

    # Rally state
    possession_team: Optional[str] = None   # "A" or "B"
    last_touch_team: Optional[str] = None
    last_touch_player: Optional[str] = None
    fault_count: int = 0            # 0 = clean; 1 = one fault this serve
    point_in_progress: bool = False # True once serve phase starts

    # Overtime
    in_overtime: bool = False
    overtime_serve_counter: int = 0
    overtime_first_server_team: Optional[str] = None
    overtime_servers: dict = field(default_factory=dict)    # team -> slot
    overtime_receivers: dict = field(default_factory=dict)  # team -> slot (the receiver for opposing team)

    # Game lifecycle
    ended: bool = False
    end_reason: Optional[str] = None
    winner_team: Optional[str] = None

    # Segment: "serve" | "play" | "ended"
    @property
    def segment(self) -> str:
        if self.ended:
            return "ended"
        if not self.point_in_progress:
            return "serve"
        return "play"
