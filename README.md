# ⚾️ Base Boys Blue Club

Baseball statistics and visualizations for the Toronto Blue Jays.

## Init setup instructions

### 1. Open a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install requests pandas
```

### 2. Confirm the packages work from terminal

```bash
python -c "import requests; import pandas; print('Packages are working')"
```

### 3. Tell VS Code to use your virtual environment

In VS Code:

1. Press `Cmd + Shift + P`
2. Search for `Python: Select Interpreter`
3. Select:

```text
./.venv/bin/python
```

### 4. Reload VS Code

In VS Code:

1. Press `Cmd + Shift + P`
2. Search for `Developer: Reload Window`
3. Press Enter

### 5. Run the test file

```bash
python3 test.py
```

### 6. Add to `.gitignore`

```gitignore
.venv/
__pycache__/
*.pyc
```
