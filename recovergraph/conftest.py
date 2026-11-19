"""Pytest path bootstrap: make `python3 -m pytest recovergraph/tests`
work from the repository root, not only from recovergraph/ itself."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
