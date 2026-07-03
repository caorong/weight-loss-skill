# Diet Pattern Detection Response

## When `has_pattern` is `true`

The user's actual macro split over 3 consecutive days is closer to a different diet mode than their current one. Notify the user using this format:

> 📋 I noticed something over the past few days — your actual eating pattern looks more like [detected_mode_name] than [current_mode_name]. Here's a quick comparison:
>
> Your average: Protein [avg_split.protein_g]g/day / Carbs [avg_split.carbs_pct]% / Fat [avg_split.fat_pct]%
> [current_mode_name] range: Protein [X-X]g/day / Carbs [X-X]% / Fat [X-X]%
> [detected_mode_name] range: Protein [X-X]g/day / Carbs [X-X]% / Fat [X-X]%

---

### HARD RULES — range numbers

1. Protein ranges use **grams per day** (from `effective_current_mode_range.protein_g` and `detected_mode_range.protein_g`). Display as `[lo]-[hi]g/day`. Do NOT show protein as percentage.
2. Carbs and fat ranges use **percentages** (from `effective_current_mode_range.carbs/fat` and `detected_mode_range.carbs/fat`).
3. The `Your average` protein number comes from `avg_split.protein_g` (grams). Carbs and fat from `avg_split.carbs_pct` and `avg_split.fat_pct`.
4. **Forbidden**: Do not invent, recall, or guess any figures. If a required field is missing from the script output, omit that line entirely rather than fabricating numbers.
5. A three-column comparison table (Actual / Current mode range / Detected mode range) is also acceptable.

---

> Switching to [detected_mode_name] could work well for you:
> ✅ [pro 1]
> ✅ [pro 2]
>
> Things to keep in mind:
> ⚠️ [con 1]
> ⚠️ [con 2]
>
> Would you like to switch to [detected_mode_name], or keep your current plan? Either way is totally fine — the best diet mode is the one you can stick with.

- Keep the tone neutral and supportive — this is a suggestion, not a correction
- Only show the top 2-3 pros and 1-2 cons from the `pros_cons` output
- Do not mention this again for at least 7 days after the user declines
- If the user agrees to switch, update `health-profile.md > Diet Config > Diet Mode` and recalculate macro targets using the new mode
