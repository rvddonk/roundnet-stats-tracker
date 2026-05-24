"""
Rally auto-tracking state machine.

Within a team, players alternate touching the ball, EXCEPT on a soft_touch
where the same player repeats.

Example sequence:
  A1 Receive -> A2 Set -> A1 Hit -> B1 Touch -> B1 Soft Touch -> B2 Hit

The alternation rule is implemented by tracking the last player per team.
After a soft_touch, the pointer is NOT updated, so the partner takes over next.
"""

from app.core.rotation import partner_slot, team_of

# Actions that do NOT advance the per-team alternation pointer
_NON_ALTERNATING = {"soft_touch", "weak_soft_touch"}


class RallyTracker:
    def __init__(self):
        # Last player who touched the ball per team (slot or None)
        self.last_player: dict[str, str | None] = {"A": None, "B": None}
        self._pending_receiver: str | None = None

    def reset_for_point(self, server_slot: str, receiver_slot: str):
        """Called at the start of each new point."""
        self.last_player = {"A": None, "B": None}
        srv_team = team_of(server_slot)
        self.last_player[srv_team] = server_slot
        self._pending_receiver = receiver_slot

    def credit_receive(self) -> str:
        """Credit the receive action to the designated receiver."""
        slot = self._pending_receiver
        if slot is None:
            raise RuntimeError("No pending receiver set")
        team = team_of(slot)
        self.last_player[team] = slot
        return slot

    def credit_chosen(self, slot: str) -> str:
        """
        Credit an action to an explicitly-chosen slot (e.g. user picked which
        opposing-team player got the touch). Updates alternation pointer so
        subsequent non-soft actions on that team alternate to the partner.
        """
        team = team_of(slot)
        self.last_player[team] = slot
        return slot

    def credit_action(self, action_kind: str, acting_team: str) -> str:
        """
        Determine which player gets credit for a play action.
        action_kind: e.g. "set", "weak_hit", "soft_touch", etc.
        Returns the slot of the credited player.
        """
        normalized = action_kind.replace("weak_", "")
        last = self.last_player[acting_team]

        if last is None:
            # First touch of this team — default to the "1" player
            credited = f"{acting_team}1"
        elif normalized in _NON_ALTERNATING:
            # Soft touch: same player repeats
            credited = last
        else:
            # Normal alternation
            credited = partner_slot(last)

        # Update pointer only for non-soft-touch actions
        if normalized not in _NON_ALTERNATING:
            self.last_player[acting_team] = credited

        return credited

    def last_for_team(self, team: str) -> str | None:
        return self.last_player.get(team)
