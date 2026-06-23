"""
refresh.py - Fetch today's Garmin data for all users and export to JSON.

Run this whenever you want to update the public leaderboard:
    python refresh.py

Does everything in one shot:
  - Logs into Garmin once per user (using saved tokens, no password)
  - Fetches today's sleep and activity data
  - Updates local cache files in data/
  - Writes scores.json and activities.json ready to copy to GitHub Pages

No need to run app.py first.
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from garminconnect import Garmin

ROOT = Path(__file__).parent
TOKENS_DIR = ROOT / "tokens"
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

SCORES_OUT = ROOT / "scores.json"
ACTIVITIES_OUT = ROOT / "activities.json"

HISTORY_DAYS = 7

CYCLING_TYPES = {
    "cycling", "road_biking", "indoor_cycling", "mountain_biking",
    "gravel_cycling", "virtual_ride",
}
RUNNING_TYPES = {
    "running", "trail_running", "treadmill_running",
    "track_running", "virtual_run", "indoor_running",
}
SWIMMING_TYPES = {
    "lap_swimming", "open_water_swimming", "swimming",
}


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def fmt_duration(seconds):
    if not seconds:
        return "0m"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}h {m}m" if h else f"{m}m"


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def load_json(path, default):
    if not path.exists():
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# Sleep
# ---------------------------------------------------------------------------

def extract_sleep_summary(raw):
    dto = (raw or {}).get("dailySleepDTO") or {}
    scores = dto.get("sleepScores") or {}
    overall = scores.get("overall") or {}
    score = overall.get("value")
    if score is None:
        return None
    total = dto.get("sleepTimeSeconds") or 0
    deep  = dto.get("deepSleepSeconds") or 0
    light = dto.get("lightSleepSeconds") or 0
    rem   = dto.get("remSleepSeconds") or 0
    awake = dto.get("awakeSleepSeconds") or 0
    def pct(p): return round(100 * p / total, 1) if total else 0
    return {
        "date": dto.get("calendarDate"),
        "score": score,
        "quality": overall.get("qualifierKey"),
        "total": total, "total_str": fmt_duration(total),
        "deep": deep, "deep_str": fmt_duration(deep), "deep_pct": pct(deep),
        "light": light, "light_str": fmt_duration(light), "light_pct": pct(light),
        "rem": rem, "rem_str": fmt_duration(rem), "rem_pct": pct(rem),
        "awake": awake, "awake_str": fmt_duration(awake),
        "avg_hr": dto.get("avgHeartRate"),
        "avg_hrv": (raw or {}).get("avgOvernightHrv"),
        "hrv_status": (raw or {}).get("hrvStatus"),
        "avg_stress": dto.get("avgSleepStress"),
        "avg_spo2": dto.get("averageSpO2Value"),
        "avg_respiration": dto.get("averageRespirationValue"),
        "resting_hr": (raw or {}).get("restingHeartRate"),
        "body_battery_change": (raw or {}).get("bodyBatteryChange"),
    }


def fetch_sleep(client, name, today):
    cache_file = DATA_DIR / f"{name}.json"
    cache = load_json(cache_file, {"history": {}})
    if "history" not in cache:
        cache = {"history": {}}
    history = cache["history"]

    today_str = today.isoformat()
    target_dates = [(today - timedelta(days=i)).isoformat() for i in range(HISTORY_DAYS)]
    to_fetch = [d for d in target_dates if d == today_str or d not in history or history.get(d) is None]

    for d_str in to_fetch:
        try:
            raw = client.get_sleep_data(d_str)
            history[d_str] = extract_sleep_summary(raw)
            status = history[d_str]["score"] if history[d_str] else "no data"
            print(f"    sleep {d_str}: {status}")
        except Exception as e:
            if d_str not in history:
                history[d_str] = None
            print(f"    sleep {d_str}: error - {e}")

    cutoff = (today - timedelta(days=30)).isoformat()
    cache["history"] = {d: v for d, v in history.items() if d >= cutoff}
    cache["fetched_at"] = datetime.now().isoformat(timespec="seconds")
    save_json(cache_file, cache)
    return cache


def build_sleep_payload(name, cache):
    history = cache.get("history", {})
    today = date.today()
    window = []
    for i in range(HISTORY_DAYS - 1, -1, -1):
        d_str = (today - timedelta(days=i)).isoformat()
        entry = history.get(d_str)
        # Include full entry so history page can show quality, duration, stages.
        # Sparkline still works since it only reads d.score.
        if entry:
            window.append(entry)
        else:
            window.append({"date": d_str, "score": None})
    latest = None
    for d in reversed(window):
        if d.get("score") is not None:
            latest = d
            break
    valid = [d["score"] for d in window if d.get("score") is not None]
    weekly_avg = round(sum(valid) / len(valid), 1) if valid else None
    return {"name": name, "latest": latest, "weekly_avg": weekly_avg, "history": window}


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------

def fetch_activities(client, name, today):
    cache_file = DATA_DIR / f"{name}_activities.json"
    cache = load_json(cache_file, {"steps": {}, "activities": []})
    steps_cache = cache.get("steps", {})
    acts_cache  = cache.get("activities", [])

    today_str = today.isoformat()
    start_str = (today - timedelta(days=HISTORY_DAYS - 1)).isoformat()
    target_dates = [(today - timedelta(days=i)).isoformat() for i in range(HISTORY_DAYS)]

    # Steps: re-fetch today, use cache for older days
    # Re-fetch today AND yesterday every run (Garmin keeps syncing steps
    # throughout the day, so yesterday's number isn't final until today).
    # Anything missing entirely also gets fetched.
    recent_cutoff = (today - timedelta(days=1)).isoformat()
    dates_needing_steps = [
        d for d in target_dates
        if d >= recent_cutoff or d not in steps_cache
    ]
    for d_str in dates_needing_steps:
        try:
            summary = client.get_user_summary(d_str)
            steps_cache[d_str] = summary.get("totalSteps") or 0
            print(f"    steps  {d_str}: {steps_cache[d_str]:,}")
        except Exception as e:
            if d_str not in steps_cache:
                steps_cache[d_str] = None
            print(f"    steps  {d_str}: error - {e}")

    # Activities: fetch full window and replace EVERYTHING in that window.
    # get_activities_by_date returns the complete set for start_str..today_str,
    # so we drop all cached entries within that range (keeping only older ones)
    # before re-adding. Dropping just today would duplicate the prior 6 days.
    try:
        raw_acts = client.get_activities_by_date(start_str, today_str) or []
        kept = [a for a in acts_cache if a.get("date", "") < start_str]
        added = 0
        for act in raw_acts:
            act_type = ((act.get("activityType") or {}).get("typeKey") or "").lower()
            act_date = (act.get("startTimeLocal") or "")[:10]
            if act_date not in target_dates:
                continue
            if act_type in RUNNING_TYPES:
                category = "running"
            elif act_type in CYCLING_TYPES:
                category = "cycling"
            elif act_type in SWIMMING_TYPES:
                category = "swimming"
            else:
                continue
            dist_km = round((act.get("distance") or 0) / 1000, 2)
            kept.append({
                "date": act_date,
                "type": category,
                "distance_km": dist_km,
                "duration_s": int(act.get("duration") or 0),
                "raw_type": act_type,
            })
            added += 1
        acts_cache = kept
        print(f"    activities fetched: {len(raw_acts)} total, {added} run/cycle/swim kept")
    except Exception as e:
        print(f"    activities: error - {e}")

    cutoff = (today - timedelta(days=30)).isoformat()
    steps_cache = {d: v for d, v in steps_cache.items() if d >= cutoff}
    acts_cache  = [a for a in acts_cache if a.get("date", "") >= cutoff]

    cache = {
        "steps": steps_cache,
        "activities": acts_cache,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }
    save_json(cache_file, cache)
    return cache


def build_activity_payload(name, cache):
    today = date.today()
    target_dates = {(today - timedelta(days=i)).isoformat() for i in range(HISTORY_DAYS)}
    steps_cache = cache.get("steps", {})
    acts = cache.get("activities", [])

    weekly_steps = sum(v for d, v in steps_cache.items() if d in target_dates and v is not None)
    days_with_steps = sum(1 for d in target_dates if steps_cache.get(d) is not None)
    avg_steps = round(weekly_steps / days_with_steps) if days_with_steps else None

    def total_km(cat):
        return round(sum(a["distance_km"] for a in acts if a["type"] == cat and a["date"] in target_dates), 1)
    def count(cat):
        return sum(1 for a in acts if a["type"] == cat and a["date"] in target_dates)

    return {
        "name": name,
        "weekly_steps": weekly_steps,
        "avg_daily_steps": avg_steps,
        "running_km": total_km("running"),
        "running_sessions": count("running"),
        "cycling_km": total_km("cycling"),
        "cycling_sessions": count("cycling"),
        "swimming_km": total_km("swimming"),
        "swimming_sessions": count("swimming"),
    }



def compute_wooden_spoon(histories, today):
    """Lowest single night score across given users in the last 30 days."""
    cutoff_30 = (today - timedelta(days=30)).isoformat()
    spoon = None
    for name, hist in histories.items():
        for d_str, entry in hist.items():
            if d_str < cutoff_30 or not entry or entry.get("score") is None:
                continue
            if spoon is None or entry["score"] < spoon["score"]:
                spoon = {
                    "name": name,
                    "score": entry["score"],
                    "date": d_str,
                    "quality": entry.get("quality"),
                }
    return spoon


def compute_streak(histories, today):
    """Most consecutive nights as SOLE top scorer among given users in the
    last 30 days. A tie for first or a day with no data breaks every streak.
    Returns None unless the best streak is 2+ nights."""
    current_name, current_len = None, 0
    best_name, best_len, best_end = None, 0, None
    for i in range(29, -1, -1):
        d_str = (today - timedelta(days=i)).isoformat()
        day_scores = {}
        for uname, hist in histories.items():
            entry = hist.get(d_str)
            if entry and entry.get("score") is not None:
                day_scores[uname] = entry["score"]

        winner = None
        if day_scores:
            top = max(day_scores.values())
            leaders = [u for u, s in day_scores.items() if s == top]
            if len(leaders) == 1:
                winner = leaders[0]

        if winner and winner == current_name:
            current_len += 1
        elif winner:
            current_name, current_len = winner, 1
        else:
            current_name, current_len = None, 0

        if current_len > best_len:
            best_name, best_len, best_end = current_name, current_len, d_str

    if best_name and best_len >= 2:
        return {"name": best_name, "days": best_len, "end_date": best_end}
    return None


def compute_nights_won(histories, today):
    """Count nights each user had the sole top score in the last 30 days.
    Returns {"counts": [{"name", "nights"}...] sorted desc, "no_winner": N}."""
    counts = {name: 0 for name in histories}
    no_winner = 0
    days_with_data = 0
    for i in range(29, -1, -1):
        d_str = (today - timedelta(days=i)).isoformat()
        day_scores = {}
        for uname, hist in histories.items():
            entry = hist.get(d_str)
            if entry and entry.get("score") is not None:
                day_scores[uname] = entry["score"]
        if not day_scores:
            continue
        days_with_data += 1
        top = max(day_scores.values())
        leaders = [u for u, s in day_scores.items() if s == top]
        if len(leaders) == 1:
            counts[leaders[0]] += 1
        else:
            no_winner += 1
    result = [{"name": n, "nights": c} for n, c in counts.items()]
    result.sort(key=lambda x: -x["nights"])
    return {"counts": result, "no_winner": no_winner, "days_with_data": days_with_data}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not TOKENS_DIR.exists() or not any(TOKENS_DIR.iterdir()):
        print("No users found. Run setup.py <name> first.")
        return

    today = date.today()
    now = datetime.now().isoformat(timespec="seconds")
    sleep_users = []
    activity_users = []

    for user_dir in sorted(TOKENS_DIR.iterdir()):
        if not user_dir.is_dir():
            continue
        name = user_dir.name
        print(f"\n{name}")
        print(f"  Logging in...")
        try:
            client = Garmin()
            client.login(str(user_dir))
            print(f"  OK - fetching data...")

            sleep_cache = fetch_sleep(client, name, today)
            act_cache   = fetch_activities(client, name, today)

            sleep_users.append(build_sleep_payload(name, sleep_cache))
            activity_users.append(build_activity_payload(name, act_cache))

        except Exception as e:
            print(f"  Login failed: {e}")

    if not sleep_users:
        print("\nNo data fetched. Check your tokens.")
        return

    # Sort sleep by last night's score
    sleep_users.sort(key=lambda u: -(u["latest"]["score"] if u.get("latest") else -999))
    activity_users.sort(key=lambda u: u["name"])

    # Load all 30-day histories once
    all_histories = {}
    for user_dir in sorted(TOKENS_DIR.iterdir()):
        if not user_dir.is_dir():
            continue
        cache = load_json(DATA_DIR / f"{user_dir.name}.json", {"history": {}})
        all_histories[user_dir.name] = cache.get("history", {})

    # Optional names.json: {"Kyle": "Sleep Lord 3000", ...} for display names
    display_names = load_json(ROOT / "names.json", {})

    # Optional groups.json: {"groupA": ["Kyle", ...], "groupB": [...]}
    groups = load_json(ROOT / "groups.json", {})
    if groups:
        known = set(all_histories.keys())
        for gname, members in groups.items():
            for m in members:
                if m not in known:
                    print(f"  [warn] groups.json: '{m}' in {gname} has no token/data")

    # Awards for everyone ("all") plus per group
    wooden_spoon = compute_wooden_spoon(all_histories, today)
    longest_streak = compute_streak(all_histories, today)
    nights_won = compute_nights_won(all_histories, today)
    if wooden_spoon:
        print(f"  Wooden spoon: {wooden_spoon['name']} scored {wooden_spoon['score']} on {wooden_spoon['date']}")
    if longest_streak:
        print(f"  Longest streak: {longest_streak['name']} held 1st for {longest_streak['days']} nights")

    group_awards = {}
    for gname, members in groups.items():
        sub = {n: h for n, h in all_histories.items() if n in members}
        group_awards[gname] = {
            "wooden_spoon": compute_wooden_spoon(sub, today),
            "longest_streak": compute_streak(sub, today),
            "nights_won": compute_nights_won(sub, today),
        }

    save_json(SCORES_OUT, {
        "users": sleep_users,
        "wooden_spoon": wooden_spoon,
        "longest_streak": longest_streak,
        "nights_won": nights_won,
        "groups": groups,
        "group_awards": group_awards,
        "display_names": display_names,
        "updated_at": now,
    })
    save_json(ACTIVITIES_OUT, {"users": activity_users, "groups": groups, "display_names": display_names, "updated_at": now})

    print(f"\nDone.")
    print(f"  {SCORES_OUT.name} and {ACTIVITIES_OUT.name} are ready.")
    print(f"  Copy both to your GitHub Pages repo on the other VM and push.")


if __name__ == "__main__":
    main()
