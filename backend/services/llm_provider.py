from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


class LLMProviderError(RuntimeError):
    """Raised when no configured LLM provider can return a usable response."""


DEFAULT_OPENROUTER_MODELS = [
    "openai/gpt-oss-20b:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "nvidia/nemotron-nano-9b-v2:free",
    "poolside/laguna-xs-2.1:free",
    "cohere/north-mini-code:free",
]


@dataclass
class LLMResult:
    provider: str
    model: str
    content: str
    parsed: dict[str, Any] | None = None
    fallback_used: bool = False
    attempted_models: list[str] = field(default_factory=list)


class LLMProvider:
    """Small provider facade for Google AI Studio with OpenRouter fallback.

    The rest of AWUM should depend on this class rather than provider-specific
    SDKs. It deliberately uses urllib from the standard library so the server can
    run in the existing lightweight environment.
    """

    def __init__(self):
        self.google_key = os.environ.get("GOOGLE_AI_API_KEY") or os.environ.get("GEMINI_API_KEY")
        self.openrouter_key = os.environ.get("OPENROUTER_API_KEY")
        self.google_model = os.environ.get("AWUM_GOOGLE_MODEL") or os.environ.get("AWUM_LLM_MODEL") or "gemini-1.5-flash"
        self.openrouter_models = self._openrouter_models_from_env()
        self.openrouter_model = self.openrouter_models[0]
        self.timeout = float(os.environ.get("AWUM_LLM_TIMEOUT_SECONDS", "20"))

    def status(self) -> dict[str, Any]:
        primary = "google" if self.google_key else ("openrouter" if self.openrouter_key else "local_fallback")
        fallback_chain = []
        if self.google_key and self.openrouter_key:
            fallback_chain.append("openrouter")
        if primary != "local_fallback":
            fallback_chain.append("local_fallback")
        return {
            "google_configured": bool(self.google_key),
            "openrouter_configured": bool(self.openrouter_key),
            "primary": primary,
            "primary_label": "Google AI Studio" if primary == "google" else ("OpenRouter" if primary == "openrouter" else "Local fallback"),
            "fallback_chain": fallback_chain,
            "google_model": self.google_model,
            "openrouter_model": self.openrouter_model,
            "openrouter_models": self.openrouter_models,
        }

    def complete_json(self, system_prompt: str, user_prompt: str, schema_hint: dict[str, Any] | None = None) -> LLMResult:
        errors: list[str] = []
        prompt = self._json_prompt(system_prompt, user_prompt, schema_hint)
        if self.google_key:
            try:
                result = self._call_google(prompt)
                result.parsed = self._parse_json_object(result.content)
                return result
            except Exception as exc:  # provider fallback boundary
                errors.append(f"google: {exc}")
        if self.openrouter_key:
            attempted_models: list[str] = []
            for model in self.openrouter_models:
                attempted_models.append(model)
                try:
                    result = self._call_openrouter(system_prompt, prompt, model)
                    result.parsed = self._parse_json_object(result.content)
                    result.fallback_used = bool(errors) or model != self.openrouter_models[0]
                    result.attempted_models = attempted_models
                    return result
                except Exception as exc:
                    errors.append(f"openrouter:{model}: {exc}")
        raise LLMProviderError("No LLM provider returned valid JSON. " + "; ".join(errors))

    def _openrouter_models_from_env(self) -> list[str]:
        configured = []
        raw_list = os.environ.get("AWUM_OPENROUTER_MODELS", "")
        configured.extend(model.strip() for model in raw_list.split(",") if model.strip())
        single_model = os.environ.get("AWUM_OPENROUTER_MODEL")
        if single_model:
            configured.insert(0, single_model.strip())
        models = []
        for model in configured + DEFAULT_OPENROUTER_MODELS:
            if model and model not in models:
                models.append(model)
        return models

    def _json_prompt(self, system_prompt: str, user_prompt: str, schema_hint: dict[str, Any] | None) -> str:
        schema = json.dumps(schema_hint or {}, indent=2, ensure_ascii=False)
        return (
            f"{system_prompt}\n\n"
            "Return one valid JSON object only. Do not include markdown fences or commentary.\n"
            f"Schema hint:\n{schema}\n\n"
            f"User request/context:\n{user_prompt}"
        )

    def _call_google(self, prompt: str) -> LLMResult:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.google_model}:generateContent?key={self.google_key}"
        )
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.7, "response_mime_type": "application/json"},
        }
        data = self._post_json(url, payload)
        candidates = data.get("candidates") or []
        parts = (((candidates[0] or {}).get("content") or {}).get("parts") or []) if candidates else []
        content = "".join(str(part.get("text", "")) for part in parts).strip()
        if not content:
            raise LLMProviderError("empty Google response")
        return LLMResult(provider="google", model=self.google_model, content=content)

    def _call_openrouter(self, system_prompt: str, prompt: str, model: str) -> LLMResult:
        headers = {
            "Authorization": f"Bearer {self.openrouter_key}",
            "HTTP-Referer": os.environ.get("AWUM_SITE_URL", "http://127.0.0.1:8080"),
            "X-Title": "AWUM",
        }
        payload = {
            "model": model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
        }
        data = self._post_json("https://openrouter.ai/api/v1/chat/completions", payload, headers)
        choices = data.get("choices") or []
        content = ((choices[0] or {}).get("message") or {}).get("content", "").strip() if choices else ""
        if not content:
            raise LLMProviderError("empty OpenRouter response")
        return LLMResult(provider="openrouter", model=model, content=content)

    def _post_json(self, url: str, payload: dict[str, Any], extra_headers: dict[str, str] | None = None) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", **(extra_headers or {})}
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise LLMProviderError(f"HTTP {exc.code}: {detail}") from exc

    def _parse_json_object(self, content: str) -> dict[str, Any]:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if not match:
                raise
            parsed = json.loads(match.group(0))
        if not isinstance(parsed, dict):
            raise LLMProviderError("LLM returned JSON that was not an object")
        return parsed


