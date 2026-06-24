"""Shared definitions for core-stat tooltips/help text."""

CORE_STAT_DEFINITIONS = {
    "Aces / Aced": (
        "Aces: serves that win the point immediately, without allowing the setter to make a viable set. "
    ),
    "Breaks/Broken": (
        "Breaks: points won by the serving team. "
        "Broken: points lost while serving."
    ),
    "Holds": (
        "Receiving team won the point after a receive happened. "
        "Shown as holds / total_receives (excluding aces)."
    ),
    "Side-outs": (
        "Holds where the serving team never got a hit in that rally. "
        "Shown as side_outs / total_receives (excluding aces)."
    ),
    "Clean Side-outs": (
        "Side-outs where the serving team never touched the ball at all. "
        "Shown as clean_side_outs / total_receives (excluding aces)."
    ),
    "Errors": (
        "Total attributed hit/set errors. "
        "Receive/touch-sourced errors are excluded by design."
    ),
    "Hit Errors": "(hit_errors + weak_hit_errors) / hits.",
    "Set Errors": "(set_errors + weak_set_errors) / sets.",
    "Double Faults": "double_faults_total / points_served.",
    "First Fault": "single_faults_total / points_served.",
    "Touches": "total_touches / opponent_hits.",
    "Weak Touches": "weak_touches / total_touches.",
    "Weak Receives": "weak_receives / total_receives (excluding aces).",
    "Weak Sets": "weak_sets / sets.",
    "Weak Hits": "weak_hits / hits.",
}
