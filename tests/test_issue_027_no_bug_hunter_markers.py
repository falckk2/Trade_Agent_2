"""
ISSUE-027: All BUG-HUNTER temporary diagnostic logging markers have been removed
from the source files.

Tests verify:
1. No BUG-HUNTER marker strings remain in any source file
2. The specific files mentioned in the issue are clean
"""

import pytest
from pathlib import Path

PROJECT_ROOT = Path("/home/rehan/Trade_Agent_2")

_FILES_TO_CHECK = [
    "src/exchange/blofin_exchange.py",
    "src/engine/trading_engine.py",
    "src/portfolio/manager.py",
    "src/execution/executor.py",
    "main.py",
]

_MARKER = "BUG-HUNTER"


class TestNoBugHunterMarkers:
    @pytest.mark.parametrize("rel_path", _FILES_TO_CHECK)
    def test_file_has_no_bug_hunter_marker(self, rel_path):
        """Each source file must contain no BUG-HUNTER marker lines."""
        file_path = PROJECT_ROOT / rel_path
        assert file_path.exists(), f"Source file missing: {rel_path}"
        content = file_path.read_text(encoding="utf-8")
        assert _MARKER not in content, \
            f"BUG-HUNTER marker still present in {rel_path} — run cleanup"

    def test_no_bug_hunter_anywhere_in_src(self):
        """Recursively verify no BUG-HUNTER markers in any src/ file."""
        src_dir = PROJECT_ROOT / "src"
        offending = []
        for py_file in src_dir.rglob("*.py"):
            if _MARKER in py_file.read_text(encoding="utf-8", errors="replace"):
                offending.append(str(py_file))

        assert not offending, \
            f"BUG-HUNTER markers found in: {offending}"
