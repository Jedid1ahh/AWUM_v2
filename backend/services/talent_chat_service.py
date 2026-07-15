from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from services.llm_provider import LLMProvider, LLMProviderError


class TalentChatService:
    """Grounded one-on-one talent conversations.

    The service never lets a chat target drift away from the selected wrestler:
    prompts include the live wrestler row, responses are JSON-only, and the
    returned name is validated against the selected row before the UI sees it.
    """

    def __init__(self, database, provider: LLMProvider | None = None):
        self.database = database
        self.conn = database.conn
        self.provider = provider or LLMProvider()

    def chat(self, wrestler_id: str, message: str, year: int = 1, week: int = 1) -> dict[str, Any]:
        wrestler = self.database.get_wrestler_by_id(wrestler_id)
        if not wrestler:
            raise ValueError("Wrestler not found.")
        message = (message or "").strip()
        if not message:
            raise ValueError("Message is required.")

        stage = self._stage_for(wrestler)
        system_prompt = (
            "You are writing an in-character private locker-room reply for exactly one wrestler. "
            "Use ONLY the selected_wrestler.name supplied in context. Do not invent roster names, titles, feuds, or promises. "
            "Return compact JSON with keys: wrestler_name, reply, morale, stage, promise_requested. "
            "promise_requested should be true only if the booker clearly promises a future push, title shot, win, match, or time off."
        )
        user_prompt = json.dumps(
            {
                "booker_message": message,
                "current_year": year,
                "current_week": week,
                "selected_wrestler": self._wrestler_context(wrestler),
                "allowed_stage_values": ["ecstatic", "content", "concerned", "angry", "exhausted"],
            },
            ensure_ascii=False,
        )
        schema = {
            "wrestler_name": wrestler["name"],
            "reply": "first-person in-character response",
            "morale": wrestler.get("morale", 50),
            "stage": stage,
            "promise_requested": False,
        }

        provider_meta = {"provider": "local_fallback", "model": "deterministic"}
        try:
            result = self.provider.complete_json(system_prompt, user_prompt, schema)
            parsed = result.parsed or {}
            if str(parsed.get("wrestler_name") or wrestler["name"]).strip() != wrestler["name"]:
                raise LLMProviderError("Talent chat response referenced the wrong wrestler.")
            reply = str(parsed.get("reply") or "").strip() or self._local_reply(wrestler, message)
            morale = self._bounded_int(parsed.get("morale"), wrestler.get("morale", 50))
            stage = self._safe_stage(parsed.get("stage"), morale)
            provider_meta = {"provider": result.provider, "model": result.model, "attempted_models": result.attempted_models}
        except Exception as exc:
            reply = self._local_reply(wrestler, message)
            morale = self._bounded_int(wrestler.get("morale"), 50)
            stage = self._stage_from_morale(morale)
            provider_meta["error"] = str(exc)

        promise = self._maybe_create_promise(wrestler, message, year, week)
        return {
            "wrestler_id": wrestler_id,
            "wrestler_name": wrestler["name"],
            "reply": reply,
            "morale": morale,
            "stage": stage,
            "promise_created": bool(promise),
            "promise": promise,
            "provider": provider_meta,
        }

    def _wrestler_context(self, wrestler: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": wrestler.get("id"),
            "name": wrestler.get("name"),
            "alignment": wrestler.get("alignment"),
            "role": wrestler.get("role"),
            "brand": wrestler.get("primary_brand"),
            "popularity": wrestler.get("popularity"),
            "momentum": wrestler.get("momentum"),
            "morale": wrestler.get("morale"),
            "fatigue": wrestler.get("fatigue"),
            "mic": wrestler.get("mic"),
        }

    def _maybe_create_promise(self, wrestler: dict[str, Any], message: str, year: int, week: int) -> dict[str, Any] | None:
        text = message.lower()
        promise_type = None
        if re.search(r"\b(push|feature|spotlight|build around)\b", text):
            promise_type = "push"
        elif re.search(r"\b(title shot|championship|belt)\b", text):
            promise_type = "title_opportunity"
        elif re.search(r"\b(win|victory|go over)\b", text):
            promise_type = "win"
        elif re.search(r"\b(time off|rest|recover)\b", text):
            promise_type = "time_off"
        if not promise_type or not re.search(r"\b(i('| a)?m|i am|we('| wi)?ll|promise|guarantee|give you|you('| wi)?ll)\b", text):
            return None
        deadline_weeks = self._deadline_weeks(text)
        promise_id = self.database.save_contract_promise(
            {
                "promise_type": promise_type,
                "wrestler_id": wrestler["id"],
                "wrestler_name": wrestler["name"],
                "promised_year": year,
                "promised_week": week,
                "deadline_weeks": deadline_weeks,
            }
        )
        return {
            "id": promise_id,
            "promise_type": promise_type,
            "deadline_weeks": deadline_weeks,
            "created_at": datetime.now().isoformat(),
        }

    def _deadline_weeks(self, text: str) -> int:
        m = re.search(r"(\d+)\s*(day|days|week|weeks)", text)
        if not m:
            return 2
        amount = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("day"):
            return max(1, (amount + 6) // 7)
        return max(1, amount)

    def _local_reply(self, wrestler: dict[str, Any], message: str) -> str:
        name = wrestler.get("name", "I")
        morale = self._bounded_int(wrestler.get("morale"), 50)
        if morale < 40:
            return f"I hear you, but words are cheap. If you mean it, prove it with the booking, and {name} will respond in the ring."
        if "push" in message.lower():
            return f"That is what I wanted to hear. Give me the spotlight and I will make the company look smart for believing in {name}."
        return f"I appreciate you coming to me directly. Keep the plan clear, keep me in the loop, and {name} will deliver."

    def _stage_for(self, wrestler: dict[str, Any]) -> str:
        if self._bounded_int(wrestler.get("fatigue"), 0) >= 80:
            return "exhausted"
        return self._stage_from_morale(self._bounded_int(wrestler.get("morale"), 50))

    def _safe_stage(self, value: Any, morale: int) -> str:
        value = str(value or "").lower()
        return value if value in {"ecstatic", "content", "concerned", "angry", "exhausted"} else self._stage_from_morale(morale)

    def _stage_from_morale(self, morale: int) -> str:
        if morale >= 80:
            return "ecstatic"
        if morale >= 60:
            return "content"
        if morale >= 40:
            return "concerned"
        return "angry"

    def _bounded_int(self, value: Any, fallback: int) -> int:
        try:
            return max(0, min(100, int(value)))
        except (TypeError, ValueError):
            return fallback
