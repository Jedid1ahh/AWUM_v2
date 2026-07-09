#!/usr/bin/env python3
"""Reset all championship holders, defenses, and title reign history.

This is an intentionally explicit maintenance script for users who need to
perform the championship fresh-start reset manually outside the web UI.

By default the script runs in dry-run mode and prints what it would change.
Use --apply to write the JSON/SQLite changes.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


TITLE_HISTORY_ARRAYS = (
    "title_reigns",
    "title_defenses",
    "title_vacancies",
    "title_reign_stats",
)

CHAMPIONSHIP_CLEAR_VALUES = {
    "current_holder_id": None,
    "current_holder_name": None,
    "interim_holder_id": None,
    "interim_holder_name": None,
    "last_defense_year": None,
    "last_defense_week": None,
    "last_defense_show_id": None,
    "total_defenses": 0,
}

WRESTLER_TITLE_STAT_VALUES = {
    "total_title_reigns": 0,
    "total_days_as_champion": 0,
    "longest_reign_days": 0,
}

DEFAULT_JSON_FILES = (
    "backend/data/championships.json",
    "backend/data/saves/autosave.json",
    "backend/data/saves/save_slot_1.json",
)

DEFAULT_SQLITE_FILES = (
    "backend/data/awum.db",
    "backend/data/database.db",
    "backend/data/universe.db",
    "backend/data/saves/universe.db",
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def championship_lists(payload: Any) -> list[list[dict[str, Any]]]:
    """Return every championship list shape used by the project data files."""
    if isinstance(payload, list):
        return [payload]
    if isinstance(payload, dict) and isinstance(payload.get("championships"), list):
        return [payload["championships"]]
    return []


def reset_championship_record(record: dict[str, Any], timestamp: str) -> int:
    changes = 0
    for key, value in CHAMPIONSHIP_CLEAR_VALUES.items():
        if record.get(key) != value:
            record[key] = value
            changes += 1

    if record.get("history") != []:
        record["history"] = []
        changes += 1

    if "vacancy_reason" in record and record.get("vacancy_reason") != "Fresh start reset":
        record["vacancy_reason"] = "Fresh start reset"
        changes += 1

    if changes and "updated_at" in record:
        record["updated_at"] = timestamp

    return changes


def reset_json_file(path: Path, apply: bool) -> tuple[int, str]:
    if not path.exists():
        return 0, "missing"

    payload = json.loads(path.read_text())
    timestamp = datetime.now().isoformat()
    changes = 0

    for champs in championship_lists(payload):
        for championship in champs:
            if isinstance(championship, dict):
                changes += reset_championship_record(championship, timestamp)

    if isinstance(payload, dict):
        for key in TITLE_HISTORY_ARRAYS:
            if payload.get(key) != []:
                payload[key] = []
                changes += 1

        for row in payload.get("wrestler_stats", []) or []:
            if not isinstance(row, dict):
                continue
            for key, value in WRESTLER_TITLE_STAT_VALUES.items():
                if row.get(key) != value:
                    row[key] = value
                    changes += 1

        metadata = payload.get("metadata")
        if changes and isinstance(metadata, dict):
            metadata["last_modified"] = timestamp

    if apply and changes:
        path.write_text(json.dumps(payload, indent=2) + "\n")

    return changes, "updated" if changes else "clean"


def sqlite_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row[0] for row in rows}


def reset_sqlite_file(path: Path, apply: bool) -> tuple[int, str]:
    if not path.exists():
        return 0, "missing"

    conn = sqlite3.connect(path)
    tables = sqlite_tables(conn)
    changes = 0

    if "championships" in tables:
        changes += conn.execute(
            """
            UPDATE championships
            SET current_holder_id = NULL,
                current_holder_name = NULL,
                interim_holder_id = NULL,
                interim_holder_name = NULL,
                last_defense_year = NULL,
                last_defense_week = NULL,
                last_defense_show_id = NULL,
                total_defenses = 0
            WHERE current_holder_id IS NOT NULL
               OR current_holder_name IS NOT NULL
               OR interim_holder_id IS NOT NULL
               OR interim_holder_name IS NOT NULL
               OR last_defense_year IS NOT NULL
               OR last_defense_week IS NOT NULL
               OR last_defense_show_id IS NOT NULL
               OR COALESCE(total_defenses, 0) != 0
            """
        ).rowcount

    for table in TITLE_HISTORY_ARRAYS:
        if table in tables:
            changes += conn.execute(f"DELETE FROM {table}").rowcount

    if "wrestler_stats" in tables:
        changes += conn.execute(
            """
            UPDATE wrestler_stats
            SET total_title_reigns = 0,
                total_days_as_champion = 0,
                longest_reign_days = 0
            WHERE COALESCE(total_title_reigns, 0) != 0
               OR COALESCE(total_days_as_champion, 0) != 0
               OR COALESCE(longest_reign_days, 0) != 0
            """
        ).rowcount

    if "wrestler_legacy_stats" in tables:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(wrestler_legacy_stats)").fetchall()}
        assignments = []
        predicates = []
        for column in ("total_title_reigns", "total_days_as_champion", "longest_reign_days"):
            if column in columns:
                assignments.append(f"{column} = 0")
                predicates.append(f"COALESCE({column}, 0) != 0")
        if assignments:
            changes += conn.execute(
                f"UPDATE wrestler_legacy_stats SET {', '.join(assignments)} WHERE {' OR '.join(predicates)}"
            ).rowcount

    if apply:
        conn.commit()
    else:
        conn.rollback()
    conn.close()

    return changes, "updated" if changes else "clean"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write changes instead of dry-running")
    parser.add_argument(
        "--json-file",
        action="append",
        default=[],
        help="additional JSON file to reset, relative to repo root or absolute",
    )
    parser.add_argument(
        "--sqlite-file",
        action="append",
        default=[],
        help="additional SQLite DB to reset, relative to repo root or absolute",
    )
    return parser.parse_args()


def resolve_paths(root: Path, defaults: tuple[str, ...], extra: list[str]) -> list[Path]:
    paths = [root / item for item in defaults]
    for item in extra:
        path = Path(item)
        paths.append(path if path.is_absolute() else root / path)
    return paths


def main() -> int:
    args = parse_args()
    root = repo_root()
    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"Championship reset maintenance script ({mode})")

    total_changes = 0

    for path in resolve_paths(root, DEFAULT_JSON_FILES, args.json_file):
        changes, status = reset_json_file(path, args.apply)
        total_changes += changes
        print(f"JSON   {path.relative_to(root) if path.is_relative_to(root) else path}: {status} ({changes} changes)")

    for path in resolve_paths(root, DEFAULT_SQLITE_FILES, args.sqlite_file):
        changes, status = reset_sqlite_file(path, args.apply)
        total_changes += changes
        print(f"SQLite {path.relative_to(root) if path.is_relative_to(root) else path}: {status} ({changes} rows/changes)")

    if args.apply:
        print(f"Applied championship reset. Total changes: {total_changes}")
    else:
        print(f"Dry run complete. Total pending changes: {total_changes}. Re-run with --apply to write them.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
