"""
generate.py — Fetch Blue Jays + opposing pitcher data and render index.html

Data sources:
  - MLB Stats API   : today's game, probable pitchers, last-3-start game logs
  - Open-Meteo API  : venue weather (free, no key)
  - pybaseball      : Statcast pitch data for per-game Whiff%, Chase%, BABIP,
                      WHIP (approximate), and RHB/LHB event splits

Logo source:
  - https://www.mlbstatic.com/team-logos/{teamId}.svg
    Not an officially documented public API — relies on MLB's CDN. Works for
    local/fan use but may break if MLB changes their CDN structure.
"""

from __future__ import annotations

from html import escape
from functools import lru_cache
import sys
import subprocess
from datetime import date, datetime, timedelta
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

try:
    import pybaseball as pyb

    pyb.cache.enable()
    HAS_PYBASEBALL = True
except Exception:
    HAS_PYBASEBALL = False

# ── constants ─────────────────────────────────────────────────────────────────

TEAM_ID = 141  # Toronto Blue Jays
DEFAULT_REPORT_DATE = date.today().isoformat()
TEMPLATE = Path(__file__).parent / "template.html"
OUTPUT = Path(__file__).parent / "index.html"
# Date picker / local server paused for now.
# STYLESHEET = Path(__file__).parent / "styles.css"
# HOST = "127.0.0.1"
# PORT = 8765
DASH = "—"

_SWING_DESC = frozenset({
    "swinging_strike", "swinging_strike_blocked", "foul", "foul_tip",
    "hit_into_play", "hit_into_play_no_out", "hit_into_play_score",
    "foul_bunt", "missed_bunt",
})
_WHIFF_DESC = frozenset({"swinging_strike", "swinging_strike_blocked"})
_OUT_ZONES = frozenset({11, 12, 13, 14})

_HIT_EVENTS  = frozenset({"single", "double", "triple", "home_run"})
_WALK_EVENTS = frozenset({"walk", "hit_by_pitch"})
_K_EVENTS    = frozenset({"strikeout", "strikeout_double_play"})

_BIP_EVENTS = frozenset({
    "single", "double", "triple",
    "field_out", "force_out", "grounded_into_double_play", "double_play",
    "fielders_choice", "fielders_choice_out",
    "sac_fly", "sac_fly_error", "sac_bunt", "field_error",
})

# Outs recorded on the play — GIDP = 2, triple play = 3, else 1
_OUT_EVENTS_1 = frozenset({
    "strikeout", "field_out", "force_out",
    "fielders_choice_out", "sac_fly", "sac_bunt", "sac_fly_error",
})
_OUT_EVENTS_2 = frozenset({
    "strikeout_double_play", "grounded_into_double_play", "double_play",
})
_OUT_EVENTS_3 = frozenset({"triple_play"})


def _count_outs(events: pd.Series) -> int:
    return int(
        events.isin(_OUT_EVENTS_1).sum() * 1
        + events.isin(_OUT_EVENTS_2).sum() * 2
        + events.isin(_OUT_EVENTS_3).sum() * 3
    )


# ── MLB Stats API ─────────────────────────────────────────────────────────────

