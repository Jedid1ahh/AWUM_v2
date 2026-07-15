from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class LLMProviderError(RuntimeError):
    """Raised when no configured LLM provider can return a usable response."""


@dataclass
class LLMResult:
    provider: str
    model: str
    content: str
    parsed: dict[str, Any] | None = None
    fallback_used: bool = False


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
        self.openrouter_model = os.environ.get("AWUM_OPENROUTER_MODEL") or "openai/gpt-4o-mini"
        self.timeout = float(os.environ.get("AWUM_LLM_TIMEOUT_SECONDS", "20"))

    def status(self) -> dict[str, Any]:
        return {
            "google_configured": bool(self.google_key),
            "openrouter_configured": bool(self.openrouter_key),
            "primary": "google" if self.google_key else ("openrouter" if self.openrouter_key else "local_fallback"),
            "google_model": self.google_model,
            "openrouter_model": self.openrouter_model,
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
            try:
                result = self._call_openrouter(system_prompt, prompt)
                result.parsed = self._parse_json_object(result.content)
                result.fallback_used = bool(errors)
                return result
            except Exception as exc:
                errors.append(f"openrouter: {exc}")
        raise LLMProviderError("No LLM provider returned valid JSON. " + "; ".join(errors))

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

    def _call_openrouter(self, system_prompt: str, prompt: str) -> LLMResult:
        headers = {
            "Authorization": f"Bearer {self.openrouter_key}",
            "HTTP-Referer": os.environ.get("AWUM_SITE_URL", "http://127.0.0.1:8080"),
            "X-Title": "AWUM",
        }
        payload = {
            "model": self.openrouter_model,
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
        return LLMResult(provider="openrouter", model=self.openrouter_model, content=content)

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
        system_prompt = (
            "You are AWUM's in-game assistant showrunner. Create one actionable proposal for the player's approval. "
            "Never claim the world has already changed. Major changes must wait for approval."
        )
        user_prompt = json.dumps({"request": prompt, "context": context}, ensure_ascii=False)
        try:
            result = self.provider.complete_json(system_prompt, user_prompt, self.PROPOSAL_SCHEMA)
            proposal = result.parsed or {}
            provider_meta = {"provider": result.provider, "model": result.model, "fallback_used": result.fallback_used}
        except LLMProviderError as exc:
            proposal = self._local_pitch(prompt, context)
            provider_meta = {"provider": "local_fallback", "model": "deterministic", "error": str(exc)}
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
        title = clean[:72] or "AI Pitch"
        return {
            "title": title,
            "summary": clean or "Review this locally generated fallback pitch.",
            "category": context.get("category", "booking"),
            "priority": context.get("priority", "opportunity"),
            "proposal_type": context.get("proposal_type", "system"),
            "actions": [],
            "effects_preview": {},
            "source_type": "llm_pitch",
        }
