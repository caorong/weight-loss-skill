# Quick TDEE Estimation (Standalone Mode)

When this skill is used without a prior weight-loss-planner session, and the user doesn't have a calorie target, use the planner-calc script for a quick inline estimation. This is a simplified version — for a thorough plan with milestones, recommend the weight-loss-planner skill.

## Quick Flow

1. Ask: height, weight, age, sex, activity level (see `weight-loss-planner/references/formulas.md > Activity Level Selection Policy` — default to `lightly_active` unless the user's description clearly fits another level; do not use levels above `moderately_active` in normal cases)
2. Run the planner-calc script to compute TDEE and calorie target:

```bash
# Get TDEE:
python3 {weight-loss-planner:baseDir}/scripts/planner-calc.py tdee \
  --weight <kg> --height <cm> --age <years> --sex male|female \
  --activity sedentary|lightly_active|moderately_active|very_active|extremely_active

# Get calorie target with a deficit:
python3 {weight-loss-planner:baseDir}/scripts/planner-calc.py calorie-target \
  --tdee <tdee_value> --rate-kg 0.5

# Or do it all at once if you have a target weight:
python3 {weight-loss-planner:baseDir}/scripts/planner-calc.py forward-calc \
  --weight <kg> --height <cm> --age <years> --sex male|female \
  --activity <level> --target-weight <kg> --mode balanced
```

The script automatically handles BMR (Mifflin-St Jeor), activity multipliers, safety floors (max(BMR, 1000)), and rate recommendations.

3. Present the calorie target to the user and proceed with meal planning.

## Unit Conversions

```bash
python3 {weight-loss-planner:baseDir}/scripts/planner-calc.py unit-convert --value 170 --from lbs --to kg
python3 {weight-loss-planner:baseDir}/scripts/planner-calc.py unit-convert --value 65 --from in --to cm
```

## When to Recommend the Full Skill Instead

If the user wants milestones, phased plans, or has >20 lbs to lose, recommend the weight-loss-planner skill for a proper assessment. This quick calc is just to get a calorie number for meal planning when the user's primary ask is "tell me what to eat" rather than "build me a weight loss plan."
