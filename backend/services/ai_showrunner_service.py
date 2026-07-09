from __future__ import annotations

import json
import random
import re
from datetime import datetime
from typing import Any

from repositories.phase_expansion_repository import PhaseExpansionRepository, new_id


ANGLE_LIBRARY_SEEDS = [
    ("contract_signing_chaos", "Contract Signing Chaos", "feud_escalation", "contract_signing", "high", 8, 2, 6, ["feud_heat +10", "match_stipulation_pressure +8", "injury_risk +2"]),
    ("parking_lot_attack", "Parking Lot Attack", "shock_attack", "backstage_attack", "high", 6, 2, 4, ["feud_heat +12", "victim_morale -3", "attacker_heat +8"]),
    ("medical_update_interruption", "Medical Update Interrupted", "injury_story", "interview", "medium", 5, 1, 3, ["return_anticipation +8", "sympathy +6"]),
    ("gm_office_deal", "GM Office Deal", "authority", "backstage", "medium", 4, 2, 5, ["storyline_clarity +7", "authority_heat +5"]),
    ("mystery_attacker_clue", "Mystery Attacker Clue", "mystery", "vignette", "medium", 4, 1, 4, ["suspense +10", "social_buzz +6"]),
    ("faction_recruitment", "Faction Recruitment Pitch", "faction", "backstage", "medium", 5, 2, 6, ["faction_momentum +8", "turn_tease +6"]),
    ("fake_injury_swerve", "Fake Injury Swerve", "swerve", "in_ring_attack", "high", 8, 2, 4, ["shock +12", "trust_penalty_if_overused +5"]),
    ("champion_celebration_ruined", "Champion Celebration Ruined", "title_scene", "in_ring_attack", "high", 6, 2, 6, ["title_heat +10", "challenger_momentum +7"]),
    ("cash_in_tease", "Cash-In Tease", "money_in_bank", "run_in", "high", 3, 2, 4, ["briefcase_heat +9", "champion_anxiety +7"]),
    ("locker_room_pull_apart", "Locker Room Pull-Apart", "brawl", "backstage", "medium", 4, 4, 8, ["locker_room_tension +7", "feud_heat +8"]),
    ("betrayal_tease", "Betrayal Tease", "turn", "confrontation", "medium", 5, 2, 5, ["turn_suspicion +8", "team_chemistry_pressure +6"]),
    ("open_challenge_surprise", "Open Challenge Surprise", "opportunity", "match", "medium", 3, 2, 3, ["underused_visibility +10", "upset_probability +5"]),
]