class LLMProposalService:
    """Turns an LLM/direct pitch into canonical booker approval queue items."""

    PROPOSAL_SCHEMA = {
        "title": "Short inbox title",
        "summary": "One paragraph pitch summary",
        "category": "booking|feud|promo|talent|finance|contract|post_show|media|system",
        "priority": "critical|high|medium|low|opportunity",
        "proposal_type": "match|segment|promo|feud_payoff|finance|talent|system",
        "actions": [{"type": "machine_readable_action", "parameters": {}}],
        "effects_preview": {},
    }

    def __init__(self, showrunner_service, provider: LLMProvider | None = None):
        self.showrunner = showrunner_service
        self.provider = provider or LLMProvider()

    def provider_status(self) -> dict[str, Any]:
        return self.provider.status()

    def create_pitch(self, prompt: str, context: dict[str, Any] | None = None, year: int = 1, week: int = 1) -> dict[str, Any]:
        context = context or {}
        game_context = self._game_context()
        grounded_context = {**context, "game_state": game_context}
        system_prompt = (
            "You are AWUM's in-game assistant showrunner. Create one actionable proposal for the player's approval. "
            "Never claim the world has already changed. Major changes must wait for approval. "
            "Use ONLY wrestler names from game_state.active_roster_names and, for feud payoffs, prefer game_state.active_feuds. "
            "If no suitable real wrestler exists, ask for more roster context instead of inventing names."
        )
        user_prompt = json.dumps({"request": prompt, "context": grounded_context}, ensure_ascii=False)
        try:
            result = self.provider.complete_json(system_prompt, user_prompt, self.PROPOSAL_SCHEMA)
            proposal = result.parsed or {}
            self._validate_proposal_grounding(proposal, game_context)
            provider_meta = {
                "provider": result.provider,
                "model": result.model,
                "fallback_used": result.fallback_used,
                "attempted_models": result.attempted_models,
                "grounded": True,
            }
        except LLMProviderError as exc:
            proposal = self._local_pitch(prompt, grounded_context)
            provider_meta = {"provider": "local_fallback", "model": "deterministic", "error": str(exc), "grounded": True}
        item = self.enqueue_proposal(proposal, year=year, week=week, provider_meta=provider_meta)
        return {"proposal": proposal, "approval": item, "provider": provider_meta}

    def enqueue_proposal(self, proposal: dict[str, Any], year: int = 1, week: int = 1, provider_meta: dict[str, Any] | None = None) -> dict[str, Any]:
        title = str(proposal.get("title") or proposal.get("headline") or "LLM Pitch").strip()[:160]
        summary = str(proposal.get("summary") or proposal.get("rationale") or "LLM-generated pitch awaiting review.").strip()
        category = str(proposal.get("category") or proposal.get("proposal_type") or "llm_pitch").lower().replace(" ", "_")[:64]
        priority = str(proposal.get("priority") or "opportunity").lower()
        if priority not in {"critical", "high", "medium", "low", "opportunity", "urgent"}:
            priority = "opportunity"
        source_type = str(proposal.get("source_type") or f"llm_{proposal.get('proposal_type') or 'pitch'}").lower().replace(" ", "_")[:80]
        source_id = str(proposal.get("source_id") or f"{source_type}:{title}:{year}:{week}")[:180]
        recommendation = {
            "llm_proposal": proposal,
            "provider": provider_meta or {},
            "recommended_action": proposal.get("recommended_action") or "approve_counter_or_reject",
        }
        return self.showrunner.queue_external_item(
            year=year,
            week=week,
            source_type=source_type,
            source_id=source_id,
            category=category,
            priority=priority,
            title=title,
            summary=summary,
            recommendation=recommendation,
            policy=str(proposal.get("autonomy_policy") or "ask"),
            auto_week=proposal.get("auto_execute_after_week"),
        )

    def _local_pitch(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        clean = " ".join(str(prompt or "").split())
        game_state = context.get("game_state") or {}
        roster = game_state.get("active_roster") or []
        feuds = game_state.get("active_feuds") or []
        wants_feud = "feud" in clean.lower() or str(context.get("proposal_type", "")).lower() == "feud_payoff"
        if wants_feud and feuds:
            feud = feuds[0]
            names = feud.get("participant_names") or []
            if len(names) >= 2:
                return {
                    "title": f"Feud payoff: {names[0]} vs {names[1]}",
                    "summary": f"Review a grounded payoff between active roster members {names[0]} and {names[1]} from an existing feud before anything is booked.",
                    "category": "feud",
                    "priority": "high" if int(feud.get("intensity") or 0) >= 80 else "opportunity",
                    "proposal_type": "feud_payoff",
                    "referenced_wrestlers": names[:2],
                    "actions": [{"type": "review_feud_payoff", "parameters": {"feud_id": feud.get("id"), "participants": names[:2]}}],
                    "effects_preview": {"grounded_roster_names": names[:2]},
                    "source_type": "llm_feud_payoff",
                }
        if len(roster) >= 2:
            first, second = roster[0], roster[1]
            return {
                "title": f"Grounded pitch: {first['name']} vs {second['name']}",
                "summary": f"Review an LLM fallback pitch using real roster members {first['name']} and {second['name']}: {clean or 'Create a booking idea.'}",
                "category": context.get("category", "booking"),
                "priority": context.get("priority", "opportunity"),
                "proposal_type": context.get("proposal_type", "match"),
                "referenced_wrestlers": [first["name"], second["name"]],
                "actions": [{"type": "review_grounded_pitch", "parameters": {"participants": [first["name"], second["name"]]}}],
                "effects_preview": {"grounded_roster_names": [first["name"], second["name"]]},
                "source_type": "llm_pitch",
            }
        title = clean[:72] or "AI Pitch"
        return {
            "title": title,
            "summary": clean or "Review this locally generated fallback pitch.",
            "category": context.get("category", "booking"),
            "priority": context.get("priority", "opportunity"),
            "proposal_type": context.get("proposal_type", "system"),
            "referenced_wrestlers": [],
            "actions": [],
            "effects_preview": {},
            "source_type": "llm_pitch",
        }

    def _game_context(self) -> dict[str, Any]:
        conn = getattr(getattr(self.showrunner, "database", None), "conn", None)
        if conn is None:
            return {"active_roster": [], "active_roster_names": [], "active_feuds": []}
        roster = []
        try:
            rows = conn.execute(
                """
                SELECT id, name, role, primary_brand, popularity, momentum, morale
                FROM wrestlers
                WHERE COALESCE(is_retired, 0) = 0
                ORDER BY popularity DESC, momentum DESC, name
                LIMIT 40
                """
            ).fetchall()
            roster = [dict(row) for row in rows]
        except Exception:
            roster = []
        feuds = []
        try:
            rows = conn.execute(
                """
                SELECT id, participant_names, intensity, status, match_count
                FROM feuds
                WHERE status != 'resolved'
                ORDER BY intensity DESC, match_count DESC
                LIMIT 12
                """
            ).fetchall()
            for row in rows:
                feud = dict(row)
                try:
                    feud["participant_names"] = json.loads(feud.get("participant_names") or "[]")
                except Exception:
                    feud["participant_names"] = []
                feuds.append(feud)
        except Exception:
            feuds = []
        return {
            "active_roster": roster,
            "active_roster_names": [row.get("name") for row in roster if row.get("name")],
            "active_feuds": feuds,
        }

    def _validate_proposal_grounding(self, proposal: dict[str, Any], game_context: dict[str, Any]) -> None:
        roster_names = {str(name) for name in game_context.get("active_roster_names", []) if name}
        if not roster_names:
            return
        unknown = []
        for name in self._proposal_referenced_names(proposal):
            if name and name not in roster_names and name not in unknown:
                unknown.append(name)
        if unknown:
            raise LLMProviderError(f"LLM proposal referenced wrestler(s) not in roster: {', '.join(unknown)}")

    def _proposal_referenced_names(self, proposal: dict[str, Any]) -> list[str]:
        names: list[str] = []

        def add(value):
            if isinstance(value, str):
                cleaned = value.strip().strip(" .,;:!?")
                if cleaned:
                    names.append(cleaned)

        def walk(value, key: str = ""):
            if isinstance(value, dict):
                for child_key, child_value in value.items():
                    lowered = str(child_key).lower()
                    if lowered in {"wrestler_name", "winner_name", "loser_name"} or (lowered == "name" and key in {"participants", "winner", "loser", "wrestlers", "referenced_wrestlers"}):
                        add(child_value)
                    elif lowered in {"referenced_wrestlers", "wrestler_names", "participant_names", "participants", "winner", "loser"}:
                        walk(child_value, lowered)
                    else:
                        walk(child_value, lowered)
            elif isinstance(value, list):
                for item in value:
                    walk(item, key)
            elif key in {"referenced_wrestlers", "wrestler_names", "participant_names", "participants", "winner", "loser"}:
                add(value)

        walk(proposal)
        text = " ".join(str(proposal.get(field) or "") for field in ("title", "summary", "rationale"))
        for match in re.finditer(r"(?:^|[:;\-])\s*([A-Z][A-Za-z'’.-]*(?:\s+[A-Z][A-Za-z'’.-]*){0,3})\s+vs\.?\s+([A-Z][A-Za-z'’.-]*(?:\s+[A-Z][A-Za-z'’.-]*){0,3})", text):
            add(match.group(1))
            add(match.group(2))
        for match in re.finditer(r"between\s+([A-Z][A-Za-z'’.-]*(?:\s+[A-Z][A-Za-z'’.-]*){0,3})\s+and\s+([A-Z][A-Za-z'’.-]*(?:\s+[A-Z][A-Za-z'’.-]*){0,3})", text):
            add(match.group(1))
            add(match.group(2))
        for match in re.finditer(r"\b(The\s+[A-Z][A-Za-z'’.-]*)\b", text):
            add(match.group(1))
        return names
