import csv
import io
import json
import re
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from tqdm import tqdm  # type: ignore[reportMissingModuleSource]

from enrichment import run_enrichment
from html_report import write_jinja_html_report
from unit_formatters import (
    format_distance,
    format_distance_difference,
    format_temperature,
    format_wind_speed,
)

MLB_API_BASE = "https://statsapi.mlb.com/api"
BASEBALL_SAVANT_BASE = "https://baseballsavant.mlb.com"

OUTPUT_DIR = Path("output")

POSITION_NUMBERS = {
    "P": "1",
    "C": "2",
    "1B": "3",
    "2B": "4",
    "3B": "5",
    "SS": "6",
    "LF": "7",
    "CF": "8",
    "RF": "9",
    "DH": "DH",
    "PH": "PH",
    "PR": "PR",
}


POSITION_DESCRIPTION_TO_ABBREVIATION = {
    "pitcher": "P",
    "catcher": "C",
    "first baseman": "1B",
    "second baseman": "2B",
    "third baseman": "3B",
    "shortstop": "SS",
    "left fielder": "LF",
    "center fielder": "CF",
    "right fielder": "RF",
}

POSITION_DESCRIPTION_TO_NUMBER = {
    "pitcher": "1",
    "catcher": "2",
    "first baseman": "3",
    "second baseman": "4",
    "third baseman": "5",
    "shortstop": "6",
    "left fielder": "7",
    "center fielder": "8",
    "right fielder": "9",
}


def build_game_context(feed, venue_details=None):
    if venue_details is None:
        venue_details = {}

    game_data = feed.get("gameData", {})
    away_name = safe_get(game_data, "teams", "away", "name", default="Away")
    away_id = safe_get(game_data, "teams", "away", "id")
    home_name = safe_get(game_data, "teams", "home", "name", default="Home")
    home_id = safe_get(game_data, "teams", "home", "id")
    game_date = safe_get(game_data, "datetime", "officialDate", default="Unknown date")
    game_first_pitch = safe_get(feed, "gameData", "gameInfo", "firstPitch")
    timezone = get_game_timezone(feed, venue_details)
    home_runs = safe_get(feed, "liveData", "linescore", "teams", "home", "runs")
    away_runs = safe_get(feed, "liveData", "linescore", "teams", "away", "runs")
    game_state = safe_get(feed, "gameData", "status", "abstractGameState", default="")
    weather = safe_get(feed, "gameData", "weather", default={})
    attendance = safe_get(feed, "gameData", "gameInfo", "attendance")
    game_end_time = safe_get(feed, "gameData", "gameInfo", "endDateTime")
    game_duration = safe_get(feed, "gameData", "gameInfo", "gameDurationMinutes")

    # Formatting for header
    if game_first_pitch and game_duration:
        start = datetime.fromisoformat(game_first_pitch.replace("Z", "+00:00"))
        end = start + timedelta(minutes=int(game_duration))
        eastern_start = start.astimezone(ZoneInfo("America/Toronto"))
        eastern_end = end.astimezone(ZoneInfo("America/Toronto"))
        game_first_pitch = eastern_start.strftime("%I:%M %p %Z").lstrip("0")
        game_end_time = eastern_end.strftime("%I:%M %p %Z").lstrip("0")
        hours, mins = divmod(int(game_duration), 60)
        game_duration = f"{hours}h {mins}m"

    # Add a comma for attendance number
    if attendance:
        attendance = f"{attendance:,}"

    # prints all game data
    # print(game_data)
    return {
        "title": {
            "away_logo": get_team_logo_url(away_id),
            "home_logo": get_team_logo_url(home_id),
            "away_name": away_name,
            "home_name": home_name,
        },
        "game": {
            "date": game_date,
            "game_first_pitch": game_first_pitch,
            "game_end_time": game_end_time,
            "game_duration": game_duration,
            "timezone": timezone,
            "final_score": f"{game_state}: {away_runs} - {home_runs}",
            "attendance": attendance,
        },
        "weather": {
            "condition": weather.get("condition"),
            "temp": format_temperature(weather.get("temp")),
            "wind": parse_wind(weather.get("wind")),
        },
    }


def get_count_display(balls, strikes):
    ball_one = "⬛️" if balls >= 1 else "⬜️"
    ball_two = "⬛️" if balls >= 2 else "⬜️"
    ball_three = "⬛️" if balls >= 3 else "⬜️"
    strike_one = "⬛️" if strikes >= 1 else "⬜️"
    strike_two = "⬛️" if strikes >= 2 else "⬜️"

    return {
        "ball_one": ball_one,
        "ball_two": ball_two,
        "ball_three": ball_three,
        "strike_one": strike_one,
        "strike_two": strike_two,
    }


