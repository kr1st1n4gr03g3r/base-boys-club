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
- Weather
- Ballpark information
- Team logos
- Lineups
- Pitching totals
- Pitching decisions
- At-bat results
- Extra runner results
- Pitch-by-pitch plate appearance data
- Batted-ball data such as exit velocity, distance, launch angle, and trajectory
- Statcast metrics

## Project Structure

```text
base-boys-club/
├── import_data.py
├── unit_formatters.py
├── scorecard.py
├── templates/
│   └── scorecard.html
├── styles/
│   └── report.css
├── output/
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

Recommended `requirements.txt`:

```txt
requests
```

Recommended `requirements-dev.txt`:

```txt
ruff
```

## Run the Program

From the project root:

```bash
python3 import_data.py
```

You will be prompted for:

```text
Home team, e.g. Orioles:
Away team, e.g. Blue Jays:
```

A second option is to also provide a game date:

```text
Home team, e.g. Orioles:
Away team, e.g. Blue Jays:
Game date (YYYY-MM-DD):
```

Example:

```text
Home team, e.g. Orioles: orioles
Away team, e.g. Blue Jays: jays
Game date (YYYY-MM-DD): 2026-05-31
```

## Useful Testing Shortcut

You can create a file called `test_input.txt`:

```text
orioles
jays
2026-05-31
```

Then run:

```bash
python3 import_data.py < test_input.txt
```

This feeds the prompts automatically while testing.

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
