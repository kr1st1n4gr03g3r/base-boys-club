import webbrowser
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from unit_formatters import (
    format_distance,
    format_distance_difference,
    format_speed,
    format_temperature,
    format_wind_speed,
)

MLB_API_BASE = "https://statsapi.mlb.com/api"

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


def get_result_type_shorthand(event, description):
    event_lower = event.lower() if event else ""
    description_lower = description.lower() if description else ""

    if "triple play" in event_lower or "triple play" in description_lower:
        return "TP"

    if "double play" in event_lower or "double play" in description_lower:
        return "DP"

    if "home run" in event_lower:
        return "HR"

    if "strikeout" in event_lower:
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

    return ""


def get_batter_result_shorthand(play):
    event = safe_get(play, "result", "event", default="")
    description = safe_get(play, "result", "description", default="")

    result_code = get_result_type_shorthand(event, description)

    if not result_code:
        return ""

    if result_code in ["K", "ꓘ", "HR", "DP", "TP"]:
        return result_code

    position_number = get_position_from_description(description)

    if position_number:
        return f"{result_code}-{position_number}"

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


def get_game_weather(feed):
    """
    Pulls weather from the MLB game feed when available.
    Outputs:
      Weather: Sunny, 21°C / 69°F, Wind: 10 kph / 6 mph, R To L
    """
    weather = safe_get(feed, "gameData", "weather", default={})

    if not weather:
        return "Weather: Not available in MLB feed"

    condition = weather.get("condition")
    temp = weather.get("temp")
    wind = weather.get("wind")

    parts = []

    if condition:
        parts.append(condition)

    if temp:
        parts.append(format_temperature(temp))

    formatted_wind = parse_wind(wind)

    if formatted_wind:
        parts.append(f"Wind: {formatted_wind}")

    if not parts:
        return "Weather: Not available in MLB feed"

    return "Weather: " + ", ".join(parts)


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


def get_final_score_lines(feed):
    game_state = safe_get(feed, "gameData", "status", "abstractGameState", default="")
    detailed_state = safe_get(feed, "gameData", "status", "detailedState", default="")

    home_team = safe_get(feed, "gameData", "teams", "home", "name", default="Home")
    away_team = safe_get(feed, "gameData", "teams", "away", "name", default="Away")

    home_runs = safe_get(feed, "liveData", "linescore", "teams", "home", "runs")
    away_runs = safe_get(feed, "liveData", "linescore", "teams", "away", "runs")

    if home_runs is None or away_runs is None:
        return ["**Final Score**: Not available"]

    if game_state != "Final" and "Final" not in detailed_state:
        return [f"**Score**: {away_team} {away_runs}, {home_team} {home_runs}"]

    return [f"**Final Score**: {away_team} {away_runs}, {home_team} {home_runs}"]


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


def get_lineup(feed, side):
    """
    side should be "home" or "away".

    Returns lineup grouped by batting order slot.
    Pinch hitters and substitutes stay inside the original lineup slot.
    """
    team = safe_get(feed, "liveData", "boxscore", "teams", side, default={})
    players = team.get("players", {})
    game_players = safe_get(feed, "gameData", "players", default={})

    lineup = {}

    for player_key, player_data in players.items():
        batting_order = player_data.get("battingOrder")

        if not batting_order:
            continue

        batting_order_text = str(batting_order)

        try:
            slot = int(batting_order_text[0])
            order_within_slot = int(batting_order_text)
        except ValueError:
            continue

        person = player_data.get("person", {})
        position = player_data.get("position", {})
        player_id = person.get("id")

        game_player_key = f"ID{player_id}" if player_id else None
        game_player_data = game_players.get(game_player_key, {})

        jersey_number = player_data.get("jerseyNumber")
        if not jersey_number:
            jersey_number = game_player_data.get("primaryNumber")

        player = {
            "id": player_id,
            "name": person.get("fullName", "Unknown player"),
            "position": position.get("abbreviation") or "Unknown",
            "jersey_number": jersey_number or "",
            "bats": safe_get(game_player_data, "batSide", "code", default="?"),
            "throws": safe_get(game_player_data, "pitchHand", "code", default="?"),
            "batting_order": batting_order_text,
            "order_within_slot": order_within_slot,
        }

        if slot not in lineup:
            lineup[slot] = []

        lineup[slot].append(player)

    for slot in lineup:
        lineup[slot].sort(key=lambda player: player["order_within_slot"])

    return lineup


