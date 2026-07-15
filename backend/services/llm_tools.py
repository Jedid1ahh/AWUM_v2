from __future__ import annotations

import json
import re
from typing import Any


def normalize_brand(value: Any) -> str:
    """Normalize brand labels like 'ROC Alpha Weekly' and 'Alpha brand'."""
    value = str(value or "").lower()
    value = re.sub(r"\b(roc|brand|weekly|show)\b", " ", value)
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def brand_matches(wrestler_brand: Any, requested_brand: Any) -> bool:
    if not requested_brand:
        return True
    raw_norm = normalize_brand(wrestler_brand)
    requested_norm = normalize_brand(requested_brand)
    if not raw_norm or not requested_norm:
        return False
    return (
        raw_norm == requested_norm
        or requested_norm in raw_norm.split()
        or requested_norm in raw_norm
        or raw_norm in requested_norm
    )


def active_roster(conn, limit: int = 80) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, name, gender, role, primary_brand, popularity, momentum, morale
        FROM wrestlers
        WHERE COALESCE(is_retired, 0) = 0
        ORDER BY popularity DESC, momentum DESC, name
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def active_feuds(conn, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, participant_names, intensity, status, match_count
        FROM feuds
        WHERE status != 'resolved'
        ORDER BY intensity DESC, match_count DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    feuds = []
    for row in rows:
        feud = dict(row)
        try:
            feud["participant_names"] = json.loads(feud.get("participant_names") or "[]")
        except Exception:
            feud["participant_names"] = []
        feuds.append(feud)
    return feuds
