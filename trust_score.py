"""
trust_score.py

Single-source trust score calculation engine.

Usage (example):
  python trust_score.py dummy_users.json trust_scores_output.json

Functions:
  - compute_profile_score(user)
  - compute_verification_score(user)
  - compute_activity_score(user)
  - apply_inactivity_decay(score, last_active_iso, reference_dt)
  - assign_badges(result, user, reference_dt)
  - compute_trust_score(user, reference_dt)
"""

from __future__ import annotations
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple

# -------------------------
# Configuration / constants
# -------------------------
MAX_SCORE = 100

# component max points
PROFILE_MAX = 30
VERIFICATION_MAX = 40
ACTIVITY_MAX = 30

# profile split
PHOTOS_MAX_POINTS = 20   # for up to 6 photos
PHOTOS_CAP = 6
BIO_POINTS = 5
INTERESTS_POINTS = 5

# verification split
SELFIE_POINTS = 20
ID_POINTS = 20

# activity sub-splits (sum to 30)
ACTIVITY_LOGIN_STREAK_MAX = 20   # mapped from 0..30 days
ACTIVITY_STREAK_CAP_DAYS = 30
ACTIVITY_RESPONSE_MAX = 10       # mapped from 0..100%
ACTIVITY_REPORTS_PENALTY_MAX = 8 # up to -8 for 5+ reports
ACTIVITY_REPORTS_CAP = 5

# decay
DECAY_PER_WEEK = 5  # -5 per full inactive week

# badge thresholds
BADGE_THRESHOLDS = {
    "Verified User": {"min_score": 85, "require_id_verification": True},
    "Trusted Member": {"min_score": 70},
    "Active Dater": {"min_score": 60, "recent_days": 7}
}

# -------------------------
# Data class for result
# -------------------------
@dataclass
class TrustScoreResult:
    user_id: str
    profile_score: float
    verification_score: float
    activity_score: float
    raw_total: float
    decay_applied: float
    final_score: float
    badges: List[str]
    breakdown: Dict[str, Any]

# -------------------------
# Helper parsers
# -------------------------
def parse_iso_datetime(s: Optional[str]) -> Optional[datetime]:
    if s is None:
        return None
    # Accept YYYY-MM-DD or full ISO with timezone Z
    try:
        # Attempt full ISO parse
        if s.endswith("Z"):
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except Exception:
        # Fallback: try date-only YYYY-MM-DD
        try:
            dt = datetime.strptime(s, "%Y-%m-%d")
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            raise ValueError(f"Unrecognized ISO datetime: {s}")

# -------------------------
# Component calculators
# -------------------------
def compute_profile_score(user: Dict[str, Any]) -> float:
    # Photos: linear up to PHOTOS_CAP -> PHOTOS_MAX_POINTS
    photos = int(user.get("photos", 0) or 0)
    photos_points = min(photos, PHOTOS_CAP) / PHOTOS_CAP * PHOTOS_MAX_POINTS

    bio_points = BIO_POINTS if bool(user.get("bio")) else 0
    interests_points = INTERESTS_POINTS if bool(user.get("interests")) else 0

    total = photos_points + bio_points + interests_points
    # clamp and round
    return round(max(0.0, min(PROFILE_MAX, total)), 2)

def compute_verification_score(user: Dict[str, Any]) -> float:
    selfie = bool(user.get("selfie_verified"))
    idv = bool(user.get("id_verified"))
    total = (SELFIE_POINTS if selfie else 0) + (ID_POINTS if idv else 0)
    return round(max(0.0, min(VERIFICATION_MAX, total)), 2)

def compute_activity_score(user: Dict[str, Any]) -> Tuple[float, Dict[str, float]]:
    # login streak mapping 0..ACTIVITY_STREAK_CAP_DAYS -> 0..ACTIVITY_LOGIN_STREAK_MAX
    streak_days = int(user.get("login_streak_days", 0) or 0)
    streak_score = min(streak_days, ACTIVITY_STREAK_CAP_DAYS) / ACTIVITY_STREAK_CAP_DAYS * ACTIVITY_LOGIN_STREAK_MAX

    # response rate 0..100 -> 0..ACTIVITY_RESPONSE_MAX
    resp = int(user.get("response_rate_pct", 0) or 0)
    resp_clamped = max(0, min(resp, 100))
    resp_score = resp_clamped / 100.0 * ACTIVITY_RESPONSE_MAX

    # reports: 0..ACTIVITY_REPORTS_CAP -> 0..-ACTIVITY_REPORTS_PENALTY_MAX
    reports = int(user.get("reports_received", 0) or 0)
    reports_clamped = min(reports, ACTIVITY_REPORTS_CAP)
    reports_penalty = - (reports_clamped / ACTIVITY_REPORTS_CAP) * ACTIVITY_REPORTS_PENALTY_MAX

    total = streak_score + resp_score + reports_penalty
    total = max(-ACTIVITY_REPORTS_PENALTY_MAX, total)  # lower bound (avoid extreme negatives)
    total = min(ACTIVITY_MAX, total)
    return round(total, 2), {
        "streak_score": round(streak_score,2),
        "response_score": round(resp_score,2),
        "reports_penalty": round(reports_penalty,2)
    }

