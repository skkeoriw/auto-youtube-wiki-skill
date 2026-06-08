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

    def _fake_inventory_verifier(self, tmp_path: Path) -> Path:
        verifier = tmp_path / "verify-runtime-inventory.sh"
        verifier.write_text(
            "#!/usr/bin/env bash\n"
            "printf 'INVENTORY\\n' >> \"$ARG_FILE\"\n"
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

    def _fake_repo_verifier(self, tmp_path: Path) -> Path:
        verifier = tmp_path / "verify-runtime-repo-versions.sh"
        verifier.write_text(
            "#!/usr/bin/env bash\n"
            "printf 'REPOS\\n' >> \"$ARG_FILE\"\n"
            "printf '%s\\n' \"$@\" >> \"$ARG_FILE\"\n"
            "exit 0\n",
            encoding="utf-8",
        )
        verifier.chmod(0o755)
        return verifier

    def _run(self, verifier: Path, args_file: Path, *args: str, fail_name: str = ""):
        control_plane_verifier = self._fake_control_plane_verifier(args_file.parent)
        inventory_verifier = self._fake_inventory_verifier(args_file.parent)
        sop_ui_verifier = self._fake_sop_ui_verifier(args_file.parent)
        repo_verifier = self._fake_repo_verifier(args_file.parent)
        env = os.environ.copy()
        env.update({
            "VERIFY_RUNTIME_CHANNEL_SCRIPT": str(verifier),
            "VERIFY_TUNNEL_CONTROL_PLANE_SCRIPT": str(control_plane_verifier),
            "VERIFY_RUNTIME_INVENTORY_SCRIPT": str(inventory_verifier),
            "VERIFY_SOP_UI_DISCOVERY_SCRIPT": str(sop_ui_verifier),
            "VERIFY_RUNTIME_REPO_VERSIONS_SCRIPT": str(repo_verifier),
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
            self.assertEqual(text.count("INVENTORY\n"), 1)
            self.assertEqual(text.count("SOPUI\n"), 1)
            self.assertNotIn("REPOS\n", text)
            self.assertIn("--tunnel-api=https://tunnel-api.chxyka.ccwu.cc\n", text)
            self.assertIn("--ui-url=https://sop-ui-prototype.chxyka.ccwu.cc\n", text)
            self.assertIn("--name=youtube-wiki\n", text)
            self.assertIn("--name=youtube-wiki-168\n", text)
            self.assertIn("--name=youtube-wiki-222\n", text)
            self.assertIn("--expect-runtime=youtube-wiki|youtube-wiki|https://youtube-wiki.chxyka.ccwu.cc\n", text)
            self.assertIn("--expect-runtime=youtube-wiki-168|youtube-wiki-168|https://youtube-wiki-168.chxyka.ccwu.cc\n", text)
            self.assertIn("--expect-runtime=youtube-wiki-222|youtube-wiki-222|https://youtube-wiki-222.chxyka.ccwu.cc\n", text)
            self.assertIn("--expect-auto-domain-source-mode=managed\n", text)
            self.assertIn("--expect-auto-domain-source-ref=main\n", text)
            self.assertIn("--expect-auto-domain-source-commit=testcommit\n", text)
            self.assertEqual(text.count("--expect-ui-url=https://sop-ui-prototype.chxyka.ccwu.cc\n"), 4)
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
            self.assertEqual(text.count("INVENTORY\n"), 1)
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
            self.assertIn("INVENTORY\n", text)

    def test_no_control_plane_skips_control_plane_verifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            verifier, args_file = self._fake_verifier(Path(tmp))
            result = self._run(verifier, args_file, "--only=youtube-wiki-222", "--no-control-plane")

            self.assertEqual(result.returncode, 0, result.stderr)
            text = args_file.read_text(encoding="utf-8")
            self.assertNotIn("CONTROL\n", text)
            self.assertIn("INVENTORY\n", text)
            self.assertEqual(text.count("CALL\n"), 1)
            self.assertEqual(text.count("SOPUI\n"), 1)

    def test_no_inventory_skips_inventory_verifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            verifier, args_file = self._fake_verifier(Path(tmp))
            result = self._run(verifier, args_file, "--only=youtube-wiki-222", "--no-inventory")

            self.assertEqual(result.returncode, 0, result.stderr)
            text = args_file.read_text(encoding="utf-8")
            self.assertNotIn("INVENTORY\n", text)

    def test_strict_inventory_forwards_strict_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            verifier, args_file = self._fake_verifier(Path(tmp))
            result = self._run(verifier, args_file, "--only=youtube-wiki-222", "--strict-inventory")

            self.assertEqual(result.returncode, 0, result.stderr)
            text = args_file.read_text(encoding="utf-8")
            self.assertIn("--strict\n", text)

    def test_no_sop_type_check_skips_sop_type_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            verifier, args_file = self._fake_verifier(Path(tmp))
            result = self._run(verifier, args_file, "--only=youtube-wiki-222", "--no-sop-type-check")

            self.assertEqual(result.returncode, 0, result.stderr)
            text = args_file.read_text(encoding="utf-8")
            self.assertNotIn("--expect-sop-type=", text)

    def test_source_commit_latest_resolves_git_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_repo = tmp_path / "auto-domain-cli"
            subprocess.run(["git", "init", "-q", str(source_repo)], check=True)
            subprocess.run(["git", "-C", str(source_repo), "checkout", "-q", "-b", "main"], check=True)
            (source_repo / "README.md").write_text("auto-domain source\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(source_repo), "add", "README.md"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(source_repo),
                    "-c",
                    "user.name=test",
                    "-c",
                    "user.email=test@example.local",
                    "commit",
                    "-q",
                    "-m",
                    "init",
                ],
                check=True,
            )
            expected_commit = subprocess.check_output(
                ["git", "-C", str(source_repo), "rev-parse", "--short", "HEAD"],
                text=True,
            ).strip()

            verifier, args_file = self._fake_verifier(tmp_path)
            result = self._run(
                verifier,
                args_file,
                "--only=youtube-wiki-222",
                f"--source-repo={source_repo}",
                "--source-ref=main",
                "--source-commit=latest",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"resolved auto-domain source latest: {source_repo}@main -> {expected_commit}", result.stdout)
            text = args_file.read_text(encoding="utf-8")
            self.assertIn(f"--expect-auto-domain-source-repo={source_repo}\n", text)
            self.assertIn("--expect-auto-domain-source-ref=main\n", text)
            self.assertIn(f"--expect-auto-domain-source-commit={expected_commit}\n", text)

    def test_no_sop_ui_skips_ui_discovery_verifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            verifier, args_file = self._fake_verifier(Path(tmp))
            result = self._run(verifier, args_file, "--only=youtube-wiki-222", "--no-sop-ui")

            self.assertEqual(result.returncode, 0, result.stderr)
            text = args_file.read_text(encoding="utf-8")
            self.assertNotIn("SOPUI\n", text)

    def test_repo_version_check_invokes_repo_verifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            verifier, args_file = self._fake_verifier(Path(tmp))
            result = self._run(
                verifier,
                args_file,
                "--only=youtube-wiki-222",
                "--repo-version-check",
                "--repo-target=youtube-wiki-222|runtime|34.29.222.183|/tmp/key",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            text = args_file.read_text(encoding="utf-8")
            self.assertIn("REPOS\n", text)
            self.assertIn("--target=youtube-wiki-222|runtime|34.29.222.183|/tmp/key\n", text)


if __name__ == "__main__":
    unittest.main()
