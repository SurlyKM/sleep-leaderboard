"""
refresh.py - Fetch everyone's Garmin data via the official Personal API and
export the JSON the public leaderboard reads.

    python refresh.py

How it gets each person's key
-----------------------------
Keys come from an environment variable, never from a file in the repo. Set
ONE secret/variable called GARMIN_API_KEYS to a JSON object of name -> key:

In GitHub that is a single Actions secret (Settings -> Secrets and variables
-> Actions). Adding or removing a friend is a one-line edit to that secret, no
workflow change. For a local run you can instead drop the same JSON in
garmin_api_keys.json next to this script (it is gitignored), or export a
single GARMIN_PERSONAL_API_KEY plus GARMIN_PERSONAL_NAME for a solo test.

What it writes
--------------
  - data/<name>.json             sleep history cache (also the fallback if a
  - data/<name>_activities.json  key fails on a given run)
  - scores.json                  sleep leaderboard + awards
  - activities.json              weekly activity stats

The output shape is identical to the old garminconnect version, so the
frontend (index.html / history.html / activities.html) needs no changes.
"""

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from garmin_personal import (
    GarminPersonal, GarminAuthError,
    merge_extras,
    SLEEP_MAX, HRV_MAX, DAILY_MAX, STEPS_MAX,
)

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

SCORES_OUT = ROOT / "scores.json"
ACTIVITIES_OUT = ROOT / "activities.json"

HISTORY_DAYS = 7     # weekly window shown on the board
AWARD_DAYS = 30      # window for wooden spoon / streak / nights won


# ---------------------------------------------------------------------------
# Key loading
# ---------------------------------------------------------------------------

def load_keys():
    """Return {name: api_key}. Order of preference:
      1. GARMIN_API_KEYS env var (JSON object)         <- GitHub Actions
      2. garmin_api_keys.json next to this script       <- local, gitignored
      3. GARMIN_PERSONAL_API_KEY (+ GARMIN_PERSONAL_NAME, default 'Me')
    """
    raw = os.environ.get("GARMIN_API_KEYS")
    if raw:
        try:
            keys = json.loads(raw)
            if isinstance(keys, dict) and keys:
                return {str(k): str(v).strip() for k, v in keys.items()}
        except json.JSONDecodeError as e:
            raise SystemExit(f"GARMIN_API_KEYS is not valid JSON: {e}")

    local = ROOT / "garmin_api_keys.json"
    if local.exists():
        try:
            keys = json.loads(local.read_text())
            if isinstance(keys, dict) and keys:
                return {str(k): str(v).strip() for k, v in keys.items()}
        except json.JSONDecodeError as e:
            raise SystemExit(f"garmin_api_keys.json is not valid JSON: {e}")

    single = os.environ.get("GARMIN_PERSONAL_API_KEY")
    if single:
        name = os.environ.get("GARMIN_PERSONAL_NAME", "Me")
        return {name: single.strip()}

    raise SystemExit(
        "No API keys found. Set GARMIN_API_KEYS (JSON object of name -> key), "
        "or create garmin_api_keys.json, or set GARMIN_PERSONAL_API_KEY."
    )


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
# Per-user fetch
# ---------------------------------------------------------------------------

def fetch_user(client, today):
    """Fetch and assemble one person's sleep history + activity cache using
    an authenticated GarminPersonal client. Returns (sleep_cache, act_cache)."""
    sleep_start = (today - timedelta(days=SLEEP_MAX - 1)).isoformat()
    end = today.isoformat()
    hrv_start = (today - timedelta(days=HRV_MAX - 1)).isoformat()
    daily_start = (today - timedelta(days=DAILY_MAX - 1)).isoformat()
    steps_start = (today - timedelta(days=STEPS_MAX - 1)).isoformat()

    # Sleep is the one call that must succeed; the rest are best-effort extras.
    sleep = client.sleep_by_date(sleep_start, end)
    hrv = client.hrv_by_date(hrv_start, end)
    daily = client.daily_summaries(daily_start, end)
    merge_extras(sleep, hrv, daily)

    got = sum(1 for s in sleep.values() if s.get("score") is not None)
    hrv_hits = sum(1 for s in sleep.values() if s.get("avg_hrv") is not None)
    bb_hits = sum(1 for s in sleep.values() if s.get("body_battery_change") is not None)
    print(f"    sleep nights with a score: {got}  (hrv filled {hrv_hits}, body-battery {bb_hits})")

    sleep_cache = {
        "history": sleep,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }

    steps = client.steps_by_date(steps_start, end)
    acts = client.activities(sleep_start, end)
    print(f"    steps days: {len(steps)}, run/cycle/swim activities: {len(acts)}")
    act_cache = {
        "steps": steps,
        "activities": acts,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }
    return sleep_cache, act_cache


