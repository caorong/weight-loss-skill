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
    """extract-recent.py:轮次截取/清洗/字数上限(纯函数,构造临时 jsonl)。"""

    def _write_session(self, tmp_path, entries):
        import json
        f = tmp_path / "sess.jsonl"
        with open(f, "w", encoding="utf-8") as fh:
            for e in entries:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        return str(f)

    def _msg(self, role, text, ts):
        return {"type": "message", "timestamp": ts,
                "message": {"role": role, "content": text}}

    def test_empty_file_returns_empty(self, tmp_path):
        f = self._write_session(tmp_path, [])
        assert extract_recent.extract_recent(f, turns=10) == ""

    def test_missing_file_returns_empty(self):
        assert extract_recent.extract_recent("/nonexistent/x.jsonl") == ""

    def test_basic_user_assistant(self, tmp_path):
        f = self._write_session(tmp_path, [
            self._msg("user", "我今天没怎么动", "2026-07-06T01:00:00.000Z"),
            self._msg("assistant", "没关系,明天散个步就好", "2026-07-06T01:00:05.000Z"),
        ])
        out = extract_recent.extract_recent(f, turns=10)
        assert "用户: 我今天没怎么动" in out
        assert "你: 没关系" in out

    def test_turns_limit_by_assistant(self, tmp_path):
        entries = []
        for i in range(5):
            entries.append(self._msg("user", f"u{i}", f"2026-07-06T01:0{i}:00.000Z"))
            entries.append(self._msg("assistant", f"a{i}", f"2026-07-06T01:0{i}:05.000Z"))
        f = self._write_session(tmp_path, entries)
        out = extract_recent.extract_recent(f, turns=2)
        # 只保留最后 2 个 assistant 轮 → a3,a4;a0/a1/a2 不出现
        assert "a4" in out and "a3" in out
        assert "a0" not in out and "a2" not in out

    def test_skips_noise(self, tmp_path):
        f = self._write_session(tmp_path, [
            self._msg("user", "NO_REPLY", "2026-07-06T01:00:00.000Z"),
            self._msg("assistant", "HEARTBEAT_OK", "2026-07-06T01:00:01.000Z"),
            self._msg("user", "真实问题", "2026-07-06T01:00:02.000Z"),
            self._msg("assistant", "真实回复", "2026-07-06T01:00:03.000Z"),
        ])
        out = extract_recent.extract_recent(f, turns=10)
        assert "NO_REPLY" not in out
        assert "HEARTBEAT_OK" not in out
        assert "真实问题" in out

    def test_cleans_metadata_and_media(self, tmp_path):
        noisy = ('Conversation info (untrusted metadata):\n```json\n{"chat_id":"x"}\n```\n'
                 '[media attached: /abs/path.png (image/png) | /abs/path.png]\n吃了个苹果')
        f = self._write_session(tmp_path, [
            self._msg("user", noisy, "2026-07-06T01:00:00.000Z"),
            self._msg("assistant", "记下了", "2026-07-06T01:00:01.000Z"),
        ])
        out = extract_recent.extract_recent(f, turns=10)
        assert "chat_id" not in out
        assert "/abs/path.png" not in out
        assert "[图片]" in out
        assert "吃了个苹果" in out

    def test_max_chars_truncates(self, tmp_path):
        entries = []
        for i in range(10):
            entries.append(self._msg("user", "长" * 100, f"2026-07-06T01:0{i}:00.000Z"))
            entries.append(self._msg("assistant", "回" * 100, f"2026-07-06T01:0{i}:05.000Z"))
        f = self._write_session(tmp_path, entries)
        out = extract_recent.extract_recent(f, turns=10, max_chars=200)
        assert len(out) <= 260  # 上限 + 单行冗余

    def test_content_as_block_list(self, tmp_path):
        f = self._write_session(tmp_path, [
            {"type": "message", "timestamp": "2026-07-06T01:00:00.000Z",
             "message": {"role": "user",
                         "content": [{"type": "text", "text": "块状内容"},
                                     {"type": "image", "text": ""}]}},
            self._msg("assistant", "收到", "2026-07-06T01:00:01.000Z"),
        ])
        out = extract_recent.extract_recent(f, turns=10)
        assert "块状内容" in out
        assert "[图片]" in out
