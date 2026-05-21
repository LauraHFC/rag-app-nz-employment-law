-- 001_risk_controls.sql
-- Risk control audit tables (Privacy Act 2020 aligned)
-- Target: SQLite (Railway persistent volume at data/audit.db)
-- Retention policy:
--   consent_events  → 7 years  (Limitation Act 2010)
--   answer_audit    → 2 years rolling
--   source_registry → indefinite (freshness tracking)
--   banned_phrases  → indefinite (operational registry)

-- ── Consent events ────────────────────────────────────────────────────────────
-- Every disclaimer modal acknowledgement, decline, or version-bump re-prompt.
CREATE TABLE IF NOT EXISTS consent_events (
  event_id               TEXT PRIMARY KEY,
  user_id                TEXT NULL,
  session_id             TEXT NOT NULL,
  event_type             TEXT NOT NULL
                           CHECK (event_type IN (
                             'first_message_disclaimer',
                             'disclaimer_declined',
                             'reprompt',
                             'policy_version_bump'
                           )),
  disclaimer_version     TEXT NOT NULL DEFAULT '1.0',
  privacy_policy_version TEXT NOT NULL DEFAULT '1.0',
  -- JSON: {"general_info": true, "no_reliance": true, "read_policies": true}
  checkbox_states        TEXT NOT NULL DEFAULT '{}',
  user_agent             TEXT,
  -- sha256(ip || YYYY-MM monthly salt) — computed server-side, never stored raw
  ip_hash                TEXT,
  accepted_at            TEXT NOT NULL,
  ui_locale              TEXT NOT NULL DEFAULT 'en-NZ',
  -- sha256(first_question_text) — avoid storing sensitive content in consent table
  question_hash          TEXT
);

CREATE INDEX IF NOT EXISTS consent_events_session_idx
  ON consent_events (session_id, accepted_at DESC);

CREATE INDEX IF NOT EXISTS consent_events_version_idx
  ON consent_events (disclaimer_version, privacy_policy_version);

CREATE INDEX IF NOT EXISTS consent_events_type_idx
  ON consent_events (event_type, accepted_at DESC);


