import http.server
import hashlib
import importlib.util
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
SSE_STREAM_WINDOW_SECONDS = float(os.environ.get("SSE_STREAM_WINDOW_SECONDS", "5"))
SSE_HEARTBEAT_SECONDS = float(os.environ.get("SSE_HEARTBEAT_SECONDS", "3"))
GENERIC_NODE_CLI_URL = os.environ.get("SOP_NODE_CLI_URL", "https://skill.vyibc.com/sop-node.sh")
_RUN_INDEX_CLASS = None


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


def request_endpoint(handler):
    host = handler.headers.get("X-Forwarded-Host") or handler.headers.get("Host") or ""
    if not host:
        return ""
    proto = handler.headers.get("X-Forwarded-Proto")
    if not proto:
        proto = "http" if host.startswith(("127.0.0.1", "localhost")) else "https"
    return f"{proto}://{host}".rstrip("/")


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


def run_workspace(sop, pipeline_id):
    return Path(sop["wiki_local_path"]) / "raw" / "pipeline-runs" / pipeline_id


def run_index_class():
    global _RUN_INDEX_CLASS
    if _RUN_INDEX_CLASS is not None:
        return _RUN_INDEX_CLASS
    plugin_root = Path(os.environ.get(
        "AGENT_BRAIN_PLUGINS_PATH",
        str(Path.home() / "agent-brain-plugins"),
    )).expanduser()
    candidates = [
        Path(os.environ.get("SOP_RUN_INDEX_MODULE", "")).expanduser() if os.environ.get("SOP_RUN_INDEX_MODULE") else None,
        plugin_root / "youtube-wiki" / "infrastructure" / "run_index.py",
        Path(os.environ.get(
            "YOUTUBE_WIKI_PLUGIN_DIR",
            str(Path.home() / "agent-brain-plugins" / "youtube-wiki"),
        )).expanduser() / "infrastructure" / "run_index.py",
    ]
    module_path = next((path for path in candidates if path and path.exists()), None)
    if not module_path:
        return None
    spec = importlib.util.spec_from_file_location("sop_run_index", module_path)
    if not spec or not spec.loader:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _RUN_INDEX_CLASS = module.RunIndexStore
    return _RUN_INDEX_CLASS


def run_index_store(sop, create=False):
    cls = run_index_class()
    if cls is None:
        return None
    store = cls(sop["wiki_local_path"])
    if create or store.db_path.exists():
        return store
    return None


def indexed_run(sop, pipeline_id, rebuild=True):
    store = run_index_store(sop, create=rebuild)
    if not store:
        return None
    try:
        data = store.get_run(pipeline_id)
        if data:
            return data
        if rebuild and store.rebuild_from_workspace(pipeline_id, sop):
            return store.get_run(pipeline_id)
    except Exception:
        return None
    return None


