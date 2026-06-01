"""
ISSUE-002: .env file security mitigations — .env.example created, .gitignore verified.

Tests verify:
1. .env.example exists with empty placeholder fields
2. .env is listed in .gitignore
3. File permissions on .env are restricted (600 or similar)
"""

import os
import stat
import pytest
from pathlib import Path

PROJECT_ROOT = Path("/home/rehan/Trade_Agent_2")
ENV_FILE = PROJECT_ROOT / ".env"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"
GITIGNORE = PROJECT_ROOT / ".gitignore"


class TestEnvSecurity:
    def test_env_example_exists(self):
        """A .env.example file must exist to document the expected variables."""
        assert ENV_EXAMPLE.exists(), ".env.example does not exist — documentation artifact missing"

    def test_env_example_has_placeholder_fields(self):
        """env.example must contain placeholder entries for the three credential fields."""
        content = ENV_EXAMPLE.read_text()
        assert "BLOFIN_API_KEY" in content
        assert "BLOFIN_SECRET" in content
        assert "BLOFIN_PASSPHRASE" in content

    def test_env_example_has_no_real_secrets(self):
        """env.example must NOT contain actual credential values."""
        content = ENV_EXAMPLE.read_text()
        # The known leaked values from the issue report
        assert "be6691ff34734ba9816968028682729c" not in content
        assert "f30aaf131a624c93817106d36a6a4ba9" not in content
        assert "qazwsxedc" not in content

    def test_env_in_gitignore(self):
        """The .gitignore must include .env so it is never committed."""
        if not GITIGNORE.exists():
            pytest.skip(".gitignore does not exist in this repo")
        content = GITIGNORE.read_text()
        # Look for lines that would match .env
        lines = [line.strip() for line in content.splitlines()]
        assert any(line in (".env", "*.env", ".env*") for line in lines), \
            ".env is not listed in .gitignore"

    def test_env_permissions_restricted_if_present(self):
        """If .env exists, it should be owner-read-only (mode 0o600)."""
        if not ENV_FILE.exists():
            pytest.skip(".env file not present — skipping permission check")
        mode = stat.S_IMODE(os.stat(ENV_FILE).st_mode)
        # Accept 600 (owner rw only) — also accept 400 (owner ro)
        assert mode in (0o600, 0o400), \
            f".env has mode {oct(mode)}, expected 0o600 or 0o400"