def get_diamond_icon(play):
    """
    Returns the diamond icon name for how the batter reached base (e.g. "B1"),
    or "" if the batter didn't reach base.

    TODO: not yet handled -- catcher's interference, fielder obstruction,
    batted ball striking a runner or umpire.
    """
    event_type = safe_get(play, "result", "eventType", default="")
    description = safe_get(play, "result", "description", default="").lower()
    is_out = safe_get(play, "result", "isOut", default=False)
    print(event_type, description)

    # Reaches First (B1)

    if event_type == "single":
        return "B1"

    if event_type == "hit_by_pitch":
        return "B1"

    if event_type in ("walk", "intent_walk") and not is_out:
        return "B1"

    if event_type in ("field_error", "fielders_choice") and not is_out:
        return "B1"

    # dropped third strike / wild pitch still lets the batter reach 1st
    if event_type == "strikeout" and not is_out and "wild pitch" in description:
        return "B1"

    # Reaches Second (B2)

    if event_type == "double":
        return "B2"

    return ""


def build_team_players(team_players, game_players, at_bat_counts):
    slots = {}

    for _, player_data in team_players.items():
        batting_order = player_data.get("battingOrder")
        if not batting_order:
            continue

        batting_order_int = int(batting_order)
        slot_number = int(str(batting_order)[0])

        player_id = safe_get(player_data, "person", "id")
        game_player = game_players.get(f"ID{player_id}", {})

        if slot_number not in slots:
            slots[slot_number] = {"players": []}

        slots[slot_number]["players"].append(
            {
                "player_id": player_id,
                "batting_order": batting_order_int,
                "bat_side": safe_get(game_player, "batSide", "code", default=""),
                "primary_number": game_player.get("primaryNumber", ""),
                "boxscore_name": game_player.get("boxscoreName", ""),
                "position": safe_get(
                    game_player, "primaryPosition", "abbreviation", default=""
                ),
            }
        )

    result = []
    for slot_number in sorted(slots.keys()):
        slot = slots[slot_number]
        slot["players"].sort(key=lambda p: p["batting_order"])

        player_ids = [p["player_id"] for p in slot["players"]]
        innings = []
        for i in range(1, 10):
            inning_data = {}
            for player_id in player_ids:
                if player_id in at_bat_counts and i in at_bat_counts[player_id]:
                    inning_data = at_bat_counts[player_id][i]
                    break
            count_display = get_count_display(
                inning_data.get("balls", 0),
                inning_data.get("strikes", 0),
            )
            count_display["result"] = inning_data.get("result", "")
            count_display["icon"] = inning_data.get("icon", "")
            innings.append(count_display)

        slot["innings"] = innings
        result.append(slot)

    return result


def player_scorecard(feed):
    game_players = safe_get(feed, "gameData", "players", default={})
    all_plays = safe_get(feed, "liveData", "plays", "allPlays", default=[])
    at_bat_counts = {}

    for play in all_plays:
        batter_id = safe_get(play, "matchup", "batter", "id")
        inning = safe_get(play, "about", "inning")
        balls = safe_get(play, "count", "balls", default=0)
        strikes = safe_get(play, "count", "strikes", default=0)

        if batter_id and inning:
            if batter_id not in at_bat_counts:
                at_bat_counts[batter_id] = {}
            if inning not in at_bat_counts[batter_id]:
                at_bat_counts[batter_id][inning] = {
                    "balls": balls,
                    "strikes": strikes,
                    "result": get_batter_result_shorthand(play),
                    "icon": get_diamond_icon(play),
                }

    home_team_players = safe_get(
        feed, "liveData", "boxscore", "teams", "home", "players", default={}
    )
    away_team_players = safe_get(
        feed, "liveData", "boxscore", "teams", "away", "players", default={}
    )

    return {
        "home": {
            "players": build_team_players(
                home_team_players, game_players, at_bat_counts
            ),
        },
        "away": {
            "players": build_team_players(
                away_team_players, game_players, at_bat_counts
            ),
        },
        # "innings": {},
        # "player_stats": {},
    }


def get_position_number(position_code):
    return POSITION_NUMBERS.get(position_code, position_code)


