"""
Per-game JSON enrichment: Phase 1 (MLB Stats API blocks A, B, C, F, I).

Endpoints used:
  A  /v1/people/{id}/stats?stats=statSplits&group=hitting|pitching&sitCodes=vr,vl
  B  /v1/people/{id}/stats?stats=byDateRange&group=hitting|pitching&startDate=...&endDate=...
  C  /v1/people/{id}/stats?stats=vsPlayerTotal|vsPlayer&group=hitting&opposingPlayerId={id}
  F  /v1/people/{id}/stats?stats=gameLog&group=pitching
     /v1/transactions?teamId={id}&startDate=...&endDate=...
  I  /v1/people/{id}/stats?stats=gameLog&group=pitching  (relievers)

All params confirmed against live responses 2026-06-16.
NOTE: MLB endpoint parameters drift; re-confirm if results go empty unexpectedly.
"""

import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

MLB_API_BASE = "https://statsapi.mlb.com/api"
CACHE_DIR = Path(".cache")
CACHE_TTL_SECONDS = 3600  # 1 hour
RATE_LIMIT_DELAY = 0.3    # seconds between cache-miss requests; be polite to public API

SLASH_FIELDS = [
    "avg", "obp", "slg", "ops",
    "plateAppearances", "atBats", "hits", "homeRuns",
    "strikeOuts", "baseOnBalls",
]
PITCHING_FIELDS = [
    "era", "whip", "avg", "obp", "slg", "ops",
    "inningsPitched", "battersFaced",
    "strikeOuts", "baseOnBalls", "hits", "homeRuns", "earnedRuns",
]


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------

def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def make_envelope(value, source, status, note=None):
    env = {"value": value, "source": source, "as_of": _now_iso(), "status": status}
    if note:
        env["note"] = note
    return env


def unavailable(source, note):
    return make_envelope(None, source, "UNAVAILABLE", note)


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------

def _cache_path(url, params):
    key = url + json.dumps(params, sort_keys=True)
    h = hashlib.md5(key.encode()).hexdigest()
    return CACHE_DIR / f"{h}.json"


def _cached_get(url, params=None):
    """GET with disk cache (TTL 1 hour) and polite rate limiting on misses."""
    if params is None:
        params = {}
    CACHE_DIR.mkdir(exist_ok=True)
    path = _cache_path(url, params)

    if path.exists() and (time.time() - path.stat().st_mtime) < CACHE_TTL_SECONDS:
        with open(path) as f:
            return json.load(f)

    time.sleep(RATE_LIMIT_DELAY)
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    with open(path, "w") as f:
        json.dump(data, f)

    return data


# ---------------------------------------------------------------------------
# Summary counter
# ---------------------------------------------------------------------------