def get_lineup_table_lines(feed, side, team_label):
    lineup = get_lineup(feed, side)

    lines = []

    lines.append("  <table>")
    lines.append(f'    <tr><th colspan="6">{team_label} Lineup</th></tr>')
    lines.append("    <tr>")
    lines.append("      <th>#</th>")
    lines.append("      <th>Batter</th>")
    lines.append("      <th>Player Number</th>")
    lines.append("      <th>Bats</th>")
    lines.append("      <th>Throws</th>")
    lines.append("      <th>Pos.</th>")
    lines.append("    </tr>")

    for slot in sorted(lineup):
        players = lineup[slot]

        for index, player in enumerate(players):
            name = player.get("name", "Unknown player")
            position = player.get("position") or "Unknown"
            jersey_number = player.get("jersey_number")
            bats = player.get("bats") or "?"
            throws = player.get("throws") or "?"

            if index == 0:
                lineup_label = f"{slot}."
            else:
                lineup_label = "PH/SUB"

            if jersey_number:
                jersey_text = f"#{jersey_number}"
            else:
                jersey_text = ""

            lines.append(
                "    <tr>"
                f"<td>{lineup_label}</td>"
                f"<td><strong>{name}</strong></td>"
                f"<td>{jersey_text}</td>"
                f"<td>{bats}</td>"
                f"<td>{throws}</td>"
                f"<td>{position}</td>"
                "</tr>"
            )

    lines.append("  </table>")

    return lines


def get_pitching_lines(feed, side):
    """
    side should be "home" or "away".

    Returns HTML table rows for pitchers used in the game.
    Uses game totals from liveData.boxscore.
    """
    team = safe_get(feed, "liveData", "boxscore", "teams", side, default={})
    players = team.get("players", {})
    pitcher_ids = team.get("pitchers", [])
    game_players = safe_get(feed, "gameData", "players", default={})

    lines = []

    for pitcher_id in pitcher_ids:
        player_key = f"ID{pitcher_id}"
        player_data = players.get(player_key, {})
        game_player_data = game_players.get(player_key, {})

        person = player_data.get("person", {})
        stats = safe_get(player_data, "stats", "pitching", default={})

        name = person.get("fullName", "Unknown pitcher")
        throws = safe_get(game_player_data, "pitchHand", "code", default="?")

        innings_pitched = stats.get("inningsPitched", "")
        hits = stats.get("hits", "")
        runs = stats.get("runs", "")
        earned_runs = stats.get("earnedRuns", "")
        walks = stats.get("baseOnBalls", "")
        strikeouts = stats.get("strikeOuts", "")
        home_runs = stats.get("homeRuns", "")
        era = stats.get("era")

        if era is None:
            era = safe_get(player_data, "seasonStats", "pitching", "era", default="")

        lines.append(
            "      <tr>"
            f"<td><strong>{name}</strong></td>"
            f"<td>{throws}</td>"
            f"<td>{innings_pitched}</td>"
            f"<td>{hits}</td>"
            f"<td>{runs}</td>"
            f"<td>{earned_runs}</td>"
            f"<td>{walks}</td>"
            f"<td>{strikeouts}</td>"
            f"<td>{home_runs}</td>"
            f"<td>{era}</td>"
            "</tr>"
        )

    totals = safe_get(team, "teamStats", "pitching", default={})

    lines.append(
        "      <tr>"
        "<td><strong>Totals</strong></td>"
        "<td></td>"
        f"<td><strong>{totals.get('inningsPitched', '')}</strong></td>"
        f"<td><strong>{totals.get('hits', '')}</strong></td>"
        f"<td><strong>{totals.get('runs', '')}</strong></td>"
        f"<td><strong>{totals.get('earnedRuns', '')}</strong></td>"
        f"<td><strong>{totals.get('baseOnBalls', '')}</strong></td>"
        f"<td><strong>{totals.get('strikeOuts', '')}</strong></td>"
        f"<td><strong>{totals.get('homeRuns', '')}</strong></td>"
        "<td></td>"
        "</tr>"
    )

    return lines


