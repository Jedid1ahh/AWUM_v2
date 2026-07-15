import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.dirname(__file__))

from persistence.database import Database
from services.ai_showrunner_service import AIShowrunnerService


class AIShowrunnerTests(unittest.TestCase):
    def setUp(self):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        tmp_dir = os.path.join(root, "test_tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        self.db_path = os.path.join(tmp_dir, f"ai_showrunner_{uuid.uuid4().hex}.db")
        self.database = Database(self.db_path)
        self.service = AIShowrunnerService(self.database)
        self._seed_wrestlers()

    def tearDown(self):
        if self.database is not None:
            self.database.close()
        for suffix in ("", "-wal", "-shm"):
            path = self.db_path + suffix
            if os.path.exists(path):
                os.remove(path)

    def _seed_wrestlers(self):
        now = "2026-06-30T00:00:00"
        rows = [
            ("w_alpha", "Alpha Ace", 34, "Male", "face", "main_event", "Cross-Brand", 82, 75, 68, 78, 84, 72, 12, 1, 86, 20, 70, 10, "None", None, 0, 250000, 104, 70, 1, 1, 0),
            ("w_beta", "Beta Brawler", 31, "Male", "heel", "upper_midcard", "Cross-Brand", 76, 64, 58, 62, 72, 68, 8, 0, 78, 12, 52, 18, "None", None, 0, 120000, 78, 30, 1, 1, 0),
            ("w_gamma", "Gamma Prospect", 24, "Female", "face", "midcard", "Cross-Brand", 58, 61, 74, 55, 60, 72, 3, 0, 63, 8, 48, 12, "None", None, 0, 70000, 52, 44, 1, 1, 0),
            ("w_delta", "Delta Storm", 28, "Female", "heel", "midcard", "Cross-Brand", 67, 69, 77, 64, 65, 70, 5, 0, 69, 15, 62, 9, "None", None, 0, 90000, 52, 40, 1, 1, 0),
            ("w_echo", "Echo Knight", 39, "Male", "face", "veteran", "Cross-Brand", 71, 82, 51, 75, 86, 64, 17, 1, 81, 10, 58, 15, "None", None, 0, 180000, 104, 62, 1, 1, 0),
        ]
        self.database.conn.executemany(
            """
            INSERT OR REPLACE INTO wrestlers (
                id, name, age, gender, alignment, role, primary_brand,
                brawling, technical, speed, mic, psychology, stamina,
                years_experience, is_major_superstar, popularity, momentum,
                morale, fatigue, injury_severity, injury_description,
                injury_weeks_remaining, contract_salary, contract_total_weeks,
                contract_weeks_remaining, contract_signing_year,
                contract_signing_week, is_retired, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [row + (now, now) for row in rows],
        )
        self.database.conn.commit()

    def _add_war_games_depth(self):
        now = "2026-06-30T00:00:00"
        rows = []
        for idx in range(10):
            rows.append((f"wg_m_{idx}", f"War Man {idx}", 30 + idx, "Male", "face", "midcard", "Cross-Brand", 70 + idx, 70, 60, 60, 70, 70, 8, 0, 70 + idx, 20, 70, 0, "None", None, 0, 80000, 52, 40, 1, 1, 0))
            rows.append((f"wg_w_{idx}", f"War Woman {idx}", 28 + idx, "Female", "heel", "midcard", "Cross-Brand", 68 + idx, 70, 65, 62, 70, 70, 8, 0, 68 + idx, 20, 70, 0, "None", None, 0, 80000, 52, 40, 1, 1, 0))
        self.database.conn.executemany(
            """
            INSERT OR REPLACE INTO wrestlers (
                id, name, age, gender, alignment, role, primary_brand,
                brawling, technical, speed, mic, psychology, stamina,
                years_experience, is_major_superstar, popularity, momentum,
                morale, fatigue, injury_severity, injury_description,
                injury_weeks_remaining, contract_salary, contract_total_weeks,
                contract_weeks_remaining, contract_signing_year,
                contract_signing_week, is_retired, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [row + (now, now) for row in rows],
        )
        self.database.conn.commit()

    def test_weekly_showrunner_persists_card_roadmap_and_approvals(self):
        result = self.service.run_weekly(1, 9, seed=77, force=True, autonomy_level="balanced")

        self.assertFalse(result["already_ran"])
        self.assertGreaterEqual(len(result["card"]["segments"]), 7)
        self.assertGreaterEqual(len(result["approvals_created"]), 3)
        self.assertGreaterEqual(len(result["roadmaps"]), 1)
        self.assertIn("angle_execution", result["special_systems"])
        self.assertGreaterEqual(len(result["special_systems"]["mitb"]["briefcases"]), 1)
        self.assertTrue(result["special_systems"]["war_games"]["id"])
        self.assertGreaterEqual(len(result["special_systems"]["crown_payoffs"]), 1)
        self.assertGreaterEqual(result["special_systems"]["dark_house_autopilot"]["total"], 2)
        self.assertGreaterEqual(len(result["special_systems"]["promo_beats"]), 1)

        saved_plan = self.service.repo.get_show_plan(result["show"]["show_id"])
        self.assertIsNotNone(saved_plan)
        self.assertGreaterEqual(len(saved_plan["segments"]), 7)

        self.database.close()
        self.database = Database(self.db_path)
        restarted = AIShowrunnerService(self.database)
        dashboard = restarted.dashboard()

        self.assertGreaterEqual(dashboard["summary"]["pending_approvals"], 3)
        self.assertGreaterEqual(dashboard["summary"]["active_roadmaps"], 1)
        self.assertGreaterEqual(dashboard["summary"]["angle_templates"], 12)
        self.assertGreaterEqual(dashboard["summary"]["active_mitb"], 1)
        self.assertGreaterEqual(dashboard["summary"]["active_war_games"], 1)
        self.assertGreaterEqual(dashboard["summary"]["crown_payoffs"], 1)
        self.assertGreaterEqual(dashboard["summary"]["dark_house_runs"], 2)
        self.assertGreaterEqual(dashboard["summary"]["promo_beats"], 1)
        self.assertEqual("drafted", dashboard["summary"]["last_run_status"])

        categories = {item["category"] for item in dashboard["pending_approvals"]}
        self.assertIn("angle_library", categories)
        self.assertIn("money_in_bank_setup", categories)
        self.assertIn("war_games", categories)
        self.assertIn("crown_tournament", categories)
        self.assertIn("dark_house_autopilot", categories)
        self.assertIn("promo_dialogue", categories)

    def test_approving_war_games_materializes_gender_separated_factions(self):
        self._add_war_games_depth()
        self.service.run_weekly(1, 9, seed=77, force=True, autonomy_level="balanced")
        dashboard = self.service.dashboard()
        war_games_item = next(item for item in dashboard["pending_approvals"] if item["category"] == "war_games")

        before = self.database.get_all_factions(active_only=True)
        self.service.decide_approval(war_games_item["id"], {"decision": "approve", "notes": "Lock the teams."})
        after = self.database.get_all_factions(active_only=True)

        created = [f for f in after if f not in before and f["faction_name"].startswith("War Games")]
        self.assertEqual(4, len(created))
        expected_names = {
            "War Games Men's Team A",
            "War Games Men's Team B",
            "War Games Women's Team A",
            "War Games Women's Team B",
        }
        self.assertEqual(expected_names, {f["faction_name"] for f in created})
        for faction in created:
            self.assertEqual(5, len(faction["member_ids"]))


    def test_approving_legacy_mixed_war_games_payload_self_heals_factions(self):
        self._add_war_games_depth()
        self.service.run_weekly(1, 9, seed=77, force=True, autonomy_level="balanced")
        plan = self.service.list_war_games_plans(limit=1)[0]

        legacy_payload = dict(plan)
        legacy_payload["divisions_json"] = {}
        legacy_payload["faction_a_json"] = [
            {"id": "w_alpha", "name": "Alpha Ace", "gender": "Male"},
            {"id": "w_beta", "name": "Beta Brawler", "gender": "Male"},
            {"id": "w_echo", "name": "Echo Knight", "gender": "Male"},
            {"id": "w_gamma", "name": "Gamma Prospect", "gender": "Female"},
        ]
        legacy_payload["faction_b_json"] = [
            {"id": "wg_m_0", "name": "War Man 0", "gender": "Male"},
            {"id": "wg_m_1", "name": "War Man 1", "gender": "Male"},
            {"id": "wg_w_0", "name": "War Woman 0", "gender": "Female"},
            {"id": "wg_w_1", "name": "War Woman 1", "gender": "Female"},
        ]

        before = self.database.get_all_factions(active_only=True)
        self.service._materialize_war_games_factions(legacy_payload)
        after = self.database.get_all_factions(active_only=True)

        created = [f for f in after if f not in before and "Team" in f["faction_name"]]
        self.assertEqual(4, len(created))
        for faction in created:
            self.assertEqual(5, len(faction["member_ids"]))
            genders = {self.database.get_wrestler_by_id(member_id)["gender"].lower() for member_id in faction["member_ids"]}
            self.assertEqual(1, len(genders))

    def test_approval_decision_and_aggressive_auto_execute(self):
        result = self.service.run_weekly(1, 10, seed=88, force=True, autonomy_level="aggressive")

        self.assertGreaterEqual(len(result["auto_executed"]), 1)
        dashboard = self.service.dashboard()
        pending = dashboard["pending_approvals"][0]
        decided = self.service.decide_approval(pending["id"], {"decision": "counter", "counter_pitch": "Keep the beat, change the winner."})

        self.assertEqual("countered", decided["status"])
        self.assertEqual("Keep the beat, change the winner.", decided["player_response_json"]["counter_pitch"])

        latest = self.service.latest_booking_draft()
        original_segment_count = len(latest["show_draft"]["segments"])
        live = self.service.maybe_live_interruption(latest["show_draft"], seed=5, force=True)
        self.assertTrue(live["inserted"])
        self.assertGreater(len(live["show_draft"]["segments"]), original_segment_count)

        beats = self.service.generate_promo_beats(1, 10, show_draft=latest["show_draft"], seed=7, force=True)
        self.assertGreaterEqual(beats["total"], 1)

        dark = self.service.run_dark_house_autopilot(1, 11, seed=9, force=True)
        self.assertGreaterEqual(dark["total"], 2)

    def test_google_ai_studio_is_primary_when_both_provider_keys_exist(self):
        from unittest.mock import patch
        from services.llm_provider import LLMProvider

        with patch.dict(os.environ, {"GOOGLE_AI_API_KEY": "google-key", "OPENROUTER_API_KEY": "openrouter-key"}, clear=True):
            provider = LLMProvider()

        status = provider.status()
        self.assertEqual(status["primary"], "google")
        self.assertEqual(status["primary_label"], "Google AI Studio")
        self.assertIn("openrouter", status["fallback_chain"])

    def test_openrouter_uses_project_default_model_fallback_order(self):
        from unittest.mock import patch
        from services.llm_provider import DEFAULT_OPENROUTER_MODELS, LLMProvider

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}, clear=True):
            provider = LLMProvider()

        self.assertEqual(provider.openrouter_models, DEFAULT_OPENROUTER_MODELS)
        self.assertEqual(provider.status()["openrouter_models"], DEFAULT_OPENROUTER_MODELS)

    def test_openrouter_model_env_prepends_custom_model_without_losing_defaults(self):
        from unittest.mock import patch
        from services.llm_provider import DEFAULT_OPENROUTER_MODELS, LLMProvider

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key", "AWUM_OPENROUTER_MODEL": "custom/model:free"}, clear=True):
            provider = LLMProvider()

        self.assertEqual(provider.openrouter_models[0], "custom/model:free")
        self.assertEqual(provider.openrouter_models[1:], DEFAULT_OPENROUTER_MODELS)

    def test_llm_pitch_rejects_unknown_wrestler_names_and_uses_roster_fallback(self):
        from services.llm_provider import LLMProposalService

        class HallucinatingProvider:
            def status(self):
                return {"primary": "test"}

            def complete_json(self, system_prompt, user_prompt, schema_hint=None):
                from services.llm_provider import LLMResult
                return LLMResult(
                    provider="test",
                    model="hallucination",
                    content="{}",
                    parsed={
                        "title": "Heat-Up Payoff: The Rumble vs. The Viper",
                        "summary": "Deliver an explosive payoff to the long-running feud between The Rumble and The Viper.",
                        "category": "feud",
                        "priority": "high",
                        "proposal_type": "feud_payoff",
                        "referenced_wrestlers": ["The Rumble", "The Viper"],
                    },
                )

        service = LLMProposalService(self.service, HallucinatingProvider())
        result = service.create_pitch(
            "Suggest a feud payoff.",
            context={"category": "feud", "proposal_type": "feud_payoff"},
            year=1,
            week=14,
        )

        approval = result["approval"]
        self.assertEqual(result["provider"]["provider"], "local_fallback")
        self.assertNotIn("The Rumble", approval["title"])
        self.assertNotIn("The Viper", approval["summary"])
        self.assertIn("Alpha Ace", approval["summary"])

    def test_external_llm_pitch_uses_approval_queue(self):
        from services.llm_provider import LLMProvider, LLMProposalService

        provider = LLMProvider()
        provider.google_key = None
        provider.openrouter_key = None
        service = LLMProposalService(self.service, provider)
        result = service.create_pitch(
            "Suggest a protected promo for Alpha Ace.",
            context={"category": "promo", "priority": "opportunity"},
            year=1,
            week=12,
        )
        approval = result["approval"]
        self.assertEqual(approval["status"], "pending")
        self.assertEqual(approval["source_type"], "llm_pitch")
        inbox = self.service.inbox(status="pending", category="promo")
        self.assertGreaterEqual(inbox["total"], 1)

    def test_approved_llm_match_materializes_booking_segment(self):
        approval = self.service.queue_external_item(
            1,
            15,
            "llm_pitch",
            "test_pitch",
            "match",
            "high",
            "Book Alpha Ace vs Beta Brawler",
            "Approved match using real roster members only.",
            {
                "llm_proposal": {
                    "title": "Book Alpha Ace vs Beta Brawler",
                    "summary": "Alpha Ace faces Beta Brawler in a featured match.",
                    "proposal_type": "match",
                    "referenced_wrestlers": ["Alpha Ace", "Beta Brawler"],
                }
            },
            "ask",
        )

        decided = self.service.decide_approval(approval["id"], {"decision": "approve"})
        self.assertEqual(decided["status"], "approved")

        segment = self.database.conn.execute(
            "SELECT * FROM booking_segments WHERE source_item_id = ? AND item_type = 'llm_approved'",
            (approval["id"],),
        ).fetchone()
        self.assertIsNotNone(segment)
        self.assertEqual(segment["allocation_status"], "approved_llm")

    def test_approved_llm_proposal_with_unknown_wrestler_is_blocked(self):
        approval = self.service.queue_external_item(
            1,
            15,
            "llm_pitch",
            "bad_pitch",
            "match",
            "high",
            "Book The Rumble vs The Viper",
            "Fake names should never execute.",
            {
                "llm_proposal": {
                    "title": "Book The Rumble vs The Viper",
                    "summary": "The Rumble faces The Viper.",
                    "proposal_type": "match",
                    "referenced_wrestlers": ["The Rumble", "The Viper"],
                }
            },
            "ask",
        )

        self.service.decide_approval(approval["id"], {"decision": "approve"})
        execution = self.database.conn.execute(
            "SELECT * FROM llm_proposal_executions WHERE approval_id = ?",
            (approval["id"],),
        ).fetchone()
        self.assertEqual(execution["status"], "blocked")
        segment = self.database.conn.execute(
            "SELECT * FROM booking_segments WHERE source_item_id = ?",
            (approval["id"],),
        ).fetchone()
        self.assertIsNone(segment)

    def test_talent_chat_tracks_push_promise_for_selected_wrestler(self):
        from services.talent_chat_service import TalentChatService

        class WrongNameProvider:
            def complete_json(self, system_prompt, user_prompt, schema_hint=None):
                from services.llm_provider import LLMResult
                return LLMResult(
                    provider="test",
                    model="wrong-name",
                    content="{}",
                    parsed={"wrestler_name": "The Rumble", "reply": "I am not real.", "morale": 99, "stage": "ecstatic"},
                )

        chat = TalentChatService(self.database, WrongNameProvider())
        result = chat.chat("w_alpha", "I promise I will push you in 2 days.", 1, 16)

        self.assertEqual(result["wrestler_name"], "Alpha Ace")
        self.assertTrue(result["promise_created"])
        self.assertEqual(result["promise"]["deadline_weeks"], 1)
        promise = self.database.conn.execute(
            "SELECT * FROM contract_promises WHERE wrestler_id = ?",
            ("w_alpha",),
        ).fetchone()
        self.assertIsNotNone(promise)
        self.assertEqual(promise["promise_type"], "push")

    def test_llm_pitch_respects_requested_brand_and_gender_constraints(self):
        from services.llm_provider import LLMProposalService

        now = "2026-06-30T00:00:00"
        extra = [
            ("w_alpha_m1", "Alpha King", 32, "Male", "face", "main_event", "ROC Alpha", 80, 75, 70, 82, 78, 72, 10, 1, 91, 20, 72, 5, "None", None, 0, 200000, 80, 40, 1, 1, 0),
            ("w_alpha_m2", "Alpha Duke", 30, "Male", "heel", "upper_midcard", "ROC Alpha", 78, 72, 74, 77, 75, 70, 8, 0, 88, 18, 70, 5, "None", None, 0, 180000, 80, 40, 1, 1, 0),
            ("w_vel_m1", "Velocity Rogue", 33, "Male", "heel", "main_event", "Velocity", 81, 73, 76, 83, 77, 71, 11, 1, 93, 22, 74, 4, "None", None, 0, 210000, 80, 40, 1, 1, 0),
        ]
        self.database.conn.executemany(
            """
            INSERT OR REPLACE INTO wrestlers (
                id, name, age, gender, alignment, role, primary_brand,
                brawling, technical, speed, mic, psychology, stamina,
                years_experience, is_major_superstar, popularity, momentum,
                morale, fatigue, injury_severity, injury_description,
                injury_weeks_remaining, contract_salary, contract_total_weeks,
                contract_weeks_remaining, contract_signing_year,
                contract_signing_week, is_retired, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [row + (now, now) for row in extra],
        )
        self.database.conn.commit()

        class WrongBrandProvider:
            def complete_json(self, system_prompt, user_prompt, schema_hint=None):
                from services.llm_provider import LLMResult
                return LLMResult(
                    provider="test",
                    model="wrong-brand",
                    content="{}",
                    parsed={
                        "title": "Velocity Rogue vs Alpha King promo",
                        "summary": "Velocity Rogue confronts Alpha King on Alpha.",
                        "category": "promo",
                        "proposal_type": "promo",
                        "referenced_wrestlers": ["Velocity Rogue", "Alpha King"],
                    },
                )

        service = LLMProposalService(self.service, WrongBrandProvider())
        result = service.create_pitch(
            "Suggest a promo beat between two top male stars on the Alpha brand for next ROC Alpha weekly show.",
            context={},
            year=1,
            week=17,
        )

        self.assertEqual(result["provider"]["provider"], "local_fallback")
        self.assertIn("Alpha King", result["approval"]["summary"])
        self.assertIn("Alpha Duke", result["approval"]["summary"])
        self.assertNotIn("Velocity Rogue", result["approval"]["summary"])

    def test_repeated_llm_pitch_repairs_existing_empty_recommendation(self):
        empty = self.service.queue_external_item(
            1,
            14,
            "llm_pitch",
            "llm_pitch:Grounded pitch: Alpha Ace vs Beta Brawler:1:14",
            "booking",
            "opportunity",
            "Grounded pitch: Alpha Ace vs Beta Brawler",
            "Old empty pitch.",
            {},
            "ask",
        )

        from services.llm_provider import LLMProvider, LLMProposalService

        provider = LLMProvider()
        provider.google_key = None
        provider.openrouter_key = None
        service = LLMProposalService(self.service, provider)
        result = service.create_pitch(
            "Suggest a promo beat between two top male stars.",
            context={"category": "booking"},
            year=1,
            week=14,
        )

        self.assertEqual(result["approval"]["id"], empty["id"])
        recommendation = result["approval"]["recommendation_json"]
        self.assertIn("llm_proposal", recommendation)
        self.assertTrue(recommendation["llm_proposal"].get("referenced_wrestlers"))

    def test_approved_llm_intergender_match_is_blocked(self):
        approval = self.service.queue_external_item(
            1,
            18,
            "llm_pitch",
            "intergender_pitch",
            "match",
            "high",
            "Book Alpha Ace vs Gamma Prospect",
            "AI should not book intergender matches.",
            {
                "llm_proposal": {
                    "title": "Book Alpha Ace vs Gamma Prospect",
                    "summary": "Alpha Ace faces Gamma Prospect.",
                    "proposal_type": "match",
                    "referenced_wrestlers": ["Alpha Ace", "Gamma Prospect"],
                }
            },
            "ask",
        )

        self.service.decide_approval(approval["id"], {"decision": "approve"})
        execution = self.database.conn.execute(
            "SELECT * FROM llm_proposal_executions WHERE approval_id = ?",
            (approval["id"],),
        ).fetchone()
        self.assertEqual(execution["status"], "blocked")
        self.assertIn("intergender", execution["result_json"])

    def test_approved_llm_segment_appears_in_latest_booking_draft(self):
        self.service.run_weekly(1, 19, seed=88, force=True, autonomy_level="balanced")
        approval = self.service.queue_external_item(
            1,
            19,
            "llm_pitch",
            "promo_pitch",
            "promo",
            "high",
            "Promo beat: Alpha Ace and Beta Brawler",
            "A grounded promo beat for the latest AI card.",
            {
                "llm_proposal": {
                    "title": "Promo beat: Alpha Ace and Beta Brawler",
                    "summary": "Alpha Ace and Beta Brawler trade promo beats before the main event.",
                    "proposal_type": "promo",
                    "referenced_wrestlers": ["Alpha Ace", "Beta Brawler"],
                }
            },
            "ask",
        )
        self.service.decide_approval(approval["id"], {"decision": "approve"})

        draft = self.service.latest_booking_draft()
        segment_names = [segment.get("display_name") or segment.get("description") for segment in draft["show_draft"].get("segments", [])]
        self.assertTrue(any("Alpha Ace and Beta Brawler" in str(name) for name in segment_names))


if __name__ == "__main__":
    unittest.main()
