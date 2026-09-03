"""Guard against imports that work under pytest but break in production.

Tests run with cwd=backend/, where `from app.x import y` resolves. Production runs
from the project root as `backend.app.main:app`, where it raises ModuleNotFoundError.
The tagging worker shipped with this bug on 2026-08-11 and died silently on startup -
the service came up fine and simply never tagged anything.
"""
import re
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent / "app"
ABSOLUTE_APP_IMPORT = re.compile(r"^\s*(?:from|import)\s+app\.", re.MULTILINE)


def _app_modules():
    return sorted(p for p in APP_DIR.rglob("*.py") if "__pycache__" not in p.parts)


@pytest.mark.parametrize("path", _app_modules(), ids=lambda p: str(p.name))
def test_no_absolute_app_imports(path):
    offenders = [
        f"{path.relative_to(APP_DIR.parent)}:{i}: {line.strip()}"
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if ABSOLUTE_APP_IMPORT.match(line)
    ]
    assert not offenders, (
        "Use a relative import (`from .x import y`) instead — absolute `app.` imports "
        "resolve under pytest but not in production:\n  " + "\n  ".join(offenders)
    )
