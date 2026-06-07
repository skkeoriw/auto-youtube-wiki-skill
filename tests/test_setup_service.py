#!/usr/bin/env python3

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP_SERVICE = ROOT / "scripts" / "setup-service.sh"


def _extract_shell_function(name):
    text = SETUP_SERVICE.read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n", text, flags=re.M | re.S)
    if not match:
        raise AssertionError(f"{name} function not found")
    return match.group(0)


def _run(cmd, cwd=None, env=None):
    result = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result


class SetupServiceManagedSourceTest(unittest.TestCase):
    def test_managed_source_clone_and_update_stays_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = tmp_path / "repo"
            source_dir = tmp_path / "managed-source"
            agent = repo / "skills/auto-domain/agent/agent.js"
            agent.parent.mkdir(parents=True)
            agent.write_text("console.log('v1')\n", encoding="utf-8")

            _run("git init -b main", cwd=repo)
            _run("git config user.email test@example.local && git config user.name test", cwd=repo)
            _run("git add . && git commit -m initial", cwd=repo)

            fn = _extract_shell_function("prepare_auto_domain_source_agent")
            env = os.environ.copy()
            env.update({
                "AUTO_DOMAIN_REPO": str(repo),
                "AUTO_DOMAIN_REF": "main",
                "AUTO_DOMAIN_SOURCE_DIR": str(source_dir),
            })
            first = _run(f"{fn}\nprepare_auto_domain_source_agent", env=env)

            expected_agent = source_dir / "skills/auto-domain/agent/agent.js"
            self.assertEqual(first.stdout.strip(), str(expected_agent))
            self.assertIn("using latest auto-domain source", first.stderr)
            self.assertEqual(_run("git status --short", cwd=source_dir).stdout.strip(), "")

            agent.write_text("console.log('v2')\n", encoding="utf-8")
            _run("git add . && git commit -m update-agent", cwd=repo)
            second = _run(f"{fn}\nprepare_auto_domain_source_agent", env=env)

            self.assertEqual(second.stdout.strip(), str(expected_agent))
            self.assertEqual(
                _run("git log --oneline -1", cwd=source_dir).stdout.strip().split(" ", 1)[1],
                "update-agent",
            )
            self.assertEqual(_run("git status --short", cwd=source_dir).stdout.strip(), "")

    def test_safe_metadata_runner_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            safe_runner = tmp_path / "safe-run.sh"
            unsafe_runner = tmp_path / "unsafe-run.sh"
            safe_runner.write_text(
                "ARGS=(\"--port=$PORT\")\nsetsid node \"$AGENT_JS\" \"${ARGS[@]}\"\n",
                encoding="utf-8",
            )
            unsafe_runner.write_text(
                "ARGS=\"--port=$PORT\"\nsetsid node \"$AGENT_JS\" $ARGS\n",
                encoding="utf-8",
            )

            fn = _extract_shell_function("auto_domain_script_supports_safe_metadata")
            cmd = (
                f"{fn}\n"
                f"auto_domain_script_supports_safe_metadata {safe_runner} && echo safe-ok\n"
                f"auto_domain_script_supports_safe_metadata {unsafe_runner} || echo unsafe-rejected\n"
            )
            result = _run(cmd)

            self.assertIn("safe-ok", result.stdout)
            self.assertIn("unsafe-rejected", result.stdout)

    def test_setup_service_prefers_managed_source_over_legacy_runner_default(self):
        text = SETUP_SERVICE.read_text(encoding="utf-8")

        self.assertIn("AUTO_DOMAIN_REPO=", text)
        self.assertIn("AUTO_DOMAIN_SOURCE_DIR=", text)
        self.assertIn("prepare_auto_domain_source_agent", text)
        self.assertIn('setsid node "$AUTO_DOMAIN_AGENT_JS"', text)
        self.assertIn("using managed latest source instead", text)
        self.assertNotIn("AGENT_URL=", text)


if __name__ == "__main__":
    unittest.main()