def read_run_events(events_file, after_sequence=0):
    events = []
    if not events_file.exists():
        return events
    for index, line in enumerate(events_file.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        sequence = int(event.get("sequence") or index)
        event["sequence"] = sequence
        if sequence > after_sequence:
            events.append(event)
    return events


def format_sse_event(event):
    sequence = int(event.get("sequence") or 0)
    event_type = str(event.get("event") or "message")
    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return f"id: {sequence}\nevent: {event_type}\ndata: {payload}\n\n".encode("utf-8")


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


def artifact_with_preview(sop, artifact):
    """Attach a bounded text preview for indexed artifacts at response time."""
    if not isinstance(artifact, dict):
        return artifact
    record = dict(artifact)
    if record.get("preview"):
        return record
    path = safe_artifact_path(sop["wiki_local_path"], record.get("path", ""))
    if not path or not path.is_file():
        return record
    suffix = path.suffix.lower()
    if suffix not in TEXT_FORMATS:
        return record
    try:
        stat = path.stat()
        if stat.st_size > 1024 * 1024:
            return record
        preview = path.read_text(encoding="utf-8", errors="replace")[:16000]
        record["preview"] = preview
        record["preview_truncated"] = stat.st_size > len(preview.encode("utf-8"))
    except OSError:
        pass
    return record


def artifacts_with_preview(sop, artifacts):
    if not isinstance(artifacts, list):
        return artifacts
    return [artifact_with_preview(sop, artifact) for artifact in artifacts]


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
    workspace = run_workspace(sop, pipeline_id)
    node_file = workspace / "nodes" / f"{node_id}.json"
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

    input_snapshot = read_json(workspace / "nodes" / node_id / "input.json") or {}
    if isinstance(input_snapshot.get("resolved_inputs"), dict):
        resolved_inputs = input_snapshot["resolved_inputs"]
    else:
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
    detail = {
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
        "capabilities": read_json(workspace / "nodes" / node_id / "capabilities.json") or {},
        "plan": read_json(workspace / "nodes" / node_id / "plan.json"),
        "validation": {
            "status": validation_status,
            "missing_outputs": recorded_validation.get("missing_outputs", missing),
            "unexpected_outputs": recorded_validation.get("unexpected_outputs", []),
        },
    }
    store = run_index_store(sop)
    if store:
        try:
            indexed = store.get_node_state(pipeline_id, node_id)
            if indexed:
                indexed_artifacts = artifacts_with_preview(sop, store.get_artifacts(pipeline_id, node_id))
                detail.update({
                    **indexed,
                    "artifacts": indexed_artifacts,
                    "discovered_candidates": discovered_candidates,
                    "plan": detail.get("plan"),
                    "index_resolution": "indexed",
                })
        except Exception:
            pass
    return detail


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
    manifest = read_yaml(skill_dir / "node.yaml") if (skill_dir / "node.yaml").exists() else {}
    manifest_executor = manifest.get("executor") if isinstance(manifest.get("executor"), dict) else {}
    configured_executor = config.get("executor") if isinstance(config.get("executor"), dict) else {}

    return {
        "node_id": node_id,
        "title": config.get("title", node_id),
        "mode": config.get("mode", "blocking"),
        "needs": config.get("needs") or [],
        "executor": {
            **manifest_executor,
            **configured_executor,
            "type": configured_executor.get("type") or manifest_executor.get("type") or "skill",
            "skill": config.get("skill") or manifest_executor.get("skill", ""),
            "webhook_route": config.get("webhook_route", ""),
        },
        "inputs": config.get("inputs", {}),
        "outputs": config.get("outputs", {}),
        "optional_inputs": config.get("optional_inputs", {}),
        "infra": config.get("infra", {"tg_notify": True, "log_record": True}),
        "params": config.get("params") or {},
        "skill_script": skill_script,
        "skill_readme": skill_readme,
        "manifest": manifest,
        "ui": manifest.get("ui") if isinstance(manifest.get("ui"), dict) else {},
    }


def classify_node(node_id, config, static):
    executor = static.get("executor") or {}
    if config.get("mode") == "manual" or node_id == "retry":
        return "manual-action"
    if node_id == "tg-notify" or executor.get("skill") == "sop-tg-notify":
        return "notification-capability"
    if executor.get("type") in {"http", "public-api"}:
        return "public-api"
    if executor.get("type") == "agent-skill" and executor.get("webhook_route"):
        return "hermes-agent-skill"
    if config.get("skill") and config.get("webhook_route"):
        return "repo-skill-script"
    return "custom"


def node_actions(instance_id, node_id):
    return {
        "inspect": {
            "method": "GET",
            "path": f"/api/sop/{instance_id}/nodes/{node_id}",
            "requires_pipeline": False,
            "destructive": False,
        },
        "actions": {
            "method": "GET",
            "path": f"/api/sop/{instance_id}/nodes/{node_id}/actions",
            "requires_pipeline": False,
            "destructive": False,
        },
        "status": {
            "method": "GET",
            "path": f"/api/sop/{instance_id}/runs/{{pipeline_id}}/nodes/{node_id}",
            "requires_pipeline": True,
            "destructive": False,
        },
        "retry": {
            "method": "POST",
            "path": f"/api/sop/{instance_id}/runs/{{pipeline_id}}/nodes/{node_id}/actions/retry",
            "requires_pipeline": True,
            "destructive": True,
        },
        "cancel": {
            "method": "POST",
            "path": f"/api/sop/{instance_id}/runs/{{pipeline_id}}/nodes/{node_id}/actions/cancel",
            "requires_pipeline": True,
            "destructive": True,
        },
        "trigger": {
            "method": "POST",
            "path": f"/api/sop/{instance_id}/nodes/{node_id}/actions/trigger",
            "requires_pipeline": False,
            "destructive": True,
            "enabled": False,
        },
    }


def node_cli_examples(endpoint, instance_id, node_id, pipeline_id="<pipeline_id>"):
    base = (
        f"bash <(curl -fsSL {GENERIC_NODE_CLI_URL}) --endpoint={endpoint} "
        f"--instance={instance_id} --node={node_id}"
    )
    return {
        "inspect": f"{base} --action=inspect",
        "actions": f"{base} --action=actions",
        "status": f"{base} --pipeline-id={pipeline_id} --action=status",
        "retry_dry_run": f"{base} --pipeline-id={pipeline_id} --action=retry --dry-run",
        "cancel_dry_run": f"{base} --pipeline-id={pipeline_id} --action=cancel --dry-run",
    }


def normalize_contract(value, direction):
    if not isinstance(value, dict):
        return {}
    result = {}
    for name, spec in value.items():
        if isinstance(spec, dict):
            result[name] = dict(spec)
        elif direction == "input":
            result[name] = {"type": "auto", "required": True, "from": spec}
        elif str(spec).startswith("context."):
            result[name] = {"type": "string", "from": spec}
        else:
            result[name] = {"type": "files" if "*" in str(spec) else "file", "path": spec}
    return result


def validate_node_definition(node_id, config, static):
    missing = []
    executor = static.get("executor") or {}
    if not node_id:
        missing.append("node_id")
    if not static.get("title"):
        missing.append("title")
    if not executor.get("type"):
        missing.append("executor.type")
    if executor.get("type") in {"agent-skill", "skill"} and not executor.get("skill"):
        missing.append("executor.skill")
    if config.get("mode") != "manual" and not config.get("outputs"):
        missing.append("outputs")
    return missing


def node_registry_item(sop, node_id, endpoint=""):
    config = (sop.get("nodes") or {}).get(node_id)
    if config is None:
        return None
    static = node_static_config(sop, node_id)
    if static is None:
        return None
    instance_id = sop.get("id") or sop.get("name") or ""
    manifest = static.get("manifest") if isinstance(static.get("manifest"), dict) else {}
    manifest_caps = manifest.get("capabilities") if isinstance(manifest.get("capabilities"), dict) else {}
    return {
        **static,
        "description": manifest.get("description", ""),
        "case": classify_node(node_id, config, static),
        "skill": {
            "id": (static.get("executor") or {}).get("skill", ""),
            "source": "repository",
            "install_command": (manifest.get("skill") or {}).get("install_command", "") if isinstance(manifest.get("skill"), dict) else "",
            "readme_path": static.get("skill_script", "").replace("/scripts/", "/SKILL.md") if static.get("skill_script") else "",
            "summary": static.get("skill_readme", ""),
        },
        "inputs": normalize_contract(static.get("inputs", {}), "input"),
        "optional_inputs": normalize_contract(static.get("optional_inputs", {}), "input"),
        "outputs": normalize_contract(static.get("outputs", {}), "output"),
        "capabilities": {
            "git": manifest_caps.get("git", {"enabled": True, "required": False}),
            "telegram": manifest_caps.get("telegram", {"enabled": (static.get("infra") or {}).get("tg_notify", True), "required": False}),
            "sse": {"enabled": True, "required": True},
        },
        "actions": node_actions(instance_id, node_id),
        "cli": node_cli_examples(endpoint or "{endpoint}", instance_id, node_id),
        "ui": static.get("ui") or {},
        "modules": node_modules(sop, node_id, endpoint),
        "editable": True,
        "publish_enabled": False,
        "missing_fields": validate_node_definition(node_id, config, static),
    }


NODE_MODULE_DEFINITIONS = [
    ("basic", "Basic", "节点身份、分类和发布状态"),
    ("executor", "Executor", "执行器、Agent、Webhook 和操作入口"),
    ("skill", "Skill", "节点背后的 Skill 安装、说明和来源"),
    ("inputs", "Inputs", "输入契约和当前 Run 的 resolved inputs"),
    ("outputs", "Outputs", "输出契约、实际输出和校验结果"),
    ("artifacts", "Artifacts", "当前 Run 的记录产物和候选产物"),
    ("capabilities", "Capabilities", "Git、TG、SSE 和日志等附属能力"),
    ("runtime", "Runtime State", "节点运行状态、进度、耗时和错误"),
    ("actions", "Actions", "Inspect、Retry、Cancel、Validate 和 Publish"),
    ("logs", "Logs / Events", "节点日志、事件和错误线索"),
]


def module_status(module_id, static, run_detail=None):
    missing = validate_node_definition(static.get("node_id", ""), (static or {}), static or {})
    if module_id in {"basic", "executor", "skill", "actions"}:
        return "warning" if missing else "ready"
    if module_id == "inputs":
        return "ready" if static.get("inputs") or static.get("optional_inputs") else "warning"
    if module_id == "outputs":
        return "ready" if static.get("outputs") else "warning"
    if module_id == "capabilities":
        return "ready"
    if run_detail:
        if module_id == "runtime":
            return run_detail.get("status", "waiting")
        if module_id == "artifacts":
            return "ready" if run_detail.get("artifacts") else "warning"
        if module_id == "logs":
            return run_detail.get("status", "waiting")
    return "waiting"


def module_summary(module_id, static, run_detail=None):
    executor = static.get("executor") or {}
    skill = executor.get("skill") or ""
    if module_id == "basic":
        return f"{static.get('title', static.get('node_id'))} · {static.get('mode', 'blocking')}"
    if module_id == "executor":
        return f"{executor.get('type', 'skill')} · {executor.get('agent') or executor.get('webhook_route') or skill or 'local'}"
    if module_id == "skill":
        return skill or "未配置 skill"
    if module_id == "inputs":
        total = len(static.get("inputs") or {}) + len(static.get("optional_inputs") or {})
        resolved = len((run_detail or {}).get("resolved_inputs") or {})
        return f"{resolved}/{total} resolved" if run_detail else f"{total} inputs"
    if module_id == "outputs":
        total = len(static.get("outputs") or {})
        actual = len((run_detail or {}).get("actual_outputs") or {})
        return f"{actual}/{total} actual outputs" if run_detail else f"{total} outputs"
    if module_id == "artifacts":
        return f"{len((run_detail or {}).get('artifacts') or [])} recorded artifacts" if run_detail else "run-scoped artifacts"
    if module_id == "capabilities":
        return "git / telegram / sse"
    if module_id == "runtime":
        return f"{(run_detail or {}).get('status', 'waiting')} · {(run_detail or {}).get('duration_s', 0)}s"
    if module_id == "actions":
        return "inspect / retry / cancel / validate / publish"
    if module_id == "logs":
        return "node events and latest log"
    return ""


def node_modules(sop, node_id, endpoint="", pipeline_id=None):
    static = node_static_config(sop, node_id)
    if static is None:
        return []
    run_detail = node_runtime_detail(sop, pipeline_id, node_id) if pipeline_id else None
    modules = []
    for module_id, title, description in NODE_MODULE_DEFINITIONS:
        modules.append({
            "id": module_id,
            "title": title,
            "description": description,
            "status": module_status(module_id, static, run_detail),
            "summary": module_summary(module_id, static, run_detail),
            "detail_url": (
                f"/api/sop/{sop.get('id', '')}/runs/{pipeline_id}/nodes/{node_id}/modules/{module_id}"
                if pipeline_id
                else f"/api/sop/{sop.get('id', '')}/nodes/{node_id}/modules/{module_id}"
            ),
            "run_scoped": bool(pipeline_id),
        })
    return modules


def node_module_detail(sop, node_id, module_id, endpoint="", pipeline_id=None):
    item = node_registry_item(sop, node_id, endpoint)
    if item is None:
        return None
    modules = {module["id"]: module for module in node_modules(sop, node_id, endpoint, pipeline_id)}
    if module_id not in modules:
        return None
    run_detail = node_runtime_detail(sop, pipeline_id, node_id) if pipeline_id else {}
    base = {
        "sop_id": sop.get("id", ""),
        "node_id": node_id,
        "pipeline_id": pipeline_id or "",
        "module": modules[module_id],
    }
    if module_id == "basic":
        detail = {
            "node_id": item.get("node_id"),
            "title": item.get("title"),
            "description": item.get("description"),
            "mode": item.get("mode"),
            "needs": item.get("needs"),
            "ui": item.get("ui"),
            "editable": item.get("editable"),
            "publish_enabled": item.get("publish_enabled"),
            "missing_fields": item.get("missing_fields"),
        }
    elif module_id == "executor":
        detail = {"executor": item.get("executor"), "case": item.get("case"), "actions": item.get("actions"), "cli": item.get("cli")}
    elif module_id == "skill":
        detail = {"skill": item.get("skill"), "skill_script": item.get("skill_script"), "skill_readme": item.get("skill_readme")}
    elif module_id == "inputs":
        detail = {"declared_inputs": item.get("inputs"), "optional_inputs": item.get("optional_inputs"), "resolved_inputs": run_detail.get("resolved_inputs", {})}
    elif module_id == "outputs":
        detail = {"declared_outputs": item.get("outputs"), "actual_outputs": run_detail.get("actual_outputs", {}), "validation": run_detail.get("validation", {})}
    elif module_id == "artifacts":
        detail = {"artifacts": run_detail.get("artifacts", []), "discovered_candidates": run_detail.get("discovered_candidates", [])}
    elif module_id == "capabilities":
        detail = {"declared_capabilities": item.get("capabilities"), "run_capabilities": run_detail.get("capabilities", {})}
    elif module_id == "runtime":
        detail = {
            "status": run_detail.get("status", "waiting"),
            "run_id": run_detail.get("run_id", ""),
            "started_at": run_detail.get("started_at", ""),
            "finished_at": run_detail.get("finished_at", ""),
            "updated_at": run_detail.get("updated_at", ""),
            "attempt": run_detail.get("attempt"),
            "progress": run_detail.get("progress"),
            "duration_s": run_detail.get("duration_s"),
            "error": run_detail.get("error", ""),
        }
    elif module_id == "actions":
        detail = {"actions": item.get("actions"), "cli": item.get("cli"), "publish_enabled": item.get("publish_enabled")}
    else:
        events = []
        log_text = ""
        if pipeline_id:
            node_file = Path(sop["wiki_local_path"]) / "raw" / "pipeline-runs" / pipeline_id / "nodes" / f"{node_id}.json"
            node = read_json(node_file) or {}
            log_file = Path(sop["wiki_local_path"]) / "logs" / "stage-events" / f"{node.get('run_id', '')}.jsonl"
            log_text = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
            for line in log_text.splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("stage", node_id) == node_id:
                    events.append(event)
        detail = {"log": log_text, "events": events}
    return {**base, "detail": detail}


def node_registry(sop, endpoint=""):
    nodes = []
    for node_id in (sop.get("nodes") or {}):
        item = node_registry_item(sop, node_id, endpoint)
        if item is not None:
            nodes.append(item)
    return {
        "sop_id": sop.get("id", ""),
        "nodes": nodes,
    }


def slugify(value):
    import re
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value).strip().lower())
    value = re.sub(r"-+", "-", value).strip("-")
    return value or f"node-{int(time.time())}"


