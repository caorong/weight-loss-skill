"""Standalone installer integration tests; only synthetic temporary workspaces."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "scripts/install-standalone.py"


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="meal install ")
        self.ws = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def install(self, *flags):
        return subprocess.run([sys.executable, str(INSTALL), "--workspace", str(self.ws),
                               "--timezone", "Asia/Tokyo", "--runner", "python", *flags],
                              capture_output=True, text=True, timeout=30)

    def test_whitelist_and_runtime(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        base = self.ws / "skills/diet-tracking-analysis"
        files = sorted(str(p.relative_to(base)) for p in base.rglob("*") if p.is_file())
        self.assertEqual(files, sorted([".python-version", "SKILL.md", "pyproject.toml", "references/standalone-flow.md",
              "references/coach-api.md", "references/coach-flows.md", "references/native-reminders.md", "runtime.json",
              "scripts/meal-log.py", "scripts/coach.py", "scripts/coach_store.py", "scripts/self-test-coach.py", "scripts/self-test.py", "uv.lock"]))
        config = json.loads((base / "runtime.json").read_text())
        self.assertEqual(config["schema_version"], 3)
        self.assertIn("coach", config["commands"])
        config = json.loads((base / "runtime.json").read_text())
        self.assertEqual(config["workspace"], str(self.ws))
        self.assertEqual(config["timezone_fallback"], "Asia/Tokyo")

    def test_profiles_not_overwritten(self):
        for name in ["USER.md", "AGENTS.md", "PLAN.md", "health-profile.md", "data/weight.json"]:
            path = self.ws / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("original")
        self.assertEqual(self.install().returncode, 0)
        for name in ["USER.md", "AGENTS.md", "PLAN.md", "health-profile.md", "data/weight.json"]:
            self.assertEqual((self.ws / name).read_text(), "original")

    def test_dry_run_is_read_only(self):
        self.assertEqual(self.install("--dry-run").returncode, 0)
        self.assertEqual(list(self.ws.iterdir()), [])

    def test_existing_skill_needs_explicit_replace(self):
        self.assertEqual(self.install().returncode, 0)
        self.assertNotEqual(self.install().returncode, 0)

    def test_replace_keeps_backup(self):
        self.install()
        target = self.ws / "skills/diet-tracking-analysis/SKILL.md"
        target.write_text("old skill")
        result = self.install("--replace")
        self.assertEqual(result.returncode, 0, result.stderr)
        previous = list((self.ws / "data/skill-backups").glob("*/SKILL.md"))
        self.assertEqual(len(previous), 1)
        self.assertEqual(previous[0].read_text(), "old skill")
        self.assertNotEqual(target.read_text(), "old skill")

    def test_invalid_timezone_makes_no_files(self):
        result = self.install("--timezone", "invalid/zone")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(self.ws.iterdir()), [])

    def test_install_rejects_skills_symlink(self):
        other = self.ws / "elsewhere"
        other.mkdir()
        (self.ws / "skills").symlink_to(other, target_is_directory=True)
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(other.iterdir()), [])

    def test_installed_cli_works_from_unrelated_cwd(self):
        self.install()
        cli = self.ws / "skills/diet-tracking-analysis/scripts/meal-log.py"
        result = subprocess.run([sys.executable, str(cli), "summary", "--workspace", str(self.ws),
                                 "--tz", "Asia/Tokyo"], cwd="/tmp", capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])

    def test_installed_skill_excludes_plugin_workflow(self):
        self.install()
        source = (self.ws / "skills/diet-tracking-analysis/SKILL.md").read_text()
        self.assertNotIn("ONLY use `meal_checkin`", source)
        self.assertNotIn("/home/nanorhino/", source)
        self.assertIn("saved: true", source)


if __name__ == "__main__":
    unittest.main()
