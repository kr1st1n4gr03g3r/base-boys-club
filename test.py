from datetime import date

import requests

TEAM_ID = 141  # Toronto Blue Jays
START_DATE = f"{date.today().year}-04-01"
END_DATE = date.today().isoformat()
DASH = "—"


def mlb(path: str, **params):
    response = requests.get(
        f"https://statsapi.mlb.com/api/v1{path}",
        params=params,
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def get_last_completed_game():
    data = mlb(
        "/schedule",
        sportId=1,
        teamId=TEAM_ID,
        startDate=START_DATE,
        endDate=END_DATE,
        gameType="R",
    )

    completed_games = []
    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            if game.get("status", {}).get("codedGameState") == "F":
                completed_games.append(game)

    if not completed_games:
        raise RuntimeError("No completed Blue Jays games found.")

    return completed_games[-1]


def get_game_context(game: dict) -> dict:
    home_team = game["teams"]["home"]["team"]
    away_team = game["teams"]["away"]["team"]
    blue_jays_are_home = home_team["id"] == TEAM_ID

    return {
        "game_pk": game["gamePk"],
        "date": game["gameDate"][:10],
        "blue_jays_side": "home" if blue_jays_are_home else "away",
        "opponent_side": "away" if blue_jays_are_home else "home",
        "opponent": away_team["name"] if blue_jays_are_home else home_team["name"],
    }


def get_player_info(person_id: int) -> dict:
    return mlb(f"/people/{person_id}")["people"][0]


def abbreviate_bat_side(person_id: int) -> str:
    try:
        description = get_player_info(person_id).get("batSide", {}).get("description", "")
    except Exception:
        return DASH

    desc = description.lower()
    if "switch" in desc:
        return "S"
    if "right" in desc:
        return "R"
    if "left" in desc:
        return "L"
    return description or DASH


def abbreviate_pitch_hand(person_id: int | None) -> str:
    if not person_id:
        return DASH

    try:
        description = get_player_info(person_id).get("pitchHand", {}).get("description", "")
    except Exception:
        return DASH

    desc = description.lower()
    if "right" in desc:
        return "RHP"
    if "left" in desc:
        return "LHP"
    return description or DASH


def get_starting_pitcher_id(team_boxscore: dict) -> int | None:
    for player in team_boxscore.get("players", {}).values():
        pitching_stats = player.get("stats", {}).get("pitching", {})
        if pitching_stats.get("gamesStarted") == 1:
            return player.get("person", {}).get("id")
    return None


def safe_div(num: int | float, den: int | float, places: int = 3) -> str:
    return f"{num / den:.{places}f}" if den else DASH


def pct(num: int | float, den: int | float) -> str:
    return f"{100 * num / den:.1f}%" if den else DASH


def total_bases(batting: dict) -> int:
    hits = int(batting.get("hits", 0))
    doubles = int(batting.get("doubles", 0))
    triples = int(batting.get("triples", 0))
    homers = int(batting.get("homeRuns", 0))
    singles = hits - doubles - triples - homers
    return singles + (2 * doubles) + (3 * triples) + (4 * homers)


def build_hitter_row(
    date_str: str,
    opponent: str,
    pitcher_hand: str,
    lineup_spot: int,
    player: dict,
) -> dict:
    batting = player.get("stats", {}).get("batting", {})
    person = player.get("person", {})

    ab = int(batting.get("atBats", 0))
    pa = int(batting.get("plateAppearances", 0))
    hits = int(batting.get("hits", 0))
    walks = int(batting.get("baseOnBalls", 0))
    hbp = int(batting.get("hitByPitch", 0))
    sac_flies = int(batting.get("sacFlies", 0))
    strikeouts = int(batting.get("strikeOuts", 0))

    obp_den = ab + walks + hbp + sac_flies

    return {
        "Date": date_str,
        "Player": person.get("fullName", DASH),
        "Opp": opponent,
        "Bat Side": abbreviate_bat_side(person["id"]),
        "Pitcher Hand": pitcher_hand,
        "Lineup Spot": lineup_spot,
        "PA": pa,
        "H": hits,
        "HR": batting.get("homeRuns", 0),
        "BB": walks,
        "K": strikeouts,
        "AVG": safe_div(hits, ab),
        "OBP": safe_div(hits + walks + hbp, obp_den),
        "SLG": safe_div(total_bases(batting), ab),
        "K%": pct(strikeouts, pa),
        "BB%": pct(walks, pa),
    }


def get_top_five_hitter_rows(game: dict) -> list[dict]:
    context = get_game_context(game)
    boxscore = mlb(f"/game/{context['game_pk']}/boxscore")

    blue_jays_box = boxscore["teams"][context["blue_jays_side"]]
    opponent_box = boxscore["teams"][context["opponent_side"]]
    opponent_starter_id = get_starting_pitcher_id(opponent_box)
    pitcher_hand = abbreviate_pitch_hand(opponent_starter_id)

    batting_order = blue_jays_box.get("battingOrder", [])
    if not batting_order:
        raise RuntimeError("Could not find Blue Jays batting order in boxscore.")

    rows = []
    for lineup_spot, player_id in enumerate(batting_order[:5], start=1):
        player_key = f"ID{player_id}"
        player = blue_jays_box["players"][player_key]
        rows.append(
            build_hitter_row(
                context["date"],
                context["opponent"],
                pitcher_hand,
                lineup_spot,
                player,
            )
        )

    return rows


def print_rows(rows: list[dict]) -> None:
    columns = [
        "Date", "Player", "Opp", "Bat Side", "Pitcher Hand", "Lineup Spot",
        "PA", "H", "HR", "BB", "K", "AVG", "OBP", "SLG", "K%", "BB%",
    ]
    print(" | ".join(columns))
    print(" | ".join(["---"] * len(columns)))
    for row in rows:
        print(" | ".join(str(row.get(column, DASH)) for column in columns))


def test():
    game = get_last_completed_game()
    rows = get_top_five_hitter_rows(game)

    print("Blue Jays top 1-5 hitter smoke test")
    print_rows(rows)


if __name__ == "__main__":
    test()
