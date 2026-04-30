import requests

TEAM_ID = 141  # Toronto Blue Jays
START_DATE = "2026-04-01"
END_DATE = "2026-10-31"


def get_last_completed_game():
    url = "https://statsapi.mlb.com/api/v1/schedule"

    params = {
        "sportId": 1,
        "teamId": TEAM_ID,
        "startDate": START_DATE,
        "endDate": END_DATE,
        "gameType": "R",
    }

    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()

    data = response.json()

    completed_games = []

    for date_block in data["dates"]:
        for game in date_block["games"]:
            if game["status"]["codedGameState"] == "F":
                completed_games.append(game)

    if not completed_games:
        raise RuntimeError("No completed games found.")

    return completed_games[-1]


def get_starting_pitcher_from_boxscore(game):
    game_pk = game["gamePk"]

    home_team = game["teams"]["home"]["team"]
    away_team = game["teams"]["away"]["team"]

    blue_jays_are_home = home_team["id"] == TEAM_ID
    blue_jays_side = "home" if blue_jays_are_home else "away"
    opponent = away_team["name"] if blue_jays_are_home else home_team["name"]

    url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"

    response = requests.get(url, timeout=20)
    response.raise_for_status()

    boxscore = response.json()
    players = boxscore["teams"][blue_jays_side]["players"]

    for player in players.values():
        pitching_stats = player.get("stats", {}).get("pitching")

        if pitching_stats and pitching_stats.get("gamesStarted") == 1:
            return {
                "date": game["gameDate"][:10],
                "pitcher": player["person"]["fullName"],
                "opponent": opponent,
                "ip": pitching_stats.get("inningsPitched"),
            }

    raise RuntimeError("Could not find starting pitcher.")


def test():
    game = get_last_completed_game()
    starter = get_starting_pitcher_from_boxscore(game)

    print("Date:", starter["date"])
    print("Pitcher:", starter["pitcher"])
    print("Opp:", starter["opponent"])
    print("IP:", starter["ip"])


if __name__ == "__main__":
    test()