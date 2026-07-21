# User Data Format Reference

This file documents the expected structure of the user data files created by the onboarding skill and consumed by downstream skills.

Data is split across three files with distinct responsibilities:

---

## USER.md — Identity (cross-scenario)

Contains basic identity info that is scenario-independent.

### Fields used by this skill
```markdown
## Basic Info
- Name: [string]
- Age: [number]
- Sex: [Male / Female]
- Height: [X cm]

## Health Flags
- [list or None]
```

**Note:** Weight is NOT stored in USER.md. Read current weight from `data/weight.json` via `weight-tracker.py load --last 1`.

---

## health-profile.md — Health Facts & Settings (scenario input)

Contains health-related facts and configuration parameters. These are **inputs** to plan generation, not outputs.

### Fields used by this skill
```markdown
## Body
- Unit Preference: [kg | lb]

## Activity & Lifestyle
- Work Type: [sedentary | active]
- Activity Level: [Sedentary / Lightly Active / Moderately Active / Very Active / Extremely Active]
- Exercise Habits: [free text]

## Fitness
- Fitness Level: [beginner | intermediate | advanced]
- Fitness Goal: [lose_fat | build_muscle | stay_healthy | improve_endurance]

## Diet Config
- Diet Mode: [Balanced / Keto / Mediterranean / ...]
- Food Restrictions: [list or None]

## Meal Schedule
- Meals per Day: [number]
- Breakfast: [HH:MM]
- Lunch: [HH:MM]
- Dinner: [HH:MM]

## Goals
- Target Weight: [X kg]
- Weight to Lose: [X kg (calculated)]
- Core Motivation: [string]
```

### Fields written by this skill
- `Diet Config > Diet Mode` — after user selects in Round 1
- `Meal Schedule` — after user provides in Round 2
- `Diet Config > Food Restrictions` — if user mentions new restrictions in Round 3

---

## health-preferences.md — Accumulated Preferences (append-only)

Contains user preferences accumulated across all conversations. Each entry is timestamped.

### Structure
```markdown
# Health Preferences

## Dietary
- [YYYY-MM-DD] Food likes, dislikes, allergies, flavor preferences

## Exercise
- [YYYY-MM-DD] Activity preferences, dislikes, physical limitations

## Scheduling & Lifestyle
- [YYYY-MM-DD] Work schedule, busy days, eating patterns

## Cooking & Kitchen
- [YYYY-MM-DD] Equipment, cooking skill, meal prep willingness
```

This section is appended to over time as users reveal preferences during conversations. Not all entries will be present from onboarding — preferences accumulate across sessions. All skills that read user data should check for and use this file when generating personalized content.

---

## Parsing Guidelines

- If files use different section headers (e.g., "Profile" instead of "Basic Info"), that's fine — look for the data fields, not the headers.
- Heights might appear as `5'10"`, `5 ft 10 in`, `178 cm`, or `70 inches` — handle all common formats.
- Weights might be in lbs or kg — check for units and convert as needed.
- Activity level might be the standard label or a free-text description — map it to the closest standard level for TDEE calculation.
- If `Target Weight` is already present in `health-profile.md`, you can pre-fill Step 3 but still confirm with the user.
