"""
garmin_personal.py - Thin client for Garmin's official Personal API.

Replaces the old garminconnect scraping + saved-token approach. Each person
provides a Personal API key (generated once from their own Garmin account).
The flow is two official steps, no scraping, no password, no MFA:

  1. POST the API key to the token endpoint     -> a 24h Bearer access token
  2. GET the data ranges with that Bearer token  -> sleep / hrv / steps / etc.

Everything here is stdlib only (urllib), so the GitHub Action needs no pip
installs. The key itself never appears in this file - it is passed in from an
environment variable (see refresh.py).

Docs: https://developer.garmin.com/gc-developer-program/personal/
"""

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://personal.connectapi.garmin.com"
TOKEN_URL = f"{BASE}/personalapitoken-service/personal-api/personal-api-key/token"

SLEEP_URL = f"{BASE}/sleep-service/sleep/dailySleepSummariesByDate"
HRV_URL = f"{BASE}/hrv-service/hrv/daily"                       # /{from}/{to}
DAILY_URL = f"{BASE}/usersummary-service/v2/dailysummaries"
STEPS_URL = f"{BASE}/usersummary-service/stats/steps/daily"      # /{from}/{to}
ACTS_URL = f"{BASE}/activitylist-service/activities/search/activities"

# Garmin range-endpoint ceilings (days). We stay at/under these.
SLEEP_MAX = 31
HRV_MAX = 31
DAILY_MAX = 31
STEPS_MAX = 28

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
# Small helpers
# ---------------------------------------------------------------------------

def fmt_duration(seconds):
    if not seconds:
        return "0m"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}h {m}m" if h else f"{m}m"


def parse_ts(v):
    """Bed/wake may arrive as epoch milliseconds or an ISO date-time string.
    Garmin's *Local values are already shifted to the user's local time, so we
    read them as UTC and drop the tzinfo to get the plain wall-clock time."""
    if v in (None, "", 0):
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000, tz=timezone.utc).replace(tzinfo=None)
    s = str(v).replace("Z", "").strip()
    try:
        return datetime.fromisoformat(s).replace(tzinfo=None)
    except ValueError:
        return None


