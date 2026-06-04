import http.server
import hashlib
import json
import mimetypes
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import yaml

PORT = int(os.environ.get("BRIDGE_PORT", "18789"))
SCRIPT = os.environ.get("BRIDGE_SCRIPT", "")
REGISTRY_PATH = Path(os.environ.get("SOP_REGISTRY_PATH", str(Path.home() / ".sop" / "registry.json"))).expanduser()


def json_response(handler, status, data):
    body = json.dumps(data, ensure_ascii=False, indent=2).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization")
    handler.end_headers()
    handler.wfile.write(body)


def text_response(handler, status, text):
    body = text.encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def wiki_base():
    return Path(os.environ.get("YOUTUBE_WIKI_BASE", str(Path.home() / "wiki"))).expanduser()


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def read_yaml(path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


TEXT_FORMATS = {
    ".md": "markdown",
    ".txt": "text",
    ".json": "json",
    ".jsonl": "jsonl",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".csv": "csv",
    ".log": "log",
}


def safe_artifact_path(wiki_path, relative_path):
    """Resolve an artifact path while preventing reads outside the instance."""
    base = Path(wiki_path).expanduser().resolve()
    candidate = (base / str(relative_path)).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate


def artifact_type(node_id, output_name, path):
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return "image.cover" if "cover" in output_name else "image"
    if output_name in {"reports", "analysis_file"}:
        return "research.report" if output_name == "reports" else "research.analysis"
    if output_name == "transcript_file":
        return "research.transcript"
    if output_name == "mindmaps":
        return "research.mindmap"
    if output_name in {"pages", "index"}:
        return "wiki.page"
    if node_id == "tg-notify":
        return "notification.archive"
    return "file"


def artifact_record(sop, node_id, output_name, path, resolution):
    base = Path(sop["wiki_local_path"]).expanduser().resolve()
    try:
        relative = path.resolve().relative_to(base).as_posix()
    except ValueError:
        return None
    stat = path.stat()
    suffix = path.suffix.lower()
    record = {
        "id": hashlib.sha256(f"{node_id}:{output_name}:{relative}".encode()).hexdigest()[:16],
        "producer": node_id,
        "output": output_name,
        "type": artifact_type(node_id, output_name, path),
        "format": TEXT_FORMATS.get(suffix, suffix.lstrip(".") or "binary"),
        "path": relative,
        "title": path.name,
        "size": stat.st_size,
        "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        "tags": ["wiki-source"] if output_name in {"reports", "analysis_file", "transcript_file"} else [],
        "metadata": {},
        "resolution": resolution,
    }
    if suffix in TEXT_FORMATS and stat.st_size <= 1024 * 1024:
        try:
            record["preview"] = path.read_text(encoding="utf-8", errors="replace")[:16000]
            record["preview_truncated"] = stat.st_size > len(record["preview"].encode("utf-8"))
        except OSError:
            pass
    return record


def run_context(sop, pipeline_id):
    wiki = Path(sop["wiki_local_path"])
    candidates = [wiki / "raw" / "pipeline-runs" / pipeline_id / "context.json"]
    for path in candidates:
        data = read_json(path)
        if not isinstance(data, dict):
            continue
        return data
    run_file = wiki / "raw" / "pipeline-runs" / pipeline_id / "run.json"
    return read_json(run_file) or {}


def normalized_contract(value, direction):
    result = {}
    if not isinstance(value, dict):
        return result
    for name, spec in value.items():
        if isinstance(spec, dict):
            result[name] = dict(spec)
        elif direction == "input":
            result[name] = {"from": spec, "required": True}
        elif str(spec).startswith("context."):
            result[name] = {"from": spec, "type": "string"}
        else:
            result[name] = {"path": spec, "type": "files" if "*" in str(spec) else "file"}
    return result


def context_output_paths(node_id, output_name, context):
    if node_id == "youtube-fetch" and output_name == "metadata_file":
        return [context.get("stage_b_fetch", {}).get("meta_file", "")]
    if node_id == "notebooklm-research":
        files = context.get("stage_b", {}).get("output_files", [])
        marker = "notebooklm-analysis" if output_name == "reports" else "notebooklm-mindmaps"
        return [path for path in files if marker in str(path)]
    if node_id == "wiki-build":
        if output_name == "pages":
            return context.get("stage_c", {}).get("file_paths", [])
        if output_name == "index":
            return ["index.md"]
    return []


def resolve_output_artifacts(sop, pipeline_id, node_id, output_name, spec, context, run_id="",
                             include_context=True, include_pattern=True):
    wiki = Path(sop["wiki_local_path"]).expanduser().resolve()
    paths = []
    if include_context:
        for relative in context_output_paths(node_id, output_name, context):
            path = safe_artifact_path(wiki, relative)
            if path and path.is_file():
                paths.append((path, "context"))

    pattern = spec.get("path", "") if isinstance(spec, dict) else str(spec)
    pattern = pattern.replace("{pipeline_id}", pipeline_id).replace("{run_id}", run_id or "*")
    if include_pattern and pattern and not Path(pattern).is_absolute() and ".." not in Path(pattern).parts:
        if pattern.endswith("/**"):
            pattern += "/*"
        for path in wiki.glob(pattern):
            if path.is_file() and safe_artifact_path(wiki, path.relative_to(wiki)):
                paths.append((path, "pattern"))

    seen = set()
    artifacts = []
    for path, resolution in paths:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        record = artifact_record(sop, node_id, output_name, path, resolution)
        if record:
            artifacts.append(record)
    return artifacts


def resolve_context_value(context, source):
    value = context
    for key in str(source).split(".")[1:]:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def node_runtime_detail(sop, pipeline_id, node_id):
    wiki = Path(sop["wiki_local_path"])
    node_file = wiki / "raw" / "pipeline-runs" / pipeline_id / "nodes" / f"{node_id}.json"
    state = read_json(node_file) or {}
    config = (sop.get("nodes") or {}).get(node_id) or {}
    context = run_context(sop, pipeline_id)

    declared_inputs = normalized_contract(config.get("inputs", state.get("inputs", {})), "input")
    optional_inputs = normalized_contract(config.get("optional_inputs", state.get("optional_inputs", {})), "input")
    for spec in optional_inputs.values():
        spec["required"] = False
    declared_outputs = normalized_contract(config.get("outputs", state.get("outputs", {})), "output")

    has_recorded_outputs = isinstance(state.get("actual_outputs"), dict)
    actual_outputs = dict(state.get("actual_outputs")) if has_recorded_outputs else {}
    artifacts = []
    if has_recorded_outputs:
        for name, paths in actual_outputs.items():
            for relative in paths if isinstance(paths, list) else []:
                path = safe_artifact_path(sop["wiki_local_path"], relative)
                if path and path.is_file():
                    record = artifact_record(sop, node_id, name, path, "recorded")
                    if record:
                        artifacts.append(record)
    else:
        for name, spec in declared_outputs.items():
            if spec.get("from", "").startswith("context."):
                actual_outputs[name] = resolve_context_value(context, spec["from"])
                continue
            records = resolve_output_artifacts(
                sop, pipeline_id, node_id, name, spec, context, state.get("run_id", ""),
                include_pattern=False,
            )
            actual_outputs[name] = [record["path"] for record in records]
            artifacts.extend(records)

    # Only compute discovered candidates for historical runs that have no recorded
    # actual_outputs. For runs with a proper Run Workspace, actual_outputs is
    # authoritative and the glob scan adds no value — and grows without bound.
    actual_paths = {artifact["path"] for artifact in artifacts}
    discovered_candidates = []
    if not has_recorded_outputs:
        _CANDIDATE_LIMIT = 10
        for name, spec in declared_outputs.items():
            if len(discovered_candidates) >= _CANDIDATE_LIMIT:
                break
            for candidate in resolve_output_artifacts(
                sop, pipeline_id, node_id, name, spec, context, state.get("run_id", ""),
                include_context=False,
            ):
                if candidate["path"] not in actual_paths:
                    candidate["ownership"] = "unconfirmed"
                    discovered_candidates.append(candidate)
                    if len(discovered_candidates) >= _CANDIDATE_LIMIT:
                        break

    resolved_inputs = {}
    all_inputs = {**declared_inputs, **optional_inputs}
    for name, spec in all_inputs.items():
        source = str(spec.get("from", ""))
        if source.startswith("context."):
            resolved_inputs[name] = resolve_context_value(context, source)
            continue
        parts = source.split(".outputs.", 1)
        if len(parts) == 2:
            upstream = node_runtime_detail(sop, pipeline_id, parts[0])
            resolved_inputs[name] = upstream.get("actual_outputs", {}).get(parts[1], [])
        else:
            resolved_inputs[name] = None

    missing = [
        name for name in declared_outputs
        if actual_outputs.get(name) is None or actual_outputs.get(name) == "" or actual_outputs.get(name) == []
    ]
    recorded_validation = state.get("validation") if isinstance(state.get("validation"), dict) else {}
    validation_status = recorded_validation.get("status") or ("passed" if not missing else "warning")
    return {
        **state,
        "pipeline_id": state.get("pipeline_id", pipeline_id),
        "node_id": state.get("node_id", node_id),
        "status": state.get("status", "waiting"),
        "executor": config.get("executor") or {
            "type": "skill",
            "skill": config.get("skill", ""),
            "webhook_route": config.get("webhook_route", config.get("route", "")),
        },
        "declared_inputs": declared_inputs,
        "resolved_inputs": resolved_inputs,
        "declared_outputs": declared_outputs,
        "actual_outputs": actual_outputs,
        "artifacts": artifacts,
        "discovered_candidates": discovered_candidates,
        "validation": {
            "status": validation_status,
            "missing_outputs": recorded_validation.get("missing_outputs", missing),
            "unexpected_outputs": recorded_validation.get("unexpected_outputs", []),
        },
    }


def node_static_config(sop, node_id):
    """Return static node configuration from sop.yaml, independent of any run."""
    nodes = sop.get("nodes") or {}
    config = nodes.get(node_id)
    if config is None:
        return None

    plugin_dir = Path(os.environ.get(
        "YOUTUBE_WIKI_PLUGIN_DIR",
        str(Path.home() / "agent-brain-plugins" / "youtube-wiki"),
    )).expanduser()
    skills_dir = plugin_dir / "skills"

    # Resolve skill script path
    skill_name = config.get("skill") or config.get("webhook_route") or node_id
    skill_dir = skills_dir / f"sop-{node_id}"
    if not skill_dir.exists():
        skill_dir = skills_dir / skill_name
    script_candidates = [
        skill_dir / "scripts" / f"run_{node_id.replace('-', '_')}.sh",
        skill_dir / "scripts" / f"run_{node_id}.sh",
    ]
    skill_script = next((str(p.relative_to(plugin_dir.parent)) for p in script_candidates if p.exists()), None)

    # Read SKILL.md summary (first 800 chars)
    skill_readme = None
    for readme_name in ("SKILL.md", "README.md"):
        readme_path = skill_dir / readme_name
        if readme_path.exists():
            try:
                skill_readme = readme_path.read_text(encoding="utf-8")[:800]
            except OSError:
                pass
            break

    return {
        "node_id": node_id,
        "title": config.get("title", node_id),
        "mode": config.get("mode", "blocking"),
        "needs": config.get("needs") or [],
        "executor": {
            "type": config.get("executor", {}).get("type", "skill") if isinstance(config.get("executor"), dict) else "skill",
            "skill": config.get("skill", ""),
            "webhook_route": config.get("webhook_route", ""),
        },
        "inputs": config.get("inputs", {}),
        "outputs": config.get("outputs", {}),
        "optional_inputs": config.get("optional_inputs", {}),
        "infra": config.get("infra", {"tg_notify": True, "log_record": True}),
        "params": config.get("params") or {},
        "skill_script": skill_script,
        "skill_readme": skill_readme,
    }


def read_registry():
    data = read_json(REGISTRY_PATH) or {}
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("instances"), list):
        data["instances"] = []
    return data


