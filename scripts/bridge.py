import http.server
import json
import os
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlparse

import yaml

PORT = int(os.environ.get("BRIDGE_PORT", "18789"))
SCRIPT = os.environ.get("BRIDGE_SCRIPT", "")


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


def load_sops():
    sops = []
    for sop_file in sorted(wiki_base().glob("*/sop.yaml")):
        try:
            sop = yaml.safe_load(sop_file.read_text(encoding="utf-8")) or {}
        except Exception:
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
            "name": sop.get("name", sop_id),
            "title": sop.get("title", sop.get("name", sop_id)),
            "version": sop.get("version", ""),
            "repo": sop.get("repo", ""),
            "wiki_dir": sop_file.parent.name,
            "wiki_local_path": str(wiki_path),
            "sop_file": str(sop_file),
            "nodes": nodes,
        })
    return sops


def find_sop(sop_id):
    for sop in load_sops():
        if sop_id in {sop["id"], sop["name"], sop["wiki_dir"]}:
            return sop
    return None


def sop_manifest():
    return {
        "runtime": "youtube-wiki",
        "sops": [
            {
                "id": sop["id"],
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


def sop_runs(sop):
    runs = []
    for run_file in run_files(sop)[:80]:
        data = read_json(run_file)
        if data:
            runs.append(data)
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


class Handler(http.server.BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization")
        self.end_headers()

    def do_GET(self):
        path = [unquote(p) for p in urlparse(self.path).path.strip("/").split("/") if p]
        try:
            if path == ["api", "sop"]:
                return json_response(self, 200, sop_manifest())
            if len(path) >= 3 and path[0] == "api" and path[1] == "sop":
                sop = find_sop(path[2])
                if not sop:
                    return json_response(self, 404, {"detail": "SOP not found"})
                if len(path) == 3:
                    return json_response(self, 200, {k: v for k, v in sop.items() if k != "sop_file"})
                if len(path) == 4 and path[3] == "dag":
                    return json_response(self, 200, sop_dag(sop))
                if len(path) == 4 and path[3] == "runs":
                    return json_response(self, 200, sop_runs(sop))
                if len(path) == 5 and path[3] == "runs":
                    run_file = Path(sop["wiki_local_path"]) / "raw" / "pipeline-runs" / path[4] / "run.json"
                    data = read_json(run_file)
                    return json_response(self, 200 if data else 404, data or {"detail": "Run not found"})
                if len(path) == 7 and path[3] == "runs" and path[5] == "nodes":
                    node_file = Path(sop["wiki_local_path"]) / "raw" / "pipeline-runs" / path[4] / "nodes" / f"{path[6]}.json"
                    data = read_json(node_file)
                    return json_response(self, 200 if data else 404, data or {"detail": "Node not found"})
                if len(path) == 7 and path[3] == "runs" and path[5] == "logs":
                    node_file = Path(sop["wiki_local_path"]) / "raw" / "pipeline-runs" / path[4] / "nodes" / f"{path[6]}.json"
                    node = read_json(node_file) or {}
                    log_file = Path(sop["wiki_local_path"]) / "logs" / "stage-events" / f"{node.get('run_id', '')}.jsonl"
                    log = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
                    return json_response(self, 200, {"pipeline_id": path[4], "node_id": path[6], "log": log})
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
        if len(path) == 4 and path[:2] == ["api", "sop"] and path[3] == "runs":
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            status, result = trigger_sop(sop, data)
            return json_response(self, status, result)

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


print(f"[bridge] 127.0.0.1:{PORT}", flush=True)
http.server.HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
