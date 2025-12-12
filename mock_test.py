from trust_score import compute_trust_score, parse_iso_datetime
from datetime import datetime, timezone

def ask_int(label,default=0):
    val = input(f"{label} [{default}]: ").strip()
    if val == "":
        return default
    try:
        return int(val)
    except:
        print("Invalid Input")
        return default
    
def ask_bool(label,default=False):
    d = "Y" if default else "N"
    val = input(f"{label} (Y/N) [{d}]: ").strip().lower()
    if val == "":
        return default
    return val in ["y","yes"]

def main():
    print("=== Trust Score Input ===")

    user = {}

    user["user_id"] = input("User ID [user_cli]: ").strip() or "user_cli"
    user["photos"] = ask_int("Number of photos (0–8)", 6)
    user["bio"] = ask_bool("Bio present", True)
    user["interests"] = ask_bool("Interests present", True)
    user["selfie_verified"] = ask_bool("Selfie verified", True)
    user["id_verified"] = ask_bool("ID verified", False)
    user["login_streak_days"] = ask_int("Login streak days", 7)
    user["response_rate_pct"] = ask_int("Response rate % (0–100)", 80)
    user["reports_received"] = ask_int("Reports received", 0)

    default_last_active = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    la = input(f"Last active at (ISO) [{default_last_active}]: ").strip() or default_last_active

    try:
        parse_iso_datetime(la)
    except:
        print("Invalid ISO format. Using current time.")
        la = default_last_active

    user["last_active_at"] = la

    print("\n=== Computing Score... ===\n")

    result = compute_trust_score(user)

    print("Final Trust Score Output:\n")
    print(f"User ID: {result.user_id}")
    print(f"Profile Score: {result.profile_score}")
    print(f"Verification Score: {result.verification_score}")
    print(f"Activity Score: {result.activity_score}")
    print(f"Raw Total Before Decay: {result.raw_total}")
    print(f"Decay Applied: {result.decay_applied}")
    print(f"Final Score: {result.final_score}")
    print(f"Badges: {result.badges}")
    print("\nDetails:", result.breakdown)

if __name__ == "__main__":
    main()