def draft_from_skill(spec):
    node_id = slugify(spec.get("node_id") or spec.get("title") or "new-node")
    skill_id = str(spec.get("skill_id") or node_id)
    upstream = str(spec.get("upstream") or "")
    upstream_output = str(spec.get("upstream_output") or "output")
    input_name = str(spec.get("input_name") or "input")
    output_name = str(spec.get("output_name") or "artifact")
    return {
        "id": node_id,
        "title": spec.get("title") or node_id,
        "description": spec.get("description") or "",
        "version": "0.1-draft",
        "skill": {
            "id": skill_id,
            "install_command": spec.get("skill_install_command") or "",
            "source": "install-command" if spec.get("skill_install_command") else "repository",
        },
        "executor": {
            "type": spec.get("executor_type") or "agent-skill",
            "agent": spec.get("agent") or "hermes",
            "skill": skill_id,
            "entry": spec.get("entry") or f"scripts/run_{node_id.replace('-', '_')}.sh",
        },
        "mode": spec.get("mode") or "blocking",
        "needs": [upstream] if upstream else [],
        "inputs": {
            input_name: {
                "type": spec.get("input_type") or "auto",
                "required": True,
                "from": f"{upstream}.outputs.{upstream_output}" if upstream else "",
            }
        },
        "outputs": {
            output_name: {
                "type": spec.get("output_type") or "file",
                "path": spec.get("output_path") or f"raw/{node_id}/{{pipeline_id}}/{output_name}",
            }
        },
        "capabilities": {
            "git": {"enabled": True, "required": False},
            "telegram": {"enabled": True, "required": False},
            "sse": {"enabled": True, "required": True},
        },
        "ui": {"category": spec.get("category") or "custom"},
    }


