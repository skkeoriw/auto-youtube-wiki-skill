import http.server
import hashlib
import hmac
import importlib.util
import json
import mimetypes
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
import urllib.error
import urllib.request

import yaml

PORT = int(os.environ.get("BRIDGE_PORT", "18789"))
SCRIPT = os.environ.get("BRIDGE_SCRIPT", "")
REGISTRY_PATH = Path(os.environ.get("SOP_REGISTRY_PATH", str(Path.home() / ".sop" / "registry.json"))).expanduser()
RUNTIME_MANAGEMENT_CONFIG_PATH = Path(os.environ.get(
    "SOP_RUNTIME_MANAGEMENT_CONFIG_PATH",
    str(Path.home() / ".sop" / "runtime-management" / "config.json"),
)).expanduser()
RUNTIME_SETTINGS_CLOUDFLARE_EMAIL = os.environ.get("RUNTIME_SETTINGS_CLOUDFLARE_EMAIL", os.environ.get("CLOUDFLARE_EMAIL", os.environ.get("CF_EMAIL", "")))
RUNTIME_SETTINGS_CLOUDFLARE_API_KEY = os.environ.get("RUNTIME_SETTINGS_CLOUDFLARE_API_KEY", os.environ.get("CLOUDFLARE_API_KEY", os.environ.get("CF_API_KEY", "")))
RUNTIME_SETTINGS_CLOUDFLARE_API_TOKEN = os.environ.get("RUNTIME_SETTINGS_CLOUDFLARE_API_TOKEN", os.environ.get("CLOUDFLARE_API_TOKEN", os.environ.get("CF_API_TOKEN", "")))
RUNTIME_SETTINGS_CLOUDFLARE_ACCOUNT_ID = os.environ.get("RUNTIME_SETTINGS_CLOUDFLARE_ACCOUNT_ID", os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""))
RUNTIME_SETTINGS_D1_DATABASE_ID = os.environ.get("RUNTIME_SETTINGS_D1_DATABASE_ID", os.environ.get("CLOUDFLARE_D1_DATABASE_ID", ""))
RUNTIME_SETTINGS_D1_DATABASE_NAME = os.environ.get("RUNTIME_SETTINGS_D1_DATABASE_NAME", "runtime-settings-db")
RUNTIME_SETTINGS_BACKEND = os.environ.get("RUNTIME_SETTINGS_BACKEND", "d1")
RUNTIME_SETTINGS_D1_TABLE = "global_settings"
RUNTIME_SETTINGS_D1_AUDIT_TABLE = "global_settings_audit"
SSE_STREAM_WINDOW_SECONDS = float(os.environ.get("SSE_STREAM_WINDOW_SECONDS", "5"))
SSE_HEARTBEAT_SECONDS = float(os.environ.get("SSE_HEARTBEAT_SECONDS", "3"))
GENERIC_NODE_CLI_URL = os.environ.get("SOP_NODE_CLI_URL", "https://skill.vyibc.com/sop-node.sh")
_RUN_INDEX_CLASS = None
_SOP_READ_CACHE = {}
_SOP_READ_CACHE_TTL_SECONDS = float(os.environ.get("SOP_READ_CACHE_TTL_SECONDS", "3"))

RUNTIME_MANAGEMENT_COMMON_NODES = [
    "management-request-validate",
    "action-router",
]

CREATE_RUNTIME_NODES = [
    "parse-create-runtime-request",
    "ssh-preflight",
    "infer-runtime-plan",
    "install-base-deps",
    "clone-runtime-repos",
    "write-runtime-config",
    "init-runtime-registry",
    "start-runtime-bridge",
    "register-channel",
    "verify-runtime-visible",
]

DELETE_RUNTIME_NODES = [
    "parse-delete-runtime-request",
    "resolve-runtime-target",
    "safety-check",
    "stop-runtime-services",
    "unregister-channel",
    "cleanup-runtime-services",
    "cleanup-runtime-files",
    "verify-local-clean",
    "verify-channel-removed",
    "verify-runtime-removed",
]

CREATE_INSTANCE_NODES = [
    "parse-create-instance-request",
    "prepare-instance-workspace",
    "upsert-instance-registry",
    "verify-instance-visible",
]

DELETE_INSTANCE_NODES = [
    "parse-delete-instance-request",
    "safety-check-instance",
    "remove-instance-registry",
    "cleanup-instance-files",
    "verify-instance-removed",
]

RUNTIME_MANAGEMENT_SUMMARY_NODE = "management-summary"
RUNTIME_MANAGEMENT_ACTIONS = {
    "create-runtime",
    "delete-runtime",
    "create-instance",
    "delete-instance",
}
RUNTIME_MANAGEMENT_NODES = [
    *RUNTIME_MANAGEMENT_COMMON_NODES,
    *CREATE_RUNTIME_NODES,
    *DELETE_RUNTIME_NODES,
    *CREATE_INSTANCE_NODES,
    *DELETE_INSTANCE_NODES,
    RUNTIME_MANAGEMENT_SUMMARY_NODE,
]

SECRET_KEYS = {"password", "token", "key", "secret", "credential", "private_key", "ssh_private_key", "private_key_content"}
RUNTIME_CAPABILITY_ENV = {
    "GITHUB_TOKEN": ["github_token", "repo_token"],
    "DEEPSEEK_API_KEY": ["deepseek_api_key", "hermes_deepseek_api_key"],
    "WIKI_LLM_PROVIDER": ["wiki_llm_provider", "default_model_provider"],
    "WIKI_DEEPSEEK_MODEL": ["wiki_deepseek_model", "hermes_default_model"],
    "HERMES_MODEL_PROVIDER": ["hermes_model_provider"],
    "HERMES_MODEL": ["hermes_model", "hermes_default_model"],
    "HERMES_MODEL_BASE_URL": ["hermes_model_base_url", "hermes_base_url", "openai_base_url"],
    "HERMES_OPENAI_API_KEY": ["hermes_openai_api_key", "openai_api_key"],
    "OPENAI_API_KEY": ["openai_api_key", "hermes_openai_api_key"],
    "GOOGLE_CLOUD_API_KEY": ["google_cloud_api_key", "gemini_api_key"],
    "GEMINI_API_KEY": ["gemini_api_key"],
    "WIKI_GEMINI_MODEL": ["wiki_gemini_model", "gemini_model"],
    "GOOGLE_PROJECT_ID": ["google_project_id"],
    "VERTEX_LOCATION": ["vertex_location"],
    "WIKI_VERTEX_MODEL": ["wiki_vertex_model", "vertex_model"],
    "HERMES_WEBHOOK_TOKEN": ["hermes_webhook_token"],
    "HERMES_WEBHOOK_PORT": ["hermes_webhook_port"],
    "HERMES_WEBHOOK_URL": ["hermes_webhook_url"],
    "HERMES_SMOKE_ROUTE": ["hermes_smoke_route"],
    "WEBHOOK_PUBLIC_HOST": ["webhook_public_host", "hermes_public_host"],
    "NOTEBOOKLM_BRIDGE_URL": ["notebooklm_bridge_url"],
    "NOTEBOOKLM_BRIDGE_TOKEN": ["notebooklm_bridge_token"],
    "NOTEBOOKLM_CLIENT_ID": ["notebooklm_client_id"],
    "BRIDGE_PORT": ["bridge_port"],
    "YOUTUBE_WIKI_TG_TOKEN": ["youtube_wiki_tg_token", "telegram_token"],
    "YOUTUBE_WIKI_TG_CHAT_ID": ["youtube_wiki_tg_chat_id", "telegram_chat_id"],
    "YOUTUBE_CONTENT_API_URL": ["youtube_content_api_url"],
    "YOUTUBE_CONTENT_API_TOKEN": ["youtube_content_api_token"],
    "YOUTUBE_RESEARCH_WORKFLOW_URL": ["youtube_research_workflow_url"],
    "YOUTUBE_RESEARCH_WORKFLOW_TOKEN": ["youtube_research_workflow_token"],
    "CLOUDFLARE_EMAIL": ["cloudflare_email", "cf_email"],
    "CLOUDFLARE_API_KEY": ["cloudflare_api_key", "cf_api_key", "CF_API_KEY"],
    "RUNTIME_SETTINGS_BACKEND": ["runtime_settings_backend"],
    "RUNTIME_SETTINGS_CLOUDFLARE_EMAIL": ["runtime_settings_cloudflare_email"],
    "RUNTIME_SETTINGS_CLOUDFLARE_API_KEY": ["runtime_settings_cloudflare_api_key"],
    "RUNTIME_SETTINGS_CLOUDFLARE_API_TOKEN": ["runtime_settings_cloudflare_api_token"],
    "RUNTIME_SETTINGS_CLOUDFLARE_ACCOUNT_ID": ["runtime_settings_cloudflare_account_id", "cloudflare_account_id"],
    "RUNTIME_SETTINGS_D1_DATABASE_ID": ["runtime_settings_d1_database_id", "cloudflare_d1_database_id", "d1_database_id"],
    "RUNTIME_SETTINGS_D1_DATABASE_NAME": ["runtime_settings_d1_database_name", "d1_database_name"],
    "TUNNEL_API": ["tunnel_api_url"],
    "SOP_UI_URL": ["sop_ui_url"],
}
RUNTIME_MANAGEMENT_REQUEST_DEFAULTS = {
    "GITHUB_CHANGFENGHU_TOKEN": ["github_changfenghu_token", "changfenghu_github_token"],
    "GITHUB_SKKEORIW_TOKEN": ["github_skkeoriw_token", "skkeoriw_github_token"],
    "AGENT_REPO": ["agent_repo", "brain_repo"],
    "SKILL_REPO": ["skill_repo"],
    "AUTO_DOMAIN_REPO": ["auto_domain_repo"],
    "AUTO_DOMAIN_TUNNEL_REPO": ["auto_domain_tunnel_repo"],
    "SKILL_PUBLISHER_REPO": ["skill_publisher_repo"],
    "RUNTIME_TARGET_SSH_COMMAND": ["ssh_command"],
    "RUNTIME_TARGET_PRIVATE_KEY": ["private_key", "ssh_private_key", "ssh_private_key_content"],
    "RUNTIME_TARGET_PRIVATE_KEY_B64": ["private_key_b64", "ssh_private_key_b64"],
    "RUNTIME_TARGET_RUNTIME_ID": ["runtime_id"],
    "RUNTIME_TARGET_CHANNEL_URL": ["channel_url"],
}
CREATE_RUNTIME_MANAGEMENT_DEFAULT_EXCLUDES = {
    "RUNTIME_TARGET_RUNTIME_ID",
    "RUNTIME_TARGET_CHANNEL_URL",
}
RUNTIME_REQUIRED_ENV = {
    "GITHUB_TOKEN",
    "DEEPSEEK_API_KEY",
    "HERMES_OPENAI_API_KEY",
    "NOTEBOOKLM_BRIDGE_URL",
    "NOTEBOOKLM_BRIDGE_TOKEN",
    "CLOUDFLARE_API_KEY",
}
RUNTIME_MANAGEMENT_REQUIRED_DEFAULTS = {
    "RUNTIME_TARGET_SSH_COMMAND",
    "RUNTIME_TARGET_PRIVATE_KEY",
    "RUNTIME_TARGET_PRIVATE_KEY_B64",
}
RUNTIME_CONFIG_CATEGORIES = {
    "GITHUB_TOKEN": "github",
    "DEEPSEEK_API_KEY": "hermes",
    "HERMES_MODEL_PROVIDER": "hermes",
    "HERMES_MODEL": "hermes",
    "HERMES_MODEL_BASE_URL": "hermes",
    "HERMES_OPENAI_API_KEY": "hermes",
    "OPENAI_API_KEY": "hermes",
    "WIKI_LLM_PROVIDER": "llm",
    "WIKI_DEEPSEEK_MODEL": "llm",
    "GOOGLE_CLOUD_API_KEY": "llm",
    "GEMINI_API_KEY": "llm",
    "WIKI_GEMINI_MODEL": "llm",
    "GOOGLE_PROJECT_ID": "llm",
    "VERTEX_LOCATION": "llm",
    "WIKI_VERTEX_MODEL": "llm",
    "HERMES_WEBHOOK_TOKEN": "hermes",
    "HERMES_WEBHOOK_PORT": "hermes",
    "HERMES_WEBHOOK_URL": "hermes",
    "WEBHOOK_PUBLIC_HOST": "hermes",
    "NOTEBOOKLM_BRIDGE_URL": "notebooklm",
    "NOTEBOOKLM_BRIDGE_TOKEN": "notebooklm",
    "NOTEBOOKLM_CLIENT_ID": "notebooklm",
    "BRIDGE_PORT": "runtime",
    "YOUTUBE_WIKI_TG_TOKEN": "telegram",
    "YOUTUBE_WIKI_TG_CHAT_ID": "telegram",
    "YOUTUBE_CONTENT_API_URL": "youtube",
    "YOUTUBE_CONTENT_API_TOKEN": "youtube",
    "YOUTUBE_RESEARCH_WORKFLOW_URL": "youtube",
    "YOUTUBE_RESEARCH_WORKFLOW_TOKEN": "youtube",
    "CLOUDFLARE_EMAIL": "cloudflare",
    "CLOUDFLARE_API_KEY": "cloudflare",
    "RUNTIME_SETTINGS_BACKEND": "settings",
    "RUNTIME_SETTINGS_CLOUDFLARE_EMAIL": "settings",
    "RUNTIME_SETTINGS_CLOUDFLARE_API_KEY": "settings",
    "RUNTIME_SETTINGS_CLOUDFLARE_API_TOKEN": "settings",
    "RUNTIME_SETTINGS_CLOUDFLARE_ACCOUNT_ID": "settings",
    "RUNTIME_SETTINGS_D1_DATABASE_ID": "settings",
    "RUNTIME_SETTINGS_D1_DATABASE_NAME": "settings",
    "TUNNEL_API": "cloudflare",
    "SOP_UI_URL": "runtime",
    "GITHUB_CHANGFENGHU_TOKEN": "github",
    "GITHUB_SKKEORIW_TOKEN": "github",
    "AGENT_REPO": "repo",
    "SKILL_REPO": "repo",
    "AUTO_DOMAIN_REPO": "repo",
    "AUTO_DOMAIN_TUNNEL_REPO": "repo",
    "SKILL_PUBLISHER_REPO": "repo",
    "RUNTIME_TARGET_SSH_COMMAND": "target",
    "RUNTIME_TARGET_PRIVATE_KEY": "target",
    "RUNTIME_TARGET_PRIVATE_KEY_B64": "target",
    "RUNTIME_TARGET_RUNTIME_ID": "target",
    "RUNTIME_TARGET_CHANNEL_URL": "target",
}