class GarminAuthError(Exception):
    """Raised when a key cannot be exchanged for a token (expired/revoked key,
    or Garmin refusing the request). refresh.py treats this like the old
    'expired token' case so the person stays on the board, flagged."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class GarminPersonal:
    """One instance per person. Exchanges their key for a token on creation,
    then exposes range fetchers that return data keyed by calendar date."""

    def __init__(self, api_key, timeout=30):
        self.timeout = timeout
        self.token, self.scheme = self._get_token(api_key)

    # -- low level ----------------------------------------------------------

    def _request(self, url, method="GET", headers=None):
        req = urllib.request.Request(url, method=method, headers=headers or {})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")

    def _get_json(self, url, params=None):
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        status, body = self._request(
            url, headers={"Authorization": f"{self.scheme} {self.token}"}
        )
        if status != 200:
            raise RuntimeError(f"HTTP {status} from {url}\n{body[:300]}")
        if not body.strip():
            return None
        return json.loads(body)

    def _get_token(self, api_key):
        try:
            status, body = self._request(
                TOKEN_URL, method="POST",
                headers={"Authorization": f"ApiKey {api_key}"},
            )
        except Exception as e:  # network / HTTP error exchanging the key
            raise GarminAuthError(f"token request failed: {e}")
        if status != 200:
            raise GarminAuthError(f"token endpoint returned HTTP {status}: {body[:200]}")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            # Some responses are a bare token string, not JSON.
            tok = body.strip().strip('"')
            if not tok:
                raise GarminAuthError("empty token response")
            return tok, "Bearer"
        tok = data.get("access_token") or data.get("accessToken") or data.get("token")
        scheme = (data.get("token_type") or data.get("tokenType") or "Bearer").capitalize()
        if not tok:
            raise GarminAuthError(f"no token field in response: {list(data.keys())}")
        return tok, scheme

    # -- range fetchers (each returns {date_str: value/dict}) ---------------

    def sleep_by_date(self, start, end):
        """Full sleep detail per night. Returns {date: summary_dict}.
        This one call covers the leaderboard, history, awards and most of the
        detail tiles (score, quality, duration, stages, HR, stress, SpO2,
        respiration). Verified against real data."""
        days = self._get_json(SLEEP_URL, {"startDate": start, "endDate": end}) or []
        out = {}
        for d in days:
            summary = extract_sleep(d)
            if summary and summary.get("date"):
                out[summary["date"]] = summary
        return out

    def hrv_by_date(self, start, end):
        """Returns {date: {"avg_hrv", "hrv_status"}}. Best-effort: HRV only
        exists for devices/nights that measured it."""
        try:
            raw = self._get_json(f"{HRV_URL}/{start}/{end}")
        except Exception:
            return {}
        out = {}
        summaries = (raw or {}).get("hrvSummaries") or []
        for s in summaries:
            d = s.get("calendarDate")
            if d:
                out[d] = {"avg_hrv": s.get("lastNightAvg"), "hrv_status": s.get("status")}
        return out

    def daily_summaries(self, start, end):
        """Returns {date: {"resting_hr", "body_battery_change"}} from the v2
        daily summaries. body_battery_change here is the day's charged value
        (closest the Personal API exposes to the old overnight gain)."""
        try:
            raw = self._get_json(DAILY_URL, {"startDate": start, "endDate": end})
        except Exception:
            return {}
        rows = raw.get("data") if isinstance(raw, dict) else raw
        out = {}
        for row in rows or []:
            d = row.get("calendarDate")
            if not d:
                continue
            hr = row.get("heartRate") or {}
            bb = row.get("bodyBattery") or {}
            out[d] = {
                "resting_hr": hr.get("restingValue"),
                "body_battery_change": bb.get("chargedValue"),
            }
        return out

    def steps_by_date(self, start, end):
        """Returns {date: total_steps}."""
        try:
            rows = self._get_json(f"{STEPS_URL}/{start}/{end}") or []
        except Exception:
            return {}
        out = {}
        for r in rows:
            d = r.get("calendarDate")
            if d:
                out[d] = r.get("totalSteps") or 0
        return out

    def activities(self, start, end, limit=200):
        """Returns a list of {date, type, distance_km, duration_s, raw_type}
        for run/cycle/swim activities in the window."""
        try:
            raw = self._get_json(ACTS_URL, {
                "startDate": start, "endDate": end,
                "limit": limit, "start": 0,
            }) or []
        except Exception:
            return []
        acts = raw.get("activityList") if isinstance(raw, dict) else raw
        out = []
        for a in acts or []:
            type_key = ((a.get("activityType") or {}).get("typeKey") or "").lower()
            if type_key in RUNNING_TYPES:
                category = "running"
            elif type_key in CYCLING_TYPES:
                category = "cycling"
            elif type_key in SWIMMING_TYPES:
                category = "swimming"
            else:
                continue
            act_date = (a.get("startTimeLocal") or "")[:10]
            out.append({
                "date": act_date,
                "type": category,
                "distance_km": round((a.get("distance") or 0) / 1000, 2),
                "duration_s": int(a.get("duration") or 0),
                "raw_type": type_key,
            })
        return out


# ---------------------------------------------------------------------------
# Sleep extraction (module-level so it is unit-testable without a network)
# ---------------------------------------------------------------------------

def extract_sleep(d):
    """Map one Personal API DailySleepDTO into the flat summary dict the
    frontend already consumes. HRV, resting HR and body battery are filled in
    later from their own endpoints (merge_extras), so they default to None."""
    if not d:
        return None
    scores = d.get("sleepScores") or {}
    overall = scores.get("overall") or {}
    score = overall.get("value")
    if score is None:
        return None
    total = d.get("sleepTimeSeconds") or 0
    deep = d.get("deepSleepSeconds") or 0
    light = d.get("lightSleepSeconds") or 0
    rem = d.get("remSleepSeconds") or 0
    awake = d.get("awakeSleepSeconds") or 0

    def pct(p):
        return round(100 * p / total, 1) if total else 0

    return {
        "date": d.get("calendarDate"),
        "score": score,
        "quality": overall.get("qualifierKey"),
        "total": total, "total_str": fmt_duration(total),
        "deep": deep, "deep_str": fmt_duration(deep), "deep_pct": pct(deep),
        "light": light, "light_str": fmt_duration(light), "light_pct": pct(light),
        "rem": rem, "rem_str": fmt_duration(rem), "rem_pct": pct(rem),
        "awake": awake, "awake_str": fmt_duration(awake),
        "avg_hr": d.get("avgHeartRate"),
        "avg_hrv": None,            # filled from hrv_by_date
        "hrv_status": None,         # filled from hrv_by_date
        "avg_stress": d.get("avgSleepStress"),
        "avg_spo2": d.get("averageSpO2Value"),
        "avg_respiration": d.get("averageRespirationValue"),
        "resting_hr": None,         # filled from daily_summaries
        "body_battery_change": None,  # filled from daily_summaries
    }


def merge_extras(sleep_by_date, hrv_by_date, daily_by_date):
    """Fold HRV, resting HR and body battery into each night's summary in
    place, matched on calendar date. Missing days simply stay None."""
    for d, summary in sleep_by_date.items():
        hrv = hrv_by_date.get(d)
        if hrv:
            if hrv.get("avg_hrv") is not None:
                summary["avg_hrv"] = hrv["avg_hrv"]
            if hrv.get("hrv_status"):
                summary["hrv_status"] = hrv["hrv_status"]
        extra = daily_by_date.get(d)
        if extra:
            if extra.get("resting_hr") is not None:
                summary["resting_hr"] = extra["resting_hr"]
            if extra.get("body_battery_change") is not None:
                summary["body_battery_change"] = extra["body_battery_change"]
    return sleep_by_date