def create_node_draft(sop, spec):
    wiki = Path(sop["wiki_local_path"])
    draft = draft_from_skill(spec)
    draft_id = f"{draft['id']}-{int(time.time())}"
    draft_dir = wiki / "raw" / "node-drafts" / draft_id
    draft_dir.mkdir(parents=True, exist_ok=True)
    (draft_dir / "node.yaml").write_text(yaml.safe_dump(draft, allow_unicode=True, sort_keys=False), encoding="utf-8")
    missing = validate_node_definition(draft["id"], draft, draft)
    validation = {
        "status": "passed" if not missing else "warning",
        "missing_fields": missing,
        "production_dag_changed": False,
    }
    (draft_dir / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"draft_id": draft_id, "draft_path": str(draft_dir), "node": draft, "validation": validation}


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


def runtime_info():
    registry = read_registry()
    sops = load_sops()
    runtime_id = registry.get("runtime_id", "youtube-wiki")
    channel_url = registry.get("channel_url", "")
    spi_base_url = registry.get("spi_base_url", "")
    return {
        "runtime_id": runtime_id,
        "id": runtime_id,
        "display_name": registry.get("display_name") or runtime_id,
        "channel_name": registry.get("channel_name", ""),
        "channel_url": channel_url,
        "spi_base_url": spi_base_url,
        "status": "online",
        "supported_sop_types": sorted({sop.get("sop_type", "") for sop in sops if sop.get("sop_type")}),
        "instance_count": len(sops),
        "registry_path": str(REGISTRY_PATH),
        "created_at": registry.get("created_at", ""),
        "updated_at": registry.get("updated_at", ""),
        "health": {
            "spi": "ok",
            "registry": "ok" if REGISTRY_PATH.exists() else "missing",
            "channel": "ok" if channel_url else "missing",
            "instances": "ok" if sops else "empty",
        },
    }


def workflow_binding(sop):
    sop_id = sop.get("id") or sop.get("instance_id", "")
    business_nodes = [
        node_id for node_id, config in (sop.get("nodes") or {}).items()
        if node_id != "retry" and (config or {}).get("mode") != "manual"
    ]
    return {
        "workflow_id": sop.get("raw_id") or sop.get("sop_type") or sop_id,
        "workflow_name": sop.get("title") or sop.get("name") or sop_id,
        "workflow_version": sop.get("version", ""),
        "definition_source": "sop.yaml",
        "definition_path": "sop.yaml",
        "node_count": len(business_nodes),
        "enabled_node_count": len(business_nodes),
        "binding_status": "ready" if business_nodes else "invalid",
    }


def instance_status(sop, latest_execution=None):
    if not sop.get("enabled", True):
        return "disabled"
    workspace = Path(sop["wiki_local_path"])
    if not workspace.exists() or not Path(sop.get("sop_file", "")).exists():
        return "initializing"
    if latest_execution and latest_execution.get("status") == "running":
        return "running"
    if latest_execution and latest_execution.get("status") == "failed":
        return "failed"
    return "ready"


