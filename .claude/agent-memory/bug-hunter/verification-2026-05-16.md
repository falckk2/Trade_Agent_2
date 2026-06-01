---
name: verification-2026-05-16
description: Bug-hunter Mode B verification of issue-resolver's fix pass on 2026-05-16 — 22 fixes confirmed, 0 failed, 0 regressions
metadata:
  type: project
---

Verification pass for the 2026-05-16 issue-resolver fix run.

**Result:** All 22 issues marked `Resolved` by issue-resolver were Confirmed by bug-hunter. The 2 `Investigating` (ISSUE-014, ISSUE-019) issues have coherent logging instrumentation in place. The 3 `Open` issues (ISSUE-012, ISSUE-017, ISSUE-022) are still present in the code as expected. Pytest: 106 passed, 5 skipped.

**Why:** Documents the verification outcome so future audits can pick up from a known-good baseline rather than re-checking everything.

**How to apply:** When the next issue-resolver pass completes, compare the new Fix History entries against this baseline — anything newly resolved needs verification, anything still Open/Investigating against this list does not need re-inspection unless symptoms appear.

Notable verification observations worth carrying forward:
- `BloFinExchange` constructor now raises if a second instance is constructed while another is active. Tests that build two exchanges back-to-back must `disconnect()` between them or they will fail at `__init__`.
- `_load_trade_history` now skips malformed rows individually and logs the skipped count — a non-zero skip count in startup logs is a meaningful signal that the CSV was corrupted previously.
- The WebSocket listen task is held in `ws_task` (no fire-and-forget) so cancellation works on shutdown. Future changes that drop this reference will break clean shutdown.
- Atomic CSV write uses `filepath.with_suffix(".csv.tmp")` — relies on `with_suffix` placing the temp in the same directory. Changing the temp-file naming to use `tempfile.mktemp()` would break atomicity across filesystems.
- ISSUE-002 (leaked credentials) is marked Resolved but the actual credential rotation is still an operator action — the code-side mitigations don't invalidate the leaked keys.