def get_pitching_table_lines(feed, side, team_abbreviation):
    pitching_lines = get_pitching_lines(feed, side)

    lines = []

    lines.append("  <table>")
    lines.append(f'    <tr><th colspan="10">Pitchers - {team_abbreviation}</th></tr>')
    lines.append("    <tr>")
    lines.append("      <th>Pitcher</th>")
    lines.append("      <th>Throws</th>")
    lines.append("      <th>IP</th>")
    lines.append("      <th>H</th>")
    lines.append("      <th>R</th>")
    lines.append("      <th>ER</th>")
    lines.append("      <th>BB</th>")
    lines.append("      <th>K</th>")
    lines.append("      <th>HR</th>")
    lines.append("      <th>ERA</th>")
    lines.append("    </tr>")
    lines.extend(pitching_lines)
    lines.append("  </table>")

    return lines


def get_pitching_decision_lines(feed):
    decisions = safe_get(feed, "liveData", "decisions", default={})

    winner = safe_get(decisions, "winner", "fullName")
    loser = safe_get(decisions, "loser", "fullName")
    save = safe_get(decisions, "save", "fullName")

    parts = []

    if winner:
        parts.append(f"W: {winner}")

    if loser:
        parts.append(f"L: {loser}")

    if save:
        parts.append(f"S: {save}")

    if not parts:
        return ["**Decisions**: Not available"]

    return [f"**Decisions**: {' | '.join(parts)}"]


def get_ballpark_lines(feed, venue_details=None):
    if venue_details is None:
        venue_details = {}

    venue_name = safe_get(venue_details, "name")
    if not venue_name:
        venue_name = safe_get(feed, "gameData", "venue", "name", default="Unknown park")

    field_info = venue_details.get("fieldInfo", {})

    roof_type = normalize_roof_type(field_info.get("roofType"))
    turf_type = field_info.get("turfType", "Not available")

    left_line = field_info.get("leftLine")
    left = field_info.get("left")
    left_center = field_info.get("leftCenter")
    center = field_info.get("center")
    right_center = field_info.get("rightCenter")
    right = field_info.get("right")
    right_line = field_info.get("rightLine")

    lines = []

    lines.append("<details>")
    lines.append("<summary>Park Info</summary>")
    lines.append("")
    lines.append(f"Park: {venue_name}")
    lines.append(
        "Outfield dimensions: "
        f"LF Line {format_distance(left_line)}, "
        f"LF {format_distance(left)}, "
        f"LC {format_distance(left_center)}, "
        f"CF {format_distance(center)}, "
        f"RC {format_distance(right_center)}, "
        f"RF {format_distance(right)}, "
        f"RF Line {format_distance(right_line)}"
    )
    lines.append(format_dimension_line("Left Fence", left_line))
    lines.append(format_dimension_line("Center Fence", center))
    lines.append(format_dimension_line("Right Fence", right_line))
    lines.append(f"Wall Asymmetry: {get_wall_asymmetry(field_info)}")
    lines.append(f"Roof Type: {roof_type}")
    lines.append(f"Turf Type: {turf_type}")
    lines.append("")
    lines.append(format_dimension_line("leftLine", left_line))
    lines.append(format_dimension_line("left", left))
    lines.append(format_dimension_line("leftCenter", left_center))
    lines.append(format_dimension_line("center", center))
    lines.append(format_dimension_line("rightCenter", right_center))
    lines.append(format_dimension_line("right", right))
    lines.append(format_dimension_line("rightLine", right_line))

    lines.append("")
    lines.append("</details>")

    return lines


def get_pitch_line(event):
    """
    Converts one pitch event into a readable line.
    """
    pitch_number = event.get("pitchNumber")
    description = safe_get(event, "details", "description", default="Unknown")
    pitch_type = safe_get(
        event, "details", "type", "description", default="Unknown pitch"
    )
    speed = safe_get(event, "pitchData", "startSpeed")

    balls = safe_get(event, "count", "balls")
    strikes = safe_get(event, "count", "strikes")
    count = format_count(balls, strikes)

    speed_text = format_speed(speed)

    pieces = []

    if pitch_number is not None:
        pieces.append(f"{pitch_number}.")

    if count:
        pieces.append(f"{count}:")

    pieces.append(description)

    if speed_text:
        pieces.append(speed_text)

    if pitch_type:
        pieces.append(pitch_type)

    return " ".join(pieces)


