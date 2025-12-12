# worker_debug.py — verbose debug worker (drop-in)
import sqlite3
import time
import traceback
import socket
import os
import json
from typing import Optional, Dict, Any

DB_FILE = "trust_engine.db"
POLL_INTERVAL = 1.0
CLAIM_TIMEOUT = 30.0

# Import compute_trust_score from your engine.
try:
    from trust_score import compute_trust_score
except Exception as e:
    raise RuntimeError("IMPORT ERROR: failed to import compute_trust_score from trust_score.py: " + repr(e))

def log(*args, **kwargs):
    print(*args, **kwargs, flush=True)

def claim_job(conn: sqlite3.Connection, worker_name: str) -> Optional[Dict[str, Any]]:
    cur = conn.cursor()
    try:
        conn.execute("BEGIN IMMEDIATE;")
        cur.execute("""
            SELECT id, user_id FROM recompute_jobs
            WHERE processed = 0 AND processing = 0
            ORDER BY enqueued_at ASC
            LIMIT 1;
        """)
        row = cur.fetchone()
        if row is None:
            conn.commit()
            return None
        job_id, user_id = row[0], row[1]
        cur.execute("""
            UPDATE recompute_jobs
            SET processing = 1, processor = ?, attempts = attempts + 1
            WHERE id = ?;
        """, (worker_name, job_id))
        conn.commit()
        cur.execute("SELECT id, user_id, enqueued_at, attempts FROM recompute_jobs WHERE id = ? LIMIT 1;", (job_id,))
        r = cur.fetchone()
        return {"id": r[0], "user_id": r[1], "enqueued_at": r[2], "attempts": r[3]}
    except sqlite3.OperationalError as oe:
        try:
            conn.rollback()
        except Exception:
            pass
        log("[claim_job] OperationalError (probably DB busy):", oe)
        return None
    except Exception as ee:
        try:
            conn.rollback()
        except Exception:
            pass
        raise

def fetch_user_as_dict(conn: sqlite3.Connection, user_id: str) -> Optional[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("""
        SELECT user_id, photos, bio_filled, interests_count,
               selfie_verified, id_verified, login_streak,
               response_rate_pct, reports_count, last_active_at
        FROM users WHERE user_id = ? LIMIT 1;
    """, (user_id,))
    r = cur.fetchone()
    if r is None:
        return None
    return {
        "user_id": r[0],
        "photos": int(r[1]) if r[1] is not None else 0,
        "bio": bool(r[2]),
        "interests": int(r[3]) if r[3] is not None else 0,
        "selfie_verified": bool(r[4]),
        "id_verified": bool(r[5]),
        "login_streak_days": int(r[6]) if r[6] is not None else 0,
        "response_rate_pct": int(r[7]) if r[7] is not None else 0,
        "reports_received": int(r[8]) if r[8] is not None else 0,
        "last_active_at": r[9],
    }

def upsert_trust_score_and_audit(conn: sqlite3.Connection, user_id: str, final_score: float, details_obj: Any):
    cur = conn.cursor()
    # Upsert trust_scores
    cur.execute(
        """
        INSERT INTO trust_scores(user_id, score, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(user_id) DO UPDATE SET
           score = excluded.score,
           updated_at = datetime('now');
        """,
        (user_id, final_score),
    )
    # Serialize details
    try:
        details_json = json.dumps(details_obj, default=str)
    except Exception as e:
        details_json = str(details_obj)
        log("[upsert_trust_score_and_audit] details JSON serialization failed:", e)
    cur.execute(
        """
        INSERT INTO trust_score_audit(user_id, new_score, details, computed_at)
        VALUES (?, ?, ?, datetime('now'));
        """,
        (user_id, final_score, details_json),
    )
    conn.commit()

def mark_job_done(conn: sqlite3.Connection, job_id: int):
    cur = conn.cursor()
    cur.execute("""
        UPDATE recompute_jobs
        SET processed = 1, processed_at = datetime('now'), processing = 0
        WHERE id = ?;
    """, (job_id,))
    conn.commit()

def mark_job_failed(conn: sqlite3.Connection, job_id: int, err: str):
    cur = conn.cursor()
    cur.execute("""
        UPDATE recompute_jobs
        SET processed = 2, last_error = ?, processed_at = datetime('now'), processing = 0
        WHERE id = ?;
    """, (err, job_id))
    conn.commit()

def worker_loop(worker_name: str):
    log("[worker_debug] starting", worker_name)
    log("cwd:", os.getcwd())
    log("db path:", DB_FILE)
    conn = sqlite3.connect(DB_FILE, timeout=CLAIM_TIMEOUT, isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        while True:
            job = claim_job(conn, worker_name)
            if job is None:
                # print a small heartbeat so we know worker is alive
                log("[worker_debug] no job — sleeping", POLL_INTERVAL)
                time.sleep(POLL_INTERVAL)
                continue
            job_id = job["id"]
            user_id = job["user_id"]
            log(f"[worker_debug] claimed job_id={job_id} user_id={user_id} attempts={job.get('attempts')}")
            try:
                user = fetch_user_as_dict(conn, user_id)
                log("[worker_debug] fetched user:", user)
                if user is None:
                    log("[worker_debug] user missing — marking job done", user_id)
                    mark_job_done(conn, job_id)
                    continue

                # Call your compute function
                try:
                    score_result = compute_trust_score(user)
                    log("[worker_debug] compute_trust_score returned type:", type(score_result))
                    try:
                        # Try to extract final_score and details generously
                        final_score = None
                        details_obj = None
                        if isinstance(score_result, dict):
                            final_score = score_result.get("final_score") or score_result.get("finalScore") or score_result.get("final")
                            details_obj = score_result.get("breakdown") or score_result
                        else:
                            final_score = getattr(score_result, "final_score", None) or getattr(score_result, "finalScore", None) or getattr(score_result, "final", None)
                            details_obj = getattr(score_result, "breakdown", None) or getattr(score_result, "__dict__", score_result)
                        log("[worker_debug] extracted final_score:", final_score)
                    except Exception as e:
                        log("[worker_debug] error extracting fields from score_result:", e)
                        raise
                except Exception as e:
                    log("[worker_debug] compute_trust_score raised exception:", e)
                    raise

                if final_score is None:
                    raise RuntimeError(f"final_score is None — compute_trust_score returned: {score_result!r}")

                # Persist
                upsert_trust_score_and_audit(conn, user_id, float(final_score), details_obj)
                mark_job_done(conn, job_id)
                log(f"[worker_debug] DONE job={job_id} user={user_id} score={final_score}")
            except Exception as e:
                tb = traceback.format_exc()
                log(f"[worker_debug] error processing job {job_id}: {e}\n{tb}")
                try:
                    mark_job_failed(conn, job_id, str(e))
                except Exception as ee:
                    log("[worker_debug] failed to mark job failed:", ee)
    finally:
        conn.close()

if __name__ == "__main__":
    name = f"{socket.gethostname()}-{os.getpid()}"
    worker_loop(name)