def get_position_from_description(description):
    if not description:
        return ""

    description_lower = description.lower()

    for keyword, abbreviation in POSITION_DESCRIPTION_TO_ABBREVIATION.items():
        if keyword in description_lower:
            return get_position_number(abbreviation)

    return ""


def get_result_type_shorthand(event, description, is_out=False):
    event_lower = event.lower() if event else ""
    description_lower = description.lower() if description else ""

    if "triple play" in event_lower or "triple play" in description_lower:
        return "TP"

    if "double play" in event_lower or "double play" in description_lower:
        return "DP"

    if "home run" in event_lower:
        return "HR"

    if "strikeout" in event_lower:
        # WILD - dropped third strike / wild pitch still lets the batter reach 1st
        if not is_out and "wild pitch" in description_lower:
            return "WILD"
        if (
            "strikes out looking" in description_lower
            or "called out on strikes" in description_lower
        ):
            return "ꓘ"
        return "K"

    if event_lower in ["flyout", "fly out"]:
        return "F"

    if event_lower in ["pop out", "popout"]:
        return "P"

    if event_lower in ["lineout", "line out"]:
        return "L"

    if event_lower == "groundout":
        return "G"

    if event_lower == "single":
        return "B1"

    if event_lower == "double":
        return "B2"

    if event_lower == "triple":
        return "B3"

    # HBP
    if event_lower == "hit by pitch":
        return "HBP"

    # BB
    if event_lower == "walk" and not is_out:
        return "BB"

    # IW
    if event_lower == "intent walk" and not is_out:
        return "IW"

    # Err
    if event_lower in ("field error", "fielders choice") and not is_out:
        return "Err"

    return ""


def get_fielding_sequence_from_description(description):
    """
    Extracts scoring sequences from MLB descriptions.

    Example:
        'grounds out, first baseman Pete Alonso to pitcher Kyle Bradish.'
        returns '3-1'
    """
    if not description:
        return ""

    description_lower = description.lower()

    found_positions = []

    for position_description, position_number in POSITION_DESCRIPTION_TO_NUMBER.items():
        pattern = rf"\b{re.escape(position_description)}\b"

        for match in re.finditer(pattern, description_lower):
            found_positions.append(
                {
                    "start": match.start(),
                    "number": position_number,
                }
            )

    if not found_positions:
        return ""

    found_positions.sort(key=lambda item: item["start"])

    position_numbers = []

    for item in found_positions:
        if not position_numbers or position_numbers[-1] != item["number"]:
            position_numbers.append(item["number"])

    if len(position_numbers) < 2:
        return ""

    return "-".join(position_numbers)


def get_batter_result_shorthand(play):
    event = safe_get(play, "result", "event", default="")
    description = safe_get(play, "result", "description", default="")
    is_out = safe_get(play, "result", "isOut", default=False)

    result_code = get_result_type_shorthand(event, description, is_out)

    if not result_code:
        return ""

    if result_code in ["K", "ꓘ", "HR", "DP", "TP"]:
        return result_code

    fielding_sequence = get_fielding_sequence_from_description(description)

    if fielding_sequence:
        return f"{result_code}: {fielding_sequence}"

    position_number = get_position_from_description(description)

    if position_number:
        return f"{result_code}: {position_number}"

    return result_code


def get_schedule(date, home_team_name=None, away_team_name=None):
    """
    date format: YYYY-MM-DD
    Example:
        get_schedule("2025-06-01", home_team_name="Orioles", away_team_name="Blue Jays")
    """
    url = f"{MLB_API_BASE}/v1/schedule"
    params = {"sportId": 1, "date": date, "hydrate": "team,venue"}

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    games = []

    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            home = game["teams"]["home"]["team"]["name"]
            away = game["teams"]["away"]["team"]["name"]
            venue = game.get("venue", {}).get("name", "")

            if home_team_name and home_team_name.lower() not in home.lower():
                continue

            if away_team_name and away_team_name.lower() not in away.lower():
                continue

            games.append(
                {
                    "gamePk": game["gamePk"],
                    "gameDate": game.get("gameDate"),
                    "home": home,
                    "away": away,
                    "venue": venue,
                    "status": game.get("status", {}).get("detailedState"),
                }
            )

    return games


