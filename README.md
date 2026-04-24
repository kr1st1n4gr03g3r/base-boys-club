# ⚾️ Base Boys Blue Club
Baseball statistics and visualizations for the Toronto Blue Jays -->

### Init Setup instructions:

1. Open a virtual environment:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pybaseball pandas
pip freeze > requirements.txt
```

2. Create a quick test file
```bash
touch main.py
```

3. Put into `main.py`:
```python
from pybaseball import batting_stats

batters = batting_stats(2026)
print(batters[["Name", "Team", "G", "HR", "AVG", "OBP", "SLG"]].head(10))
```
4. Run it:

```bash
python main.py
```

5. Add to `.gitignore`:
```gitignore
.venv/
__pycache__/
*.pyc
```