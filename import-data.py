import requests
from datetime import datetime


MLB_API_BASE = "https://statsapi.mlb.com/api"


def get_schedule(date, home_team_name=None, away_team_name=None):
    """
    date format: YYYY-MM-DD
    Example:
        get_schedule("2025-06-01", home_team_name="Orioles", away_team_name="Blue Jays")
    """
    url = f"{MLB_API_BASE}/v1/schedule"
    params = {
        "sportId": 1,
        "date": date,
        "hydrate": "team,venue"
    }

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

            games.append({
                "gamePk": game["gamePk"],
                "gameDate": game.get("gameDate"),
                "home": home,
                "away": away,
                "venue": venue,
                "status": game.get("status", {}).get("detailedState")
            })

    return games


def get_game_feed(game_pk):
    url = f"{MLB_API_BASE}/v1.1/game/{game_pk}/feed/live"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


def safe_get(dictionary, *keys, default=None):
    current = dictionary

    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)

    return current if current is not None else default


def format_speed(value):
    if value is None:
        return ""
    return f"{value:.1f} mph"


def format_count(balls, strikes):
    if balls is None or strikes is None:
        return ""
    return f"{balls}-{strikes}"


def get_pitch_line(event):
    """
    Converts one pitch event into a readable line.
    """
    pitch_number = event.get("pitchNumber")
    description = safe_get(event, "details", "description", default="Unknown")
    pitch_type = safe_get(event, "details", "type", "description", default="Unknown pitch")
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
            "trajectory": hit_data.get("trajectory")
        }

    return None


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


def generate_pitch_by_pitch_report(feed):
    game_data = feed.get("gameData", {})
    live_data = feed.get("liveData", {})

    game_date = safe_get(game_data, "datetime", "officialDate", default="Unknown date")
    home_team = safe_get(game_data, "teams", "home", "name", default="Unknown home team")
    away_team = safe_get(game_data, "teams", "away", "name", default="Unknown away team")
    venue = safe_get(game_data, "venue", "name", default="Unknown park")

    batting_orders = derive_batting_orders(feed)

    lines = []
    lines.append(f"Game Date: {game_date}")
    lines.append(f"Home: {home_team}")
    lines.append(f"Away: {away_team}")
    lines.append(f"Park: {venue}")
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

        batter = safe_get(play, "matchup", "batter", "fullName", default="Unknown batter")
        batter_id = safe_get(play, "matchup", "batter", "id")
        pitcher = safe_get(play, "matchup", "pitcher", "fullName", default="Unknown pitcher")

        lineup_number = batting_orders.get(batter_id)
        lineup_text = f"#{lineup_number}" if lineup_number else "Unknown lineup slot"

        event = safe_get(play, "result", "event", default="Unknown result")
        description = safe_get(play, "result", "description", default="")

        final_balls = safe_get(play, "count", "balls")
        final_strikes = safe_get(play, "count", "strikes")
        final_count = format_count(final_balls, final_strikes)

        lines.append(f"Batter: {batter} ({lineup_text})")
        lines.append(f"Pitcher: {pitcher}")
        lines.append(f"Batter result: {event}")

        if description:
            lines.append(f"Description: {description}")

        if final_count:
            lines.append(f"Final count: {final_count}")

        batted_ball = get_batted_ball_data(play)

        if batted_ball:
            ev = batted_ball.get("exit_velocity")
            distance = batted_ball.get("distance")
            angle = batted_ball.get("launch_angle")
            trajectory = batted_ball.get("trajectory")

            if ev is not None:
                lines.append(f"Exit velocity: {ev:.1f} mph")

            if distance is not None:
                lines.append(f"Distance: {distance} ft")

            if angle is not None:
                lines.append(f"Launch angle: {angle}°")

            if trajectory:
                lines.append(f"Trajectory: {trajectory}")

        lines.append("Pitches:")

        for event_item in play.get("playEvents", []):
            if not event_item.get("isPitch"):
                continue

            lines.append(f"  {get_pitch_line(event_item)}")

        lines.append("")

    return "\n".join(lines)


def main():
    game_date = input("Game date (YYYY-MM-DD): ").strip()
    home = input("Home team, e.g. Orioles: ").strip()
    away = input("Away team, e.g. Blue Jays: ").strip()

    games = get_schedule(game_date, home_team_name=home, away_team_name=away)

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
            print(f"{index}. gamePk {game['gamePk']}: {game['away']} at {game['home']}, {game['venue']}")

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
    report = generate_pitch_by_pitch_report(feed)

    output_file = f"pitch_by_pitch_{game['gamePk']}.txt"

    with open(output_file, "w", encoding="utf-8") as file:
        file.write(report)

    print(f"Report written to {output_file}")


if __name__ == "__main__":
    main()