def parse_wind(wind_text):
    """
    Converts MLB wind text like:
      '6 mph, R To L'
      '6 mph, Out To RF'
    into:
      '10 kph / 6 mph, R To L'
    """
    if not wind_text:
        return None

    parts = wind_text.split(",", 1)
    speed_part = parts[0].strip()
    direction_part = parts[1].strip() if len(parts) > 1 else ""

    speed_number = None

    for token in speed_part.split():
        try:
            speed_number = float(token)
            break
        except ValueError:
            continue

    if speed_number is None:
        return wind_text

    speed_text = format_wind_speed(speed_number)

    if direction_part:
        return f"{speed_text}, {direction_part}"

    return speed_text


def get_game_feed(game_pk):
    url = f"{MLB_API_BASE}/v1.1/game/{game_pk}/feed/live"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


def get_venue_details(venue_id):
    url = f"{MLB_API_BASE}/v1/venues/{venue_id}"
    params = {"hydrate": "location,fieldInfo,timezone"}

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    venues = data.get("venues", [])

    if not venues:
        return {}

    return venues[0]


def parse_float(value):
    if value in (None, ""):
        return None

    try:
        return float(value)
    except ValueError:
        return None


def fetch_savant_csv(path, params):
    response = requests.get(f"{BASEBALL_SAVANT_BASE}{path}", params=params, timeout=30)
    response.raise_for_status()
    text = response.text.lstrip("﻿")
    return list(csv.DictReader(io.StringIO(text)))


def get_batting_expected_stats(season):
    """xBA, xSLG, xwOBA for qualified batters in a season."""
    rows = fetch_savant_csv(
        "/leaderboard/expected_statistics",
        {
            "type": "batter",
            "year": season,
            "position": "",
            "team": "",
            "min": "1",
            "csv": "true",
        },
    )

    return {
        int(row["player_id"]): {
            "xba": parse_float(row.get("est_ba")),
            "xslg": parse_float(row.get("est_slg")),
            "xwoba": parse_float(row.get("est_woba")),
        }
        for row in rows
    }


def get_pitching_expected_stats(season):
    """xERA for pitchers in a season."""
    rows = fetch_savant_csv(
        "/leaderboard/expected_statistics",
        {
            "type": "pitcher",
            "year": season,
            "position": "",
            "team": "",
            "min": "1",
            "csv": "true",
        },
    )

    return {
        int(row["player_id"]): {"xera": parse_float(row.get("xera"))} for row in rows
    }


def get_batted_ball_metrics(season):
    """Barrel%, hard-hit%, and average exit velocity for a season."""
    rows = fetch_savant_csv(
        "/leaderboard/custom",
        {
            "year": season,
            "type": "batter",
            "min": "1",
            "selections": "barrel_batted_rate,hard_hit_percent,exit_velocity_avg",
            "chart": "false",
            "x": "barrel_batted_rate",
            "y": "barrel_batted_rate",
            "r": "no",
            "chartType": "beeswarm",
            "csv": "true",
        },
    )

    return {
        int(row["player_id"]): {
            "barrel_pct": parse_float(row.get("barrel_batted_rate")),
            "hard_hit_pct": parse_float(row.get("hard_hit_percent")),
            "avg_ev": parse_float(row.get("exit_velocity_avg")),
        }
        for row in rows
    }


def get_sprint_speed_metrics(season):
    """Sprint speed (ft/sec) for a season."""
    rows = fetch_savant_csv(
        "/leaderboard/sprint_speed",
        {"year": season, "position": "", "team": "", "min": "0", "csv": "true"},
    )

    return {
        int(row["player_id"]): {"sprint_speed": parse_float(row.get("sprint_speed"))}
        for row in rows
    }


def get_plate_discipline_metrics(season):
    """Whiff%, chase%, and other swing-decision/plate-discipline rates for batters."""
    rows = fetch_savant_csv(
        "/leaderboard/custom",
        {
            "year": season,
            "type": "batter",
            "min": "1",
            "selections": "whiff_percent,oz_swing_percent,z_swing_percent,swing_percent,iz_contact_percent,oz_swing_miss_percent,z_swing_miss_percent",
            "chart": "false",
            "x": "whiff_percent",
            "y": "whiff_percent",
            "r": "no",
            "chartType": "beeswarm",
            "csv": "true",
        },
    )

    return {
        int(row["player_id"]): {
            "whiff_pct": parse_float(row.get("whiff_percent")),
            "chase_pct": parse_float(row.get("oz_swing_percent")),
            "zone_swing_pct": parse_float(row.get("z_swing_percent")),
            "swing_pct": parse_float(row.get("swing_percent")),
            "zone_contact_pct": parse_float(row.get("iz_contact_percent")),
            "chase_whiff_pct": parse_float(row.get("oz_swing_miss_percent")),
            "zone_whiff_pct": parse_float(row.get("z_swing_miss_percent")),
        }
        for row in rows
    }


