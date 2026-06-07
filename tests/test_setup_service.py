#!/usr/bin/env python3

import os
import re
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from shlex import quote


ROOT = Path(__file__).resolve().parents[1]
SETUP_SERVICE = ROOT / "scripts" / "setup-service.sh"


def _extract_shell_function(name):
    text = SETUP_SERVICE.read_text(encoding="utf-8")
    if name == "build_metadata":
        match = re.search(r"^build_metadata\(\) \{\n.*?^PY\n^\}\n", text, flags=re.M | re.S)
        if not match:
            raise AssertionError(f"{name} function not found")
        return match.group(0)
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
        executable="/bin/bash",
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
        self.assertIn("AUTO_DOMAIN_ALLOW_LOCAL_RUNNER=", text)
        self.assertIn("auto_domain_source", text)
        self.assertIn("prepare_auto_domain_source_agent", text)
        self.assertIn("verify_runtime_channel", text)
        self.assertIn('setsid node "$AUTO_DOMAIN_AGENT_JS"', text)
        self.assertIn("local auto-domain-cli runner ignored", text)
        self.assertIn("using managed latest source instead", text)
        self.assertNotIn("AGENT_URL=", text)

    def test_build_metadata_includes_auto_domain_source_contract(self):
        fn = _extract_shell_function("build_metadata")
        cmd = "\n".join([
            "NAME=youtube-wiki-test",
            "ENDPOINT=https://youtube-wiki-test.example.com",
            "REPO=skkeoriw/wiki-test",
            "RUNTIME_ID=youtube-wiki-test",
            "UI_URL=https://sop-ui-prototype.example.com",
            "AUTO_DOMAIN_SOURCE_MODE=managed",
            "AUTO_DOMAIN_SOURCE_REPO=https://github.com/skkeoriw/auto-domain-cli.git",
            "AUTO_DOMAIN_SOURCE_REF=main",
            "AUTO_DOMAIN_SOURCE_COMMIT=8738556",
            fn,
            "build_metadata",
        ])
        result = _run(cmd)
        metadata = json.loads(result.stdout)

        self.assertEqual(metadata["type"], "sop-runtime")
        self.assertEqual(metadata["runtime_id"], "youtube-wiki-test")
        self.assertEqual(metadata["auto_domain_source"], {
            "mode": "managed",
            "repo": "https://github.com/skkeoriw/auto-domain-cli.git",
            "ref": "main",
            "commit": "8738556",
        })

    def test_verify_runtime_channel_invokes_public_verifier_with_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            args_file = tmp_path / "args.txt"
            verifier = tmp_path / "verify-runtime-channel.sh"
            verifier.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$@\" > \"$ARG_FILE\"\n"
                "exit 0\n",
                encoding="utf-8",
            )
            verifier.chmod(0o755)

            fn = _extract_shell_function("verify_runtime_channel")
            env = os.environ.copy()
            env.update({
                "ARG_FILE": str(args_file),
            })
            cmd = "\n".join([
                f"SCRIPT_DIR={quote(str(tmp_path))}",
                "NAME=youtube-wiki-test",
                "ENDPOINT=https://youtube-wiki-test.example.com",
                "RUNTIME_ID=youtube-wiki-test",
                "REPO=skkeoriw/wiki-test",
                "PORT=18121",
                "AUTO_DOMAIN_SOURCE_MODE=managed",
                "AUTO_DOMAIN_SOURCE_REPO=https://github.com/skkeoriw/auto-domain-cli.git",
                "AUTO_DOMAIN_SOURCE_COMMIT=8738556",
                fn,
                "verify_runtime_channel",
            ])
            result = _run(cmd, env=env)

            self.assertIn("runtime channel metadata verified", result.stdout)
            args = args_file.read_text(encoding="utf-8").splitlines()
            self.assertEqual(args, [
                "--name=youtube-wiki-test",
                "--endpoint=https://youtube-wiki-test.example.com",
                "--expect-runtime-id=youtube-wiki-test",
                "--expect-repo=skkeoriw/wiki-test",
                "--expect-port=18121",
                "--expect-auto-domain-source-mode=managed",
                "--expect-auto-domain-source-repo=https://github.com/skkeoriw/auto-domain-cli.git",
                "--expect-auto-domain-source-commit=8738556",
            ])


if __name__ == "__main__":
    unittest.main()
