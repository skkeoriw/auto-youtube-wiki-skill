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

    def _fake_control_plane_verifier(self, tmp_path: Path) -> Path:
        verifier = tmp_path / "verify-tunnel-control-plane.sh"
        verifier.write_text(
            "#!/usr/bin/env bash\n"
            "printf 'CONTROL\\n' >> \"$ARG_FILE\"\n"
            "printf '%s\\n' \"$@\" >> \"$ARG_FILE\"\n"
            "exit 0\n",
            encoding="utf-8",
        )
        verifier.chmod(0o755)
        return verifier

    def _fake_sop_ui_verifier(self, tmp_path: Path) -> Path:
        verifier = tmp_path / "verify-sop-ui-runtime-discovery.sh"
        verifier.write_text(
            "#!/usr/bin/env bash\n"
            "printf 'SOPUI\\n' >> \"$ARG_FILE\"\n"
            "printf '%s\\n' \"$@\" >> \"$ARG_FILE\"\n"
            "exit 0\n",
            encoding="utf-8",
        )
        verifier.chmod(0o755)
        return verifier

    def _run(self, verifier: Path, args_file: Path, *args: str, fail_name: str = ""):
        control_plane_verifier = self._fake_control_plane_verifier(args_file.parent)
        sop_ui_verifier = self._fake_sop_ui_verifier(args_file.parent)
        env = os.environ.copy()
        env.update({
            "VERIFY_RUNTIME_CHANNEL_SCRIPT": str(verifier),
            "VERIFY_TUNNEL_CONTROL_PLANE_SCRIPT": str(control_plane_verifier),
            "VERIFY_SOP_UI_DISCOVERY_SCRIPT": str(sop_ui_verifier),
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
            self.assertEqual(text.count("CONTROL\n"), 1)
            self.assertEqual(text.count("SOPUI\n"), 1)
            self.assertIn("--tunnel-api=https://tunnel-api.chxyka.ccwu.cc\n", text)
            self.assertIn("--ui-url=https://sop-ui-prototype.chxyka.ccwu.cc\n", text)
            self.assertIn("--name=youtube-wiki\n", text)
            self.assertIn("--name=youtube-wiki-168\n", text)
            self.assertIn("--name=youtube-wiki-222\n", text)
            self.assertIn("--expect-runtime=youtube-wiki|youtube-wiki|https://youtube-wiki.chxyka.ccwu.cc\n", text)
            self.assertIn("--expect-runtime=youtube-wiki-168|youtube-wiki-168|https://youtube-wiki-168.chxyka.ccwu.cc\n", text)
            self.assertIn("--expect-runtime=youtube-wiki-222|youtube-wiki-222|https://youtube-wiki-222.chxyka.ccwu.cc\n", text)
            self.assertIn("--expect-auto-domain-source-mode=managed\n", text)
            self.assertIn("--expect-auto-domain-source-commit=testcommit\n", text)
            self.assertIn("--expect-sop-type=runtime-provisioning\n", text)
            self.assertIn("--expect-sop-type=youtube-research-wiki\n", text)
            self.assertIn("--no-options\n", text)
            self.assertIn("passed=3 failed=0 total=3", result.stdout)

    def test_only_filters_runtime_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            verifier, args_file = self._fake_verifier(Path(tmp))
            result = self._run(verifier, args_file, "--only=youtube-wiki-222")

            self.assertEqual(result.returncode, 0, result.stderr)
            text = args_file.read_text(encoding="utf-8")
            self.assertEqual(text.count("CALL\n"), 1)
            self.assertEqual(text.count("CONTROL\n"), 1)
            self.assertEqual(text.count("SOPUI\n"), 1)
            self.assertIn("--name=youtube-wiki-222\n", text)
            self.assertNotIn("--name=youtube-wiki\n", text)
            self.assertIn("--expect-runtime=youtube-wiki-222|youtube-wiki-222|https://youtube-wiki-222.chxyka.ccwu.cc\n", text)
            self.assertIn("passed=1 failed=0 total=1", result.stdout)

    def test_failed_runtime_returns_nonzero_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            verifier, args_file = self._fake_verifier(Path(tmp))
            result = self._run(verifier, args_file, fail_name="youtube-wiki-168")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("passed=2 failed=1 total=3", result.stdout)
            self.assertIn("failed runtimes: youtube-wiki-168", result.stderr)
            text = args_file.read_text(encoding="utf-8")
            self.assertNotIn("SOPUI\n", text)

    def test_no_control_plane_skips_control_plane_verifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            verifier, args_file = self._fake_verifier(Path(tmp))
            result = self._run(verifier, args_file, "--only=youtube-wiki-222", "--no-control-plane")

            self.assertEqual(result.returncode, 0, result.stderr)
            text = args_file.read_text(encoding="utf-8")
            self.assertNotIn("CONTROL\n", text)
            self.assertEqual(text.count("CALL\n"), 1)
            self.assertEqual(text.count("SOPUI\n"), 1)

    def test_no_sop_type_check_skips_sop_type_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            verifier, args_file = self._fake_verifier(Path(tmp))
            result = self._run(verifier, args_file, "--only=youtube-wiki-222", "--no-sop-type-check")

            self.assertEqual(result.returncode, 0, result.stderr)
            text = args_file.read_text(encoding="utf-8")
            self.assertNotIn("--expect-sop-type=", text)

    def test_no_sop_ui_skips_ui_discovery_verifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            verifier, args_file = self._fake_verifier(Path(tmp))
            result = self._run(verifier, args_file, "--only=youtube-wiki-222", "--no-sop-ui")

            self.assertEqual(result.returncode, 0, result.stderr)
            text = args_file.read_text(encoding="utf-8")
            self.assertNotIn("SOPUI\n", text)


if __name__ == "__main__":
    unittest.main()
