"""
Pure scoring / win-condition logic.
"""

from app.core.models import GameConfig


def check_game_end(
    score_a: int,
    score_b: int,
    config: GameConfig,
) -> str | None:
    """
    Returns the winning team ('A' or 'B') if the game is over, else None.

    Precedence:
    1. Hard cap: if either score >= hard_cap and that team leads by >= 1 → win.
    2. Standard: if either score >= target and lead >= win_by → win.
    """
    # Hard cap check (highest priority)
    if score_a >= config.hard_cap and score_a > score_b:
        return "A"
    if score_b >= config.hard_cap and score_b > score_a:
        return "B"

    # Standard win
    if score_a >= config.target_score and (score_a - score_b) >= config.win_by:
        return "A"
    if score_b >= config.target_score and (score_b - score_a) >= config.win_by:
        return "B"

    return None


def should_trigger_overtime(
    score_a: int,
    score_b: int,
    config: GameConfig,
    already_in_ot: bool,
) -> bool:
    """
    Returns True if overtime should be triggered.
    Overtime triggers when one team has reached target and the other is one behind
    (e.g. 21:20 or 20:21 for target=21).
    """
    if already_in_ot:
        return False
    target = config.target_score
    lead = abs(score_a - score_b)
    max_score = max(score_a, score_b)
    return max_score >= target and lead == 1
