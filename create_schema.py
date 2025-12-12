# create_schema.py
# Creates the SQLite DB file trust_engine.db and the required schema + triggers.
# Rewritten to avoid "AFTER INSERT OR UPDATE" single-trigger syntax which caused the
# "near 'OR': syntax error" on some SQLite builds. Uses two explicit triggers.

import sqlite3
import os

DB_FILE = "trust_engine.db"

SQL = """
PRAGMA foreign_keys = ON;

-- USERS table
CREATE TABLE IF NOT EXISTS users (
  user_id TEXT PRIMARY KEY,
  photos INTEGER DEFAULT 0,
  bio_filled INTEGER DEFAULT 0,
  interests_count INTEGER DEFAULT 0,
  selfie_verified INTEGER DEFAULT 0,
  id_verified INTEGER DEFAULT 0,
  login_streak INTEGER DEFAULT 0,
  response_rate_pct INTEGER DEFAULT 0,
  reports_count INTEGER DEFAULT 0,
  last_active_at TEXT,
  updated_at TEXT DEFAULT (datetime('now'))
);

-- TRUST SCORES table (one row per user)
CREATE TABLE IF NOT EXISTS trust_scores (
  user_id TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
  score NUMERIC(6,2) NOT NULL DEFAULT 0.00,
  updated_at TEXT DEFAULT (datetime('now'))
);

-- AUDIT trail for score changes (no old_score, per request)
CREATE TABLE IF NOT EXISTS trust_score_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE,
  new_score NUMERIC(6,2),
  details TEXT,
  computed_at TEXT DEFAULT (datetime('now'))
);

-- Remove any old triggers with these names (safe if they don't exist)
DROP TRIGGER IF EXISTS trg_compute_trust_on_user_insert;
DROP TRIGGER IF EXISTS trg_compute_trust_on_user_update;

-- Trigger BODY SQL as a reusable statement block (we'll inline it into both triggers)
-- Note: SQLite requires each statement inside BEGIN...END to end with a semicolon.
-- This trigger computes score and inserts/updates trust_scores and appends an audit row.

-- INSERT trigger
CREATE TRIGGER trg_compute_trust_on_user_insert
AFTER INSERT ON users
FOR EACH ROW
BEGIN
  INSERT OR REPLACE INTO trust_scores(user_id, score, updated_at)
  VALUES (
    NEW.user_id,
    (
      -- PROFILE (0..30)
      (
        (CASE WHEN ((NEW.photos * 1.0) / 6.0) < 1.0 THEN ((NEW.photos * 1.0) / 6.0) ELSE 1.0 END) * 15.0
        + (CASE WHEN NEW.bio_filled = 1 THEN 10.0 ELSE 0.0 END)
        + (CASE WHEN ((NEW.interests_count * 1.0) / 5.0) < 1.0 THEN ((NEW.interests_count * 1.0) / 5.0) ELSE 1.0 END) * 5.0
      )
      -- VERIFICATION (0..40)
      + (CASE WHEN NEW.selfie_verified = 1 THEN 20.0 ELSE 0.0 END)
      + (CASE WHEN NEW.id_verified = 1 THEN 20.0 ELSE 0.0 END)
      -- ACTIVITY (0..30)
      + (CASE WHEN NEW.login_streak < 10 THEN NEW.login_streak ELSE 10 END) * 1.0
      + (CASE WHEN ((NEW.response_rate_pct * 1.0) / 10.0) < 10.0 THEN ((NEW.response_rate_pct * 1.0) / 10.0) ELSE 10.0 END)
      + (CASE WHEN (NEW.reports_count * -2.0) < -10.0 THEN -10.0 ELSE (NEW.reports_count * -2.0) END)
    ),
    datetime('now')
  );

  INSERT INTO trust_score_audit(user_id, new_score, details, computed_at)
  VALUES (
    NEW.user_id,
    (SELECT score FROM trust_scores WHERE user_id = NEW.user_id LIMIT 1),
    json_object(
      'photos', NEW.photos,
      'photos_score', (CASE WHEN ((NEW.photos * 1.0) / 6.0) < 1.0 THEN ((NEW.photos * 1.0) / 6.0) ELSE 1.0 END) * 15.0,
      'bio_filled', NEW.bio_filled,
      'bio_score', (CASE WHEN NEW.bio_filled = 1 THEN 10.0 ELSE 0.0 END),
      'interests_count', NEW.interests_count,
      'interests_score', (CASE WHEN ((NEW.interests_count * 1.0) / 5.0) < 1.0 THEN ((NEW.interests_count * 1.0) / 5.0) ELSE 1.0 END) * 5.0,
      'selfie_verified', NEW.selfie_verified,
      'id_verified', NEW.id_verified,
      'verification_score', ((CASE WHEN NEW.selfie_verified = 1 THEN 20.0 ELSE 0.0 END) + (CASE WHEN NEW.id_verified = 1 THEN 20.0 ELSE 0.0 END)),
      'login_streak', NEW.login_streak,
      'login_streak_score', (CASE WHEN NEW.login_streak < 10 THEN NEW.login_streak ELSE 10 END),
      'response_rate_pct', NEW.response_rate_pct,
      'response_rate_score', (CASE WHEN ((NEW.response_rate_pct * 1.0) / 10.0) < 10.0 THEN ((NEW.response_rate_pct * 1.0) / 10.0) ELSE 10.0 END),
      'reports_count', NEW.reports_count,
      'reports_penalty', (CASE WHEN (NEW.reports_count * -2.0) < -10.0 THEN -10.0 ELSE (NEW.reports_count * -2.0) END)
    ),
    datetime('now')
  );
END;

-- UPDATE trigger (same body)
CREATE TRIGGER trg_compute_trust_on_user_update
AFTER UPDATE ON users
FOR EACH ROW
BEGIN
  INSERT OR REPLACE INTO trust_scores(user_id, score, updated_at)
  VALUES (
    NEW.user_id,
    (
      (CASE WHEN ((NEW.photos * 1.0) / 6.0) < 1.0 THEN ((NEW.photos * 1.0) / 6.0) ELSE 1.0 END) * 15.0
      + (CASE WHEN NEW.bio_filled = 1 THEN 10.0 ELSE 0.0 END)
      + (CASE WHEN ((NEW.interests_count * 1.0) / 5.0) < 1.0 THEN ((NEW.interests_count * 1.0) / 5.0) ELSE 1.0 END) * 5.0
      + (CASE WHEN NEW.selfie_verified = 1 THEN 20.0 ELSE 0.0 END)
      + (CASE WHEN NEW.id_verified = 1 THEN 20.0 ELSE 0.0 END)
      + (CASE WHEN NEW.login_streak < 10 THEN NEW.login_streak ELSE 10 END) * 1.0
      + (CASE WHEN ((NEW.response_rate_pct * 1.0) / 10.0) < 10.0 THEN ((NEW.response_rate_pct * 1.0) / 10.0) ELSE 10.0 END)
      + (CASE WHEN (NEW.reports_count * -2.0) < -10.0 THEN -10.0 ELSE (NEW.reports_count * -2.0) END)
    ),
    datetime('now')
  );

  INSERT INTO trust_score_audit(user_id, new_score, details, computed_at)
  VALUES (
    NEW.user_id,
    (SELECT score FROM trust_scores WHERE user_id = NEW.user_id LIMIT 1),
    json_object(
      'photos', NEW.photos,
      'photos_score', (CASE WHEN ((NEW.photos * 1.0) / 6.0) < 1.0 THEN ((NEW.photos * 1.0) / 6.0) ELSE 1.0 END) * 15.0,
      'bio_filled', NEW.bio_filled,
      'bio_score', (CASE WHEN NEW.bio_filled = 1 THEN 10.0 ELSE 0.0 END),
      'interests_count', NEW.interests_count,
      'interests_score', (CASE WHEN ((NEW.interests_count * 1.0) / 5.0) < 1.0 THEN ((NEW.interests_count * 1.0) / 5.0) ELSE 1.0 END) * 5.0,
      'selfie_verified', NEW.selfie_verified,
      'id_verified', NEW.id_verified,
      'verification_score', ((CASE WHEN NEW.selfie_verified = 1 THEN 20.0 ELSE 0.0 END) + (CASE WHEN NEW.id_verified = 1 THEN 20.0 ELSE 0.0 END)),
      'login_streak', NEW.login_streak,
      'login_streak_score', (CASE WHEN NEW.login_streak < 10 THEN NEW.login_streak ELSE 10 END),
      'response_rate_pct', NEW.response_rate_pct,
      'response_rate_score', (CASE WHEN ((NEW.response_rate_pct * 1.0) / 10.0) < 10.0 THEN ((NEW.response_rate_pct * 1.0) / 10.0) ELSE 10.0 END),
      'reports_count', NEW.reports_count,
      'reports_penalty', (CASE WHEN (NEW.reports_count * -2.0) < -10.0 THEN -10.0 ELSE (NEW.reports_count * -2.0) END)
    ),
    datetime('now')
  );
END;
"""

def main():
    creating = not os.path.exists(DB_FILE)
    if creating:
        print(f"[info] creating {DB_FILE}")
    else:
        print(f"[info] {DB_FILE} exists — applying schema and trigger changes (DROP/CREATE).")

    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        conn.executescript(SQL)
        conn.commit()
    finally:
        conn.close()
    print("[done] schema + triggers created in", DB_FILE)

if __name__ == "__main__":
    main()