RUNTIME_NODE_EXPLAIN = {
    "management-request-validate": ("校验管理请求", "校验创建/删除 Runtime 或 Instance 的请求，并把敏感信息转为 run-scoped secret 引用。", ["识别管理动作", "解析 Runtime、通道、SSH 和 instance 输入", "写入脱敏请求与上下文"], ["缺少 SSH 或 private key 时检查 Runtime Management 默认配置。"]),
    "action-router": ("选择执行分支", "根据 action 选择 Runtime/Instance 创建或删除分支，并把未选择分支标记为 skipped。", ["读取 action", "选择 create/delete runtime/instance 分支", "生成 active/skipped 节点列表"], ["action 只能是 create-runtime、delete-runtime、create-instance 或 delete-instance。"]),
    "parse-create-runtime-request": ("解析创建 Runtime 请求", "整理目标机器、Runtime 身份、默认 instance 和 secret 引用。", ["解析 SSH command", "生成 runtime_id/channel_url", "确认 runtime-management instance"], ["runtime_id 应使用 runtime-ip 形式。"]),
    "ssh-preflight": ("SSH 预检", "验证目标机器是否可登录、用户和基础环境是否满足初始化要求。", ["登录目标机器", "读取系统信息", "检查磁盘和权限"], ["SSH 失败时确认 authorized_keys 和 key 权限。"]),
    "infer-runtime-plan": ("推断初始化计划", "生成 Runtime 初始化计划，并检查 channel/runtime 是否冲突或可幂等收敛。", ["查询 tunnel-admin", "检查 client_ip", "生成 create-new/converge-existing 计划"], ["同名 channel 属于其他 IP 时必须换名或先删除。"]),
    "install-base-deps": ("安装基础依赖", "安装或确认 git、curl、python3、node 等 Runtime 必需命令。", ["识别包管理器", "安装依赖", "输出命令版本"], ["包安装失败时检查网络和 sudo 权限。"]),
    "clone-runtime-repos": ("拉取 Runtime 仓库", "在目标机器 clone 或 fast-forward SOP Core 与 Skill CLI 仓库。", ["配置 Git 凭据", "更新 agent-brain-plugins", "更新 auto-youtube-wiki-skill", "校验关键文件"], ["GitHub clone 失败时检查 GITHUB_TOKEN 权限。", "origin mismatch 表示目标目录已有错误仓库。"]),
    "write-runtime-config": ("写入 Runtime 配置", "把继承配置和目标 Runtime 身份写入目标机器环境文件。", ["合并管理配置", "写入 env 文件", "校验必需配置"], ["配置缺失时先到 Settings 初始化管理配置。"]),
    "init-runtime-registry": ("初始化 Registry", "初始化 registry、runtime-management workspace 和 run index。", ["创建 registry", "写入 enabled instance", "初始化 run index"], ["registry 不存在时检查 init-new-machine 输出。"]),
    "start-runtime-bridge": ("启动 Runtime Bridge", "启动目标 SOP SPI bridge，并验证本地 /api/sop。", ["停止旧进程", "启动 bridge", "检查本地 SPI"], ["本地 SPI 不通时查看 bridge log。"]),
    "register-channel": ("注册公网通道", "通过 auto-domain 注册公网 channel，并验证 metadata 与 UI discovery。", ["注册 channel", "写入 metadata", "验证公网 SPI/CORS/UI"], ["tunnel inactive 时检查 auto-domain 和 Cloudflare 配置。"]),
    "verify-runtime-visible": ("验证 Runtime 可见", "确认新 Runtime 公网可访问并暴露 runtime-management instance。", ["请求公网 /api/sop", "确认 runtime_id", "确认 instance"], ["不可见时按 bridge、tunnel、metadata 顺序排查。"]),
    "parse-delete-runtime-request": ("解析删除 Runtime 请求", "整理删除目标、SSH 信息、force 策略和 secret 引用。", ["解析 runtime_id/channel", "继承 SSH target", "写入删除上下文"], ["删除目标不明确时确认 runtime_id 或默认目标。"]),
    "resolve-runtime-target": ("解析删除目标", "从 tunnel-admin 查找目标 Runtime，确定删除对象和注册状态。", ["查询 tunnel-admin", "匹配 channel/runtime", "读取 metadata"], ["找不到目标时确认 runtime_id；force 可继续清理本地残留。"]),
    "safety-check": ("删除安全检查", "删除前检查目标 Runtime 是否有运行中的 executions。", ["查询 runs", "识别 running", "根据 force 决策"], ["存在 running execution 时不要普通删除。"]),
    "stop-runtime-services": ("停止 Runtime 服务", "停止目标机器 bridge 和 auto-domain 相关进程。", ["停止 bridge", "停止 channel 进程", "避免杀掉当前 SSH"], ["remaining_processes 不为空时继续清理服务残留。"]),
    "unregister-channel": ("反注册公网通道", "从 tunnel-admin 删除目标 Runtime 的公网 channel。", ["调用 tunnel delete API", "删除 metadata", "记录响应"], ["反注册失败时检查 TUNNEL_API 和权限。"]),
    "cleanup-runtime-services": ("清理服务残留", "清理 Runtime、Hermes、agent、auto-domain 服务和进程残留。", ["二次停止服务", "清理标记和缓存", "复查进程端口"], ["remaining_ports 不为空时目标仍有服务监听。"]),
    "cleanup-runtime-files": ("清理 Runtime 文件", "删除 create-runtime 创建的仓库、配置、registry、workspace、缓存和 secret 文件。", ["删除仓库", "删除 env/registry/workspace", "删除缓存", "记录残留路径"], ["remaining_paths 不为空时说明没有清理干净。"]),
    "verify-local-clean": ("验证本地清理干净", "确认目标机器没有 Runtime 文件、进程、端口和 Hermes/agent 残留。", ["检查路径", "检查进程", "检查端口"], ["任何 remaining_* 不为空都不能认为删除干净。"]),
    "verify-channel-removed": ("验证通道已移除", "确认 tunnel-admin 不再把目标 channel 标记为 active/local ok。", ["查询 tunnel-admin", "确认 channel 非 active", "记录状态"], ["tunnel 仍 active 说明反注册未生效。"]),
    "verify-runtime-removed": ("验证 Runtime 已删除", "综合验证公网 SPI 不可用、tunnel 不活跃、本地清理通过。", ["请求公网 SPI", "复核 tunnel", "合并本地和通道验证"], ["公网 502 但 tunnel 仍 active 不能算删除成功。"]),
    "parse-create-instance-request": ("解析创建 Instance 请求", "标准化 instance_id、repo、sop_type 和目标 Runtime SSH 上下文。", ["读取 create-instance 请求", "继承 Runtime 连接配置", "写入 instance 创建上下文"], ["instance_id/repo 为空时无法创建业务实例。"]),
    "prepare-instance-workspace": ("准备 Instance 工作区", "在目标 Runtime 上创建或收敛该 Instance 的独立工作目录。", ["创建 workspace", "准备 repo/raw/artifacts/runs 目录", "记录路径"], ["workspace 路径冲突时检查 instance_id 和 repo 名称。"]),
    "upsert-instance-registry": ("注册 Instance", "把 Instance 写入 Runtime registry，并保持可重复执行。", ["更新 registry", "确认 enabled 状态", "记录 registry 报告"], ["registry 无法写入时检查目标机器 ~/.sop 权限。"]),
    "verify-instance-visible": ("验证 Instance 可见", "确认 Runtime SPI 已能发现新增 Instance。", ["请求 /api/sop", "匹配 instance_id", "记录可见性"], ["Instance 不可见时检查 bridge 是否重载 registry。"]),
    "parse-delete-instance-request": ("解析删除 Instance 请求", "定位要删除的 Instance、workspace、repo 和 force 策略。", ["读取 delete-instance 请求", "继承 Runtime 连接配置", "写入删除上下文"], ["runtime-management 是受保护管理实例，不能当作业务 Instance 删除。"]),
    "safety-check-instance": ("检查 Instance 运行中任务", "删除前检查该 Instance 是否还有 running/pending executions。", ["查询 runs", "识别运行中任务", "根据 force 决策"], ["存在运行中任务时普通删除应停止。"]),
    "remove-instance-registry": ("移除 Instance Registry", "从 Runtime registry 中移除目标 Instance。", ["读取 registry", "删除 instance 条目", "写回 registry"], ["registry 仍包含目标 instance 时删除不能算完成。"]),
    "cleanup-instance-files": ("清理 Instance 文件", "删除该 Instance 的 workspace、repo、run 记录和产物索引，不影响 Runtime。", ["删除 workspace", "检查残留路径", "记录清理报告"], ["remaining_paths 不为空说明还有残留。"]),
    "verify-instance-removed": ("验证 Instance 已删除", "确认 SPI 不再暴露该 Instance 且工作区已清理。", ["请求 /api/sop", "检查 workspace", "合并删除结论"], ["只删 registry 不删 workspace 不算清理干净。"]),
    "management-summary": ("生成管理摘要", "汇总 Runtime/Instance create/delete 分支结果，形成可交接执行结论。", ["读取分支报告", "计算最终 status", "写入 summary"], ["摘要失败通常说明前置分支报告缺失。"]),
}


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


def write_json(path, data, mode=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if mode is not None:
        path.chmod(mode)


def runtime_settings_cloudflare_headers():
    if RUNTIME_SETTINGS_CLOUDFLARE_API_TOKEN:
        return {"Authorization": f"Bearer {RUNTIME_SETTINGS_CLOUDFLARE_API_TOKEN}"}
    if RUNTIME_SETTINGS_CLOUDFLARE_EMAIL and RUNTIME_SETTINGS_CLOUDFLARE_API_KEY:
        return {
            "X-Auth-Email": RUNTIME_SETTINGS_CLOUDFLARE_EMAIL,
            "X-Auth-Key": RUNTIME_SETTINGS_CLOUDFLARE_API_KEY,
        }
    return {}


def runtime_settings_d1_ready():
    return bool(RUNTIME_SETTINGS_CLOUDFLARE_ACCOUNT_ID and RUNTIME_SETTINGS_D1_DATABASE_ID and runtime_settings_cloudflare_headers())


def runtime_settings_backend():
    if str(RUNTIME_SETTINGS_BACKEND).lower() == "d1" and runtime_settings_d1_ready():
        return "d1"
    return "file"


def runtime_settings_alias_map():
    mapping = {}
    for canonical, aliases in {**RUNTIME_CAPABILITY_ENV, **RUNTIME_MANAGEMENT_REQUEST_DEFAULTS}.items():
        mapping[canonical] = canonical
        mapping[canonical.lower()] = canonical
        for alias in aliases:
            mapping[alias] = canonical
            mapping[alias.lower()] = canonical
    return mapping


def normalize_runtime_settings_values(values):
    aliases = runtime_settings_alias_map()
    normalized = {}
    for key, value in (values or {}).items():
        canonical = aliases.get(str(key).strip(), str(key).strip())
        text = "" if value is None else str(value).strip()
        if text:
            normalized[canonical] = text
    return normalized


def cloudflare_request(method, path, payload=None):
    headers = runtime_settings_cloudflare_headers()
    if not headers:
        raise RuntimeError("Cloudflare credentials are not configured")
    req_headers = {"Content-Type": "application/json", **headers}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4{path}",
        data=data,
        headers=req_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Cloudflare API {method} {path} failed: HTTP {exc.code}: {raw[:500]}")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cloudflare API {method} {path} failed: {exc.reason}")
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Cloudflare API {method} {path} returned invalid JSON: {exc}")
    if not body.get("success", True):
        raise RuntimeError(f"Cloudflare API {method} {path} failed: {body.get('errors') or body}")
    return body


def runtime_settings_d1_raw(payload):
    return cloudflare_request(
        "POST",
        f"/accounts/{RUNTIME_SETTINGS_CLOUDFLARE_ACCOUNT_ID}/d1/database/{RUNTIME_SETTINGS_D1_DATABASE_ID}/raw",
        payload,
    )


def runtime_settings_ensure_d1_schema():
    if not runtime_settings_d1_ready():
        return False
    schema_sql = "; ".join([
        f"""
        CREATE TABLE IF NOT EXISTS {RUNTIME_SETTINGS_D1_TABLE} (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          category TEXT NOT NULL DEFAULT 'runtime',
          secret INTEGER NOT NULL DEFAULT 0,
          source TEXT NOT NULL DEFAULT 'management_config',
          updated_at TEXT NOT NULL,
          updated_by TEXT NOT NULL DEFAULT '',
          version INTEGER NOT NULL DEFAULT 1
        )
        """.strip(),
        f"CREATE INDEX IF NOT EXISTS idx_{RUNTIME_SETTINGS_D1_TABLE}_category ON {RUNTIME_SETTINGS_D1_TABLE}(category)",
        f"""
        CREATE TABLE IF NOT EXISTS {RUNTIME_SETTINGS_D1_AUDIT_TABLE} (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          key TEXT NOT NULL,
          value TEXT NOT NULL,
          category TEXT NOT NULL DEFAULT 'runtime',
          secret INTEGER NOT NULL DEFAULT 0,
          source TEXT NOT NULL DEFAULT 'management_config',
          updated_at TEXT NOT NULL,
          updated_by TEXT NOT NULL DEFAULT '',
          version INTEGER NOT NULL DEFAULT 1
        )
        """.strip(),
        f"CREATE INDEX IF NOT EXISTS idx_{RUNTIME_SETTINGS_D1_AUDIT_TABLE}_key ON {RUNTIME_SETTINGS_D1_AUDIT_TABLE}(key)",
    ])
    runtime_settings_d1_raw({"sql": schema_sql})
    return True


