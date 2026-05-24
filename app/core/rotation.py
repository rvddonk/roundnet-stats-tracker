from typing import NamedTuple


class ServePair(NamedTuple):
    server: str    # slot e.g. "A1"
    receiver: str  # slot e.g. "B1"


# The 8-point serve rotation cycle.
# Each player serves exactly twice per cycle: once to each opponent.
SERVE_ROTATION: tuple[ServePair, ...] = (
    ServePair("A1", "B1"),  # 0
    ServePair("B2", "A2"),  # 1
    ServePair("B2", "A1"),  # 2
    ServePair("A2", "B1"),  # 3
    ServePair("A2", "B2"),  # 4
    ServePair("B1", "A1"),  # 5
    ServePair("B1", "A2"),  # 6
    ServePair("A1", "B2"),  # 7
)


def team_of(slot: str) -> str:
    """Return 'A' or 'B' from a slot like 'A1'."""
    return slot[0]


def partner_slot(slot: str) -> str:
    """Return the partner's slot (A1 <-> A2, B1 <-> B2)."""
    team, num = slot[0], slot[1]
    return f"{team}{'2' if num == '1' else '1'}"


def rotation_index_for(server: str, receiver: str) -> int:
    """Find the rotation index matching a given server+receiver pair.
    Returns -1 if no match (should not happen if UI limits choices correctly)."""
    for i, pair in enumerate(SERVE_ROTATION):
        if pair.server == server and pair.receiver == receiver:
            return i
    return -1


def next_rotation_index(current: int) -> int:
    return (current + 1) % len(SERVE_ROTATION)


def pair_at(index: int) -> ServePair:
    return SERVE_ROTATION[index % len(SERVE_ROTATION)]


def all_valid_pairs() -> list[ServePair]:
    """Return all 8 valid serve pairs (for populating the first-serve picker)."""
    return list(SERVE_ROTATION)
