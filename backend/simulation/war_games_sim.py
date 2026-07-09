"""
WarGames Simulation Engine
Team vs. team, timed-entry, double-cage stipulation match.

Modeled directly on the AWUMM WarGames booking bible:
- Two teams. The team that LOSES the coin toss enters first (disadvantage);
  the team that WINS the coin toss (advantage) enters second and gets the
  extra/deciding entrant if the squads are uneven.
- Entries alternate on a clock. Nothing is "official" (no pins/submissions
  count) until every entrant is in and the bell rings.
- Once the bell rings, it's a standard team match: win by pinfall or
  submission for either side.
- A momentum system and a dramatic-beat spot library (top-of-cage dives,
  table spots, submission teases, finisher reversals, etc.) drive the
  highlight reel and star rating, using the point values from the bible.
"""

import random
from typing import Any, Dict, List, Optional

from models.match import MatchHighlight
from models.wrestler import Wrestler

MOMENTUM_VALUES = {
    "superkick_combo": 15,
    "cage_wall_spot": 10,
    "top_of_cage_spot": 25,
    "through_table": 20,
    "return_run_in": 30,
    "finisher": 35,
    "submission_tease": 8,
}

# Dramatic beats checklist from the booking bible. Keys map to how we detect
# them from the generated spot log.
DRAMATIC_BEATS = [
    "injured_limb_targeted",
    "numerical_disadvantage_survival",
    "unlikely_allies_moment",
    "top_of_cage_high_spot",
    "table_spot_inside_cage",
    "final_two_showdown",
    "finisher_reversal_sequence",
    "emotional_post_match_moment",
]


