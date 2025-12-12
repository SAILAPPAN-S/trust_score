# Trust Score Engine — Event-Driven Background Scoring System

This project implements a **real-time trust score engine** for a dating application.  
The system recalculates a user’s trust score **whenever their profile changes**, using a reliable and scalable pipeline:

**SQLite Triggers → Job Queue → Python Worker → Trust Score Engine → Database Updates**

This architecture ensures fast writes, asynchronous-heavy computation, clarity, and auditability.

---

## 1. System Overview

### Components:
1. **SQLite Database**
   - Stores `users`, `trust_scores`, `trust_score_audit`, `recompute_jobs`.
   - Has **AFTER INSERT/UPDATE triggers** that enqueue a recompute job.

2. **Python Trust Score Engine (`trust_score.py`)**
   - Computes:
     - Profile score  
     - Verification score  
     - Activity score  
     - Inactivity decay  
     - Breakdown + badges  

3. **Job Queue (`recompute_jobs`)**
   - Each time a user is updated, SQLite triggers push a row into this table.
   - Prevents duplicate pending jobs.

4. **Worker (`worker.py` or `worker_debug.py`)**
   - Runs continuously.
   - Picks up queued jobs.
   - Computes the trust score.
   - Updates:
     - `trust_scores`
     - `trust_score_audit`

5. **User Upsert Module (`db_upsert.py`)**
   - Handles INSERT/UPDATE into `users`.
   - Optionally waits for the worker to finish processing.

---

## 2. Database Schema

Tables created:

- `users`
- `trust_scores`
- `trust_score_audit`
- `recompute_jobs`

Triggers created:

- `trg_enqueue_recompute_on_user_insert`
- `trg_enqueue_recompute_on_user_update`

These triggers **do not compute the score** — they only enqueue jobs.

---

## 3. How the Pipeline Works

### Step 1 — App updates a user
`db_upsert.py` does:
```
INSERT ... ON CONFLICT DO UPDATE
```

### Step 2 — SQLite Trigger fires
Adds a job to `recompute_jobs`:
```
(user_id='xxxxx', processed=0, processing=0)
```

### Step 3 — Worker detects pending job
Worker flow:
- Claim job
- Fetch user data
- Compute trust score
- Insert/Update `trust_scores`
- Insert into `trust_score_audit`
- Mark job as processed

### Step 4 — Application reads trust score
Apps can call:
- `get_trust_score(conn, user_id)`
- `get_audit_rows(conn, user_id)`

---

## 4. Project Structure

```
trust_score_engine/
│
├── trust_score.py            # Main scoring logic
├── db_upsert.py              # Insert/update users, helpers, optional wait
├── worker.py                 # Lite worker
├── worker_debug.py           # Verbose debug worker
├── worker_once.py            # One-shot job processor
├── create_recompute.py       # Creates job queue + triggers
├── insert_user.py            # Example: insert test user
├── update_user_28.py         # Example: update existing user
└── trust_engine.db           # SQLite DB (auto-generated)
```

---

## 5. Installing Requirements

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

If no requirements file, you only need Python standard library.

---

## 6. Initial Setup (Run Once)

Inside the project folder:

```bash
python3 create_recompute.py
```

This creates:
- `recompute_jobs`
- enqueue triggers

---

## 7. Start the Worker (Always Running)

```bash
python3 -u worker.py
```

Or verbose mode:

```bash
python3 -u worker_debug.py
```

---

## 8. Insert or Update a User (Triggers Recompute)

### Option A — Using `db_upsert.py`
```bash
python3 db_upsert.py
```

### Option B — Update a single field
```bash
python3 update_user_28.py
```

### Option C — Insert a new test user
```bash
python3 insert_user.py
```

---

## 9. Checking Database Values

```bash
python3 - <<'PY'
import sqlite3
c = sqlite3.connect("trust_engine.db")
print("users:", list(c.execute("SELECT * FROM users")))
print("scores:", list(c.execute("SELECT * FROM trust_scores")))
print("audit:", list(c.execute("SELECT * FROM trust_score_audit")))
print("jobs:", list(c.execute("SELECT * FROM recompute_jobs")))
c.close()
PY
```

---

## 10. Integration into Your Application

Call:

```python
upsert_user(conn, user_json)
```

or:

```python
upsert_user_and_wait(conn, user_json)
```

Worker handles recomputation automatically.

---

## 11. Key Advantages

- Non-blocking writes  
- Asynchronous heavy computation  
- Full auditability  
- Scalable (multiple workers)  
- Decoupled engine & database  

---

## 12. Troubleshooting

| Issue | Cause | Fix |
|------|--------|-----|
| Worker says “no such table: recompute_jobs” | DB not initialized | Run `create_recompute.py` |
| Worker prints nothing | No pending jobs | Upsert or update a user |
| Score not updating | Worker not running | Start worker |
| Duplicate jobs | Trigger prevented them | Normal behavior |

---

