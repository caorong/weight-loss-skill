"""Offline uv installation tests. No personal data or external APIs are used."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "scripts/install-standalone.py"
UV = shutil.which("uv")


@unittest.skipUnless(UV, "uv must be installed to run the uv integration suite")
class UvInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="uv meal test ")
        self.ws = Path(self.tmp.name)
        self.base = self.ws / "skills/diet-tracking-analysis"

    def tearDown(self):
        self.tmp.cleanup()

    def install(self, *flags, env=None, script=INSTALL):
        return subprocess.run([sys.executable, str(script), "--workspace", str(self.ws),
                               "--timezone", "Asia/Tokyo", "--offline", *flags],
                              capture_output=True, text=True, timeout=60, env=env)

    def config(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads((self.base / "runtime.json").read_text())

    def invoke(self, config, *flags, env=None):
        return subprocess.run(config["command"] + ["summary", "--workspace", str(self.ws),
                              "--tz", "Asia/Tokyo", *flags], cwd="/tmp", env=env,
                              capture_output=True, text=True, timeout=30)

    def test_uv_is_default_and_runtime_is_installed_project(self):
        cfg = self.config()
        self.assertEqual(cfg["runner"], "uv")
        self.assertIn(str(self.base), cfg["command"])
        self.assertIn("--locked", cfg["command"])
        self.assertIn("--offline", cfg["command"])
        self.assertTrue(Path(cfg["python"]).is_relative_to(self.base / ".venv"))
        self.assertTrue((self.base / "uv.lock").is_file())

    def test_installed_command_works_without_python_on_path(self):
        cfg = self.config()
        env = {**os.environ, "PATH": "/nonexistent"}
        result = self.invoke(cfg, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])

    def test_stale_lock_fails_without_rewriting_it(self):
        cfg = self.config()
        lock = (self.base / "uv.lock").read_bytes()
        project = self.base / "pyproject.toml"
        project.write_text(project.read_text().replace('version = "4.1.0"', 'version = "4.1.1"'))
        result = self.invoke(cfg)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.base / "uv.lock").read_bytes(), lock)

    def test_uv_missing_is_explicit_failure_not_fallback(self):
        result = self.install(env={**os.environ, "PATH": "/nonexistent"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("uv is missing", json.loads(result.stdout)["error"])
        self.assertEqual(list(self.ws.iterdir()), [])

    def test_dry_run_does_not_create_installed_environment(self):
        result = self.install("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["dry_run"])
        self.assertEqual(list(self.ws.iterdir()), [])

    def test_install_reports_verification_without_seeding_real_meals(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertTrue(out["self_test"]["ok"])
        self.assertEqual(len(out["self_test"]["checks"]), 7)
        self.assertFalse(out["self_test"]["real_workspace_modified"])
        self.assertEqual(out["model_vision"], "not_tested")
        self.assertEqual(list((self.ws / "data/meals").glob("*.json")), [])

    def test_failure_rolls_back_previous_skill(self):
        cfg = self.config()
        original = (self.base / "runtime.json").read_bytes()
        with tempfile.TemporaryDirectory() as commands:
            fake = Path(commands) / "uv"
            fake.write_text("#!/bin/sh\necho 'synthetic sync failure' >&2\nexit 1\n")
            fake.chmod(0o700)
            result = self.install("--replace", env={**os.environ, "PATH": commands})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.base / "runtime.json").read_bytes(), original)
        self.assertEqual(self.invoke(cfg).returncode, 0)

    def test_environment_redirect_is_rejected(self):
        result = self.install(env={**os.environ, "UV_PROJECT_ENVIRONMENT": "/tmp/unrelated-venv"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unset", json.loads(result.stdout)["error"])
        self.assertEqual(list(self.ws.iterdir()), [])

    def test_checkout_can_be_removed_after_install(self):
        # Install using a disposable checkout venv, not the system interpreter.
        with tempfile.TemporaryDirectory(prefix="disposable source ") as src:
            checkout = Path(src)
            (checkout / "scripts").mkdir()
            shutil.copyfile(INSTALL, checkout / "scripts/install-standalone.py")
            shutil.copytree(ROOT / "diet-tracking-analysis", checkout / "diet-tracking-analysis",
                            ignore=shutil.ignore_patterns(".venv", "__pycache__"))
            result = subprocess.run([UV, "run", "--locked", "--offline", "--no-python-downloads",
                        "--project", str(checkout / "diet-tracking-analysis"), "python",
                        str(checkout / "scripts/install-standalone.py"), "--workspace", str(self.ws),
                        "--timezone", "Asia/Tokyo", "--offline"],
                        capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        cfg = json.loads((self.base / "runtime.json").read_text())
        self.assertEqual(self.invoke(cfg).returncode, 0)

    def test_explicit_python_fallback_is_retained(self):
        result = self.install("--runner", "python", env={**os.environ, "PATH": "/nonexistent"})
        self.assertEqual(result.returncode, 0, result.stderr)
        cfg = json.loads((self.base / "runtime.json").read_text())
        self.assertEqual(cfg["runner"], "python")
        self.assertFalse((self.base / ".venv").exists())
        self.assertEqual(self.invoke(cfg).returncode, 0)

    def test_replace_preserves_real_data_and_profiles(self):
        cfg = self.config()
        for name in ("USER.md", "PLAN.md", "AGENTS.md", "data/weight.json"):
            (self.ws / name).write_text("unchanged", encoding="utf-8")
        result = self.install("--replace")
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("USER.md", "PLAN.md", "AGENTS.md", "data/weight.json"):
            self.assertEqual((self.ws / name).read_text(), "unchanged")
        self.assertEqual(self.invoke(cfg).returncode, 0)


if __name__ == "__main__":
    unittest.main()
