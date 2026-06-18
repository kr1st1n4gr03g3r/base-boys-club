# ⚾ Base Boys Club

A Python project that pulls completed MLB game data from various MLB Stats APIs and generates various baseball game dashboards for analysis.

## What This Project Does

`base-boys-club` pulls completed game data from three public APIs and stores it as JSON. That JSON is then used to power dashboards and analysis tools.

- **MLB Stats API** (`statsapi.mlb.com`) — game feed, lineups, pitching, play-by-play
- **Baseball Savant / Statcast** (`baseballsavant.mlb.com`) — pitch-level Statcast data and season metrics (exit velocity, launch angle, spin rate, plate location, and more)
- **MLB Static** (`mlbstatic.com`) — team logos

**First iteration:** a per-game player scorecard dashboard.

**Second feature:** additional analysis and views built from the same JSON source — one data pull, multiple uses.

The data currently includes:

- Game date and time
- Teams and final score
- Weather and ballpark information
- Team logos
- Lineups and batting order
- Statcast metrics: xBA, xSLG, xwOBA, xERA, barrel%, hard-hit%, avg exit velocity, sprint speed
- Plate discipline: whiff%, chase%, zone swing%, zone contact%, and more
- Platoon splits (vs RHP / vs LHP) for hitters and pitchers
- Recent form (last 15 and last 30 days)
- Batter-vs-pitcher history (career and current season)
- Pitcher workload: last 5 starts, pitch counts, days rest, IL context
- Bullpen usage: recent appearances, pitches per day, back-to-back flags

## Project Structure

```text
base-boys-club/
├── import_data.py      # main entry point
├── enrichment.py       # per-game enrichment (MLB Stats API blocks)
├── html_report.py      # Jinja2 HTML scorecard renderer
├── unit_formatters.py
├── templates/
│   └── scorecard.html
├── styles/
│   └── report.css
├── output/             # generated .json and .html files
├── .cache/             # disk cache for API responses (gitignored)
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

## Setup

Create a virtual environment:

```bash
python3 -m venv .venv
```

Activate it:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Optional, install development tools:

```bash
python3 -m pip install -r requirements-dev.txt
```

## Dependencies

`requirements.txt`:

```txt
requests
jinja2
pybaseball
```

`requirements-dev.txt`:

```txt
ruff
```

## Run the Program

From the project root:

```bash
python3 import_data.py
```

You will be prompted for a game date or a direct MLB game number (gamePk):

```text
Game date (YYYY-MM-DD) or baseball game number:
```

**By game number** (fastest):

```text
Game date (YYYY-MM-DD) or baseball game number: 824750
```

**By date** (prompts for home and away team):

```text
Game date (YYYY-MM-DD) or baseball game number: 2026-05-31
Home team, e.g. Orioles: Orioles
Away team, e.g. Blue Jays: Blue Jays
```

The program writes two files to `output/`:

- `<date>_<away>_at_<home>.json` — full enriched game data
- `<date>_<away>_at_<home>.html` — Jinja2 scorecard, opened automatically in the browser

API responses are cached in `.cache/` for one hour so re-runs are fast.

## Useful Testing Shortcut

You can pipe input to skip the prompts:

```bash
echo "824750" | python3 import_data.py
```

## Reference Scripts

Scripts in `reference/` query the MLB Stats API and open the results in the browser.

| Script | What it shows |
|---|---|
| `python3 reference/query-event-types.py` | All MLB event types with their official codes and abbreviations |

Output files are written to `reference/` and can be reopened without re-fetching.

## Development Tools

Format and lint with Ruff:

```bash
ruff check import_data.py
ruff format import_data.py
```

To check for Python syntax errors:

```bash
python3 -m py_compile import_data.py
```
