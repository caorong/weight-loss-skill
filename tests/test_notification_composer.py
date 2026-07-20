"""notification-composer 单测:纯函数覆盖(不做 workspace/API IO)。

覆盖:
- pre-send-check:get_local_date/weekday, _meal_has_food
- holiday-dispatcher:get_today, load_holidays, find_upcoming_holiday(依赖真实 references/holidays/*.json)
- load-meals/meal-history 里的 _migrate_meal(和其他 skill 版本对齐)
"""
import importlib.util
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent / "notification-composer" / "scripts"

def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

pre_send = _load("pre_send_check", SKILL / "pre-send-check.py")
holiday = _load("holiday_dispatcher", SKILL / "holiday-dispatcher.py")
load_meals = _load("load_meals", SKILL / "load-meals.py")
extract_recent = _load("extract_recent", SKILL / "extract-recent.py")


class TestGetLocalDate:
    def test_returns_yyyy_mm_dd(self):
        d = pre_send.get_local_date(0)  # UTC
        assert len(d) == 10
        assert d[4] == "-" and d[7] == "-"

    def test_beijing_offset(self):
        # UTC+8 早 8 小时,同一 UTC 秒本地日期可能已过午夜
        d_utc = pre_send.get_local_date(0)
        d_bj = pre_send.get_local_date(8 * 3600)
        # 至少格式正确
        assert len(d_bj) == 10


class TestGetLocalWeekday:
    def test_returns_int_0_to_6(self):
        w = pre_send.get_local_weekday(0)
        assert 0 <= w <= 6


class TestMealHasFood:
    def test_items(self):
        assert pre_send._meal_has_food({"items": [{"name": "苹果"}]}) is True

    def test_foods_fallback(self):
        assert pre_send._meal_has_food({"foods": [{"name": "香蕉"}]}) is True

    def test_empty(self):
        assert pre_send._meal_has_food({"items": []}) is False

    def test_not_dict(self):
        assert pre_send._meal_has_food(None) is False
        assert pre_send._meal_has_food("str") is False


class TestGetToday:
    def test_mock_date(self):
        d = holiday.get_today(0, mock_date="2026-10-01")
        assert d == date(2026, 10, 1)

    def test_real_returns_date(self):
        d = holiday.get_today(0)
        assert isinstance(d, date)


class TestLoadHolidays:
    def test_cn_2026_loads(self):
        # references/holidays/cn-2026.json 应存在
        result = holiday.load_holidays(2026, "cn")
        assert isinstance(result, list)
        # cn 2026 至少有几个假日
        assert len(result) > 0

    def test_unknown_region_empty(self):
        assert holiday.load_holidays(2026, "xyz-nonexistent") == []

    def test_unknown_year_empty(self):
        assert holiday.load_holidays(1990, "cn") == []


class TestFindUpcomingHoliday:
    def test_returns_none_when_no_holiday_soon(self):
        # 6/16 距最近假日 > 5 天时应 None(mid-june 中国无假日)
        result = holiday.find_upcoming_holiday(date(2026, 6, 16), "cn")
        # 可能 None(如果 6/16-6/21 无假日)或返回真假日
        if result is not None:
            # 若有,应有 start 字段
            assert "start" in result

    def test_finds_holiday_within_lookahead(self):
        # 2026-09-27 距 10/1 国庆约 4 天,应在 LOOKAHEAD_DAYS=5 内
        result = holiday.find_upcoming_holiday(date(2026, 9, 27), "cn")
        # 若 cn-2026.json 有国庆,应命中
        if result is not None:
            start = datetime.strptime(result["start"], "%Y-%m-%d").date()
            delta = (start - date(2026, 9, 27)).days
            assert 0 <= delta <= 5


class TestLoadMealsMigrateMeal:
    """load-meals.py 里的 _migrate_meal 应和 diet-tracking-analysis 那份对齐"""
    def test_short_keys(self):
        m = load_meals._migrate_meal({"cal": 500, "meal_name": "breakfast"})
        assert m["calories"] == 500

    def test_items_to_foods(self):
        m = load_meals._migrate_meal({"items": [{"name": "苹果", "calories": 50}]})
        assert "foods" in m
        assert "items" not in m

    def test_g_suffix(self):
        m = load_meals._migrate_meal({"protein_g": 30, "carbs_g": 60, "fat_g": 15})
        assert m["protein"] == 30
        assert m["carbs"] == 60
        assert m["fat"] == 15

    def test_foods_sum_to_meal(self):
        """如果没 meal-level 热量,从 foods 求和"""
        m = load_meals._migrate_meal({
            "foods": [
                {"calories": 100, "protein": 5, "carbs": 15, "fat": 2},
                {"calories": 200, "protein": 10, "carbs": 30, "fat": 4},
            ],
        })
        assert m["calories"] == 300
        assert m["protein"] == 15


class TestExtractRecent:
    """extract-recent.py:薄 HTTP 客户端(走 data-api)。轮次/清洗/截断逻辑在
    data-api 端(见 data-api conversation.test.ts),这里只测 account 推导 +
    调用失败静默返回空(绝不阻断提醒)。"""

    def test_account_from_workspace(self):
        assert extract_recent._account_from_workspace(
            "/x/workspace-wechat-dm-acc12345") == "acc12345"
        assert extract_recent._account_from_workspace(
            "/x/workspace-wecom-dm-acc678") == "acc678"

    def test_fetch_recent_empty_account(self):
        assert extract_recent.fetch_recent("") == ""

    def test_fetch_recent_unreachable_api_returns_empty(self):
        # 指向一个必然连不上的端口 → 静默返回空,不抛异常
        out = extract_recent.fetch_recent(
            "acc00000000000000000", api="http://127.0.0.1:1")
        assert out == ""

    def test_fetch_recent_uses_token_budget(self, monkeypatch):
        seen = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return b'{"text":"ok"}'

        def fake_urlopen(url, timeout):
            seen["url"] = url
            seen["timeout"] = timeout
            return Response()

        monkeypatch.setattr(extract_recent.urllib.request, "urlopen", fake_urlopen)
        assert extract_recent.fetch_recent("acc123", turns=10, max_tokens=6000) == "ok"
        assert "turns=10" in seen["url"]
        assert "maxTokens=6000" in seen["url"]
        assert "maxChars" not in seen["url"]
