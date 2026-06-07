#!/usr/bin/env bash
set -euo pipefail

AGENT_REPO="${AGENT_REPO:-https://github.com/skkeoriw/agent-brain-plugins.git}"
SKILL_REPO="${SKILL_REPO:-https://github.com/skkeoriw/auto-youtube-wiki-skill.git}"
EXPECT_AGENT_COMMIT="${EXPECT_AGENT_COMMIT:-}"
EXPECT_SKILL_COMMIT="${EXPECT_SKILL_COMMIT:-}"
TARGETS=()

usage() {
  cat <<'EOF'
Usage:
  scripts/verify-runtime-repo-versions.sh [options]

Checks remote Runtime machines have the expected local source versions.
This is read-only and does not trigger workflow execution.

Options:
  --target=name|user|host|key_path    runtime SSH target; can be repeated
  --agent-repo=https://...            expected agent-brain-plugins origin
  --skill-repo=https://...            expected auto-youtube-wiki-skill origin
  --expect-agent-commit=abcdef0       expected agent commit; default GitHub main
  --expect-skill-commit=abcdef0       expected skill commit; default GitHub main
  -h, --help                          show this help
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --target=*) TARGETS+=("${1#--target=}"); shift ;;
    --agent-repo=*) AGENT_REPO="${1#--agent-repo=}"; shift ;;
    --skill-repo=*) SKILL_REPO="${1#--skill-repo=}"; shift ;;
    --expect-agent-commit=*) EXPECT_AGENT_COMMIT="${1#--expect-agent-commit=}"; shift ;;
    --expect-skill-commit=*) EXPECT_SKILL_COMMIT="${1#--expect-skill-commit=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }
TARGETS_JSON="$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "${TARGETS[@]}")"

python3 - "$TARGETS_JSON" "$AGENT_REPO" "$SKILL_REPO" "$EXPECT_AGENT_COMMIT" "$EXPECT_SKILL_COMMIT" <<'PY'
import json
import re
import subprocess
import sys
import urllib.parse


targets_json, agent_repo, skill_repo, expect_agent, expect_skill = sys.argv[1:]
try:
    targets = json.loads(targets_json)
except json.JSONDecodeError:
    targets = []


def fail(message):
    print(f"[runtime-repos] ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def short_commit(value):
    return (value or "").strip()[:7]


def ls_remote(repo):
    result = subprocess.run(
        ["git", "ls-remote", repo, "refs/heads/main"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    if result.returncode != 0:
        fail(f"git ls-remote failed for {repo}: {result.stderr[:300]}")
    line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    commit = line.split()[0] if line else ""
    if not commit:
        fail(f"git ls-remote returned no main commit for {repo}")
    return short_commit(commit)


def normalize_origin(origin):
    origin = (origin or "").strip()
    origin = re.sub(r"^https://[^/@]+@github\.com/", "https://github.com/", origin)
    return origin


def parse_target(value):
    parts = value.split("|")
    if len(parts) != 4:
        fail(f"invalid --target value, expected name|user|host|key_path: {value!r}")
    name, user, host, key_path = [part.strip() for part in parts]
    if not name or not user or not host:
        fail(f"invalid --target value: {value!r}")
    return name, user, host, key_path


def parse_kv(text):
    data = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and re.match(r"^[A-Za-z0-9_]+$", key):
            data[key] = value.strip()
    return data


def ssh_query(user, host, key_path):
    script = r'''
set -e
printf 'agent_origin=%s\n' "$(git -C "$HOME/agent-brain-plugins" remote get-url origin)"
printf 'agent_branch=%s\n' "$(git -C "$HOME/agent-brain-plugins" rev-parse --abbrev-ref HEAD)"
printf 'agent_commit=%s\n' "$(git -C "$HOME/agent-brain-plugins" rev-parse --short HEAD)"
printf 'skill_origin=%s\n' "$(git -C "$HOME/auto-youtube-wiki-skill" remote get-url origin)"
printf 'skill_branch=%s\n' "$(git -C "$HOME/auto-youtube-wiki-skill" rev-parse --abbrev-ref HEAD)"
printf 'skill_commit=%s\n' "$(git -C "$HOME/auto-youtube-wiki-skill" rev-parse --short HEAD)"
'''
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no"]
    if key_path:
        cmd.extend(["-i", key_path])
    cmd.extend([f"{user}@{host}", "bash", "-lc", script])
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)


if not targets:
    fail("at least one --target is required")

expected_agent = short_commit(expect_agent) or ls_remote(agent_repo)
expected_skill = short_commit(expect_skill) or ls_remote(skill_repo)
failures = []

for raw in targets:
    name, user, host, key_path = parse_target(raw)
    result = ssh_query(user, host, key_path)
    if result.returncode != 0:
        failures.append(f"{name}: ssh failed: {result.stderr[:300]}")
        continue
    data = parse_kv(result.stdout)
    agent_origin = normalize_origin(data.get("agent_origin", ""))
    skill_origin = normalize_origin(data.get("skill_origin", ""))
    checks = {
        "agent_origin_ok": agent_origin == agent_repo,
        "skill_origin_ok": skill_origin == skill_repo,
        "agent_branch_ok": data.get("agent_branch") == "main",
        "skill_branch_ok": data.get("skill_branch") == "main",
        "agent_commit_ok": short_commit(data.get("agent_commit")) == expected_agent,
        "skill_commit_ok": short_commit(data.get("skill_commit")) == expected_skill,
    }
    bad = [key for key, value in checks.items() if not value]
    if bad:
        failures.append(
            f"{name}: {', '.join(bad)} "
            f"(agent={short_commit(data.get('agent_commit'))}/{expected_agent}, "
            f"skill={short_commit(data.get('skill_commit'))}/{expected_skill})"
        )
        continue
    print(f"[runtime-repos] ok: {name}")
    print(f"[runtime-repos] agent: main/{short_commit(data.get('agent_commit'))} {agent_origin}")
    print(f"[runtime-repos] skill: main/{short_commit(data.get('skill_commit'))} {skill_origin}")

if failures:
    for item in failures:
        print(f"[runtime-repos] ERROR: {item}", file=sys.stderr)
    raise SystemExit(1)

print(f"[runtime-repos] summary: passed={len(targets)} failed=0 total={len(targets)}")
PY