class StatusCounter:
    def __init__(self):
        self._rows = []

    def record(self, block, status, note=""):
        self._rows.append((block, status, note))

    def print_summary(self):
        verified = sum(1 for _, s, _ in self._rows if s == "VERIFIED")
        partial  = sum(1 for _, s, _ in self._rows if s == "PARTIAL")
        unavail  = sum(1 for _, s, _ in self._rows if s == "UNAVAILABLE")
        total    = len(self._rows)

        print("\n--- Enrichment Summary ---")
        print(f"  VERIFIED: {verified}  PARTIAL: {partial}  UNAVAILABLE: {unavail}  (total: {total})")

        by_block = {}
        for block, status, note in self._rows:
            prefix = block.split()[0]
            by_block.setdefault(prefix, {"VERIFIED": 0, "PARTIAL": 0, "UNAVAILABLE": 0})
            by_block[prefix][status] += 1

        print(f"  {'Block':<6} {'VERIFIED':>8} {'PARTIAL':>8} {'UNAVAIL':>8}")
        for prefix in sorted(by_block):
            c = by_block[prefix]
            print(f"  {prefix:<6} {c['VERIFIED']:>8} {c['PARTIAL']:>8} {c['UNAVAILABLE']:>8}")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _safe_get(d, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return cur if cur is not None else default


def _pick_fields(stat, fields):
    return {k: stat.get(k) for k in fields}


def _first_splits(data):
    stats_list = data.get("stats", [])
    if not stats_list:
        return []
    return stats_list[0].get("splits", [])


# ---------------------------------------------------------------------------
# Block A: Platoon splits
# MLB Stats API statSplits — sitCodes vr=vs Right, vl=vs Left
# ---------------------------------------------------------------------------

def get_platoon_splits(player_id, group, season):
    source = f"MLB Stats API statSplits group={group} player={player_id}"
    url = f"{MLB_API_BASE}/v1/people/{player_id}/stats"
    params = {
        "stats": "statSplits",
        "group": group,
        "sitCodes": "vr,vl",
        "season": season,
        "sportId": 1,
    }
    try:
        data = _cached_get(url, params)
        splits = _first_splits(data)
        if not splits:
            return unavailable(source, "no splits returned")

        fields = SLASH_FIELDS if group == "hitting" else PITCHING_FIELDS
        result = {}
        for s in splits:
            code = _safe_get(s, "split", "code")
            if code not in ("vr", "vl"):
                continue
            pa = s["stat"].get("plateAppearances") or s["stat"].get("battersFaced", 0)
            result[code] = _pick_fields(s["stat"], fields) if pa else None

        if not result:
            return unavailable(source, "no vr/vl entries in response")

        status = "VERIFIED" if result.get("vr") and result.get("vl") else "PARTIAL"
        return make_envelope(result, source, status)

    except Exception as e:
        logger.warning("Block A player %s: %s", player_id, e)
        return unavailable(source, f"error: {e}")


# ---------------------------------------------------------------------------
# Block B: Recent form — last 15 and last 30 days
# MLB Stats API byDateRange; date format YYYY-MM-DD required
# API may return two identical rows; we take splits[0]
# ---------------------------------------------------------------------------

def get_recent_form(player_id, group, official_date, season):
    source = f"MLB Stats API byDateRange group={group} player={player_id}"
    fields = SLASH_FIELDS if group == "hitting" else PITCHING_FIELDS
    try:
        end = datetime.strptime(official_date, "%Y-%m-%d")
        windows = {}
        for label, days in [("last15", 15), ("last30", 30)]:
            start = end - timedelta(days=days)
            url = f"{MLB_API_BASE}/v1/people/{player_id}/stats"
            params = {
                "stats": "byDateRange",
                "group": group,
                "startDate": start.strftime("%Y-%m-%d"),
                "endDate": official_date,
                "season": season,
                "sportId": 1,
            }
            try:
                data = _cached_get(url, params)
                splits = _first_splits(data)
                if not splits:
                    windows[label] = None
                    continue
                stat = splits[0]["stat"]
                pa = stat.get("plateAppearances") or stat.get("battersFaced") or stat.get("gamesPlayed", 0)
                windows[label] = _pick_fields(stat, fields) if pa else None
            except Exception as e:
                logger.warning("Block B player %s %s: %s", player_id, label, e)
                windows[label] = None

        has_data = any(windows.values())
        if not has_data:
            return unavailable(source, "no activity in either window")
        status = "VERIFIED" if all(windows.values()) else "PARTIAL"
        return make_envelope(windows, source, status)

    except Exception as e:
        logger.warning("Block B player %s: %s", player_id, e)
        return unavailable(source, f"error: {e}")


# ---------------------------------------------------------------------------
# Block C: Batter-vs-pitcher history
# vsPlayerTotal = career, vsPlayer + season = current year
# PARTIAL when career PA < 20
# ---------------------------------------------------------------------------

def get_vs_pitcher(batter_id, pitcher_id, season):
    source = f"MLB Stats API vsPlayer batter={batter_id} pitcher={pitcher_id}"
    url = f"{MLB_API_BASE}/v1/people/{batter_id}/stats"
    results = {}

    for stat_type, label in [("vsPlayerTotal", "career"), ("vsPlayer", "season")]:
        params = {
            "stats": stat_type,
            "group": "hitting",
            "opposingPlayerId": pitcher_id,
            "sportId": 1,
        }
        if stat_type == "vsPlayer":
            params["season"] = season
        try:
            data = _cached_get(url, params)
            splits = _first_splits(data)
            if not splits:
                results[label] = None
                continue
            stat = splits[0]["stat"]
            pa = stat.get("plateAppearances") or stat.get("atBats", 0)
            results[label] = _pick_fields(stat, SLASH_FIELDS) if pa else None
        except Exception as e:
            logger.warning("Block C %s vs %s (%s): %s", batter_id, pitcher_id, label, e)
            results[label] = None

    career = results.get("career")
    if not career:
        return unavailable(source, "zero career PA")

    career_pa = career.get("plateAppearances") or 0
    status = "VERIFIED" if career_pa >= 20 else "PARTIAL"
    note = f"{career_pa} career PA — small sample" if career_pa < 20 else None
    return make_envelope(results, source, status, note=note)


# ---------------------------------------------------------------------------
# Block F: Pitcher workload — recent starts + IL context
# gameLog gives per-game lines; transactions gives IL moves
# ---------------------------------------------------------------------------

def get_pitcher_workload(pitcher_id, team_id, season, official_date):
    result = {}
    game_date = datetime.strptime(official_date, "%Y-%m-%d")

    # Recent starts / pitch counts
    source_gl = f"MLB Stats API gameLog group=pitching player={pitcher_id}"
    try:
        url = f"{MLB_API_BASE}/v1/people/{pitcher_id}/stats"
        params = {"stats": "gameLog", "group": "pitching", "season": season, "sportId": 1}
        data = _cached_get(url, params)
        splits = _first_splits(data)

        # Exclude any entry for today itself (may appear as scheduled stub)
        past = [s for s in splits if s.get("date") and s["date"] < official_date]
        recent = past[-5:]

        starts = []
        for i, s in enumerate(recent):
            st = s["stat"]
            rest = None
            if i > 0:
                prev = datetime.strptime(recent[i - 1]["date"], "%Y-%m-%d")
                this = datetime.strptime(s["date"], "%Y-%m-%d")
                rest = (this - prev).days - 1
            starts.append({
                "date": s["date"],
                "innings_pitched": st.get("inningsPitched"),
                "pitches": st.get("numberOfPitches"),
                "earned_runs": st.get("earnedRuns"),
                "strikeouts": st.get("strikeOuts"),
                "rest_days_after_prev": rest,
            })

        days_rest = None
        if recent:
            last = datetime.strptime(recent[-1]["date"], "%Y-%m-%d")
            days_rest = (game_date - last).days - 1

        result["recent_starts"] = make_envelope(
            {"starts": starts, "days_rest_before_game": days_rest},
            source_gl, "VERIFIED" if starts else "UNAVAILABLE",
            note=None if starts else "no past starts found",
        )
    except Exception as e:
        logger.warning("Block F gameLog pitcher %s: %s", pitcher_id, e)
        result["recent_starts"] = unavailable(source_gl, f"error: {e}")

    # IL context
    source_txn = f"MLB Stats API transactions teamId={team_id}"
    try:
        url = f"{MLB_API_BASE}/v1/transactions"
        params = {
            "teamId": team_id,
            "startDate": f"{season}-04-01",
            "endDate": official_date,
        }
        data = _cached_get(url, params)
        txns = data.get("transactions", [])

        il_txns = [
            t for t in txns
            if t.get("person", {}).get("id") == pitcher_id
            and (
                "injured" in (t.get("typeDesc") or "").lower()
                or "il" in (t.get("typeCode") or "").lower()
            )
        ]

        if il_txns:
            latest = sorted(il_txns, key=lambda t: t.get("date", ""))[-1]
            result["il_context"] = make_envelope(
                {
                    "type": latest.get("typeDesc"),
                    "date": latest.get("date"),
                    "description": latest.get("description"),
                },
                source_txn, "VERIFIED",
            )
        else:
            result["il_context"] = make_envelope(
                None, source_txn, "VERIFIED", note="no IL transactions this season"
            )
    except Exception as e:
        logger.warning("Block F transactions pitcher %s: %s", pitcher_id, e)
        result["il_context"] = unavailable(source_txn, f"error: {e}")

    return result


# ---------------------------------------------------------------------------
# Block I: Bullpen recent usage and rest
# gameLog per reliever; availability label is DERIVED, not an official stat
# ---------------------------------------------------------------------------

def get_bullpen_usage(pitcher_ids, season, official_date):
    source_tmpl = "MLB Stats API gameLog group=pitching player={id}"
    result = {}

    try:
        game_date = datetime.strptime(official_date, "%Y-%m-%d")
    except ValueError:
        return {}

    for pitcher_id in pitcher_ids:
        source = source_tmpl.format(id=pitcher_id)
        try:
            url = f"{MLB_API_BASE}/v1/people/{pitcher_id}/stats"
            params = {"stats": "gameLog", "group": "pitching", "season": season, "sportId": 1}
            data = _cached_get(url, params)
            splits = _first_splits(data)
            past = [s for s in splits if s.get("date") and s["date"] < official_date]

            if not past:
                result[pitcher_id] = make_envelope(
                    {"appearances_this_season": 0, "last_appearance_date": None,
                     "pitches_last_1d": 0, "pitches_last_2d": 0, "pitches_last_3d": 0,
                     "back_to_back": False, "availability_note": "derived — no appearances yet"},
                    source, "VERIFIED",
                )
                continue

            dates_pitched = {datetime.strptime(s["date"], "%Y-%m-%d") for s in past}
            pitch_by_offset = {1: 0, 2: 0, 3: 0}
            for s in past:
                d = datetime.strptime(s["date"], "%Y-%m-%d")
                ago = (game_date - d).days
                pitches = s["stat"].get("numberOfPitches") or 0
                for offset in (1, 2, 3):
                    if ago <= offset:
                        pitch_by_offset[offset] += pitches

            last = past[-1]
            last_date = datetime.strptime(last["date"], "%Y-%m-%d")
            days_since = (game_date - last_date).days
            back_to_back = (
                (game_date - timedelta(1)) in dates_pitched
                and (game_date - timedelta(2)) in dates_pitched
            )

            p3 = pitch_by_offset[3]
            if back_to_back:
                avail = "heavy recent usage"
            elif days_since == 1 or p3 >= 30:
                avail = "monitor"
            else:
                avail = "likely available"

            result[pitcher_id] = make_envelope(
                {
                    "appearances_this_season": len(past),
                    "last_appearance_date": last["date"],
                    "last_appearance_pitches": last["stat"].get("numberOfPitches"),
                    "days_since_last_appearance": days_since,
                    "pitches_last_1d": pitch_by_offset[1],
                    "pitches_last_2d": pitch_by_offset[2],
                    "pitches_last_3d": pitch_by_offset[3],
                    "back_to_back": back_to_back,
                    "availability_note": f"derived — {avail}",
                },
                source, "VERIFIED",
            )
        except Exception as e:
            logger.warning("Block I reliever %s: %s", pitcher_id, e)
            result[pitcher_id] = unavailable(source, f"error: {e}")

    return result


# ---------------------------------------------------------------------------
# Master runner
# ---------------------------------------------------------------------------

def run_enrichment(feed):
    """
    Runs all Phase 1 enrichment blocks. Merges results into:
      feed["gameData"]["players"][IDxxxx]["enrichment"]  (per-player)
      feed["enrichment"]                                  (game-level)
    Returns a StatusCounter for summary printing.
    """
    counter = StatusCounter()

    season       = _safe_get(feed, "gameData", "game", "season")
    official_date = _safe_get(feed, "gameData", "datetime", "officialDate")
    game_status  = _safe_get(feed, "gameData", "status", "abstractGameState", default="Unknown")
    prob         = _safe_get(feed, "gameData", "probablePitchers", default={})
    home_pid     = _safe_get(prob, "home", "id")
    away_pid     = _safe_get(prob, "away", "id")
    home_team_id = _safe_get(feed, "gameData", "teams", "home", "id")
    away_team_id = _safe_get(feed, "gameData", "teams", "away", "id")

    bx = _safe_get(feed, "liveData", "boxscore", "teams", default={})
    home_order   = bx.get("home", {}).get("battingOrder", [])
    away_order   = bx.get("away", {}).get("battingOrder", [])
    home_bullpen = bx.get("home", {}).get("bullpen", [])
    away_bullpen = bx.get("away", {}).get("bullpen", [])

    players = _safe_get(feed, "gameData", "players", default={})
    all_batter_ids  = set(home_order + away_order)
    all_pitcher_ids = {p for p in [home_pid, away_pid] if p}

    # Initialise enrichment dict on each player we'll touch
    for pid in all_batter_ids | all_pitcher_ids:
        players.get(f"ID{pid}", {}).setdefault("enrichment", {})

    # ---- A: Platoon splits ----
    print("  Block A: platoon splits...", end=" ", flush=True)
    a_ok = 0
    for pid in all_batter_ids:
        env = get_platoon_splits(pid, "hitting", season)
        _write_player(players, pid, "splits_vs_hand", env)
        counter.record("A", env["status"])
        a_ok += env["status"] != "UNAVAILABLE"
    for pid in all_pitcher_ids:
        env = get_platoon_splits(pid, "pitching", season)
        _write_player(players, pid, "splits_vs_hand", env)
        counter.record("A", env["status"])
        a_ok += env["status"] != "UNAVAILABLE"
    print(f"{a_ok}/{len(all_batter_ids) + len(all_pitcher_ids)} enriched")

    # ---- B: Recent form ----
    print("  Block B: recent form...", end=" ", flush=True)
    b_ok = 0
    for pid in all_batter_ids:
        env = get_recent_form(pid, "hitting", official_date, season)
        _write_player(players, pid, "recent_form", env)
        counter.record("B", env["status"])
        b_ok += env["status"] != "UNAVAILABLE"
    for pid in all_pitcher_ids:
        env = get_recent_form(pid, "pitching", official_date, season)
        _write_player(players, pid, "recent_form", env)
        counter.record("B", env["status"])
        b_ok += env["status"] != "UNAVAILABLE"
    print(f"{b_ok}/{len(all_batter_ids) + len(all_pitcher_ids)} enriched")

    # ---- C: Batter-vs-pitcher ----
    print("  Block C: batter vs pitcher...", end=" ", flush=True)
    c_ok = 0
    for batter_id, opp_pid in (
        [(bid, away_pid) for bid in home_order] +
        [(bid, home_pid) for bid in away_order]
    ):
        if not opp_pid:
            continue
        env = get_vs_pitcher(batter_id, opp_pid, season)
        _write_player(players, batter_id, "vs_opposing_pitcher", env)
        counter.record("C", env["status"])
        c_ok += env["status"] != "UNAVAILABLE"
    print(f"{c_ok}/{len(home_order) + len(away_order)} with history")

    # ---- F: Pitcher workload ----
    print("  Block F: pitcher workload...", end=" ", flush=True)
    for pid, team_id in [(home_pid, home_team_id), (away_pid, away_team_id)]:
        if not pid:
            continue
        workload = get_pitcher_workload(pid, team_id, season, official_date)
        key = f"ID{pid}"
        if key in players:
            players[key].setdefault("enrichment", {}).update({"pitcher_workload": workload})
        for sub_key, env in workload.items():
            if isinstance(env, dict) and "status" in env:
                counter.record("F", env["status"])
    print("done")

    # ---- I: Bullpen usage ----
    print("  Block I: bullpen usage...", end=" ", flush=True)
    for bullpen_ids in (home_bullpen, away_bullpen):
        usage = get_bullpen_usage(bullpen_ids, season, official_date)
        for pid, env in usage.items():
            _write_player(players, pid, "bullpen_usage", env)
            counter.record("I", env["status"])
    print("done")

    # ---- Game-level enrichment dict ----
    feed.setdefault("enrichment", {})
    feed["enrichment"]["generated_at"] = _now_iso()
    feed["enrichment"]["game_status_at_pull"] = game_status

    # ---- L: Umpire tendencies (stub) ----
    feed["enrichment"]["umpire_tendencies"] = unavailable(
        "", "no accessible umpire-scorecards dataset configured"
    )
    counter.record("L", "UNAVAILABLE")

    # ---- M: Times-through-order splits (stub) ----
    feed["enrichment"]["times_through_order_splits"] = unavailable(
        "", "no clean endpoint; per-PA derivation deferred to Phase 2+"
    )
    counter.record("M", "UNAVAILABLE")

    return counter


def _write_player(players, player_id, field, value):
    key = f"ID{player_id}"
    if key in players:
        players[key].setdefault("enrichment", {})[field] = value