def scanned_sops():
    sops = []
    for sop_file in sorted(wiki_base().glob("*/sop.yaml")):
        sop = read_yaml(sop_file)
        if not sop:
            continue
        raw_wiki_path = Path(str(sop.get("wiki_local_path") or sop_file.parent)).expanduser()
        wiki_path = raw_wiki_path if raw_wiki_path.is_absolute() else sop_file.parent
        sop_id = sop.get("id") or sop.get("name") or sop_file.parent.name
        nodes = sop.get("nodes") if isinstance(sop.get("nodes"), dict) else {}
        if not nodes:
            previous = ""
            for stage in sop.get("pipeline", []):
                if not isinstance(stage, dict) or not stage.get("stage"):
                    continue
                node_id = stage["stage"]
                nodes[node_id] = {
                    "title": node_id,
                    "mode": "manual" if node_id == "retry" else "blocking",
                    "needs": [previous] if previous and node_id != "retry" else [],
                    "webhook_route": stage.get("webhook_route", ""),
                    "trigger": {"type": "file", "path": stage.get("trigger", "")},
                }
                if node_id != "retry":
                    previous = node_id
        sops.append({
            "id": sop_id,
            "raw_id": sop_id,
            "name": sop.get("name", sop_id),
            "title": sop.get("title", sop.get("name", sop_id)),
            "version": sop.get("version", ""),
            "repo": sop.get("repo", ""),
            "wiki_dir": sop_file.parent.name,
            "wiki_local_path": str(wiki_path),
            "sop_file": str(sop_file),
            "nodes": nodes,
        })
    counts = {}
    for sop in sops:
        counts[sop["id"]] = counts.get(sop["id"], 0) + 1
    for sop in sops:
        if counts.get(sop["id"], 0) > 1:
            sop["id"] = sop["wiki_dir"]
    return sops


