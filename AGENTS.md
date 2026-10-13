# Repository agent entry point

For **installing or using** this fork in OpenClaw, read `AI-INSTALL.md` first,
then `README.md`. Install only the standalone diet-tracking-analysis entry (local coach v5: meals, profiles, measurements, plans, reviews and opt-in reminder configuration).
Do not install all skill directories or run the upstream packaging/publishing scripts.

For code changes, also read `CLAUDE.md` and `docs/CONVENTIONS.md` when present.
The lightweight archive omits those upstream development files.
Use uv with the project in `diet-tracking-analysis/`; no global pip installs.

This file guides the installer/developer AI. It is NOT a target Agent identity:
never copy it over the user's workspace AGENTS.md, USER.md, or existing policy.

Release note: GitHub v5 branch is feat/local-coach-v5, based on main after
PR #1 (af51bc2). Use this branch/commit until its PR is merged. Verify v5
coach files and read AI-INSTALL.md before installing.