def instance_summary(sop, include_latest=True):
    sop_id = sop.get("id") or sop.get("instance_id", "")
    instance_id = sop.get("instance_id") or sop_id
    store = run_index_store(sop)
    runs = []
    if include_latest:
        try:
            runs = (store.list_runs(limit=1) if store else []) or []
        except Exception:
            runs = []
        if not runs:
            run_files_found = run_files(sop)
            if run_files_found:
                run = read_json(run_files_found[0]) or {}
                if run and not run.get("pipeline_id"):
                    run["pipeline_id"] = run_files_found[0].parent.name
                if run:
                    runs = [run_summary(sop, run)]
    latest = execution_summary(sop, runs[0]) if runs else None
    artifact_count = int((latest or {}).get("artifact_count") or 0)
    page_count = int((latest or {}).get("page_count") or 0)
    run_index_path = ""
    run_index_status = "missing"
    if store:
        run_index_path = str(store.db_path)
        run_index_status = "ready" if store.db_path.exists() else "missing"
    workspace = Path(sop["wiki_local_path"])
    return {
        "id": sop_id,
        "instance_id": instance_id,
        "runtime_id": sop.get("runtime_id", ""),
        "title": sop.get("title") or instance_id,
        "description": sop.get("description", ""),
        "sop_type": sop.get("sop_type", ""),
        "enabled": bool(sop.get("enabled", True)),
        "repo": sop.get("repo", ""),
        "repo_branch": sop.get("repo_branch", "main"),
        "wiki_local_path": sop.get("wiki_local_path", ""),
        "workspace_status": "ready" if workspace.exists() and Path(sop.get("sop_file", "")).exists() else "missing",
        "run_index_path": run_index_path,
        "run_index_status": run_index_status,
        "workflow_binding": workflow_binding(sop),
        "capabilities": instance_capabilities(sop),
        "execution_count": count_executions(sop),
        "latest_execution": latest,
        "artifact_count": artifact_count,
        "page_count": page_count,
        "status": instance_status(sop, latest),
        "channel_url": sop.get("channel_url", ""),
        "spi_base_url": sop.get("spi_base_url", ""),
        "created_at": sop.get("created_at", ""),
        "updated_at": sop.get("updated_at", ""),
        "dag_url": f"/api/sop/{sop_id}/dag",
        "runs_url": f"/api/sop/{sop_id}/runs",
        "executions_url": f"/api/sop/instances/{sop_id}/executions",
    }


def count_executions(sop):
    store = run_index_store(sop)
    if store:
        try:
            return len(store.list_runs(limit=200))
        except Exception:
            pass
    return len(run_files(sop))


def instance_capabilities(sop):
    workspace = Path(sop["wiki_local_path"])
    store = run_index_store(sop)
    return {
        "workspace": "ok" if workspace.exists() else "missing",
        "sop_yaml": "ok" if Path(sop.get("sop_file", "")).exists() else "missing",
        "run_index": "ok" if store and store.db_path.exists() else "missing",
        "git": "configured" if sop.get("repo") else "missing",
        "telegram": "configured" if os.environ.get("YOUTUBE_WIKI_TG_TOKEN") else "unknown",
        "notebooklm": "configured" if os.environ.get("NOTEBOOKLM_BRIDGE_URL") else "unknown",
        "vertex": "configured" if os.environ.get("GOOGLE_PROJECT_ID") or os.environ.get("GEMINI_API_KEY") else "unknown",
        "tunnel": "ok" if sop.get("channel_url") else "unknown",
    }


def execution_summary(sop, run):
    data = dict(run or {})
    pipeline_id = str(data.get("pipeline_id") or data.get("execution_id") or "")
    workflow = workflow_binding(sop)
    data.update({
        "execution_id": pipeline_id,
        "pipeline_id": pipeline_id,
        "runtime_id": sop.get("runtime_id", ""),
        "instance_id": sop.get("instance_id", sop.get("id", "")),
        "workflow_id": data.get("workflow_id") or workflow["workflow_id"],
        "workflow_version": data.get("workflow_version") or workflow["workflow_version"],
        "workflow_snapshot": data.get("workflow_snapshot") or {},
        "input": data.get("input") if isinstance(data.get("input"), dict) else {
            "url": data.get("source_url", "")
        },
        "failed_node": data.get("failed_node") or next(
            (node_id for node_id, status in (data.get("nodes") or {}).items() if status == "failed"),
            "",
        ),
        "event_count": data.get("event_count") or (
            len(read_run_events(run_workspace(sop, pipeline_id) / "events.jsonl")) if pipeline_id else 0
        ),
    })
    return data


def sop_manifest():
    registry = read_registry()
    runtime = runtime_info()
    return {
        "runtime": runtime["runtime_id"],
        "runtime_id": runtime["runtime_id"],
        "runtime_info": runtime,
        "channel": {
            "name": registry.get("channel_name", ""),
            "url": registry.get("channel_url", ""),
            "spi_base_url": registry.get("spi_base_url", ""),
        },
        "registry_path": str(REGISTRY_PATH),
        "sops": [instance_summary(sop) for sop in load_sops()],
    }


def sop_instances():
    manifest = sop_manifest()
    return {
        "runtime_id": manifest["runtime_id"],
        "runtime": manifest.get("runtime_info", {}),
        "channel": manifest["channel"],
        "instances": manifest["sops"],
    }


def sop_dag(sop):
    sop_id = sop.get("id") or sop.get("instance_id", "")
    nodes = []
    edges = []
    for node_id, node in (sop.get("nodes") or {}).items():
        if node.get("mode") == "manual" or node_id == "retry":
            continue
        static = node_static_config(sop, node_id) or {}
        manifest = static.get("manifest") if isinstance(static.get("manifest"), dict) else {}
        manifest_caps = manifest.get("capabilities") if isinstance(manifest.get("capabilities"), dict) else {}
        nodes.append({
            "id": node_id,
            "title": node.get("title", node_id),
            "mode": node.get("mode", "blocking"),
            "webhook_route": node.get("webhook_route", node.get("route", "")),
            "needs": node.get("needs") or [],
            "executor": static.get("executor") or {},
            "inputs": node.get("inputs", {}),
            "outputs": node.get("outputs", {}),
            "optional_inputs": node.get("optional_inputs", {}),
            "capabilities": {
                "git": manifest_caps.get("git", {"enabled": True, "required": False}),
                "telegram": manifest_caps.get("telegram", {
                    "enabled": (static.get("infra") or {}).get("tg_notify", True),
                    "required": False,
                }),
                "sse": {"enabled": True, "required": True},
            },
            "ui": static.get("ui") or {},
        })
        for need in node.get("needs") or []:
            edges.append({"source": need, "target": node_id})
    return {"sop_id": sop_id, "nodes": nodes, "edges": edges}


def run_files(sop):
    base = Path(sop["wiki_local_path"]) / "raw" / "pipeline-runs"
    if not base.exists():
        return []
    return sorted(base.glob("*/run.json"), key=lambda f: f.stat().st_mtime, reverse=True)


