#!/usr/bin/env python3

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = ROOT / "scripts" / "verify-runtime-repo-versions.sh"


class VerifyRuntimeRepoVersionsTest(unittest.TestCase):
    def fake_ssh(self, tmp_path: Path, agent_commit="abc1234", skill_commit="def5678", token_origin=False):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        ssh = bin_dir / "ssh"
        agent_origin = (
            "https://x-access-token:SECRET@github.com/skkeoriw/agent-brain-plugins.git"
            if token_origin else
            "https://github.com/skkeoriw/agent-brain-plugins.git"
        )
        ssh.write_text(
            "#!/usr/bin/env bash\n"
            f"echo 'agent_origin={agent_origin}'\n"
            "echo 'agent_branch=main'\n"
            f"echo 'agent_commit={agent_commit}'\n"
            "echo 'skill_origin=https://github.com/skkeoriw/auto-youtube-wiki-skill.git'\n"
            "echo 'skill_branch=main'\n"
            f"echo 'skill_commit={skill_commit}'\n",
            encoding="utf-8",
        )
        ssh.chmod(0o755)
        return bin_dir

    def fake_sshpass(self, bin_dir: Path):
        sshpass = bin_dir / "sshpass"
        sshpass.write_text(
            "#!/usr/bin/env bash\n"
            "if [ -z \"$SSHPASS\" ]; then echo 'missing SSHPASS' >&2; exit 9; fi\n"
            "if [ \"$1\" = '-e' ]; then shift; fi\n"
            "exec \"$@\"\n",
            encoding="utf-8",
        )
        sshpass.chmod(0o755)

    def run_script(self, bin_dir: Path, *args: str):
        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        return subprocess.run(
            [
                str(VERIFY_SCRIPT),
                "--target=runtime-a|user|host|/tmp/key",
                "--expect-agent-commit=abc1234",
                "--expect-skill-commit=def5678",
                *args,
            ],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_verify_runtime_repo_versions_success_and_redacts_token_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = self.fake_ssh(Path(tmp), token_origin=True)
            result = self.run_script(bin_dir)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[runtime-repos] ok: runtime-a", result.stdout)
        self.assertIn("https://github.com/skkeoriw/agent-brain-plugins.git", result.stdout)
        self.assertNotIn("SECRET", result.stdout)
        self.assertNotIn("SECRET", result.stderr)

    def test_verify_runtime_repo_versions_rejects_stale_agent_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = self.fake_ssh(Path(tmp), agent_commit="old0000")
            result = self.run_script(bin_dir)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("agent_commit_ok", result.stderr)
        self.assertIn("old0000/abc1234", result.stderr)

    def test_verify_runtime_repo_versions_supports_password_env_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = self.fake_ssh(Path(tmp))
            self.fake_sshpass(bin_dir)
            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env["RUNTIME_TEST_PASSWORD"] = "secret-for-test"
            result = subprocess.run(
                [
                    str(VERIFY_SCRIPT),
                    "--target=runtime-a|user|host||RUNTIME_TEST_PASSWORD",
                    "--expect-agent-commit=abc1234",
                    "--expect-skill-commit=def5678",
                ],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[runtime-repos] ok: runtime-a", result.stdout)
        self.assertNotIn("secret-for-test", result.stdout)
        self.assertNotIn("secret-for-test", result.stderr)

    def test_verify_runtime_repo_versions_rejects_missing_password_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = self.fake_ssh(Path(tmp))
            self.fake_sshpass(bin_dir)
            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env.pop("RUNTIME_TEST_PASSWORD", None)
            result = subprocess.run(
                [
                    str(VERIFY_SCRIPT),
                    "--target=runtime-a|user|host||RUNTIME_TEST_PASSWORD",
                    "--expect-agent-commit=abc1234",
                    "--expect-skill-commit=def5678",
                ],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("password env var is empty or missing", result.stderr)


if __name__ == "__main__":
    unittest.main()