def get_statcast_metrics(season):
    """
    Combines xBA, xSLG, xwOBA, xERA, barrel%, hard-hit%, avg EV, sprint
    speed, and plate-discipline rates into a single dict keyed by player_id.
    """
    metrics = {}

    sources = [
        get_batting_expected_stats,
        get_pitching_expected_stats,
        get_batted_ball_metrics,
        get_sprint_speed_metrics,
        get_plate_discipline_metrics,
    ]

    for fetch in tqdm(sources, desc="Fetching Statcast data"):
        source = fetch(season)  # call it here, inside the loop
        for player_id, values in source.items():
            metrics.setdefault(player_id, {}).update(values)

    return metrics


SKIP_ENRICHMENT = True  # set to False for full run


def attach_statcast_metrics(feed):
    """Adds a 'statcast' dict to every player in feed['gameData']['players']."""
    season = safe_get(feed, "gameData", "game", "season")
    statcast_by_player_id = get_statcast_metrics(season) if season else {}

    players = safe_get(feed, "gameData", "players", default={})

    for player_data in players.values():
        player_id = player_data.get("id")
        player_data["statcast"] = statcast_by_player_id.get(player_id, {})


def safe_get(dictionary, *keys, default=None):
    current = dictionary

    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)

    return current if current is not None else default


def get_team_logo_url(team_id):
    if not team_id:
        return ""

    return f"https://www.mlbstatic.com/team-logos/{team_id}.svg"


def format_count(balls, strikes):
    if balls is None or strikes is None:
        return ""
    return f"{balls}-{strikes}"


def format_dimension_line(label, value):
    return f"{label}: {format_distance(value)}"


def normalize_roof_type(roof_type):
    if not roof_type:
        return "Not available"

    roof = str(roof_type).strip().lower()

    if roof in ["open", "outdoor", "no roof", "none"]:
        return "No Roof"

    if "retract" in roof:
        return "Retractable Roof"

    if roof in ["dome", "fixed", "closed", "permanent", "perm. roof"]:
        return "Perm. Roof"

    return str(roof_type)


def format_game_time_et(feed):
    game_date = safe_get(feed, "gameData", "datetime", "dateTime")

    if not game_date:
        return "Not available"

    try:
        cleaned_game_date = game_date.replace("Z", "+00:00")
        utc_datetime = datetime.fromisoformat(cleaned_game_date)
        eastern_datetime = utc_datetime.astimezone(ZoneInfo("America/Toronto"))
        return eastern_datetime.strftime("%I:%M %p %Z").lstrip("0")
    except ValueError:
        return game_date


def get_game_timezone(feed, venue_details=None):
    if venue_details is None:
        venue_details = {}

    venue_timezone = safe_get(venue_details, "timeZone", "id")
    if venue_timezone:
        return venue_timezone

    venue_timezone = safe_get(venue_details, "timezone", "id")
    if venue_timezone:
        return venue_timezone

    game_timezone = safe_get(feed, "gameData", "venue", "timeZone", "id")
    if game_timezone:
        return game_timezone

    game_timezone = safe_get(feed, "gameData", "venue", "timezone", "id")
    if game_timezone:
        return game_timezone

    return "Not available"


def get_wall_asymmetry(field_info):
    left_line = safe_get(field_info, "leftLine")
    right_line = safe_get(field_info, "rightLine")
    left_center = safe_get(field_info, "leftCenter")
    right_center = safe_get(field_info, "rightCenter")

    notes = []

    try:
        if left_line is not None and right_line is not None:
            left_line_number = int(left_line)
            right_line_number = int(right_line)
            difference = abs(left_line_number - right_line_number)

            if difference == 0:
                notes.append("Left and right field lines are symmetrical")
            else:
                shorter_side = (
                    "left-field line"
                    if left_line_number < right_line_number
                    else "right-field line"
                )
                notes.append(
                    f"{shorter_side} is shorter by {format_distance_difference(difference)}"
                )

        if left_center is not None and right_center is not None:
            left_center_number = int(left_center)
            right_center_number = int(right_center)
            difference = abs(left_center_number - right_center_number)

            if difference == 0:
                notes.append("Left-centre and right-centre are symmetrical")
            else:
                deeper_gap = (
                    "left-centre"
                    if left_center_number > right_center_number
                    else "right-centre"
                )
                notes.append(
                    f"{deeper_gap} is deeper by {format_distance_difference(difference)}"
                )
    except (TypeError, ValueError):
        return "Not available"

    if not notes:
        return "Not available"

    return "; ".join(notes)


