"""
api/db.py
=========
SQLite audit database for risk-control tables.

Tables (defined in api/migrations/001_risk_controls.sql):
  consent_events   — disclaimer modal acknowledgements / declines
  answer_audit     — per-answer risk metadata
  source_registry  — crawled source freshness tracking
  banned_phrases   — operational banned-phrase registry

DB file location:
  data/audit.db  (relative to project root)
  On Railway: ensure this path is inside a persistent volume.

Public API:
  init_db()                    — create tables + seed data (idempotent)
  log_consent_event(...)       — insert one consent_events row
  log_answer_audit(...)        — insert one answer_audit row
  get_db()                     — return a sqlite3.Connection (for one-off queries)
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH      = _PROJECT_ROOT / "data" / "audit.db"
_MIGRATION    = Path(__file__).parent / "migrations" / "001_risk_controls.sql"

DISCLAIMER_VERSION     = "1.0"
PRIVACY_POLICY_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    """Return a new SQLite connection with row_factory set to Row."""
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # safe for concurrent writes
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------------------------
# Initialisation (idempotent — safe to call on every startup)
# ---------------------------------------------------------------------------

def init_db() -> None:
    """
    Create tables, seed banned_phrases, and reconcile schema for Sprint 6.
    Uses CREATE TABLE IF NOT EXISTS + INSERT OR IGNORE so re-running is safe.

    Sprint 6 reconciliation (only applies to pre-existing DBs):
      - DROP COLUMN intent_class, intent_confidence, domain_tier, routing_outcome
      - ADD COLUMN refusal_reason TEXT NULL
    SQLite ≥ 3.35 supports ALTER TABLE … DROP COLUMN. Wrapped in PRAGMA-driven
    existence checks to stay idempotent.
    """
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    sql = _MIGRATION.read_text(encoding="utf-8")

    conn = get_db()
    try:
        conn.executescript(sql)
        conn.commit()

        # Sprint 6 schema reconciliation
        existing_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(answer_audit)").fetchall()
        }
        # Indexes that reference now-removed columns must be dropped FIRST,
        # otherwise SQLite refuses to DROP COLUMN.
        legacy_indexes_to_drop = [
            ("answer_audit_outcome_idx", "routing_outcome"),
        ]
        for idx_name, dependent_col in legacy_indexes_to_drop:
            if dependent_col in existing_cols:
                try:
                    conn.execute(f"DROP INDEX IF EXISTS {idx_name}")
                except sqlite3.OperationalError as exc:
                    log.warning("[db] Could not drop legacy index %s: %s", idx_name, exc)

        cols_to_drop = [
            "intent_class",
            "intent_confidence",
            "domain_tier",
            "routing_outcome",
        ]
        for col in cols_to_drop:
            if col in existing_cols:
                try:
                    conn.execute(f"ALTER TABLE answer_audit DROP COLUMN {col}")
                    log.info("[db] Sprint 6 migration: dropped column answer_audit.%s", col)
                except sqlite3.OperationalError as exc:
                    log.warning(
                        "[db] Could not drop answer_audit.%s (requires SQLite ≥ 3.35): %s",
                        col, exc,
                    )
        if "refusal_reason" not in existing_cols:
            # Only add if the table existed before AND the column was missing.
            # On fresh installs the 001 script already created it.
            post_create_cols = {
                row["name"] for row in conn.execute(
                    "PRAGMA table_info(answer_audit)"
                ).fetchall()
            }
            if "refusal_reason" not in post_create_cols:
                conn.execute("ALTER TABLE answer_audit ADD COLUMN refusal_reason TEXT NULL")
                log.info("[db] Sprint 6 migration: added column answer_audit.refusal_reason")

        conn.commit()
        log.info("[db] Audit database initialised at %s", _DB_PATH)
    except Exception as exc:
        log.exception("[db] Failed to initialise database: %s", exc)
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# IP hashing (server-side only — never stored raw)
# ---------------------------------------------------------------------------

def hash_ip(ip: str) -> str:
    """sha256(ip || YYYY-MM) — rotating monthly salt."""
    monthly_salt = datetime.now(timezone.utc).strftime("%Y-%m")
    return hashlib.sha256(f"{ip}{monthly_salt}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# Consent event logging
# ---------------------------------------------------------------------------

def log_consent_event(
    *,
    session_id: str,
    event_type: str,                          # 'first_message_disclaimer' | 'disclaimer_declined' | ...
    checkbox_states: dict,
    user_id: str | None = None,
    user_agent: str | None = None,
    ip_raw: str | None = None,                # raw IP — hashed here before storage
    ui_locale: str = "en-NZ",
    question_hash: str | None = None,
    disclaimer_version: str = DISCLAIMER_VERSION,
    privacy_policy_version: str = PRIVACY_POLICY_VERSION,
) -> str:
    """
    Insert one row into consent_events.
    Returns the generated event_id (UUID).
    Best-effort: logs errors but does not raise.
    """
    event_id   = str(uuid4())
    ip_hash    = hash_ip(ip_raw) if ip_raw else None
    accepted_at = datetime.now(timezone.utc).isoformat()

    try:
        conn = get_db()
        conn.execute(
            """
            INSERT INTO consent_events
              (event_id, user_id, session_id, event_type,
               disclaimer_version, privacy_policy_version,
               checkbox_states, user_agent, ip_hash,
               accepted_at, ui_locale, question_hash)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                event_id,
                user_id,
                session_id,
                event_type,
                disclaimer_version,
                privacy_policy_version,
                json.dumps(checkbox_states),
                user_agent,
                ip_hash,
                accepted_at,
                ui_locale,
                question_hash,
            ],
        )
        conn.commit()
        log.info("[db] consent_event logged: event_id=%s type=%s", event_id, event_type)
    except Exception as exc:
        log.exception("[db] consent_event insert failed: %s", exc)
    finally:
        conn.close()

    return event_id