def get_batted_ball_data(play):
    """
    Batted-ball data usually appears on the pitch event where the ball was put in play.
    """
    for event in play.get("playEvents", []):
        if not event.get("isPitch"):
            continue

        hit_data = event.get("hitData")
        if not hit_data:
            continue

        return {
            "exit_velocity": hit_data.get("launchSpeed"),
            "distance": hit_data.get("totalDistance"),
            "launch_angle": hit_data.get("launchAngle"),
            "trajectory": hit_data.get("trajectory"),
        }

    return None


def get_extra_result_lines(play):
    runners = play.get("runners", [])
    lines = []

    batter_id = safe_get(play, "matchup", "batter", "id")

    for runner in runners:
        runner_id = safe_get(runner, "details", "runner", "id")
        runner_name = safe_get(
            runner, "details", "runner", "fullName", default="Unknown runner"
        )
        event = safe_get(runner, "details", "event", default="")
        start = safe_get(runner, "movement", "start", default="")
        end = safe_get(runner, "movement", "end", default="")
        is_out = safe_get(runner, "movement", "isOut", default=False)
        rbi = safe_get(runner, "details", "rbi", default=False)

        # Skip the batter's own ordinary result as it is redundant as an "extra" result.
        if runner_id == batter_id and event in [
            "Single",
            "Double",
            "Triple",
            "Home Run",
        ]:
            continue

        if not event and not start and not end:
            continue

        movement_parts = []

        if start:
            movement_parts.append(f"from {start}")

        if end:
            movement_parts.append(f"to {end}")

        if is_out:
            movement_parts.append("out")

        if rbi:
            movement_parts.append("RBI")

        movement_text = ", ".join(movement_parts)

        if movement_text:
            lines.append(f"Extra result: {runner_name} - {event} ({movement_text})")
        else:
            lines.append(f"Extra result: {runner_name} - {event}")

    return lines


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