def sop_runs(sop, query=None):
    sop_id = sop.get("id") or sop.get("instance_id", "")
    instance_id = sop.get("instance_id") or sop_id
    query = query or {}
    try:
        limit = max(1, min(200, int((query.get("limit") or ["80"])[0])))
    except Exception:
        limit = 80
    status_filter = (query.get("status") or [""])[0]
    runs = []
    seen = set()
    store = run_index_store(sop)
    if store:
        try:
            for data in store.list_runs(limit=limit, status=status_filter):
                runs.append(execution_summary(sop, data))
                seen.add(data.get("pipeline_id"))
                if len(runs) >= limit:
                    return {"sop_id": sop_id, "instance_id": instance_id, "executions": runs, "runs": runs}
        except Exception:
            runs = []
            seen = set()
    for run_file in run_files(sop):
        data = read_json(run_file)
        if not data:
            continue
        # Guarantee pipeline_id is always present; derive from directory name if missing.
        if not data.get("pipeline_id"):
            data["pipeline_id"] = run_file.parent.name
        if data.get("pipeline_id") in seen:
            continue
        if not status_filter or data.get("status") == status_filter:
            runs.append(execution_summary(sop, run_summary(sop, data)))
        if len(runs) >= limit:
            break
    return {"sop_id": sop_id, "instance_id": instance_id, "executions": runs, "runs": runs}


def _iso_duration_seconds(started_at, finished_at):
    if not started_at or not finished_at:
        return 0
    try:
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        finish = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
        return max(0, int((finish - start).total_seconds()))
    except (TypeError, ValueError):
        return 0


def run_summary(sop, run):
    """Add stable UI aggregates without removing legacy Run fields."""
    data = dict(run or {})
    pipeline_id = str(data.get("pipeline_id") or "")
    run_dir = run_workspace(sop, pipeline_id)
    raw_node_states = data.get("nodes") if isinstance(data.get("nodes"), dict) else {}
    business_node_ids = {
        node_id for node_id, config in (sop.get("nodes") or {}).items()
        if node_id != "retry" and (config or {}).get("mode") != "manual"
    }
    node_states = {
        node_id: status for node_id, status in raw_node_states.items()
        if not business_node_ids or node_id in business_node_ids
    }
    node_count = len(node_states)
    done_count = sum(status in {"done", "skipped"} for status in node_states.values())
    failed_count = sum(status == "failed" for status in node_states.values())
    running_node = next((node_id for node_id, status in node_states.items() if status == "running"), "")
    progress = round(done_count * 100 / node_count) if node_count else 0

    artifacts = read_json(run_dir / "artifacts.json") or []
    if not isinstance(artifacts, list):
        artifacts = []
    events = read_run_events(run_dir / "events.jsonl")
    git_events = [event for event in events if str(event.get("event", "")).startswith("git.")]
    telegram_events = [event for event in events if str(event.get("event", "")).startswith("telegram.")]

    node_details = []
    node_state_summaries = {}
    for node_id in node_states:
        state = read_json(run_dir / "nodes" / f"{node_id}.json") or {}
        if state:
            node_details.append(state)
        node_state_summaries[node_id] = {
            "status": state.get("status", node_states.get(node_id, "waiting")),
            "started_at": state.get("started_at", ""),
            "finished_at": state.get("finished_at", ""),
            "duration_s": int(state.get("duration_s") or 0),
            "attempt": int(state.get("attempt") or 0),
            "progress": int(state.get("progress") or (100 if state.get("status") in {"done", "skipped"} else 0)),
            "artifact_count": len(state.get("artifacts") or []),
            "error": state.get("error") or "",
        }
    duration_s = sum(int(state.get("duration_s") or 0) for state in node_details)
    if not duration_s:
        duration_s = _iso_duration_seconds(data.get("started_at"), data.get("updated_at"))

    wiki_state = next((state for state in node_details if state.get("node_id") == "wiki-build"), {})
    wiki_outputs = wiki_state.get("actual_outputs") if isinstance(wiki_state.get("actual_outputs"), dict) else {}
    pages = wiki_outputs.get("pages") if isinstance(wiki_outputs.get("pages"), list) else []
    page_count = len(pages)
    if not page_count:
        context = read_json(run_dir / "context.json") or {}
        stage_c = context.get("stage_c") if isinstance(context.get("stage_c"), dict) else {}
        context_pages = stage_c.get("file_paths") if isinstance(stage_c.get("file_paths"), list) else []
        page_count = int(
            stage_c.get("pages_new_this_run")
            or stage_c.get("page_count")
            or len(context_pages)
        )

    data.update({
        "node_count": node_count,
        "done_count": done_count,
        "failed_count": failed_count,
        "running_node": running_node,
        "progress": progress,
        "artifact_count": len(artifacts),
        "git_event_count": len(git_events),
        "telegram_event_count": len(telegram_events),
        "page_count": page_count,
        "duration_s": duration_s,
        "node_states": node_state_summaries,
    })
    return data


def normalized_run_dag(sop, pipeline_id):
    snapshot = read_json(run_workspace(sop, pipeline_id) / "dag.json")
    if not snapshot:
        return None
    raw_nodes = snapshot.get("nodes") or []
    if isinstance(raw_nodes, dict):
        nodes = []
        for node_id, node in raw_nodes.items():
            item = dict(node or {})
            if item.get("mode") == "manual" or node_id == "retry":
                continue
            item["id"] = node_id
            static = node_static_config(sop, node_id) or {}
            item.setdefault("title", static.get("title", node_id))
            item.setdefault("executor", static.get("executor") or {})
            item.setdefault("ui", static.get("ui") or {})
            nodes.append(item)
    else:
        nodes = [node for node in raw_nodes if node.get("mode") != "manual" and node.get("id") != "retry"]
    node_ids = {node.get("id") for node in nodes}
    edges = [edge for edge in (snapshot.get("edges") or []) if edge.get("source") in node_ids and edge.get("target") in node_ids]
    return {**snapshot, "nodes": nodes, "edges": edges}


