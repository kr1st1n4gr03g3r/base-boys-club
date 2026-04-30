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

import sys
import subprocess
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
        v = _mlb(f"/venues/{venue_id}", hydrate="location")["venues"][0]
        coords = v.get("location", {}).get("defaultCoordinates", {})
        lat, lon = coords.get("latitude"), coords.get("longitude")
        if lat and lon:
            return float(lat), float(lon)
    except Exception:
        pass
    return None


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
        return f"{temp:.0f}°F, {wind:.0f} mph wind, {precip}% precip"
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
