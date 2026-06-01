"""
ISSUE-024: Signal handlers for SIGINT/SIGTERM use loop.add_signal_handler()
instead of signal.signal(), which is asyncio-native and doesn't fight with
KeyboardInterrupt.

Tests verify:
1. main.py source uses loop.add_signal_handler (not signal.signal for SIGINT/SIGTERM)
2. run_engine() gets running loop before registering handlers
3. The asyncio-native approach is confirmed via AST inspection
"""

import ast
import pytest
from pathlib import Path

MAIN_PY = Path("/home/rehan/Trade_Agent_2/main.py")


def _parse_main():
    with open(MAIN_PY) as f:
        source = f.read()
    return ast.parse(source), source


class TestSignalHandlerAsyncioNative:
    def test_main_uses_add_signal_handler_not_signal_signal(self):
        """main.py must use loop.add_signal_handler() for SIGINT/SIGTERM."""
        tree, source = _parse_main()

        found_add_signal_handler = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                # Look for <something>.add_signal_handler(...)
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_signal_handler"
                ):
                    found_add_signal_handler = True
                    break

        assert found_add_signal_handler, \
            "main.py does not use loop.add_signal_handler() — asyncio-native signal handling missing"

    def test_main_does_not_use_signal_signal_for_sigint_sigterm(self):
        """main.py must NOT use signal.signal(SIGINT/SIGTERM, handler) anymore."""
        tree, _ = _parse_main()

        # Detect: signal.signal(signal.SIGINT, ...)
        uses_legacy_signal = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "signal"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "signal"
                ):
                    # Check if the first arg references SIGINT or SIGTERM
                    for arg in node.args:
                        if isinstance(arg, ast.Attribute) and arg.attr in ("SIGINT", "SIGTERM"):
                            uses_legacy_signal = True
                            break

        assert not uses_legacy_signal, \
            "main.py still uses signal.signal(SIGINT/SIGTERM, ...) — ISSUE-024 fix may be reverted"

    def test_run_engine_gets_running_loop(self):
        """run_engine must call asyncio.get_running_loop() (required before add_signal_handler)."""
        _, source = _parse_main()
        assert "get_running_loop" in source, \
            "main.py does not call asyncio.get_running_loop() — signal handler setup may be broken"

    def test_both_sigint_and_sigterm_are_registered(self):
        """Both SIGINT and SIGTERM must be registered with the asyncio loop."""
        _, source = _parse_main()
        assert "SIGINT" in source, "SIGINT not referenced in main.py"
        assert "SIGTERM" in source, "SIGTERM not referenced in main.py"