# ---------------------------------------------------------------------------
# Answer audit logging
# ---------------------------------------------------------------------------

def log_answer_audit(
    *,
    domain_label: str,
    model: str,
    citations: list[dict],
    banned_phrase_hits: list[str],
    regeneration_count: int,
    refused: bool,
    refusal_reason: str | None,
    crisis_route_fired: bool,
    conversation_id: str | None = None,
    user_id: str | None = None,
    prompt_version: str = "2.0",
) -> str:
    """
    Insert one row into answer_audit.

    Sprint 6 — schema slimmed: dropped intent_class, intent_confidence,
    domain_tier, routing_outcome (over-engineered risk control fields).
    Added refusal_reason (short reason string when refused=1).

    Returns the generated message_id (UUID).
    Best-effort: logs errors but does not raise.
    """
    message_id      = str(uuid4())
    conversation_id = conversation_id or str(uuid4())
    created_at      = datetime.now(timezone.utc).isoformat()

    try:
        conn = get_db()
        conn.execute(
            """
            INSERT INTO answer_audit
              (message_id, conversation_id, user_id,
               domain_label, prompt_version, model,
               citations, banned_phrase_hits,
               regeneration_count, refused, refusal_reason,
               crisis_route_fired, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                message_id,
                conversation_id,
                user_id,
                domain_label,
                prompt_version,
                model,
                json.dumps(citations),
                json.dumps(banned_phrase_hits),
                int(regeneration_count),
                int(refused),
                refusal_reason,
                int(crisis_route_fired),
                created_at,
            ],
        )
        conn.commit()
        log.info(
            "[db] answer_audit logged: message_id=%s domain=%s refused=%s regen=%d",
            message_id, domain_label, refused, regeneration_count,
        )
    except Exception as exc:
        log.exception("[db] answer_audit insert failed: %s", exc)
    finally:
        conn.close()

    return message_id