def sop_from_instance(runtime, instance):
    wiki_path = Path(str(instance.get("local_path", ""))).expanduser()
    sop_file = wiki_path / "sop.yaml"
    sop = read_yaml(sop_file)
    if not sop:
        return None
    nodes = sop.get("nodes") if isinstance(sop.get("nodes"), dict) else {}
    if not nodes:
        previous = ""
        for stage in sop.get("pipeline", []):
            if not isinstance(stage, dict) or not stage.get("stage"):
                continue
            node_id = stage["stage"]
            nodes[node_id] = {
                "title": node_id,
                "mode": "manual" if node_id == "retry" else "blocking",
                "needs": [previous] if previous and node_id != "retry" else [],
                "webhook_route": stage.get("webhook_route", ""),
                "trigger": {"type": "file", "path": stage.get("trigger", "")},
            }
            if node_id != "retry":
                previous = node_id
    instance_id = instance.get("instance_id") or wiki_path.name
    return {
        "id": instance_id,
        "instance_id": instance_id,
        "raw_id": sop.get("id") or sop.get("name") or instance_id,
        "sop_type": instance.get("sop_type") or sop.get("id") or sop.get("name", ""),
        "name": sop.get("name", instance_id),
        "title": sop.get("title", sop.get("name", instance_id)),
        "version": sop.get("version", ""),
        "repo": instance.get("repo") or sop.get("repo", ""),
        "wiki_dir": wiki_path.name,
        "wiki_local_path": str(wiki_path),
        "sop_file": str(sop_file),
        "nodes": nodes,
        "enabled": bool(instance.get("enabled", True)),
        "runtime_id": runtime.get("runtime_id", ""),
        "channel_name": runtime.get("channel_name", ""),
        "channel_url": runtime.get("channel_url", ""),
        "spi_base_url": runtime.get("spi_base_url", ""),
        "created_at": instance.get("created_at", ""),
        "updated_at": instance.get("updated_at", ""),
    }


