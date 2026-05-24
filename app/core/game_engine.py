"""
GameEngine — orchestrates all game state, actions, undo, and DB persistence.

UI calls the public `on_*` methods and reads `state` to refresh its display.
After each action the engine emits a state_changed signal (via callback).
"""

import copy
from datetime import datetime
from typing import Callable, Optional

from app.core.models import GameConfig, GameState
from app.core.rally_tracker import RallyTracker
from app.core.rotation import (
    pair_at,
    next_rotation_index,
    rotation_index_for,
    team_of,
    partner_slot,
)
from app.core.scoring import check_game_end, should_trigger_overtime
from app.db import events_repo, games_repo
from app.db.players_repo import add_or_bump


class GameEngine:
    """
    Manages a single game session end-to-end.

    Callbacks:
        on_state_changed()      — called after every state mutation
        on_fault_prompt(cb)     — engine calls cb(fault_types) then awaits
                                   set_fault_type(fault_type) before continuing
        on_game_ended(state)    — called when the game is over
        on_overtime_started()   — called when OT triggers
    """

    def __init__(
        self,
        on_state_changed: Callable,
        on_fault_prompt: Callable,
        on_game_ended: Callable,
        on_overtime_started: Callable | None = None,
    ):
        self._on_state_changed = on_state_changed
        self._on_fault_prompt = on_fault_prompt
        self._on_game_ended = on_game_ended
        self._on_overtime_started = on_overtime_started

        self.state: GameState | None = None
        self._tracker = RallyTracker()

        # Undo stacks (per current point).
        # _point_events is the per-point activity log: each entry is
        # {"id", "type", "player", "fault_type"} — used both for undo
        # (the id) and for the UI sequence panel (the rest).
        self._point_start_snapshot: GameState | None = None
        self._point_snapshots: list[GameState] = []
        self._point_events: list[dict] = []
        self._seq_in_point: int = 0

        # Stack of point-start snapshots for points that have since been
        # completed. Used by `delete_last_point` to roll back the most-
        # recently-awarded point. Populated in `_start_new_point` just
        # before the new point's snapshot overwrites the current one.
        self._prev_point_start_snapshots: list[GameState] = []

        # Pending fault type (set async from UI prompt)
        self._awaiting_fault_type: bool = False

        # Whether the point currently in progress started under OT rules.
        # Used at point-end to decide between rotation advance vs. OT
        # serve-counter advance, so that the OT-trigger point itself does
        # not bump the counter prematurely.
        self._point_started_in_ot: bool = False

    # ------------------------------------------------------------------ #
    #  Game Lifecycle                                                       #
    # ------------------------------------------------------------------ #

    def start_game(
        self,
        config: GameConfig,
        players: dict,   # {"A1": name, "A2": name, "B1": name, "B2": name}
        first_server_slot: str,
        first_receiver_slot: str,
    ):
        # Persist player names
        for name in players.values():
            add_or_bump(name)

        rotation_idx = rotation_index_for(first_server_slot, first_receiver_slot)
        if rotation_idx == -1:
            # Fallback: start at 0
            rotation_idx = 0

        game_id = games_repo.create_game(
            target_score=config.target_score,
            hard_cap=config.hard_cap,
            a1=players["A1"],
            a2=players["A2"],
            b1=players["B1"],
            b2=players["B2"],
            first_server_slot=first_server_slot,
            first_receiver_slot=first_receiver_slot,
            starting_rotation_idx=rotation_idx,
            win_by=config.win_by,
        )

        self.state = GameState(
            game_id=game_id,
            config=config,
            players=players,
            rotation_index=rotation_idx,
            current_server=first_server_slot,
            current_receiver=first_receiver_slot,
        )
        self._start_new_point()

    def _start_new_point(self):
        """Initialise state for a new point and take the start snapshot."""
        # Preserve the just-completed point's start snapshot so `delete_last_point`
        # can roll back to it. Skipped for the very first point of a game, where
        # no prior point exists.
        if self._point_start_snapshot is not None:
            self._prev_point_start_snapshots.append(self._point_start_snapshot)

        s = self.state
        if s.in_overtime:
            server, receiver = self._overtime_pair()
        else:
            pair = pair_at(s.rotation_index)
            server, receiver = pair.server, pair.receiver

        s.current_server = server
        s.current_receiver = receiver
        s.possession_team = team_of(server)
        s.last_touch_team = None
        s.last_touch_player = None
        s.fault_count = 0
        s.point_in_progress = False

        self._point_started_in_ot = s.in_overtime
        self._tracker.reset_for_point(server, receiver)

        # Take point-start snapshot
        self._point_start_snapshot = copy.deepcopy(s)
        self._point_snapshots.clear()
        self._point_events.clear()
        self._seq_in_point = 0

    def _overtime_pair(self):
        """Compute server/receiver for current overtime point."""
        s = self.state
        # Alternate server every point
        if s.overtime_serve_counter % 2 == 0:
            srv_team = s.overtime_first_server_team
        else:
            srv_team = "B" if s.overtime_first_server_team == "A" else "A"
        rcv_team = "B" if srv_team == "A" else "A"
        server = s.overtime_servers.get(srv_team, f"{srv_team}1")
        receiver = s.overtime_receivers.get(rcv_team, f"{rcv_team}1")
        return server, receiver

    # ------------------------------------------------------------------ #
    #  Action dispatchers                                                   #
    # ------------------------------------------------------------------ #

    def on_serve_action(self, kind: str):
        """kind: 'ace' | 'fault' | 'receive' | 'weak_receive'"""
        if not self.state or self.state.ended or self.state.segment != "serve":
            return
        if self._awaiting_fault_type:
            return

        s = self.state
        if kind == "ace":
            self._save_snapshot()
            self._record_event("serve_ace", player=s.current_server)
            self._end_point(team_of(s.current_server))

        elif kind == "fault":
            self._save_snapshot()
            if s.fault_count == 0:
                # First fault — prompt for type
                self._record_event("serve_fault", player=s.current_server)
                s.fault_count = 1
                self._awaiting_fault_type = True
                self._on_state_changed()
                self._on_fault_prompt(self._receive_fault_type_first)
            else:
                # Second fault — record double fault, prompt for type, then
                # award point to receivers in the callback.
                self._record_event("serve_double_fault", player=s.current_server)
                self._awaiting_fault_type = True
                self._on_state_changed()
                self._on_fault_prompt(self._receive_fault_type_double)

        elif kind in ("receive", "weak_receive"):
            self._save_snapshot()
            s.fault_count = 0
            player = self._tracker.credit_receive()
            self._record_event(kind, player=player)
            s.possession_team = team_of(player)
            s.last_touch_team = s.possession_team
            s.last_touch_player = player
            s.point_in_progress = True
            self._notify()

    def _receive_fault_type_first(self, fault_type: str | None):
        """Called by UI after user selects fault type for the FIRST fault.

        `fault_type=None` means the user cancelled the picker — roll back the
        just-recorded serve_fault entirely.
        """
        self._awaiting_fault_type = False
        if not self.state:
            return
        if fault_type is None:
            self._cancel_pending_fault()
            return
        # Update last event's fault_type in DB (insert a follow-up annotation event)
        self._record_event("serve_fault_type", player=self.state.current_server,
                           fault_type=fault_type)
        self._notify()

    def _receive_fault_type_double(self, fault_type: str | None):
        """Called by UI after user selects fault type for the SECOND (double) fault.

        Annotates the double-fault event with its type, then awards the
        point to the receiving team. `fault_type=None` cancels the double
        fault entirely (state rolls back to before the second Fault click).
        """
        self._awaiting_fault_type = False
        if not self.state:
            return
        if fault_type is None:
            self._cancel_pending_fault()
            return
        s = self.state
        self._record_event("serve_fault_type", player=s.current_server,
                           fault_type=fault_type)
        rcv_team = team_of(s.current_receiver)
        self._end_point(rcv_team)

    def _cancel_pending_fault(self):
        """Undo the fault event currently awaiting a type selection.

        Pops the snapshot taken just before the Fault click, deletes the
        serve_fault / serve_double_fault event from the DB, and notifies UI.
        """
        if self._point_events:
            last = self._point_events.pop()
            events_repo.delete_event(last["id"])
        if self._point_snapshots:
            self.state = self._point_snapshots.pop()
            self._restore_tracker_from_state(self.state)
            games_repo.update_score(
                self.state.game_id, self.state.score_a, self.state.score_b
            )
            self._seq_in_point = len(self._point_events)
        self._notify()

    def set_fault_type(self, fault_type: str):
        """Called by UI after user selects fault type."""
        self._receive_fault_type_first(fault_type)

    def on_play_action(self, kind: str, player_slot: str | None = None):
        """kind: 'set'|'weak_set'|'hit'|'weak_hit'|'touch'|'weak_touch'|
                 'soft_touch'|'weak_soft_touch'

        player_slot: optional explicit slot for actions where the UI lets the
        user pick which player gets credited (currently 'touch' / 'weak_touch',
        since either opposing-team player can take the touch after a hit).
        """
        if not self.state or self.state.ended or self.state.segment != "play":
            return
        if self._awaiting_fault_type:
            return

        s = self.state
        acting_team = s.possession_team

        normalized = kind.replace("weak_", "")
        if player_slot and normalized == "touch":
            if team_of(player_slot) != acting_team:
                return  # picked slot isn't on the team in possession
            self._save_snapshot()
            player = self._tracker.credit_chosen(player_slot)
        else:
            self._save_snapshot()
            player = self._tracker.credit_action(kind, acting_team)
        self._record_event(kind, player=player)

        s.last_touch_team = acting_team
        s.last_touch_player = player

        if normalized == "hit":
            # Possession switches
            s.possession_team = "B" if acting_team == "A" else "A"

        self._notify()

    def on_other_action(self, kind: str):
        """kind: 'point'|'error'|'lost'|'overtime'|'game_end'"""
        if not self.state or self.state.ended:
            return
        if self._awaiting_fault_type:
            return

        s = self.state

        if kind == "point":
            if s.last_touch_team is None:
                # No touch yet — award to current serving team
                winning_team = team_of(s.current_server)
            else:
                winning_team = s.last_touch_team
            self._save_snapshot()
            self._record_event("point", player=s.last_touch_player)
            self._end_point(winning_team)

        elif kind == "error":
            # Error attributes the point loss to the player who just acted.
            # The UI gates the button on _ERROR_PRECURSOR_EVENTS being present,
            # but guard here too in case the engine is driven directly.
            if s.last_touch_team is None or s.last_touch_player is None:
                return
            winning_team = "B" if s.last_touch_team == "A" else "A"
            self._save_snapshot()
            self._record_event("error", player=s.last_touch_player)
            self._end_point(winning_team)

        elif kind == "lost":
            # Generic "the last-active team lost the point" — unlike Error,
            # we don't attribute it to a specific action in the analysis. The
            # player slot is still stamped on the event for the sequence log.
            # Same precursor gate as Error.
            if s.last_touch_team is None:
                return
            winning_team = "B" if s.last_touch_team == "A" else "A"
            self._save_snapshot()
            self._record_event("lost", player=s.last_touch_player)
            self._end_point(winning_team)

        elif kind == "overtime":
            if not s.in_overtime:
                self._save_snapshot()
                self._enter_overtime()

        elif kind == "game_end":
            self._save_snapshot()
            self._record_event("game_end")
            self._finish_game(
                winner_team=None,
                end_reason="manual",
            )

    def _end_point(self, winning_team: str):
        s = self.state

        # Award point
        if winning_team == "A":
            s.score_a += 1
        else:
            s.score_b += 1

        # Persist score
        games_repo.update_score(s.game_id, s.score_a, s.score_b)

        # Check overtime auto-trigger (before checking win)
        if should_trigger_overtime(s.score_a, s.score_b, s.config, s.in_overtime):
            self._enter_overtime()

        # Check game-end conditions
        winner = check_game_end(s.score_a, s.score_b, s.config)
        if winner:
            reason = (
                "hard_cap"
                if (s.score_a >= s.config.hard_cap or s.score_b >= s.config.hard_cap)
                else "target_reached"
            )
            self._finish_game(winner_team=winner, end_reason=reason)
            return

        # Advance rotation and start next point. Use the snapshot taken at
        # point-start (not live s.in_overtime) so the OT-trigger point — which
        # was played under normal rotation but ends with s.in_overtime=True —
        # advances rotation, leaving the OT counter at 0 for the first real
        # OT point.
        if self._point_started_in_ot:
            s.overtime_serve_counter += 1
        else:
            s.rotation_index = next_rotation_index(s.rotation_index)
        s.point_number += 1
        self._start_new_point()
        self._notify()

    def _enter_overtime(self):
        s = self.state
        if s.in_overtime:
            return
        s.in_overtime = True
        s.overtime_serve_counter = 0

        # Freeze current server/receiver for each team as OT references
        # Server per team: whoever was serving most recently for each team
        # We derive this from the rotation table
        from app.core.rotation import SERVE_ROTATION
        # Build a map: for each team, the server they last used
        # by looking at the current rotation position
        ot_servers: dict[str, str] = {}
        ot_receivers: dict[str, str] = {}

        current_pair = SERVE_ROTATION[s.rotation_index % len(SERVE_ROTATION)]
        srv_team = team_of(current_pair.server)
        rcv_team = team_of(current_pair.receiver)
        ot_servers[srv_team] = current_pair.server
        ot_receivers[rcv_team] = current_pair.receiver

        # For the other team, look back through rotation to find most recent
        partner_srv_team = "B" if srv_team == "A" else "A"
        partner_rcv_team = "B" if rcv_team == "A" else "A"
        for offset in range(1, 8):
            idx = (s.rotation_index - offset) % len(SERVE_ROTATION)
            pair = SERVE_ROTATION[idx]
            if team_of(pair.server) == partner_srv_team and partner_srv_team not in ot_servers:
                ot_servers[partner_srv_team] = pair.server
            if team_of(pair.receiver) == partner_rcv_team and partner_rcv_team not in ot_receivers:
                ot_receivers[partner_rcv_team] = pair.receiver
            if len(ot_servers) == 2 and len(ot_receivers) == 2:
                break

        # Defaults if not found
        for t in ("A", "B"):
            ot_servers.setdefault(t, f"{t}1")
            ot_receivers.setdefault(t, f"{t}1")

        s.overtime_first_server_team = srv_team
        s.overtime_servers = ot_servers
        s.overtime_receivers = ot_receivers

        self._record_event("overtime_triggered")
        if self._on_overtime_started:
            self._on_overtime_started()

    def _finish_game(self, winner_team: str | None, end_reason: str):
        s = self.state
        s.ended = True
        s.winner_team = winner_team
        s.end_reason = end_reason

        games_repo.finalize_game(
            game_id=s.game_id,
            winner_team=winner_team,
            end_reason=end_reason,
            score_a=s.score_a,
            score_b=s.score_b,
        )

        self._notify()
        self._on_game_ended(s)

    # ------------------------------------------------------------------ #
    #  Undo / Reset Point                                                   #
    # ------------------------------------------------------------------ #

    def undo(self) -> bool:
        """
        Undo the last action in the current point.
        Returns True if something was undone.
        """
        if not self._point_events:
            return False

        # Delete the last DB event for this point.
        last = self._point_events.pop()
        events_repo.delete_event(last["id"])

        # A serve_fault_type is a follow-up annotation written immediately
        # after the user picks a type from the dialog — no snapshot was
        # pushed for it. Pair it back to its serve_fault and undo both, so
        # one Undo click cleanly cancels the whole fault.
        if last["type"] == "serve_fault_type" and self._point_events:
            prev = self._point_events[-1]
            if prev["type"] == "serve_fault":
                self._point_events.pop()
                events_repo.delete_event(prev["id"])

        # Restore snapshot
        if self._point_snapshots:
            self.state = self._point_snapshots.pop()
            # Restore tracker state for this snapshot
            self._restore_tracker_from_state(self.state)
            games_repo.update_score(
                self.state.game_id, self.state.score_a, self.state.score_b
            )
            self._seq_in_point = len(self._point_events)
            self._awaiting_fault_type = False
            self._notify()
            return True

        # Nothing left to undo in this point
        return False

    def reset_point(self):
        """
        Clear all events for the current point and restart it fresh.
        """
        if not self.state:
            return
        # Delete all current-point events from DB
        events_repo.delete_point_events(self.state.game_id, self.state.point_number)
        self._point_events.clear()
        self._point_snapshots.clear()
        self._awaiting_fault_type = False

        # Restore to point-start snapshot
        if self._point_start_snapshot:
            self.state = copy.deepcopy(self._point_start_snapshot)
            games_repo.update_score(
                self.state.game_id, self.state.score_a, self.state.score_b
            )

        self._seq_in_point = 0
        self._tracker.reset_for_point(
            self.state.current_server, self.state.current_receiver
        )
        self._notify()

    def delete_last_point(self) -> bool:
        """
        Roll back the most-recently-completed point: restore state to its
        start, delete all DB events for that point and any in-progress events
        for the current point, and reopen the rolled-back point fresh.

        Returns True if a point was deleted.
        """
        if not self.state or not self._prev_point_start_snapshots:
            return False

        s = self.state
        # Drop any in-progress events for the current point and the events
        # for the point being deleted.
        events_repo.delete_point_events(s.game_id, s.point_number)
        events_repo.delete_point_events(s.game_id, s.point_number - 1)

        # Restore to the start of the deleted point.
        prev = self._prev_point_start_snapshots.pop()
        self.state = copy.deepcopy(prev)
        games_repo.update_score(
            self.state.game_id, self.state.score_a, self.state.score_b
        )

        # Re-arm per-point machinery for the restored point.
        self._point_start_snapshot = copy.deepcopy(self.state)
        self._point_snapshots.clear()
        self._point_events.clear()
        self._seq_in_point = 0
        self._awaiting_fault_type = False
        self._point_started_in_ot = self.state.in_overtime
        self._tracker.reset_for_point(
            self.state.current_server, self.state.current_receiver
        )

        self._notify()
        return True

    def _restore_tracker_from_state(self, state: GameState):
        """
        Re-initialise the rally tracker from a restored snapshot.
        This is a best-effort: we reset based on last_touch_player per team.
        """
        self._tracker.reset_for_point(state.current_server, state.current_receiver)
        if state.last_touch_team and state.last_touch_player:
            self._tracker.last_player[state.last_touch_team] = state.last_touch_player

    # ------------------------------------------------------------------ #
    #  Available actions (for UI button enable/disable)                    #
    # ------------------------------------------------------------------ #

    # Events that can precede an "error": the user must have logged one of
    # these in the current point before pressing Error, since Error attributes
    # the point loss to "the action the player just performed".
    _ERROR_PRECURSOR_EVENTS = {
        "receive", "weak_receive",
        "set", "weak_set",
        "hit", "weak_hit",
        "touch", "weak_touch",
        "soft_touch", "weak_soft_touch",
    }

    def available_actions(self) -> dict[str, bool]:
        if not self.state:
            return {}
        s = self.state
        waiting = self._awaiting_fault_type
        seg = s.segment

        serve_actions = seg == "serve" and not waiting
        play_actions = seg == "play" and not waiting
        can_undo = bool(self._point_events) and not waiting
        can_reset = bool(self._point_events or
                         (self._point_start_snapshot and s.point_in_progress)) and not waiting
        can_delete_last = bool(self._prev_point_start_snapshots) and not waiting
        can_error = (
            (seg in ("serve", "play"))
            and not waiting
            and any(
                e["type"] in self._ERROR_PRECURSOR_EVENTS
                for e in self._point_events
            )
        )

        return {
            # Serve segment
            "ace": serve_actions,
            "fault": serve_actions,
            "receive": serve_actions,
            "weak_receive": serve_actions,
            # Play segment
            "set": play_actions,
            "weak_set": play_actions,
            "hit": play_actions,
            "weak_hit": play_actions,
            "touch": play_actions,
            "weak_touch": play_actions,
            "soft_touch": play_actions,
            "weak_soft_touch": play_actions,
            # Other — always available (with guards)
            "point": (seg in ("serve", "play")) and not waiting,
            "error": can_error,
            "lost": can_error,
            "overtime": not s.in_overtime and not s.ended and not waiting,
            "game_end": not s.ended and not waiting,
            "undo": can_undo,
            "reset_point": can_reset,
            "delete_last_point": can_delete_last,
        }

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _save_snapshot(self):
        """Push a deep copy of the current state onto the undo stack."""
        self._point_snapshots.append(copy.deepcopy(self.state))

    def _record_event(
        self,
        event_type: str,
        player: str | None = None,
        fault_type: str | None = None,
    ) -> int:
        s = self.state
        self._seq_in_point += 1
        event_id = events_repo.append(
            game_id=s.game_id,
            point=s.point_number,
            seq_in_point=self._seq_in_point,
            timestamp=datetime.utcnow().isoformat(),
            event_type=event_type,
            player=player,
            fault_type=fault_type,
            serving_team=team_of(s.current_server),
            receiving_team=team_of(s.current_receiver),
            score_a=s.score_a,
            score_b=s.score_b,
        )
        self._point_events.append({
            "id": event_id,
            "type": event_type,
            "player": player,
            "fault_type": fault_type,
        })
        return event_id

    def _notify(self):
        self._on_state_changed()