def run_artifact_candidates(sop, pipeline_id):
    candidates = []
    seen = set()
    for node_id in (sop.get("nodes") or {}):
        for artifact in node_runtime_detail(sop, pipeline_id, node_id).get("discovered_candidates", []):
            key = artifact.get("id") or artifact.get("path")
            if key and key not in seen:
                seen.add(key)
                candidates.append(artifact)
    return candidates


def trigger_sop(sop, body):
    repo = body.get("repo") or sop.get("repo")
    input_data = body.get("input") if isinstance(body.get("input"), dict) else {}
    url = input_data.get("url") or body.get("url")
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
        if input_data.get("force_notebooklm_fallback") is True:
            data["test_overrides"] = patch_run_test_overrides(
                sop,
                data["pipeline_id"],
                {"force_notebooklm_fallback": True},
            )
    return 200, data


def patch_run_test_overrides(sop, pipeline_id, overrides):
    """Persist explicit test-only run controls after the normal trigger created context."""
    wiki = Path(sop["wiki_local_path"])
    targets = [
        wiki / "raw" / "pipeline-context.json",
        wiki / "raw" / "pipeline-runs" / pipeline_id / "context.json",
    ]
    patched = []
    for path in targets:
        data = read_json(path)
        if not isinstance(data, dict):
            continue
        current = data.get("test_overrides") if isinstance(data.get("test_overrides"), dict) else {}
        data["test_overrides"] = {**current, **overrides}
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            patched.append(str(path.relative_to(wiki)))
        except OSError:
            continue
    return {"patched": patched, **overrides}


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
            if path == ["api", "sop", "runtime"]:
                return json_response(self, 200, runtime_info())
            if path == ["api", "sop", "instances"]:
                return json_response(self, 200, sop_instances())
            if len(path) >= 4 and path[0] == "api" and path[1] == "sop" and path[2] == "instances":
                sop = find_sop(path[3])
                if not sop:
                    return json_response(self, 404, {"detail": "Instance not found"})
                if len(path) == 4:
                    return json_response(self, 200, instance_summary(sop))
                if len(path) == 5 and path[4] == "workflow":
                    dag = sop_dag(sop)
                    return json_response(self, 200, {
                        "instance_id": sop.get("instance_id") or sop.get("id", ""),
                        "workflow_binding": workflow_binding(sop),
                        "dag": dag,
                    })
                if len(path) == 5 and path[4] == "executions":
                    return json_response(self, 200, sop_runs(sop, query))
                if len(path) == 6 and path[4] == "executions":
                    run_file = Path(sop["wiki_local_path"]) / "raw" / "pipeline-runs" / path[5] / "run.json"
                    data = read_json(run_file)
                    if data and not data.get("pipeline_id"):
                        data["pipeline_id"] = path[5]
                    indexed = indexed_run(sop, path[5], rebuild=bool(data))
                    payload = indexed or (run_summary(sop, data) if data else None)
                    return json_response(
                        self,
                        200 if payload else 404,
                        execution_summary(sop, payload) if payload else {"detail": "Execution not found"},
                    )
                if len(path) == 8 and path[4] == "executions" and path[6] == "nodes":
                    data = node_runtime_detail(sop, path[5], path[7])
                    data["execution_id"] = data.get("pipeline_id", path[5])
                    data["instance_id"] = sop.get("instance_id") or sop.get("id", "")
                    return json_response(self, 200, data)
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
                    indexed = indexed_run(sop, path[4], rebuild=bool(data))
                    payload = indexed or (run_summary(sop, data) if data else None)
                    return json_response(
                        self,
                        200 if payload else 404,
                        execution_summary(sop, payload) if payload else {"detail": "Run not found"},
                    )
                if len(path) == 6 and path[3] == "runs":
                    run_dir = Path(sop["wiki_local_path"]) / "raw" / "pipeline-runs" / path[4]
                    section = path[5]
                    if section in {"dag", "context", "artifacts"}:
                        if section == "artifacts":
                            store = run_index_store(sop)
                            indexed_artifacts = None
                            if store:
                                try:
                                    indexed_artifacts = store.get_artifacts(path[4]) if store.get_run(path[4]) else None
                                except Exception:
                                    indexed_artifacts = None
                            data = indexed_artifacts if indexed_artifacts is not None else read_json(run_dir / "artifacts.json")
                            data = artifacts_with_preview(sop, data)
                        else:
                            data = (
                                normalized_run_dag(sop, path[4])
                                if section == "dag"
                                else read_json(run_dir / f"{section}.json")
                            )
                        return json_response(self, 200 if data is not None else 404, data if data is not None else {
                            "detail": f"Run {section} not found"
                        })
                    if section == "artifact-candidates":
                        return json_response(self, 200, {
                            "pipeline_id": path[4],
                            "artifacts": run_artifact_candidates(sop, path[4]),
                        })
                    if section == "events":
                        store = run_index_store(sop)
                        events = []
                        if store:
                            try:
                                events = store.get_events(path[4])
                            except Exception:
                                events = []
                        if not events:
                            event_file = run_dir / "events.jsonl"
                            events = read_run_events(event_file)
                        return json_response(self, 200, {"pipeline_id": path[4], "events": events})
                if len(path) == 7 and path[3] == "runs" and path[5] == "events" and path[6] == "stream":
                    return self.stream_run_events(sop, path[4])
                if len(path) == 7 and path[3] == "runs" and path[5] == "nodes":
                    data = node_runtime_detail(sop, path[4], path[6])
                    return json_response(self, 200, data)
                if len(path) == 8 and path[3] == "runs" and path[5] == "nodes":
                    data = node_runtime_detail(sop, path[4], path[6])
                    section = path[7]
                    if section == "modules":
                        endpoint = str((sop.get("channel") or {}).get("url") or request_endpoint(self))
                        return json_response(self, 200, {
                            "sop_id": sop.get("id", ""),
                            "pipeline_id": path[4],
                            "node_id": path[6],
                            "modules": node_modules(sop, path[6], endpoint, path[4]),
                        })
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
                    if section == "capabilities":
                        return json_response(self, 200, {
                            "pipeline_id": path[4],
                            "node_id": path[6],
                            "capabilities": data["capabilities"],
                        })
                    if section == "plan":
                        plan = data.get("plan")
                        return json_response(self, 200 if plan is not None else 404, plan or {
                            "detail": "Node plan not found"
                        })
                if len(path) == 9 and path[3] == "runs" and path[5] == "nodes" and path[7] == "modules":
                    endpoint = str((sop.get("channel") or {}).get("url") or request_endpoint(self))
                    data = node_module_detail(sop, path[6], path[8], endpoint, path[4])
                    return json_response(self, 200 if data else 404, data or {
                        "detail": f"Node module {path[8]!r} not found"
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
                # GET /api/sop/{instance}/nodes — Node Registry
                if len(path) == 4 and path[3] == "nodes":
                    endpoint = str((sop.get("channel") or {}).get("url") or request_endpoint(self))
                    return json_response(self, 200, node_registry(sop, endpoint))
                # GET /api/sop/{instance}/node-drafts — list drafts
                if len(path) == 4 and path[3] == "node-drafts":
                    drafts_dir = Path(sop["wiki_local_path"]) / "raw" / "node-drafts"
                    drafts = []
                    if drafts_dir.exists():
                        for draft_dir in sorted(drafts_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                            if draft_dir.is_dir():
                                drafts.append({
                                    "draft_id": draft_dir.name,
                                    "node": read_yaml(draft_dir / "node.yaml"),
                                    "validation": read_json(draft_dir / "validation.json") or {},
                                })
                    return json_response(self, 200, {"sop_id": sop.get("id", ""), "drafts": drafts})
                # GET /api/sop/{instance}/nodes/{node_id} — static node config
                if len(path) == 5 and path[3] == "nodes":
                    endpoint = str((sop.get("channel") or {}).get("url") or request_endpoint(self))
                    cfg = node_registry_item(sop, path[4], endpoint)
                    if cfg is None:
                        return json_response(self, 404, {"detail": f"Node {path[4]!r} not found"})
                    return json_response(self, 200, cfg)
                # GET /api/sop/{instance}/nodes/{node_id}/modules
                if len(path) == 6 and path[3] == "nodes" and path[5] == "modules":
                    endpoint = str((sop.get("channel") or {}).get("url") or request_endpoint(self))
                    if node_registry_item(sop, path[4], endpoint) is None:
                        return json_response(self, 404, {"detail": f"Node {path[4]!r} not found"})
                    return json_response(self, 200, {
                        "sop_id": sop.get("id", ""),
                        "node_id": path[4],
                        "modules": node_modules(sop, path[4], endpoint),
                    })
                # GET /api/sop/{instance}/nodes/{node_id}/modules/{module_id}
                if len(path) == 7 and path[3] == "nodes" and path[5] == "modules":
                    endpoint = str((sop.get("channel") or {}).get("url") or request_endpoint(self))
                    data = node_module_detail(sop, path[4], path[6], endpoint)
                    return json_response(self, 200 if data else 404, data or {
                        "detail": f"Node module {path[6]!r} not found"
                    })
                # GET /api/sop/{instance}/nodes/{node_id}/actions
                if len(path) == 6 and path[3] == "nodes" and path[5] == "actions":
                    if node_registry_item(sop, path[4]) is None:
                        return json_response(self, 404, {"detail": f"Node {path[4]!r} not found"})
                    return json_response(self, 200, {
                        "sop_id": sop.get("id", ""),
                        "node_id": path[4],
                        "actions": node_actions(sop.get("id", ""), path[4]),
                    })
            return text_response(self, 200, "ok")
        except Exception as exc:
            return json_response(self, 500, {"detail": str(exc)})

    def stream_run_events(self, sop, pipeline_id):
        run_dir = run_workspace(sop, pipeline_id)
        store = run_index_store(sop)
        indexed = indexed_run(sop, pipeline_id, rebuild=run_dir.exists())
        if not run_dir.exists() and not indexed:
            return json_response(self, 404, {"detail": "Run not found"})
        try:
            last_sequence = int(self.headers.get("Last-Event-ID", "0") or 0)
        except ValueError:
            last_sequence = 0
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.wfile.write(b"retry: 1000\n: connected\n\n")
        self.wfile.flush()
        events_file = run_dir / "events.jsonl"
        heartbeat_at = time.monotonic()
        stream_deadline = time.monotonic() + SSE_STREAM_WINDOW_SECONDS
        while True:
            try:
                events = []
                if store:
                    try:
                        events = store.get_events(pipeline_id, last_sequence)
                    except Exception:
                        events = []
                if not events:
                    events = read_run_events(events_file, last_sequence)
                for event in events:
                    self.wfile.write(format_sse_event(event))
                    self.wfile.flush()
                    last_sequence = event["sequence"]
                run = indexed_run(sop, pipeline_id, rebuild=False) or read_json(run_dir / "run.json") or {}
                if run.get("status") in {"done", "failed", "cancelled"}:
                    break
                if time.monotonic() - heartbeat_at >= SSE_HEARTBEAT_SECONDS:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    heartbeat_at = time.monotonic()
                if time.monotonic() >= stream_deadline:
                    break
                time.sleep(0.5)
            except (BrokenPipeError, ConnectionResetError):
                break
        self.close_connection = True

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

        # POST /api/sop/{instance}/runs/{pipeline_id}/nodes/{node_id}/actions/retry
        if (len(path) == 9 and path[:2] == ["api", "sop"]
                and path[3] == "runs" and path[5] == "nodes" and path[7] == "actions" and path[8] == "retry"):
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

        # POST /api/sop/{instance}/runs/{pipeline_id}/nodes/{node_id}/actions/cancel
        if (len(path) == 9 and path[:2] == ["api", "sop"]
                and path[3] == "runs" and path[5] == "nodes" and path[7] == "actions" and path[8] == "cancel"):
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            _status, result = cancel_node(sop, path[4], path[6], data.get("reason", "用户取消节点"))
            http_code = 404 if _status is None else 200
            return json_response(self, http_code, result)

        # POST /api/sop/{instance}/nodes/{node_id}/actions/trigger
        if (len(path) == 7 and path[:2] == ["api", "sop"]
                and path[3] == "nodes" and path[5] == "actions" and path[6] == "trigger"):
            return json_response(self, 409, {
                "status": "disabled",
                "node_id": path[4],
                "message": "Node trigger action is draft-only in this version",
            })

        # POST /api/sop/{instance}/node-drafts
        if len(path) == 4 and path[:2] == ["api", "sop"] and path[3] == "node-drafts":
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            return json_response(self, 201, create_node_draft(sop, data))

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
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