# ---------------------------------------------------------------------------
# Payload builders (unchanged output shape)
# ---------------------------------------------------------------------------

def build_sleep_payload(name, cache):
    history = cache.get("history", {})
    today = date.today()
    window = []
    for i in range(HISTORY_DAYS - 1, -1, -1):
        d_str = (today - timedelta(days=i)).isoformat()
        entry = history.get(d_str)
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


# ---------------------------------------------------------------------------
# Awards (unchanged logic)
# ---------------------------------------------------------------------------

def compute_wooden_spoon(histories, today):
    cutoff = (today - timedelta(days=AWARD_DAYS)).isoformat()
    spoon = None
    for name, hist in histories.items():
        for d_str, entry in hist.items():
            if d_str < cutoff or not entry or entry.get("score") is None:
                continue
            if spoon is None or entry["score"] < spoon["score"]:
                spoon = {"name": name, "score": entry["score"],
                         "date": d_str, "quality": entry.get("quality")}
    return spoon


def compute_streak(histories, today):
    current_name, current_len = None, 0
    best_name, best_len, best_end = None, 0, None
    for i in range(AWARD_DAYS - 1, -1, -1):
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
    counts = {name: 0 for name in histories}
    no_winner = 0
    days_with_data = 0
    for i in range(AWARD_DAYS - 1, -1, -1):
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
    keys = load_keys()
    today = date.today()
    now = datetime.now().isoformat(timespec="seconds")

    sleep_users = []
    activity_users = []
    token_errors = []  # names whose key failed this run (kept for the frontend)

    for name in sorted(keys):
        print(f"\n{name}")
        try:
            print("  Exchanging key for a token...")
            client = GarminPersonal(keys[name])
            print("  OK - fetching data...")
            sleep_cache, act_cache = fetch_user(client, today)

            # Only overwrite the committed cache when we actually got sleep
            # data, so a bad/empty run never wipes a good history.
            if sleep_cache["history"]:
                save_json(DATA_DIR / f"{name}.json", sleep_cache)
            else:
                sleep_cache = load_json(DATA_DIR / f"{name}.json", {"history": {}})
            save_json(DATA_DIR / f"{name}_activities.json", act_cache)

            sleep_users.append(build_sleep_payload(name, sleep_cache))
            activity_users.append(build_activity_payload(name, act_cache))

        except Exception as e:
            # Key expired/revoked, or Garmin refused. Keep the person on the
            # board from cache, flagged so the page shows "needs reconnecting".
            kind = "key rejected" if isinstance(e, GarminAuthError) else "fetch failed"
            print(f"  {kind}: {e}")
            token_errors.append(name)
            sleep_cache = load_json(DATA_DIR / f"{name}.json", {"history": {}})
            act_cache = load_json(DATA_DIR / f"{name}_activities.json",
                                  {"steps": {}, "activities": []})
            sp = build_sleep_payload(name, sleep_cache)
            sp["token_error"] = True
            sleep_users.append(sp)
            ap = build_activity_payload(name, act_cache)
            ap["token_error"] = True
            activity_users.append(ap)

    if not sleep_users:
        print("\nNo data at all. Check your keys.")
        return

    sleep_users.sort(key=lambda u: -(u["latest"]["score"] if u.get("latest") else -999))
    activity_users.sort(key=lambda u: u["name"])

    # 30-day histories for the awards: read every committed cache so a flagged
    # (key-failed) user still counts from their last good data.
    all_histories = {}
    for name in keys:
        cache = load_json(DATA_DIR / f"{name}.json", {"history": {}})
        all_histories[name] = cache.get("history", {})

    display_names = load_json(ROOT / "names.json", {})
    groups = load_json(ROOT / "groups.json", {})
    if groups:
        known = set(all_histories.keys())
        for gname, members in groups.items():
            for m in members:
                if m not in known:
                    print(f"  [warn] groups.json: '{m}' in {gname} has no key/data")

    wooden_spoon = compute_wooden_spoon(all_histories, today)
    longest_streak = compute_streak(all_histories, today)
    nights_won = compute_nights_won(all_histories, today)
    if wooden_spoon:
        print(f"\n  Wooden spoon: {wooden_spoon['name']} scored {wooden_spoon['score']} on {wooden_spoon['date']}")
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
        "token_errors": token_errors,
        "updated_at": now,
    })
    save_json(ACTIVITIES_OUT, {
        "users": activity_users,
        "groups": groups,
        "display_names": display_names,
        "token_errors": token_errors,
        "updated_at": now,
    })

    print(f"\nDone. {SCORES_OUT.name} and {ACTIVITIES_OUT.name} are ready.")
    if token_errors:
        print(f"  NEEDS RECONNECTING ({len(token_errors)}): {', '.join(token_errors)}")
        print("  These people need to generate a fresh Personal API key.")


if __name__ == "__main__":
    main()
