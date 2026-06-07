#!/usr/bin/env python3

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FLEET_SCRIPT = ROOT / "scripts" / "verify-runtime-fleet.sh"


class VerifyRuntimeFleetTest(unittest.TestCase):
    def _fake_verifier(self, tmp_path: Path, fail_name: str = "") -> tuple[Path, Path]:
        args_file = tmp_path / "args.txt"
        verifier = tmp_path / "verify-runtime-channel.sh"
        verifier.write_text(
            "#!/usr/bin/env bash\n"
            "printf 'CALL\\n' >> \"$ARG_FILE\"\n"
            "printf '%s\\n' \"$@\" >> \"$ARG_FILE\"\n"
            "name=''\n"
            "for arg in \"$@\"; do\n"
            "  case \"$arg\" in --name=*) name=\"${arg#--name=}\" ;; esac\n"
            "done\n"
            "if [ -n \"$FAIL_NAME\" ] && [ \"$name\" = \"$FAIL_NAME\" ]; then exit 9; fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        verifier.chmod(0o755)
        return verifier, args_file

    def _run(self, verifier: Path, args_file: Path, *args: str, fail_name: str = ""):
        env = os.environ.copy()
        env.update({
            "VERIFY_RUNTIME_CHANNEL_SCRIPT": str(verifier),
            "ARG_FILE": str(args_file),
            "FAIL_NAME": fail_name,
        })
        return subprocess.run(
            [str(FLEET_SCRIPT), *args],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_default_fleet_passes_expected_source_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            verifier, args_file = self._fake_verifier(Path(tmp))
            result = self._run(verifier, args_file, "--source-commit=testcommit", "--no-options")

            self.assertEqual(result.returncode, 0, result.stderr)
            text = args_file.read_text(encoding="utf-8")
            self.assertEqual(text.count("CALL\n"), 3)
            self.assertIn("--name=youtube-wiki\n", text)
            self.assertIn("--name=youtube-wiki-168\n", text)
            self.assertIn("--name=youtube-wiki-222\n", text)
            self.assertIn("--expect-auto-domain-source-mode=managed\n", text)
            self.assertIn("--expect-auto-domain-source-commit=testcommit\n", text)
            self.assertIn("--no-options\n", text)
            self.assertIn("passed=3 failed=0 total=3", result.stdout)

    def test_only_filters_runtime_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            verifier, args_file = self._fake_verifier(Path(tmp))
            result = self._run(verifier, args_file, "--only=youtube-wiki-222")

            self.assertEqual(result.returncode, 0, result.stderr)
            text = args_file.read_text(encoding="utf-8")
            self.assertEqual(text.count("CALL\n"), 1)
            self.assertIn("--name=youtube-wiki-222\n", text)
            self.assertNotIn("--name=youtube-wiki\n", text)
            self.assertIn("passed=1 failed=0 total=1", result.stdout)

    def test_failed_runtime_returns_nonzero_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            verifier, args_file = self._fake_verifier(Path(tmp))
            result = self._run(verifier, args_file, fail_name="youtube-wiki-168")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("passed=2 failed=1 total=3", result.stdout)
            self.assertIn("failed runtimes: youtube-wiki-168", result.stderr)


if __name__ == "__main__":
    unittest.main()