def generate_pitch_by_pitch_report(feed, venue_details=None):
    game_data = feed.get("gameData", {})
    live_data = feed.get("liveData", {})

    game_date = safe_get(game_data, "datetime", "officialDate", default="Unknown date")
    game_time_et = format_game_time_et(feed)
    game_timezone = get_game_timezone(feed, venue_details)

    home_team = safe_get(
        game_data, "teams", "home", "name", default="Unknown home team"
    )
    away_team = safe_get(
        game_data, "teams", "away", "name", default="Unknown away team"
    )

    home_team_id = safe_get(game_data, "teams", "home", "id")
    away_team_id = safe_get(game_data, "teams", "away", "id")
    home_logo_url = get_team_logo_url(home_team_id)
    away_logo_url = get_team_logo_url(away_team_id)

    batting_orders = derive_batting_orders(feed)

    lines = []
    lines.append(
        f"# {away_team} @ {home_team} <br><br>"
        f'<img src="{away_logo_url}" alt="{away_team} logo" width="100">'
        f'<img src="{home_logo_url}" alt="{home_team} logo" width="100">'
    )

    lines.append(f"**Game Date**: {game_date}")
    lines.append(f"**Time (ET)**: {game_time_et}")
    lines.append(f"**Timezone**: {game_timezone}")

    lines.append(get_game_weather(feed))
    lines.extend(get_final_score_lines(feed))
    lines.extend(get_ballpark_lines(feed, venue_details))

    lines.append("")
    lines.append("## Lineups")
    lines.append("")
    lines.append("<table>")
    lines.append("  <tr>")
    lines.append('    <td style="vertical-align: top;">')
    lines.extend(get_lineup_table_lines(feed, "away", "Away"))
    lines.append("    </td>")
    lines.append('    <td style="vertical-align: top;">')
    lines.extend(get_lineup_table_lines(feed, "home", "Home"))
    lines.append("    </td>")
    lines.append("  </tr>")
    lines.append("</table>")
    lines.append("")

    away_abbreviation = safe_get(
        game_data, "teams", "away", "abbreviation", default="AWAY"
    )
    home_abbreviation = safe_get(
        game_data, "teams", "home", "abbreviation", default="HOME"
    )

    lines.append("")
    lines.append("## Pitching")
    lines.append("")
    lines.append("<table>")
    lines.append("  <tr>")
    lines.append('    <td style="vertical-align: top;">')
    lines.extend(get_pitching_table_lines(feed, "away", away_abbreviation))
    lines.append("    </td>")
    lines.append('    <td style="vertical-align: top;">')
    lines.extend(get_pitching_table_lines(feed, "home", home_abbreviation))
    lines.append("    </td>")
    lines.append("  </tr>")
    lines.append("</table>")
    lines.append("")
    lines.extend(get_pitching_decision_lines(feed))
    lines.append("")

    current_half = None

    all_plays = safe_get(live_data, "plays", "allPlays", default=[])

    for play in all_plays:
        inning = safe_get(play, "about", "inning")
        half = safe_get(play, "about", "halfInning")

        if inning is None or half is None:
            continue

        half_label = f"{half.title()} {inning}"

        if half_label != current_half:
            current_half = half_label
            lines.append("")
            lines.append(f"=== {half_label} ===")
            lines.append("")

        batter = safe_get(
            play, "matchup", "batter", "fullName", default="Unknown batter"
        )
        batter_id = safe_get(play, "matchup", "batter", "id")
        pitcher = safe_get(
            play, "matchup", "pitcher", "fullName", default="Unknown pitcher"
        )

        lineup_number = batting_orders.get(batter_id)
        lineup_text = f"#{lineup_number}" if lineup_number else "Unknown lineup slot"

        event = safe_get(play, "result", "event", default="Unknown result")
        description = safe_get(play, "result", "description", default="")

        result_shorthand = get_batter_result_shorthand(play)

        final_balls = safe_get(play, "count", "balls")
        final_strikes = safe_get(play, "count", "strikes")
        final_count = format_count(final_balls, final_strikes)

        lines.append(f"**Batter: {batter} ({lineup_text})**")
        lines.append(f"Pitcher: {pitcher}")

        if result_shorthand:
            lines.append(f"Batter result: {event} - {result_shorthand}")
        else:
            lines.append(f"Batter result: {event}")

        if description:
            lines.append(f"Description: {description}")

        extra_result_lines = get_extra_result_lines(play)

        for extra_line in extra_result_lines:
            lines.append(extra_line)

        if final_count:
            lines.append(f"Final count: {final_count}")

        batted_ball = get_batted_ball_data(play)

        if batted_ball:
            ev = batted_ball.get("exit_velocity")
            distance = batted_ball.get("distance")
            angle = batted_ball.get("launch_angle")
            trajectory = batted_ball.get("trajectory")

            if ev is not None:
                lines.append(f"<sub>Exit velocity: {format_speed(ev)}</sub>")

            if distance is not None:
                lines.append(f"<sub>Distance: {format_distance(distance)}</sub>")

            if angle is not None:
                lines.append(f"<sub>Launch angle: {angle}°</sub>")

            if trajectory:
                lines.append(f"<sub>Trajectory: {trajectory}</sub>")

        lines.append("Pitches:")

        for event_item in play.get("playEvents", []):
            if not event_item.get("isPitch"):
                continue

            lines.append(f"  {get_pitch_line(event_item)}")

        lines.append("")

    return "\n".join(lines)


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

    return f"{game_date}_{away_slug}_at_{home_slug}.md"


def main():
    game_date = input("Game date (YYYY-MM-DD): ").strip()
    home = input("Home team, e.g. Orioles: ").strip()
    away = input("Away team, e.g. Blue Jays: ").strip()

    games = get_schedule(game_date, home_team_name=home, away_team_name=away)

    print("")
    print("Would you like:")
    print("a) 📁 A markdown file saved to the /output folder?")
    print("b) 🌎 The browser to open the report?")
    print("c) 🎉 Both, please")
    output_choice = input("Choose a, b, or c: ").strip().lower()

    if output_choice not in ["a", "b", "c"]:
        print("Invalid selection. Please choose a, b, or c.")

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

    feed = get_game_feed(game["gamePk"])

    venue_id = safe_get(feed, "gameData", "venue", "id")
    venue_details = {}

    if venue_id:
        venue_details = get_venue_details(venue_id)

    report = generate_pitch_by_pitch_report(feed, venue_details)

    OUTPUT_DIR.mkdir(exist_ok=True)

    output_file = OUTPUT_DIR / get_output_filename(feed)

    with open(output_file, "w", encoding="utf-8") as file:
        file.write(report)

    if output_choice in ["a", "c"]:
        print(f"Report written to {output_file}")

    if output_choice in ["b", "c"]:
        webbrowser.open(output_file.resolve().as_uri())
        print(f"Report opened in browser: {output_file}")


if __name__ == "__main__":
    main()