def runtime_settings_d1_rows():
    if not runtime_settings_d1_ready():
        return []
    runtime_settings_ensure_d1_schema()
    data = runtime_settings_d1_raw({
        "sql": (
            f"SELECT key, value, category, secret, source, updated_at, updated_by, version "
            f"FROM {RUNTIME_SETTINGS_D1_TABLE} ORDER BY key"
        ),
    })
    results = data.get("result") or []
    if not results:
        return []
    rows = (results[0].get("results") or {}).get("rows") or []
    columns = (results[0].get("results") or {}).get("columns") or []
    items = []
    for row in rows:
        item = {}
        for idx, column in enumerate(columns):
            item[column] = row[idx] if idx < len(row) else None
        items.append(item)
    return items


def runtime_settings_d1_values():
    values, _updated_at = runtime_settings_d1_snapshot()
    return values


def runtime_settings_d1_snapshot():
    values = {}
    updated_at = ""
    for row in runtime_settings_d1_rows():
        key = str(row.get("key") or "").strip()
        value = row.get("value")
        if key and value not in {None, ""}:
            values[key] = str(value)
        row_updated_at = str(row.get("updated_at") or "")
        if row_updated_at and row_updated_at > updated_at:
            updated_at = row_updated_at
    return normalize_runtime_settings_values(values), updated_at


def runtime_settings_d1_versions():
    versions = {}
    for row in runtime_settings_d1_rows():
        key = str(row.get("key") or "").strip()
        if key:
            try:
                versions[key] = int(row.get("version") or 0)
            except Exception:
                versions[key] = 0
    return versions


