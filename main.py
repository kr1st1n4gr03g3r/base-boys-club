from pybaseball import statcast, playerid_reverse_lookup
import pandas as pd

data = statcast(start_dt="2026-04-01", end_dt="2026-04-24")

# Create batting_team column
data["batting_team"] = data.apply(
    lambda row: row["away_team"] if row["inning_topbot"] == "Top" else row["home_team"],
    axis=1
)

# Only Blue Jays hitters
jays_hitters = data[data["batting_team"] == "TOR"].copy()

# Look up batter names from MLBAM IDs
batter_ids = jays_hitters["batter"].dropna().astype(int).unique()
names = playerid_reverse_lookup(batter_ids, key_type="mlbam")

names["batter_name"] = names["name_first"] + " " + names["name_last"]

jays_hitters = jays_hitters.merge(
    names[["key_mlbam", "batter_name"]],
    left_on="batter",
    right_on="key_mlbam",
    how="left"
)

print(jays_hitters[[
    "batter_name",
    "game_date",
    "home_team",
    "away_team",
    "batting_team",
    "events",
    "description",
    "launch_speed",
    "launch_angle",
    "estimated_ba_using_speedangle",
    "estimated_woba_using_speedangle"
]].head(20))