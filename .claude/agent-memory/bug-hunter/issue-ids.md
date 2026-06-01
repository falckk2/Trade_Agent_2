---
name: issue-ids
description: Tracks the highest-numbered ISSUE-NNN entry written to issues.md so future bug-hunter runs assign new IDs without collision
metadata:
  type: project
---

Last assigned ID: **ISSUE-027** (in `/home/rehan/Trade_Agent_2/issues.md`, audit on 2026-05-16).

**Why:** issues.md is append-only — new issues must start at ISSUE-028 or higher.

**How to apply:** When opening a new bug-hunter session, read `issues.md` and look for the highest ID, then begin numbering from N+1. If the file has been resorted or pruned by another agent, scan all existing entries to find the max.
