# Weight Trend Judgment — Model Decides

`save-and-check.py` returns weight history and context; the model decides on its own whether to intervene.

## Returned Fields

- `save`: the save result
- `context.recent_weights`: the last 14 weight entries
- `context.plan`: plan info (TDEE, target weight, calorie target)
- `context.active_strategy`: whether an intervention strategy is currently running
- `context.last_intervention_date`: date of the last intervention

## Judgment Guide

The core test: **is the weight change moving in the same direction as the user's goal, and is any deviation a genuine trend rather than day-to-day noise?**

Weight swings ±0.5–1kg naturally every day (water, sodium, bowel movements). It takes several days of data to tell a trend apart from noise.

- **On track with the goal** (e.g. losing weight when the goal is to lose) → encourage.
- **Everyday noise** (1–2 days of minor movement within the normal band) → acknowledge normally, don't probe.
- **Likely a real deviation** (several consecutive days in the wrong direction, or a clear rebound off a low that exceeds the normal swing) → reassure, then gently offer to look into the cause. If the user says yes → read `weight-gain-strategy/references/cause-check-flow.md`.
- **Plateau** (little to no change over 14 days, drifting within a narrow band) → gently ask whether they'd like to look into why. If they say yes → likewise go to `cause-check-flow.md`.
- `active_strategy.active: true` → don't intervene again.
- `last_intervention_date` within the past 7 days → don't intervene again (recorded after both plateau and deviation prompts).
- **Only raise it when you're at least 80% confident the deviation is real. An unnecessary question is also an intrusion.**
