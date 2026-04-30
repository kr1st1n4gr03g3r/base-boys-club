"""
generate.py — Fetch Blue Jays pitcher data and render index.html

Data sources:
  - MLB Stats API   : today's game, probable pitcher, last-3-start game logs
  - Open-Meteo API  : venue weather (free, no key)
  - pybaseball      : Statcast pitch data for per-game Whiff%, Chase%, BABIP,
                      WHIP (approximate), and RHB/LHB event splits
"""

from __future__ import annotations

import sys
from datetime import date, datetime
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
SEASON = date.today().year
TODAY = date.today().isoformat()
TEMPLATE = Path(__file__).parent / "template.html"
OUTPUT = Path(__file__).parent / "index.html"
DASH = "—"

# Statcast pitch description buckets
_SWING_DESC = frozenset({
    "swinging_strike", "swinging_strike_blocked", "foul", "foul_tip",
    "hit_into_play", "hit_into_play_no_out", "hit_into_play_score",
    "foul_bunt", "missed_bunt",
})
_WHIFF_DESC = frozenset({"swinging_strike", "swinging_strike_blocked"})
_OUT_ZONES = frozenset({11, 12, 13, 14})  # zones outside the strike zone

_HIT_EVENTS = frozenset({"single", "double", "triple", "home_run"})
_WALK_EVENTS = frozenset({"walk", "hit_by_pitch"})
_K_EVENTS = frozenset({"strikeout", "strikeout_double_play"})

# Balls in play (denominator for BABIP)
_BIP_EVENTS = frozenset({
    "single", "double", "triple",
    "field_out", "force_out", "grounded_into_double_play", "double_play",
    "fielders_choice", "fielders_choice_out",
    "sac_fly", "sac_fly_error", "sac_bunt", "field_error",
})

# Outs recorded on the play — used to compute split IP (total_outs / 3)
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