def _mlb(path: str, **params):
    r = requests.get(f"https://statsapi.mlb.com/api/v1{path}", params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def get_game_for_date(report_date: str) -> dict | None:
    data = _mlb("/schedule",
                sportId=1, teamId=TEAM_ID,
                startDate=report_date, endDate=report_date,
                gameType="R",
                hydrate="probablePitcher,venue,team")
    for blk in data.get("dates", []):
        for game in blk["games"]:
            return game
    return None


def get_player_info(person_id: int) -> dict:
    return _mlb(f"/people/{person_id}")["people"][0]


def get_venue_coords(venue_id: int) -> tuple[float, float] | None:
    try:
        coords = get_venue_info(venue_id).get("location", {}).get("defaultCoordinates", {})
        lat, lon = coords.get("latitude"), coords.get("longitude")
        if lat and lon:
            return float(lat), float(lon)
    except Exception:
        pass
    return None


@lru_cache(maxsize=256)
def get_venue_info(venue_id: int) -> dict:
    try:
        return _mlb(f"/venues/{venue_id}", hydrate="location,timezone")["venues"][0]
    except Exception:
        return {}


def get_last_n_starts(pitcher_id: int, report_date: str, n: int = 3) -> list[dict]:
    season = date.fromisoformat(report_date).year
    data = _mlb(f"/people/{pitcher_id}/stats",
                stats="gameLog", group="pitching",
                season=season, gameType="R")
    splits = data.get("stats", [{}])[0].get("splits", [])
    starts = [
        s for s in splits
        if s.get("stat", {}).get("gamesStarted", 0) == 1
        and s.get("date", "") <= report_date
    ]
    return starts[-n:]


def get_boxscore_starter(game_pk: int, side: str) -> dict:
    try:
        box = _mlb(f"/game/{game_pk}/boxscore")
        players = box.get("teams", {}).get(side, {}).get("players", {})
        for player in players.values():
            pitching = player.get("stats", {}).get("pitching", {})
            if pitching.get("gamesStarted") == 1:
                person = player.get("person", {})
                return {"id": person.get("id"), "fullName": person.get("fullName", "TBD")}
    except Exception:
        pass
    return {}


def resolve_pitcher(probable: dict) -> tuple[int | None, str, str]:
    """Return (id, fullName, pitch_hand)."""
    pitcher_id   = probable.get("id")
    pitcher_name = probable.get("fullName", "TBD")
    pitch_hand   = DASH

    if pitcher_id:
        try:
            info = get_player_info(pitcher_id)
            hand = info.get("pitchHand", {}).get("description", "")
            pitch_hand = "RHP" if "right" in hand.lower() else "LHP" if "left" in hand.lower() else hand
        except Exception:
            pass

    return pitcher_id, pitcher_name, pitch_hand


def pitch_hand_icon(pitch_hand: str) -> str:
    if pitch_hand == "RHP":
        return "✋"
    if pitch_hand == "LHP":
        return "🤚"
    return ""


def format_display_date(value: str) -> str:
    try:
        return date.fromisoformat(value).strftime("%d/%m/%Y")
    except Exception:
        return value


# ── Weather ───────────────────────────────────────────────────────────────────

def get_weather(lat: float, lon: float, game_utc_str: str) -> str:
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon,
                "hourly": "temperature_2m,precipitation_probability,windspeed_10m",
                "temperature_unit": "celsius",
                "wind_speed_unit": "kmh",
                "timezone": "auto",
                "forecast_days": 2,
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()

        game_dt = datetime.fromisoformat(game_utc_str.replace("Z", "+00:00"))
        tz_str  = data.get("timezone", "UTC")
        try:
            local_dt = game_dt.astimezone(ZoneInfo(tz_str))
        except Exception:
            local_dt = game_dt

        target = local_dt.strftime("%Y-%m-%dT%H:00")
        times  = data["hourly"]["time"]
        idx    = times.index(target) if target in times else 0

        temp   = data["hourly"]["temperature_2m"][idx]
        wind   = data["hourly"]["windspeed_10m"][idx]
        precip = data["hourly"]["precipitation_probability"][idx]
        return f"{temp:.0f}°C, {wind:.0f} kph wind, {precip}% precip"
    except Exception:
        return DASH


# ── Statcast splits ───────────────────────────────────────────────────────────

def _safe_pct(num: int, den: int) -> str:
    return f"{100 * num / den:.1f}%" if den else DASH


def _split_counts(df: pd.DataFrame) -> dict:
    events = df["events"].fillna("")
    desc   = df["description"].fillna("")
    zone   = df["zone"].fillna(0).astype(int)

    bf = df["at_bat_number"].nunique()
    h  = int(events.isin(_HIT_EVENTS).sum())
    hr = int((events == "home_run").sum())
    bb = int(events.isin(_WALK_EVENTS).sum())
    k  = int(events.isin(_K_EVENTS).sum())

    h_in_play = int(events.isin({"single", "double", "triple"}).sum())
    bip       = int(events.isin(_BIP_EVENTS).sum())
    babip     = f"{h_in_play / bip:.3f}" if bip else DASH

    ip_approx = _count_outs(events) / 3
    whip      = f"{(h + bb) / ip_approx:.2f}" if ip_approx > 0 else DASH

    swings        = int(desc.isin(_SWING_DESC).sum())
    whiffs        = int(desc.isin(_WHIFF_DESC).sum())
    outside       = zone.isin(_OUT_ZONES)
    chase_swings  = int((outside & desc.isin(_SWING_DESC)).sum())
    outside_total = int(outside.sum())

    return {
        "H": h, "BB": bb, "K": k, "HR": hr, "BF": int(bf),
        "babip":     babip,
        "whip":      whip,
        "whiff_pct": _safe_pct(whiffs, swings),
        "chase_pct": _safe_pct(chase_swings, outside_total),
        "k_pct":     _safe_pct(k, int(bf)),
        "bb_pct":    _safe_pct(bb, int(bf)),
    }


def get_statcast_splits(mlbam_id: int, game_date: str) -> dict | None:
    if not HAS_PYBASEBALL:
        return None
    try:
        df = pyb.statcast_pitcher(game_date, game_date, player_id=mlbam_id)
        if df is None or df.empty:
            return None
        return {
            "overall": _split_counts(df),
            "rhb":     _split_counts(df[df["stand"] == "R"]),
            "lhb":     _split_counts(df[df["stand"] == "L"]),
        }
    except Exception:
        return None


# ── IP parsing ────────────────────────────────────────────────────────────────

def parse_ip(ip_str) -> float:
    """'5.1' → 5.333,  '5.2' → 5.667  (MLB uses thirds, not tenths)"""
    try:
        parts = str(ip_str).split(".")
        return int(parts[0]) + (int(parts[1]) / 3 if len(parts) > 1 else 0)
    except Exception:
        return 0.0


# ── Row builders ──────────────────────────────────────────────────────────────

def build_row(split: dict, n: int, sc: dict | None,
              prev_date: str | None, key_prefix: str = "") -> dict:
    """Build template context for one game row.

    key_prefix="" for Blue Jays pitcher, "OP_" for opposing pitcher.
    R and ER on split rows are omitted here — template hardcodes N/A for them.
    """
    stat      = split["stat"]
    game_date = split.get("date", DASH)
    pitcher   = split.get("player", {}).get("fullName", DASH)
    opponent  = split.get("opponent", {}).get("name", DASH)

    ip_str = stat.get("inningsPitched", DASH)
    ip_val = parse_ip(ip_str)
    h  = int(stat.get("hits", 0))
    bb = int(stat.get("baseOnBalls", 0))
    k  = int(stat.get("strikeOuts", 0))
    er = int(stat.get("earnedRuns", 0))
    bf = int(stat.get("battersFaced", 0))

    era_game  = f"{er * 9 / ip_val:.2f}" if ip_val else DASH
    whip_game = f"{(h + bb) / ip_val:.2f}" if ip_val else DASH
    k_pct     = _safe_pct(k, bf)
    bb_pct    = _safe_pct(bb, bf)

    days_off = DASH
    if prev_date:
        try:
            days_off = str((date.fromisoformat(game_date) - date.fromisoformat(prev_date)).days)
        except Exception:
            pass

    whiff_pct = chase_pct = DASH
    babip = DASH
    if sc:
        ov        = sc["overall"]
        whiff_pct = ov["whiff_pct"]
        chase_pct = ov["chase_pct"]
        babip     = ov["babip"]
        if bf == 0:
            k_pct  = ov["k_pct"]
            bb_pct = ov["bb_pct"]

    def split_cells(side_key: str, side_prefix: str) -> dict:
        full = f"{key_prefix}{side_prefix}"
        if not sc:
            return {f"{full}_{k}_{n}": DASH for k in
                    ["H", "BB", "K", "HR", "WHIP",
                     "K_PCT", "BB_PCT", "BABIP", "WHIFF_PCT", "CHASE_PCT"]}
        s = sc[side_key]
        return {
            f"{full}_H_{n}":         s["H"],
            f"{full}_BB_{n}":        s["BB"],
            f"{full}_K_{n}":         s["K"],
            f"{full}_HR_{n}":        s["HR"],
            f"{full}_WHIP_{n}":      s["whip"],
            f"{full}_K_PCT_{n}":     s["k_pct"],
            f"{full}_BB_PCT_{n}":    s["bb_pct"],
            f"{full}_BABIP_{n}":     s["babip"],
            f"{full}_WHIFF_PCT_{n}": s["whiff_pct"],
            f"{full}_CHASE_PCT_{n}": s["chase_pct"],
        }

    p = key_prefix
    row = {
        f"{p}DATE_{n}":      format_display_date(game_date),
        f"{p}NAME_{n}":      pitcher,
        f"{p}OPP_{n}":       opponent,
        f"{p}IP_{n}":        ip_str,
        f"{p}DAYS_OFF_{n}":  days_off,
        f"{p}H_{n}":         h,
        f"{p}R_{n}":         stat.get("runs", DASH),
        f"{p}ER_{n}":        er,
        f"{p}BB_{n}":        bb,
        f"{p}K_{n}":         k,
        f"{p}HR_{n}":        stat.get("homeRuns", DASH),
        f"{p}ERA_{n}":       era_game,
        f"{p}WHIP_{n}":      whip_game,
        f"{p}K_PCT_{n}":     k_pct,
        f"{p}BB_PCT_{n}":    bb_pct,
        f"{p}BABIP_{n}":     babip,
        f"{p}WHIFF_PCT_{n}": whiff_pct,
        f"{p}CHASE_PCT_{n}": chase_pct,
    }
    row.update(split_cells("rhb", "RHB"))
    row.update(split_cells("lhb", "LHB"))
    return row


def empty_row(n: int, key_prefix: str = "") -> dict:
    p = key_prefix
    keys = ["DATE", "NAME", "OPP", "IP", "DAYS_OFF",
            "H", "R", "ER", "BB", "K", "HR", "ERA", "WHIP",
            "K_PCT", "BB_PCT", "BABIP", "WHIFF_PCT", "CHASE_PCT"]
    split_keys = ["H", "BB", "K", "HR", "WHIP",
                  "K_PCT", "BB_PCT", "BABIP", "WHIFF_PCT", "CHASE_PCT"]
    row = {f"{p}{k}_{n}": DASH for k in keys}
    for side in ("RHB", "LHB"):
        row.update({f"{p}{side}_{k}_{n}": DASH for k in split_keys})
    return row


# ── Hitter builders ───────────────────────────────────────────────────────────

def html(value) -> str:
    return escape(str(value if value is not None else DASH))


def _int_stat(stats: dict, key: str) -> int:
    try:
        return int(stats.get(key, 0))
    except Exception:
        return 0


def _rate(num: int | float, den: int | float, places: int = 3) -> str:
    return f"{num / den:.{places}f}" if den else DASH


def _pct(num: int | float, den: int | float) -> str:
    return f"{100 * num / den:.1f}%" if den else DASH


def _bat_side(person_id: int | None) -> str:
    if not person_id:
        return DASH
    try:
        side = get_player_info(person_id).get("batSide", {}).get("description", "")
    except Exception:
        return DASH
    lower = side.lower()
    if "switch" in lower:
        return "S"
    if "right" in lower:
        return "R"
    if "left" in lower:
        return "L"
    return side or DASH


def _total_bases(stats: dict) -> int:
    hits = _int_stat(stats, "hits")
    doubles = _int_stat(stats, "doubles")
    triples = _int_stat(stats, "triples")
    homers = _int_stat(stats, "homeRuns")
    singles = hits - doubles - triples - homers
    return singles + (2 * doubles) + (3 * triples) + (4 * homers)


def _boxscore_player_row(
    player: dict,
    report_date: str,
    opponent: str,
    pitcher_hand: str,
    lineup_spot: int,
) -> dict:
    stats = player.get("stats", {}).get("batting", {})
    person = player.get("person", {})
    person_id = person.get("id")

    ab = _int_stat(stats, "atBats")
    pa = _int_stat(stats, "plateAppearances")
    hits = _int_stat(stats, "hits")
    walks = _int_stat(stats, "baseOnBalls")
    hbp = _int_stat(stats, "hitByPitch")
    sac_flies = _int_stat(stats, "sacFlies")
    strikeouts = _int_stat(stats, "strikeOuts")
    obp_den = ab + walks + hbp + sac_flies

    return {
        "date": format_display_date(report_date),
        "player": person.get("fullName", "TBD"),
        "opp": opponent,
        "bat_side": _bat_side(person_id),
        "pitcher_hand": pitcher_hand,
        "lineup_spot": lineup_spot,
        "pa": pa,
        "h": hits,
        "hr": _int_stat(stats, "homeRuns"),
        "bb": walks,
        "k": strikeouts,
        "avg": _rate(hits, ab),
        "obp": _rate(hits + walks + hbp, obp_den),
        "slg": _rate(_total_bases(stats), ab),
        "k_pct": _pct(strikeouts, pa),
        "bb_pct": _pct(walks, pa),
        "whiff_pct": DASH,
        "chase_pct": DASH,
        "hardhit_pct": DASH,
        "barrel_pct": DASH,
        "babip": DASH,
    }


def _blank_hitter_row(lineup_spot: int, report_date: str, opponent: str, pitcher_hand: str) -> dict:
    return {
        "date": format_display_date(report_date),
        "player": "TBD",
        "opp": opponent,
        "bat_side": DASH,
        "pitcher_hand": pitcher_hand,
        "lineup_spot": lineup_spot,
        "pa": DASH,
        "h": DASH,
        "hr": DASH,
        "bb": DASH,
        "k": DASH,
        "avg": DASH,
        "obp": DASH,
        "slg": DASH,
        "k_pct": DASH,
        "bb_pct": DASH,
        "whiff_pct": DASH,
        "chase_pct": DASH,
        "hardhit_pct": DASH,
        "barrel_pct": DASH,
        "babip": DASH,
    }


def _sum_hitter_rows(rows: list[dict], fallback: dict) -> dict:
    if not rows:
        combined = fallback.copy()
        combined.update({
            "pa": DASH, "h": DASH, "hr": DASH, "bb": DASH, "k": DASH,
            "avg": DASH, "obp": DASH, "slg": DASH, "k_pct": DASH, "bb_pct": DASH,
        })
        return combined

    pa = sum(int(r["pa"]) for r in rows if isinstance(r["pa"], int))
    hits = sum(int(r["h"]) for r in rows if isinstance(r["h"], int))
    homers = sum(int(r["hr"]) for r in rows if isinstance(r["hr"], int))
    walks = sum(int(r["bb"]) for r in rows if isinstance(r["bb"], int))
    strikeouts = sum(int(r["k"]) for r in rows if isinstance(r["k"], int))

    # Boxscore rows do not carry AB/TB after formatting, so combined slash lines
    # intentionally wait for the real hitter aggregation pass.
    combined = fallback.copy()
    combined.update({
        "pa": pa,
        "h": hits,
        "hr": homers,
        "bb": walks,
        "k": strikeouts,
        "avg": DASH,
        "obp": DASH,
        "slg": DASH,
        "k_pct": _pct(strikeouts, pa),
        "bb_pct": _pct(walks, pa),
    })
    return combined


@lru_cache(maxsize=128)
def _get_boxscore(game_pk: int) -> dict | None:
    try:
        return _mlb(f"/game/{game_pk}/boxscore")
    except Exception:
        return None


def _lineup_players(boxscore: dict | None, side: str, limit: int = 5) -> list[dict]:
    if not boxscore:
        return []
    team_box = boxscore.get("teams", {}).get(side, {})
    players = team_box.get("players", {})
    lineup = team_box.get("battingOrder", [])[:limit]
    result = []
    for lineup_spot, player_id in enumerate(lineup, start=1):
        player = players.get(f"ID{player_id}")
        if player:
            result.append({"lineup_spot": lineup_spot, "player": player})
    return result


@lru_cache(maxsize=64)
def _completed_games_for_team(team_id: int, end_date: str, limit: int = 20) -> list[dict]:
    season_start = f"{date.fromisoformat(end_date).year}-03-01"
    data = _mlb(
        "/schedule",
        sportId=1,
        teamId=team_id,
        startDate=season_start,
        endDate=end_date,
        gameType="R",
    )
    games = []
    for block in data.get("dates", []):
        for game in block.get("games", []):
            game_date = game.get("gameDate", "")[:10]
            if game_date < end_date and game.get("status", {}).get("codedGameState") == "F":
                games.append(game)
    return games[-limit:]


def _venue_city(venue_id: int | None) -> str:
    if not venue_id:
        return DASH
    venue = get_venue_info(venue_id)
    loc = venue.get("location", {})
    city = loc.get("city")
    state = loc.get("stateAbbrev") or loc.get("state")
    country = loc.get("country")
    parts = [part for part in (city, state or country) if part]
    return ", ".join(parts) if parts else DASH


def _venue_timezone(venue_id: int | None) -> str | None:
    if not venue_id:
        return None
    venue = get_venue_info(venue_id)
    tz = venue.get("timeZone", {})
    return tz.get("id") or tz.get("tz")


def _tz_offset_hours(tz_name: str | None, when: datetime) -> float | None:
    if not tz_name:
        return None
    try:
        offset = when.astimezone(ZoneInfo(tz_name)).utcoffset()
    except Exception:
        return None
    if offset is None:
        return None
    return offset.total_seconds() / 3600


def _distance_km(a: tuple[float, float] | None, b: tuple[float, float] | None) -> float | None:
    if not a or not b:
        return None
    lat1, lon1 = a
    lat2, lon2 = b
    radius_km = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    root = (
        sin(dlat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    )
    return 2 * radius_km * asin(sqrt(root))


def _game_local_datetime(game: dict, venue_id: int | None = None) -> datetime | None:
    game_utc_str = game.get("gameDate")
    if not game_utc_str:
        return None
    try:
        game_dt = datetime.fromisoformat(game_utc_str.replace("Z", "+00:00"))
    except Exception:
        return None
    tz_name = _venue_timezone(venue_id or game.get("venue", {}).get("id"))
    if tz_name:
        try:
            return game_dt.astimezone(ZoneInfo(tz_name))
        except Exception:
            pass
    return game_dt


def _night_before_day(previous_game: dict | None, current_game: dict, current_venue_id: int | None) -> str:
    if not previous_game:
        return DASH
    prev_local = _game_local_datetime(previous_game)
    current_local = _game_local_datetime(current_game, current_venue_id)
    if not prev_local or not current_local:
        return "Unknown"
    is_back_to_back = (current_local.date() - prev_local.date()).days == 1
    was_night = prev_local.hour >= 18
    is_day = current_local.hour < 17
    return "Yes" if is_back_to_back and was_night and is_day else "No"


def _consecutive_game_days(games: list[dict], report_date: str) -> int:
    game_dates = {date.fromisoformat(g["gameDate"][:10]) for g in games if g.get("gameDate")}
    current = date.fromisoformat(report_date)
    count = 1
    check = current - timedelta(days=1)
    while check in game_dates:
        count += 1
        check -= timedelta(days=1)
    return count


def _fatigue_metrics(team_id: int, team_label: str, report_date: str, current_game: dict, current_venue_id: int | None) -> dict:
    recent_games = _completed_games_for_team(team_id, report_date, limit=20)
    previous_game = recent_games[-1] if recent_games else None
    report_day = date.fromisoformat(report_date)
    current_coords = get_venue_coords(current_venue_id) if current_venue_id else None

    if previous_game:
        prev_date = date.fromisoformat(previous_game["gameDate"][:10])
        previous_venue_id = previous_game.get("venue", {}).get("id")
        previous_coords = get_venue_coords(previous_venue_id) if previous_venue_id else None
        days_off = max((report_day - prev_date).days - 1, 0)
        distance = _distance_km(previous_coords, current_coords)
        previous_city = _venue_city(previous_venue_id)
    else:
        previous_venue_id = None
        days_off = DASH
        distance = None
        previous_city = DASH

    current_city = _venue_city(current_venue_id)
    three_day_start = report_day - timedelta(days=3)
    games_last_3 = sum(
        1
        for game in recent_games
        if three_day_start <= date.fromisoformat(game["gameDate"][:10]) < report_day
    )
    consecutive_days = _consecutive_game_days(recent_games, report_date)

    game_dt = datetime.fromisoformat(current_game.get("gameDate", "").replace("Z", "+00:00"))
    previous_offset = _tz_offset_hours(_venue_timezone(previous_venue_id), game_dt) if previous_game else None
    current_offset = _tz_offset_hours(_venue_timezone(current_venue_id), game_dt)
    if previous_offset is None or current_offset is None:
        tz_change = DASH
        jet_lag = "Unknown"
        tz_delta = 0
    else:
        tz_delta = current_offset - previous_offset
        sign = "+" if tz_delta > 0 else ""
        tz_change = f"{sign}{tz_delta:.0f}h"
        jet_lag = "East" if tz_delta > 0 else "West" if tz_delta < 0 else "None"

    night_day = _night_before_day(previous_game, current_game, current_venue_id)
    distance_label = f"{distance:.0f} km" if distance is not None else DASH

    risk_points = 0
    if days_off == 0:
        risk_points += 1
    if games_last_3 >= 3:
        risk_points += 1
    if distance and distance >= 800:
        risk_points += 1
    if abs(tz_delta) >= 1:
        risk_points += 1
    if night_day == "Yes":
        risk_points += 1
    arrival_risk = "High" if risk_points >= 3 else "Moderate" if risk_points >= 1 else "Low"

    notes = []
    if previous_game:
        notes.append(f"Prev: {previous_city}")
    if days_off == 0:
        notes.append("No off day")
    elif days_off != DASH:
        notes.append(f"{days_off} off day{'s' if days_off != 1 else ''}")
    if distance is not None:
        notes.append("Travel estimated from venue coordinates")
    if night_day == "Yes":
        notes.append("Night-before-day flag")
    if jet_lag in ("East", "West"):
        notes.append(f"{jet_lag}bound time shift")
    rest_notes = "; ".join(notes) if notes else "No previous completed game found"

    return {
        "team": team_label,
        "date": format_display_date(report_date),
        "venue": current_game.get("venue", {}).get("name", DASH),
        "city": current_city,
        "previous_city": previous_city,
        "days_off": days_off,
        "games_last_3": games_last_3,
        "consecutive_days": consecutive_days,
        "tz_change": tz_change,
        "travel_distance": distance_label,
        "night_before_day": night_day,
        "jet_lag": jet_lag,
        "arrival_risk": arrival_risk,
        "rest_notes": rest_notes,
    }


def _last_hitter_games(
    team_id: int,
    side_team_id: int,
    player_id: int,
    end_date: str,
    pitcher_hand: str,
    max_games: int = 3,
) -> list[dict]:
    rows = []
    for game in reversed(_completed_games_for_team(team_id, end_date)):
        box = _get_boxscore(game["gamePk"])
        if not box:
            continue
        home_id = game["teams"]["home"]["team"]["id"]
        side = "home" if home_id == side_team_id else "away"
        opponent = game["teams"]["away" if side == "home" else "home"]["team"]["name"]
        team_box = box.get("teams", {}).get(side, {})
        player = team_box.get("players", {}).get(f"ID{player_id}")
        lineup = team_box.get("battingOrder", [])
        if not player or str(player_id) not in [str(x) for x in lineup]:
            continue
        lineup_spot = [str(x) for x in lineup].index(str(player_id)) + 1
        rows.append(_boxscore_player_row(player, game["gameDate"][:10], opponent, pitcher_hand, lineup_spot))
        if len(rows) == max_games:
            break
    return rows


def _hitter_row_html(row: dict, include_date: bool = True, include_opp: bool = True) -> str:
    cells = []
    if include_date:
        cells.append(f"<td>{html(row['date'])}</td>")
    cells.extend([
        f"<td>{html(row['lineup_spot'])}</td>",
        f"<td class=\"identity\">{html(row['player'])}</td>",
    ])
    if include_opp:
        cells.append(f"<td>{html(row['opp'])}</td>")
    cells.extend([
        f"<td>{html(row['bat_side'])}</td>",
        f"<td>{html(row['pitcher_hand'])}</td>",
        f"<td>{html(row['pa'])}</td>",
        f"<td>{html(row['h'])}</td>",
        f"<td>{html(row['hr'])}</td>",
        f"<td>{html(row['bb'])}</td>",
        f"<td>{html(row['k'])}</td>",
        f"<td>{html(row['avg'])}</td>",
        f"<td>{html(row['obp'])}</td>",
        f"<td>{html(row['slg'])}</td>",
        f"<td>{html(row['k_pct'])}</td>",
        f"<td>{html(row['bb_pct'])}</td>",
        f"<td class=\"na\">{html(row['whiff_pct'])}</td>",
        f"<td class=\"na\">{html(row['chase_pct'])}</td>",
        f"<td class=\"na\">{html(row['hardhit_pct'])}</td>",
        f"<td class=\"na\">{html(row['barrel_pct'])}</td>",
        f"<td class=\"na\">{html(row['babip'])}</td>",
    ])
    return "<tr class=\"hitter-row\">" + "".join(cells) + "</tr>"


def _hitter_detail_rows_html(rows: list[dict], fallback: dict) -> str:
    detail_rows = rows or [
        {**fallback, "date": f"Game -{n}", "pa": DASH, "h": DASH, "hr": DASH, "bb": DASH,
         "k": DASH, "avg": DASH, "obp": DASH, "slg": DASH, "k_pct": DASH, "bb_pct": DASH,
         "whiff_pct": DASH, "chase_pct": DASH, "hardhit_pct": DASH, "barrel_pct": DASH, "babip": DASH}
        for n in range(1, 4)
    ]
    rendered = []
    for i, row in enumerate(detail_rows):
        klass = "hitter-row hitter-detail-start" if i == 0 else "hitter-row"
        rendered.append(_hitter_row_html(row).replace('class="hitter-row"', f'class="{klass}"', 1))
    return "\n".join(rendered)


def _season_hitter_row_html(player: dict | None, team_label: str, lineup_spot: int) -> str:
    name = player.get("person", {}).get("fullName") if player else f"Lineup Spot {lineup_spot}"
    person_id = player.get("person", {}).get("id") if player else None
    bat_side = _bat_side(person_id)
    return (
        "<tr class=\"hitter-row\">"
        f"<td>{html(lineup_spot)}</td>"
        f"<td class=\"identity\">{html(name or 'TBD')}</td>"
        f"<td>{html(team_label)}</td>"
        f"<td>{html(bat_side)}</td>"
        "<td colspan=\"18\" class=\"na\">Awaiting season hitter data</td>"
        "</tr>"
    )


def _fatigue_row_html(metrics: dict) -> str:
    return (
        "<tr class=\"hitter-row\">"
        f"<td class=\"identity\">{html(metrics['team'])}</td>"
        f"<td>{html(metrics['date'])}</td>"
        f"<td>{html(metrics['venue'])}</td>"
        f"<td>{html(metrics['city'])}</td>"
        f"<td>{html(metrics['previous_city'])}</td>"
        f"<td>{html(metrics['days_off'])}</td>"
        f"<td>{html(metrics['games_last_3'])}</td>"
        f"<td>{html(metrics['consecutive_days'])}</td>"
        f"<td>{html(metrics['tz_change'])}</td>"
        f"<td>{html(metrics['travel_distance'])}</td>"
        f"<td>{html(metrics['night_before_day'])}</td>"
        f"<td>{html(metrics['jet_lag'])}</td>"
        f"<td>{html(metrics['arrival_risk'])}</td>"
        f"<td>{html(metrics['rest_notes'])}</td>"
        "</tr>"
    )


def _hitter_table_header(include_date: bool = True, include_opp: bool = True) -> str:
    date_header = "<th>Date</th>" if include_date else ""
    opp_header = "<th>Opp</th>" if include_opp else ""
    context_cols = 4 + int(include_date) + int(include_opp)
    return f"""
      <thead>
        <tr class="group-header">
          <th colspan="{context_cols}">Game Context</th>
          <th colspan="8">Box Score</th>
          <th colspan="7">Plate Discipline / Quality</th>
        </tr>
        <tr>
          {date_header}
          <th>Lineup Spot</th>
          <th>Player</th>
          {opp_header}
          <th>Bat Side</th>
          <th>Pitcher Hand</th>
          <th>PA</th>
          <th>H</th>
          <th>HR</th>
          <th>BB</th>
          <th>K</th>
          <th>AVG</th>
          <th>OBP</th>
          <th>SLG</th>
          <th>K%</th>
          <th>BB%</th>
          <th>Whiff%</th>
          <th>Chase%</th>
          <th>HardHit%</th>
          <th>Barrel%</th>
          <th>BABIP</th>
        </tr>
      </thead>
    """


def _season_table_header() -> str:
    return """
      <thead>
        <tr class="group-header">
          <th colspan="4">Player</th>
          <th colspan="10">Season Production</th>
          <th colspan="8">Batted Ball / Expected</th>
        </tr>
        <tr>
          <th>Lineup Spot</th><th>Player</th><th>Team</th><th>Bat Side</th>
          <th>PA</th><th>AVG</th><th>OBP</th><th>SLG</th><th>OPS</th><th>ISO</th>
          <th>BABIP</th><th>K%</th><th>BB%</th><th>Whiff%</th><th>Chase%</th>
          <th>Contact%</th><th>HardHit%</th><th>Barrel%</th><th>wOBA</th><th>xwOBA</th>
          <th>vs RHP OPS</th><th>vs LHP OPS</th>
        </tr>
      </thead>
    """


def _fatigue_table_header() -> str:
    return """
      <thead>
        <tr class="group-header">
          <th colspan="5">Travel Context</th><th colspan="5">Workload</th><th colspan="4">Risk</th>
        </tr>
        <tr>
          <th>Team</th><th>Date</th><th>Venue</th><th>City</th><th>Previous City</th>
          <th>Days Off</th><th>Games Last 3 Days</th><th>Consecutive Game Days</th>
          <th>Time Zone Change</th><th>Travel Distance</th><th>Night Before Day?</th>
          <th>Jet Lag Direction</th><th>Arrival Risk</th><th>Rest Notes</th>
        </tr>
      </thead>
    """


def build_hitter_section(
    label: str,
    team_id: int,
    side: str,
    opponent_label: str,
    pitcher_hand: str,
    report_date: str,
    boxscore: dict | None,
    current_game: dict,
    current_venue_id: int | None,
    is_opponent: bool = False,
) -> str:
    lineup = _lineup_players(boxscore, side, 5)
    status = "Confirmed" if len(lineup) == 5 else "Probable"
    card_class = "game-header hitters-card opponent-hitters-card" if is_opponent else "game-header hitters-card"

    today_rows = []
    detail_rows = []
    season_rows = []
    fatigue = _fatigue_metrics(team_id, label, report_date, current_game, current_venue_id)
    for spot in range(1, 6):
        lineup_item = next((item for item in lineup if item["lineup_spot"] == spot), None)
        player = lineup_item["player"] if lineup_item else None
        fallback = _blank_hitter_row(spot, report_date, opponent_label, pitcher_hand)
        if player:
            fallback["player"] = player.get("person", {}).get("fullName", "TBD")
            fallback["bat_side"] = _bat_side(player.get("person", {}).get("id"))
            recent = _last_hitter_games(team_id, team_id, player.get("person", {}).get("id"), report_date, pitcher_hand)
        else:
            recent = []
        today_rows.append(_hitter_row_html(_sum_hitter_rows(recent, fallback), include_date=False, include_opp=False))
        detail_rows.append(_hitter_detail_rows_html(recent, fallback))
        season_rows.append(_season_hitter_row_html(player, label, spot))

    return f"""
    <div class="{card_class}">
      <div class="hitter-header-line">
        {html(label)} Hitters
        <span class="sep2">|</span>
        Top 1-5 Lineup
        <span class="sep2">|</span>
        <span class="lineup-status">{html(status)}</span>
      </div>
    </div>

    <h3>{html(label)} Today</h3>
    <div class="table-scroll">
      <table class="hitters-table">
        {_hitter_table_header(include_date=False, include_opp=False)}
        <tbody>
          {''.join(today_rows)}
        </tbody>
      </table>
    </div>

    <h3>{html(label)} last 3 games</h3>
    <div class="table-scroll">
      <table class="hitters-table">
        {_hitter_table_header(include_date=True)}
        <tbody>
          {''.join(detail_rows)}
        </tbody>
      </table>
    </div>

    <h3>{html(label)} season</h3>
    <div class="table-scroll">
      <table class="hitters-table">
        {_season_table_header()}
        <tbody>
          {''.join(season_rows)}
        </tbody>
      </table>
    </div>

    <h3>{html(label)} fatigue</h3>
    <div class="table-scroll">
      <table class="hitters-table">
        {_fatigue_table_header()}
        <tbody>
          {_fatigue_row_html(fatigue)}
        </tbody>
      </table>
    </div>
    """


# ── Template renderer ─────────────────────────────────────────────────────────

def render(tmpl: str, data: dict) -> str:
    for k, v in data.items():
        tmpl = tmpl.replace("{{" + k + "}}", str(v))
    return tmpl


# Date picker paused for now.
# def build_date_options(report_date: str, days_back: int = 14) -> str:
#     selected = date.fromisoformat(report_date)
#     options = []
#     for offset in range(days_back + 1):
#         value = (selected - timedelta(days=offset)).isoformat()
#         label = (selected - timedelta(days=offset)).strftime("%a, %b %-d, %Y")
#         selected_attr = " selected" if value == report_date else ""
#         options.append(f'<option value="{value}"{selected_attr}>{label}</option>')
#     return "\n".join(options)


def open_output_in_chrome(path: Path) -> None:
    url = path.resolve().as_uri()
    try:
        result = subprocess.run(
            [
                "osascript",
                "-e", 'tell application "Google Chrome"',
                "-e", "if not (exists window 1) then make new window",
                "-e", f'tell window 1 to make new tab with properties {{URL:"{url}"}}',
                "-e", "activate",
                "-e", "end tell",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return

        fallback = subprocess.run(
            ["open", "-a", "Google Chrome", url],
            check=False,
            capture_output=True,
            text=True,
        )
        if fallback.returncode != 0:
            details = fallback.stderr.strip() or result.stderr.strip() or "unknown error"
            print(f"Could not open Chrome automatically: {details}")
    except Exception as exc:
            print(f"Could not open Chrome automatically: {exc}")


# Date picker / local server paused for now.
# def open_url_in_chrome(url: str) -> None:
#     try:
#         subprocess.run(["open", "-a", "Google Chrome", url], check=False)
#     except Exception as exc:
#         print(f"Could not open Chrome automatically: {exc}")


def render_report(report_date: str) -> str:
    print(f"Fetching data for {report_date} …")

    game = get_game_for_date(report_date)
    if not game:
        raise ValueError(f"No Blue Jays game scheduled on {report_date}.")

    return build_report_html(game, report_date)


def build_report_html(game: dict, report_date: str) -> str:
    selected_date = date.fromisoformat(report_date)
    is_past_date = selected_date < date.today()

    home     = game["teams"]["home"]["team"]
    away     = game["teams"]["away"]["team"]
    jays_home    = home["id"] == TEAM_ID
    jays_side    = "home" if jays_home else "away"
    opp_side     = "away" if jays_home else "home"
    opp_team     = away if jays_home else home
    opponent_name = opp_team["name"]
    opp_team_id   = opp_team["id"]

    game_utc_str = game.get("gameDate", "")
    venue_name   = game.get("venue", {}).get("name", DASH)
    venue_id     = game.get("venue", {}).get("id")

    try:
        gdt       = datetime.fromisoformat(game_utc_str.replace("Z", "+00:00"))
        game_time = gdt.astimezone(ZoneInfo("America/Toronto")).strftime("%-I:%M %p ET")
    except Exception:
        game_time = DASH

    current_time = datetime.now(ZoneInfo("America/Toronto")).strftime("%-I:%M:%S %p ET")

    weather = DASH
    if venue_id and not is_past_date:
        coords = get_venue_coords(venue_id)
        if coords:
            weather = get_weather(coords[0], coords[1], game_utc_str)

    detail = game.get("status", {}).get("detailedState", "Scheduled")
    if detail == "Final":
        pitcher_status = "Final"
    elif detail in ("Pre-Game", "Warmup", "In Progress"):
        pitcher_status = "Confirmed"
    else:
        pitcher_status = "Probable"
    status_class = f"status-{pitcher_status.lower()}"

    game_pk = game.get("gamePk")
    jays_probable = game["teams"][jays_side].get("probablePitcher", {})
    opp_probable = game["teams"][opp_side].get("probablePitcher", {})
    if not jays_probable and game_pk:
        jays_probable = get_boxscore_starter(game_pk, jays_side)
    if not opp_probable and game_pk:
        opp_probable = get_boxscore_starter(game_pk, opp_side)

    pitcher_id, pitcher_name, pitch_hand = resolve_pitcher(jays_probable)
    opp_pitcher_id, opp_pitcher_name, opp_pitch_hand = resolve_pitcher(opp_probable)

    jays_logo_url = f"https://www.mlbstatic.com/team-logos/{TEAM_ID}.svg"
    opp_logo_url  = f"https://www.mlbstatic.com/team-logos/{opp_team_id}.svg"
    boxscore = _get_boxscore(game_pk) if game_pk else None
    hitters_content = build_hitter_section(
        "Blue Jays",
        TEAM_ID,
        jays_side,
        opponent_name,
        opp_pitch_hand,
        report_date,
        boxscore,
        game,
        venue_id,
    ) + build_hitter_section(
        opponent_name,
        opp_team_id,
        opp_side,
        "Blue Jays",
        pitch_hand,
        report_date,
        boxscore,
        game,
        venue_id,
        is_opponent=True,
    )

    ctx: dict = {
        "TODAY_DATE":    format_display_date(report_date),
        # Date picker paused for now.
        "DATE_OPTIONS":  "",
        "DATE_FORM_ACTION": "",
        "OPP_TODAY":     opponent_name,
        "GAME_TIME":     game_time,
        "GAME_START_ISO": game_utc_str,
        "GAME_STATUS":    detail,
        "CURRENT_TIME":  current_time,
        "VENUE_NAME":    venue_name,
        "WEATHER":       weather,
        "STARTING_PITCHER": pitcher_name,
        "PITCH_HAND":       pitch_hand,
        "PITCH_HAND_ICON":  pitch_hand_icon(pitch_hand),
        "PITCHER_STATUS":   pitcher_status,
        "STATUS_CLASS":     status_class,
        "OPP_STARTING_PITCHER": opp_pitcher_name,
        "OPP_PITCH_HAND":       opp_pitch_hand,
        "OPP_PITCH_HAND_ICON":  pitch_hand_icon(opp_pitch_hand),
        "OPP_PITCHER_STATUS":   pitcher_status,
        "OPP_STATUS_CLASS":     status_class,
        "JAYS_LOGO_URL": jays_logo_url,
        "OPP_LOGO_URL":  opp_logo_url,
        "OPP_TEAM_NAME": opponent_name,
        "HITTERS_CONTENT": hitters_content,
    }

    jays_starts = get_last_n_starts(pitcher_id, report_date, 3) if pitcher_id else []
    prev_date = None
    for i, split in enumerate(jays_starts):
        sc = get_statcast_splits(pitcher_id, split["date"]) if pitcher_id else None
        ctx.update(build_row(split, i + 1, sc, prev_date, key_prefix=""))
        prev_date = split["date"]
    for i in range(len(jays_starts), 3):
        ctx.update(empty_row(i + 1, key_prefix=""))

    opp_starts = get_last_n_starts(opp_pitcher_id, report_date, 3) if opp_pitcher_id else []
    opp_prev_date = None
    for i, split in enumerate(opp_starts):
        sc = get_statcast_splits(opp_pitcher_id, split["date"]) if opp_pitcher_id else None
        ctx.update(build_row(split, i + 1, sc, opp_prev_date, key_prefix="OP_"))
        opp_prev_date = split["date"]
    for i in range(len(opp_starts), 3):
        ctx.update(empty_row(i + 1, key_prefix="OP_"))

    return render(TEMPLATE.read_text(), ctx)


# Date picker / local server paused for now.
# class ReportHandler(BaseHTTPRequestHandler):
#     def do_GET(self):
#         parsed = urlparse(self.path)
#         if parsed.path == "/styles.css":
#             self._send_file(STYLESHEET, "text/css")
#             return
#
#         if parsed.path not in ("/", "/index.html"):
#             self.send_error(404)
#             return
#
#         report_date = parse_qs(parsed.query).get("date", [DEFAULT_REPORT_DATE])[0]
#         try:
#             datetime.strptime(report_date, "%Y-%m-%d")
#             html = render_report(report_date)
#         except Exception as exc:
#             html = f"""<!DOCTYPE html>
# <html lang="en">
# <head>
#   <meta charset="UTF-8">
#   <title>Base Boys Blue Club</title>
#   <link rel="stylesheet" href="/styles.css">
# </head>
# <body>
#   <h1>⚾ Base Boys Blue Club</h1>
#   <div class="game-header">
#     <div class="matchup">Could not render {report_date}</div>
#     <div class="pitcher-line">{exc}</div>
#   </div>
# </body>
# </html>"""
#         self._send_bytes(html.encode("utf-8"), "text/html; charset=utf-8")
#
#     def log_message(self, format: str, *args) -> None:
#         return
#
#     def _send_file(self, path: Path, content_type: str) -> None:
#         self._send_bytes(path.read_bytes(), content_type)
#
#     def _send_bytes(self, body: bytes, content_type: str) -> None:
#         self.send_response(200)
#         self.send_header("Content-Type", content_type)
#         self.send_header("Content-Length", str(len(body)))
#         self.end_headers()
#         self.wfile.write(body)
#
#
# def serve(report_date: str) -> None:
#     url = f"http://{HOST}:{PORT}/?date={report_date}"
#     print(f"Serving date picker at {url}")
#     open_url_in_chrome(url)
#     HTTPServer((HOST, PORT), ReportHandler).serve_forever()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    output = render_report(DEFAULT_REPORT_DATE)
    OUTPUT.write_text(output)
    print(f"Written → {OUTPUT}")
    open_output_in_chrome(OUTPUT)


if __name__ == "__main__":
    main()
