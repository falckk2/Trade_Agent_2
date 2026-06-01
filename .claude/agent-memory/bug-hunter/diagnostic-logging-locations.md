---
name: diagnostic-logging-locations
description: Files that bug-hunter agent has instrumented with temporary diagnostic logging markers that need to be cleaned up after investigation
metadata:
  type: project
---

As of 2026-05-16 verification pass, all BUG-HUNTER markers have been cleaned up. Confirmed with:

```bash
grep -rn "BUG-HUNTER" /home/rehan/Trade_Agent_2/src/ /home/rehan/Trade_Agent_2/main.py
# (exit code 1 — no matches)
```

The issue-resolver pass retained several permanent diagnostic logs at INFO/WARNING/DEBUG levels that are NOT marker-tagged but came out of the cleanup:
- `src/exchange/blofin_exchange.py:140-184, 298, 314-318` — instrument spec / balance shape / get_order not-found / positionId-missing warnings
- `src/portfolio/manager.py:58-76, 220-223` — CLOSE/FLIP detection and `_on_order_filled` cache fill DEBUG logs
- `src/engine/trading_engine.py:215-220, 269-272, 286-289, 322-325` — composite aggregation, signal, portfolio sizing DEBUG logs

These are intentional permanent diagnostics, not bug-hunter instrumentation.

**Why:** Tracking which files contain bug-hunter-added logging is what lets cleanup happen safely without grepping the whole repo each time. Future audits should re-create this file with fresh entries when instrumentation is added.

**How to apply:** When adding new diagnostic logging in a future audit, append the file path here under a "Currently instrumented" heading with the issue IDs it serves. Update this file (and the issue's Fix History) when the markers are removed.