class WarGamesSimulator:
    """Simulates a two-team WarGames double-cage match."""

    def __init__(self):
        self.random = random.Random()

    def simulate_war_games(
        self,
        side_a: List[Wrestler],
        side_b: List[Wrestler],
        advantage_side: str = "a",
        universe_state=None,
        booked_winner_side: Optional[str] = None,
        injured_wrestler_id: Optional[str] = None,
        unlikely_allies: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Args:
            side_a / side_b: full rosters for each team (any size 3-5+).
            advantage_side: 'a' or 'b' - which team won the coin toss.
                Gets the extra/deciding entrant and enters second in
                every pairing.
            booked_winner_side: 'a' or 'b' to force an outcome, else None
                for a simulated result.
            injured_wrestler_id: optional wrestler ID whose "injured limb"
                gets targeted throughout, for the dramatic beat.
            unlikely_allies: optional pair of wrestler IDs (one from each
                side, or a returning ally) to feature an "unlikely allies"
                beat, e.g. a surprise save.

        Returns:
            Dict with winner_side, entry_order, highlights, momentum log,
            dramatic_beats_hit, duration_minutes, star_rating_bonus.
        """
        disadvantage_side = "b" if advantage_side == "a" else "a"
        teams = {"a": list(side_a), "b": list(side_b)}
        disadvantage_team, advantage_team = teams[disadvantage_side], teams[advantage_side]

        entry_order = self._build_entry_order(disadvantage_side, disadvantage_team, advantage_side, advantage_team)

        momentum = {"a": 0, "b": 0}
        highlights: List[MatchHighlight] = []
        beats_hit = {beat: False for beat in DRAMATIC_BEATS}
        clock = 0

        # --- Build-up periods: one entrant at a time, alternating ---
        in_ring: List[Dict[str, Any]] = []
        for period_idx, (side, wrestler) in enumerate(entry_order, start=1):
            clock += self.random.randint(2, 3)
            in_ring.append({"side": side, "wrestler": wrestler})
            label = "advantage" if side == advantage_side else "disadvantage"
            highlights.append(
                MatchHighlight(
                    timestamp=f"{clock}:00",
                    description=f"{wrestler.name} ({label} side) enters the cage - Period {period_idx}.",
                    highlight_type="opening" if period_idx == 1 else "signature",
                )
            )

            if len(in_ring) >= 2:
                self._generate_period_spots(
                    in_ring, momentum, highlights, beats_hit, clock,
                    injured_wrestler_id, unlikely_allies,
                )

            # Numerical disadvantage survival beat: first team down a body
            solo_side_counts = {"a": 0, "b": 0}
            for entry in in_ring:
                solo_side_counts[entry["side"]] += 1
            if not beats_hit["numerical_disadvantage_survival"] and abs(solo_side_counts["a"] - solo_side_counts["b"]) >= 2:
                beats_hit["numerical_disadvantage_survival"] = True
                short_side = min(solo_side_counts, key=solo_side_counts.get)
                survivor = next(e["wrestler"] for e in in_ring if e["side"] == short_side)
                highlights.append(
                    MatchHighlight(
                        timestamp=f"{clock}:00",
                        description=f"{survivor.name} fights off a numbers disadvantage, refusing to fall.",
                        highlight_type="comeback",
                    )
                )

        # --- Bell rings: all entrants in, match is officially underway ---
        clock += 1
        highlights.append(
            MatchHighlight(
                timestamp=f"{clock}:00",
                description="All entrants are in. The bell rings - WarGames is officially underway.",
                highlight_type="signature",
            )
        )

        # --- Chaos period: extra spots now that pins/submissions are live ---
        chaos_spots = self.random.randint(4, 6)
        for _ in range(chaos_spots):
            clock += self.random.randint(1, 2)
            self._generate_period_spots(
                in_ring, momentum, highlights, beats_hit, clock,
                injured_wrestler_id, unlikely_allies, chaos=True,
            )

        # --- Final two showdown ---
        clock += 2
        a_roster = [e["wrestler"] for e in in_ring if e["side"] == "a"]
        b_roster = [e["wrestler"] for e in in_ring if e["side"] == "b"]
        anchor_a = max(a_roster, key=lambda w: w.overall_rating) if a_roster else None
        anchor_b = max(b_roster, key=lambda w: w.overall_rating) if b_roster else None
        if anchor_a and anchor_b:
            beats_hit["final_two_showdown"] = True
            highlights.append(
                MatchHighlight(
                    timestamp=f"{clock}:00",
                    description=f"{anchor_a.name} and {anchor_b.name} collide in the center of the ring - the story of the match.",
                    highlight_type="nearfall",
                )
            )
            # Finisher reversal sequence
            clock += 1
            beats_hit["finisher_reversal_sequence"] = True
            highlights.append(
                MatchHighlight(
                    timestamp=f"{clock}:00",
                    description=f"{anchor_b.name} reverses {anchor_a.name}'s finisher attempt into one of their own!",
                    highlight_type="nearfall",
                )
            )
            momentum["a" if anchor_a in a_roster else "b"] += MOMENTUM_VALUES["finisher"] // 2

        # --- Determine winner ---
        winner_side = self._determine_winner(
            teams, momentum, advantage_side, booked_winner_side
        )
        loser_side = "b" if winner_side == "a" else "a"
        winning_team = teams[winner_side]
        finisher_wrestler = max(winning_team, key=lambda w: w.overall_rating)

        clock += 1
        highlights.append(
            MatchHighlight(
                timestamp=f"{clock}:00",
                description=f"{finisher_wrestler.name} delivers the finishing blow. Referee counts... 1...2...3!",
                highlight_type="finish",
            )
        )
        momentum[winner_side] += MOMENTUM_VALUES["finisher"]

        # --- Emotional post-match moment ---
        beats_hit["emotional_post_match_moment"] = True
        survivors = teams[winner_side]
        highlights.append(
            MatchHighlight(
                timestamp=f"{clock + 1}:00",
                description=f"The cage door opens. {', '.join(w.name for w in survivors)} celebrate together - battered, bloodied, victorious.",
                highlight_type="finish",
            )
        )

        duration = max(20, clock + self.random.randint(2, 5))
        total_momentum = momentum["a"] + momentum["b"]
        beats_hit_count = sum(1 for hit in beats_hit.values() if hit)
        star_rating_bonus = round(min(1.5, (total_momentum / 200.0) + (beats_hit_count * 0.05)), 2)

        return {
            "winner_side": winner_side,
            "loser_side": loser_side,
            "winning_team": winning_team,
            "losing_team": teams[loser_side],
            "advantage_side": advantage_side,
            "entry_order": entry_order,
            "highlights": highlights,
            "momentum": momentum,
            "dramatic_beats_hit": beats_hit,
            "duration_minutes": duration,
            "star_rating_bonus": star_rating_bonus,
        }

    def _build_entry_order(self, disadvantage_side, disadvantage_team, advantage_side, advantage_team):
        """Alternate entries starting with the disadvantage side; leftover
        advantage-side members (numerical edge) enter at the end."""
        order = []
        i = 0
        while i < len(disadvantage_team) or i < len(advantage_team):
            if i < len(disadvantage_team):
                order.append((disadvantage_side, disadvantage_team[i]))
            if i < len(advantage_team):
                order.append((advantage_side, advantage_team[i]))
            i += 1
        return order

    def _generate_period_spots(
        self, in_ring, momentum, highlights, beats_hit, clock,
        injured_wrestler_id, unlikely_allies, chaos=False,
    ):
        if len(in_ring) < 2:
            return
        actor = self.random.choice(in_ring)
        target = self.random.choice([e for e in in_ring if e is not actor])
        actor_w, target_w = actor["wrestler"], target["wrestler"]
        same_side = actor["side"] == target["side"]

        weighted_spots = []
        weighted_spots.append(("cage_wall_spot", 3))
        if actor_w.speed >= 65:
            weighted_spots.append(("top_of_cage_spot", 3 if chaos else 1))
        if actor_w.brawling >= 65:
            weighted_spots.append(("through_table", 2 if chaos else 1))
        if actor_w.technical >= 65:
            weighted_spots.append(("submission_tease", 2))
        if not same_side:
            weighted_spots.append(("superkick_combo", 2))
        if chaos:
            weighted_spots.append(("finisher", 1))

        spot_type = self.random.choices(
            [s[0] for s in weighted_spots], weights=[s[1] for s in weighted_spots], k=1
        )[0]

        if same_side and spot_type in ("cage_wall_spot", "through_table", "top_of_cage_spot"):
            # attacks should generally target the other side
            opponents = [e for e in in_ring if e["side"] != actor["side"]]
            if opponents:
                target = self.random.choice(opponents)
                target_w = target["wrestler"]

        momentum_side = actor["side"]
        momentum[momentum_side] += MOMENTUM_VALUES[spot_type]

        descriptions = {
            "cage_wall_spot": f"{actor_w.name} drives {target_w.name} into the steel cage wall.",
            "top_of_cage_spot": f"{actor_w.name} climbs to the TOP of the cage and comes crashing down on {target_w.name}!",
            "through_table": f"{actor_w.name} puts {target_w.name} through a table set up inside the cage!",
            "superkick_combo": f"{actor_w.name} and an ally connect with a synchronized superkick combo on {target_w.name}!",
            "finisher": f"{actor_w.name} hits their signature finisher on {target_w.name}!",
            "submission_tease": f"{actor_w.name} locks in a submission on {target_w.name}, wrenching for all it's worth.",
        }
        highlight_type = "signature" if spot_type != "finisher" else "nearfall"
        highlights.append(
            MatchHighlight(timestamp=f"{clock}:00", description=descriptions[spot_type], highlight_type=highlight_type)
        )

        if spot_type == "top_of_cage_spot":
            beats_hit["top_of_cage_high_spot"] = True
        elif spot_type == "through_table":
            beats_hit["table_spot_inside_cage"] = True

        if injured_wrestler_id and target_w.id == injured_wrestler_id and spot_type in ("cage_wall_spot", "submission_tease"):
            beats_hit["injured_limb_targeted"] = True
            highlights.append(
                MatchHighlight(
                    timestamp=f"{clock}:00",
                    description=f"{actor_w.name} goes right back to the injured body part of {target_w.name}!",
                    highlight_type="signature",
                )
            )

        if unlikely_allies and not beats_hit["unlikely_allies_moment"]:
            ally_ids = set(unlikely_allies)
            ring_ids = {e["wrestler"].id for e in in_ring}
            if ally_ids.issubset(ring_ids) and self.random.random() < 0.3:
                beats_hit["unlikely_allies_moment"] = True
                a_name, b_name = (next(e["wrestler"].name for e in in_ring if e["wrestler"].id == wid) for wid in unlikely_allies)
                highlights.append(
                    MatchHighlight(
                        timestamp=f"{clock}:00",
                        description=f"{a_name} and {b_name} - once enemies - stand back to back against the odds. An unlikely alliance.",
                        highlight_type="comeback",
                    )
                )
                momentum["a"] += MOMENTUM_VALUES["return_run_in"] // 2
                momentum["b"] += MOMENTUM_VALUES["return_run_in"] // 2

    def _determine_winner(self, teams, momentum, advantage_side, booked_winner_side):
        if booked_winner_side in ("a", "b"):
            return booked_winner_side

        def team_power(side):
            team = teams[side]
            avg_rating = sum(w.overall_rating for w in team) / max(1, len(team))
            return avg_rating + momentum[side] * 0.15

        power_a, power_b = team_power("a"), team_power("b")
        total = power_a + power_b
        roll = self.random.uniform(0, total) if total > 0 else self.random.uniform(0, 1)
        return "a" if roll <= power_a else "b"


war_games_simulator = WarGamesSimulator()
