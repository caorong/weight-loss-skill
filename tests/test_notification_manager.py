"""notification-manager 单测:cron 解析 + 时区转换 + 时间槽分配。

关键函数:
- _expand_field:cron 字段展开(*, /, -, 逗号)
- cron_to_utc_minutes:本地 tz cron → UTC 分钟数列表
- utc_minute_to_local:反向转
- adjust_cron_expr:替换 cron 里的 hour/minute
- _window_for_type:job 类型 → 允许滑动窗口
- build_utc_minute_counts:数所有 job 落在哪些 UTC 分钟(负载均衡基础)
"""
import importlib.util
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent / "notification-manager" / "scripts"
spec = importlib.util.spec_from_file_location("find_slot", SKILL / "find-slot.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class TestCronPayloadPathResolution:
    def test_creator_expands_notification_composer_placeholders(self):
        script = (SKILL / "create-reminder.sh").read_text()
        assert 'NOTIFICATION_COMPOSER_BASE="$PROJECT_ROOT/.openclaw-user-service/skills/notification-composer"' in script
        assert 'MESSAGE="${MESSAGE//\\{notification-composer:baseDir\\}/$NOTIFICATION_COMPOSER_BASE}"' in script
        assert 'MESSAGE="${MESSAGE//\\{workspaceDir\\}/$USER_WORKSPACE}"' in script
        assert 'MESSAGE="${MESSAGE//\\{tz_offset\\}/$TZ_OFFSET}"' in script

    def test_first_meal_payload_is_resolved_before_creation(self):
        script = (SKILL / "batch-create-reminders.sh").read_text()
        first_meal_lines = [line for line in script.splitlines() if line.strip().startswith("FM_MSG_")]
        assert len(first_meal_lines) == 2
        assert all("$NOTIFICATION_COMPOSER_BASE/scripts/pre-send-check.py" in line
                   for line in first_meal_lines)
        assert all("--tz-offset $TZ_OFFSET" in line for line in first_meal_lines)
        assert all("{notification-composer:baseDir}" not in line for line in first_meal_lines)


class TestExpandField:
    def test_wildcard(self):
        assert set(mod._expand_field("*", 0, 5)) == {0, 1, 2, 3, 4, 5}

    def test_single_value(self):
        assert mod._expand_field("3", 0, 23) == [3]

    def test_comma_list(self):
        assert set(mod._expand_field("0,15,30,45", 0, 59)) == {0, 15, 30, 45}

    def test_range(self):
        assert set(mod._expand_field("8-11", 0, 23)) == {8, 9, 10, 11}

    def test_step_from_wildcard(self):
        # */5 in 0-59 → 0,5,10,...,55
        result = set(mod._expand_field("*/5", 0, 59))
        assert 0 in result
        assert 5 in result
        assert 55 in result
        assert 4 not in result

    def test_step_from_range(self):
        # 0-30/10 → 0,10,20,30
        assert set(mod._expand_field("0-30/10", 0, 59)) == {0, 10, 20, 30}


class TestCronToUtcMinutes:
    def test_utc_direct(self):
        # 8:00 UTC → UTC minute 480
        assert mod.cron_to_utc_minutes("0 8 * * *", "UTC") == [480]

    def test_beijing_to_utc(self):
        # 8:00 Asia/Shanghai (UTC+8) → 0:00 UTC = 0
        assert mod.cron_to_utc_minutes("0 8 * * *", "Asia/Shanghai") == [0]

    def test_bad_cron_raises(self):
        with pytest.raises(ValueError, match="5-field"):
            mod.cron_to_utc_minutes("0 8 * *", "UTC")

    def test_multiple_minutes(self):
        # 每 15 分钟, 8:00 8:15 8:30 8:45 UTC → 4 个 utc min
        result = mod.cron_to_utc_minutes("0,15,30,45 8 * * *", "UTC")
        assert set(result) == {480, 495, 510, 525}


class TestUtcMinuteToLocal:
    def test_utc_to_shanghai(self):
        # UTC minute 0 = 00:00 UTC = 08:00 Asia/Shanghai
        h, m = mod.utc_minute_to_local(0, "Asia/Shanghai")
        assert h == 8
        assert m == 0

    def test_roundtrip_beijing(self):
        # cron 8:00 CST → UTC min 0 → 转回 CST → 8:00
        utc_mins = mod.cron_to_utc_minutes("0 8 * * *", "Asia/Shanghai")
        h, m = mod.utc_minute_to_local(utc_mins[0], "Asia/Shanghai")
        assert h == 8 and m == 0

    def test_negative_offset(self):
        # UTC 12:00 → EST(UTC-5 冬令时,7:00)
        h, m = mod.utc_minute_to_local(720, "America/New_York")
        assert h in (7, 8)  # 视 DST


class TestAdjustCronExpr:
    def test_replaces_hour_minute(self):
        assert mod.adjust_cron_expr("0 8 * * *", 9, 30) == "30 9 * * *"

    def test_preserves_dom_month_dow(self):
        assert mod.adjust_cron_expr("0 8 * * 1", 10, 15) == "15 10 * * 1"
        assert mod.adjust_cron_expr("0 8 1 * *", 10, 15) == "15 10 1 * *"


class TestWindowForType:
    def test_meal_wider(self):
        # meal/weight 有 5 分钟后置窗
        before, after = mod._window_for_type("meal")
        assert before == 10
        assert after == 5

    def test_weight_same(self):
        assert mod._window_for_type("weight") == (10, 5)

    def test_other_only_before(self):
        # 其他 job 只允许提前 10 分,不推后
        assert mod._window_for_type("weekly_report") == (10, 0)


class TestBuildUtcMinuteCounts:
    def test_counts_cron_jobs(self):
        jobs = [
            {"enabled": True, "schedule": {"kind": "cron", "expr": "0 8 * * *", "tz": "UTC"}},
            {"enabled": True, "schedule": {"kind": "cron", "expr": "0 8 * * *", "tz": "UTC"}},
        ]
        counts = mod.build_utc_minute_counts(jobs)
        assert counts[480] == 2  # 两 job 都在 UTC 8:00

    def test_skips_disabled(self):
        jobs = [
            {"enabled": False, "schedule": {"kind": "cron", "expr": "0 8 * * *", "tz": "UTC"}},
        ]
        assert mod.build_utc_minute_counts(jobs) == {}

    def test_skips_non_cron(self):
        # kind=oneshot 不算
        jobs = [
            {"enabled": True, "schedule": {"kind": "oneshot", "expr": "0 8 * * *", "tz": "UTC"}},
        ]
        assert mod.build_utc_minute_counts(jobs) == {}

    def test_bad_cron_skipped_no_crash(self):
        jobs = [
            {"id": "bad", "enabled": True, "schedule": {"kind": "cron", "expr": "invalid", "tz": "UTC"}},
            {"enabled": True, "schedule": {"kind": "cron", "expr": "0 8 * * *", "tz": "UTC"}},
        ]
        counts = mod.build_utc_minute_counts(jobs)
        # bad 跳过,不炸
        assert counts.get(480) == 1