# -------------------------
# Decay and badges
# -------------------------
def apply_inactivity_decay(base_score: float, last_active_iso: Optional[str], reference_dt: Optional[datetime]=None) -> Tuple[float, float]:
    """
    Returns (new_score, decay_applied)
    - last_active_iso: ISO date string (YYYY-MM-DD or full)
    - decay per full week of inactivity: DECAY_PER_WEEK
    """
    if reference_dt is None:
        reference_dt = datetime.now(timezone.utc)

    if not last_active_iso:
        # If no last_active value, do not apply decay automatically (policy choice).
        return round(max(0.0, min(MAX_SCORE, base_score)), 2), 0.0

    last_active_dt = parse_iso_datetime(last_active_iso)
    days_inactive = (reference_dt - last_active_dt).days
    full_weeks = days_inactive // 7
    decay = full_weeks * DECAY_PER_WEEK
    new_score = max(0.0, base_score - decay)
    new_score = min(MAX_SCORE, new_score)
    return round(new_score,2), float(decay)

def assign_badges(result: TrustScoreResult, user: Dict[str, Any], reference_dt: Optional[datetime]=None) -> List[str]:
    if reference_dt is None:
        reference_dt = datetime.now(timezone.utc)

    badges: List[str] = []
    score = result.final_score
    id_verified = bool(user.get("id_verified"))
    selfie = bool(user.get("selfie_verified"))

    # Verified User: require id_verified True and score >= threshold
    vcfg = BADGE_THRESHOLDS["Verified User"]
    if id_verified and score >= vcfg["min_score"]:
        badges.append("Verified User")

    # Trusted Member
    if score >= BADGE_THRESHOLDS["Trusted Member"]["min_score"]:
        badges.append("Trusted Member")

    # Active Dater: require recent activity
    adcfg = BADGE_THRESHOLDS["Active Dater"]
    last_active_iso = user.get("last_active_at")
    recent = False
    if last_active_iso:
        last_active_dt = parse_iso_datetime(last_active_iso)
        days_ago = (reference_dt - last_active_dt).days
        recent = days_ago <= adcfg["recent_days"]
    if score >= adcfg["min_score"] and recent:
        badges.append("Active Dater")

    return badges

# -------------------------
# Orchestrator
# -------------------------
def compute_trust_score(user: Dict[str, Any], reference_dt: Optional[datetime]=None) -> TrustScoreResult:
    """
    Compute detailed trust score for a single user dict.
    """
    if reference_dt is None:
        reference_dt = datetime.now(timezone.utc)

    uid = user.get("user_id", "unknown")

    profile_score = compute_profile_score(user)
    verification_score = compute_verification_score(user)
    activity_score, activity_breakdown = compute_activity_score(user)

    raw_total = profile_score + verification_score + activity_score
    # cap raw_total before decay
    raw_total = max(0.0, min(MAX_SCORE, raw_total))
    raw_total = round(raw_total, 2)

    final_score, decay_applied = apply_inactivity_decay(raw_total, user.get("last_active_at"), reference_dt)

    # build result
    result = TrustScoreResult(
        user_id=uid,
        profile_score=profile_score,
        verification_score=verification_score,
        activity_score=activity_score,
        raw_total=raw_total,
        decay_applied=decay_applied,
        final_score=final_score,
        badges=[],
        breakdown={
            "activity_breakdown": activity_breakdown,
            "last_active_at": user.get("last_active_at")
        }
    )
    # assign badges (based on final_score and attributes)
    result.badges = assign_badges(result, user, reference_dt)
    return result

# -------------------------
# CLI / file helpers
# -------------------------
def compute_batch_from_file(input_json_path: str, output_json_path: str, reference_dt: Optional[datetime]=None) -> None:
    with open(input_json_path, "r", encoding="utf-8") as f:
        users = json.load(f)
    if reference_dt is None:
        reference_dt = datetime.now(timezone.utc)

    results = []
    for u in users:
        res = compute_trust_score(u, reference_dt)
        results.append(asdict(res))
    # write to output
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"Wrote {len(results)} trust score results to {output_json_path}")
