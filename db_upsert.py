# db_upsert.py
# Provides upsert_user(conn, user_dict) and upsert_user_and_wait(conn, user_dict, timeout=15)
# Usage:
#   from db_upsert import upsert_user_and_wait, DB_FILE
#   conn = sqlite3.connect(DB_FILE); conn.execute("PRAGMA foreign_keys=ON;")
#   upsert_user_and_wait(conn, user_json, timeout=15)
#   conn.close()

import sqlite3
import time
from typing import Dict, Any, Optional

DB_FILE = "trust_engine.db"

def map_input_to_row(d: Dict[str, Any]) -> Dict[str, Any]:
    interests_val = d.get("interests")
    if isinstance(interests_val, bool):
        interests_count = 5 if interests_val else 0
    elif isinstance(interests_val, int):
        interests_count = max(0, min(5, int(interests_val)))
    else:
        interests_count = 0

    return {
        "user_id": str(d["user_id"]),
        "photos": int(d.get("photos", 0)),
        "bio_filled": 1 if d.get("bio") else 0,
        "interests_count": interests_count,
        "selfie_verified": 1 if d.get("selfie_verified") else 0,
        "id_verified": 1 if d.get("id_verified") else 0,
        "login_streak": int(d.get("login_streak_days") or d.get("login_streak") or 0),
        "response_rate_pct": int(d.get("response_rate_pct") or d.get("response_rate") or 0),
        "reports_count": int(d.get("reports_received") or d.get("reports_count") or 0),
        "last_active_at": d.get("last_active_at")
    }

def upsert_user(conn: sqlite3.Connection, user_json: Dict[str, Any]) -> None:
    """
    Insert or update a user row. The DB trigger (enqueue trigger) will create a recompute job.
    """
    row = map_input_to_row(user_json)
    sql = """
    INSERT INTO users (
      user_id, photos, bio_filled, interests_count,
      selfie_verified, id_verified,
      login_streak, response_rate_pct, reports_count, last_active_at, updated_at
    )
    VALUES (
      :user_id, :photos, :bio_filled, :interests_count,
      :selfie_verified, :id_verified,
      :login_streak, :response_rate_pct, :reports_count, :last_active_at, datetime('now')
    )
    ON CONFLICT(user_id) DO UPDATE SET
      photos = excluded.photos,
      bio_filled = excluded.bio_filled,
      interests_count = excluded.interests_count,
      selfie_verified = excluded.selfie_verified,
      id_verified = excluded.id_verified,
      login_streak = excluded.login_streak,
      response_rate_pct = excluded.response_rate_pct,
      reports_count = excluded.reports_count,
      last_active_at = excluded.last_active_at,
      updated_at = datetime('now')
    ;
    """
    cur = conn.cursor()
    cur.execute(sql, row)
    conn.commit()

def get_trust_score(conn: sqlite3.Connection, user_id: str) -> Optional[tuple]:
    cur = conn.cursor()
    cur.execute("SELECT user_id, score, updated_at FROM trust_scores WHERE user_id = ? LIMIT 1", (user_id,))
    return cur.fetchone()

def get_audit_rows(conn: sqlite3.Connection, user_id: str):
    cur = conn.cursor()
    cur.execute(
        "SELECT id, user_id, new_score, details, computed_at FROM trust_score_audit WHERE user_id = ? ORDER BY computed_at DESC",
        (user_id,)
    )
    return cur.fetchall()

def _find_latest_job(conn: sqlite3.Connection, user_id: str) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute("""
        SELECT id, user_id, processing, processed, attempts, enqueued_at, processed_at, last_error
        FROM recompute_jobs
        WHERE user_id = ?
        ORDER BY enqueued_at DESC, id DESC
        LIMIT 1;
    """, (user_id,))
    r = cur.fetchone()
    if r is None:
        return None
    return {
        "id": r[0], "user_id": r[1], "processing": r[2], "processed": r[3],
        "attempts": r[4], "enqueued_at": r[5], "processed_at": r[6], "last_error": r[7]
    }

def upsert_user_and_wait(conn: sqlite3.Connection, user_json: Dict[str, Any], timeout: float = 15.0, poll_interval: float = 0.5):
    """
    Upsert the user, then wait up to `timeout` seconds for the worker to process the recompute job.
    If no job is enqueued (because one was already pending), this function will attempt to wait for trust_scores to be updated.
    After wait completes (success or timeout) the function prints the trust_scores row and recent audits.
    """
    user_id = str(user_json["user_id"])
    # snapshot existing trust_score updated_at (if any)
    before = get_trust_score(conn, user_id)
    before_updated_at = before[2] if before else None

    # perform upsert (this fires the enqueue trigger)
    upsert_user(conn, user_json)

    start = time.monotonic()
    deadline = start + timeout

    # attempt to locate the job inserted by the trigger
    job = None
    while time.monotonic() < deadline:
        job = _find_latest_job(conn, user_id)
        if job is not None:
            # If job already processed, break immediately.
            if job["processed"] == 1:
                break
            # otherwise wait until processed
            if job["processed"] == 0:
                time.sleep(poll_interval)
                continue
            # if processed==2 (failed), break and return status
            if job["processed"] == 2:
                break
        else:
            # no job row found — possibly no enqueue (duplicate pending prevented)
            # fallback: wait for trust_scores updated_at to change / or for trust_scores to appear
            ts = get_trust_score(conn, user_id)
            if ts is not None:
                # if there was no previous score, or updated_at changed, assume updated
                if before_updated_at is None or ts[2] != before_updated_at:
                    break
            time.sleep(poll_interval)

    # Final fetches
    final_score_row = get_trust_score(conn, user_id)
    audits = get_audit_rows(conn, user_id)

    # Print results (caller can also inspect return value)
    print("== trust_scores ==")
    print(final_score_row)

    print("\n== latest audit rows ==")
    if not audits:
        print("(no audit rows)")
    else:
        for a in audits:
            print(a)

    # return a structured result for programmatic use
    return {
        "trust_score_row": final_score_row,
        "audit_rows": audits,
        "last_job": job
    }

if __name__ == "__main__":
    # demo usage
    sample = {
        "user_id": "user_28",
        "photos": 4,
        "bio": True,
        "interests": True,
        "selfie_verified": True,
        "id_verified": True,
        "login_streak_days": 22,
        "response_rate_pct": 88,
        "reports_received": 3,
        "last_active_at": "2025-12-09T14:35:00Z"
    }

    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON;")

    result = upsert_user_and_wait(conn, sample, timeout=15.0)
    conn.close()