def get_todays_game() -> dict | None:
    data = _mlb("/schedule",
                sportId=1, teamId=TEAM_ID,
                startDate=TODAY, endDate=TODAY,
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
        v = _mlb(f"/venues/{venue_id}", hydrate="location")["venues"][0]
        coords = v.get("location", {}).get("defaultCoordinates", {})
        lat, lon = coords.get("latitude"), coords.get("longitude")
        if lat and lon:
            return float(lat), float(lon)
    except Exception:
        pass
    return None


def get_last_n_starts(pitcher_id: int, n: int = 3) -> list[dict]:
    data = _mlb(f"/people/{pitcher_id}/stats",
                stats="gameLog", group="pitching",
                season=SEASON, gameType="R")
    splits = data.get("stats", [{}])[0].get("splits", [])
    starts = [s for s in splits if s.get("stat", {}).get("gamesStarted", 0) == 1]
    return starts[-n:]


# ── Weather ───────────────────────────────────────────────────────────────────

def get_weather(lat: float, lon: float, game_utc_str: str) -> str:
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon,
                "hourly": "temperature_2m,precipitation_probability,windspeed_10m",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "timezone": "auto",
                "forecast_days": 2,
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()

        game_dt = datetime.fromisoformat(game_utc_str.replace("Z", "+00:00"))
        tz_str = data.get("timezone", "UTC")
        try:
            local_dt = game_dt.astimezone(ZoneInfo(tz_str))
        except Exception:
            local_dt = game_dt

        target = local_dt.strftime("%Y-%m-%dT%H:00")
        times = data["hourly"]["time"]
        idx = times.index(target) if target in times else 0

        temp = data["hourly"]["temperature_2m"][idx]
        wind = data["hourly"]["windspeed_10m"][idx]
        precip = data["hourly"]["precipitation_probability"][idx]
        return f"{temp:.0f}°F, {wind:.0f} mph wind, {precip}% precip"
    except Exception:
        return DASH


# ── Statcast splits ───────────────────────────────────────────────────────────

def _safe_pct(num: int, den: int) -> str:
    return f"{100 * num / den:.1f}%" if den else DASH


def _split_counts(df: pd.DataFrame) -> dict:
    """Compute all per-split metrics from a Statcast pitch DataFrame subset."""
    events = df["events"].fillna("")
    desc = df["description"].fillna("")
    zone = df["zone"].fillna(0).astype(int)

    bf = df["at_bat_number"].nunique()
    h = int(events.isin(_HIT_EVENTS).sum())
    hr = int((events == "home_run").sum())
    bb = int(events.isin(_WALK_EVENTS).sum())
    k = int(events.isin(_K_EVENTS).sum())

    # BABIP — (H - HR) / balls in play
    h_in_play = int(events.isin({"single", "double", "triple"}).sum())
    bip = int(events.isin(_BIP_EVENTS).sum())
    babip = f"{h_in_play / bip:.3f}" if bip else DASH

    # IP from actual outs recorded on the play (GIDP = 2, triple play = 3, else 1)
    ip_approx = _count_outs(events) / 3
    whip = f"{(h + bb) / ip_approx:.2f}" if ip_approx > 0 else DASH

    # Whiff% and Chase%
    swings = int(desc.isin(_SWING_DESC).sum())
    whiffs = int(desc.isin(_WHIFF_DESC).sum())
    outside = zone.isin(_OUT_ZONES)
    chase_swings = int((outside & desc.isin(_SWING_DESC)).sum())
    outside_total = int(outside.sum())

    return {
        "H": h, "BB": bb, "K": k, "HR": hr, "BF": int(bf),
        "babip": babip,
        "whip": whip,
        "whiff_pct": _safe_pct(whiffs, swings),
        "chase_pct": _safe_pct(chase_swings, outside_total),
        "k_pct": _safe_pct(k, int(bf)),
        "bb_pct": _safe_pct(bb, int(bf)),
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
            "rhb": _split_counts(df[df["stand"] == "R"]),
            "lhb": _split_counts(df[df["stand"] == "L"]),
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


# ── Per-game row builder ──────────────────────────────────────────────────────

def build_row(split: dict, n: int, sc: dict | None, prev_date: str | None) -> dict:
    stat = split["stat"]
    game_date = split.get("date", DASH)
    pitcher = split.get("player", {}).get("fullName", DASH)
    opponent = split.get("opponent", {}).get("name", DASH)

    ip_str = stat.get("inningsPitched", DASH)
    ip_val = parse_ip(ip_str)
    h = int(stat.get("hits", 0))
    bb = int(stat.get("baseOnBalls", 0))
    k = int(stat.get("strikeOuts", 0))
    er = int(stat.get("earnedRuns", 0))
    bf = int(stat.get("battersFaced", 0))

    # Game-level ERA and WHIP from official game log counts
    era_game = f"{er * 9 / ip_val:.2f}" if ip_val else DASH
    whip_game = f"{(h + bb) / ip_val:.2f}" if ip_val else DASH
    k_pct = _safe_pct(k, bf)
    bb_pct = _safe_pct(bb, bf)

    days_off = DASH
    if prev_date:
        try:
            delta = date.fromisoformat(game_date) - date.fromisoformat(prev_date)
            days_off = str(delta.days)
        except Exception:
            pass

    # Statcast-derived metrics (Whiff%, Chase%, BABIP, split stats)
    whiff_pct = chase_pct = DASH
    babip = DASH
    if sc:
        ov = sc["overall"]
        whiff_pct = ov["whiff_pct"]
        chase_pct = ov["chase_pct"]
        babip = ov["babip"]
        # Override K%/BB% with Statcast if game log BF is 0
        if bf == 0:
            k_pct = ov["k_pct"]
            bb_pct = ov["bb_pct"]

    def split_cells(side_key: str, prefix: str) -> dict:
        # R and ER are not included — template hardcodes N/A for those split cells
        if not sc:
            return {
                f"{prefix}_H_{n}": DASH, f"{prefix}_BB_{n}": DASH,
                f"{prefix}_K_{n}": DASH, f"{prefix}_HR_{n}": DASH,
                f"{prefix}_WHIP_{n}": DASH, f"{prefix}_K_PCT_{n}": DASH,
                f"{prefix}_BB_PCT_{n}": DASH, f"{prefix}_BABIP_{n}": DASH,
                f"{prefix}_WHIFF_PCT_{n}": DASH, f"{prefix}_CHASE_PCT_{n}": DASH,
            }
        s = sc[side_key]
        return {
            f"{prefix}_H_{n}": s["H"],
            f"{prefix}_BB_{n}": s["BB"],
            f"{prefix}_K_{n}": s["K"],
            f"{prefix}_HR_{n}": s["HR"],
            f"{prefix}_WHIP_{n}": s["whip"],
            f"{prefix}_K_PCT_{n}": s["k_pct"],
            f"{prefix}_BB_PCT_{n}": s["bb_pct"],
            f"{prefix}_BABIP_{n}": s["babip"],
            f"{prefix}_WHIFF_PCT_{n}": s["whiff_pct"],
            f"{prefix}_CHASE_PCT_{n}": s["chase_pct"],
        }

    row = {
        f"DATE_{n}": game_date,
        f"NAME_{n}": pitcher,
        f"OPP_{n}": opponent,
        f"IP_{n}": ip_str,
        f"DAYS_OFF_{n}": days_off,
        f"H_{n}": h,
        f"R_{n}": stat.get("runs", DASH),
        f"ER_{n}": er,
        f"BB_{n}": bb,
        f"K_{n}": k,
        f"HR_{n}": stat.get("homeRuns", DASH),
        f"ERA_{n}": era_game,
        f"WHIP_{n}": whip_game,
        f"K_PCT_{n}": k_pct,
        f"BB_PCT_{n}": bb_pct,
        f"BABIP_{n}": babip,
        f"WHIFF_PCT_{n}": whiff_pct,
        f"CHASE_PCT_{n}": chase_pct,
    }
    row.update(split_cells("rhb", "RHB"))
    row.update(split_cells("lhb", "LHB"))
    return row


def empty_row(n: int) -> dict:
    keys = [
        "DATE", "NAME", "OPP", "IP", "DAYS_OFF",
        "H", "R", "ER", "BB", "K", "HR", "ERA", "WHIP",
        "K_PCT", "BB_PCT", "BABIP", "WHIFF_PCT", "CHASE_PCT",
    ]
    split_keys = [
        "H", "BB", "K", "HR", "WHIP",
        "K_PCT", "BB_PCT", "BABIP", "WHIFF_PCT", "CHASE_PCT",
    ]
    row = {f"{k}_{n}": DASH for k in keys}
    for p in ("RHB", "LHB"):
        row.update({f"{p}_{k}_{n}": DASH for k in split_keys})
    return row


# ── Template renderer ─────────────────────────────────────────────────────────

def render(tmpl: str, data: dict) -> str:
    for k, v in data.items():
        tmpl = tmpl.replace("{{" + k + "}}", str(v))
    return tmpl


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Fetching data for {TODAY} …")

    game = get_todays_game()
    if not game:
        print("No Blue Jays game scheduled today.")
        sys.exit(0)

    home = game["teams"]["home"]["team"]
    away = game["teams"]["away"]["team"]
    jays_home = home["id"] == TEAM_ID
    opponent_name = away["name"] if jays_home else home["name"]
    jays_side = "home" if jays_home else "away"

    game_utc_str = game.get("gameDate", "")
    venue_name = game.get("venue", {}).get("name", DASH)
    venue_id = game.get("venue", {}).get("id")

    try:
        gdt = datetime.fromisoformat(game_utc_str.replace("Z", "+00:00"))
        game_time = gdt.astimezone(ZoneInfo("America/Toronto")).strftime("%-I:%M %p ET")
    except Exception:
        game_time = DASH

    weather = DASH
    if venue_id:
        coords = get_venue_coords(venue_id)
        if coords:
            weather = get_weather(coords[0], coords[1], game_utc_str)

    probable = game["teams"][jays_side].get("probablePitcher", {})
    pitcher_id = probable.get("id")
    pitcher_name = probable.get("fullName", "TBD")

    pitch_hand = DASH
    if pitcher_id:
        try:
            info = get_player_info(pitcher_id)
            hand = info.get("pitchHand", {}).get("description", "")
            pitch_hand = "RHP" if "right" in hand.lower() else "LHP" if "left" in hand.lower() else hand
        except Exception:
            pass

    detail = game.get("status", {}).get("detailedState", "Scheduled")
    pitcher_status = "Confirmed" if detail in ("Pre-Game", "Warmup", "In Progress") else "Probable"
    status_class = "status-confirmed" if pitcher_status == "Confirmed" else "status-probable"

    starts = get_last_n_starts(pitcher_id, 3) if pitcher_id else []

    ctx: dict = {
        "TODAY_DATE": TODAY,
        "OPP_TODAY": opponent_name,
        "GAME_TIME": game_time,
        "VENUE_NAME": venue_name,
        "WEATHER": weather,
        "STARTING_PITCHER": pitcher_name,
        "PITCH_HAND": pitch_hand,
        "PITCHER_STATUS": pitcher_status,
        "STATUS_CLASS": status_class,
    }

    prev_date = None
    for i, split in enumerate(starts):
        sc = get_statcast_splits(pitcher_id, split["date"]) if pitcher_id else None
        ctx.update(build_row(split, i + 1, sc, prev_date))
        prev_date = split["date"]

    for i in range(len(starts), 3):
        ctx.update(empty_row(i + 1))

    output = render(TEMPLATE.read_text(), ctx)
    OUTPUT.write_text(output)
    print(f"Written → {OUTPUT}")


if __name__ == "__main__":
    main()
