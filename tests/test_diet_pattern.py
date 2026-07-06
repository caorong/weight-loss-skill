"""diet-pattern-detection 单测:检测饮食模式匹配 / macro 距离 / pros-cons。

覆盖:
- _calc_macro_pcts:每日热量 < 500 kcal 返 None(样本不足过滤)
- _mode_distance:actual macro% 到 mode 目标区间的偏离度
- _get_pros_cons:每种 mode 的 pros/cons 结构完整
"""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "diet-pattern-detection" / "scripts" / "detect-pattern.py"
spec = importlib.util.spec_from_file_location("detect_pattern", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class TestCalcMacroPcts:
    def test_below_500_returns_none(self):
        # 数据太少 → None(过滤噪声)
        meals = [{"calories": 300, "protein": 20, "carbs": 30, "fat": 10}]
        assert mod._calc_macro_pcts(meals) is None

    def test_returns_pcts(self):
        # 1500 kcal, protein 100g×4=400 (26.7%), carbs 200g×4=800 (53.3%), fat 50g×9=450 (30%)
        # 但物理上 400+800+450=1650 > 1500,这个 fixture 只测函数不做守恒
        meals = [{"calories": 1500, "protein": 100, "carbs": 200, "fat": 50}]
        r = mod._calc_macro_pcts(meals)
        assert r is not None
        assert r["calories"] == 1500.0
        # protein: 100*4/1500*100 = 26.67
        assert r["protein_pct"] == 26.7

    def test_zero_calories_returns_none(self):
        assert mod._calc_macro_pcts([]) is None

    def test_multi_meal_sum(self):
        meals = [
            {"calories": 500, "protein": 30, "carbs": 60, "fat": 15},
            {"calories": 500, "protein": 30, "carbs": 60, "fat": 15},
        ]
        r = mod._calc_macro_pcts(meals)
        assert r["calories"] == 1000.0


class TestModeDistance:
    def test_perfect_match_zero_distance(self):
        # balanced protein 25-35, carbs 35-45, fat 20-35
        # 落在区间内 → 距离 0
        d = mod._mode_distance(p_pct=30, c_pct=40, f_pct=25, mode="balanced")
        assert d == 0.0

    def test_below_range_adds_gap(self):
        # balanced carbs 35-45: carbs 25 < 35 → gap 10, fat 25 ok
        # (蛋白维度已改为按克数评分,不传 protein_g → 贡献 0;见下方 protein-gram 测试)
        d = mod._mode_distance(p_pct=15, c_pct=25, f_pct=25, mode="balanced")
        assert d == 10.0

    def test_above_range_adds_gap(self):
        # balanced fat 20-35: fat 40 > 35 → gap 5, carbs 40 ok
        d = mod._mode_distance(p_pct=40, c_pct=40, f_pct=40, mode="balanced")
        assert d == 5.0

    def test_multi_dimension_sum(self):
        # carbs 30 < 35 (5) + fat 15 < 20 (5) = 10
        d = mod._mode_distance(p_pct=20, c_pct=30, f_pct=15, mode="balanced")
        assert d == 10.0

    def test_protein_grams_within_range_zero(self):
        # balanced 蛋白 1.2-1.6 g/kg;target_weight=60 → 区间 72-96g
        # protein_g=80 落区间内 → 蛋白贡献 0;carbs/fat 也 ok → 距离 0
        d = mod._mode_distance(p_pct=30, c_pct=40, f_pct=25, mode="balanced",
                               protein_g=80, target_weight=60)
        assert d == 0.0

    def test_protein_grams_below_range_adds_gap(self):
        # target_weight=60 → 下限 72g;protein_g=36 → (72-36)/72*100 = 50
        # carbs/fat 落区间 → 总距离 50
        d = mod._mode_distance(p_pct=30, c_pct=40, f_pct=25, mode="balanced",
                               protein_g=36, target_weight=60)
        assert d == 50.0

    def test_keto_far_from_balanced_diet(self):
        # 高碳饮食算 keto 距离(keto carbs 5-10),50 - 10 = 40
        d = mod._mode_distance(p_pct=22, c_pct=50, f_pct=70, mode="keto")
        # protein 22 ok, carbs 50-10=40 gap, fat 70 ok
        assert d == 40.0

    def test_unknown_mode_infinite(self):
        d = mod._mode_distance(30, 40, 25, mode="unknown-mode")
        assert d == float("inf")


class TestGetProsCons:
    def test_balanced_has_switch_names(self):
        # 返回 { switch_to, switch_to_name, switch_from, switch_from_name, ... }
        r = mod._get_pros_cons("balanced", "balanced")
        assert "switch_to" in r
        assert r["switch_to"] == "balanced"
        assert r["switch_from"] == "balanced"

    def test_switch_scenario(self):
        # current balanced → detected high_protein
        r = mod._get_pros_cons("balanced", "high_protein")
        assert isinstance(r, dict)

    def test_all_diet_modes_have_pros_cons(self):
        """每种支持的 mode 都应能拿到 pros/cons"""
        modes = ["balanced", "high_protein", "low_carb", "keto",
                 "mediterranean", "plant_based", "usda"]
        for m in modes:
            r = mod._get_pros_cons(m, m)
            assert isinstance(r, dict)
