import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Exercise the pinned host API source; production installs the workspace package.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend/packages/extension-api"))
