---
name: verification-2026-06-11
description: Mode B pass over fable_issues.md Fix-Attempted entries — 8 promoted to Resolved, 5 held
metadata:
  type: project
---

Bug-hunter verification pass on `fable_issues.md` (2026-06-11). Baseline + final pytest: 444 passed, 8 skipped.

**Promoted Fix Attempted → Resolved** (code-inspected + dedicated test file green, no live/external gap): FABLE-004, 005, 006, 007, 009, 010, 012, 014.

**Held at Fix Attempted** (each has a real remaining acceptance criterion, not a defect):
- FABLE-008 — only the config mitigation (interval 30 / 1H) landed; event-driven redesign unbuilt. Self-described partial.
- FABLE-011 — real Telegram Bot API delivery never observed (needs live token).
- FABLE-015 — `deploy/trade-agent.service` correct, but automatic crash-restart unobserved + VPS system-unit pending.
- FABLE-017 — receiver verified via curl/cloudflared, but a genuine TradingView-originated alert still pending.
- FABLE-018 — equity_curve.csv + performance_report.py built; the "action"/scheduling half of the feedback loop unbuilt.

Nothing failed or regressed; no demotions. **Why:** these decisions match the task rule "promote only when fully verified AND no live/external verification pending."

**How to apply:** On the next pass, the quickest promotions to check are FABLE-011/015/017/018 — they flip to Resolved on a single observed real-world event each (Telegram message / crash-restart in journald / TV alert / scheduled report). FABLE-008 needs actual code (the redesign), not just an observation.

Environment note: `systemctl --user` and `journalctl` were denied by the harness this session, so FABLE-015 runtime supervision state could not be re-confirmed live — relied on the service-file inspection only.