-- ── Per-answer audit ──────────────────────────────────────────────────────────
-- One row per assistant response. Captures slim risk-control metadata.
-- Sprint 6: dropped intent_class, intent_confidence, domain_tier, routing_outcome
-- (over-engineered risk control fields). Added refusal_reason.
CREATE TABLE IF NOT EXISTS answer_audit (
  message_id             TEXT PRIMARY KEY,
  conversation_id        TEXT NOT NULL,
  user_id                TEXT NULL,
  domain_label           TEXT NOT NULL,
  prompt_version         TEXT NOT NULL DEFAULT '2.0',
  model                  TEXT NOT NULL,
  -- JSON arrays stored as text for SQLite compatibility
  citations              TEXT NOT NULL DEFAULT '[]',
  banned_phrase_hits     TEXT NOT NULL DEFAULT '[]',
  regeneration_count     INTEGER NOT NULL DEFAULT 0,
  refused                INTEGER NOT NULL DEFAULT 0  CHECK (refused IN (0,1)),
  refusal_reason         TEXT NULL,
  crisis_route_fired     INTEGER NOT NULL DEFAULT 0  CHECK (crisis_route_fired IN (0,1)),
  created_at             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS answer_audit_domain_idx
  ON answer_audit (domain_label, created_at DESC);

CREATE INDEX IF NOT EXISTS answer_audit_created_idx
  ON answer_audit (created_at DESC);

CREATE INDEX IF NOT EXISTS answer_audit_crisis_idx
  ON answer_audit (crisis_route_fired, created_at DESC);

CREATE INDEX IF NOT EXISTS answer_audit_regen_idx
  ON answer_audit (regeneration_count, created_at DESC);

CREATE INDEX IF NOT EXISTS answer_audit_refused_idx
  ON answer_audit (refused, created_at DESC);


-- ── Source registry ───────────────────────────────────────────────────────────
-- Every source the retrieval store can cite. Tracks freshness.
-- Populated incrementally by the crawl pipeline; read by the citation validator.
CREATE TABLE IF NOT EXISTS source_registry (
  source_id              TEXT PRIMARY KEY,   -- sha256(url)[:16]
  url                    TEXT NOT NULL UNIQUE,
  title                  TEXT NOT NULL DEFAULT '',
  source_type            TEXT NOT NULL DEFAULT 'gov_guidance'
                           CHECK (source_type IN (
                             'statute','regulation','case',
                             'gov_guidance','community_law'
                           )),
  jurisdiction           TEXT NOT NULL DEFAULT 'NZ',
  retrieved_at           TEXT NOT NULL,      -- ISO 8601 of last successful crawl
  freshness_status       TEXT NOT NULL DEFAULT 'fresh'
                           CHECK (freshness_status IN ('fresh','stale','unknown')),
  superseded_by          TEXT NULL REFERENCES source_registry(source_id)
);

CREATE INDEX IF NOT EXISTS source_registry_status_idx
  ON source_registry (freshness_status, retrieved_at DESC);


-- ── Banned-phrase registry ────────────────────────────────────────────────────
-- Operational registry: update rows here to add/remove banned phrases
-- without requiring a code redeploy.
CREATE TABLE IF NOT EXISTS banned_phrases (
  phrase_id    TEXT PRIMARY KEY,
  pattern      TEXT NOT NULL UNIQUE,
  language     TEXT NOT NULL DEFAULT 'en'
                 CHECK (language IN ('en','zh')),
  rationale    TEXT NOT NULL DEFAULT '',
  active       INTEGER NOT NULL DEFAULT 1  CHECK (active IN (0,1)),
  added_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Seed: mirrors the hardcoded list in pipeline/output_guard.py
-- Insert OR IGNORE so re-running migration is safe.
INSERT OR IGNORE INTO banned_phrases (phrase_id, pattern, language, rationale) VALUES
  ('bp_en_01', '\byou\s+should\b',                     'en', 'Prescriptive advice — use hedged third-person instead'),
  ('bp_en_02', '\bthe\s+best\s+thing\s+to\s+do\b',    'en', 'Opinion/recommendation'),
  ('bp_en_03', '\bin\s+your\s+(case|situation)\b',     'en', 'Applies law to user facts = legal advice'),
  ('bp_en_04', '\bthis\s+is\s+(a\s+)?personal\s+grievance\b', 'en', 'Characterisation of user facts'),
  ('bp_en_05', '\bthis\s+is\s+unfair\s+dismissal\b',  'en', 'Characterisation of user facts'),
  ('bp_en_06', '\byou\s+are\s+entitled\s+to\b',       'en', 'Conclusion of legal right'),
  ('bp_en_07', '\byou\s+have\s+a\s+(strong|weak|good|poor)\s+case\b', 'en', 'Merits opinion'),
  ('bp_en_08', '\bI\s+(recommend|suggest|would\s+advise)\b', 'en', 'Direct advice'),
  ('bp_en_09', '\b(definitely|certainly|guaranteed)\b','en', 'False precision — law is fact-dependent'),
  ('bp_en_10', '\bas\s+your\s+lawyer\b',              'en', 'Holding-out risk under LCA 2006'),
  ('bp_zh_01', '你应该',   'zh', 'Prescriptive advice'),
  ('bp_zh_02', '你需要',   'zh', 'Prescriptive instruction'),
  ('bp_zh_03', '你必须',   'zh', 'Prescriptive instruction'),
  ('bp_zh_04', '最好的办法是', 'zh', 'Recommendation/opinion'),
  ('bp_zh_05', '在你的情况下', 'zh', 'Applies law to user facts'),
  ('bp_zh_06', '你有权',   'zh', 'Conclusion of legal right'),
  ('bp_zh_07', '我建议',   'zh', 'Direct advice'),
  ('bp_zh_08', '我推荐',   'zh', 'Direct advice'),
  ('bp_zh_09', '一定',     'zh', 'False precision'),
  ('bp_zh_10', '肯定',     'zh', 'False precision');