def runtime_settings_d1_save(values, updated_by="runtime-management"):
    if not runtime_settings_d1_ready():
        raise RuntimeError("D1 backend is not configured")
    now = datetime.now(timezone.utc).isoformat()
    versions = runtime_settings_d1_versions()
    batch = []
    changed = {}
    for key, value in normalize_runtime_settings_values(values).items():
        version = versions.get(key, 0) + 1
        category = RUNTIME_CONFIG_CATEGORIES.get(key, "runtime")
        secret = 1 if is_secret_key(key) else 0
        batch.append({
            "sql": (
                f"INSERT INTO {RUNTIME_SETTINGS_D1_TABLE} "
                "(key, value, category, secret, source, updated_at, updated_by, version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET "
                "value=excluded.value, "
                "category=excluded.category, "
                "secret=excluded.secret, "
                "source=excluded.source, "
                "updated_at=excluded.updated_at, "
                "updated_by=excluded.updated_by, "
                "version=excluded.version"
            ),
            "params": [key, value, category, secret, "management_config", now, updated_by, version],
        })
        batch.append({
            "sql": (
                f"INSERT INTO {RUNTIME_SETTINGS_D1_AUDIT_TABLE} "
                "(key, value, category, secret, source, updated_at, updated_by, version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            "params": [key, value, category, secret, "management_config", now, updated_by, version],
        })
        changed[key] = value
    if batch:
        runtime_settings_d1_raw({"batch": batch})
    return changed


def runtime_settings_d1_has_rows():
    if not runtime_settings_d1_ready():
        return False
    runtime_settings_ensure_d1_schema()
    data = runtime_settings_d1_raw({
        "sql": f"SELECT COUNT(*) AS count FROM {RUNTIME_SETTINGS_D1_TABLE}",
    })
    results = data.get("result") or []
    if not results:
        return False
    rows = (results[0].get("results") or {}).get("rows") or []
    return bool(rows and rows[0] and int(rows[0][0]) > 0)


def runtime_settings_load_from_file():
    data = read_json(RUNTIME_MANAGEMENT_CONFIG_PATH)
    if not isinstance(data, dict):
        return {"values": {}, "updated_at": ""}
    values = data.get("values") if isinstance(data.get("values"), dict) else {}
    return {
        "values": normalize_runtime_settings_values(values),
        "updated_at": str(data.get("updated_at") or ""),
    }


def runtime_settings_save_to_file(values, updated_at=None):
    payload = {
        "values": normalize_runtime_settings_values(values),
        "updated_at": updated_at or datetime.now(timezone.utc).isoformat(),
    }
    write_json(RUNTIME_MANAGEMENT_CONFIG_PATH, payload, mode=0o600)
    return payload


def runtime_settings_load():
    backend = runtime_settings_backend()
    file_data = runtime_settings_load_from_file()
    if backend == "d1":
        try:
            if not runtime_settings_d1_has_rows() and file_data["values"]:
                runtime_settings_d1_save(file_data["values"], updated_by="bootstrap-from-file")
                return {
                    "values": file_data["values"],
                    "updated_at": file_data["updated_at"] or datetime.now(timezone.utc).isoformat(),
                    "backend": "d1",
                }
            if runtime_settings_d1_has_rows():
                values, updated_at = runtime_settings_d1_snapshot()
                if values:
                    return {"values": values, "updated_at": updated_at or datetime.now(timezone.utc).isoformat(), "backend": "d1"}
        except Exception:
            pass
    return {**file_data, "backend": "file"}


def mask_value(value):
    if value is None or value == "":
        return value
    if isinstance(value, (list, dict)):
        # A secret-named field can still hold a list/dict (e.g. updated_keys);
        # recurse rather than crash on the unhashable membership test below.
        return mask_data(value)
    text = str(value)
    if len(text) <= 8:
        return "***"
    return f"{text[:3]}***{text[-3:]}"


def is_secret_key(key):
    key_l = str(key).lower()
    return any(secret in key_l for secret in SECRET_KEYS)


def display_config_value(key, value):
    if value in {None, ""}:
        return ""
    return mask_value(value) if is_secret_key(key) else str(value)


def mask_data(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if is_secret_key(key):
                result[key] = mask_value(item) if item else item
            else:
                result[key] = mask_data(item)
        return result
    if isinstance(value, list):
        return [mask_data(item) for item in value]
    return value


def read_yaml(path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def read_env_file_values(path):
    env_path = Path(path).expanduser()
    values = {}
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            if key:
                values[key] = value
    except Exception:
        return {}
    return values


def runtime_config_group_status(items):
    groups = {}
    for item in items:
        category = item.get("category") or "runtime"
        groups.setdefault(category, False)
        if item.get("present"):
            groups[category] = True
    groups["llm"] = groups.get("llm", False) or groups.get("hermes", False)
    groups["tunnel"] = groups.get("cloudflare", False)
    return groups


def runtime_config_inheritance_preview(sop):
    env_file = os.environ.get("YOUTUBE_WIKI_ENV_FILE", str(Path.home() / ".agent-brain-plugins.env"))
    env_file_values = read_env_file_values(env_file)
    management_values = read_runtime_management_config_values()
    items = []
    for key, aliases in {**RUNTIME_CAPABILITY_ENV, **RUNTIME_MANAGEMENT_REQUEST_DEFAULTS}.items():
        source = "missing"
        raw_value = ""
        matched_key = ""
        candidate_keys = [key, *aliases]
        for candidate in candidate_keys:
            if candidate in os.environ and os.environ.get(candidate, "") != "":
                source = "environment"
                raw_value = os.environ.get(candidate, "")
                matched_key = candidate
                break
        if not raw_value:
            for candidate in candidate_keys:
                if candidate in management_values and management_values.get(candidate, "") != "":
                    source = "management_config"
                    raw_value = management_values.get(candidate, "")
                    matched_key = candidate
                    break
        if not raw_value:
            for candidate in candidate_keys:
                if candidate in env_file_values and env_file_values.get(candidate, "") != "":
                    source = "env_file"
                    raw_value = env_file_values.get(candidate, "")
                    matched_key = candidate
                    break
        items.append({
            "key": key,
            "aliases": aliases,
            "matched_key": matched_key,
            "source": source,
            "present": bool(raw_value),
            "masked_value": display_config_value(key, raw_value),
            "secret": is_secret_key(key),
            "required": key in RUNTIME_REQUIRED_ENV or key in RUNTIME_MANAGEMENT_REQUIRED_DEFAULTS,
            "category": RUNTIME_CONFIG_CATEGORIES.get(key, "runtime"),
        })
    return {
        "instance_id": sop.get("instance_id") or sop.get("id", "runtime-management"),
        "env_file": str(Path(env_file).expanduser()),
        "items": items,
        "groups": runtime_config_group_status(items),
        "note": "Secret-like values are masked; field names, source and presence are always shown.",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def read_runtime_management_config():
    return runtime_settings_load()


def read_runtime_management_config_values():
    return read_runtime_management_config().get("values", {})


def runtime_management_config_preview(sop):
    data = read_runtime_management_config()
    values = data.get("values", {})
    backend = data.get("backend", runtime_settings_backend())
    items = []
    for key, aliases in {**RUNTIME_CAPABILITY_ENV, **RUNTIME_MANAGEMENT_REQUEST_DEFAULTS}.items():
        matched_key = next((candidate for candidate in [key, *aliases] if values.get(candidate)), "")
        raw_value = values.get(matched_key, "") if matched_key else ""
        items.append({
            "key": key,
            "aliases": aliases,
            "matched_key": matched_key,
            "source": "management_config" if raw_value else "missing",
            "present": bool(raw_value),
            "masked_value": display_config_value(key, raw_value),
            "secret": is_secret_key(key),
            "required": key in RUNTIME_REQUIRED_ENV or key in RUNTIME_MANAGEMENT_REQUIRED_DEFAULTS,
            "category": RUNTIME_CONFIG_CATEGORIES.get(key, "runtime"),
        })
    return {
        "instance_id": sop.get("instance_id") or sop.get("id", "runtime-management"),
        "config_path": str(RUNTIME_MANAGEMENT_CONFIG_PATH),
        "backend": backend,
        "d1": {
            "enabled": runtime_settings_d1_ready(),
            "account_id": mask_value(RUNTIME_SETTINGS_CLOUDFLARE_ACCOUNT_ID) if RUNTIME_SETTINGS_CLOUDFLARE_ACCOUNT_ID else "",
            "database_id": mask_value(RUNTIME_SETTINGS_D1_DATABASE_ID) if RUNTIME_SETTINGS_D1_DATABASE_ID else "",
            "database_name": RUNTIME_SETTINGS_D1_DATABASE_NAME,
        },
        "items": items,
        "groups": runtime_config_group_status(items),
        "updated_at": data.get("updated_at", ""),
        "note": "Raw saved values are never returned by this API.",
    }


def save_runtime_management_config(values):
    current = normalize_runtime_settings_values(read_runtime_management_config_values())
    allowed_keys = set(runtime_settings_alias_map())
    changed = {}
    for key, value in (values or {}).items():
        normalized_key = runtime_settings_alias_map().get(str(key).strip(), str(key).strip())
        if normalized_key not in allowed_keys:
            continue
        text = str(value).strip()
        if text:
            current[normalized_key] = text
            changed[normalized_key] = text
    payload = {"values": current, "updated_at": datetime.now(timezone.utc).isoformat()}
    if runtime_settings_backend() == "d1":
        try:
            runtime_settings_d1_save(changed, updated_by="management-config-save")
            payload = runtime_settings_save_to_file(current, payload["updated_at"])
        except Exception:
            write_json(RUNTIME_MANAGEMENT_CONFIG_PATH, payload, mode=0o600)
    else:
        write_json(RUNTIME_MANAGEMENT_CONFIG_PATH, payload, mode=0o600)
    return changed


def current_runtime_inheritable_values(overwrite=False):
    env_file = os.environ.get("YOUTUBE_WIKI_ENV_FILE", str(Path.home() / ".agent-brain-plugins.env"))
    env_file_values = read_env_file_values(env_file)
    current = read_runtime_management_config_values()
    values = {}
    for key, aliases in {**RUNTIME_CAPABILITY_ENV, **RUNTIME_MANAGEMENT_REQUEST_DEFAULTS}.items():
        if not overwrite and any(current.get(candidate) for candidate in [key, *aliases]):
            continue
        candidate_keys = [key, *aliases]
        raw_value = ""
        for candidate in candidate_keys:
            if os.environ.get(candidate):
                raw_value = os.environ.get(candidate, "")
                break
        if not raw_value:
            for candidate in candidate_keys:
                if env_file_values.get(candidate):
                    raw_value = env_file_values.get(candidate, "")
                    break
        if raw_value:
            values[key] = raw_value
    return values


def initialize_runtime_management_config(overwrite=False):
    values = current_runtime_inheritable_values(overwrite=overwrite)
    changed = save_runtime_management_config(values)
    return changed


def request_has_runtime_config(body, env_key, aliases):
    for key in [env_key, env_key.lower(), *aliases]:
        if body.get(key) not in {None, ""}:
            return True
    return False


def inject_runtime_management_config(body):
    values = read_runtime_management_config_values()
    if not values:
        return body
    merged = {**body}
    injected = []
    action = str(merged.get("management_action") or merged.get("action") or "").strip()
    for env_key, aliases in RUNTIME_CAPABILITY_ENV.items():
        if request_has_runtime_config(merged, env_key, aliases):
            continue
        if os.environ.get(env_key):
            continue
        for candidate in [env_key, *aliases]:
            value = values.get(candidate)
            if value:
                merged[env_key] = value
                injected.append(env_key)
                break
    for default_key, request_keys in RUNTIME_MANAGEMENT_REQUEST_DEFAULTS.items():
        if action == "create-runtime" and default_key in CREATE_RUNTIME_MANAGEMENT_DEFAULT_EXCLUDES:
            continue
        if any(merged.get(candidate) not in {None, ""} for candidate in request_keys):
            continue
        for candidate in [default_key, *request_keys]:
            value = values.get(candidate)
            if value:
                merged[request_keys[0]] = value
                injected.append(request_keys[0])
                break
    if injected:
        merged["_management_config_injected"] = sorted(set(injected))
    return merged


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


def indexed_run_is_stale(sop, pipeline_id, indexed):
    """Return true when workspace evidence is newer than the SQLite run index."""
    if not indexed:
        return False
    run_file = run_workspace(sop, pipeline_id) / "run.json"
    workspace = read_json(run_file)
    if not isinstance(workspace, dict):
        return False

    indexed_status = str(indexed.get("status") or "")
    workspace_status = str(workspace.get("status") or "")
    terminal_statuses = {"done", "failed", "cancelled"}
    if workspace_status in terminal_statuses and indexed_status not in terminal_statuses:
        return True

    indexed_updated = str(indexed.get("updated_at") or "")
    workspace_updated = str(workspace.get("updated_at") or "")
    if workspace_updated and indexed_updated and workspace_updated > indexed_updated:
        return True

    workspace_nodes = workspace.get("nodes") if isinstance(workspace.get("nodes"), dict) else {}
    indexed_nodes = indexed.get("nodes") if isinstance(indexed.get("nodes"), dict) else {}
    for node_id, workspace_node_status in workspace_nodes.items():
        indexed_node_status = indexed_nodes.get(node_id)
        if str(workspace_node_status) in terminal_statuses and str(indexed_node_status) == "running":
            return True

    return False


def indexed_run(sop, pipeline_id, rebuild=True):
    store = run_index_store(sop, create=rebuild)
    if not store:
        return None
    try:
        data = store.get_run(pipeline_id)
        if data:
            if rebuild and indexed_run_is_stale(sop, pipeline_id, data):
                if store.rebuild_from_workspace(pipeline_id, sop):
                    rebuilt = store.get_run(pipeline_id)
                    if rebuilt:
                        return rebuilt
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


def node_explain_metadata(node_id, title="", purpose=""):
    item = RUNTIME_NODE_EXPLAIN.get(node_id)
    if item:
        title_zh, purpose_zh, actions, hints = item
        return {
            "title_zh": title_zh,
            "purpose_zh": purpose_zh,
            "actions": actions,
            "failure_hints": hints,
        }
    return {
        "title_zh": title or node_id,
        "purpose_zh": purpose or "该节点暂未配置中文说明，系统会继续展示现有运行状态、输入、输出和产物。",
        "actions": ["该节点暂未配置执行步骤说明。"],
        "failure_hints": ["查看 error、validation、artifacts 和 raw log 定位失败原因。"],
    }


def input_groups_from_contract(declared_inputs):
    business, environment, secrets = [], [], []
    for key, spec in (declared_inputs or {}).items():
        text = f"{key} {spec}".lower()
        item = {"key": key, "source": spec}
        if any(secret in text for secret in SECRET_KEYS):
            secrets.append({**item, "secret": True})
        elif any(word in text for word in ["env", "config", "github", "cloudflare", "vertex", "gemini", "notebooklm", "telegram", "token"]):
            environment.append(item)
        else:
            business.append(item)
    return business, environment, secrets


def artifact_explanations_from_outputs(node_id, declared_outputs):
    explain = {}
    for key in (declared_outputs or {}):
        if key == "report":
            explain[key] = "本节点执行摘要、状态和校验结果。"
        elif key == "repo_checkout_report":
            explain[key] = "仓库 checkout 结果，包括 origin、branch、commit、stdout/stderr。"
        elif key.endswith("_report"):
            explain[key] = "该节点的结构化检查报告。"
        elif key in {"masked_request", "provision_context"}:
            explain[key] = "Runtime Management 的脱敏请求或共享上下文。"
        else:
            explain[key] = f"{node_id} 节点输出 {key}。"
    return explain


def key_results_from_node_detail(detail):
    results = []

    def add(key, value, label=None):
        if value is None or value == "" or value == []:
            return
        results.append({"key": key, "label": label or key.replace("_", " ").title(), "value": mask_data(value)})

    stdout = str(detail.get("stdout") or (detail.get("detail") or {}).get("stdout") or "")
    for line in stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in {"agent_commit", "skill_commit", "agent_branch", "skill_branch", "agent_origin", "skill_origin"}:
            add(key, value)
    for key in [
        "runtime_id", "channel_url", "target_host", "plan_mode", "repos_ready",
        "required_commands_ok", "registry_valid", "runtime_id_match", "local_spi_ok",
        "public_spi_ok", "cors_ok", "channel_registered", "runtime_management_visible",
        "deleted", "services_stopped", "services_removed", "local_clean_ok",
        "channel_removed_ok", "tunnel_still_active",
    ]:
        add(key, detail.get(key))
    plan = detail.get("plan") if isinstance(detail.get("plan"), dict) else {}
    add("plan_mode", plan.get("plan_mode"))
    add("conflicts", plan.get("conflicts"))
    for key in ["remaining_paths", "remaining_processes", "remaining_ports"]:
        if isinstance(detail.get(key), list):
            add(key, len(detail.get(key)))
    return results[:12]


def ensure_node_explanation(detail):
    node_id = str(detail.get("node_id") or "")
    title = str(detail.get("title") or node_id)
    purpose = str(detail.get("purpose") or "")
    declared_inputs = detail.get("declared_inputs") if isinstance(detail.get("declared_inputs"), dict) else {}
    resolved_inputs = detail.get("resolved_inputs") if isinstance(detail.get("resolved_inputs"), dict) else {}
    declared_outputs = detail.get("declared_outputs") if isinstance(detail.get("declared_outputs"), dict) else {}
    actual_outputs = detail.get("actual_outputs") if isinstance(detail.get("actual_outputs"), dict) else {}
    validation = detail.get("validation") if isinstance(detail.get("validation"), dict) else {}
    meta = node_explain_metadata(node_id, title, purpose)
    business, environment, secrets = input_groups_from_contract(declared_inputs)
    definition = detail.get("definition") if isinstance(detail.get("definition"), dict) else {}
    inputs = detail.get("inputs") if isinstance(detail.get("inputs"), dict) else {}
    outputs = detail.get("outputs") if isinstance(detail.get("outputs"), dict) else {}
    troubleshooting = detail.get("troubleshooting") if isinstance(detail.get("troubleshooting"), dict) else {}
    return {
        **detail,
        "definition": {
            "title": title,
            "title_zh": definition.get("title_zh") or meta["title_zh"],
            "purpose": purpose,
            "purpose_zh": definition.get("purpose_zh") or meta["purpose_zh"],
            "branch": detail.get("branch", ""),
            "executor": detail.get("executor", {}),
            "retryable": detail.get("retryable", True),
            **definition,
        },
        "inputs": {
            "declared": declared_inputs,
            "resolved": resolved_inputs,
            "business": inputs.get("business") or business,
            "environment": inputs.get("environment") or environment,
            "secrets": inputs.get("secrets") or secrets,
        },
        "actions": detail.get("actions") if isinstance(detail.get("actions"), list) and detail.get("actions") else meta["actions"],
        "outputs": {
            "declared": declared_outputs,
            "actual": actual_outputs,
            "artifact_explanations": outputs.get("artifact_explanations") or artifact_explanations_from_outputs(node_id, declared_outputs),
            "key_results": outputs.get("key_results") or key_results_from_node_detail(detail),
        },
        "troubleshooting": {
            "failure_hints": troubleshooting.get("failure_hints") or ([detail.get("manual_fix_hint")] if detail.get("manual_fix_hint") else meta["failure_hints"]),
            "retryable": detail.get("retryable", True),
            "safe_to_retry": detail.get("retryable", True),
            "error": detail.get("error", ""),
            "validation": validation,
        },
    }


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


def run_dag_node_config(sop, pipeline_id, node_id):
    snapshot = read_json(run_workspace(sop, pipeline_id) / "dag.json") or {}
    nodes = snapshot.get("nodes") or []
    if isinstance(nodes, dict):
        item = nodes.get(node_id)
        return dict(item or {}) if isinstance(item, dict) else None
    for item in nodes if isinstance(nodes, list) else []:
        if isinstance(item, dict) and item.get("id") == node_id:
            return dict(item)
    return None


def provision_node_report(sop, pipeline_id, node_id):
    wiki = Path(sop["wiki_local_path"])
    candidates = [
        wiki / "raw" / "provision" / pipeline_id / f"{node_id}.json",
        wiki / "raw" / "provision" / pipeline_id / f"{node_id.replace('_', '-')}.json",
    ]
    for path in candidates:
        resolved = safe_artifact_path(wiki, path.relative_to(wiki))
        if resolved and resolved.is_file():
            report = read_json(resolved)
            return report if isinstance(report, dict) else {}
    return {}


def node_runtime_detail(sop, pipeline_id, node_id):
    wiki = Path(sop["wiki_local_path"])
    workspace = run_workspace(sop, pipeline_id)
    node_file = workspace / "nodes" / f"{node_id}.json"
    state = read_json(node_file) or {}
    report = provision_node_report(sop, pipeline_id, node_id)
    config = (sop.get("nodes") or {}).get(node_id) or run_dag_node_config(sop, pipeline_id, node_id) or {}
    context = run_context(sop, pipeline_id)

    declared_inputs = normalized_contract(config.get("inputs") or state.get("inputs") or state.get("declared_inputs") or {}, "input")
    optional_inputs = normalized_contract(config.get("optional_inputs") or state.get("optional_inputs") or {}, "input")
    for spec in optional_inputs.values():
        spec["required"] = False
    declared_outputs = normalized_contract(config.get("outputs") or state.get("outputs") or state.get("declared_outputs") or {}, "output")

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
        "title": state.get("title") or config.get("title", node_id),
        "purpose": state.get("purpose") or config.get("purpose", config.get("description", "")),
        "branch": state.get("branch") or config.get("branch", ""),
        "retryable": state.get("retryable", True),
        "manual_fix_hint": state.get("manual_fix_hint", ""),
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
        "infra": config.get("infra", {}),
        "report_detail": mask_data(report.get("detail")) if isinstance(report.get("detail"), dict) else {},
        "report_reason": report.get("reason", "") if isinstance(report.get("reason"), str) else "",
        "report_manual_fix_hint": report.get("manual_fix_hint", "") if isinstance(report.get("manual_fix_hint"), str) else "",
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
                    "report_detail": detail.get("report_detail"),
                    "report_reason": detail.get("report_reason"),
                    "report_manual_fix_hint": detail.get("report_manual_fix_hint"),
                    "index_resolution": "indexed",
                })
        except Exception:
            pass
    return ensure_node_explanation(detail)


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
        "purpose": config.get("purpose", config.get("description", "")),
        "branch": config.get("branch", ""),
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
        "ui": config.get("ui") if isinstance(config.get("ui"), dict) else manifest.get("ui") if isinstance(manifest.get("ui"), dict) else {},
        "retryable": config.get("retryable", True),
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


def node_actions(instance_id, node_id, classification=None):
    classification = classification or {}
    side_effect = classification.get("side_effect")
    dep_class = classification.get("dep_class")
    # Single-node test is enabled whenever the engine classifies the node.
    trigger_enabled = bool(classification)
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
            "destructive": side_effect == "mutating",
            "enabled": trigger_enabled,
            "dep_class": dep_class,
            "side_effect": side_effect,
            "requires_confirm": side_effect == "mutating",
            "requires_seed": dep_class == "artifact_dependent",
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
        "description": static.get("purpose") or manifest.get("description", ""),
        "purpose": static.get("purpose", ""),
        "branch": static.get("branch", ""),
        "retryable": static.get("retryable", True),
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
        "actions": node_actions(instance_id, node_id, node_classification_for(node_id)),
        "cli": node_cli_examples(endpoint or "{endpoint}", instance_id, node_id),
        "ui": static.get("ui") or {},
        "modules": node_modules(sop, node_id, endpoint),
        "editable": True,
        "publish_enabled": False,
        "classification": node_classification_for(node_id),
        "missing_fields": validate_node_definition(node_id, config, static),
    }


def node_classification_for(node_id):
    """Compact classification view (engine-sourced) for the asset center:
    dep_class / side_effect / testable_standalone / deps. Empty dict if unknown."""
    contract = provision_node_contract(node_id)
    if not contract:
        return {}
    return {
        "dep_class": contract.get("dep_class"),
        "side_effect": contract.get("side_effect"),
        "testable_standalone": contract.get("testable_standalone", False),
        "request_inputs": contract.get("request_inputs") or [],
        "artifact_deps": contract.get("artifact_deps") or [],
        "state_preconditions": contract.get("state_preconditions") or [],
    }


NODE_MODULE_CONTRACT_VERSION = "node-module-contract/v1"
NODE_DRAFT_SCHEMA_VERSION = "node-draft-schema/v1"

NODE_MODULE_DEFINITIONS = [
    {
        "id": "basic",
        "title": "Basic",
        "lane": "definition",
        "order": 10,
        "description": "节点身份、分类和发布状态",
        "schema": ["node_id", "title", "description", "mode", "needs", "ui"],
    },
    {
        "id": "executor",
        "title": "Executor",
        "lane": "execution",
        "order": 20,
        "description": "执行器、Agent、Webhook 和操作入口",
        "schema": ["executor.type", "executor.skill", "executor.agent", "executor.entry", "actions", "cli"],
    },
    {
        "id": "skill",
        "title": "Skill",
        "lane": "execution",
        "order": 30,
        "description": "节点背后的 Skill 安装、说明和来源",
        "schema": ["skill.id", "skill.source", "skill.install_command", "skill.readme_path"],
    },
    {
        "id": "inputs",
        "title": "Inputs",
        "lane": "contract",
        "order": 40,
        "description": "输入契约和当前 Run 的 resolved inputs",
        "schema": ["declared_inputs", "optional_inputs", "resolved_inputs"],
    },
    {
        "id": "outputs",
        "title": "Outputs",
        "lane": "contract",
        "order": 50,
        "description": "输出契约、实际输出和校验结果",
        "schema": ["declared_outputs", "actual_outputs", "validation"],
    },
    {
        "id": "artifacts",
        "title": "Artifacts",
        "lane": "artifact",
        "order": 60,
        "description": "当前 Run 的记录产物和候选产物",
        "schema": ["artifacts", "discovered_candidates"],
    },
    {
        "id": "capabilities",
        "title": "Capabilities",
        "lane": "capability",
        "order": 70,
        "description": "Git、TG、SSE 和日志等附属能力",
        "schema": ["declared_capabilities", "run_capabilities"],
    },
    {
        "id": "runtime",
        "title": "Runtime State",
        "lane": "execution",
        "order": 80,
        "description": "节点运行状态、进度、耗时和错误",
        "schema": ["status", "run_id", "attempt", "progress", "duration_s", "error"],
    },
    {
        "id": "actions",
        "title": "Actions",
        "lane": "operation",
        "order": 90,
        "description": "Inspect、Retry、Cancel、Validate 和 Publish",
        "schema": ["actions", "cli", "publish_enabled"],
    },
    {
        "id": "logs",
        "title": "Logs / Events",
        "lane": "observability",
        "order": 100,
        "description": "节点日志、事件和错误线索",
        "schema": ["log", "events"],
    },
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


def module_metrics(module_id, static, run_detail=None):
    run_detail = run_detail or {}
    if module_id == "basic":
        return {
            "needs": len(static.get("needs") or []),
            "missing_fields": len(static.get("missing_fields") or []),
            "editable": bool(static.get("editable", True)),
        }
    if module_id == "executor":
        executor = static.get("executor") or {}
        return {
            "type": executor.get("type", "skill"),
            "has_agent": bool(executor.get("agent")),
            "has_entry": bool(executor.get("entry")),
            "action_count": len(static.get("actions") or {}),
        }
    if module_id == "skill":
        skill = static.get("skill") or {}
        return {
            "has_install_command": bool(skill.get("install_command")),
            "has_readme": bool(skill.get("readme_path") or skill.get("summary")),
        }
    if module_id == "inputs":
        declared = static.get("inputs") or {}
        optional = static.get("optional_inputs") or {}
        resolved = run_detail.get("resolved_inputs") or {}
        return {"declared": len(declared), "optional": len(optional), "resolved": len(resolved)}
    if module_id == "outputs":
        declared = static.get("outputs") or {}
        actual = run_detail.get("actual_outputs") or {}
        validation = run_detail.get("validation") or {}
        return {"declared": len(declared), "actual": len(actual), "validation": validation.get("status", "")}
    if module_id == "artifacts":
        return {
            "recorded": len(run_detail.get("artifacts") or []),
            "candidates": len(run_detail.get("discovered_candidates") or []),
        }
    if module_id == "capabilities":
        declared = static.get("capabilities") or {}
        current = run_detail.get("capabilities") or {}
        return {"declared": len(declared), "runtime": len(current)}
    if module_id == "runtime":
        return {
            "status": run_detail.get("status", "waiting"),
            "attempt": run_detail.get("attempt") or 0,
            "progress": run_detail.get("progress") or 0,
            "duration_s": run_detail.get("duration_s") or 0,
        }
    if module_id == "actions":
        actions = static.get("actions") or {}
        return {
            "total": len(actions),
            "destructive": len([item for item in actions.values() if isinstance(item, dict) and item.get("destructive")]),
        }
    if module_id == "logs":
        return {"event_count": len((run_detail.get("events") or []))}
    return {}


def node_modules(sop, node_id, endpoint="", pipeline_id=None):
    static = node_static_config(sop, node_id)
    if static is None:
        return []
    run_detail = node_runtime_detail(sop, pipeline_id, node_id) if pipeline_id else None
    modules = []
    for definition in NODE_MODULE_DEFINITIONS:
        module_id = definition["id"]
        modules.append({
            "id": module_id,
            "title": definition["title"],
            "lane": definition["lane"],
            "order": definition["order"],
            "description": definition["description"],
            "status": module_status(module_id, static, run_detail),
            "summary": module_summary(module_id, static, run_detail),
            "schema": definition["schema"],
            "metrics": module_metrics(module_id, static, run_detail),
            "contract_version": NODE_MODULE_CONTRACT_VERSION,
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


def node_draft_schema():
    return {
        "schema_id": NODE_DRAFT_SCHEMA_VERSION,
        "title": "Node Draft from Skill",
        "description": "把一个 Skill 安装命令转换成可验证的 SOP 节点草稿；不会修改生产 DAG。",
        "fields": [
            {
                "name": "skill_install_command",
                "label": "Skill install command",
                "type": "string",
                "required": True,
                "placeholder": "bash <(curl -fsSL https://skill.vyibc.com/install-demo.sh)",
                "maps_to": "skill.install_command",
            },
            {"name": "skill_id", "label": "Skill ID", "type": "slug", "required": True, "maps_to": "skill.id"},
            {"name": "node_id", "label": "Node ID", "type": "slug", "required": True, "maps_to": "id"},
            {"name": "title", "label": "Title", "type": "string", "required": True, "maps_to": "title"},
            {"name": "description", "label": "Description", "type": "text", "required": False, "maps_to": "description"},
            {"name": "upstream", "label": "Upstream node", "type": "node_id", "required": False, "maps_to": "needs[0]"},
            {"name": "upstream_output", "label": "Upstream output", "type": "string", "required": False, "default": "output", "maps_to": "inputs.*.from"},
            {"name": "input_name", "label": "Input name", "type": "slug", "required": False, "default": "input", "maps_to": "inputs"},
            {"name": "output_name", "label": "Output name", "type": "slug", "required": False, "default": "artifact", "maps_to": "outputs"},
            {
                "name": "output_path",
                "label": "Output path",
                "type": "path_pattern",
                "required": False,
                "default": "raw/{node_id}/{pipeline_id}/{output_name}",
                "maps_to": "outputs.*.path",
            },
        ],
        "defaults": {
            "executor_type": "agent-skill",
            "agent": "hermes",
            "mode": "blocking",
            "input_type": "auto",
            "output_type": "file",
            "category": "custom",
            "capabilities": {
                "git": {"enabled": True, "required": False},
                "telegram": {"enabled": True, "required": False},
                "sse": {"enabled": True, "required": True},
            },
        },
        "safety": {
            "production_dag_changed": False,
            "writes": ["raw/node-drafts/{draft_id}/node.yaml", "raw/node-drafts/{draft_id}/validation.json"],
            "publish_enabled": False,
        },
    }


def validate_node_draft_input(spec, existing_nodes=None):
    errors = []
    existing_nodes = existing_nodes or set()
    for field in node_draft_schema()["fields"]:
        name = str(field["name"])
        value = spec.get(name)
        if field.get("required") and (value is None or str(value).strip() == ""):
            errors.append({
                "field": name,
                "code": "required",
                "message": f"{field.get('label', name)} is required",
            })
    for name in ("skill_id", "node_id", "input_name", "output_name"):
        value = spec.get(name)
        if value and slugify(str(value)) != str(value).strip().lower():
            errors.append({
                "field": name,
                "code": "slug",
                "message": f"{name} must contain only letters, numbers, dash or underscore",
            })
    node_id = str(spec.get("node_id") or "").strip()
    if node_id and node_id in existing_nodes:
        errors.append({
            "field": "node_id",
            "code": "node_exists",
            "message": f"node_id {node_id} already exists in production DAG",
        })
    return {
        "schema_id": NODE_DRAFT_SCHEMA_VERSION,
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "missing_fields": [error["field"] for error in errors if error["code"] == "required"],
    }


def create_node_draft(sop, spec):
    existing_nodes = set((sop.get("nodes") or {}).keys()) if isinstance(sop.get("nodes"), dict) else set()
    input_validation = validate_node_draft_input(spec, existing_nodes)
    if input_validation["errors"]:
        return {
            "draft_id": "",
            "draft_path": "",
            "node": {},
            "validation": {
                **input_validation,
                "production_dag_changed": False,
            },
        }
    wiki = Path(sop["wiki_local_path"])
    draft = draft_from_skill(spec)
    draft_id = f"{draft['id']}-{int(time.time())}"
    draft_dir = wiki / "raw" / "node-drafts" / draft_id
    draft_dir.mkdir(parents=True, exist_ok=True)
    (draft_dir / "node.yaml").write_text(yaml.safe_dump(draft, allow_unicode=True, sort_keys=False), encoding="utf-8")
    missing = validate_node_definition(draft["id"], draft, draft)
    validation = {
        "schema_id": NODE_DRAFT_SCHEMA_VERSION,
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
    has_sop_definition = bool(sop)
    if not sop:
        sop = {}
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
    instance_title = instance.get("display_name") or instance.get("title") or instance_id
    return {
        "id": instance_id,
        "instance_id": instance_id,
        "raw_id": sop.get("id") or sop.get("name") or "",
        "sop_type": instance.get("sop_type") or sop.get("id") or sop.get("name", ""),
        "workspace_kind": instance.get("workspace_kind") or ("workflow-bound" if has_sop_definition else "execution-workspace"),
        "name": sop.get("name", instance_id),
        "title": instance_title,
        "workflow_title": sop.get("title", sop.get("name", "")),
        "version": sop.get("version", ""),
        "repo": instance.get("repo") or sop.get("repo", ""),
        "wiki_dir": wiki_path.name,
        "wiki_local_path": str(wiki_path),
        "sop_file": str(sop_file),
        "has_sop_definition": has_sop_definition,
        "nodes": nodes,
        "enabled": bool(instance.get("enabled", True)),
        "runtime_id": runtime.get("runtime_id", ""),
        "channel_name": runtime.get("channel_name", ""),
        "channel_url": runtime.get("channel_url", ""),
        "spi_base_url": runtime.get("spi_base_url", ""),
        "created_at": instance.get("created_at", ""),
        "updated_at": instance.get("updated_at", ""),
    }


def plugin_root():
    return Path(os.environ.get("AGENT_BRAIN_PLUGINS_PATH", str(Path.home() / "agent-brain-plugins"))).expanduser()


_PROVISION_MODULE_CACHE = {}


def provision_module():
    """Import the engine module (provision_runtime.py) as the single source for
    node execution/test metadata. Cached; returns None if unavailable."""
    if "mod" in _PROVISION_MODULE_CACHE:
        return _PROVISION_MODULE_CACHE["mod"]
    mod = None
    try:
        runner = plugin_root() / "youtube-wiki" / "infrastructure" / "provision_runtime.py"
        if runner.exists():
            import importlib.util
            spec = importlib.util.spec_from_file_location("provision_runtime_bridge", runner)
            mod = importlib.util.module_from_spec(spec)
            # Register before exec so @dataclass annotation resolution can find the module.
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
    except Exception:
        mod = None
    _PROVISION_MODULE_CACHE["mod"] = mod
    return mod


def provision_node_contract(node_id):
    """Return the engine node contract (incl. dep_class / side_effect /
    request_inputs / artifact_deps / state_preconditions) or None."""
    mod = provision_module()
    if mod is None or not hasattr(mod, "node_contract"):
        return None
    try:
        if node_id not in getattr(mod, "RUNTIME_MANAGEMENT_NODES", []):
            return None
        return mod.node_contract(node_id)
    except Exception:
        return None


def ensure_runtime_management_sop(runtime):
    template = plugin_root() / "youtube-wiki" / "templates" / "runtime-management-sop"
    if not (template / "sop.yaml").exists():
        return None
    workspace = Path(os.environ.get("RUNTIME_MANAGEMENT_WORKSPACE", str(wiki_base() / "runtime-management"))).expanduser()
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        shutil.copytree(template, workspace, dirs_exist_ok=True)
        (workspace / ".sop").mkdir(parents=True, exist_ok=True)
        store_cls = run_index_class()
        if store_cls:
            store_cls(workspace).initialize()
    except Exception:
        return None
    instance = {
        "instance_id": "runtime-management",
        "sop_type": "runtime-management",
        "repo": "skkeoriw/runtime-management",
        "local_path": str(workspace),
        "enabled": True,
        "created_at": runtime.get("created_at", ""),
        "updated_at": runtime.get("updated_at", ""),
    }
    return sop_from_instance(runtime, instance)


def sync_runtime_management_definition(local_path):
    """Keep the DEPLOYED runtime-management workflow definition in lockstep with
    the authoritative template (agent-brain-plugins). Without this, the workspace
    sop.yaml is a frozen snapshot taken when the instance was first registered —
    any historical version (old single-workflow, missing branches/nodes) lingers
    forever and is served to the DAG/UI/SPI even after the template is updated.
    Idempotent: only rewrites when the template content actually differs."""
    template = plugin_root() / "youtube-wiki" / "templates" / "runtime-management-sop" / "sop.yaml"
    if not template.exists() or not local_path:
        return
    try:
        dst = Path(local_path).expanduser() / "sop.yaml"
        dst.parent.mkdir(parents=True, exist_ok=True)
        latest = template.read_bytes()
        if (not dst.exists()) or dst.read_bytes() != latest:
            dst.write_bytes(latest)
    except Exception:
        pass


def load_sops():
    registry = read_registry()
    sops = []
    for instance in registry.get("instances", []):
        if not isinstance(instance, dict) or not instance.get("enabled", True):
            continue
        if instance.get("instance_id") == "runtime-management" or instance.get("sop_type") == "runtime-management":
            sync_runtime_management_definition(instance.get("local_path"))
        sop = sop_from_instance(registry, instance)
        if sop:
            sops.append(sop)
    if not any(sop.get("instance_id") == "runtime-management" for sop in sops):
        management = ensure_runtime_management_sop(registry)
        if management:
            sops.insert(0, management)
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


def hermes_smoke_route():
    return (os.environ.get("HERMES_SMOKE_ROUTE") or "sop-runtime-hermes-smoke").strip().strip("/") or "sop-runtime-hermes-smoke"


def hermes_webhook_url():
    route = hermes_smoke_route()
    raw = (
        os.environ.get("HERMES_WEBHOOK_URL")
        or os.environ.get("WEBHOOK_PUBLIC_HOST")
        or os.environ.get("HERMES_PUBLIC_HOST")
        or ""
    ).strip().rstrip("/")
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"
    if "/webhooks/" in raw:
        return raw
    return f"{raw}/webhooks/{route}"


def shell_quote_single(value):
    return "'" + str(value).replace("'", "'\\''") + "'"


def hermes_manual_curl(url, payload):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return "\n".join([
        f"body={shell_quote_single(body)}",
        "sig=$(printf '%s' \"$body\" | openssl dgst -sha256 -hmac \"$HERMES_WEBHOOK_TOKEN\" -hex | sed 's/^.* //')",
        "curl -sS -X POST \\",
        f"  {shell_quote_single(url)} \\",
        "  -H 'Content-Type: application/json' \\",
        "  -H 'User-Agent: Mozilla/5.0 SOP-Runtime-Hermes-Smoke/1.0' \\",
        "  -H \"X-Hub-Signature-256: sha256=$sig\" \\",
        "  --data-binary \"$body\"",
    ])


def strip_ansi(text):
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(text or ""))


def hermes_agent_command():
    configured = os.environ.get("HERMES_CLI", "").strip()
    if configured:
        return configured
    discovered = shutil.which("hermes")
    if discovered:
        return discovered
    local_bin = Path.home() / ".local" / "bin" / "hermes"
    if local_bin.exists():
        return str(local_bin)
    return ""


def hermes_agent_manual_command(command, message):
    if not command:
        return "Hermes CLI is missing on this Runtime."
    args = (shlex.split(command) if any(ch.isspace() for ch in command) else [command]) + ["--oneshot", message or "你好 你是谁"]
    return " ".join(shlex.quote(arg) for arg in args)


def hermes_agent_check(message, runner=None):
    command = hermes_agent_command()
    prompt = message or "你好 你是谁"
    base = {
        "mode": "hermes-agent-chat-check",
        "command": command,
        "manual_command": hermes_agent_manual_command(command, prompt),
        "message": prompt,
    }
    if not command:
        return 503, {
            **base,
            "ok": False,
            "reason": "Hermes CLI is not installed or is not on PATH for this Runtime",
            "response": "",
            "exit_code": None,
        }

    timeout_seconds = int(os.environ.get("HERMES_AGENT_CHECK_TIMEOUT", "120") or "120")
    run = runner or subprocess.run
    env = os.environ.copy()
    env["TERM"] = "xterm-256color"
    args = (shlex.split(command) if any(ch.isspace() for ch in command) else [command]) + ["--oneshot", prompt]
    started = time.monotonic()
    try:
        completed = run(
            args,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            env=env,
        )
        latency_ms = round((time.monotonic() - started) * 1000)
        stdout = strip_ansi(completed.stdout or "")
        stderr = strip_ansi(completed.stderr or "")
        response = stdout.strip() or stderr.strip()
        failure_text = f"{response}\n{stderr}".lower()
        cli_error = any(pattern in failure_text for pattern in [
            "api call failed",
            "badrequesterror",
            "non-retryable",
            "cloudflare tunnel error",
            "error code:",
            "http 4",
            "http 5",
            "traceback",
            "exception",
        ])
        ok = completed.returncode == 0 and bool(response) and not cli_error
        return (200 if ok else 502), {
            **base,
            "ok": ok,
            "exit_code": completed.returncode,
            "latency_ms": latency_ms,
            "response": response,
            "stderr": stderr.strip() if stderr and not ok else "",
            "reason": "" if ok else "Hermes CLI returned an error response" if cli_error else "Hermes CLI did not return a successful response",
            "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    except subprocess.TimeoutExpired as exc:
        latency_ms = round((time.monotonic() - started) * 1000)
        return 504, {
            **base,
            "ok": False,
            "exit_code": None,
            "latency_ms": latency_ms,
            "response": strip_ansi(exc.stdout or ""),
            "stderr": strip_ansi(exc.stderr or ""),
            "reason": f"Hermes CLI timed out after {timeout_seconds}s",
            "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    except Exception as exc:
        latency_ms = round((time.monotonic() - started) * 1000)
        return 502, {
            **base,
            "ok": False,
            "exit_code": None,
            "latency_ms": latency_ms,
            "response": "",
            "stderr": "",
            "reason": str(exc),
            "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }


def hermes_post_with_retry(target, data, headers, attempts=3, opener=None, sleeper=None):
    opener = opener or urllib.request.urlopen
    sleeper = sleeper or time.sleep
    http_status = 0
    content_type = ""
    response_body = ""
    error = ""
    attempts_used = 0
    for attempt in range(1, attempts + 1):
        attempts_used = attempt
        try:
            req = urllib.request.Request(target, data=data, headers=headers, method="POST")
            with opener(req, timeout=60) as response:
                http_status = response.status
                content_type = response.headers.get("content-type", "")
                response_body = response.read().decode("utf-8", errors="replace")
                error = ""
        except urllib.error.HTTPError as exc:
            http_status = exc.code
            content_type = exc.headers.get("content-type", "")
            response_body = exc.read().decode("utf-8", errors="replace")
            error = f"HTTP {exc.code}"
        except Exception as exc:
            http_status = 0
            response_body = ""
            error = str(exc)
        if http_status in {200, 201, 202, 204}:
            break
        retry_text = f"{response_body}\n{error}".lower()
        transient_failure = http_status in {502, 503, 504} or "fetch failed" in retry_text or "tunnel offline" in retry_text
        if attempt < attempts and transient_failure:
            sleeper(1.5 * attempt)
            continue
        break
    return http_status, content_type, response_body, error, attempts_used


def hermes_smoke_check(message):
    target = hermes_webhook_url()
    token = os.environ.get("HERMES_WEBHOOK_TOKEN", "")
    info = runtime_info()
    payload = {
        "message": message or "你好 你是谁",
        "runtime_id": info.get("runtime_id", ""),
        "channel_url": info.get("channel_url", ""),
        "spi_base_url": info.get("spi_base_url", ""),
        "source": "sop-runtime-bridge",
        "mode": "hermes-smoke-check",
    }
    base = {
        "target_url": target,
        "route": hermes_smoke_route(),
        "curl": hermes_manual_curl(target or "https://<WEBHOOK_PUBLIC_HOST>/webhooks/sop-runtime-hermes-smoke", payload),
        "token_present": bool(token),
        "payload": payload,
    }
    if not target:
        return 422, {
            **base,
            "ok": False,
            "reason": "HERMES_WEBHOOK_URL or WEBHOOK_PUBLIC_HOST is not configured on this Runtime",
        }
    if not token:
        return 422, {
            **base,
            "ok": False,
            "reason": "HERMES_WEBHOOK_TOKEN is not configured on this Runtime",
        }

    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(token.encode("utf-8"), data, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json,text/plain,*/*",
        "User-Agent": "Mozilla/5.0 SOP-Runtime-Hermes-Smoke/1.0",
        "X-Hub-Signature-256": f"sha256={signature}",
    }
    started = time.monotonic()
    http_status, content_type, response_body, error, attempts = hermes_post_with_retry(target, data, headers, attempts=3)
    latency_ms = round((time.monotonic() - started) * 1000)
    ok = http_status in {200, 201, 202, 204}
    return (200 if ok else 502), {
        **base,
        "ok": ok,
        "attempts": attempts,
        "http_status": http_status,
        "content_type": content_type,
        "latency_ms": latency_ms,
        "response": response_body,
        "error": "" if ok else error or f"HTTP {http_status}",
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def workflow_binding(sop):
    sop_id = sop.get("id") or sop.get("instance_id", "")
    business_nodes = [
        node_id for node_id, config in (sop.get("nodes") or {}).items()
        if node_id != "retry" and (config or {}).get("mode") != "manual"
    ]
    if not sop.get("has_sop_definition", True):
        return {
            "workflow_id": "",
            "workflow_name": "",
            "workflow_version": "",
            "definition_source": "stateless-catalog",
            "definition_path": "",
            "node_count": 0,
            "enabled_node_count": 0,
            "binding_status": "unbound",
        }
    return {
        "workflow_id": sop.get("raw_id") or sop.get("sop_type") or sop_id,
        "workflow_name": sop.get("workflow_title") or sop.get("name") or sop_id,
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
    if not workspace.exists():
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
    latest_data = latest_execution_for_instance(sop) if include_latest else None
    latest = execution_summary(sop, latest_data) if latest_data else None
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
        "display_name": sop.get("title") or instance_id,
        "description": sop.get("description", ""),
        "sop_type": sop.get("sop_type", ""),
        "workspace_kind": sop.get("workspace_kind", ""),
        "enabled": bool(sop.get("enabled", True)),
        "repo": sop.get("repo", ""),
        "repo_branch": sop.get("repo_branch", "main"),
        "wiki_local_path": sop.get("wiki_local_path", ""),
        "workspace_status": "ready" if workspace.exists() else "missing",
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
            if hasattr(store, "count_runs"):
                return store.count_runs()
            return len(store.list_runs(limit=200))
        except Exception:
            pass
    return len(run_files(sop))


def latest_execution_for_instance(sop):
    store = run_index_store(sop)
    if store:
        try:
            if hasattr(store, "latest_run_summary"):
                latest = store.latest_run_summary()
                if latest:
                    return latest
            runs = store.list_runs(limit=1)
            if runs:
                return runs[0]
        except Exception:
            pass
    run_files_found = run_files(sop)
    if run_files_found:
        run = read_json(run_files_found[0]) or {}
        if run and not run.get("pipeline_id"):
            run["pipeline_id"] = run_files_found[0].parent.name
        if run:
            return run_summary(sop, run)
    return None


def instance_capabilities(sop):
    workspace = Path(sop["wiki_local_path"])
    store = run_index_store(sop)
    return {
        "workspace": "ok" if workspace.exists() else "missing",
        "sop_yaml": "ok" if Path(sop.get("sop_file", "")).exists() else "unbound",
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


def query_value(query, key, default=""):
    value = (query or {}).get(key)
    if isinstance(value, list):
        return str(value[0]) if value else default
    return str(value) if value is not None else default


def cached_read(cache_key, loader, ttl=None):
    ttl = _SOP_READ_CACHE_TTL_SECONDS if ttl is None else ttl
    now = time.time()
    cached = _SOP_READ_CACHE.get(cache_key)
    if cached and now - cached.get("ts", 0) <= ttl:
        return cached.get("value")
    value = loader()
    _SOP_READ_CACHE[cache_key] = {"ts": now, "value": value}
    if len(_SOP_READ_CACHE) > 64:
        for key, item in list(_SOP_READ_CACHE.items()):
            if now - item.get("ts", 0) > ttl:
                _SOP_READ_CACHE.pop(key, None)
    return value


def query_int(query, key, default, minimum=1, maximum=200):
    try:
        value = int(query_value(query, key, str(default)) or default)
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


def page_params(query, default_page_size=50):
    page = query_int(query, "page", 1, 1, 100000)
    page_size = query_int(query, "page_size", query_int(query, "limit", default_page_size), 1, 200)
    offset = (page - 1) * page_size
    return page, page_size, offset


def page_meta(page, page_size, total):
    total = int(total or 0)
    page_count = max(1, (total + page_size - 1) // page_size) if page_size else 1
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "page_count": page_count,
        "has_next": page < page_count,
        "has_prev": page > 1,
    }


def filter_instance_summaries(instances, query):
    q = query_value(query, "q").lower()
    status_filter = query_value(query, "status")
    sort = query_value(query, "sort", "updated_at")
    order = query_value(query, "order", "desc").lower()
    items = []
    for item in instances:
        if status_filter and item.get("status") != status_filter:
            continue
        haystack = " ".join(str(item.get(key) or "") for key in (
            "id", "instance_id", "title", "description", "sop_type", "repo", "runtime_id"
        )).lower()
        if q and q not in haystack:
            continue
        items.append(item)
    sort_key = sort if sort in {"id", "instance_id", "title", "status", "created_at", "updated_at", "execution_count"} else "updated_at"
    reverse = order != "asc"
    return sorted(items, key=lambda item: str(item.get(sort_key) or ""), reverse=reverse)


def sop_instances_v1(query=None):
    query = query or {}
    runtime = runtime_info()
    registry = read_registry()
    all_instances = [instance_summary(sop) for sop in load_sops()]
    filtered = filter_instance_summaries(all_instances, query)
    page, page_size, offset = page_params(query, 50)
    items = filtered[offset:offset + page_size]
    return {
        "runtime_id": runtime["runtime_id"],
        "runtime": runtime,
        "channel": {
            "name": registry.get("channel_name", ""),
            "url": registry.get("channel_url", ""),
            "spi_base_url": registry.get("spi_base_url", ""),
        },
        "instances": items,
        "data": items,
        "page": page_meta(page, page_size, len(filtered)),
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
            "purpose": node.get("purpose", static.get("purpose", "")),
            "branch": node.get("branch", static.get("branch", "")),
            "definition": {
                "title": node.get("title", node_id),
                "title_zh": node_explain_metadata(node_id, node.get("title", node_id), node.get("purpose", static.get("purpose", "")))["title_zh"],
                "purpose": node.get("purpose", static.get("purpose", "")),
                "purpose_zh": node_explain_metadata(node_id, node.get("title", node_id), node.get("purpose", static.get("purpose", "")))["purpose_zh"],
                "branch": node.get("branch", static.get("branch", "")),
                "executor": static.get("executor") or {},
                "retryable": True,
            },
            "actions": node_explain_metadata(node_id, node.get("title", node_id), node.get("purpose", static.get("purpose", "")))["actions"],
            "troubleshooting": {
                "failure_hints": node_explain_metadata(node_id, node.get("title", node_id), node.get("purpose", static.get("purpose", "")))["failure_hints"],
                "retryable": True,
                "safe_to_retry": True,
            },
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
    page, page_size, offset = page_params(query, 80)
    status_filter = query_value(query, "status")
    q = query_value(query, "q")
    action = query_value(query, "action")
    source_type = query_value(query, "source_type")
    failed_node = query_value(query, "failed_node")
    date_from = query_value(query, "from") or query_value(query, "date_from")
    date_to = query_value(query, "to") or query_value(query, "date_to")
    sort = query_value(query, "sort", "updated_at")
    order = query_value(query, "order", "desc")
    runs = []
    seen = set()
    total = 0
    store = run_index_store(sop)
    if store:
        try:
            if hasattr(store, "count_runs"):
                total = store.count_runs(status_filter, q, action, source_type, failed_node, date_from, date_to)
            used_summary_store = hasattr(store, "list_run_summaries")
            if used_summary_store:
                store_runs = store.list_run_summaries(
                    limit=page_size,
                    offset=offset,
                    status=status_filter,
                    q=q,
                    action=action,
                    source_type=source_type,
                    failed_node=failed_node,
                    date_from=date_from,
                    date_to=date_to,
                    sort=sort,
                    order=order,
                )
            else:
                store_runs = store.list_runs(limit=page_size, status=status_filter)
            for data in store_runs:
                runs.append(execution_summary(sop, data))
                seen.add(data.get("pipeline_id"))
            if runs or used_summary_store:
                if not total:
                    total = len(runs)
                return {
                    "sop_id": sop_id,
                    "instance_id": instance_id,
                    "executions": runs,
                    "runs": runs,
                    "data": runs,
                    "page": page_meta(page, page_size, total),
                }
        except Exception:
            runs = []
            seen = set()
            total = 0
    fallback_runs = []
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
            summary = execution_summary(sop, run_summary(sop, data))
            haystack = " ".join(str(summary.get(key) or "") for key in (
                "pipeline_id", "execution_id", "status", "source_url", "source_type", "failed_node"
            )).lower()
            if q and q.lower() not in haystack:
                continue
            fallback_runs.append(summary)
    total = len(fallback_runs)
    runs = fallback_runs[offset:offset + page_size]
    return {
        "sop_id": sop_id,
        "instance_id": instance_id,
        "executions": runs,
        "runs": runs,
        "data": runs,
        "page": page_meta(page, page_size, total),
    }


def sop_run_detail(sop, pipeline_id):
    run_file = Path(sop["wiki_local_path"]) / "raw" / "pipeline-runs" / pipeline_id / "run.json"
    data = read_json(run_file)
    if data and not data.get("pipeline_id"):
        data["pipeline_id"] = pipeline_id
    store = run_index_store(sop)
    indexed = None
    if store:
        try:
            if hasattr(store, "get_run_detail"):
                indexed = store.get_run_detail(pipeline_id)
            else:
                indexed = store.get_run(pipeline_id)
        except Exception:
            indexed = None
    payload = indexed or indexed_run(sop, pipeline_id, rebuild=bool(data)) or (run_summary(sop, data) if data else None)
    return execution_summary(sop, payload) if payload else None


def sop_run_nodes(sop, pipeline_id):
    detail = sop_run_detail(sop, pipeline_id) or {}
    nodes = detail.get("nodes") if isinstance(detail.get("nodes"), dict) else {}
    states = detail.get("node_states") if isinstance(detail.get("node_states"), dict) else {}
    items = []
    for node_id, node_status in nodes.items():
        state = states.get(node_id) if isinstance(states.get(node_id), dict) else {}
        items.append({
            "pipeline_id": pipeline_id,
            "execution_id": pipeline_id,
            "node_id": node_id,
            "status": state.get("status") or node_status,
            "started_at": state.get("started_at", ""),
            "finished_at": state.get("finished_at", ""),
            "updated_at": state.get("updated_at", ""),
            "duration_s": int(state.get("duration_s") or 0),
            "progress": int(state.get("progress") or (100 if node_status in {"done", "skipped"} else 0)),
            "artifact_count": int(state.get("artifact_count") or 0),
            "error": state.get("error", ""),
        })
    return {"pipeline_id": pipeline_id, "execution_id": pipeline_id, "nodes": items}


def sop_run_events(sop, pipeline_id, query=None):
    after_sequence = query_int(query or {}, "after_sequence", 0, 0, 100000000)
    store = run_index_store(sop)
    events = []
    if store:
        try:
            events = store.get_events(pipeline_id, after_sequence)
        except Exception:
            events = []
    if not events:
        events = read_run_events(run_workspace(sop, pipeline_id) / "events.jsonl", after_sequence)
    return {"pipeline_id": pipeline_id, "execution_id": pipeline_id, "events": events}


def sop_run_artifacts(sop, pipeline_id):
    store = run_index_store(sop)
    artifacts = None
    if store:
        try:
            artifacts = store.get_artifacts(pipeline_id)
        except Exception:
            artifacts = None
    if artifacts is None:
        artifacts = read_json(run_workspace(sop, pipeline_id) / "artifacts.json")
    return {
        "pipeline_id": pipeline_id,
        "execution_id": pipeline_id,
        "artifacts": artifacts_with_preview(sop, artifacts),
    }


def sop_run_logs(sop, pipeline_id):
    run_dir = run_workspace(sop, pipeline_id)
    logs = []
    if run_dir.exists():
        for path in sorted(run_dir.rglob("*.log"), key=lambda item: item.stat().st_mtime, reverse=True)[:50]:
            try:
                rel = path.relative_to(run_dir).as_posix()
                logs.append({
                    "path": rel,
                    "size": path.stat().st_size,
                    "updated_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                })
            except Exception:
                continue
    return {"pipeline_id": pipeline_id, "execution_id": pipeline_id, "logs": logs}


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


def run_node_ids(sop, pipeline_id):
    dag = normalized_run_dag(sop, pipeline_id)
    if dag:
        return {str(node.get("id", "")) for node in dag.get("nodes", []) if node.get("id")}
    run = read_json(run_workspace(sop, pipeline_id) / "run.json") or {}
    if isinstance(run.get("nodes"), dict):
        return set(run["nodes"].keys())
    return set((sop.get("nodes") or {}).keys())


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


def runtime_management_nodes(action):
    return RUNTIME_MANAGEMENT_NODES


def runtime_management_active_nodes(action):
    if action == "delete-runtime":
        return {*RUNTIME_MANAGEMENT_COMMON_NODES, *DELETE_RUNTIME_NODES, RUNTIME_MANAGEMENT_SUMMARY_NODE}
    if action == "create-runtime":
        return {*RUNTIME_MANAGEMENT_COMMON_NODES, *CREATE_RUNTIME_NODES, RUNTIME_MANAGEMENT_SUMMARY_NODE}
    if action == "create-instance":
        return {*RUNTIME_MANAGEMENT_COMMON_NODES, *CREATE_INSTANCE_NODES, RUNTIME_MANAGEMENT_SUMMARY_NODE}
    if action == "delete-instance":
        return {*RUNTIME_MANAGEMENT_COMMON_NODES, *DELETE_INSTANCE_NODES, RUNTIME_MANAGEMENT_SUMMARY_NODE}
    return {*RUNTIME_MANAGEMENT_COMMON_NODES, RUNTIME_MANAGEMENT_SUMMARY_NODE}


def runtime_management_node_needs(node):
    if node == RUNTIME_MANAGEMENT_COMMON_NODES[0]:
        return []
    if node == "action-router":
        return ["management-request-validate"]
    for branch_nodes in (CREATE_RUNTIME_NODES, DELETE_RUNTIME_NODES, CREATE_INSTANCE_NODES, DELETE_INSTANCE_NODES):
        if node == branch_nodes[0]:
            return ["action-router"]
        if node in branch_nodes:
            return [branch_nodes[branch_nodes.index(node) - 1]]
    if node == RUNTIME_MANAGEMENT_SUMMARY_NODE:
        return [CREATE_RUNTIME_NODES[-1], DELETE_RUNTIME_NODES[-1], CREATE_INSTANCE_NODES[-1], DELETE_INSTANCE_NODES[-1]]
    return []


def create_runtime_management_workspace_run(sop, pipeline_id, action, body):
    wiki = Path(sop["wiki_local_path"])
    run_dir = wiki / "raw" / "pipeline-runs" / pipeline_id
    now = _now_iso_utc()
    nodes = runtime_management_nodes(action)
    active_nodes = runtime_management_active_nodes(action)
    node_status = {node: ("waiting" if node in active_nodes else "skipped") for node in nodes}
    dag = sop_dag(sop)
    if not dag.get("nodes"):
        dag = {
            "sop_id": "runtime-management",
            "nodes": [{
                "id": node,
                "title": node.replace("-", " ").title(),
                "mode": "blocking",
                "needs": runtime_management_node_needs(node),
                "executor": {"type": "skill", "skill": "sop-runtime-provisioning", "webhook_route": "sop-runtime-provisioning"},
                "inputs": {},
                "outputs": {"report": f"raw/provision/{pipeline_id}/{node}.json"},
                "ui": {"category": "runtime-management", "icon": "server"},
            } for node in nodes],
            "edges": [],
        }
        dag["edges"] = [
            {"source": need, "target": node["id"]}
            for node in dag["nodes"]
            for need in node.get("needs", [])
        ]
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "nodes").mkdir(exist_ok=True)
    write_json(run_dir / "context.json", {
        "pipeline_id": pipeline_id,
        "management_action": action,
        "provision_request": mask_data(body),
    })
    write_json(run_dir / "dag.json", {
        **dag,
        "sop_id": "runtime-management",
        "workflow_id": "runtime-management",
        "selected_action": action,
    })
    dag_nodes = {node.get("id"): node for node in dag.get("nodes", [])}
    for node in nodes:
        config = dag_nodes.get(node, {})
        status = node_status[node]
        write_json(run_dir / "nodes" / f"{node}.json", {
            "pipeline_id": pipeline_id,
            "node_id": node,
            "run_id": f"provision-{pipeline_id}",
            "status": status,
            "mode": "blocking",
            "needs": config.get("needs") or [],
            "title": config.get("title", node),
            "purpose": config.get("purpose", ""),
            "branch": config.get("branch", ""),
            "executor": config.get("executor") or {},
            "retryable": status != "skipped",
            "started_at": "",
            "finished_at": "",
            "duration_s": 0,
            "attempt": 0,
            "progress": 100 if status == "skipped" else 0,
            "declared_inputs": config.get("inputs") or {},
            "resolved_inputs": {},
            "declared_outputs": config.get("outputs") or {},
            "actual_outputs": {},
            "artifacts": [],
            "validation": {"status": "skipped" if status == "skipped" else "pending", "missing_outputs": [], "unexpected_outputs": []},
            "error": "",
            "updated_at": now,
        })
    run = {
        "pipeline_id": pipeline_id,
        "execution_id": pipeline_id,
        "sop_id": "runtime-management",
        "workflow_id": "runtime-management",
        "repo": sop.get("repo", ""),
        "status": "running",
        "source_type": action,
        "source_url": str(body.get("channel_url") or body.get("target_host") or ""),
        "input": mask_data({
            "action": action,
            "runtime_id": body.get("runtime_id", ""),
            "target_host": body.get("target_host", ""),
            "ssh_command": body.get("ssh_command", ""),
        }),
        "nodes": node_status,
        "started_at": now,
        "updated_at": now,
    }
    write_json(run_dir / "run.json", run)
    _append_run_event(run_dir, "pipeline_started", sequence=1, pipeline_id=pipeline_id, data={"action": action})
    store = run_index_store(sop, create=True)
    if store:
        try:
            store.upsert_execution(run)
        except Exception:
            pass


def trigger_runtime_management(sop, body):
    action = str(body.get("management_action") or body.get("action") or "create-runtime")
    if action not in RUNTIME_MANAGEMENT_ACTIONS:
        return 400, {"status": "error", "message": "action must be create-runtime, delete-runtime, create-instance, or delete-instance"}
    body = inject_runtime_management_config(body)
    now_token = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    pipeline_id = str(body.get("pipeline_id") or f"{action}-{now_token}")
    wiki = Path(sop["wiki_local_path"])
    secret_dir = wiki / ".sop" / "secrets" / pipeline_id
    secret_dir.mkdir(parents=True, exist_ok=True)
    try:
        secret_dir.chmod(0o700)
    except OSError:
        pass
    request_body = {**body, "management_action": action}
    request_file = secret_dir / "request.json"
    write_json(request_file, request_body, mode=0o600)
    create_runtime_management_workspace_run(sop, pipeline_id, action, request_body)

    runner = plugin_root() / "youtube-wiki" / "infrastructure" / "provision_runtime.py"
    if not runner.exists():
        return 500, {"status": "error", "message": "provision_runtime.py not found"}
    log_dir = wiki / "logs" / "pipeline-runs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{pipeline_id}.log"
    env = {**os.environ, "PATH": f"{Path.home() / '.local/bin'}:{Path.home() / 'bin'}:{os.environ.get('PATH', '')}"}
    command = [
        "python3",
        str(runner),
        "--wiki",
        str(wiki),
        "--pipeline-id",
        pipeline_id,
        "--node",
        "all",
        "--request-file",
        str(request_file),
    ]
    if body.get("dry_run"):
        command.append("--dry-run")
    with open(log_file, "ab") as stream:
        subprocess.Popen(command, env=env, stdout=stream, stderr=subprocess.STDOUT, close_fds=True)
    return 202, {
        "status": "triggered",
        "pipeline_id": pipeline_id,
        "status_url": f"/api/sop/{sop['id']}/runs/{pipeline_id}",
    }


def read_node_test_result(sop, node_id, pipeline_id):
    """Read back the report of an isolated single-node test (nodetest namespace).
    Returns {status: 'running', pending: True} until the report appears, then the
    terminal report with detail (e.g. ssh_ok / stdout / disk_ok / reason)."""
    # Security: only the nodetest namespace, no path traversal.
    safe = re.sub(r"[^A-Za-z0-9._-]", "", pipeline_id or "")
    if not safe.startswith("nodetest-"):
        return None
    wiki = Path(sop["wiki_local_path"])
    report = read_json(wiki / "raw" / "provision" / "nodetest" / safe / f"{node_id}.json")
    if not report:
        return {"pipeline_id": safe, "node_id": node_id, "status": "running", "pending": True}
    return {
        "pipeline_id": safe,
        "node_id": node_id,
        "status": report.get("status"),
        "started_at": report.get("started_at"),
        "finished_at": report.get("finished_at"),
        "reason": report.get("reason"),
        "manual_fix_hint": report.get("manual_fix_hint"),
        "detail": mask_data(report.get("detail") or {}),
    }


def trigger_node_test(sop, node_id, body):
    """Single-node isolated test, callable run-less from the asset center or from
    a Run's node panel. Reuses the engine's --test isolation + dependency guards.

    body: {request_overrides:{...}, seed_from_run_id?, confirm_mutating?, dry_run?}
    """
    body = body if isinstance(body, dict) else {}
    contract = provision_node_contract(node_id)
    if contract is None:
        return 404, {"status": "error", "message": f"No engine contract for node {node_id!r}"}
    side_effect = contract.get("side_effect")
    dep_class = contract.get("dep_class")
    confirm = bool(body.get("confirm_mutating"))
    dry_run = bool(body.get("dry_run"))
    seed_from = str(body.get("seed_from_run_id") or body.get("seed_from") or "")
    wiki = Path(sop["wiki_local_path"])

    # Guard 1: a *real* run of a mutating node changes the target machine — require
    # explicit confirm. A dry-run only simulates, so it does NOT need confirm.
    if side_effect == "mutating" and not confirm and not dry_run:
        return 409, {
            "status": "blocked",
            "node_id": node_id,
            "reason": "mutating node requires confirm_mutating=true for a real run (dry_run is exempt)",
            "side_effect": side_effect,
            "dep_class": dep_class,
        }
    # Guard 2: artifact_dependent nodes read upstream reports — require a seed run.
    if dep_class == "artifact_dependent" and not seed_from:
        return 409, {
            "status": "blocked",
            "node_id": node_id,
            "reason": "artifact_dependent node requires seed_from_run_id",
            "artifact_deps": contract.get("artifact_deps"),
        }

    # Base the request on an existing run's frozen request (target_host / ssh_command
    # / private_key_b64 / prior config) when from_run_id is given — so a node like
    # configure-hermes-model can reach the runtime that run created. request_overrides
    # (e.g. a new key) win over the base; management config fills any remaining gaps.
    from_run = re.sub(r"[^A-Za-z0-9._-]", "", str(body.get("from_run_id") or ""))
    base = {}
    if from_run:
        base = read_json(wiki / ".sop" / "secrets" / from_run / "request.json") or {}
    overrides = body.get("request_overrides") if isinstance(body.get("request_overrides"), dict) else {}
    action = str(overrides.get("management_action") or overrides.get("action")
                 or base.get("management_action") or base.get("action")
                 or contract.get("branch") or "create-runtime")
    if action not in RUNTIME_MANAGEMENT_ACTIONS:
        action = "create-runtime"
    request_body = inject_runtime_management_config({**base, **overrides, "management_action": action, "action": action})

    now_token = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    pipeline_id = f"nodetest-{node_id}-{now_token}"
    secret_dir = wiki / ".sop" / "secrets" / pipeline_id
    secret_dir.mkdir(parents=True, exist_ok=True)
    try:
        secret_dir.chmod(0o700)
    except OSError:
        pass
    request_file = secret_dir / "request.json"
    write_json(request_file, request_body, mode=0o600)

    runner = plugin_root() / "youtube-wiki" / "infrastructure" / "provision_runtime.py"
    if not runner.exists():
        return 500, {"status": "error", "message": "provision_runtime.py not found"}
    log_dir = wiki / "logs" / "pipeline-runs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{pipeline_id}.log"
    env = {**os.environ, "PATH": f"{Path.home() / '.local/bin'}:{Path.home() / 'bin'}:{os.environ.get('PATH', '')}"}
    command = ["python3", str(runner), "--wiki", str(wiki), "--pipeline-id", pipeline_id,
               "--node", node_id, "--request-file", str(request_file), "--test"]
    if seed_from:
        command += ["--seed-from", seed_from]
    if body.get("dry_run"):
        command.append("--dry-run")
    with open(log_file, "ab") as stream:
        subprocess.Popen(command, env=env, stdout=stream, stderr=subprocess.STDOUT, close_fds=True)
    return 202, {
        "status": "triggered",
        "mode": "node-test",
        "node_id": node_id,
        "pipeline_id": pipeline_id,
        "namespace": "nodetest",
        "dep_class": dep_class,
        "side_effect": side_effect,
        "report_path": f"raw/provision/nodetest/{pipeline_id}/{node_id}.json",
    }


def trigger_sop(sop, body):
    if sop.get("sop_type") == "runtime-management" or sop.get("id") == "runtime-management":
        return trigger_runtime_management(sop, body)
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

    # Runtime-management nodes re-run through the provisioning engine with the
    # original request context (reliable), NOT the context-less hermes webhook.
    if not launched and (sop.get("sop_type") == "runtime-management" or sop.get("id") == "runtime-management"):
        request_file = wiki / ".sop" / "secrets" / pipeline_id / "request.json"
        runner = plugin_root() / "youtube-wiki" / "infrastructure" / "provision_runtime.py"
        if request_file.exists() and runner.exists():
            mgmt_env = {**os.environ, "PATH": f"{Path.home() / '.local/bin'}:{Path.home() / 'bin'}:{os.environ.get('PATH', '')}"}
            command = ["python3", str(runner), "--wiki", str(wiki), "--pipeline-id", pipeline_id,
                       "--node", node_id, "--request-file", str(request_file)]
            try:
                with open(log_path, "ab") as log:
                    subprocess.Popen(command, env=mgmt_env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
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
            }, separators=(",", ":")).encode()
            try:
                signature = hmac.new(token.encode("utf-8"), payload, hashlib.sha256).hexdigest() if token else ""
                headers = {"Content-Type": "application/json"}
                if signature:
                    headers["X-Hub-Signature-256"] = f"sha256={signature}"
                req = _req.Request(
                    f"http://localhost:{port}/webhooks/{route}",
                    data=payload,
                    headers=headers,
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
            if path == []:
                return json_response(self, 200, {"status": "ok", "service": "sop-bridge", "runtime": runtime_info()})
            if path == ["api", "sop"]:
                return json_response(self, 200, sop_manifest())
            if path == ["api", "sop", "runtime"]:
                return json_response(self, 200, cached_read("runtime", runtime_info))
            if path == ["api", "sop", "v1", "runtime"]:
                return json_response(self, 200, cached_read("runtime", runtime_info))
            if path == ["api", "sop", "v1", "instances"]:
                cache_key = f"instances:v1:{parsed.query}"
                return json_response(self, 200, cached_read(cache_key, lambda: sop_instances_v1(query)))
            if len(path) >= 5 and path[0:4] == ["api", "sop", "v1", "instances"]:
                sop = find_sop(path[4])
                if not sop:
                    return json_response(self, 404, {"detail": "Instance not found"})
                if len(path) == 5:
                    return json_response(self, 200, instance_summary(sop))
                if len(path) == 6 and path[5] == "workflow":
                    dag = sop_dag(sop)
                    return json_response(self, 200, {
                        "instance_id": sop.get("instance_id") or sop.get("id", ""),
                        "workflow_binding": workflow_binding(sop),
                        "dag": dag,
                    })
                if len(path) == 7 and path[5] == "workflow" and path[6] == "runs":
                    return json_response(self, 200, sop_runs(sop, query))
                if len(path) >= 8 and path[5] == "workflow" and path[6] == "runs":
                    pipeline_id = path[7]
                    if len(path) == 8:
                        data = sop_run_detail(sop, pipeline_id)
                        return json_response(self, 200 if data else 404, data or {"detail": "Execution not found"})
                    if len(path) == 9 and path[8] == "nodes":
                        return json_response(self, 200, sop_run_nodes(sop, pipeline_id))
                    if len(path) == 10 and path[8] == "nodes":
                        if path[9] not in run_node_ids(sop, pipeline_id):
                            return json_response(self, 404, {
                                "detail": f"Node {path[9]!r} is not part of execution {pipeline_id!r}"
                            })
                        data = node_runtime_detail(sop, pipeline_id, path[9])
                        data["execution_id"] = data.get("pipeline_id", pipeline_id)
                        data["instance_id"] = sop.get("instance_id") or sop.get("id", "")
                        return json_response(self, 200, data)
                    if len(path) == 9 and path[8] == "events":
                        return json_response(self, 200, sop_run_events(sop, pipeline_id, query))
                    if len(path) == 9 and path[8] == "artifacts":
                        return json_response(self, 200, sop_run_artifacts(sop, pipeline_id))
                    if len(path) == 9 and path[8] == "logs":
                        return json_response(self, 200, sop_run_logs(sop, pipeline_id))
            if path == ["api", "sop", "instances"]:
                cache_key = f"instances:legacy:{parsed.query}"
                return json_response(self, 200, cached_read(cache_key, lambda: sop_instances_v1(query)))
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
                    if path[7] not in run_node_ids(sop, path[5]):
                        return json_response(self, 404, {
                            "detail": f"Node {path[7]!r} is not part of execution {path[5]!r}"
                        })
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
                if len(path) == 5 and path[3] == "config" and path[4] == "inheritance":
                    if (sop.get("instance_id") or sop.get("id")) != "runtime-management" and sop.get("sop_type") != "runtime-management":
                        return json_response(self, 404, {"detail": "Runtime inheritance preview is only available for runtime-management"})
                    return json_response(self, 200, runtime_config_inheritance_preview(sop))
                if len(path) == 5 and path[3] == "config" and path[4] == "management":
                    if (sop.get("instance_id") or sop.get("id")) != "runtime-management" and sop.get("sop_type") != "runtime-management":
                        return json_response(self, 404, {"detail": "Runtime management config is only available for runtime-management"})
                    return json_response(self, 200, runtime_management_config_preview(sop))
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
                    if path[6] not in run_node_ids(sop, path[4]):
                        return json_response(self, 404, {
                            "detail": f"Node {path[6]!r} is not part of run {path[4]!r}"
                        })
                    data = node_runtime_detail(sop, path[4], path[6])
                    return json_response(self, 200, data)
                if len(path) == 8 and path[3] == "runs" and path[5] == "nodes":
                    if path[6] not in run_node_ids(sop, path[4]):
                        return json_response(self, 404, {
                            "detail": f"Node {path[6]!r} is not part of run {path[4]!r}"
                        })
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
                    if path[6] not in run_node_ids(sop, path[4]):
                        return json_response(self, 404, {
                            "detail": f"Node {path[6]!r} is not part of run {path[4]!r}"
                        })
                    endpoint = str((sop.get("channel") or {}).get("url") or request_endpoint(self))
                    data = node_module_detail(sop, path[6], path[8], endpoint, path[4])
                    return json_response(self, 200 if data else 404, data or {
                        "detail": f"Node module {path[8]!r} not found"
                    })
                if len(path) == 7 and path[3] == "runs" and path[5] == "logs":
                    node_id_log = path[6]
                    if node_id_log not in run_node_ids(sop, path[4]):
                        return json_response(self, 404, {
                            "detail": f"Node {node_id_log!r} is not part of run {path[4]!r}"
                        })
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
                # GET /api/sop/{instance}/node-drafts/schema — draft input schema
                if len(path) == 5 and path[3] == "node-drafts" and path[4] == "schema":
                    return json_response(self, 200, {
                        "sop_id": sop.get("id", ""),
                        "schema": node_draft_schema(),
                    })
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
                # GET /api/sop/{instance}/nodes/{node_id}/contract — run-less engine contract
                if len(path) == 6 and path[3] == "nodes" and path[5] == "contract":
                    contract = provision_node_contract(path[4])
                    if contract is None:
                        return json_response(self, 404, {"detail": f"No engine contract for node {path[4]!r}"})
                    return json_response(self, 200, {
                        "sop_id": sop.get("id", ""),
                        "node_id": path[4],
                        "contract": contract,
                    })
                # GET /api/sop/{instance}/nodes/{node_id}/test-result/{pipeline_id}
                if len(path) == 7 and path[3] == "nodes" and path[5] == "test-result":
                    result = read_node_test_result(sop, path[4], path[6])
                    if result is None:
                        return json_response(self, 400, {"detail": "invalid nodetest pipeline_id"})
                    return json_response(self, 200, result)
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
                        "actions": node_actions(sop.get("id", ""), path[4], node_classification_for(path[4])),
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

        # POST /api/sop/runtime/hermes-smoke  → server-side signed Hermes connectivity check.
        if path == ["api", "sop", "runtime", "hermes-smoke"]:
            status, result = hermes_smoke_check(str(data.get("message") or data.get("text") or data.get("prompt") or "你好 你是谁"))
            return json_response(self, status, result)

        # POST /api/sop/runtime/hermes-agent-check  → local Hermes CLI answer check.
        if path == ["api", "sop", "runtime", "hermes-agent-check"]:
            status, result = hermes_agent_check(str(data.get("message") or data.get("text") or data.get("prompt") or "你好 你是谁"))
            return json_response(self, status, result)

        # POST /api/sop/{instance}/config/management/init  → copy current runtime env/env_file into server-side defaults
        if len(path) == 6 and path[:2] == ["api", "sop"] and path[3] == "config" and path[4] == "management" and path[5] == "init":
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            if (sop.get("instance_id") or sop.get("id")) != "runtime-management" and sop.get("sop_type") != "runtime-management":
                return json_response(self, 404, {"detail": "Runtime management config is only available for runtime-management"})
            overwrite = bool(data.get("overwrite"))
            changed = initialize_runtime_management_config(overwrite=overwrite)
            return json_response(self, 200, {
                "status": "initialized",
                "changed_keys": sorted(changed.keys()),
                "config": runtime_management_config_preview(sop),
            })

        # POST /api/sop/{instance}/config/management  → save server-side runtime management defaults
        if len(path) == 5 and path[:2] == ["api", "sop"] and path[3] == "config" and path[4] == "management":
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            if (sop.get("instance_id") or sop.get("id")) != "runtime-management" and sop.get("sop_type") != "runtime-management":
                return json_response(self, 404, {"detail": "Runtime management config is only available for runtime-management"})
            values = data.get("values") if isinstance(data.get("values"), dict) else data
            changed = save_runtime_management_config(values)
            return json_response(self, 200, {
                "status": "saved",
                "changed_keys": sorted(changed.keys()),
                "config": runtime_management_config_preview(sop),
            })

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

        # POST /api/sop/{instance}/nodes/{node_id}/actions/trigger — single-node test
        if (len(path) == 7 and path[:2] == ["api", "sop"]
                and path[3] == "nodes" and path[5] == "actions" and path[6] == "trigger"):
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            http_code, result = trigger_node_test(sop, path[4], data)
            return json_response(self, http_code, result)

        # POST /api/sop/{instance}/node-drafts
        if len(path) == 4 and path[:2] == ["api", "sop"] and path[3] == "node-drafts":
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            draft = create_node_draft(sop, data)
            status = 422 if (draft.get("validation") or {}).get("status") == "failed" else 201
            return json_response(self, status, draft)

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