def derive_batting_orders(feed):
    """
    Derives batting order numbers from boxscore battingOrder values.
    MLB stores battingOrder like 100, 200, 300... for lineup slots.
    Substitutes can share the same slot.
    """
    boxscore_teams = safe_get(feed, "liveData", "boxscore", "teams", default={})
    orders = {}

    for side in ["home", "away"]:
        team = boxscore_teams.get(side, {})
        players = team.get("players", {})

        for player_key, player_data in players.items():
            person = player_data.get("person", {})
            player_id = person.get("id")
            batting_order = player_data.get("battingOrder")

            if player_id and batting_order:
                try:
                    lineup_slot = int(str(batting_order)[0])
                except ValueError:
                    lineup_slot = None

                if lineup_slot:
                    orders[player_id] = lineup_slot

    return orders


def slugify_team_name(team_name):
    return (
        team_name.lower()
        .replace(".", "")
        .replace(",", "")
        .replace("'", "")
        .replace(" ", "_")
    )


def get_output_filename(feed):
    game_date = safe_get(
        feed, "gameData", "datetime", "officialDate", default="unknown_date"
    )
    home_team = safe_get(feed, "gameData", "teams", "home", "name", default="home")
    away_team = safe_get(feed, "gameData", "teams", "away", "name", default="away")

    home_slug = slugify_team_name(home_team)
    away_slug = slugify_team_name(away_team)

    return f"{game_date}_{away_slug}_at_{home_slug}"


def main():
    date_or_game_pk = input("Game date (YYYY-MM-DD) or baseball game number: ").strip()
    clear_cache = input("Clear previous cache? (Y/N): ").strip().lower()

    if clear_cache == "y":
        cache_dir = Path(".cache")
        if cache_dir.exists():
            for f in cache_dir.glob("*.json"):
                f.unlink()
        print("Cache cleared.")

    games = []

    if not date_or_game_pk.isdigit():
        home = input("Home team, e.g. Orioles: ").strip()
        away = input("Away team, e.g. Blue Jays: ").strip()
        games = get_schedule(date_or_game_pk, home_team_name=home, away_team_name=away)

    if date_or_game_pk.isdigit():
        game_pk = int(date_or_game_pk)
    else:
        if not games:
            print("")
            print("No matching games found.")
            print("Try using full team names, for example:")
            print("  Home: Baltimore Orioles")
            print("  Away: Toronto Blue Jays")
            return

        if len(games) > 1:
            print("")
            print("Multiple matching games found:")
            for index, game in enumerate(games, start=1):
                print(
                    f"{index}. gamePk {game['gamePk']}: {game['away']} at {game['home']}, {game['venue']}"
                )

            selected = input("Select game number: ").strip()

            try:
                selected_index = int(selected) - 1
                game = games[selected_index]
            except (ValueError, IndexError):
                print("Invalid selection.")
                return
        else:
            game = games[0]

        print("")
        print(f"Found gamePk: {game['gamePk']}")
        print(f"{game['away']} at {game['home']}, {game['venue']}")
        print("")

        game_pk = game["gamePk"]

    feed = get_game_feed(game_pk)

    venue_id = safe_get(feed, "gameData", "venue", "id")
    venue_details = {}

    if venue_id:
        venue_details = get_venue_details(venue_id)

    attach_statcast_metrics(feed)

    if not SKIP_ENRICHMENT:
        attach_statcast_metrics(feed)
        print("Running enrichment blocks A, B, C, F, I...")
        counter = run_enrichment(feed)
        counter.print_summary()

    OUTPUT_DIR.mkdir(exist_ok=True)

    base_name = get_output_filename(feed)
    json_output_file = OUTPUT_DIR / f"{base_name}.json"
    with open(json_output_file, "w", encoding="utf-8") as file:
        json.dump(feed, file, indent=2)
    print(f"Full game JSON written to {json_output_file}")

    context = build_game_context(feed, venue_details)
    context["scorecard"] = player_scorecard(feed)
    html_file = OUTPUT_DIR / f"{base_name}.html"
    write_jinja_html_report(context, html_file)
    webbrowser.open(html_file.resolve().as_uri())
    print(f"Scorecard opened in browser: {html_file}")


if __name__ == "__main__":
    main()