def load_sops():
    registry = read_registry()
    sops = []
    for instance in registry.get("instances", []):
        if not isinstance(instance, dict) or not instance.get("enabled", True):
            continue
        sop = sop_from_instance(registry, instance)
        if sop:
            sops.append(sop)
    return sops


def find_sop(sop_id):
    for sop in load_sops():
        if sop_id in {sop["id"], sop["raw_id"], sop["name"], sop["wiki_dir"], sop.get("repo", "")}:
            return sop
    return None


def sop_manifest():
    registry = read_registry()
    return {
        "runtime": registry.get("runtime_id", "youtube-wiki"),
        "runtime_id": registry.get("runtime_id", "youtube-wiki"),
        "channel": {
            "name": registry.get("channel_name", ""),
            "url": registry.get("channel_url", ""),
            "spi_base_url": registry.get("spi_base_url", ""),
        },
        "registry_path": str(REGISTRY_PATH),
        "sops": [
            {
                "id": sop["id"],
                "instance_id": sop["instance_id"],
                "sop_type": sop["sop_type"],
                "title": sop["title"],
                "version": sop["version"],
                "repo": sop["repo"],
                "wiki_local_path": sop["wiki_local_path"],
                "dag_url": f"/api/sop/{sop['id']}/dag",
                "runs_url": f"/api/sop/{sop['id']}/runs",
            }
            for sop in load_sops()
        ],
    }