class AIShowrunnerService:
    """Persistent AI booker, PLE roadmap, and approval queue coordinator."""

    def __init__(self, database):
        self.database = database
        self.repo = PhaseExpansionRepository(database)
        self.conn = database.conn
        self._tables_ready = False

    def now(self) -> str:
        return datetime.now().isoformat()

    def dashboard(self) -> dict:
        self._ensure_tables()
        pending = self._queue_rows("pending", 30)
        recent = self._queue_rows(None, 12)
        roadmaps = self._roadmaps(8)
        last_run = self.repo.fetch_one(
            """
            SELECT * FROM ai_showrunner_runs
            WHERE deleted_at IS NULL
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        if last_run:
            self._decode_run(last_run)
        special = self._special_system_snapshot()
        return {
            "summary": {
                "pending_approvals": len(pending),
                "active_roadmaps": len(roadmaps),
                "angle_templates": special["angle_templates_count"],
                "active_mitb": len(special["mitb_briefcases"]),
                "active_war_games": len(special["war_games"]),
                "crown_payoffs": len(special["crown_payoffs"]),
                "dark_house_runs": len(special["dark_house_runs"]),
                "live_interruptions": len(special["live_interruptions"]),
                "promo_beats": len(special["promo_beats"]),
                "last_run_status": (last_run or {}).get("status", "not_run"),
            },
            "pending_approvals": pending,
            "recent_decisions": recent,
            "roadmaps": roadmaps,
            "special_systems": special,
            "last_run": last_run,
        }

    def run_weekly(self, year: int, week: int, universe=None, seed: int | None = None, force: bool = False, autonomy_level: str = "balanced") -> dict:
        self._ensure_tables()
        show = self._current_show(universe, year, week)
        existing = self.repo.fetch_one(
            """
            SELECT * FROM ai_showrunner_runs
            WHERE year = ? AND week = ? AND show_id = ? AND deleted_at IS NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (year, week, show["show_id"]),
        )
        if existing and not force:
            self._decode_run(existing)
            return {"already_ran": True, "run": existing, "dashboard": self.dashboard()}

        rng = random.Random(seed if seed is not None else (year * 1000 + week * 17 + len(show["show_id"])))
        roster = self._active_roster(show["brand"])
        if len(roster) < 2:
            raise ValueError("AI Showrunner needs at least two active wrestlers to book a show.")

        risk = self._risk_from_autonomy(autonomy_level)
        opportunity = self._opportunity_rotation(roster)
        card = self._build_card(show, roster, opportunity, rng, risk)
        promo_beats = self._generate_promo_beats_for_card(year, week, show, card, rng, risk, source_type="ai_showrunner")
        special_systems = self._run_special_systems(year, week, show, roster, opportunity, card, universe, rng, risk, autonomy_level)
        special_systems["promo_beats"] = promo_beats
        special_systems["dark_house_autopilot"] = self._run_dark_house_autopilot(year, week, show, roster, opportunity, rng, risk, autonomy_level, force=force)
        roadmaps = self._refresh_ple_roadmap(year, week, roster, universe, rng)
        approvals, auto_executed = self._create_decisions(year, week, show, card, roadmaps, autonomy_level, risk, special_systems)
        saved_plan = self._persist_show_plan(show, card, year, week)

        run_id = new_id("showrunner")
        now = self.now()
        decisions = approvals + auto_executed
        with self.repo.transaction():
            self.conn.execute(
                """
                INSERT INTO ai_showrunner_runs (
                    id, year, week, show_id, show_name, brand, autonomy_level,
                    risk_tolerance, opportunity_rotation_json, generated_card_json,
                    decisions_json, auto_executed_json, status, created_at, updated_at, deleted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    run_id,
                    year,
                    week,
                    show["show_id"],
                    show["name"],
                    show["brand"],
                    autonomy_level,
                    risk,
                    json.dumps(opportunity[:12]),
                    json.dumps(card),
                    json.dumps(decisions),
                    json.dumps(auto_executed),
                    "auto_executed" if auto_executed else "drafted",
                    now,
                    now,
                ),
            )
            self.conn.execute(
                """
                INSERT INTO internal_simulation_jobs (
                    id, job_type, trigger_year, trigger_week, status, reads_json,
                    writes_json, result_json, created_at, updated_at, deleted_at
                ) VALUES (?, 'ai_showrunner_weekly', ?, ?, 'completed', ?, ?, ?, ?, ?, NULL)
                """,
                (
                    new_id("job"),
                    year,
                    week,
                    json.dumps(["wrestlers", "story_arcs", "story_calendar_events", "ple_roadmap_plans", "angle_library_templates"]),
                    json.dumps([
                        "ai_showrunner_runs", "booker_approval_queue", "booking_show_plans", "booking_segments",
                        "angle_executions", "money_in_bank_briefcases", "war_games_plans", "crown_tournament_payoffs",
                    ]),
                    json.dumps({"run_id": run_id, "approvals": len(approvals), "auto_executed": len(auto_executed)}),
                    now,
                    now,
                ),
            )

        return {
            "already_ran": False,
            "run_id": run_id,
            "show": show,
            "card": card,
            "saved_plan": saved_plan,
            "approvals_created": approvals,
            "auto_executed": auto_executed,
            "roadmaps": roadmaps,
            "special_systems": special_systems,
            "dashboard": self.dashboard(),
        }

    def decide_approval(self, approval_id: str, data: dict) -> dict:
        self._ensure_tables()
        action = str(data.get("decision") or data.get("action") or "").lower()
        status_map = {
            "approve": "approved",
            "approved": "approved",
            "counter": "countered",
            "modify": "countered",
            "reject": "rejected",
            "rejected": "rejected",
            "dismiss": "dismissed",
            "auto_execute": "auto_executed",
        }
        if action not in status_map:
            raise ValueError("Decision must be approve, counter, reject, dismiss, or auto_execute.")
        row = self.repo.fetch_one("SELECT * FROM booker_approval_queue WHERE id = ? AND deleted_at IS NULL", (approval_id,))
        if not row:
            raise ValueError("Booker approval item not found.")
        now = self.now()
        new_status = status_map[action]
        payload = {
            "decision": action,
            "notes": data.get("notes", ""),
            "counter_pitch": data.get("counter_pitch", ""),
            "decided_at": now,
        }
        self.conn.execute(
            """
            UPDATE booker_approval_queue
            SET status = ?, player_response_json = ?, executed_at = CASE WHEN ? IN ('approved', 'auto_executed') THEN ? ELSE executed_at END,
                updated_at = ?
            WHERE id = ?
            """,
            (new_status, json.dumps(payload), new_status, now, now, approval_id),
        )
        self.conn.commit()
        if new_status in {"approved", "auto_executed"}:
            self._execute_approved_world_change(row)
        elif new_status == "rejected":
            self._execute_rejected_world_change(row)
        row = self.repo.fetch_one("SELECT * FROM booker_approval_queue WHERE id = ?", (approval_id,))
        return self._decode_queue(row)

    def _execute_approved_world_change(self, approval_row: dict) -> None:
        """Materialize approval-queue items that should change persistent game state."""
        try:
            item = self._decode_queue(dict(approval_row))
            recommendation = item.get("recommendation_json") or {}
            if item.get("source_type") == "war_games":
                self._materialize_war_games_factions(recommendation.get("war_games") or {})
            elif item.get("source_type") == "crown_tournament":
                self._materialize_crown_payoff_decision(recommendation.get("crown") or {}, approved=True)
            elif item.get("source_type") == "mitb_cash_in":
                self._materialize_mitb_cash_in_decision(recommendation.get("cash_in") or {}, approved=True)
            elif item.get("source_type") == "living_story_ai":
                arc = recommendation.get("living_arc") or {}
                if arc.get("arc_type") == "faction_formation_betrayal_seed":
                    self._materialize_living_arc_faction(arc)
                elif arc.get("arc_type") == "alignment_turn":
                    self._materialize_alignment_turn(arc)
        except Exception:
            # Approval decisions should still persist even if optional world materialization fails.
            pass

    def _execute_rejected_world_change(self, approval_row: dict) -> None:
        """
        A handful of AI recommendations have a concrete "opposite outcome"
        when the player rejects them, rather than just being dropped. Crown
        tournament payoffs are the main case: if the player rejects the AI's
        plan for the King/Queen of the Ring winner to capture the World
        Championship at Summer Slamfest, the champion automatically retains
        instead - the story still resolves, just the other way.
        """
        try:
            item = self._decode_queue(dict(approval_row))
            recommendation = item.get("recommendation_json") or {}
            if item.get("source_type") == "crown_tournament":
                self._materialize_crown_payoff_decision(recommendation.get("crown") or {}, approved=False)
            elif item.get("source_type") == "mitb_cash_in":
                self._materialize_mitb_cash_in_decision(recommendation.get("cash_in") or {}, approved=False)
        except Exception:
            pass

    def _materialize_war_games_factions(self, war_games: dict) -> list[dict]:
        """Create persistent factions from an approved WarGames plan.

        Older approval rows can still contain the former mixed-gender, four-person
        Team A/Team B payload. Before writing factions, re-read the saved plan and
        self-heal it against the current roster when enough depth exists so the
        Factions page always matches the Showrunner intent: men's Team A/B and
        women's Team A/B, five wrestlers per side.
        """
        created = []
        plan_id = war_games.get("id")
        if plan_id:
            saved = self.repo.fetch_one("SELECT * FROM war_games_plans WHERE id = ? AND deleted_at IS NULL", (plan_id,))
            if saved:
                war_games = self._decode_war_games(saved)

        roster = None
        if not self._war_games_plan_is_full_gender_split(war_games):
            roster = self.get_roster_snapshot("Cross-Brand")
            if self._full_war_games_depth_available(roster):
                if plan_id:
                    self._delete_invalid_war_games_plan(plan_id)
                war_games = self._build_war_games_plan_row(
                    war_games.get("target_event_name") or "War Games",
                    war_games.get("target_year") or war_games.get("year") or 1,
                    war_games.get("target_week") or war_games.get("week") or 1,
                    war_games.get("year") or 1,
                    war_games.get("week") or 1,
                    roster,
                    random.Random(),
                )

        event_name = war_games.get("target_event_name") or "War Games"
        target_year = war_games.get("target_year") or war_games.get("year") or 1
        target_week = war_games.get("target_week") or war_games.get("week") or 1
        divisions = war_games.get("divisions_json") or war_games.get("divisions") or {}
        sides = []
        for division_key, division_label, gender in (("mens", "Men's", "male"), ("womens", "Women's", "female")):
            division_plan = divisions.get(division_key) or {}
            for side_key, side_label in (("team_a", "Team A"), ("team_b", "Team B")):
                members = division_plan.get(side_key) or []
                if len(members) == 5 and all(str(m.get("gender", "")).lower() == gender for m in members):
                    sides.append((f"{division_label} {side_label}", members))

        # If roster depth is thin, allow a single-division fallback, but never a
        # mixed-gender side. Full saves with enough depth are handled above.
        if not sides and not self._full_war_games_depth_available(roster or self.get_roster_snapshot("Cross-Brand")):
            for label, members in (("Team A", war_games.get("faction_a_json") or war_games.get("faction_a") or []),
                                   ("Team B", war_games.get("faction_b_json") or war_games.get("faction_b") or [])):
                genders = {str(m.get("gender", "")).lower() for m in members}
                if len(members) >= 3 and len(genders - {""}) <= 1:
                    sides.append((label, members))

        for label, members in sides:
            leader = members[0]
            faction_name = f"{event_name} {label}"
            faction = self._create_persistent_faction(
                faction_name=faction_name,
                members=members,
                leader=leader,
                primary_brand="Cross-Brand",
                identity=f"AI-approved War Games side for {event_name}.",
                goals=[
                    f"Build to War Games at Y{target_year} W{target_week}",
                    "Escalate faction warfare through advantage matches",
                    "Create betrayal and power-struggle hooks after the payoff",
                ],
                entrance_style="war_games_unit",
            )
            if faction:
                created.append(faction)
        return created


    def _materialize_alignment_turn(self, arc: dict) -> None:
        target = arc.get("target_alignment")
        participant = next((p for p in arc.get("participants", []) if p.get("role") == "turn_focus"), None)
        if not target or not participant or not participant.get("id"):
            return
        percentage = {"Heel": 0, "Tweener": 50, "Face": 100}.get(target, 50)
        now = self.now()
        self.conn.execute(
            "UPDATE wrestlers SET alignment = ?, alignment_percentage = ?, updated_at = ? WHERE id = ?",
            (target, percentage, now, participant["id"]),
        )
        self.conn.commit()

    def _materialize_living_arc_faction(self, arc: dict) -> dict | None:
        participants = arc.get("participants") or []
        members = [{"id": p.get("id"), "name": p.get("name")} for p in participants if p.get("id") and p.get("name")]
        if len(members) < 3:
            return None
        leader = next((p for p in participants if p.get("role") == "leader"), members[0])
        return self._create_persistent_faction(
            faction_name=arc.get("title") or f"{leader.get('name', 'AI')} Faction",
            members=members,
            leader={"id": leader.get("id"), "name": leader.get("name")},
            primary_brand=arc.get("brand") or "Cross-Brand",
            identity=arc.get("summary") or "AI-approved living-story faction.",
            goals=(arc.get("beats") or [])[:5],
            entrance_style="ai_story_arc",
        )

    def _create_persistent_faction(self, faction_name: str, members: list[dict], leader: dict, primary_brand: str, identity: str, goals: list[str], entrance_style: str) -> dict | None:
        clean_members = [m for m in members if m.get("id") and m.get("name")]
        if len(clean_members) < 3 or not leader.get("id"):
            return None
        existing = self.database.get_all_factions(active_only=False) if hasattr(self.database, "get_all_factions") else []
        for faction in existing:
            if faction.get("faction_name") == faction_name and not faction.get("is_disbanded"):
                return faction
        try:
            from flask import current_app, has_app_context
            universe = current_app.config.get("UNIVERSE") if has_app_context() else None
        except Exception:
            universe = None
        if universe and getattr(universe, "faction_manager", None):
            faction = universe.faction_manager.create_faction(
                faction_name=faction_name,
                member_ids=[m["id"] for m in clean_members],
                member_names=[m["name"] for m in clean_members],
                leader_id=leader["id"],
                leader_name=leader.get("name") or clean_members[0]["name"],
                primary_brand=primary_brand,
                identity=identity,
                goals=goals,
                entrance_style=entrance_style,
            )
            universe.save_faction(faction)
            self.database.conn.commit()
            return faction.to_dict()
        from models.faction import Faction
        faction_id = self._next_faction_id(existing)
        faction = Faction(
            faction_id=faction_id,
            faction_name=faction_name,
            member_ids=[m["id"] for m in clean_members],
            member_names=[m["name"] for m in clean_members],
            leader_id=leader["id"],
            leader_name=leader.get("name") or clean_members[0]["name"],
            primary_brand=primary_brand,
            identity=identity,
            goals=goals,
            entrance_style=entrance_style,
        )
        faction.ensure_member_tracking()
        self.database.save_faction(faction)
        self.database.conn.commit()
        return faction.to_dict()

    def _next_faction_id(self, existing: list[dict]) -> str:
        max_id = 0
        for faction in existing:
            raw = str(faction.get("faction_id", ""))
            match = re.search(r"(\d+)$", raw)
            if match:
                max_id = max(max_id, int(match.group(1)))
        return f"faction_{max_id + 1:03d}"

    def auto_resolve_due(self, year: int, week: int) -> dict:
        self._ensure_tables()
        rows = self.repo.fetch_all(
            """
            SELECT * FROM booker_approval_queue
            WHERE status = 'pending'
              AND autonomy_policy = 'auto_if_unanswered'
              AND auto_execute_after_week IS NOT NULL
              AND auto_execute_after_week <= ?
              AND deleted_at IS NULL
            """,
            (week,),
        )
        resolved = []
        for row in rows:
            resolved.append(self.decide_approval(row["id"], {"decision": "auto_execute", "notes": f"Auto-resolved in Y{year} W{week}."}))
        return {"resolved": len(resolved), "items": resolved}

    def latest_booking_draft(self) -> dict:
        self._ensure_tables()
        run = self.repo.fetch_one(
            """
            SELECT *
            FROM ai_showrunner_runs
            WHERE deleted_at IS NULL
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        if not run:
            return {"available": False, "message": "No AI Showrunner draft has been generated yet."}
        self._decode_run(run)
        card = run.get("generated_card_json") or {}
        draft = self._card_to_booking_draft(run, card)
        self._append_scheduled_crown_matches_to_draft(draft)
        self._append_scheduled_cash_in_matches_to_draft(draft)
        return {
            "available": True,
            "run": {
                "id": run["id"],
                "year": run["year"],
                "week": run["week"],
                "show_id": run["show_id"],
                "show_name": run["show_name"],
                "brand": run["brand"],
                "status": run["status"],
                "autonomy_level": run["autonomy_level"],
                "created_at": run["created_at"],
                "updated_at": run["updated_at"],
            },
            "show_draft": draft,
        }

    def run_dark_house_autopilot(self, year: int, week: int, universe=None, seed: int | None = None, force: bool = False, autonomy_level: str = "balanced") -> dict:
        self._ensure_tables()
        show = self._current_show(universe, year, week)
        roster = self._active_roster(show["brand"])
        if len(roster) < 2:
            raise ValueError("Dark/house autopilot needs at least two active wrestlers.")
        rng = random.Random(seed if seed is not None else (year * 3001 + week * 43 + 9))
        risk = self._risk_from_autonomy(autonomy_level)
        opportunity = self._opportunity_rotation(roster)
        return self._run_dark_house_autopilot(year, week, show, roster, opportunity, rng, risk, autonomy_level, force=force)

    def generate_promo_beats(self, year: int, week: int, show_draft: dict | None = None, seed: int | None = None, force: bool = False) -> dict:
        self._ensure_tables()
        rng = random.Random(seed if seed is not None else (year * 4001 + week * 59 + 13))
        if show_draft:
            show = {
                "show_id": show_draft.get("show_id", f"promo_y{year}_w{week}"),
                "name": show_draft.get("show_name") or show_draft.get("name") or f"Promo Beats Y{year} W{week}",
                "brand": show_draft.get("brand", "Cross-Brand"),
                "show_type": show_draft.get("show_type", "weekly_tv"),
            }
            card = {
                "show_id": show["show_id"],
                "show_name": show["name"],
                "brand": show["brand"],
                "segments": show_draft.get("segments", []),
            }
        else:
            show = {"show_id": f"promo_y{year}_w{week}", "name": f"Promo Beats Y{year} W{week}", "brand": "Cross-Brand", "show_type": "weekly_tv"}
            roster = self._active_roster("Cross-Brand")
            if len(roster) < 2:
                raise ValueError("Promo beat generator needs at least two wrestlers.")
            card = {"segments": [self._segment("standalone_promo", "promo", 1, 6, roster[:2], f"{roster[0]['name']} and {roster[1]['name']} trade verbal fire.")]}
        beats = self._generate_promo_beats_for_card(year, week, show, card, rng, 0.58, source_type="manual", force=force)
        return {"beats": beats, "total": len(beats)}

    def maybe_live_interruption(self, show_draft_data: dict, universe=None, seed: int | None = None, force: bool = False, autonomy_level: str = "balanced") -> dict:
        self._ensure_tables()
        if not show_draft_data:
            return {"inserted": False, "show_draft": show_draft_data, "reason": "No show draft provided."}
        year = int(show_draft_data.get("year") or 1)
        week = int(show_draft_data.get("week") or 1)
        show_id = show_draft_data.get("show_id") or f"live_y{year}_w{week}"
        existing = self.repo.fetch_one(
            """
            SELECT * FROM live_show_interruptions
            WHERE show_id = ? AND year = ? AND week = ? AND deleted_at IS NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (show_id, year, week),
        )
        if existing and not force:
            return {"inserted": False, "show_draft": show_draft_data, "interruption": self._decode_live_interruption(existing), "reason": "A live interruption already exists for this show."}

        rng = random.Random(seed if seed is not None else (year * 5003 + week * 71 + len(show_id)))
        risk = self._risk_from_autonomy(autonomy_level)
        participant_ids = self._participant_ids_from_show_draft(show_draft_data)
        segment_pressure = len(show_draft_data.get("segments") or []) * 0.025
        title_pressure = 0.08 if any(m.get("is_title_match") for m in show_draft_data.get("matches", [])) else 0
        probability = min(0.72, 0.12 + (risk * 0.24) + segment_pressure + title_pressure)
        if not force and rng.random() > probability:
            return {"inserted": False, "show_draft": show_draft_data, "reason": "No live interruption triggered this time.", "probability": round(probability, 3)}

        brand = show_draft_data.get("brand", "Cross-Brand")
        roster = self._active_roster(brand)
        selected = self._select_interruption_participants(roster, participant_ids, rng)
        interruption_type = rng.choice(["surprise_run_in", "unscheduled_promo", "backstage_feed_cut", "authority_reversal", "rival_invasion_tease"])
        segment_type = {
            "surprise_run_in": "run_in",
            "unscheduled_promo": "promo",
            "backstage_feed_cut": "backstage_attack",
            "authority_reversal": "announcement",
            "rival_invasion_tease": "in_ring_attack",
        }.get(interruption_type, "run_in")
        max_position = max([int(s.get("card_position") or s.get("position") or 0) for s in show_draft_data.get("segments", [])] + [int(m.get("card_position") or m.get("position") or 0) for m in show_draft_data.get("matches", [])] + [0])
        inserted_position = max_position + 1
        description = self._interruption_description(interruption_type, selected, show_draft_data)
        payload = {
            "segment_id": new_id("live_segment"),
            "segment_type": segment_type,
            "display_name": "Live Interruption",
            "description": description,
            "participants": [{"id": p["id"], "name": p["name"], "role": "instigator"} for p in selected],
            "duration": 5,
            "duration_minutes": 5,
            "position": inserted_position,
            "card_position": inserted_position,
            "purpose": "start_feud" if len(selected) >= 2 else "shock",
            "advances_feud": True,
            "creates_heat": True,
            "creates_pop": interruption_type == "unscheduled_promo",
            "can_end_in_brawl": interruption_type in {"surprise_run_in", "backstage_feed_cut", "rival_invasion_tease"},
            "live_interruption": True,
        }
        effects = ["live_show_shock +12", "crowd_heat +8", "booking_volatility +6"]
        if interruption_type in {"surprise_run_in", "rival_invasion_tease"}:
            effects.append("feud_seed +10")
        row_id = new_id("live_intr")
        now = self.now()
        with self.repo.transaction():
            self.conn.execute(
                """
                INSERT INTO live_show_interruptions (
                    id, year, week, show_id, show_name, brand, trigger_context,
                    interruption_type, severity, status, participants_json,
                    segment_payload_json, mechanical_effects_json, inserted_card_position,
                    autonomy_policy, approval_id, resolved_at, created_at, updated_at, deleted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'inserted', ?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL)
                """,
                (
                    row_id, year, week, show_id, show_draft_data.get("show_name", "Live Show"), brand,
                    "run_show", interruption_type, "high" if risk >= 0.7 else "medium",
                    json.dumps([{"id": p["id"], "name": p["name"]} for p in selected]),
                    json.dumps(payload), json.dumps(effects), inserted_position,
                    "auto", now, now, now,
                ),
            )
        show_draft_data.setdefault("segments", []).append(payload)
        interruption = self._decode_live_interruption(self.repo.fetch_one("SELECT * FROM live_show_interruptions WHERE id = ?", (row_id,)))
        return {"inserted": True, "show_draft": show_draft_data, "interruption": interruption, "probability": round(probability, 3)}

    def _ensure_tables(self) -> None:
        if self._tables_ready:
            return
        required = ("ai_showrunner_runs", "booker_approval_queue", "live_show_interruptions")
        placeholders = ",".join("?" for _ in required)
        rows = self.repo.fetch_all(
            f"SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ({placeholders})",
            required,
        )
        if {row["name"] for row in rows} != set(required):
            from persistence.phase_expansion_db import create_phase_expansion_tables
            create_phase_expansion_tables(self.database)
        self._ensure_war_games_divisions_column()
        self._tables_ready = True

    def _ensure_war_games_divisions_column(self) -> None:
        try:
            self.conn.execute("ALTER TABLE war_games_plans ADD COLUMN divisions_json TEXT NOT NULL DEFAULT '{}'")
            self.conn.commit()
        except Exception:
            pass

    def _current_show(self, universe, year: int, week: int) -> dict:
        show = None
        try:
            show = universe.calendar.get_current_show() if universe and getattr(universe, "calendar", None) else None
        except Exception:
            show = None
        if show:
            return show.to_dict() if hasattr(show, "to_dict") else dict(show)
        return {
            "show_id": f"ai_y{year}_w{week}_weekly",
            "name": f"AI Weekly Y{year} W{week}",
            "brand": "Cross-Brand",
            "show_type": "weekly_tv",
            "is_ppv": False,
            "tier": "weekly",
            "year": year,
            "week": week,
        }

    def _active_roster(self, brand: str) -> list[dict]:
        rows = self.repo.fetch_all(
            """
            SELECT id, name, age, gender, alignment, role, primary_brand, brawling,
                   technical, speed, mic, psychology, stamina, years_experience,
                   is_major_superstar, popularity, momentum, morale, fatigue,
                   injury_severity, injury_weeks_remaining
            FROM wrestlers
            WHERE COALESCE(is_retired, 0) = 0
            ORDER BY popularity DESC, momentum DESC, name
            """
        )
        healthy = []
        for row in rows:
            if int(row.get("injury_weeks_remaining") or 0) > 0:
                continue
            if brand not in {"Cross-Brand", "", None} and row.get("primary_brand") not in {brand, "Cross-Brand", None, ""}:
                continue
            row["overall"] = self._overall(row)
            healthy.append(row)
        return healthy or rows[:]

    def _overall(self, row: dict) -> float:
        keys = ["brawling", "technical", "speed", "mic", "psychology", "stamina", "popularity", "momentum"]
        values = [float(row.get(k) or 0) for k in keys]
        return round(sum(values) / max(1, len(values)), 2)

    def _opportunity_rotation(self, roster: list[dict]) -> list[dict]:
        recent_runs = self.repo.fetch_all(
            """
            SELECT generated_card_json
            FROM ai_showrunner_runs
            WHERE deleted_at IS NULL
            ORDER BY created_at DESC
            LIMIT 8
            """
        )
        usage = {w["id"]: 0 for w in roster}
        for run in recent_runs:
            card = self.repo.from_json(run.get("generated_card_json"), {})
            for segment in card.get("segments", []):
                for participant in segment.get("participants", []):
                    wid = participant.get("id")
                    if wid in usage:
                        usage[wid] += 1
        ranked = []
        for w in roster:
            base = 100 - min(95, float(w.get("popularity") or 0))
            morale_need = max(0, 55 - float(w.get("morale") or 50)) * 0.4
            gamble = max(0, 75 - float(w.get("overall") or 50)) * 0.15
            ranked.append({
                "id": w["id"],
                "name": w["name"],
                "usage_8_runs": usage.get(w["id"], 0),
                "opportunity_score": round(base + morale_need + gamble - (usage.get(w["id"], 0) * 12), 2),
            })
        return sorted(ranked, key=lambda item: item["opportunity_score"], reverse=True)

    def _build_card(self, show: dict, roster: list[dict], opportunity: list[dict], rng: random.Random, risk: float) -> dict:
        top = sorted(roster, key=lambda w: (w.get("overall", 0), w.get("popularity", 0)), reverse=True)
        underused_ids = [item["id"] for item in opportunity]
        underused = [w for wid in underused_ids for w in roster if w["id"] == wid]
        lead = top[0]
        challenger = self._pick_distinct(top[1:] + underused, {lead["id"]}, rng)
        gamble = self._pick_distinct(underused + top, {lead["id"], challenger["id"]}, rng)
        opponent = self._pick_distinct(top + underused, {lead["id"], challenger["id"], gamble["id"]}, rng)
        tag_a = self._pick_distinct(roster, {lead["id"], challenger["id"], gamble["id"], opponent["id"]}, rng)
        tag_b = self._pick_distinct(roster, {lead["id"], challenger["id"], gamble["id"], opponent["id"], tag_a["id"]}, rng)
        showcase_a = self._pick_distinct(roster, {lead["id"], challenger["id"], gamble["id"], opponent["id"], tag_a["id"], tag_b["id"]}, rng)
        showcase_b = self._pick_distinct(roster, {lead["id"], challenger["id"], gamble["id"], opponent["id"], tag_a["id"], tag_b["id"], showcase_a["id"]}, rng)
        main_winner = challenger if risk >= 0.68 and challenger.get("overall", 0) >= lead.get("overall", 0) - 18 else lead
        upset_winner = gamble if risk >= 0.5 else opponent
        segments = [
            self._segment("opening_promo", "promo", 1, 8, [lead, challenger], f"{lead['name']} opens with a PLE-level claim; {challenger['name']} interrupts."),
            self._segment("opportunity_match", "match", 2, 13, [gamble, opponent], f"Opportunity match designed to test {gamble['name']} above their usual slot.", winner=upset_winner),
            self._segment("backstage_attack", "backstage_attack", 3, 5, [challenger, lead], f"{challenger['name']} creates a backstage shock to raise urgency."),
            self._segment("tag_showcase", "tag", 4, 12, [tag_a, tag_b], "Fresh chemistry test for underused roster members."),
            self._segment("workrate_showcase", "match", 5, 10, [showcase_a, showcase_b], f"{showcase_a['name']} and {showcase_b['name']} get a main-card evaluation match.", winner=showcase_a),
            self._segment("character_interview", "interview", 6, 5, [gamble], f"{gamble['name']} gets a focused character interview after the opportunity match."),
            self._segment("main_event", "main_event_title_match", 7, 22, [lead, challenger], f"{lead['name']} vs {challenger['name']} anchors the night with a protected finish.", winner=main_winner),
        ]
        return {
            "show_id": show["show_id"],
            "show_name": show["name"],
            "brand": show["brand"],
            "show_type": show.get("show_type", "weekly_tv"),
            "risk_tolerance": risk,
            "creative_goal": "Rotate opportunities while keeping one strong PLE thread hot.",
            "segments": segments,
        }

    def _segment(self, sid: str, segment_type: str, position: int, minutes: int, wrestlers: list[dict], description: str, winner: dict | None = None) -> dict:
        return {
            "id": sid,
            "segment_type": segment_type,
            "position": position,
            "duration": minutes,
            "description": description,
            "participants": [{"id": w["id"], "name": w["name"], "role": w.get("role"), "overall": w.get("overall")} for w in wrestlers],
            "winner": {"id": winner["id"], "name": winner["name"]} if winner else None,
            "mechanical_effects": self._segment_effects(segment_type),
        }

    def _segment_effects(self, segment_type: str) -> list[str]:
        effects = {
            "promo": ["Raises feud heat if crowd response lands.", "Creates approval item for opening direction."],
            "match": ["Updates opportunity rotation.", "Can improve underused wrestler momentum."],
            "backstage_attack": ["Can trigger a dynamic event popup.", "Adds urgency to the PLE roadmap."],
            "tag": ["Tests chemistry for future booking."],
            "interview": ["Improves character clarity and promo visibility."],
            "contract_signing": ["Creates PLE-stakes pressure.", "Raises refusal/interference risk."],
            "run_in": ["Can trigger cash-in or interference consequences.", "Raises live shock value."],
            "vignette": ["Builds mystery and anticipation.", "Adds social discussion."],
            "confrontation": ["Clarifies character direction.", "Can tease turns or alliances."],
            "backstage": ["Moves story without using match time.", "Can alter locker-room relationships."],
            "in_ring_attack": ["Escalates feud heat.", "Can justify stipulation or injury write-off."],
            "main_event_title_match": ["Protects or changes title-track momentum.", "Requires approval before major title implications."],
        }
        return effects.get(segment_type, ["Adds weekly continuity."])

    def _pick_distinct(self, pool: list[dict], used: set[str], rng: random.Random) -> dict:
        choices = [w for w in pool if w["id"] not in used]
        if not choices:
            choices = pool
        return rng.choice(choices[: max(1, min(6, len(choices)))])

    def _run_dark_house_autopilot(self, year: int, week: int, show: dict, roster: list[dict], opportunity: list[dict], rng: random.Random, risk: float, autonomy: str, force: bool = False) -> dict:
        existing = self.repo.fetch_all(
            """
            SELECT * FROM dark_house_show_runs
            WHERE year = ? AND week = ? AND parent_show_id = ? AND deleted_at IS NULL
            ORDER BY created_at DESC
            """,
            (year, week, show["show_id"]),
        )
        if existing and not force:
            return {"created": [], "existing": [self._decode_dark_house(row) for row in existing], "total": len(existing)}

        underused_ids = [item["id"] for item in opportunity]
        focus = [w for wid in underused_ids for w in roster if w["id"] == wid]
        focus = (focus + roster)[: max(6, min(len(roster), 10))]
        plans = [
            ("dark_show", f"{show['name']} Dark", 650, 22),
            ("house_show", f"{show['brand']} House Loop", 2200, 38),
        ]
        created = []
        for mode, name, attendance_base, runtime in plans:
            show_id = f"{show['show_id']}_{mode}_y{year}_w{week}"
            selected = []
            used = set()
            for _ in range(min(6, len(focus))):
                pick = self._pick_distinct(focus, used, rng)
                selected.append(pick)
                used.add(pick["id"])
            if len(selected) < 2:
                continue
            first = selected[0]
            second = selected[1]
            third = selected[2] if len(selected) > 2 else self._pick_distinct(roster, {first["id"], second["id"]}, rng)
            fourth = selected[3] if len(selected) > 3 else self._pick_distinct(roster, {first["id"], second["id"], third["id"]}, rng)
            fifth = selected[4] if len(selected) > 4 else first
            sixth = selected[5] if len(selected) > 5 else second
            upset = first if risk >= 0.5 else second
            card = {
                "show_id": show_id,
                "show_name": name,
                "brand": show["brand"],
                "show_mode": mode,
                "segments": [
                    self._segment(f"{mode}_opener", "match", 1, 8, [first, second], f"{first['name']} gets live reps against {second['name']}.", winner=upset),
                    self._segment(f"{mode}_promo_lab", "promo", 2, 5, [third, fourth], f"{third['name']} and {fourth['name']} test crowd-facing character beats."),
                    self._segment(f"{mode}_main", "tag", 3, 9, [third, fourth, fifth, sixth], "Low-pressure chemistry and stamina test for the opportunity rotation.", winner=third),
                ],
            }
            impacts = self._dark_house_impacts(card, mode)
            results = {
                "match_count": 2,
                "promo_count": 1,
                "standout": first["name"],
                "crowd_response": round(54 + rng.random() * 18 + (risk * 6), 2),
                "notes": "Autopilot completed and fed opportunity rotation, morale, and fatigue.",
            }
            attendance = int(attendance_base + rng.randint(0, 350) + (sum(float(w.get("popularity") or 0) for w in selected) / max(1, len(selected))) * 8)
            revenue = attendance * (18 if mode == "dark_show" else 32)
            row_id = new_id("dhshow")
            now = self.now()
            with self.repo.transaction():
                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO dark_house_show_runs (
                        id, year, week, parent_show_id, show_name, brand, show_mode,
                        autonomy_level, status, roster_focus_json, card_json, results_json,
                        opportunity_impacts_json, attendance, revenue, created_at, updated_at, deleted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?, COALESCE(
                        (SELECT created_at FROM dark_house_show_runs WHERE id = ?), ?
                    ), ?, NULL)
                    """,
                    (
                        row_id, year, week, show["show_id"], name, show["brand"], mode, autonomy,
                        json.dumps([{"id": w["id"], "name": w["name"]} for w in selected]),
                        json.dumps(card), json.dumps(results), json.dumps(impacts),
                        attendance, revenue, row_id, now, now,
                    ),
                )
            self._persist_auxiliary_show_plan(show_id, name, show["brand"], mode, year, week, runtime, card["segments"])
            self._apply_dark_house_impacts(impacts)
            created.append(self._decode_dark_house(self.repo.fetch_one("SELECT * FROM dark_house_show_runs WHERE id = ?", (row_id,))))
        return {"created": created, "existing": [], "total": len(created)}

    def _persist_auxiliary_show_plan(self, show_id: str, show_name: str, brand: str, show_type: str, year: int, week: int, runtime: int, card_segments: list[dict]) -> None:
        planned_start = 0
        segments = []
        for item in card_segments:
            segments.append({
                "id": f"aux_{show_id}_{item['id']}",
                "show_id": show_id,
                "source_item_id": item["id"],
                "item_type": show_type,
                "segment_type": item["segment_type"],
                "card_position": item["position"],
                "planned_start_minute": planned_start,
                "planned_duration_minutes": item["duration"],
                "allocation_status": "autopilot_completed",
                "expected_min_minutes": max(1, item["duration"] - 2),
                "expected_max_minutes": item["duration"] + 4,
                "is_opening": item["position"] == 1,
                "is_main_event": item["position"] == len(card_segments),
                "is_dark_match": show_type == "dark_show",
                "dark_match_phase": show_type,
                "quality_score": 58,
                "crowd_heat_score": 50,
                "payload_json": item,
            })
            planned_start += item["duration"]
        self.repo.replace_show_plan({
            "show_id": show_id,
            "show_name": show_name,
            "brand": brand,
            "show_type": show_type,
            "year": year,
            "week": week,
            "total_runtime_minutes": runtime,
            "network_break_count": 0,
            "accept_overrun": True,
            "booking_credibility_delta": 0.7,
            "planned_rating_impact": 0.01,
            "dead_air_risk_minutes": max(0, runtime - planned_start),
            "overrun_minutes": max(0, planned_start - runtime),
            "warnings": ["AI autopilot auxiliary show: review opportunity and fatigue impacts."],
        }, segments)

    def _dark_house_impacts(self, card: dict, mode: str) -> list[dict]:
        seen = {}
        for segment in card.get("segments", []):
            for participant in segment.get("participants", []):
                wid = participant.get("id")
                if not wid:
                    continue
                entry = seen.setdefault(wid, {
                    "wrestler_id": wid,
                    "wrestler_name": participant.get("name", wid),
                    "appearances": 0,
                    "morale_delta": 0,
                    "momentum_delta": 0,
                    "fatigue_delta": 0,
                    "development_delta": 0,
                })
                entry["appearances"] += 1
                entry["morale_delta"] += 1 if mode == "dark_show" else 2
                entry["momentum_delta"] += 1
                entry["fatigue_delta"] += 2 if mode == "dark_show" else 4
                entry["development_delta"] += 2 if mode == "dark_show" else 1
        return list(seen.values())

    def _apply_dark_house_impacts(self, impacts: list[dict]) -> None:
        for impact in impacts:
            row = self.repo.fetch_one("SELECT morale, momentum, fatigue FROM wrestlers WHERE id = ?", (impact["wrestler_id"],))
            if not row:
                continue
            morale = max(0, min(100, float(row.get("morale") or 50) + impact.get("morale_delta", 0)))
            momentum = max(0, min(100, float(row.get("momentum") or 50) + impact.get("momentum_delta", 0)))
            fatigue = max(0, min(100, float(row.get("fatigue") or 0) + impact.get("fatigue_delta", 0)))
            self.conn.execute(
                "UPDATE wrestlers SET morale = ?, momentum = ?, fatigue = ?, updated_at = ? WHERE id = ?",
                (morale, momentum, fatigue, self.now(), impact["wrestler_id"]),
            )
        self.conn.commit()

    def _generate_promo_beats_for_card(self, year: int, week: int, show: dict, card: dict, rng: random.Random, risk: float, source_type: str, force: bool = False) -> list[dict]:
        roster = {row["id"]: row for row in self._active_roster(show.get("brand", "Cross-Brand"))}
        candidates = []
        for index, segment in enumerate(card.get("segments", []), start=1):
            segment_type = segment.get("segment_type", "promo")
            if segment_type in {"promo", "interview", "contract_signing", "confrontation", "announcement", "vignette"}:
                candidates.append((index, segment))
        if not candidates and card.get("segments"):
            candidates.append((1, card["segments"][0]))
        beats = []
        for index, segment in candidates[:4]:
            source_id = segment.get("segment_id") or segment.get("id") or f"segment_{index}"
            existing = self.repo.fetch_one(
                """
                SELECT * FROM promo_dialogue_beats
                WHERE year = ? AND week = ? AND show_id = ? AND source_id = ? AND deleted_at IS NULL
                LIMIT 1
                """,
                (year, week, show.get("show_id"), source_id),
            )
            if existing and not force:
                beats.append(self._decode_promo_beat(existing))
                continue
            participants = self._normalize_segment_participants(segment, roster)
            if not participants:
                continue
            beat = self._build_promo_beat(year, week, show, source_type, source_id, segment, participants, rng, risk)
            self._insert_promo_beat(beat)
            beats.append(beat)
        return beats

    def _build_promo_beat(self, year: int, week: int, show: dict, source_type: str, source_id: str, segment: dict, participants: list[dict], rng: random.Random, risk: float) -> dict:
        tone = rng.choice(["defiant", "wounded", "arrogant", "urgent", "cold"])
        beat_type = segment.get("segment_type", "promo")
        lead = participants[0]
        reply = participants[1] if len(participants) > 1 else None
        hook = segment.get("description") or f"{lead['name']} gets a microphone and forces the room to listen."
        lines = [
            {"speaker_id": lead["id"], "speaker_name": lead["name"], "line": f"I am done waiting for permission. Tonight, I make them react to me."},
        ]
        if reply:
            lines.append({"speaker_id": reply["id"], "speaker_name": reply["name"], "line": f"You wanted attention. Now you have mine, and that is a problem for you."})
        lines.append({"speaker_id": lead["id"], "speaker_name": lead["name"], "line": "Remember this moment, because after tonight nobody gets to call me optional."})
        crowd_goal = rng.choice(["cheers", "heat", "shock", "sustained engagement"])
        effects = ["promo_clarity +8", "crowd_investment +6"]
        if risk >= 0.6:
            effects.append("creative_volatility +4")
        return {
            "id": new_id("promo_beat"),
            "year": year,
            "week": week,
            "show_id": show.get("show_id"),
            "source_type": source_type,
            "source_id": source_id,
            "beat_type": beat_type,
            "tone": tone,
            "status": "drafted",
            "participants_json": participants,
            "hook_text": hook,
            "lines_json": lines,
            "crowd_goal": crowd_goal,
            "risk_level": "high" if risk >= 0.68 else "medium",
            "mechanical_effects_json": effects,
            "created_at": self.now(),
            "updated_at": self.now(),
        }

    def _insert_promo_beat(self, beat: dict) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO promo_dialogue_beats (
                id, year, week, show_id, source_type, source_id, beat_type, tone,
                status, participants_json, hook_text, lines_json, crowd_goal,
                risk_level, mechanical_effects_json, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(
                (SELECT created_at FROM promo_dialogue_beats WHERE id = ?), ?
            ), ?, NULL)
            """,
            (
                beat["id"], beat["year"], beat["week"], beat["show_id"], beat["source_type"], beat["source_id"],
                beat["beat_type"], beat["tone"], beat["status"], json.dumps(beat["participants_json"]),
                beat["hook_text"], json.dumps(beat["lines_json"]), beat["crowd_goal"], beat["risk_level"],
                json.dumps(beat["mechanical_effects_json"]), beat["id"], beat["created_at"], beat["updated_at"],
            ),
        )
        self.conn.commit()

    def _normalize_segment_participants(self, segment: dict, roster_by_id: dict[str, dict]) -> list[dict]:
        participants = []
        for raw in segment.get("participants", []):
            if isinstance(raw, str):
                wrestler = roster_by_id.get(raw, {})
                participants.append({"id": raw, "name": wrestler.get("name", raw)})
            elif isinstance(raw, dict):
                wid = raw.get("id") or raw.get("wrestler_id") or raw.get("participant_id")
                if wid:
                    wrestler = roster_by_id.get(wid, {})
                    participants.append({"id": wid, "name": raw.get("name") or raw.get("wrestler_name") or wrestler.get("name", wid)})
        return participants

    def _participant_ids_from_show_draft(self, show_draft_data: dict) -> list[str]:
        ids = []
        for match in show_draft_data.get("matches", []):
            for key in ("participants",):
                for item in match.get(key, []) or []:
                    if isinstance(item, str):
                        ids.append(item)
                    elif isinstance(item, list):
                        ids.extend([v for v in item if isinstance(v, str)])
            for side_key in ("side_a", "side_b"):
                side = match.get(side_key) or {}
                ids.extend(side.get("wrestler_ids") or [])
        for segment in show_draft_data.get("segments", []):
            for item in segment.get("participants", []) or []:
                if isinstance(item, str):
                    ids.append(item)
                elif isinstance(item, dict):
                    wid = item.get("id") or item.get("wrestler_id") or item.get("participant_id")
                    if wid:
                        ids.append(wid)
        return list(dict.fromkeys([wid for wid in ids if wid]))

    def _select_interruption_participants(self, roster: list[dict], current_ids: list[str], rng: random.Random) -> list[dict]:
        current = [w for w in roster if w["id"] in current_ids]
        outsiders = [w for w in roster if w["id"] not in current_ids]
        selected = []
        if current:
            selected.append(rng.choice(current[: max(1, min(8, len(current)))]))
        if outsiders:
            selected.append(rng.choice(outsiders[: max(1, min(10, len(outsiders)))]))
        elif len(current) > 1:
            selected.append(rng.choice([w for w in current if w["id"] != selected[0]["id"]]))
        return selected or roster[:2]

    def _interruption_description(self, interruption_type: str, participants: list[dict], show_draft_data: dict) -> str:
        names = [p.get("name", p.get("id", "Unknown")) for p in participants]
        first = names[0] if names else "Someone"
        second = names[1] if len(names) > 1 else "the locker room"
        templates = {
            "surprise_run_in": f"{first} storms the ring without clearance and targets {second}, forcing production to follow the chaos.",
            "unscheduled_promo": f"{first} takes a live microphone and changes the emotional direction of the show.",
            "backstage_feed_cut": f"The broadcast cuts backstage as {first} and {second} collide in an unscheduled fight.",
            "authority_reversal": f"Management interrupts the show to alter the stakes around {first}.",
            "rival_invasion_tease": f"{first} appears through the crowd and leaves {second} staring at a possible invasion.",
        }
        return templates.get(interruption_type, f"{first} interrupts {show_draft_data.get('show_name', 'the show')} without warning.")

    def _run_special_systems(self, year: int, week: int, show: dict, roster: list[dict], opportunity: list[dict], card: dict, universe, rng: random.Random, risk: float, autonomy: str) -> dict:
        self._seed_angle_library()
        angle = self._book_angle_execution(year, week, show, roster, opportunity, card, rng, risk)
        mitb = self._refresh_mitb_system(year, week, roster, opportunity, rng)
        war_games = self._refresh_war_games_system(year, week, roster, universe, rng)
        crown = self._refresh_crown_payoff_system(year, week, roster, universe, rng)
        mitb_cash_ins = self._check_scheduled_mitb_cash_ins(year, week, universe, rng)
        living_arcs = self._propose_living_story_arcs(year, week, show, roster, universe, rng, risk)
        return {
            "angle_execution": angle,
            "mitb": mitb,
            "mitb_cash_ins": mitb_cash_ins,
            "war_games": war_games,
            "crown_payoffs": crown,
            "living_arcs": living_arcs,
            "autonomy": autonomy,
        }


    def _propose_living_story_arcs(self, year: int, week: int, show: dict, roster: list[dict], universe, rng: random.Random, risk: float) -> list[dict]:
        """Paced, approval-gated living-world faction/team/turn pitches."""
        is_major_window = bool(show.get("is_ppv") or show.get("show_type") in {"ppv", "major_ppv", "premium_live_event"} or show.get("tier") in {"ppv", "major"})
        if not is_major_window and week % 3 != 0:
            return []
        brands = sorted({w.get("primary_brand") or show.get("brand") or "Cross-Brand" for w in roster})
        if show.get("brand") not in {None, "", "Cross-Brand"}:
            brands = [show.get("brand")]
        proposals = []
        for brand in brands[:3]:
            brand_roster = [w for w in roster if brand in {"Cross-Brand", None, ""} or w.get("primary_brand") in {brand, "Cross-Brand", None, ""}]
            for gender in ("Male", "Female"):
                division = [w for w in brand_roster if str(w.get("gender", "")).lower() == gender.lower()]
                if len(division) < 2:
                    continue
                ranked = sorted(division, key=lambda w: (float(w.get("momentum") or 0), float(w.get("popularity") or 0), float(w.get("overall") or 0)), reverse=True)
                if len(ranked) >= 3:
                    members = ranked[: min(4, len(ranked))]
                    leader = members[0]
                    betrayer = members[-1]
                    proposals.append({"arc_type": "faction_formation_betrayal_seed", "brand": brand, "division": gender, "title": f"Form {leader['name']}'s {gender} faction on {brand}", "summary": f"Create a {gender.lower()} faction led by {leader['name']} with a slow-burn trust fracture around {betrayer['name']}.", "participants": [{"id": w["id"], "name": w["name"], "role": "leader" if w == leader else "member"} for w in members], "beats": ["Recruitment save after a numbers-game attack", "Six-person statement win", "Leader takes credit for the group", "Betrayal tease near the next major show", "Player-approved turn or reconciliation payoff"], "mechanical_effects": ["faction_momentum +8", "feud_seed +10", "betrayal_suspicion +12"]})
                team = ranked[-2:]
                proposals.append({"arc_type": "tag_team_formation_breakup_seed", "brand": brand, "division": gender, "title": f"Pair {team[0]['name']} & {team[1]['name']} as a new tag team", "summary": f"Test {team[0]['name']} and {team[1]['name']} as a {gender.lower()} tag act with a loyalty arc that can become a betrayal feud if chemistry fails.", "participants": [{"id": w["id"], "name": w["name"], "role": "partner"} for w in team], "beats": ["Accidental save creates alliance", "Two-week winning streak", "Miscommunication loss", "Partner jealousy promo", "Approve breakup, betrayal, or renewed unity"], "mechanical_effects": ["tag_chemistry_test +10", "underused_visibility +8", "betrayal_option_unlocked"]})
                turn_candidate = ranked[min(1, len(ranked) - 1)]
                current = str(turn_candidate.get("alignment") or "Tweener")
                target = rng.choice([a for a in ["Face", "Heel", "Tweener"] if a != current] or ["Tweener"])
                proposals.append({"arc_type": "alignment_turn", "brand": brand, "division": gender, "target_alignment": target, "title": f"Turn arc: {turn_candidate['name']} toward {target}", "summary": f"Begin a paced {current}-to-{target} character shift for {turn_candidate['name']} with weekly story consequences before any final turn is applied.", "participants": [{"id": turn_candidate["id"], "name": turn_candidate["name"], "role": "turn_focus"}], "beats": ["Ambiguous promo motive", "Choice between friend and ambition", "Crowd-reaction checkpoint", "Major-show reveal", "Approval decides final alignment"], "mechanical_effects": ["character_direction +10", "alignment_pressure +12", "story_progression +8"]})
        rng.shuffle(proposals)
        return proposals[: (4 if is_major_window else 2)]

    def _seed_angle_library(self) -> None:
        now = self.now()
        for template_id, name, category, segment_type, risk_level, cooldown, min_p, max_p, effects in ANGLE_LIBRARY_SEEDS:
            self.conn.execute(
                """
                INSERT INTO angle_library_templates (
                    id, name, category, segment_type, risk_level, cooldown_weeks,
                    min_participants, max_participants, eligibility_json,
                    mechanical_effects_json, created_at, updated_at, deleted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(id) DO UPDATE SET name = excluded.name,
                    category = excluded.category, segment_type = excluded.segment_type,
                    risk_level = excluded.risk_level, cooldown_weeks = excluded.cooldown_weeks,
                    min_participants = excluded.min_participants, max_participants = excluded.max_participants,
                    mechanical_effects_json = excluded.mechanical_effects_json,
                    updated_at = excluded.updated_at, deleted_at = NULL
                """,
                (template_id, name, category, segment_type, risk_level, cooldown, min_p, max_p, "{}", json.dumps(effects), now, now),
            )
        self.conn.commit()

    def _book_angle_execution(self, year: int, week: int, show: dict, roster: list[dict], opportunity: list[dict], card: dict, rng: random.Random, risk: float) -> dict:
        templates = self.repo.fetch_all(
            """
            SELECT t.*
            FROM angle_library_templates t
            WHERE t.deleted_at IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM angle_executions e
                  WHERE e.template_id = t.id
                    AND ((e.year * 52) + e.week) >= ?
                    AND e.deleted_at IS NULL
              )
            ORDER BY CASE t.risk_level WHEN 'high' THEN ? ELSE 0 END DESC, t.category, t.name
            """,
            (((year * 52) + week) - 4, 1 if risk >= 0.58 else 0),
        )
        if not templates:
            templates = self.repo.fetch_all("SELECT * FROM angle_library_templates WHERE deleted_at IS NULL ORDER BY name LIMIT 12")
        template = rng.choice(templates[: max(1, min(6, len(templates)))])
        participant_count = max(int(template.get("min_participants") or 2), 2)
        participant_count = min(participant_count, int(template.get("max_participants") or participant_count), len(roster))
        preferred_ids = [item["id"] for item in opportunity]
        preferred = [w for wid in preferred_ids for w in roster if w["id"] == wid]
        participants = []
        used = set()
        for pool in (preferred, roster):
            for wrestler in pool:
                if wrestler["id"] in used:
                    continue
                participants.append(wrestler)
                used.add(wrestler["id"])
                if len(participants) >= participant_count:
                    break
            if len(participants) >= participant_count:
                break
        impact = {
            "heat_delta": 12 if template["risk_level"] == "high" else 7,
            "opportunity_delta": 10 if template["category"] == "opportunity" else 4,
            "mechanical_effects": self.repo.from_json(template.get("mechanical_effects_json"), []),
        }
        execution_id = new_id("angle_exec")
        now = self.now()
        self.conn.execute(
            """
            INSERT INTO angle_executions (
                id, template_id, template_name, show_id, year, week,
                participants_json, autonomy_status, impact_json, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'drafted', ?, ?, ?, NULL)
            """,
            (
                execution_id,
                template["id"],
                template["name"],
                show["show_id"],
                year,
                week,
                json.dumps([{"id": w["id"], "name": w["name"]} for w in participants]),
                json.dumps(impact),
                now,
                now,
            ),
        )
        self.conn.commit()
        segment = self._segment(
            f"angle_{template['id']}",
            template["segment_type"],
            5,
            7 if template["risk_level"] == "high" else 5,
            participants,
            f"{template['name']}: {', '.join(w['name'] for w in participants)} drives a live, AI-selected angle.",
        )
        segment["angle_template_id"] = template["id"]
        segment["angle_execution_id"] = execution_id
        segment["mechanical_effects"] = impact["mechanical_effects"]
        card["segments"].insert(-1, segment)
        for index, item in enumerate(card["segments"], start=1):
            item["position"] = index
        return {
            "id": execution_id,
            "template_id": template["id"],
            "template_name": template["name"],
            "category": template["category"],
            "risk_level": template["risk_level"],
            "participants": segment["participants"],
            "impact": impact,
        }

    def _refresh_mitb_system(self, year: int, week: int, roster: list[dict], opportunity: list[dict], rng: random.Random) -> dict:
        active = self._mitb_rows("planned", 10) + self._mitb_rows("active", 10)
        created = []
        if not active:
            for division, gender in (("mens", "Male"), ("womens", "Female")):
                candidates = [w for w in roster if str(w.get("gender", "")).lower() == gender.lower()]
                if not candidates:
                    candidates = roster
                opp_ids = [item["id"] for item in opportunity]
                candidates = sorted(candidates, key=lambda w: (0 if w["id"] in opp_ids else 1, -float(w.get("momentum") or 0), w["name"]))
                holder = candidates[0]
                row_id = f"mitb_{division}_y{year}"
                now = self.now()
                self.conn.execute(
                    """
                    INSERT INTO money_in_bank_briefcases (
                        id, division, holder_id, holder_name, status, won_year, won_week,
                        target_title_id, target_title_name, cash_in_window_weeks,
                        cash_in_attempts_json, next_ai_check_year, next_ai_check_week,
                        created_at, updated_at, deleted_at
                    ) VALUES (?, ?, ?, ?, 'planned', ?, ?, NULL, ?, 52, '[]', ?, ?, ?, ?, NULL)
                    ON CONFLICT(id) DO UPDATE SET holder_id = excluded.holder_id,
                        holder_name = excluded.holder_name, status = excluded.status,
                        next_ai_check_year = excluded.next_ai_check_year,
                        next_ai_check_week = excluded.next_ai_check_week,
                        updated_at = excluded.updated_at, deleted_at = NULL
                    """,
                    (row_id, division, holder["id"], holder["name"], year, week, f"{division.title()} World Championship", year, week + 2, now, now),
                )
                created.append({"id": row_id, "division": division, "holder_id": holder["id"], "holder_name": holder["name"], "status": "planned"})
            self.conn.commit()
        rows = self._mitb_rows(None, 10)
        cash_in_tease = None
        if rows and rng.random() < 0.45:
            chosen = rows[0]
            cash_in_tease = {
                "briefcase_id": chosen["id"],
                "holder_name": chosen["holder_name"],
                "division": chosen["division"],
                "tease": f"{chosen['holder_name']} circles the champion with a possible cash-in window.",
                "risk": "high",
            }
        return {"created": created, "briefcases": rows, "cash_in_tease": cash_in_tease}

    # ------------------------------------------------------------------
    # Money in the Bank - Fortune's Ladder PLE
    # ------------------------------------------------------------------

    def get_roster_snapshot(self, brand: str = "Cross-Brand") -> list[dict]:
        """Public wrapper around the internal active-roster lookup, for route use."""
        return self._active_roster(brand)

    def get_mitb_ladder_field(self, division: str, roster: list[dict], count: int = 6) -> list[dict]:
        """
        Build the 6-competitor field for a Money in the Bank ladder match.
        Favors wrestlers on the rise (opportunity/momentum) over established champions,
        matching the real-world booking logic of MITB being a launchpad, not a reward
        for wrestlers who are already at the top of the card.
        """
        gender = "Male" if division == "mens" else "Female"
        candidates = [w for w in roster if str(w.get("gender", "")).lower() == gender.lower()]
        if not candidates:
            candidates = roster
        candidates = sorted(
            candidates,
            key=lambda w: (-float(w.get("momentum") or 0), -float(w.get("popularity") or 0), w.get("name", "")),
        )
        field = candidates[:count]
        planned = next((row for row in self._mitb_rows("planned", 20) if row.get("division") == division), None)
        if planned and planned.get("holder_id") and not any(w.get("id") == planned["holder_id"] for w in field):
            holder = next((w for w in candidates if w.get("id") == planned["holder_id"]), None)
            if holder:
                if len(field) >= count and field:
                    field[-1] = holder
                else:
                    field.append(holder)
        return [{"id": w["id"], "name": w["name"]} for w in field[:count]]

    def _fortunes_ladder_event_for_year(self, universe, year: int) -> dict:
        """Locate this year's Fortune's Ladder PLE. Falls back to the default
        Week 16 slot from the calendar's standard schedule if the calendar
        hasn't generated that far out yet."""
        try:
            shows = universe.calendar.generated_shows
        except Exception:
            shows = []
        match = next(
            (s for s in shows if getattr(s, "year", None) == year and "fortune" in str(getattr(s, "name", "")).lower()),
            None,
        )
        if match:
            return {"show_id": getattr(match, "show_id", None), "name": match.name, "year": year, "week": match.week}
        return {"show_id": None, "name": "Fortune's Ladder", "year": year, "week": 16}

    def get_or_build_mitb_ladder_card(self, year: int, week: int, roster: list[dict], universe=None) -> dict:
        """
        Build (or return the already-persisted, possibly player-edited) 6-person
        Money in the Bank ladder fields for both divisions. Money in the Bank is
        exclusive to the Fortune's Ladder PLE - if the requested week isn't this
        year's Fortune's Ladder show, this refuses to build a card and instead
        reports when the real one is scheduled.
        """
        self._ensure_tables()
        if universe is not None:
            ladder_event = self._fortunes_ladder_event_for_year(universe, year)
            if int(ladder_event["week"]) != int(week):
                return {
                    "error": "money_in_the_bank_is_fortunes_ladder_exclusive",
                    "message": (
                        f"Money in the Bank ladder matches only happen at Fortune's Ladder. "
                        f"This year's Fortune's Ladder is Year {year}, Week {ladder_event['week']}."
                    ),
                    "fortunes_ladder_year": year,
                    "fortunes_ladder_week": ladder_event["week"],
                }
        divisions = {}
        for division in ("mens", "womens"):
            row = self.repo.fetch_one(
                "SELECT * FROM mitb_ladder_cards WHERE division = ? AND event_year = ? AND event_week = ? AND deleted_at IS NULL",
                (division, year, week),
            )
            if row:
                divisions[division] = {
                    "id": row["id"],
                    "status": row["status"],
                    "field": self.repo.from_json(row.get("field_json"), []),
                }
                continue
            field = self.get_mitb_ladder_field(division, roster)
            row_id = f"mitbcard_{division}_y{year}w{week}"
            now = self.now()
            self.conn.execute(
                """
                INSERT INTO mitb_ladder_cards (
                    id, division, event_year, event_week, status, field_json, created_at, updated_at, deleted_at
                ) VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, NULL)
                """,
                (row_id, division, year, week, json.dumps(field), now, now),
            )
            divisions[division] = {"id": row_id, "status": "draft", "field": field}
        self.conn.commit()
        return {"event_name": "Fortune's Ladder", "year": year, "week": week, "matches": divisions}

    def replace_mitb_card_participant(
        self, division: str, year: int, week: int, old_wrestler_id: str, new_wrestler_id: str, new_wrestler_name: str,
    ) -> dict:
        """Swap one wrestler out of a persisted (not-yet-simulated) MITB ladder field for another."""
        self._ensure_tables()
        row = self.repo.fetch_one(
            "SELECT * FROM mitb_ladder_cards WHERE division = ? AND event_year = ? AND event_week = ? AND deleted_at IS NULL",
            (division, year, week),
        )
        if not row:
            return {"error": "mitb_card_not_found"}
        field = self.repo.from_json(row.get("field_json"), [])
        replaced = False
        for entry in field:
            if entry.get("id") == old_wrestler_id:
                entry["id"] = new_wrestler_id
                entry["name"] = new_wrestler_name
                replaced = True
                break
        if not replaced:
            return {"error": "wrestler_not_in_field"}
        now = self.now()
        self.conn.execute(
            "UPDATE mitb_ladder_cards SET field_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(field), now, row["id"]),
        )
        self.conn.commit()
        return {"division": division, "field": field}

    def confirm_mitb_card(self, year: int, week: int) -> dict:
        """Lock in both divisions' MITB ladder fields once the player is happy with them."""
        self._ensure_tables()
        now = self.now()
        confirmed = []
        for division in ("mens", "womens"):
            row = self.repo.fetch_one(
                "SELECT * FROM mitb_ladder_cards WHERE division = ? AND event_year = ? AND event_week = ? AND deleted_at IS NULL",
                (division, year, week),
            )
            if row:
                self.conn.execute(
                    "UPDATE mitb_ladder_cards SET status = 'confirmed', updated_at = ? WHERE id = ?", (now, row["id"])
                )
                confirmed.append(division)
        self.conn.commit()
        return {"confirmed_divisions": confirmed, "year": year, "week": week}

    def book_fortunes_ladder_card(self, year: int, week: int, roster: list[dict], universe=None) -> dict:
        """Backwards-compatible alias - now backed by the persisted/editable card."""
        return self.get_or_build_mitb_ladder_card(year, week, roster, universe)

    def resolve_mitb_winner(
        self,
        division: str,
        winner_id: str,
        winner_name: str,
        year: int,
        week: int,
        cash_in_window_weeks: int = 52,
    ) -> dict:
        """
        Called after the ladder match is simulated. Awards the briefcase to the
        winner, superseding any prior briefcase for that division (the old
        contract's window has lapsed - a new Fortune's Ladder means a new holder).
        """
        self._ensure_tables()
        now = self.now()
        self.conn.execute(
            """
            UPDATE money_in_bank_briefcases
            SET status = 'expired', updated_at = ?
            WHERE division = ? AND status IN ('planned', 'active') AND deleted_at IS NULL
            """,
            (now, division),
        )
        row_id = f"mitb_{division}_y{year}w{week}"
        self.conn.execute(
            """
            INSERT INTO money_in_bank_briefcases (
                id, division, holder_id, holder_name, status, won_year, won_week,
                target_title_id, target_title_name, cash_in_window_weeks,
                cash_in_attempts_json, next_ai_check_year, next_ai_check_week,
                created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, 'active', ?, ?, NULL, NULL, ?, '[]', ?, ?, ?, ?, NULL)
            ON CONFLICT(id) DO UPDATE SET holder_id = excluded.holder_id,
                holder_name = excluded.holder_name, status = 'active',
                won_year = excluded.won_year, won_week = excluded.won_week,
                cash_in_window_weeks = excluded.cash_in_window_weeks,
                updated_at = excluded.updated_at, deleted_at = NULL
            """,
            (row_id, division, winner_id, winner_name, year, week, cash_in_window_weeks, year, week + 2, now, now),
        )
        self.conn.commit()
        return self._decode_mitb_row(self.repo.fetch_one("SELECT * FROM money_in_bank_briefcases WHERE id = ?", (row_id,)))

    def schedule_mitb_cash_in(
        self, briefcase_id: str, target_title_id: str, target_title_name: str, scheduled_year: int, scheduled_week: int,
    ) -> dict:
        """
        Schedule an active briefcase to be cashed in on a specific singles title
        during a specific future week, instead of resolving it immediately.
        The AI Showrunner will notify the player as that week approaches and
        book the corresponding Championship match as that show's main event.
        """
        self._ensure_tables()
        row = self.repo.fetch_one(
            "SELECT * FROM money_in_bank_briefcases WHERE id = ? AND deleted_at IS NULL", (briefcase_id,)
        )
        if not row:
            return {"error": "briefcase_not_found"}
        if row["status"] != "active":
            return {"error": f"briefcase_not_active (status={row['status']})"}
        now = self.now()
        self.conn.execute(
            """
            UPDATE money_in_bank_briefcases
            SET status = 'cash_in_scheduled', target_title_id = ?, target_title_name = ?,
                next_ai_check_year = ?, next_ai_check_week = ?, updated_at = ?
            WHERE id = ?
            """,
            (target_title_id, target_title_name, scheduled_year, scheduled_week, now, briefcase_id),
        )
        self.conn.commit()
        return self._decode_mitb_row(
            self.repo.fetch_one("SELECT * FROM money_in_bank_briefcases WHERE id = ?", (briefcase_id,))
        )

    def _check_scheduled_mitb_cash_ins(self, year: int, week: int, universe, rng: random.Random) -> list[dict]:
        """
        Watch scheduled cash-ins and flag the ones now two weeks or fewer out,
        so the approval queue can notify the player and the Championship
        match can get booked as that show's main event ahead of time.
        """
        self._ensure_tables()
        approaching = []
        rows = self.repo.fetch_all(
            "SELECT * FROM money_in_bank_briefcases WHERE status = 'cash_in_scheduled' AND deleted_at IS NULL"
        )
        total_now = (year - 1) * 52 + week
        for row in rows:
            target_year = row.get("next_ai_check_year") or year
            target_week = row.get("next_ai_check_week") or week
            total_target = (target_year - 1) * 52 + target_week
            weeks_out = total_target - total_now
            if 0 <= weeks_out <= 2:
                show = self._find_show_for_week(universe, target_year, target_week)
                approaching.append({
                    "briefcase_id": row["id"],
                    "division": row["division"],
                    "holder_id": row["holder_id"],
                    "holder_name": row["holder_name"],
                    "target_title_id": row["target_title_id"],
                    "target_title_name": row["target_title_name"],
                    "cash_in_year": target_year,
                    "cash_in_week": target_week,
                    "show_name": show.get("name"),
                    "show_id": show.get("show_id"),
                    "weeks_out": weeks_out,
                })
                self.conn.execute(
                    "UPDATE money_in_bank_briefcases SET status = 'cash_in_notified', updated_at = ? WHERE id = ?",
                    (self.now(), row["id"]),
                )
        if approaching:
            self.conn.commit()
        return approaching

    def _materialize_mitb_cash_in_decision(self, cash_in: dict, approved: bool) -> dict:
        """
        Resolve a notified cash-in: approved -> the briefcase holder wins the
        title in the booked main event and is auto-drafted to that title's
        brand; rejected -> the AI's plan flips and the cash-in fails (champion
        retains), exactly like the King/Queen of the Ring payoff flow.
        """
        if not cash_in or not cash_in.get("briefcase_id"):
            return {"error": "no cash-in to materialize"}
        result = self.cash_in_mitb_briefcase(
            cash_in["briefcase_id"],
            cash_in["target_title_id"],
            cash_in["target_title_name"],
            wrestler_won=approved,
            year=cash_in.get("cash_in_year"),
            week=cash_in.get("cash_in_week"),
            show_id=cash_in.get("show_id"),
            show_name=cash_in.get("show_name") or "Cash-In",
        )
        if approved and cash_in.get("target_title_id"):
            title_row = self.repo.fetch_one("SELECT * FROM championships WHERE id = ?", (cash_in["target_title_id"],))
            if title_row and title_row.get("assigned_brand"):
                self.conn.execute(
                    "UPDATE wrestlers SET primary_brand = ? WHERE id = ?",
                    (title_row["assigned_brand"], cash_in["holder_id"]),
                )
                self.conn.commit()
                result["drafted_to_brand"] = title_row["assigned_brand"]
        return result

    def _append_scheduled_cash_in_matches_to_draft(self, draft: dict) -> None:
        """Inject the booked main-event Championship match for any MITB cash-in
        notified for this exact show/week, so it shows up without the player
        needing to send it to Booking manually."""
        self._ensure_tables()
        rows = self.repo.fetch_all(
            "SELECT * FROM money_in_bank_briefcases WHERE status = 'cash_in_notified' AND deleted_at IS NULL"
        )
        for row in rows:
            same_week = (
                int(row.get("next_ai_check_year") or 0) == int(draft.get("year") or 0)
                and int(row.get("next_ai_check_week") or 0) == int(draft.get("week") or 0)
            )
            if not same_week:
                continue
            title_row = self.repo.fetch_one("SELECT * FROM championships WHERE id = ?", (row["target_title_id"],))
            if not title_row:
                continue
            champion_id = title_row.get("current_holder_id")
            champion_name = title_row.get("current_holder_name")
            if not champion_id:
                continue
            match_id = f"cashin_{row['id']}"
            if any(m.get("match_id") == match_id for m in draft.get("matches", [])):
                continue
            draft.setdefault("matches", []).append({
                "match_id": match_id,
                "match_type": "singles",
                "side_a": {"wrestler_ids": [champion_id], "wrestler_names": [champion_name], "is_tag_team": False},
                "side_b": {"wrestler_ids": [row["holder_id"]], "wrestler_names": [row["holder_name"]], "is_tag_team": False},
                "booked_winner": champion_id,
                "booking_bias": "even",
                "importance": "main_event",
                "is_title_match": True,
                "allow_cross_brand": True,
                "ai_description": (
                    f"Main Event - {champion_name} defends the {title_row['name']} against a #1 contender, "
                    f"but {row['holder_name']}'s Money in the Bank cash-in looms right after the bell."
                ),
                "special_stipulation": "mitb_cash_in_main_event",
            })

    def list_mitb_briefcases(self, status: str | None = None, limit: int = 10) -> list[dict]:
        self._ensure_tables()
        return self._mitb_rows(status, limit)

    def cash_in_mitb_briefcase(
        self,
        briefcase_id: str,
        target_title_id: str,
        target_title_name: str,
        wrestler_won: bool,
        year: int,
        week: int,
        show_id: str | None = None,
        show_name: str | None = None,
    ) -> dict:
        """
        Record a cash-in attempt against a singles champion. Per Money in the
        Bank convention, the contract is spent on the attempt regardless of
        outcome - win or lose, the briefcase is gone.
        """
        self._ensure_tables()
        row = self.repo.fetch_one(
            "SELECT * FROM money_in_bank_briefcases WHERE id = ? AND deleted_at IS NULL", (briefcase_id,)
        )
        if not row:
            return {"error": "briefcase_not_found"}
        if row["status"] not in ("active", "cash_in_scheduled", "cash_in_notified"):
            return {"error": f"briefcase_not_active (status={row['status']})"}

        attempts = self.repo.from_json(row.get("cash_in_attempts_json"), [])
        attempts.append(
            {
                "target_title_id": target_title_id,
                "target_title_name": target_title_name,
                "wrestler_won": bool(wrestler_won),
                "year": year,
                "week": week,
                "show_id": show_id,
                "show_name": show_name,
                "recorded_at": self.now(),
            }
        )
        new_status = "cashed_in_won" if wrestler_won else "cashed_in_failed"
        now = self.now()
        self.conn.execute(
            """
            UPDATE money_in_bank_briefcases
            SET status = ?, target_title_id = ?, target_title_name = ?,
                cash_in_attempts_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_status, target_title_id, target_title_name, self.repo.to_json(attempts), now, briefcase_id),
        )
        title_change = None
        if wrestler_won:
            title_row = self.repo.fetch_one("SELECT * FROM championships WHERE id = ?", (target_title_id,))
            if title_row:
                from models.championship import calculate_reign_days

                previous_holder_id = title_row.get("current_holder_id")
                if previous_holder_id:
                    open_reign = self.repo.fetch_one(
                        """
                        SELECT * FROM title_reigns
                        WHERE title_id = ? AND wrestler_id = ? AND lost_date_year IS NULL
                        ORDER BY won_date_year DESC, won_date_week DESC LIMIT 1
                        """,
                        (target_title_id, previous_holder_id),
                    )
                    if open_reign:
                        days_held = calculate_reign_days(
                            open_reign["won_date_year"], open_reign["won_date_week"], open_reign.get("won_at_show_name"),
                            year, week, show_name or "Cash-In",
                        )
                        self.conn.execute(
                            """
                            UPDATE title_reigns
                            SET lost_at_show_name = ?, lost_date_year = ?, lost_date_week = ?, days_held = ?
                            WHERE id = ?
                            """,
                            (show_name or "Cash-In", year, week, days_held, open_reign["id"]),
                        )
                self.conn.execute(
                    """
                    INSERT INTO title_reigns (
                        title_id, wrestler_id, wrestler_name, won_at_show_id, won_at_show_name,
                        won_date_year, won_date_week, days_held, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (target_title_id, row["holder_id"], row["holder_name"], show_id, show_name or "Cash-In", year, week, now),
                )
                self.conn.execute(
                    """
                    UPDATE championships
                    SET current_holder_id = ?, current_holder_name = ?, total_defenses = 0,
                        last_defense_year = ?, last_defense_week = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (row["holder_id"], row["holder_name"], year, week, now, target_title_id),
                )
                title_change = {"title_id": target_title_id, "title_name": title_row["name"], "new_champion": row["holder_name"]}
            # else: champion retains, logged as a successful defense below.
        elif target_title_id:
            self.conn.execute(
                """
                UPDATE championships
                SET total_defenses = COALESCE(total_defenses, 0) + 1,
                    last_defense_year = ?, last_defense_week = ?, updated_at = ?
                WHERE id = ?
                """,
                (year, week, now, target_title_id),
            )
        self.conn.commit()
        result = self._decode_mitb_row(
            self.repo.fetch_one("SELECT * FROM money_in_bank_briefcases WHERE id = ?", (briefcase_id,))
        )
        if title_change:
            result["title_change"] = title_change
        return result

    def _decode_mitb_row(self, row: dict | None) -> dict:
        if not row:
            return {}
        row = dict(row)
        row["cash_in_attempts_json"] = self.repo.from_json(row.get("cash_in_attempts_json"), [])
        return row

    # ------------------------------------------------------------------
    # WarGames
    # ------------------------------------------------------------------

    def get_or_build_war_games_plan(self, year: int, week: int, roster: list[dict], universe, rng: random.Random | None = None) -> dict:
        """Fetch the existing WarGames plan for the upcoming target event, or build one."""
        self._ensure_tables()
        return self._refresh_war_games_system(year, week, roster, universe, rng or random.Random())

    def _full_war_games_depth_available(self, roster: list[dict]) -> bool:
        return all(
            len([w for w in roster if str(w.get("gender", "")).lower() == gender]) >= 10
            for gender in ("male", "female")
        )

    def _war_games_plan_is_full_gender_split(self, plan: dict) -> bool:
        divisions = plan.get("divisions_json") or plan.get("divisions") or {}
        for division_key, gender in (("mens", "male"), ("womens", "female")):
            division = divisions.get(division_key) or {}
            for side_key in ("team_a", "team_b"):
                team = division.get(side_key) or []
                if len(team) != 5:
                    return False
                if any(str(member.get("gender", "")).lower() != gender for member in team):
                    return False
        return True

    def _delete_invalid_war_games_plan(self, plan_id: str) -> None:
        self.conn.execute("DELETE FROM war_games_plans WHERE id = ?", (plan_id,))
        self.conn.commit()

    def _row_to_wrestler(self, row: dict):
        from models.wrestler import Wrestler
        return Wrestler(
            wrestler_id=row["id"],
            name=row["name"],
            age=int(row.get("age") or 25),
            gender=row.get("gender") or "Male",
            alignment=row.get("alignment") or "Face",
            role=row.get("role") or "Midcard",
            primary_brand=row.get("primary_brand") or "Cross-Brand",
            brawling=int(row.get("brawling") or 50),
            technical=int(row.get("technical") or 50),
            speed=int(row.get("speed") or 50),
            mic=int(row.get("mic") or 50),
            psychology=int(row.get("psychology") or 50),
            stamina=int(row.get("stamina") or 50),
            years_experience=int(row.get("years_experience") or 5),
            is_major_superstar=bool(row.get("is_major_superstar") or False),
            popularity=int(row.get("popularity") or 50),
            momentum=int(row.get("momentum") or 0),
            morale=int(row.get("morale") or 50),
            fatigue=int(row.get("fatigue") or 0),
        )

    def simulate_war_games_plan(
        self,
        plan: dict,
        roster: list[dict],
        booked_winner_side: str | None = None,
        injured_wrestler_id: str | None = None,
        unlikely_allies: list[str] | None = None,
    ) -> dict:
        """
        Run a booked WarGames plan (factions + advantage holder) through the
        dedicated WarGamesSimulator, then persist the result onto the plan row.
        """
        from simulation.war_games_sim import war_games_simulator

        self._ensure_war_games_result_column()
        roster_by_id = {w["id"]: w for w in roster}

        faction_a = [self._row_to_wrestler(roster_by_id[m["id"]]) for m in plan.get("faction_a_json", []) if m["id"] in roster_by_id]
        faction_b = [self._row_to_wrestler(roster_by_id[m["id"]]) for m in plan.get("faction_b_json", []) if m["id"] in roster_by_id]
        if not faction_a or not faction_b:
            return {"error": "one or both WarGames factions could not be resolved against the current roster"}

        advantage_holder_id = (plan.get("advantage_holder_json") or {}).get("id")
        advantage_side = "a" if any(w.id == advantage_holder_id for w in faction_a) else "b"

        wg_result = war_games_simulator.simulate_war_games(
            side_a=faction_a,
            side_b=faction_b,
            advantage_side=advantage_side,
            booked_winner_side=booked_winner_side,
            injured_wrestler_id=injured_wrestler_id,
            unlikely_allies=unlikely_allies,
        )

        summary = {
            "winner_side": wg_result["winner_side"],
            "winning_team": [{"id": w.id, "name": w.name} for w in wg_result["winning_team"]],
            "losing_team": [{"id": w.id, "name": w.name} for w in wg_result["losing_team"]],
            "highlights": [
                {"timestamp": h.timestamp, "description": h.description, "type": h.highlight_type}
                for h in wg_result["highlights"]
            ],
            "momentum": wg_result["momentum"],
            "dramatic_beats_hit": wg_result["dramatic_beats_hit"],
            "duration_minutes": wg_result["duration_minutes"],
            "star_rating_bonus": wg_result["star_rating_bonus"],
            "resolved_at": self.now(),
        }

        now = self.now()
        self.conn.execute(
            "UPDATE war_games_plans SET status = 'resolved', result_json = ?, updated_at = ? WHERE id = ?",
            (self.repo.to_json(summary), now, plan["id"]),
        )
        self.conn.commit()
        return summary

    def _ensure_war_games_result_column(self):
        try:
            self.conn.execute("ALTER TABLE war_games_plans ADD COLUMN result_json TEXT")
            self.conn.commit()
        except Exception:
            pass  # column already exists

    def list_war_games_plans(self, limit: int = 5) -> list[dict]:
        """
        List upcoming/recent WarGames plans. Any legacy plan that predates the
        gender-separated divisions structure (or was saved as a single mixed
        pool) is self-healed here so the page-load panel never shows stale
        mixed-gender/4-per-team data - it reflects the current roster's
        proper men's/women's split whenever there's enough roster depth.
        """
        self._ensure_tables()
        self._ensure_war_games_result_column()
        rows = self.repo.fetch_all(
            "SELECT * FROM war_games_plans WHERE deleted_at IS NULL ORDER BY target_year, target_week LIMIT ?",
            (limit,),
        )
        roster = None
        plans = []
        for row in rows:
            plan = self._decode_war_games(row)
            result = self.repo.from_json(row.get("result_json"), None)
            if result is None and not self._war_games_plan_is_full_gender_split(plan):
                if roster is None:
                    roster = self.get_roster_snapshot("Cross-Brand")
                if self._full_war_games_depth_available(roster):
                    self._delete_invalid_war_games_plan(plan["id"])
                    plan = self._build_war_games_plan_row(
                        plan["target_event_name"], plan["target_year"], plan["target_week"],
                        plan.get("year") or plan["target_year"], plan.get("week") or plan["target_week"],
                        roster, random.Random(),
                    )
                    result = None
            plan["result"] = result
            plans.append(plan)
        return plans

    # ------------------------------------------------------------------
    # King of the Ring / Queen of the Ring
    # NOT a standalone PLE. Quarterfinals + Semifinals run as segments on
    # ordinary weekly shows; the Final lands on a minor-tier PLE (or a
    # weekly show if none is available) well before Summer Slamfest.
    # ------------------------------------------------------------------

    CROWN_TOURNAMENT_NAMES = {"king": "King of the Ring", "queen": "Queen of the Ring"}

    def _find_show_for_week(self, universe, year: int, week: int) -> dict:
        """Best-effort lookup of whatever show is scheduled at a given year/week."""
        try:
            shows = universe.calendar.generated_shows
        except Exception:
            shows = []
        matches = [s for s in shows if getattr(s, "year", None) == year and getattr(s, "week", None) == week]
        if matches:
            s = matches[0]
            return {
                "show_id": getattr(s, "show_id", None),
                "name": getattr(s, "name", f"Week {week} Show"),
                "year": year,
                "week": week,
                "tier": getattr(s, "tier", "weekly"),
            }
        return {"show_id": None, "name": f"Weekly Show (Wk {week})", "year": year, "week": week, "tier": "weekly"}

    def _next_minor_ple(self, universe, year: int, after_week: int, before_week: int = 32) -> dict:
        """Earliest minor-tier PLE strictly between after_week and before_week.
        Falls back to a plain weekly show if no minor PLE fits the window -
        the tournament final is never forced onto its own dedicated PLE."""
        try:
            shows = universe.calendar.generated_shows
        except Exception:
            shows = []
        candidates = sorted(
            [
                s for s in shows
                if getattr(s, "year", None) == year
                and getattr(s, "tier", None) == "minor"
                and getattr(s, "is_ppv", False)
                and after_week < getattr(s, "week", 0) < before_week
            ],
            key=lambda s: s.week,
        )
        if candidates:
            s = candidates[0]
            return {"show_id": getattr(s, "show_id", None), "name": s.name, "year": year, "week": s.week, "tier": "minor"}
        fallback_week = min(before_week - 2, after_week + 6)
        return self._find_show_for_week(universe, year, fallback_week)

    def build_crown_tournament(
        self,
        tournament_type: str,
        year: int,
        week: int,
        roster: list[dict],
        universe,
        rng: random.Random | None = None,
    ) -> dict:
        """
        Build an 8-competitor single-elimination King/Queen of the Ring
        bracket: QF1-4 -> SF1 (QF1 winner vs QF3 winner), SF2 (QF2 winner
        vs QF4 winner) -> Final. All rounds are scheduled onto existing
        weekly shows or minor PLEs - never a dedicated event of its own -
        and the Final always lands before Summer Slamfest.
        """
        if tournament_type not in ("king", "queen"):
            return {"error": "tournament_type must be 'king' or 'queen'"}
        self._ensure_tables()
        rng = rng or random.Random()
        name = self.CROWN_TOURNAMENT_NAMES[tournament_type]
        gender = "Male" if tournament_type == "king" else "Female"

        existing = self.repo.fetch_one(
            "SELECT * FROM tournaments WHERE name = ? AND start_year = ? AND status != 'completed' AND deleted_at IS NULL",
            (name, year),
        )
        if existing:
            return self._decode_crown_tournament(existing)

        candidates = [w for w in roster if str(w.get("gender", "")).lower() == gender.lower()]
        candidates = sorted(candidates, key=lambda w: (-float(w.get("overall") or 0), -float(w.get("popularity") or 0)))[:8]
        if len(candidates) < 8:
            return {"error": f"not enough {gender} roster members for an 8-competitor bracket (have {len(candidates)}, need 8)"}

        qf_week = min(51, week + 2)
        sf_week = min(51, week + 6)
        final_info = self._next_minor_ple(universe, year, sf_week, before_week=32)

        qf_shows = [self._find_show_for_week(universe, year, min(51, qf_week + (i % 2))) for i in range(4)]
        sf_shows = [self._find_show_for_week(universe, year, min(51, sf_week + (i % 2))) for i in range(2)]

        seeds = [{"id": w["id"], "name": w["name"]} for w in candidates]
        rounds = [
            {
                "round": 1,
                "round_name": "Quarterfinal",
                "matches": [
                    {
                        "match_num": i + 1,
                        "wrestler_a": seeds[i * 2],
                        "wrestler_b": seeds[i * 2 + 1],
                        "winner": None,
                        "scheduled": qf_shows[i],
                    }
                    for i in range(4)
                ],
            },
            {
                "round": 2,
                "round_name": "Semifinal",
                "matches": [
                    {"match_num": 1, "wrestler_a": None, "wrestler_b": None, "winner": None,
                     "feeds_from": [1, 3], "scheduled": sf_shows[0]},
                    {"match_num": 2, "wrestler_a": None, "wrestler_b": None, "winner": None,
                     "feeds_from": [2, 4], "scheduled": sf_shows[1]},
                ],
            },
            {
                "round": 3,
                "round_name": "Final",
                "matches": [
                    {"match_num": 1, "wrestler_a": None, "wrestler_b": None, "winner": None,
                     "feeds_from": [1, 2], "scheduled": final_info},
                ],
            },
        ]
        bracket = {"tournament_type": tournament_type, "division": gender, "rounds": rounds}

        tournament_id = new_id("tournament")
        now = self.now()
        self.conn.execute(
            """
            INSERT INTO tournaments (
                id, name, prize_type, prize_description, format, participant_count,
                status, duration_shows, seeding_logic, narrative_arc_score, bracket_json,
                start_year, start_week, created_at, updated_at, deleted_at
            ) VALUES (?, ?, 'title_opportunity', ?, 'single_elimination', 8, 'active', 3,
                      'ranking', 50, ?, ?, ?, ?, ?, NULL)
            """,
            (
                tournament_id, name,
                f"Winner receives a guaranteed {gender.lower()} World Title opportunity at Summer Slamfest",
                self.repo.to_json(bracket), year, week, now, now,
            ),
        )
        for seed, w in enumerate(candidates, start=1):
            self.conn.execute(
                """
                INSERT INTO tournament_entries (
                    id, tournament_id, wrestler_id, wrestler_name, seed,
                    eliminated, current_round, wins, losses, created_at, updated_at, deleted_at
                ) VALUES (?, ?, ?, ?, ?, 0, 1, 0, 0, ?, ?, NULL)
                """,
                (new_id("tournament_entry"), tournament_id, w["id"], w["name"], seed, now, now),
            )
        for match in rounds[0]["matches"]:
            self.conn.execute(
                """
                INSERT INTO tournament_matches (
                    id, tournament_id, show_id, round_number, bracket_position,
                    wrestler_a_id, wrestler_b_id, winner_id, status, created_at, updated_at, deleted_at
                ) VALUES (?, ?, ?, 1, ?, ?, ?, NULL, 'scheduled', ?, ?, NULL)
                """,
                (
                    new_id("tournament_match"), tournament_id, match["scheduled"].get("show_id"),
                    match["match_num"], match["wrestler_a"]["id"], match["wrestler_b"]["id"], now, now,
                ),
            )
        self.conn.commit()
        return self._decode_crown_tournament(self.repo.fetch_one("SELECT * FROM tournaments WHERE id = ?", (tournament_id,)))

    def _decode_crown_tournament(self, row: dict | None) -> dict:
        if not row:
            return {}
        row = dict(row)
        row["bracket"] = self.repo.from_json(row.get("bracket_json"), {})
        return row

    def list_crown_tournaments(self, limit: int = 5) -> list[dict]:
        self._ensure_tables()
        rows = self.repo.fetch_all(
            "SELECT * FROM tournaments WHERE name IN (?, ?) AND deleted_at IS NULL ORDER BY start_year DESC, start_week DESC LIMIT ?",
            (self.CROWN_TOURNAMENT_NAMES["king"], self.CROWN_TOURNAMENT_NAMES["queen"], limit),
        )
        return [self._decode_crown_tournament(row) for row in rows]

    def resolve_crown_tournament_match(
        self,
        tournament_id: str,
        round_number: int,
        match_num: int,
        winner_id: str,
        winner_name: str,
        universe=None,
    ) -> dict:
        """
        Record a QF/SF/Final result, auto-advance the winner into the next
        round's bracket slot, and (when the Final resolves) award the
        Summer Slamfest title opportunity via crown_tournament_payoffs.
        """
        self._ensure_tables()
        row = self.repo.fetch_one("SELECT * FROM tournaments WHERE id = ? AND deleted_at IS NULL", (tournament_id,))
        if not row:
            return {"error": "tournament_not_found"}
        bracket = self.repo.from_json(row.get("bracket_json"), {})
        rounds = bracket.get("rounds", [])
        target_round = next((r for r in rounds if r["round"] == round_number), None)
        if not target_round:
            return {"error": f"round {round_number} not found"}
        target_match = next((m for m in target_round["matches"] if m["match_num"] == match_num), None)
        if not target_match:
            return {"error": f"match {match_num} not found in round {round_number}"}
        participant_ids = {p["id"] for p in (target_match.get("wrestler_a"), target_match.get("wrestler_b")) if p}
        if winner_id not in participant_ids:
            return {"error": f"winner_id {winner_id} is not one of this match's participants"}

        target_match["winner"] = {"id": winner_id, "name": winner_name}

        # Propagate into the next round, if any
        next_round = next((r for r in rounds if r["round"] == round_number + 1), None)
        if next_round:
            for m in next_round["matches"]:
                feeds = m.get("feeds_from", [])
                if match_num in feeds:
                    slot = "wrestler_a" if feeds.index(match_num) == 0 else "wrestler_b"
                    m[slot] = {"id": winner_id, "name": winner_name}

        now = self.now()
        is_final = round_number == max(r["round"] for r in rounds)
        status = "completed" if is_final else row["status"]
        self.conn.execute(
            "UPDATE tournaments SET bracket_json = ?, status = ?, updated_at = ? WHERE id = ?",
            (self.repo.to_json(bracket), status, now, tournament_id),
        )
        self.conn.execute(
            """
            UPDATE tournament_matches SET winner_id = ?, status = 'completed', updated_at = ?
            WHERE tournament_id = ? AND round_number = ? AND bracket_position = ?
            """,
            (winner_id, now, tournament_id, round_number, match_num),
        )
        self.conn.commit()

        result = {"tournament_id": tournament_id, "round_number": round_number, "match_num": match_num,
                   "winner_id": winner_id, "winner_name": winner_name, "tournament_completed": is_final}

        if is_final:
            tournament_type = bracket.get("tournament_type")
            division = "mens" if tournament_type == "king" else "womens"
            payoff = self.record_crown_tournament_winner(
                tournament_type, division, winner_id, winner_name, row["start_year"], row["start_week"], universe,
            )
            result["title_opportunity"] = payoff

        return result

    def record_crown_tournament_winner(
        self,
        tournament_type: str,
        division: str,
        winner_id: str,
        winner_name: str,
        year: int,
        week: int,
        universe=None,
    ) -> dict:
        """Award the crowned winner their guaranteed World Title opportunity at Summer Slamfest."""
        self._ensure_tables()
        target_name, target_year, target_week = "Summer Slamfest", year, min(52, week + 10)
        if universe is not None:
            target_name, target_year, target_week = self._target_event(
                universe, year, week, ["Summer"], fallback_name="Summer Slamfest"
            )
        row_id = f"crown_{tournament_type}_{year}"
        now = self.now()
        self.conn.execute(
            """
            INSERT INTO crown_tournament_payoffs (
                id, tournament_type, division, winner_id, winner_name, status,
                won_year, won_week, payoff_event_name, payoff_year, payoff_week,
                title_shot_json, coronation_angle_json, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, 'planned', ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(id) DO UPDATE SET winner_id = excluded.winner_id, winner_name = excluded.winner_name,
                won_year = excluded.won_year, won_week = excluded.won_week,
                payoff_event_name = excluded.payoff_event_name, payoff_year = excluded.payoff_year,
                payoff_week = excluded.payoff_week, updated_at = excluded.updated_at
            """,
            (
                row_id, tournament_type, division, winner_id, winner_name, year, week,
                target_name, target_year, target_week,
                json.dumps({"guaranteed": True, "target": f"{division.title()} world title", "can_be_countered": True}),
                json.dumps({"angle": "coronation promo interrupted", "risk": "crowd rejection if winner is over-pushed"}),
                now, now,
            ),
        )
        self.conn.commit()
        return self._decode_crown(self.repo.fetch_one("SELECT * FROM crown_tournament_payoffs WHERE id = ?", (row_id,)))

    def _refresh_war_games_system(self, year: int, week: int, roster: list[dict], universe, rng: random.Random) -> dict:
        target = self._target_event(universe, year, week, ["Autumn", "Night of Glory", "Survival"], fallback_name="War Games")
        target_name, target_year, target_week = target
        existing = self.repo.fetch_one(
            "SELECT * FROM war_games_plans WHERE target_event_name = ? AND target_year = ? AND target_week = ? AND deleted_at IS NULL",
            (target_name, target_year, target_week),
        )
        if existing:
            decoded_existing = self._decode_war_games(existing)
            if not (self._full_war_games_depth_available(roster) and not self._war_games_plan_is_full_gender_split(decoded_existing)):
                return decoded_existing
            self._delete_invalid_war_games_plan(decoded_existing["id"])
        return self._build_war_games_plan_row(target_name, target_year, target_week, year, week, roster, rng)

    def _build_war_games_plan_row(
        self, target_name: str, target_year: int, target_week: int,
        year: int, week: int, roster: list[dict], rng: random.Random,
    ) -> dict:
        """
        Build (insert) a fresh WarGames plan row for the given target event.
        Always keeps the men's and women's pools completely separate - never
        falls back to a single mixed-gender pool. Each division gets its own
        Team A / Team B of up to 5 wrestlers (as few as 3 per side if roster
        depth for that gender is thin), and a division is only included if
        both sides can field at least 3 wrestlers.
        """
        ranked = sorted(roster, key=lambda w: (w.get("overall", 0), w.get("popularity", 0)), reverse=True)
        divisions = {}
        flat_teams = []
        for division_key, gender in (("mens", "Male"), ("womens", "Female")):
            pool = [w for w in ranked if str(w.get("gender", "")).lower() == gender.lower()]
            team_size = 5 if len(pool) >= 10 else min(5, max(3, len(pool) // 2))
            team_a = pool[:team_size]
            team_b = pool[team_size:team_size * 2]
            if len(team_a) >= 3 and len(team_b) >= 3:
                divisions[division_key] = {
                    "division": gender,
                    "team_a": [{"id": w["id"], "name": w["name"], "gender": w.get("gender")} for w in team_a],
                    "team_b": [{"id": w["id"], "name": w["name"], "gender": w.get("gender")} for w in team_b],
                }
                flat_teams.extend(team_a + team_b)
        if divisions.get("mens"):
            faction_a = divisions["mens"]["team_a"]
            faction_b = divisions["mens"]["team_b"]
        elif divisions.get("womens"):
            faction_a = divisions["womens"]["team_a"]
            faction_b = divisions["womens"]["team_b"]
        else:
            # Not enough depth in either gender for a proper division match.
            # Still never mix genders within a side: fall back to whichever
            # single gender has the most bodies available.
            male_pool = [w for w in ranked if str(w.get("gender", "")).lower() == "male"]
            female_pool = [w for w in ranked if str(w.get("gender", "")).lower() == "female"]
            pool = male_pool if len(male_pool) >= len(female_pool) else female_pool
            team_size = min(4, max(2, len(pool) // 2))
            faction_a = [{"id": w["id"], "name": w["name"], "gender": w.get("gender")} for w in pool[:team_size]]
            faction_b = [{"id": w["id"], "name": w["name"], "gender": w.get("gender")} for w in pool[team_size:team_size * 2]]
        advantage_pool = flat_teams or (faction_a + faction_b) or ranked
        advantage = rng.choice(advantage_pool) if advantage_pool else None
        beats = [
            "Week 1: locker-room pull-apart",
            "Week 2: advantage match",
            "Week 3: final team reveal",
            "PLE: War Games cage payoff",
        ]
        row_id = f"wargames_{target_year}_{target_week}_{self._slug(target_name)}"
        now = self.now()
        self.conn.execute(
            """
            INSERT INTO war_games_plans (
                id, year, week, target_event_name, target_year, target_week, status,
                faction_a_json, faction_b_json, advantage_holder_json, stakes_json,
                escalation_beats_json, divisions_json, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'building', ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                row_id, year, week, target_name, target_year, target_week,
                json.dumps(faction_a),
                json.dumps(faction_b),
                json.dumps({"id": advantage["id"], "name": advantage["name"]} if advantage else {}),
                json.dumps({"prize": "brand control advantage", "risk": "faction fracture if mishandled"}),
                json.dumps(beats),
                json.dumps(divisions),
                now,
                now,
            ),
        )
        self.conn.commit()
        return self._decode_war_games(self.repo.fetch_one("SELECT * FROM war_games_plans WHERE id = ?", (row_id,)))

    def _find_world_title_for_division(self, division: str) -> dict | None:
        """Pick the flagship World title for a division (mens/womens). When several
        World-type titles exist for a gender, the highest-prestige one is the
        one a King/Queen of the Ring winner is considered to be chasing."""
        rows = self.repo.fetch_all("SELECT * FROM championships WHERE title_type = 'World'")
        is_womens = division == "womens"
        candidates = [
            r for r in rows
            if ("women" in str(r.get("name", "")).lower()) == is_womens
        ]
        if not candidates:
            candidates = rows
        if not candidates:
            return None
        return max(candidates, key=lambda r: float(r.get("prestige") or 0))

    def _materialize_crown_payoff_decision(self, crown: dict, approved: bool) -> dict:
        """
        Materialize the story-arc decision for a crowned King/Queen of the
        Ring winner's guaranteed World Title match at Summer Slamfest.

        Approved -> the crowned winner captures the title (new reign starts).
        Rejected -> the AI's proposed outcome flips automatically: the
        champion retains, logged as a successful defense.
        """
        if not crown or not crown.get("id"):
            return {"error": "no crown payoff to materialize"}
        self._ensure_tables()
        existing = self.repo.fetch_one(
            "SELECT * FROM crown_tournament_payoffs WHERE id = ? AND deleted_at IS NULL", (crown["id"],)
        )
        if not existing or existing.get("status") == "resolved":
            return {"error": "crown payoff not found or already resolved"}

        division = crown.get("division") or existing.get("division")
        winner_id = crown.get("winner_id") or existing.get("winner_id")
        winner_name = crown.get("winner_name") or existing.get("winner_name")
        payoff_event_name = crown.get("payoff_event_name") or existing.get("payoff_event_name") or "Summer Slamfest"
        payoff_year = crown.get("payoff_year") or existing.get("payoff_year")
        payoff_week = crown.get("payoff_week") or existing.get("payoff_week")

        title_row = self._find_world_title_for_division(division)
        now = self.now()
        outcome = "champion_retains"
        result: dict = {
            "crown_payoff_id": crown["id"],
            "winner_id": winner_id,
            "winner_name": winner_name,
            "payoff_event_name": payoff_event_name,
            "approved": approved,
        }

        if title_row:
            from models.championship import calculate_reign_days

            result["title_id"] = title_row["id"]
            result["title_name"] = title_row["name"]
            if approved:
                outcome = "new_champion"
                previous_holder_id = title_row.get("current_holder_id")
                # Close out the previous reign, if any and if it's still open.
                if previous_holder_id:
                    open_reign = self.repo.fetch_one(
                        """
                        SELECT * FROM title_reigns
                        WHERE title_id = ? AND wrestler_id = ? AND lost_date_year IS NULL
                        ORDER BY won_date_year DESC, won_date_week DESC LIMIT 1
                        """,
                        (title_row["id"], previous_holder_id),
                    )
                    if open_reign:
                        days_held = calculate_reign_days(
                            open_reign["won_date_year"], open_reign["won_date_week"], open_reign.get("won_at_show_name"),
                            payoff_year, payoff_week, payoff_event_name,
                        )
                        self.conn.execute(
                            """
                            UPDATE title_reigns
                            SET lost_at_show_name = ?, lost_date_year = ?, lost_date_week = ?, days_held = ?
                            WHERE id = ?
                            """,
                            (payoff_event_name, payoff_year, payoff_week, days_held, open_reign["id"]),
                        )
                # Start the new reign.
                self.conn.execute(
                    """
                    INSERT INTO title_reigns (
                        title_id, wrestler_id, wrestler_name, won_at_show_id, won_at_show_name,
                        won_date_year, won_date_week, days_held, created_at
                    ) VALUES (?, ?, ?, NULL, ?, ?, ?, 0, ?)
                    """,
                    (title_row["id"], winner_id, winner_name, payoff_event_name, payoff_year, payoff_week, now),
                )
                self.conn.execute(
                    """
                    UPDATE championships
                    SET current_holder_id = ?, current_holder_name = ?, total_defenses = 0,
                        last_defense_year = ?, last_defense_week = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (winner_id, winner_name, payoff_year, payoff_week, now, title_row["id"]),
                )
            else:
                # Champion retains - log it as a successful title defense.
                self.conn.execute(
                    """
                    UPDATE championships
                    SET total_defenses = COALESCE(total_defenses, 0) + 1,
                        last_defense_year = ?, last_defense_week = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (payoff_year, payoff_week, now, title_row["id"]),
                )
        result["outcome"] = outcome

        self.conn.execute(
            """
            UPDATE crown_tournament_payoffs
            SET status = 'resolved', updated_at = ?
            WHERE id = ?
            """,
            (now, crown["id"]),
        )
        self.conn.commit()
        return result

    def _refresh_crown_payoff_system(self, year: int, week: int, roster: list[dict], universe, rng: random.Random) -> list[dict]:
        target_name, target_year, target_week = self._target_event(universe, year, week, ["Summer", "Champions", "Clash"], fallback_name="Crown Tournament Payoff")
        created = []
        for tournament_type, division, gender in (("king", "mens", "Male"), ("queen", "womens", "Female")):
            existing = self.repo.fetch_one(
                "SELECT * FROM crown_tournament_payoffs WHERE tournament_type = ? AND division = ? AND won_year = ? AND deleted_at IS NULL",
                (tournament_type, division, year),
            )
            if existing:
                created.append(self._decode_crown(existing))
                continue
            candidates = [w for w in roster if str(w.get("gender", "")).lower() == gender.lower()] or roster
            candidates = sorted(candidates, key=lambda w: (float(w.get("popularity") or 0), -float(w.get("overall") or 0)))
            winner = candidates[0]
            row_id = f"crown_{tournament_type}_{year}"
            now = self.now()
            self.conn.execute(
                """
                INSERT INTO crown_tournament_payoffs (
                    id, tournament_type, division, winner_id, winner_name, status,
                    won_year, won_week, payoff_event_name, payoff_year, payoff_week,
                    title_shot_json, coronation_angle_json, created_at, updated_at, deleted_at
                ) VALUES (?, ?, ?, ?, ?, 'planned', ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    row_id, tournament_type, division, winner["id"], winner["name"], year, week,
                    target_name, target_year, target_week,
                    json.dumps({"guaranteed": True, "target": f"{division.title()} world title", "can_be_countered": True}),
                    json.dumps({"angle": "coronation promo interrupted", "risk": "crowd rejection if winner is over-pushed"}),
                    now, now,
                ),
            )
            created.append(self._decode_crown(self.repo.fetch_one("SELECT * FROM crown_tournament_payoffs WHERE id = ?", (row_id,))))
        self.conn.commit()
        return created

    def _refresh_ple_roadmap(self, year: int, week: int, roster: list[dict], universe, rng: random.Random) -> list[dict]:
        shows = []
        try:
            shows = [s.to_dict() if hasattr(s, "to_dict") else dict(s) for s in universe.calendar.get_upcoming_ppvs(4)]
        except Exception:
            shows = []
        if not shows:
            shows = [{"name": "Next Premium Event", "show_type": "major_ppv", "year": year, "week": min(52, week + 6), "tier": "major"}]
        created = []
        top = sorted(roster, key=lambda w: (w.get("overall", 0), w.get("popularity", 0)), reverse=True)
        risks = sorted(roster, key=lambda w: (w.get("popularity", 0), -w.get("overall", 0)))[:4]
        for show in shows:
            event_name = show.get("name", "Premium Event")
            target_year = int(show.get("year") or year)
            target_week = int(show.get("week") or week)
            if target_year < year or (target_year == year and target_week < week):
                continue
            main = {
                "recommended_winner": top[0]["name"],
                "challenger": (top[1] if len(top) > 1 else top[0])["name"],
                "finish": "protected star-making finish" if rng.random() < 0.55 else "clean decisive finish",
            }
            risk_bets = [{"wrestler_id": w["id"], "name": w["name"], "reason": "AI wants one calculated gamble on an underused act."} for w in risks[:2]]
            row = {
                "id": f"roadmap_{target_year}_{target_week}_{self._slug(event_name)}",
                "year": year,
                "week": week,
                "event_name": event_name,
                "event_type": show.get("show_type", "major_ppv"),
                "target_year": target_year,
                "target_week": target_week,
                "status": "active",
                "headline_plan": f"Build {main['challenger']} toward {main['recommended_winner']} while testing {risk_bets[0]['name']} as the surprise accelerator.",
                "main_event_json": json.dumps(main),
                "title_plans_json": json.dumps([main]),
                "risk_bets_json": json.dumps(risk_bets),
            }
            now = self.now()
            self.conn.execute(
                """
                INSERT INTO ple_roadmap_plans (
                    id, year, week, event_name, event_type, target_year, target_week,
                    status, headline_plan, main_event_json, title_plans_json,
                    risk_bets_json, created_at, updated_at, deleted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(event_name, target_year, target_week) DO UPDATE SET
                    year = excluded.year, week = excluded.week, headline_plan = excluded.headline_plan,
                    main_event_json = excluded.main_event_json, title_plans_json = excluded.title_plans_json,
                    risk_bets_json = excluded.risk_bets_json, updated_at = excluded.updated_at,
                    deleted_at = NULL
                """,
                (
                    row["id"], row["year"], row["week"], row["event_name"], row["event_type"],
                    row["target_year"], row["target_week"], row["status"], row["headline_plan"],
                    row["main_event_json"], row["title_plans_json"], row["risk_bets_json"], now, now,
                ),
            )
            created.append(row)
        self.conn.commit()
        return self._roadmaps(8)

    def _create_decisions(self, year: int, week: int, show: dict, card: dict, roadmaps: list[dict], autonomy: str, risk: float, special_systems: dict | None = None) -> tuple[list[dict], list[dict]]:
        approvals = []
        auto_executed = []
        special_systems = special_systems or {}
        main = card["segments"][-1]
        approvals.append(self._queue_item(
            year,
            week,
            "ai_showrunner",
            card["show_id"],
            "weekly_card",
            "high",
            f"Approve AI draft for {show['name']}",
            f"The AI drafted {len(card['segments'])} segments with {main['winner']['name']} positioned in the main-event result.",
            {"card": card, "recommended_action": "approve_or_counter_card"},
            "ask",
            None,
        ))
        for roadmap in roadmaps[:2]:
            approvals.append(self._queue_item(
                year,
                week,
                "ple_roadmap",
                roadmap["id"],
                "ple_direction",
                "critical" if "LegacyMania" in roadmap["event_name"] else "high",
                f"PLE roadmap: {roadmap['event_name']}",
                roadmap["headline_plan"],
                {"roadmap": roadmap, "recommended_action": "approve_counter_or_reject_direction"},
                "ask",
                None,
            ))
        opportunity_segment = card["segments"][1]
        policy = "auto_if_unanswered" if risk >= 0.55 else "ask"
        item = self._queue_item(
            year,
            week,
            "ai_showrunner",
            f"{card['show_id']}:opportunity",
            "opportunity_gamble",
            "medium",
            f"Gamble on {opportunity_segment['participants'][0]['name']}",
            opportunity_segment["description"],
            {"segment": opportunity_segment, "recommended_action": "let_ai_test_underused_wrestler"},
            policy,
            week + 1 if policy == "auto_if_unanswered" else None,
        )
        if autonomy == "aggressive":
            self.decide_approval(item["id"], {"decision": "auto_execute", "notes": "Aggressive showrunner autonomy executed the low-risk opportunity."})
            auto_executed.append(self._decode_queue(self.repo.fetch_one("SELECT * FROM booker_approval_queue WHERE id = ?", (item["id"],))))
        else:
            approvals.append(item)
        angle = special_systems.get("angle_execution")
        if angle:
            angle_item = self._queue_item(
                year,
                week,
                "angle_library",
                angle["id"],
                "angle_library",
                "high" if angle.get("risk_level") == "high" else "medium",
                f"Run angle: {angle['template_name']}",
                f"AI selected {angle['template_name']} for {', '.join(p['name'] for p in angle.get('participants', []))}.",
                {"angle": angle, "recommended_action": "approve_counter_or_let_ai_run_angle"},
                "auto_if_unanswered" if risk >= 0.55 else "ask",
                week + 1 if risk >= 0.55 else None,
            )
            approvals.append(angle_item)
        mitb = special_systems.get("mitb", {})
        if mitb.get("cash_in_tease"):
            approvals.append(self._queue_item(
                year,
                week,
                "money_in_bank",
                mitb["cash_in_tease"]["briefcase_id"],
                "money_in_bank",
                "critical",
                f"MITB tease: {mitb['cash_in_tease']['holder_name']}",
                mitb["cash_in_tease"]["tease"],
                {"mitb": mitb["cash_in_tease"], "recommended_action": "approve_counter_or_delay_cash_in"},
                "ask",
                None,
            ))
        if mitb.get("created"):
            for briefcase in mitb["created"][:2]:
                approvals.append(self._queue_item(
                    year,
                    week,
                    "money_in_bank",
                    briefcase["id"],
                    "money_in_bank_setup",
                    "high",
                    f"MITB holder plan: {briefcase['holder_name']}",
                    f"AI wants {briefcase['holder_name']} to become the {briefcase['division']} briefcase threat.",
                    {"briefcase": briefcase, "recommended_action": "approve_counter_or_reassign_holder"},
                    "ask",
                    None,
                ))
        war_games = special_systems.get("war_games")
        if war_games:
            approvals.append(self._queue_item(
                year,
                week,
                "war_games",
                war_games["id"],
                "war_games",
                "high",
                f"War Games build: {war_games['target_event_name']}",
                f"AI is forming separate men’s and women’s five-person War Games sides for Y{war_games['target_year']} W{war_games['target_week']}.",
                {"war_games": war_games, "recommended_action": "approve_counter_or_rebuild_teams"},
                "ask",
                None,
            ))
        for crown in (special_systems.get("crown_payoffs") or [])[:2]:
            crown_item = self._queue_item(
                year,
                week,
                "crown_tournament",
                crown["id"],
                "crown_tournament",
                "medium",
                f"{crown['tournament_type'].title()} payoff: {crown['winner_name']}",
                f"AI wants {crown['winner_name']} to capture the {crown['division']} World Championship at "
                f"{crown['payoff_event_name']}. Approve to crown them; reject and the champion retains instead.",
                {"crown": crown, "recommended_action": "approve_counter_or_reseed_tournament"},
                "auto_if_unanswered" if risk >= 0.58 else "ask",
                week + 2 if risk >= 0.58 else None,
            )
            if autonomy == "aggressive":
                self.decide_approval(crown_item["id"], {"decision": "auto_execute", "notes": "Aggressive autonomy locked the crown tournament direction."})
                auto_executed.append(self._decode_queue(self.repo.fetch_one("SELECT * FROM booker_approval_queue WHERE id = ?", (crown_item["id"],))))
            else:
                approvals.append(crown_item)
        for cash_in in (special_systems.get("mitb_cash_ins") or [])[:2]:
            cash_in_item = self._queue_item(
                year,
                week,
                "mitb_cash_in",
                cash_in["briefcase_id"],
                "mitb_cash_in",
                "critical",
                f"MITB cash-in approaching: {cash_in['holder_name']}",
                f"{cash_in['holder_name']}'s cash-in on the {cash_in['target_title_name']} is booked for "
                f"{cash_in['show_name']} (Y{cash_in['cash_in_year']} W{cash_in['cash_in_week']}) - "
                f"{cash_in['weeks_out']} week(s) out. The Championship match is already queued as that show's "
                f"main event. Approve for the cash-in to succeed; reject and it fails instead.",
                {"cash_in": cash_in, "recommended_action": "approve_counter_or_reschedule_cash_in"},
                "auto_if_unanswered" if risk >= 0.58 else "ask",
                cash_in["cash_in_week"] if risk >= 0.58 else None,
            )
            if autonomy == "aggressive":
                self.decide_approval(cash_in_item["id"], {"decision": "auto_execute", "notes": "Aggressive autonomy locked the cash-in outcome."})
                auto_executed.append(self._decode_queue(self.repo.fetch_one("SELECT * FROM booker_approval_queue WHERE id = ?", (cash_in_item["id"],))))
            else:
                approvals.append(cash_in_item)
        for arc in (special_systems.get("living_arcs") or []):
            approvals.append(self._queue_item(
                year, week, "living_story_ai",
                f"{show['show_id']}:{arc['arc_type']}:{arc['brand']}:{arc['division']}:{self._slug(arc['title'])}",
                arc["arc_type"], "high" if arc["arc_type"] in {"faction_formation_betrayal_seed", "alignment_turn"} else "medium",
                arc["title"], arc["summary"],
                {"living_arc": arc, "recommended_action": "approve_counter_or_reject_living_story_arc"},
                "ask", None,
            ))
        dark_house = special_systems.get("dark_house_autopilot") or {}
        dark_created = dark_house.get("created") or []
        if dark_created:
            approvals.append(self._queue_item(
                year,
                week,
                "dark_house_autopilot",
                f"{show['show_id']}:dark_house:{year}:{week}",
                "dark_house_autopilot",
                "medium",
                "Review dark/house show autopilot",
                f"AI ran {len(dark_created)} auxiliary show(s), rotating {sum(len(r.get('roster_focus_json', [])) for r in dark_created)} total talent slots.",
                {"dark_house": dark_created, "recommended_action": "approve_adjust_or_reduce_future_autopilot"},
                "auto_if_unanswered",
                week + 1,
            ))
        promo_beats = special_systems.get("promo_beats") or []
        if promo_beats:
            approvals.append(self._queue_item(
                year,
                week,
                "promo_dialogue",
                f"{show['show_id']}:promo_beats:{year}:{week}",
                "promo_dialogue",
                "medium",
                "Review AI promo/dialogue beats",
                f"AI generated {len(promo_beats)} promo beat(s) for this card.",
                {"promo_beats": promo_beats, "recommended_action": "approve_counter_or_rewrite_dialogue"},
                "auto_if_unanswered" if risk >= 0.55 else "ask",
                week + 1 if risk >= 0.55 else None,
            ))
        return approvals, auto_executed

    def _queue_item(self, year: int, week: int, source_type: str, source_id: str, category: str, priority: str, title: str, summary: str, recommendation: dict, policy: str, auto_week: int | None) -> dict:
        existing = self.repo.fetch_one(
            """
            SELECT * FROM booker_approval_queue
            WHERE source_type = ? AND source_id = ? AND category = ? AND status = 'pending' AND deleted_at IS NULL
            LIMIT 1
            """,
            (source_type, source_id, category),
        )
        if existing:
            return self._decode_queue(existing)
        now = self.now()
        row_id = new_id("approval")
        self.conn.execute(
            """
            INSERT INTO booker_approval_queue (
                id, source_type, source_id, category, priority, title, summary,
                recommendation_json, deadline_year, deadline_week, status,
                autonomy_policy, auto_execute_after_week, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, NULL)
            """,
            (row_id, source_type, source_id, category, priority, title, summary, json.dumps(recommendation), year, week + 1, policy, auto_week, now, now),
        )
        self.conn.commit()
        return self._decode_queue(self.repo.fetch_one("SELECT * FROM booker_approval_queue WHERE id = ?", (row_id,)))

    def _persist_show_plan(self, show: dict, card: dict, year: int, week: int) -> dict:
        total_runtime = 180 if show.get("is_ppv") else 120
        planned_start = 0
        segments = []
        for item in card["segments"]:
            segment_id = f"ai_{show['show_id']}_{item['id']}"
            segments.append({
                "id": segment_id,
                "show_id": show["show_id"],
                "source_item_id": item["id"],
                "item_type": "ai_showrunner",
                "segment_type": item["segment_type"],
                "card_position": item["position"],
                "planned_start_minute": planned_start,
                "planned_duration_minutes": item["duration"],
                "allocation_status": "ai_draft",
                "expected_min_minutes": max(1, item["duration"] - 3),
                "expected_max_minutes": item["duration"] + 5,
                "is_opening": 1 if item["position"] == 1 else 0,
                "is_main_event": 1 if item["id"] == "main_event" else 0,
                "quality_score": 68,
                "crowd_heat_score": 62,
                "payload_json": item,
            })
            planned_start += item["duration"]
        plan = {
            "show_id": show["show_id"],
            "show_name": show["name"],
            "brand": show["brand"],
            "show_type": show.get("show_type", "weekly_tv"),
            "year": year,
            "week": week,
            "total_runtime_minutes": total_runtime,
            "network_break_count": 8 if show.get("show_type") == "weekly_tv" else 0,
            "accept_overrun": True,
            "booking_credibility_delta": 2.5,
            "planned_rating_impact": 0.08,
            "dead_air_risk_minutes": max(0, total_runtime - planned_start),
            "overrun_minutes": max(0, planned_start - total_runtime),
            "warnings": ["AI-generated card: review major/title implications before simulating."],
        }
        return self.repo.replace_show_plan(plan, segments)


    def _append_scheduled_crown_matches_to_draft(self, draft: dict) -> None:
        """Add ready King/Queen tournament matches scheduled for this show/week into the AI draft."""
        tournaments = self.list_crown_tournaments(limit=10)
        next_position = max([int(m.get("card_position") or m.get("position") or 0) for m in draft.get("matches", [])] + [0]) + 1
        seen = {m.get("match_id") for m in draft.get("matches", [])}
        for tournament in tournaments:
            for round_data in (tournament.get("bracket") or {}).get("rounds", []):
                for match in round_data.get("matches", []):
                    scheduled = match.get("scheduled") or {}
                    same_show = scheduled.get("show_id") and scheduled.get("show_id") == draft.get("show_id")
                    same_week = int(scheduled.get("year") or draft.get("year") or 0) == int(draft.get("year") or 0) and int(scheduled.get("week") or 0) == int(draft.get("week") or 0)
                    if not (same_show or same_week):
                        continue
                    if match.get("winner") or not match.get("wrestler_a") or not match.get("wrestler_b"):
                        continue
                    match_id = f"crown_{tournament['id']}_r{round_data['round']}_m{match['match_num']}"
                    if match_id in seen:
                        continue
                    a, b = match["wrestler_a"], match["wrestler_b"]
                    draft.setdefault("matches", []).append({
                        "match_id": match_id,
                        "match_type": "singles",
                        "participants": [a["id"], b["id"]],
                        "side_a": {"wrestler_ids": [a["id"]], "wrestler_names": [a["name"]], "is_tag_team": False},
                        "side_b": {"wrestler_ids": [b["id"]], "wrestler_names": [b["name"]], "is_tag_team": False},
                        "booked_winner": (match.get("winner") or {}).get("id"),
                        "booking_bias": "even",
                        "importance": "tournament",
                        "planned_duration_minutes": 12 if round_data["round"] < 3 else 18,
                        "duration": 12 if round_data["round"] < 3 else 18,
                        "position": next_position,
                        "card_position": next_position,
                        "is_title_match": False,
                        "allow_cross_brand": True,
                        "tournament_id": tournament["id"],
                        "tournament_round": round_data["round"],
                        "tournament_match_num": match["match_num"],
                        "ai_description": f"{tournament['name']} {round_data['round_name']} {match['match_num']}: {a['name']} vs {b['name']}",
                    })
                    seen.add(match_id)
                    next_position += 1

    def _card_to_booking_draft(self, run: dict, card: dict) -> dict:
        matches = []
        segments = []
        for index, item in enumerate(card.get("segments", []), start=1):
            participants = item.get("participants") or []
            ids = [p.get("id") for p in participants if p.get("id")]
            names = [p.get("name") for p in participants if p.get("name")]
            segment_type = item.get("segment_type") or "promo"
            if segment_type in {"match", "tag", "main_event_title_match"} and len(ids) >= 2:
                side_a = ids[:1]
                side_b = ids[1:2]
                winner = (item.get("winner") or {}).get("id")
                matches.append({
                    "match_id": f"ai_match_{item.get('id', index)}",
                    "match_type": "singles",
                    "participants": [ids[0], ids[1]],
                    "side_a": {"wrestler_ids": side_a, "wrestler_names": names[:1], "is_tag_team": False},
                    "side_b": {"wrestler_ids": side_b, "wrestler_names": names[1:2], "is_tag_team": False},
                    "booked_winner": winner,
                    "booking_bias": "slight_a" if winner == ids[0] else "slight_b" if winner == ids[1] else "even",
                    "importance": "high_drama" if segment_type == "main_event_title_match" else "normal",
                    "planned_duration_minutes": item.get("duration", 10),
                    "duration": item.get("duration", 10),
                    "position": item.get("position", index),
                    "card_position": item.get("position", index),
                    "is_title_match": segment_type == "main_event_title_match",
                    "ai_description": item.get("description"),
                })
            else:
                segments.append({
                    "segment_id": f"ai_segment_{item.get('id', index)}",
                    "segment_type": segment_type,
                    "display_name": item.get("description") or segment_type.replace("_", " ").title(),
                    "description": item.get("description"),
                    "participants": ids,
                    "participant_name_map": {p.get("id"): p.get("name") for p in participants if p.get("id")},
                    "duration": item.get("duration", 5),
                    "duration_minutes": item.get("duration", 5),
                    "position": item.get("position", index),
                    "card_position": item.get("position", index),
                    "purpose": "ai_showrunner",
                    "advances_feud": True,
                    "creates_heat": True,
                    "creates_pop": segment_type in {"promo", "interview", "vignette"},
                    "ai_mechanical_effects": item.get("mechanical_effects", []),
                })
        return {
            "show_id": run["show_id"],
            "show_name": run["show_name"],
            "brand": run["brand"],
            "show_type": "weekly_tv",
            "is_ppv": False,
            "year": run["year"],
            "week": run["week"],
            "matches": matches,
            "segments": segments,
            "ai_showrunner_run_id": run["id"],
            "ai_source": "AI Showrunner",
            "ai_creative_goal": card.get("creative_goal", "AI-generated booking plan"),
        }

    def _roadmaps(self, limit: int) -> list[dict]:
        rows = self.repo.fetch_all(
            """
            SELECT * FROM ple_roadmap_plans
            WHERE deleted_at IS NULL
            ORDER BY target_year, target_week
            LIMIT ?
            """,
            (limit,),
        )
        for row in rows:
            row["main_event_json"] = self.repo.from_json(row.get("main_event_json"), {})
            row["title_plans_json"] = self.repo.from_json(row.get("title_plans_json"), [])
            row["risk_bets_json"] = self.repo.from_json(row.get("risk_bets_json"), [])
        return rows

    def _special_system_snapshot(self) -> dict:
        templates_count = self.repo.fetch_one("SELECT COUNT(*) AS total FROM angle_library_templates WHERE deleted_at IS NULL")
        recent_angles = self.repo.fetch_all(
            """
            SELECT * FROM angle_executions
            WHERE deleted_at IS NULL
            ORDER BY created_at DESC
            LIMIT 8
            """
        )
        for row in recent_angles:
            row["participants_json"] = self.repo.from_json(row.get("participants_json"), [])
            row["impact_json"] = self.repo.from_json(row.get("impact_json"), {})
        return {
            "angle_templates_count": int((templates_count or {}).get("total") or 0),
            "recent_angles": recent_angles,
            "mitb_briefcases": self._mitb_rows(None, 8),
            "war_games": [self._decode_war_games(row) for row in self.repo.fetch_all(
                "SELECT * FROM war_games_plans WHERE deleted_at IS NULL ORDER BY target_year, target_week LIMIT 5"
            )],
            "crown_payoffs": [self._decode_crown(row) for row in self.repo.fetch_all(
                "SELECT * FROM crown_tournament_payoffs WHERE deleted_at IS NULL ORDER BY payoff_year, payoff_week LIMIT 6"
            )],
            "dark_house_runs": [self._decode_dark_house(row) for row in self.repo.fetch_all(
                "SELECT * FROM dark_house_show_runs WHERE deleted_at IS NULL ORDER BY created_at DESC LIMIT 6"
            )],
            "live_interruptions": [self._decode_live_interruption(row) for row in self.repo.fetch_all(
                "SELECT * FROM live_show_interruptions WHERE deleted_at IS NULL ORDER BY created_at DESC LIMIT 6"
            )],
            "promo_beats": [self._decode_promo_beat(row) for row in self.repo.fetch_all(
                "SELECT * FROM promo_dialogue_beats WHERE deleted_at IS NULL ORDER BY created_at DESC LIMIT 8"
            )],
        }

    def _mitb_rows(self, status: str | None, limit: int) -> list[dict]:
        if status:
            rows = self.repo.fetch_all(
                """
                SELECT * FROM money_in_bank_briefcases
                WHERE status = ? AND deleted_at IS NULL
                ORDER BY won_year DESC, won_week DESC
                LIMIT ?
                """,
                (status, limit),
            )
        else:
            rows = self.repo.fetch_all(
                """
                SELECT * FROM money_in_bank_briefcases
                WHERE deleted_at IS NULL
                ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'planned' THEN 1 ELSE 2 END,
                         won_year DESC, won_week DESC
                LIMIT ?
                """,
                (limit,),
            )
        for row in rows:
            row["cash_in_attempts_json"] = self.repo.from_json(row.get("cash_in_attempts_json"), [])
        return rows

    def _decode_war_games(self, row: dict | None) -> dict:
        if not row:
            return {}
        row = dict(row)
        row["faction_a_json"] = self.repo.from_json(row.get("faction_a_json"), [])
        row["faction_b_json"] = self.repo.from_json(row.get("faction_b_json"), [])
        row["advantage_holder_json"] = self.repo.from_json(row.get("advantage_holder_json"), {})
        row["stakes_json"] = self.repo.from_json(row.get("stakes_json"), {})
        row["escalation_beats_json"] = self.repo.from_json(row.get("escalation_beats_json"), [])
        row["divisions_json"] = self.repo.from_json(row.get("divisions_json"), {})
        return row

    def _decode_crown(self, row: dict | None) -> dict:
        if not row:
            return {}
        row = dict(row)
        row["title_shot_json"] = self.repo.from_json(row.get("title_shot_json"), {})
        row["coronation_angle_json"] = self.repo.from_json(row.get("coronation_angle_json"), {})
        return row

    def _decode_dark_house(self, row: dict | None) -> dict:
        if not row:
            return {}
        row = dict(row)
        row["roster_focus_json"] = self.repo.from_json(row.get("roster_focus_json"), [])
        row["card_json"] = self.repo.from_json(row.get("card_json"), {})
        row["results_json"] = self.repo.from_json(row.get("results_json"), {})
        row["opportunity_impacts_json"] = self.repo.from_json(row.get("opportunity_impacts_json"), [])
        return row

    def _decode_live_interruption(self, row: dict | None) -> dict:
        if not row:
            return {}
        row = dict(row)
        row["participants_json"] = self.repo.from_json(row.get("participants_json"), [])
        row["segment_payload_json"] = self.repo.from_json(row.get("segment_payload_json"), {})
        row["mechanical_effects_json"] = self.repo.from_json(row.get("mechanical_effects_json"), [])
        return row

    def _decode_promo_beat(self, row: dict | None) -> dict:
        if not row:
            return {}
        row = dict(row)
        row["participants_json"] = self.repo.from_json(row.get("participants_json"), [])
        row["lines_json"] = self.repo.from_json(row.get("lines_json"), [])
        row["mechanical_effects_json"] = self.repo.from_json(row.get("mechanical_effects_json"), [])
        return row

    def _target_event(self, universe, year: int, week: int, keywords: list[str], fallback_name: str) -> tuple[str, int, int]:
        try:
            events = [s.to_dict() if hasattr(s, "to_dict") else dict(s) for s in universe.calendar.get_upcoming_ppvs(8)]
        except Exception:
            events = []
        for event in events:
            name = event.get("name", "")
            if any(keyword.lower() in name.lower() for keyword in keywords):
                return name, int(event.get("year") or year), int(event.get("week") or week)
        if events:
            event = events[min(2, len(events) - 1)]
            return event.get("name", fallback_name), int(event.get("year") or year), int(event.get("week") or min(52, week + 8))
        return fallback_name, year, min(52, week + 8)

    def _queue_rows(self, status: str | None, limit: int) -> list[dict]:
        if status:
            rows = self.repo.fetch_all(
                """
                SELECT * FROM booker_approval_queue
                WHERE status = ? AND deleted_at IS NULL
                ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                         created_at DESC
                LIMIT ?
                """,
                (status, limit),
            )
        else:
            rows = self.repo.fetch_all(
                """
                SELECT * FROM booker_approval_queue
                WHERE deleted_at IS NULL
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        return [self._decode_queue(row) for row in rows]

    def _decode_queue(self, row: dict | None) -> dict:
        if not row:
            return {}
        row = dict(row)
        row["recommendation_json"] = self.repo.from_json(row.get("recommendation_json"), {})
        row["player_response_json"] = self.repo.from_json(row.get("player_response_json"), {})
        return row

    def _decode_run(self, row: dict) -> None:
        row["opportunity_rotation_json"] = self.repo.from_json(row.get("opportunity_rotation_json"), [])
        row["generated_card_json"] = self.repo.from_json(row.get("generated_card_json"), {})
        row["decisions_json"] = self.repo.from_json(row.get("decisions_json"), [])
        row["auto_executed_json"] = self.repo.from_json(row.get("auto_executed_json"), [])

    def _risk_from_autonomy(self, autonomy: str) -> float:
        return {"cautious": 0.35, "balanced": 0.58, "aggressive": 0.78}.get(autonomy, 0.58)

    def _slug(self, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")[:40] or "event"