def sop_instances():
    manifest = sop_manifest()
    return {
        "runtime_id": manifest["runtime_id"],
        "channel": manifest["channel"],
        "instances": manifest["sops"],
    }


def sop_dag(sop):
    nodes = []
    edges = []
    for node_id, node in (sop.get("nodes") or {}).items():
        nodes.append({
            "id": node_id,
            "title": node.get("title", node_id),
            "mode": node.get("mode", "blocking"),
            "webhook_route": node.get("webhook_route", node.get("route", "")),
            "inputs": node.get("inputs", {}),
            "outputs": node.get("outputs", {}),
            "optional_inputs": node.get("optional_inputs", {}),
        })
        for need in node.get("needs") or []:
            edges.append({"source": need, "target": node_id})
    return {"sop_id": sop["id"], "nodes": nodes, "edges": edges}


def run_files(sop):
    base = Path(sop["wiki_local_path"]) / "raw" / "pipeline-runs"
    if not base.exists():
        return []
    return sorted(base.glob("*/run.json"), key=lambda f: f.stat().st_mtime, reverse=True)


def sop_runs(sop, query=None):
    query = query or {}
    try:
        limit = max(1, min(200, int((query.get("limit") or ["80"])[0])))
    except Exception:
        limit = 80
    status_filter = (query.get("status") or [""])[0]
    runs = []
    for run_file in run_files(sop):
        data = read_json(run_file)
        if not data:
            continue
        # Guarantee pipeline_id is always present; derive from directory name if missing.
        if not data.get("pipeline_id"):
            data["pipeline_id"] = run_file.parent.name
        if not status_filter or data.get("status") == status_filter:
            runs.append(data)
        if len(runs) >= limit:
            break
    return {"sop_id": sop["id"], "runs": runs}


def trigger_sop(sop, body):
    repo = body.get("repo") or sop.get("repo")
    url = (body.get("input") or {}).get("url") or body.get("url")
    if not repo or not url:
        return 400, {"status": "error", "message": "repo and input.url are required"}
    env = {**os.environ, "PATH": f"{Path.home() / '.local/bin'}:{Path.home() / 'bin'}:{os.environ.get('PATH', '')}"}
    command = ["youtube-wiki", "trigger", "--repo", repo, "--wiki-path", sop["wiki_local_path"], "--url", url]
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=90)
    if result.returncode != 0:
        return 500, {"status": "error", "message": result.stderr[-1200:] or result.stdout[-1200:]}
    start = result.stdout.find("{")
    end = result.stdout.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(result.stdout[start:end + 1])
        except Exception:
            data = {"status": "triggered", "raw": result.stdout}
    else:
        data = {"status": "triggered", "raw": result.stdout}
    if data.get("pipeline_id"):
        data["status_url"] = f"/api/sop/{sop['id']}/runs/{data['pipeline_id']}"
    return 200, data


def _now_iso_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_run_event(run_dir, event_type, **kwargs):
    events_file = run_dir / "events.jsonl"
    event = {"event": event_type, "ts": _now_iso_utc(), **kwargs}
    try:
        with open(events_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass


def cancel_run(sop, pipeline_id, reason="用户取消"):
    """Cancel a pipeline run.

    Bug fixes vs v1:
    1. Only writes cancelled flag to pipeline-context.json when the
       context's pipeline_id matches the requested pipeline_id.
       Previously it blindly overwrote the shared context, cancelling
       whatever happened to be running.
    2. Only updates run.json if the run workspace already exists.
       Previously it created directories for non-existent pipelines,
       polluting the runs list with fake entries.
    """
    wiki = Path(sop["wiki_local_path"])
    run_dir = wiki / "raw" / "pipeline-runs" / pipeline_id
    run_file = run_dir / "run.json"
    now = _now_iso_utc()

    # Require the run workspace to exist before doing anything.
    if not run_file.exists():
        return None, {"status": "error", "message": f"pipeline {pipeline_id!r} not found"}

    run_data = read_json(run_file) or {}
    if run_data.get("status") in {"done", "cancelled"}:
        return run_data.get("status"), {
            "status": run_data["status"],
            "pipeline_id": pipeline_id,
            "message": f"pipeline already {run_data['status']}",
        }

    # Only set the legacy cancel flag when the active context matches.
    ctx_file = wiki / "raw" / "pipeline-context.json"
    ctx = read_json(ctx_file) or {}
    ctx_pid = ctx.get("pipeline_id")
    if ctx_pid is None or ctx_pid == pipeline_id:
        ctx["cancelled"] = True
        ctx["cancel_reason"] = reason
        ctx_file.write_text(json.dumps(ctx, ensure_ascii=False, indent=2))

    run_data["status"] = "cancelled"
    run_data["cancel_reason"] = reason
    run_data["updated_at"] = now
    run_file.write_text(json.dumps(run_data, ensure_ascii=False, indent=2))

    _append_run_event(run_dir, "pipeline_cancelled", reason=reason)
    return "cancelled", {"status": "cancelled", "pipeline_id": pipeline_id, "reason": reason}


def cancel_node(sop, pipeline_id, node_id, reason="用户取消节点"):
    """Cancel a specific node.

    Returns 404 if the run workspace does not exist.
    """
    wiki = Path(sop["wiki_local_path"])
    run_dir = wiki / "raw" / "pipeline-runs" / pipeline_id
    run_file = run_dir / "run.json"
    now = _now_iso_utc()

    if not run_file.exists():
        return None, {"status": "error", "message": f"pipeline {pipeline_id!r} not found"}

    node_file = run_dir / "nodes" / f"{node_id}.json"
    node_data = read_json(node_file) or {}
    node_data["status"] = "cancelled"
    node_data["cancel_reason"] = reason
    node_data["updated_at"] = now
    node_file.parent.mkdir(parents=True, exist_ok=True)
    node_file.write_text(json.dumps(node_data, ensure_ascii=False, indent=2))

    run_data = read_json(run_file) or {}
    if isinstance(run_data.get("nodes"), dict):
        run_data["nodes"][node_id] = "cancelled"
        run_data["updated_at"] = now
        run_file.write_text(json.dumps(run_data, ensure_ascii=False, indent=2))

    _append_run_event(run_dir, "node_cancelled", node_id=node_id, reason=reason)
    return "cancelled", {"status": "cancelled", "pipeline_id": pipeline_id, "node_id": node_id}


def retry_node(sop, pipeline_id, node_id):
    wiki = Path(sop["wiki_local_path"])
    run_dir = wiki / "raw" / "pipeline-runs" / pipeline_id
    now = _now_iso_utc()

    run_file = run_dir / "run.json"
    if not run_file.exists():
        return 404, {"status": "error", "message": f"pipeline {pipeline_id!r} not found"}

    node_file = run_dir / "nodes" / f"{node_id}.json"
    node_data = read_json(node_file) or {}
    if node_data.get("status") == "running":
        return 409, {"status": "error", "message": "节点正在运行中，无法重试"}

    plugin_dir = Path(os.environ.get(
        "YOUTUBE_WIKI_PLUGIN_DIR",
        str(Path.home() / "agent-brain-plugins" / "youtube-wiki"),
    )).expanduser()
    skills_dir = plugin_dir / "skills"

    run_id = f"retry-{int(time.time())}"
    node_data["status"] = "running"
    node_data["run_id"] = run_id
    node_data["started_at"] = now
    node_data["updated_at"] = now
    node_data["error"] = None
    node_data["finished_at"] = None
    node_file.parent.mkdir(parents=True, exist_ok=True)
    node_file.write_text(json.dumps(node_data, ensure_ascii=False, indent=2))

    run_file = run_dir / "run.json"
    run_data = read_json(run_file) or {}
    if isinstance(run_data.get("nodes"), dict):
        run_data["nodes"][node_id] = "running"
        if run_data.get("status") not in {"running"}:
            run_data["status"] = "running"
        run_data["updated_at"] = now
        run_file.write_text(json.dumps(run_data, ensure_ascii=False, indent=2))

    _append_run_event(run_dir, "node_retry", node_id=node_id, run_id=run_id)

    # Stage script lookup — mirrors stage_runner.py forward_next
    script_map = {
        "notebooklm-research": skills_dir / "sop-notebooklm-research" / "scripts" / "run_notebooklm_research.sh",
        "youtube-deep-research": skills_dir / "sop-youtube-deep-research" / "scripts" / "run_youtube_deep_research.sh",
        "wiki-build": skills_dir / "sop-wiki-build" / "scripts" / "run_wiki_build.sh",
    }

    env = {**os.environ}
    log_path = Path("/tmp") / f"retry-{node_id}-{run_id}.log"
    launched = False

    script = script_map.get(node_id)
    if script and script.exists():
        try:
            with open(log_path, "ab") as log:
                subprocess.Popen(
                    ["bash", str(script), str(wiki), run_id],
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            launched = True
        except Exception:
            pass

    if not launched:
        # Fallback: Hermes webhook
        sop_yaml = read_yaml(wiki / "sop.yaml")
        node_cfg = (sop_yaml.get("nodes") or {}).get(node_id, {})
        route = node_cfg.get("webhook_route", "") or node_cfg.get("route", "")
        if route:
            import urllib.request as _req
            port = os.environ.get("HERMES_WEBHOOK_PORT", "8644")
            token = os.environ.get("HERMES_WEBHOOK_TOKEN", "")
            payload = json.dumps({
                "stage": node_id,
                "wiki_local_path": str(wiki),
                "run_id": run_id,
                "pipeline_id": pipeline_id,
            }).encode()
            try:
                req = _req.Request(
                    f"http://localhost:{port}/webhooks/{route}",
                    data=payload,
                    headers={"Content-Type": "application/json", "X-GitLab-Token": token},
                )
                _req.urlopen(req, timeout=15)
                launched = True
            except Exception:
                pass

    if not launched:
        node_data["status"] = "failed"
        node_data["error"] = "无法启动节点：找不到执行脚本"
        node_data["updated_at"] = _now_iso_utc()
        node_file.write_text(json.dumps(node_data, ensure_ascii=False, indent=2))
        return 500, {"status": "error", "message": "无法启动节点，请检查脚本是否存在"}

    return 200, {
        "status": "retrying",
        "pipeline_id": pipeline_id,
        "node_id": node_id,
        "run_id": run_id,
        "log": str(log_path),
    }


class Handler(http.server.BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = [unquote(p) for p in parsed.path.strip("/").split("/") if p]
        query = parse_qs(parsed.query)
        try:
            if path == ["api", "sop"]:
                return json_response(self, 200, sop_manifest())
            if path == ["api", "sop", "instances"]:
                return json_response(self, 200, sop_instances())
            if path == ["api", "sop", "debug", "scanned"]:
                return json_response(self, 200, {"sops": scanned_sops()})
            if len(path) >= 3 and path[0] == "api" and path[1] == "sop":
                sop = find_sop(path[2])
                if not sop:
                    return json_response(self, 404, {"detail": "SOP not found"})
                if len(path) == 3:
                    return json_response(self, 200, {k: v for k, v in sop.items() if k != "sop_file"})
                if len(path) == 4 and path[3] == "dag":
                    return json_response(self, 200, sop_dag(sop))
                if len(path) == 4 and path[3] == "runs":
                    return json_response(self, 200, sop_runs(sop, query))
                if len(path) == 5 and path[3] == "runs":
                    run_file = Path(sop["wiki_local_path"]) / "raw" / "pipeline-runs" / path[4] / "run.json"
                    data = read_json(run_file)
                    if data and not data.get("pipeline_id"):
                        data["pipeline_id"] = path[4]
                    return json_response(self, 200 if data else 404, data or {"detail": "Run not found"})
                if len(path) == 6 and path[3] == "runs":
                    run_dir = Path(sop["wiki_local_path"]) / "raw" / "pipeline-runs" / path[4]
                    section = path[5]
                    if section in {"dag", "context", "artifacts"}:
                        data = read_json(run_dir / f"{section}.json")
                        return json_response(self, 200 if data is not None else 404, data if data is not None else {
                            "detail": f"Run {section} not found"
                        })
                    if section == "events":
                        event_file = run_dir / "events.jsonl"
                        events = []
                        if event_file.exists():
                            for line in event_file.read_text(encoding="utf-8").splitlines():
                                try:
                                    events.append(json.loads(line))
                                except json.JSONDecodeError:
                                    continue
                        return json_response(self, 200, {"pipeline_id": path[4], "events": events})
                if len(path) == 7 and path[3] == "runs" and path[5] == "nodes":
                    data = node_runtime_detail(sop, path[4], path[6])
                    return json_response(self, 200, data)
                if len(path) == 8 and path[3] == "runs" and path[5] == "nodes":
                    data = node_runtime_detail(sop, path[4], path[6])
                    section = path[7]
                    if section == "inputs":
                        return json_response(self, 200, {
                            "declared_inputs": data["declared_inputs"],
                            "resolved_inputs": data["resolved_inputs"],
                        })
                    if section == "outputs":
                        return json_response(self, 200, {
                            "declared_outputs": data["declared_outputs"],
                            "actual_outputs": data["actual_outputs"],
                            "validation": data["validation"],
                        })
                    if section == "artifacts":
                        return json_response(self, 200, {
                            "pipeline_id": path[4],
                            "node_id": path[6],
                            "artifacts": data["artifacts"],
                        })
                    if section == "discovered-candidates":
                        return json_response(self, 200, {
                            "pipeline_id": path[4],
                            "node_id": path[6],
                            "discovered_candidates": data["discovered_candidates"],
                        })
                if len(path) == 7 and path[3] == "runs" and path[5] == "logs":
                    node_id_log = path[6]
                    node_file = Path(sop["wiki_local_path"]) / "raw" / "pipeline-runs" / path[4] / "nodes" / f"{node_id_log}.json"
                    node = read_json(node_file) or {}
                    log_file = Path(sop["wiki_local_path"]) / "logs" / "stage-events" / f"{node.get('run_id', '')}.jsonl"
                    log_text = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
                    # Parse structured events belonging to this node
                    events = []
                    for line in log_text.splitlines():
                        try:
                            ev = json.loads(line)
                            if ev.get("stage", node_id_log) == node_id_log:
                                events.append(ev)
                        except json.JSONDecodeError:
                            pass
                    return json_response(self, 200, {
                        "pipeline_id": path[4],
                        "node_id": node_id_log,
                        "log": log_text,
                        "events": events,
                    })
                # GET /api/sop/{instance}/nodes/{node_id} — static node config
                if len(path) == 5 and path[3] == "nodes":
                    cfg = node_static_config(sop, path[4])
                    if cfg is None:
                        return json_response(self, 404, {"detail": f"Node {path[4]!r} not found"})
                    return json_response(self, 200, cfg)
            return text_response(self, 200, "ok")
        except Exception as exc:
            return json_response(self, 500, {"detail": str(exc)})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length)) if length else {}
        except Exception:
            data = {}

        path = [unquote(p) for p in urlparse(self.path).path.strip("/").split("/") if p]

        # POST /api/sop/{instance}/runs  → trigger
        if len(path) == 4 and path[:2] == ["api", "sop"] and path[3] == "runs":
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            status, result = trigger_sop(sop, data)
            return json_response(self, status, result)

        # POST /api/sop/{instance}/runs/{pipeline_id}/cancel
        if (len(path) == 6 and path[:2] == ["api", "sop"]
                and path[3] == "runs" and path[5] == "cancel"):
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            _status, result = cancel_run(sop, path[4], data.get("reason", "用户取消"))
            http_code = 404 if _status is None else 200
            return json_response(self, http_code, result)

        # POST /api/sop/{instance}/runs/{pipeline_id}/nodes/{node_id}/retry
        if (len(path) == 8 and path[:2] == ["api", "sop"]
                and path[3] == "runs" and path[5] == "nodes" and path[7] == "retry"):
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            http_code, result = retry_node(sop, path[4], path[6])
            return json_response(self, http_code, result)

        # POST /api/sop/{instance}/runs/{pipeline_id}/nodes/{node_id}/cancel
        if (len(path) == 8 and path[:2] == ["api", "sop"]
                and path[3] == "runs" and path[5] == "nodes" and path[7] == "cancel"):
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            _status, result = cancel_node(sop, path[4], path[6], data.get("reason", "用户取消节点"))
            http_code = 404 if _status is None else 200
            return json_response(self, http_code, result)

        env = {**os.environ}
        for k, v in data.items():
            env[k.upper().replace("-", "_").replace(".", "_")] = str(v)

        r = subprocess.run(
            ["bash", "-l", SCRIPT],
            env=env,
            capture_output=True,
            text=True,
        )

        text_response(self, 200 if r.returncode == 0 else 500, r.stdout)

    def log_message(self, *_):
        pass


def main():
    print(f"[bridge] 127.0.0.1:{PORT}", flush=True)
    http.server.HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
