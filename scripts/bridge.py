import http.server
import base64
import copy
import difflib
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
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse
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
SOP_CONTROL_PLANE_API_URL = os.environ.get(
    "SOP_CONTROL_PLANE_API_URL",
    os.environ.get("CONTROL_PLANE_API_URL", "https://sop-control-plane.hb67egcim4.workers.dev"),
).rstrip("/")
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
    "WIKI_LLM_BASE_URL": ["wiki_llm_base_url", "llm_base_url", "openai_compatible_base_url", "openai_base_url"],
    "WIKI_LLM_API_KEY": ["wiki_llm_api_key", "llm_api_key", "wiki_llm_token"],
    "WIKI_LLM_MODEL": ["wiki_llm_model", "llm_model", "wiki_model"],
    "WIKI_DEEPSEEK_MODEL": ["wiki_deepseek_model", "hermes_default_model"],
    "EDGE_HANDOFF_LLM_BASE_URL": ["edge_handoff_llm_base_url"],
    "EDGE_HANDOFF_LLM_API_KEY": ["edge_handoff_llm_api_key", "edge_handoff_llm_token"],
    "EDGE_HANDOFF_LLM_MODEL": ["edge_handoff_llm_model"],
    "NODE_BUILDER_LLM_BASE_URL": ["node_builder_llm_base_url"],
    "NODE_BUILDER_LLM_API_KEY": ["node_builder_llm_api_key", "node_builder_llm_token"],
    "NODE_BUILDER_LLM_MODEL": ["node_builder_llm_model"],
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
    "WIKI_LLM_BASE_URL": "llm",
    "WIKI_LLM_API_KEY": "llm",
    "WIKI_LLM_MODEL": "llm",
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


YOUTUBE_WORKFLOW_ID = "youtube-research-wiki"
RUNTIME_MANAGEMENT_WORKFLOW_ID = "runtime-management"
YOUTUBE_WORKFLOW_NODES = [
    "youtube-fetch",
    "notebooklm-research",
    "youtube-deep-research",
    "wiki-build",
    "tg-notify",
]
SETTING_CONFIG_LABELS = {
    "GITHUB_TOKEN": "GitHub Token",
    "DEEPSEEK_API_KEY": "DeepSeek API Key",
    "WIKI_LLM_PROVIDER": "Wiki LLM Provider",
    "WIKI_LLM_BASE_URL": "Wiki LLM Gateway Base URL",
    "WIKI_LLM_API_KEY": "Wiki LLM Gateway API Key",
    "WIKI_LLM_MODEL": "Wiki LLM Model",
    "WIKI_DEEPSEEK_MODEL": "Wiki DeepSeek Model",
    "HERMES_MODEL_PROVIDER": "Hermes Model Provider",
    "HERMES_MODEL": "Hermes Default Model",
    "HERMES_MODEL_BASE_URL": "Hermes Model Base URL",
    "HERMES_OPENAI_API_KEY": "Hermes OpenAI-compatible API Key",
    "OPENAI_API_KEY": "OpenAI API Key",
    "GOOGLE_CLOUD_API_KEY": "Google Cloud API Key",
    "GEMINI_API_KEY": "Gemini API Key",
    "WIKI_GEMINI_MODEL": "Wiki Gemini Model",
    "GOOGLE_PROJECT_ID": "Google Project ID",
    "VERTEX_LOCATION": "Vertex Location",
    "WIKI_VERTEX_MODEL": "Wiki Vertex Model",
    "HERMES_WEBHOOK_TOKEN": "Hermes Webhook Token",
    "HERMES_WEBHOOK_PORT": "Hermes Webhook Port",
    "HERMES_WEBHOOK_URL": "Hermes Webhook URL",
    "HERMES_SMOKE_ROUTE": "Hermes Smoke Route",
    "WEBHOOK_PUBLIC_HOST": "Webhook Public Host",
    "NOTEBOOKLM_BRIDGE_URL": "NotebookLM Bridge URL",
    "NOTEBOOKLM_BRIDGE_TOKEN": "NotebookLM Bridge Token",
    "NOTEBOOKLM_CLIENT_ID": "NotebookLM Client ID",
    "BRIDGE_PORT": "Runtime Bridge Port",
    "YOUTUBE_WIKI_TG_TOKEN": "Telegram Bot Token",
    "YOUTUBE_WIKI_TG_CHAT_ID": "Telegram Chat ID",
    "YOUTUBE_CONTENT_API_URL": "YouTube Content API URL",
    "YOUTUBE_CONTENT_API_TOKEN": "YouTube Content API Token",
    "YOUTUBE_RESEARCH_WORKFLOW_URL": "YouTube Research Worker URL",
    "YOUTUBE_RESEARCH_WORKFLOW_TOKEN": "YouTube Research Worker Token",
    "CLOUDFLARE_EMAIL": "Cloudflare Email",
    "CLOUDFLARE_API_KEY": "Cloudflare API Key",
    "RUNTIME_SETTINGS_BACKEND": "Settings Backend",
    "RUNTIME_SETTINGS_CLOUDFLARE_EMAIL": "Settings Cloudflare Email",
    "RUNTIME_SETTINGS_CLOUDFLARE_API_KEY": "Settings Cloudflare API Key",
    "RUNTIME_SETTINGS_CLOUDFLARE_API_TOKEN": "Settings Cloudflare API Token",
    "RUNTIME_SETTINGS_CLOUDFLARE_ACCOUNT_ID": "Settings Cloudflare Account ID",
    "RUNTIME_SETTINGS_D1_DATABASE_ID": "Settings D1 Database ID",
    "RUNTIME_SETTINGS_D1_DATABASE_NAME": "Settings D1 Database Name",
    "TUNNEL_API": "Tunnel API",
    "SOP_UI_URL": "SOP UI URL",
    "GITHUB_CHANGFENGHU_TOKEN": "ChangfengHU GitHub Token",
    "GITHUB_SKKEORIW_TOKEN": "skkeoriw GitHub Token",
    "AGENT_REPO": "Agent Brain Repo",
    "SKILL_REPO": "Skill Repo",
    "AUTO_DOMAIN_REPO": "Auto Domain Repo",
    "AUTO_DOMAIN_TUNNEL_REPO": "Auto Domain Tunnel Repo",
    "SKILL_PUBLISHER_REPO": "Skill Publisher Repo",
    "RUNTIME_TARGET_SSH_COMMAND": "Target SSH Command",
    "RUNTIME_TARGET_PRIVATE_KEY": "Target Private Key",
    "RUNTIME_TARGET_PRIVATE_KEY_B64": "Target Private Key Base64",
    "RUNTIME_TARGET_RUNTIME_ID": "Target Runtime ID",
    "RUNTIME_TARGET_CHANNEL_URL": "Target Channel URL",
}


def canonical_runtime_setting_key(key):
    raw = str(key or "").strip()
    return runtime_settings_alias_map().get(raw, runtime_settings_alias_map().get(raw.lower(), raw))


def scoped_runtime_setting_key(scope, runtime_id, instance_id, key):
    canonical = canonical_runtime_setting_key(key)
    scope = str(scope or "global").strip()
    runtime_id = str(runtime_id or "").strip()
    instance_id = str(instance_id or "").strip()
    if scope == "instance":
        return f"instance:{runtime_id}:{instance_id}:{canonical}"
    if scope == "runtime":
        return f"runtime:{runtime_id}:{canonical}"
    return canonical


def scoped_runtime_setting_values(values, scope, runtime_id, instance_id=""):
    prefix = ""
    if scope == "instance":
        prefix = f"instance:{runtime_id}:{instance_id}:"
    elif scope == "runtime":
        prefix = f"runtime:{runtime_id}:"
    result = {}
    if not prefix:
        for key, value in (values or {}).items():
            if ":" not in str(key):
                result[canonical_runtime_setting_key(key)] = value
        return result
    for key, value in (values or {}).items():
        text = str(key)
        if text.startswith(prefix):
            result[canonical_runtime_setting_key(text[len(prefix):])] = value
    return result


def runtime_id_from_channel_url(channel_url):
    parsed = urlparse(str(channel_url or ""))
    host = parsed.netloc or parsed.path
    first_label = host.split("/")[0].split(".", 1)[0].strip()
    if re.fullmatch(r"runtime-\d+-\d+-\d+-\d+", first_label):
        return first_label
    return ""


def runtime_setting_id_aliases(sop=None, runtime=None):
    sop = sop if isinstance(sop, dict) else {}
    runtime = runtime if isinstance(runtime, dict) else runtime_info()
    explicit_runtime_id = str(sop.get("runtime_id") or "").strip()
    runtime_ids = {
        str(runtime.get("runtime_id") or "").strip(),
        str(runtime.get("id") or "").strip(),
        str(runtime.get("display_name") or "").strip(),
    }
    channel_runtime_id = runtime_id_from_channel_url(runtime.get("channel_url"))
    if explicit_runtime_id and explicit_runtime_id not in runtime_ids:
        candidates = [
            explicit_runtime_id,
            channel_runtime_id,
            runtime.get("runtime_id"),
            runtime.get("id"),
            runtime.get("display_name"),
        ]
    else:
        candidates = [
            channel_runtime_id,
            explicit_runtime_id,
            runtime.get("runtime_id"),
            runtime.get("id"),
            runtime.get("display_name"),
        ]
    result = []
    for value in candidates:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def primary_runtime_setting_id(sop=None, runtime=None):
    aliases = runtime_setting_id_aliases(sop, runtime)
    return aliases[0] if aliases else ""


def scoped_runtime_setting_values_for_aliases(values, scope, runtime_ids, instance_id=""):
    if scope == "global":
        return scoped_runtime_setting_values(values, scope, "", instance_id)
    result = {}
    for runtime_id in runtime_ids or []:
        scoped = scoped_runtime_setting_values(values, scope, runtime_id, instance_id)
        for key, value in scoped.items():
            if key not in result:
                result[key] = value
    return result


def unique_sorted(values):
    return sorted({str(value) for value in values if str(value or "").strip()})


def setting_capability_tags(key, category):
    tags = {category}
    if category == "github":
        tags.update({"git", "repo-access"})
    if category == "telegram":
        tags.update({"notification", "progress-notification"})
    if category == "youtube":
        tags.update({"youtube-research-worker", "content-api"})
    if category == "llm":
        tags.add("model")
        if str(key or "").startswith("WIKI_LLM_"):
            tags.update({"llm-gateway", "openai-compatible"})
        elif str(key or "") in {"GOOGLE_CLOUD_API_KEY", "GEMINI_API_KEY", "WIKI_GEMINI_MODEL", "GOOGLE_PROJECT_ID", "VERTEX_LOCATION", "WIKI_VERTEX_MODEL"}:
            tags.update({"gemini", "vertex"})
        elif str(key or "") in {"WIKI_DEEPSEEK_MODEL"}:
            tags.add("deepseek")
    if category == "hermes":
        tags.update({"agent-runtime", "model-auth"})
    if category == "notebooklm":
        tags.add("research-bridge")
    if category == "cloudflare":
        tags.update({"tunnel", "domain"})
    if category == "target":
        tags.update({"ssh", "machine"})
    if category == "repo":
        tags.add("source-repo")
    return unique_sorted(tags)


def setting_workflow_tags(key, category):
    workflows = set()
    if category in {"telegram", "youtube", "notebooklm", "llm"}:
        workflows.add(YOUTUBE_WORKFLOW_ID)
    if key in {"GITHUB_TOKEN", "HERMES_WEBHOOK_URL", "HERMES_WEBHOOK_TOKEN", "WEBHOOK_PUBLIC_HOST"}:
        workflows.update({YOUTUBE_WORKFLOW_ID, RUNTIME_MANAGEMENT_WORKFLOW_ID})
    if category in {"cloudflare", "settings", "repo", "target", "runtime"}:
        workflows.add(RUNTIME_MANAGEMENT_WORKFLOW_ID)
    if key.startswith("HERMES_") or key in {"DEEPSEEK_API_KEY", "OPENAI_API_KEY"}:
        workflows.update({YOUTUBE_WORKFLOW_ID, RUNTIME_MANAGEMENT_WORKFLOW_ID})
    if key in {"SOP_UI_URL", "BRIDGE_PORT"}:
        workflows.add(RUNTIME_MANAGEMENT_WORKFLOW_ID)
    return unique_sorted(workflows)


def setting_node_tags(key, category):
    if category == "telegram":
        return ["tg-notify", "youtube-deep-research"]
    if key.startswith("YOUTUBE_RESEARCH_WORKFLOW_"):
        return ["youtube-deep-research"]
    if key.startswith("YOUTUBE_CONTENT_API_"):
        return ["youtube-fetch", "youtube-deep-research"]
    if category == "notebooklm":
        return ["notebooklm-research"]
    if category == "llm":
        return ["wiki-build"]
    if key == "GITHUB_TOKEN":
        return unique_sorted([*YOUTUBE_WORKFLOW_NODES, *RUNTIME_MANAGEMENT_NODES])
    if category in {"hermes", "runtime", "cloudflare", "settings", "repo", "target"}:
        return unique_sorted(RUNTIME_MANAGEMENT_NODES)
    return []


def setting_operation_tags(key, category):
    operations = set()
    if category in {"telegram", "youtube", "notebooklm", "llm"} or key == "GITHUB_TOKEN":
        operations.update({"workflow-run", "node-run"})
    if category in {"cloudflare", "settings", "repo", "target", "runtime", "hermes"} or key in {"GITHUB_TOKEN", "GITHUB_CHANGFENGHU_TOKEN", "GITHUB_SKKEORIW_TOKEN"}:
        operations.update(RUNTIME_MANAGEMENT_ACTIONS)
    if category == "telegram":
        operations.add("create-instance")
    return unique_sorted(operations)


def setting_registry_definitions():
    definitions = []
    seen = set()
    for key, aliases in {**RUNTIME_CAPABILITY_ENV, **RUNTIME_MANAGEMENT_REQUEST_DEFAULTS}.items():
        canonical = canonical_runtime_setting_key(key)
        if canonical in seen:
            continue
        seen.add(canonical)
        category = RUNTIME_CONFIG_CATEGORIES.get(canonical, "runtime")
        workflow_tags = setting_workflow_tags(canonical, category)
        node_tags = setting_node_tags(canonical, category)
        capability_tags = setting_capability_tags(canonical, category)
        operation_tags = setting_operation_tags(canonical, category)
        definitions.append({
            "key": canonical,
            "aliases": aliases,
            "label": SETTING_CONFIG_LABELS.get(canonical, canonical.replace("_", " ").title()),
            "category": category,
            "capability": capability_tags[0] if capability_tags else category,
            "capability_tags": capability_tags,
            "workflow_tags": workflow_tags,
            "node_tags": node_tags,
            "operation_tags": operation_tags,
            "tags": unique_sorted([category, *workflow_tags, *node_tags, *capability_tags, *operation_tags]),
            "required": canonical in RUNTIME_REQUIRED_ENV or canonical in RUNTIME_MANAGEMENT_REQUIRED_DEFAULTS,
            "secret": is_secret_key(canonical),
            "scopes": ["run", "instance", "runtime", "global"],
            "description": f"{canonical} is resolved by Settings, Runtime, Instance and run override precedence.",
        })
    return sorted(definitions, key=lambda item: (item.get("category") or "", item.get("key") or ""))


def workflow_id_for_sop(sop):
    try:
        binding = workflow_binding(sop)
    except Exception:
        binding = {}
    candidate = str(
        (binding or {}).get("workflow_id")
        or sop.get("workflow_id")
        or sop.get("sop_type")
        or ""
    )
    node_ids = set((sop.get("nodes") or {}).keys())
    instance_like_id = str(sop.get("id") or sop.get("instance_id") or "")
    if (not candidate or candidate == instance_like_id) and node_ids:
        if node_ids & set(YOUTUBE_WORKFLOW_NODES):
            return YOUTUBE_WORKFLOW_ID
        if node_ids & set(RUNTIME_MANAGEMENT_NODES):
            return RUNTIME_MANAGEMENT_WORKFLOW_ID
    return candidate or str(sop.get("id") or "")


def setting_registry_item_matches(item, workflow_id="", node_id="", capability="", operation="", tag="", category=""):
    workflow_id = str(workflow_id or "").strip()
    node_id = str(node_id or "").strip()
    capability = str(capability or "").strip()
    operation = str(operation or "").strip()
    tag = str(tag or "").strip()
    category = str(category or "").strip()
    workflow_tags = set(item.get("workflow_tags") or [])
    node_tags = set(item.get("node_tags") or [])
    capability_tags = set(item.get("capability_tags") or [])
    operation_tags = set(item.get("operation_tags") or [])
    all_tags = set(item.get("tags") or [])
    if workflow_id and workflow_id not in workflow_tags and "all-workflows" not in workflow_tags:
        return False
    if node_id and node_id not in node_tags:
        return False
    if capability and capability not in capability_tags and capability != item.get("capability"):
        return False
    if operation and operation not in operation_tags:
        return False
    if category and category != item.get("category"):
        return False
    if tag and tag not in all_tags:
        return False
    return True


def setting_registry_preview(sop=None, node_id="", query=None):
    query = query or {}
    workflow_id = str(
        (query.get("workflow_id") or [""])[0]
        if isinstance(query.get("workflow_id"), list)
        else query.get("workflow_id") or ""
    ).strip()
    if not workflow_id and sop:
        workflow_id = workflow_id_for_sop(sop)
    node_id = str(
        node_id
        or ((query.get("node_id") or [""])[0] if isinstance(query.get("node_id"), list) else query.get("node_id") or "")
        or ""
    ).strip()
    filters = {
        "workflow_id": workflow_id,
        "node_id": node_id,
        "capability": str((query.get("capability") or [""])[0] if isinstance(query.get("capability"), list) else query.get("capability") or "").strip(),
        "operation": str((query.get("operation") or [""])[0] if isinstance(query.get("operation"), list) else query.get("operation") or "").strip(),
        "tag": str((query.get("tag") or [""])[0] if isinstance(query.get("tag"), list) else query.get("tag") or "").strip(),
        "category": str((query.get("category") or [""])[0] if isinstance(query.get("category"), list) else query.get("category") or "").strip(),
    }
    all_items = setting_registry_definitions()
    items = [
        item for item in all_items
        if setting_registry_item_matches(item, **filters)
    ]
    return {
        "workflow_id": workflow_id,
        "node_id": node_id,
        "filters": filters,
        "registry_total": len(all_items),
        "total": len(items),
        "items": items,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def capability_config_fields_for_node(sop, node_id="", workflow_id=""):
    if not workflow_id:
        workflow_id = workflow_id_for_sop(sop) if sop else ""
    preview = setting_registry_preview(sop, node_id=node_id, query={"workflow_id": workflow_id, "node_id": node_id})
    return [dict(item) for item in preview.get("items") or []]


def capability_config_resolution(sop, node_id="", run_overrides=None, workflow_id="", query=None):
    run_overrides = normalize_runtime_settings_values(run_overrides or {})
    runtime = runtime_info()
    runtime_id = primary_runtime_setting_id(sop, runtime)
    runtime_aliases = runtime_setting_id_aliases(sop, runtime)
    instance_id = str(sop.get("instance_id") or sop.get("id") or "")
    workflow_id = str(workflow_id or workflow_id_for_sop(sop) or "")
    env_file = os.environ.get("YOUTUBE_WIKI_ENV_FILE", str(Path.home() / ".agent-brain-plugins.env"))
    env_file_values = normalize_runtime_settings_values(read_env_file_values(env_file))
    bridge_env_values = normalize_runtime_settings_values(os.environ)
    settings = read_runtime_management_config()
    all_values = settings.get("values", {})
    global_values = scoped_runtime_setting_values(all_values, "global", runtime_id, instance_id)
    runtime_values = scoped_runtime_setting_values_for_aliases(all_values, "runtime", runtime_aliases, instance_id)
    instance_values = scoped_runtime_setting_values_for_aliases(all_values, "instance", runtime_aliases, instance_id)
    sources = [
        ("node-run-overrides", run_overrides),
        ("instance-settings", instance_values),
        ("runtime-settings", runtime_values),
        ("global-settings", global_values),
        ("bridge-env", bridge_env_values),
        ("runtime-env-file", env_file_values),
    ]
    registry = setting_registry_preview(sop, node_id=node_id, query={**(query or {}), "workflow_id": workflow_id, "node_id": node_id})
    fields = [dict(item) for item in registry.get("items") or []]
    items = []
    for field in fields:
        key = canonical_runtime_setting_key(field.get("key"))
        aliases = field.get("aliases") or RUNTIME_CAPABILITY_ENV.get(key, []) or RUNTIME_MANAGEMENT_REQUEST_DEFAULTS.get(key, [])
        candidates = [key, *aliases]
        resolved_value = ""
        resolved_source = "missing"
        matched_key = key
        scope_values = {}
        for scope_name, values in [
            ("run", run_overrides),
            ("instance", instance_values),
            ("runtime", runtime_values),
            ("global", global_values),
            ("runtime_env_file", env_file_values),
            ("bridge_env", bridge_env_values),
        ]:
            scope_raw = ""
            scope_key = key
            for candidate in candidates:
                if not is_blank_value(values.get(candidate)):
                    scope_raw = str(values.get(candidate))
                    scope_key = candidate
                    break
            scope_values[scope_name] = {
                "present": bool(scope_raw),
                "matched_key": scope_key if scope_raw else "",
                "masked_value": display_config_value(key, scope_raw) if scope_raw else "",
                "secret": is_secret_key(key),
            }
        for source_name, values in sources:
            for candidate in candidates:
                value = values.get(candidate)
                if not is_blank_value(value):
                    resolved_value = str(value)
                    resolved_source = f"{source_name}:{candidate}"
                    matched_key = candidate
                    break
            if resolved_value:
                break
        present = bool(resolved_value)
        items.append({
            "key": key,
            "aliases": aliases,
            "label": field.get("label") or key,
            "capability": field.get("capability") or RUNTIME_CONFIG_CATEGORIES.get(key, "runtime"),
            "category": RUNTIME_CONFIG_CATEGORIES.get(key, field.get("capability") or "runtime"),
            "workflow_tags": field.get("workflow_tags") or [],
            "node_tags": field.get("node_tags") or [],
            "capability_tags": field.get("capability_tags") or [],
            "operation_tags": field.get("operation_tags") or [],
            "tags": field.get("tags") or [],
            "description": field.get("description") or "",
            "required": bool(field.get("required", False)),
            "secret": is_secret_key(key),
            "editable_scopes": field.get("scopes") or ["run", "instance", "runtime", "global"],
            "matched_key": matched_key if present else "",
            "source": resolved_source,
            "source_kind": resolved_source.split(":", 1)[0] if resolved_source else "missing",
            "present": present,
            "masked_value": display_config_value(key, resolved_value) if present else "",
            "values_by_scope": scope_values,
        })
    groups = runtime_config_group_status(items)
    return {
        "runtime_id": runtime_id,
        "runtime_aliases": runtime_aliases,
        "instance_id": instance_id,
        "workflow_id": workflow_id,
        "node_id": node_id,
        "backend": settings.get("backend", runtime_settings_backend()),
        "updated_at": settings.get("updated_at", ""),
        "env_file": str(Path(env_file).expanduser()),
        "precedence": ["node-run-overrides", "instance-settings", "runtime-settings", "global-settings", "bridge-env", "runtime-env-file", "definition-default"],
        "registry_total": registry.get("registry_total", len(fields)),
        "registry_filters": registry.get("filters") or {},
        "items": items,
        "groups": groups,
        "scopes": {
            "run": "Only this Node Run request; not persisted.",
            "instance": "Saved for this Runtime + Instance in the settings backend.",
            "runtime": "Saved for this Runtime in the settings backend.",
            "global": "Saved as the global default in the settings backend.",
        },
        "note": "Secret values are masked. Submit a new value to override the selected scope.",
    }


def save_capability_config(sop, values, scope="instance", node_id=""):
    scope = str(scope or "instance").strip()
    if scope not in {"instance", "runtime", "global"}:
        raise ValueError("scope must be instance, runtime or global")
    runtime = runtime_info()
    runtime_id = primary_runtime_setting_id(sop, runtime)
    instance_id = str(sop.get("instance_id") or sop.get("id") or "")
    allowed = {field["key"] for field in setting_registry_definitions()}
    changed = {}
    current = normalize_runtime_settings_values(read_runtime_management_config_values())
    for key, value in (values or {}).items():
        canonical = canonical_runtime_setting_key(key)
        if canonical not in allowed:
            continue
        text = str(value or "").strip()
        if not text:
            continue
        scoped_key = scoped_runtime_setting_key(scope, runtime_id, instance_id, canonical)
        current[scoped_key] = text
        changed[scoped_key] = text
    if not changed:
        return {"status": "unchanged", "changed_keys": [], "scope": scope, "config": capability_config_resolution(sop, node_id)}
    payload = {"values": current, "updated_at": datetime.now(timezone.utc).isoformat()}
    if runtime_settings_backend() == "d1":
        try:
            runtime_settings_d1_save(changed, updated_by=f"{scope}-capability-config-save")
            payload = runtime_settings_save_to_file(current, payload["updated_at"])
        except Exception:
            write_json(RUNTIME_MANAGEMENT_CONFIG_PATH, payload, mode=0o600)
    else:
        write_json(RUNTIME_MANAGEMENT_CONFIG_PATH, payload, mode=0o600)
    return {
        "status": "saved",
        "scope": scope,
        "changed_keys": sorted(changed.keys()),
        "config": capability_config_resolution(sop, node_id),
    }


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


def runtime_management_secret_fields():
    return [
        "private_key",
        "ssh_private_key",
        "ssh_private_key_content",
        "private_key_content",
        "private_key_b64",
        "ssh_private_key_b64",
        "ssh_password",
    ]


def parse_ssh_host(command):
    text = str(command or "").strip()
    if not text:
        return ""
    tokens = shlex.split(text)
    host_token = ""
    for token in reversed(tokens):
        if token.startswith("-"):
            continue
        if "@" in token:
            host_token = token
            break
    if not host_token:
        return ""
    return host_token.rsplit("@", 1)[-1].strip("[]")


def control_plane_get_json(path, timeout=10):
    if not SOP_CONTROL_PLANE_API_URL:
        return {}
    url = f"{SOP_CONTROL_PLANE_API_URL}{path}"
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "sop-runtime-bridge/1.0"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def resolve_control_plane_machine(machine_id):
    safe = re.sub(r"[^A-Za-z0-9._:-]", "", str(machine_id or ""))
    if not safe:
        return {}
    try:
        payload = control_plane_get_json(f"/api/sop/v1/machines/{safe}/resolve")
    except Exception:
        return {}
    machine = payload.get("machine") if isinstance(payload, dict) else None
    return machine if isinstance(machine, dict) else {}


def find_control_plane_machine_by_host(host):
    target_host = str(host or "").strip()
    if not target_host:
        return {}
    try:
        payload = control_plane_get_json("/api/sop/v1/machines?page=1&page_size=200")
    except Exception:
        return {}
    machines = payload.get("machines") or payload.get("items") if isinstance(payload, dict) else []
    if not isinstance(machines, list):
        return {}
    for item in machines:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "active") == "deleted":
            continue
        if str(item.get("host") or "").strip() == target_host:
            machine_id = item.get("id") or ""
            return resolve_control_plane_machine(machine_id)
    return {}


def machine_credentials_from_request(merged):
    action = str(merged.get("management_action") or merged.get("action") or "").strip()
    if action not in {"create-runtime", "delete-runtime"}:
        return {}
    explicit_machine_id = str(
        merged.get("machine_id")
        or merged.get("target_machine_id")
        or merged.get("runtime_machine_id")
        or ""
    ).strip()
    request_has_secret = any(merged.get(candidate) not in {None, ""} for candidate in runtime_management_secret_fields())
    machine = {}
    if explicit_machine_id:
        machine = resolve_control_plane_machine(explicit_machine_id)
    elif not request_has_secret:
        host = str(merged.get("target_host") or "").strip() or parse_ssh_host(merged.get("ssh_command") or "")
        machine = find_control_plane_machine_by_host(host)
    if not machine:
        return {"_machine_credential_resolve_error": "machine credential not found"} if explicit_machine_id else {}

    ssh_command = str(machine.get("ssh_command") or machine.get("sshCommand") or merged.get("ssh_command") or "").strip()
    auth_type = str(machine.get("auth_type") or machine.get("authType") or "private_key").strip()
    private_key = str(machine.get("private_key") or machine.get("privateKey") or "")
    password = str(machine.get("password") or "")
    resolved = {
        "machine_id": str(machine.get("id") or explicit_machine_id or "").strip(),
        "ssh_command": ssh_command,
        "target_host": str(machine.get("host") or merged.get("target_host") or "").strip(),
    }
    if auth_type == "password" and password:
        resolved["ssh_password"] = password
        for key in ["private_key", "ssh_private_key", "ssh_private_key_content", "private_key_content", "private_key_b64", "ssh_private_key_b64"]:
            resolved[key] = ""
        return {key: value for key, value in resolved.items() if value not in {None, ""}}
    if private_key:
        resolved["private_key_b64"] = base64.b64encode(private_key.encode("utf-8")).decode("ascii")
        for key in ["private_key", "ssh_private_key", "ssh_private_key_content", "private_key_content", "ssh_private_key_b64"]:
            resolved[key] = ""
    return {key: value for key, value in resolved.items() if value not in {None, ""}}


def inject_runtime_management_config(body):
    values = read_runtime_management_config_values()
    if not values:
        merged = {**body}
        machine_credentials = machine_credentials_from_request(merged)
        if machine_credentials:
            merged.update(machine_credentials)
        return merged
    merged = {**body}
    injected = []
    action = str(merged.get("management_action") or merged.get("action") or "").strip()
    machine_credentials = machine_credentials_from_request(merged)
    if machine_credentials:
        merged.update(machine_credentials)
        if merged.get("_machine_credential_resolve_error"):
            injected.append("_machine_credential_resolve_error")
        else:
            for key in ["machine_id", "ssh_command", "target_host", "private_key_b64", "ssh_password"]:
                if merged.get(key) not in {None, ""}:
                    injected.append(key)
    request_private_key_fields = runtime_management_secret_fields()
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
        if default_key in {"RUNTIME_TARGET_PRIVATE_KEY", "RUNTIME_TARGET_PRIVATE_KEY_B64"}:
            if merged.get("machine_id") not in {None, ""}:
                continue
            if any(merged.get(candidate) not in {None, ""} for candidate in request_private_key_fields):
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


def scoped_runtime_config_value(values, runtime_id, instance_id, env_key, aliases=None):
    aliases = aliases or []
    candidates = [env_key, *aliases]
    for scope in ["instance", "runtime", "global"]:
        scoped = scoped_runtime_setting_values(values, scope, runtime_id, instance_id)
        for candidate in candidates:
            value = scoped.get(candidate)
            if not is_blank_value(value):
                return str(value), scope, candidate
    return "", "", ""


def request_has_any_value(source, keys):
    if not isinstance(source, dict):
        return False
    return any(not is_blank_value(source.get(key)) for key in keys)


def inject_node_test_instance_config(body, node_id):
    if node_id not in {"test-instance-github", "test-instance-telegram"}:
        return body
    values = read_runtime_management_config_values()
    if not values:
        return body
    merged = {**(body if isinstance(body, dict) else {})}
    runtime = runtime_info()
    runtime_id = str(merged.get("runtime_id") or runtime.get("runtime_id") or runtime.get("id") or "")
    instances = merged.get("instances") if isinstance(merged.get("instances"), list) else []
    if not instances:
        target_id = str(merged.get("instance_id") or merged.get("target_instance_id") or "").strip()
        if target_id:
            instances = [{"instance_id": target_id, "repo": merged.get("repo") or merged.get("instance_repo") or ""}]
    if not instances:
        return merged

    injected = list(merged.get("_instance_config_injected") or [])
    normalized_instances = []
    for raw_instance in instances:
        instance = dict(raw_instance) if isinstance(raw_instance, dict) else {"instance_id": str(raw_instance or "")}
        instance_id = str(instance.get("instance_id") or instance.get("id") or merged.get("instance_id") or "").strip()
        if not instance_id:
            normalized_instances.append(instance)
            continue

        if node_id == "test-instance-telegram":
            telegram = dict(instance.get("telegram") if isinstance(instance.get("telegram"), dict) else {})
            token_keys = ["token", "bot_token", "telegram_token", "telegram_bot_token", "tg_token", "youtube_wiki_tg_token", "instance_telegram_token", "instance_tg_token"]
            chat_keys = ["chat_id", "telegram_chat_id", "tg_chat_id", "youtube_wiki_tg_chat_id", "instance_telegram_chat_id", "instance_tg_chat_id"]
            token, token_scope, _token_key = scoped_runtime_config_value(
                values, runtime_id, instance_id, "YOUTUBE_WIKI_TG_TOKEN", RUNTIME_CAPABILITY_ENV["YOUTUBE_WIKI_TG_TOKEN"]
            )
            chat_id, chat_scope, _chat_key = scoped_runtime_config_value(
                values, runtime_id, instance_id, "YOUTUBE_WIKI_TG_CHAT_ID", RUNTIME_CAPABILITY_ENV["YOUTUBE_WIKI_TG_CHAT_ID"]
            )
            if token and not request_has_any_value(telegram, token_keys) and not request_has_any_value(instance, token_keys):
                telegram["token"] = token
                injected.append(f"{instance_id}:YOUTUBE_WIKI_TG_TOKEN:{token_scope}")
            if chat_id and not request_has_any_value(telegram, chat_keys) and not request_has_any_value(instance, chat_keys):
                telegram["chat_id"] = chat_id
                injected.append(f"{instance_id}:YOUTUBE_WIKI_TG_CHAT_ID:{chat_scope}")
            if telegram:
                instance["telegram"] = telegram

        if node_id == "test-instance-github":
            github_token, github_scope, _github_key = scoped_runtime_config_value(
                values, runtime_id, instance_id, "GITHUB_TOKEN", RUNTIME_CAPABILITY_ENV["GITHUB_TOKEN"]
            )
            if github_token and not request_has_runtime_config(merged, "GITHUB_TOKEN", RUNTIME_CAPABILITY_ENV["GITHUB_TOKEN"]):
                merged["GITHUB_TOKEN"] = github_token
                injected.append(f"{instance_id}:GITHUB_TOKEN:{github_scope}")

        normalized_instances.append(instance)

    merged["instances"] = normalized_instances
    if injected:
        merged["_instance_config_injected"] = sorted(set(injected))
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
        "actions": (
            detail.get("action_steps")
            if isinstance(detail.get("action_steps"), list) and detail.get("action_steps")
            else detail.get("actions")
            if isinstance(detail.get("actions"), list) and detail.get("actions")
            else meta["actions"]
        ),
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


def runtime_node_catalog_dir():
    return Path(os.environ.get("SOP_RUNTIME_NODE_CATALOG_DIR", str(Path.home() / ".sop" / "node-catalog"))).expanduser()


def runtime_node_catalog_item(node_id):
    node_id = str(node_id or "").strip()
    if not node_id:
        return None
    path = runtime_node_catalog_dir() / node_id / "node.yaml"
    if not path.exists():
        return None
    node = read_yaml(path) or {}
    if not isinstance(node, dict):
        return None
    return {
        **node,
        "id": node.get("id") or node_id,
        "node_id": node.get("id") or node_id,
        "source": "runtime-catalog",
        "runtime_catalog_path": str(path),
    }


def runtime_node_catalog_items():
    root = runtime_node_catalog_dir()
    rows = {}
    if not root.exists():
        return rows
    for path in sorted(root.glob("*/node.yaml")):
        node = read_yaml(path) or {}
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or path.parent.name).strip()
        if not node_id:
            continue
        rows[node_id] = {
            **node,
            "id": node_id,
            "node_id": node_id,
            "source": "runtime-catalog",
            "runtime_catalog_path": str(path),
        }
    return rows


def node_config_for(sop, node_id):
    nodes = sop.get("nodes") or {}
    if node_id in nodes:
        return nodes.get(node_id), "sop"
    runtime_node = runtime_node_catalog_item(node_id)
    if runtime_node is not None:
        return runtime_node, "runtime-catalog"
    return None, ""


def node_static_config(sop, node_id):
    """Return static node configuration from sop.yaml or Runtime node catalog."""
    config, source = node_config_for(sop, node_id)
    if config is None:
        return None

    plugin_dir = Path(os.environ.get(
        "YOUTUBE_WIKI_PLUGIN_DIR",
        str(Path.home() / "agent-brain-plugins" / "youtube-wiki"),
    )).expanduser()
    skills_dir = plugin_dir / "skills"

    configured_executor = config.get("executor") if isinstance(config.get("executor"), dict) else {}
    skill_block = config.get("skill") if isinstance(config.get("skill"), dict) else {}
    skill_name = configured_executor.get("skill") or skill_block.get("id") or config.get("skill") or config.get("webhook_route") or f"sop-{node_id}"
    skill_dirs = []
    for candidate in (skills_dir / str(skill_name), skills_dir / f"sop-{node_id}"):
        if candidate not in skill_dirs:
            skill_dirs.append(candidate)
    skill_dir = next((path for path in skill_dirs if path.exists()), skill_dirs[0])

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
    manifest = config if source == "runtime-catalog" else read_yaml(skill_dir / "node.yaml") if (skill_dir / "node.yaml").exists() else {}
    if skill_readme is None and source == "runtime-catalog":
        skill_block_for_readme = manifest.get("skill") if isinstance(manifest.get("skill"), dict) else {}
        digest = skill_block_for_readme.get("source_digest") if isinstance(skill_block_for_readme.get("source_digest"), dict) else {}
        for file_item in digest.get("files") or []:
            if not isinstance(file_item, dict):
                continue
            if str(file_item.get("path") or "").endswith(("SKILL.md", "README.md")):
                try:
                    skill_readme = str(file_item.get("content") or "")[:1600]
                except Exception:
                    skill_readme = ""
                break
    manifest_executor = manifest.get("executor") if isinstance(manifest.get("executor"), dict) else {}
    entry = str(configured_executor.get("entry") or manifest_executor.get("entry") or "").strip()
    script_candidates = []
    if entry:
        entry_path = Path(entry)
        if entry_path.is_absolute():
            script_candidates.append(entry_path)
        else:
            script_candidates.extend([directory / entry for directory in skill_dirs])
    for directory in skill_dirs:
        script_candidates.extend([
            directory / "scripts" / f"run_{node_id.replace('-', '_')}.sh",
            directory / "scripts" / f"run_{node_id}.sh",
            directory / "scripts" / f"run_{node_id.replace('-', '_')}.py",
            directory / "scripts" / f"run_{node_id}.py",
        ])
    skill_script = next((str(p.relative_to(plugin_dir.parent)) for p in script_candidates if p.exists()), None)
    manifest_inputs = manifest.get("inputs") if isinstance(manifest.get("inputs"), dict) else {}
    manifest_entry = manifest.get("entry") if isinstance(manifest.get("entry"), dict) else {}
    manifest_entry_inputs = manifest.get("entry_inputs") if isinstance(manifest.get("entry_inputs"), dict) else {}
    if not manifest_entry_inputs and isinstance(manifest_entry.get("inputs"), dict):
        manifest_entry_inputs = manifest_entry.get("inputs") or {}
    raw_manifest_outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), dict) else {}
    manifest_outputs = (
        raw_manifest_outputs.get("expected")
        if isinstance(raw_manifest_outputs.get("expected"), dict)
        else raw_manifest_outputs
    )
    manifest_optional_inputs = manifest.get("optional_inputs") if isinstance(manifest.get("optional_inputs"), dict) else {}
    node_inputs = merge_contracts(
        manifest_entry_inputs or manifest_inputs,
        config.get("inputs", {}),
        "input",
    )

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
            "skill": configured_executor.get("skill") or skill_block.get("id") or config.get("skill") or manifest_executor.get("skill", ""),
            "webhook_route": config.get("webhook_route", ""),
        },
        "inputs": node_inputs,
        "workflow_inputs": merge_contracts(manifest_inputs, config.get("inputs", {}), "input"),
        "entry_inputs": merge_contracts(manifest_entry_inputs or manifest_inputs, {}, "input"),
        "handoff": manifest.get("handoff") if isinstance(manifest.get("handoff"), dict) else {},
        "outputs": merge_contracts(manifest_outputs, config.get("outputs", {}), "output"),
        "optional_inputs": merge_contracts(manifest_optional_inputs, config.get("optional_inputs", {}), "input"),
        "infra": config.get("infra", {"tg_notify": True, "log_record": True}),
        "params": config.get("params") or {},
        "action_steps": config.get("actions") or manifest.get("actions") or [],
        "skill_script": skill_script,
        "skill_readme": skill_readme,
        "manifest": manifest,
        "source_digest": (manifest.get("skill") or {}).get("source_digest") if isinstance(manifest.get("skill"), dict) else {},
        "coverage_report": manifest.get("coverage_report") if isinstance(manifest.get("coverage_report"), dict) else {},
        "ui": config.get("ui") if isinstance(config.get("ui"), dict) else manifest.get("ui") if isinstance(manifest.get("ui"), dict) else {},
        "retryable": config.get("retryable", True),
        "source": source,
        "runtime_catalog_path": config.get("runtime_catalog_path", ""),
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


def merge_contracts(definition_value, binding_value, direction):
    definition = normalize_contract(definition_value, direction)
    binding = normalize_contract(binding_value, direction)
    merged = {}
    for name in ordered_unique([*definition.keys(), *binding.keys()]):
        item = {}
        if isinstance(definition.get(name), dict):
            item.update(definition[name])
        if isinstance(binding.get(name), dict):
            # Input bindings only supply runtime wiring such as `from`; output
            # bindings may also define whether a value is context/scalar or file.
            for key, value in binding[name].items():
                binding_overrides = {"from", "path", "type"} if direction == "output" else {"from", "path"}
                if key in binding_overrides or key not in item:
                    item[key] = value
        if direction == "input":
            item.setdefault("required", True)
            item.setdefault("kind", "scalar" if item.get("type") in {"string", "scalar"} else item.get("type", "auto"))
        else:
            item.setdefault("kind", "file" if item.get("type") in {"file", "files"} else item.get("type", "scalar"))
            item.setdefault("relayable", True)
        merged[name] = item
    return merged


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
    config, config_source = node_config_for(sop, node_id)
    if config is None:
        return None
    static = node_static_config(sop, node_id)
    if static is None:
        return None
    instance_id = sop.get("id") or sop.get("name") or ""
    manifest = static.get("manifest") if isinstance(static.get("manifest"), dict) else {}
    manifest_inputs = manifest.get("inputs") if isinstance(manifest.get("inputs"), dict) else {}
    manifest_entry_inputs = manifest.get("entry_inputs") if isinstance(manifest.get("entry_inputs"), dict) else {}
    manifest_optional_inputs = manifest.get("optional_inputs") if isinstance(manifest.get("optional_inputs"), dict) else {}
    manifest_outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), dict) else {}
    manifest_caps = manifest.get("capabilities") if isinstance(manifest.get("capabilities"), dict) else {}
    node_caps = config.get("capabilities") if isinstance(config.get("capabilities"), dict) else {}
    git_caps = {
        "enabled": True,
        "required": False,
        **(manifest_caps.get("git") if isinstance(manifest_caps.get("git"), dict) else {}),
        **(node_caps.get("git") if isinstance(node_caps.get("git"), dict) else {}),
    }
    telegram_caps = {
        "enabled": (static.get("infra") or {}).get("tg_notify", True),
        "required": False,
        **(manifest_caps.get("telegram") if isinstance(manifest_caps.get("telegram"), dict) else {}),
        **(node_caps.get("telegram") if isinstance(node_caps.get("telegram"), dict) else {}),
    }
    return {
        **static,
        "description": static.get("purpose") or manifest.get("description", ""),
        "purpose": static.get("purpose", ""),
        "source": static.get("source") or config_source,
        "runtime_catalog_path": static.get("runtime_catalog_path", ""),
        "branch": static.get("branch", ""),
        "retryable": static.get("retryable", True),
        "case": classify_node(node_id, config, static),
        "skill": {
            "id": (static.get("executor") or {}).get("skill", ""),
            "source": ((manifest.get("skill") or {}).get("source") if isinstance(manifest.get("skill"), dict) else "") or ("runtime-catalog" if config_source == "runtime-catalog" else "repository"),
            "install_command": (manifest.get("skill") or {}).get("install_command", "") if isinstance(manifest.get("skill"), dict) else "",
            "source_digest": static.get("source_digest") or {},
            "readme_path": static.get("skill_script", "").replace("/scripts/", "/SKILL.md") if static.get("skill_script") else "",
            "summary": static.get("skill_readme", ""),
        },
        "entry_inputs": normalize_contract(manifest_entry_inputs or manifest_inputs or static.get("entry_inputs", {}) or static.get("inputs", {}), "input"),
        "handoff": manifest.get("handoff") if isinstance(manifest.get("handoff"), dict) else static.get("handoff") or {},
        "workflow_inputs": normalize_contract(static.get("workflow_inputs") or {}, "input"),
        "inputs": normalize_contract(manifest_entry_inputs or manifest_inputs or static.get("inputs", {}), "input"),
        "optional_inputs": normalize_contract(manifest_optional_inputs or static.get("optional_inputs", {}), "input"),
        "outputs": normalize_contract(manifest_outputs or static.get("outputs", {}), "output"),
        "capabilities": {
            "git": git_caps,
            "telegram": telegram_caps,
            "sse": {"enabled": True, "required": True},
        },
        "actions": node_actions(instance_id, node_id, node_classification_for(node_id)),
        "cli": node_cli_examples(endpoint or "{endpoint}", instance_id, node_id),
        "ui": static.get("ui") or {},
        "coverage_report": static.get("coverage_report") or {},
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
WORKFLOW_EDGE_DRAFT_SCHEMA_VERSION = "workflow-edge-draft-schema/v1"

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
    node_ids = ordered_unique([*(sop.get("nodes") or {}).keys(), *runtime_node_catalog_items().keys()])
    for node_id in node_ids:
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
    entry_input_name = str(spec.get("entry_input_name") or spec.get("input_name") or "prompt")
    output_name = str(spec.get("output_name") or "result")
    return {
        "schema": "node-definition/v1",
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
        "entry_inputs": {
            entry_input_name: {
                "type": spec.get("input_type") or "string",
                "kind": "scalar",
                "value_type": spec.get("input_value_type") or "text",
                "required": True,
            }
        },
        "handoff": {
            "accepts": [
                {"role": "upstream_outputs_dir", "required": False},
                {"role": "instruction", "required": True},
                {"role": "context", "required": False},
            ],
            "produces": {
                "outputs_dir": "raw/node-runs/{run_id}/outputs",
                "manifest": "raw/node-runs/{run_id}/outputs/manifest.json",
            },
        },
        "inputs": {
            entry_input_name: {
                "type": spec.get("input_type") or "string",
                "kind": "scalar",
                "value_type": spec.get("input_value_type") or "text",
                "required": True,
            }
        },
        "outputs": {
            output_name: {
                "type": spec.get("output_type") or "file",
                "kind": spec.get("output_type") or "file",
                "value_type": spec.get("output_value_type") or "json",
                "relayable": True,
                "path": spec.get("output_path") or f"raw/node-runs/{{run_id}}/outputs/outputs/{output_name}.json",
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
        "description": "创建新节点或编辑现有节点定义的草稿；不会修改生产 DAG。",
        "draft_types": [
            {
                "id": "create_node",
                "title": "Create Node Draft",
                "description": "把一个 Skill 安装命令转换成可验证的 SOP 节点草稿。",
            },
            {
                "id": "edit_node_definition",
                "title": "Edit Existing Node Definition",
                "description": "为现有节点生成 definition change request，保存目标是 agent-brain-plugins，不直接改 runtime sop.yaml。",
            },
        ],
        "fields": [
            {"name": "draft_type", "label": "Draft Type", "type": "enum", "required": False, "default": "create_node", "maps_to": "draft_type"},
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
            {"name": "entry_input_name", "label": "Entry input name", "type": "slug", "required": False, "default": "prompt", "maps_to": "entry_inputs"},
            {"name": "input_type", "label": "Entry input type", "type": "enum", "required": False, "default": "string", "maps_to": "entry_inputs.*.type"},
            {"name": "input_value_type", "label": "Entry input value type", "type": "enum", "required": False, "default": "text", "maps_to": "entry_inputs.*.value_type"},
            {"name": "output_name", "label": "Output name", "type": "slug", "required": False, "default": "artifact", "maps_to": "outputs"},
            {
                "name": "output_path",
                "label": "Output path",
                "type": "path_pattern",
                "required": False,
                "default": "raw/node-runs/{run_id}/outputs/outputs/{output_name}.json",
                "maps_to": "outputs.*.path",
            },
        ],
        "edit_fields": [
            {"name": "node_id", "label": "Node ID", "type": "node_id", "required": True, "maps_to": "id"},
            {"name": "title", "label": "Title", "type": "string", "required": False, "maps_to": "title"},
            {"name": "description", "label": "Description", "type": "text", "required": False, "maps_to": "description"},
            {"name": "mode", "label": "Mode", "type": "enum", "required": False, "maps_to": "mode"},
            {"name": "needs", "label": "Needs", "type": "list", "required": False, "maps_to": "needs"},
            {"name": "inputs", "label": "Inputs Contract", "type": "json", "required": False, "maps_to": "inputs"},
            {"name": "optional_inputs", "label": "Optional Inputs Contract", "type": "json", "required": False, "maps_to": "optional_inputs"},
            {"name": "outputs", "label": "Outputs Contract", "type": "json", "required": False, "maps_to": "outputs"},
            {"name": "capabilities", "label": "Capabilities", "type": "json", "required": False, "maps_to": "capabilities"},
        ],
        "defaults": {
            "executor_type": "agent-skill",
            "agent": "hermes",
            "mode": "blocking",
            "input_type": "string",
            "input_value_type": "text",
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
    if str(spec.get("draft_type") or "create_node") == "edit_node_definition":
        return {
            "schema_id": NODE_DRAFT_SCHEMA_VERSION,
            "status": "passed",
            "errors": [],
            "missing_fields": [],
        }
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
    for name in ("skill_id", "node_id", "entry_input_name", "input_name", "output_name"):
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


def node_definition_project_targets(sop, node_id):
    plugin_dir = Path(os.environ.get(
        "YOUTUBE_WIKI_PLUGIN_DIR",
        str(Path.home() / "agent-brain-plugins" / "youtube-wiki"),
    )).expanduser()
    return {
        "runtime_sop_file": str(sop.get("sop_file") or ""),
        "project_skill_node_yaml": str(plugin_dir / "skills" / f"sop-{node_id}" / "node.yaml"),
        "project_template_sop_yaml": str(plugin_dir / "templates" / "wiki-repo" / "sop.yaml"),
        "save_owner": "agent-brain-plugins",
    }


def normalize_edit_contract(value):
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            value = yaml.safe_load(text)
        except Exception:
            return "__invalid_yaml__"
    return value if isinstance(value, dict) else "__invalid_contract__"


def validate_contract_block(block, direction, label):
    errors = []
    if block in (None, {}):
        return errors
    if isinstance(block, str) and block in {"__invalid_yaml__", "__invalid_contract__"}:
        return [{"field": label, "code": "invalid_contract", "message": f"{label} must be a JSON/YAML object"}]
    if not isinstance(block, dict):
        return [{"field": label, "code": "invalid_contract", "message": f"{label} must be a JSON/YAML object"}]
    for name, spec in block.items():
        field = f"{label}.{name}"
        if not str(name or "").strip():
            errors.append({"field": label, "code": "empty_name", "message": f"{label} contains an empty contract name"})
            continue
        if not isinstance(spec, (dict, str)):
            errors.append({"field": field, "code": "invalid_spec", "message": f"{field} must be a string or object"})
            continue
        spec_obj = normalize_contract({name: spec}, direction).get(name, {}) if not isinstance(spec, dict) else spec
        if direction == "input":
            resolvers = spec_obj.get("resolvers") if isinstance(spec_obj.get("resolvers"), list) else []
            for index, resolver in enumerate(resolvers):
                resolver_field = f"{field}.resolvers[{index}]"
                if not isinstance(resolver, dict):
                    errors.append({"field": resolver_field, "code": "invalid_resolver", "message": f"{resolver_field} must be an object"})
                    continue
                kind = str(resolver.get("kind") or resolver.get("type") or "").strip()
                if not kind:
                    errors.append({"field": resolver_field, "code": "missing_kind", "message": f"{resolver_field} must declare kind"})
                if kind == "json_path" and not str(resolver.get("path") or "").strip():
                    errors.append({"field": resolver_field, "code": "missing_path", "message": f"{resolver_field} json_path resolver requires path"})
                if kind == "regex" and not str(resolver.get("pattern") or "").strip():
                    errors.append({"field": resolver_field, "code": "missing_pattern", "message": f"{resolver_field} regex resolver requires pattern"})
        if direction == "output":
            path = str(spec_obj.get("path") or "").strip()
            if path:
                path_obj = Path(path)
                if path_obj.is_absolute() or ".." in path_obj.parts:
                    errors.append({"field": field, "code": "unsafe_path", "message": f"{field} path must be relative and stay inside the workspace"})
    return errors


def validate_node_definition_edit_input(sop, spec):
    node_id = str(spec.get("node_id") or "").strip()
    errors = []
    nodes = sop.get("nodes") if isinstance(sop.get("nodes"), dict) else {}
    if not node_id:
        errors.append({"field": "node_id", "code": "required", "message": "Node ID is required"})
    elif node_id not in nodes:
        errors.append({"field": "node_id", "code": "node_not_found", "message": f"node_id {node_id} does not exist in this workflow"})
    for key, direction in (("inputs", "input"), ("optional_inputs", "input"), ("outputs", "output")):
        if key in spec:
            errors.extend(validate_contract_block(normalize_edit_contract(spec.get(key)), direction, key))
    executor = normalize_edit_contract(spec.get("executor")) if "executor" in spec else None
    if isinstance(executor, str) and executor in {"__invalid_yaml__", "__invalid_contract__"}:
        errors.append({"field": "executor", "code": "invalid_contract", "message": "executor must be a JSON/YAML object"})
    elif executor not in (None, {}) and not isinstance(executor, dict):
        errors.append({"field": "executor", "code": "invalid_contract", "message": "executor must be a JSON/YAML object"})
    capabilities = normalize_edit_contract(spec.get("capabilities")) if "capabilities" in spec else None
    if isinstance(capabilities, str) and capabilities in {"__invalid_yaml__", "__invalid_contract__"}:
        errors.append({"field": "capabilities", "code": "invalid_contract", "message": "capabilities must be a JSON/YAML object"})
    elif capabilities not in (None, {}) and not isinstance(capabilities, dict):
        errors.append({"field": "capabilities", "code": "invalid_contract", "message": "capabilities must be a JSON/YAML object"})
    return {
        "schema_id": NODE_DRAFT_SCHEMA_VERSION,
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "missing_fields": [error["field"] for error in errors if error["code"] == "required"],
        "production_dag_changed": False,
    }


def proposed_node_definition_from_edit(sop, spec):
    node_id = str(spec.get("node_id") or "").strip()
    current = copy.deepcopy((sop.get("nodes") or {}).get(node_id) or {})
    static = node_static_config(sop, node_id) or {}
    proposed = copy.deepcopy(current)
    for key in ("title", "description", "purpose", "mode", "webhook_route"):
        if key in spec and spec.get(key) not in (None, ""):
            proposed[key] = spec.get(key)
    if "needs" in spec:
        needs = spec.get("needs")
        if isinstance(needs, str):
            needs = [item.strip() for item in needs.split(",") if item.strip()]
        proposed["needs"] = needs if isinstance(needs, list) else []
    if "executor" in spec:
        normalized_executor = normalize_edit_contract(spec.get("executor"))
        if isinstance(normalized_executor, dict):
            executor = copy.deepcopy(proposed.get("executor") if isinstance(proposed.get("executor"), dict) else {})
            executor.update(normalized_executor)
            for alias, target in (("skill", "skill"), ("entry", "entry"), ("agent", "agent")):
                if alias in spec and spec.get(alias) not in (None, ""):
                    executor[target] = str(spec.get(alias)).strip()
            if executor:
                proposed["executor"] = executor
    else:
        executor_updates = {}
        for alias, target in (("skill", "skill"), ("entry", "entry"), ("agent", "agent")):
            if alias in spec and spec.get(alias) not in (None, ""):
                executor_updates[target] = str(spec.get(alias)).strip()
        if executor_updates:
            executor = copy.deepcopy(proposed.get("executor") if isinstance(proposed.get("executor"), dict) else {})
            executor.update(executor_updates)
            proposed["executor"] = executor
    for key in ("inputs", "optional_inputs", "outputs", "capabilities"):
        if key in spec:
            normalized = normalize_edit_contract(spec.get(key))
            if isinstance(normalized, dict):
                proposed[key] = normalized
    return {
        "node_id": node_id,
        "before": current,
        "before_merged": {
            "inputs": normalize_contract(static.get("inputs", {}), "input"),
            "optional_inputs": normalize_contract(static.get("optional_inputs", {}), "input"),
            "outputs": normalize_contract(static.get("outputs", {}), "output"),
            "capabilities": node_registry_item(sop, node_id, "").get("capabilities") if node_registry_item(sop, node_id, "") else {},
        },
        "proposed": proposed,
    }


def summarize_definition_changes(before, proposed):
    keys = ordered_unique([*(before or {}).keys(), *(proposed or {}).keys()])
    rows = []
    for key in keys:
        if (before or {}).get(key) != (proposed or {}).get(key):
            rows.append({
                "field": key,
                "before": mask_data((before or {}).get(key)),
                "after": mask_data((proposed or {}).get(key)),
            })
    return rows


def create_node_definition_edit_draft(sop, spec):
    validation = validate_node_definition_edit_input(sop, spec)
    if validation["errors"]:
        return {
            "draft_id": "",
            "draft_type": "edit_node_definition",
            "draft_path": "",
            "node": {},
            "change_request": {},
            "validation": validation,
        }
    wiki = Path(sop["wiki_local_path"])
    node_id = str(spec.get("node_id") or "").strip()
    proposal = proposed_node_definition_from_edit(sop, spec)
    draft_id = f"{node_id}-definition-edit-{int(time.time())}"
    draft_dir = wiki / "raw" / "node-drafts" / draft_id
    draft_dir.mkdir(parents=True, exist_ok=True)
    (draft_dir / "node.yaml").write_text(yaml.safe_dump(proposal["proposed"], allow_unicode=True, sort_keys=False), encoding="utf-8")
    targets = node_definition_project_targets(sop, node_id)
    change_request = {
        "version": 1,
        "draft_type": "edit_node_definition",
        "draft_id": draft_id,
        "node_id": node_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "save_target": "agent-brain-plugins",
        "targets": targets,
        "change_summary": summarize_definition_changes(proposal["before"], proposal["proposed"]),
        "before": mask_data(proposal["before"]),
        "before_merged": mask_data(proposal["before_merged"]),
        "proposed": mask_data(proposal["proposed"]),
        "notes": [
            "This draft does not modify runtime sop.yaml.",
            "Apply/publish must be handled by a repo-first change in agent-brain-plugins.",
        ],
    }
    (draft_dir / "change_request.json").write_text(json.dumps(change_request, ensure_ascii=False, indent=2), encoding="utf-8")
    validation = {
        **validation,
        "change_count": len(change_request["change_summary"]),
        "targets": targets,
    }
    (draft_dir / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "draft_id": draft_id,
        "draft_type": "edit_node_definition",
        "draft_path": str(draft_dir),
        "node": proposal["proposed"],
        "change_request": change_request,
        "validation": validation,
    }


def create_node_draft(sop, spec):
    if str(spec.get("draft_type") or "create_node") == "edit_node_definition":
        return create_node_definition_edit_draft(sop, spec)
    node_draft = spec.get("node_draft") if isinstance(spec.get("node_draft"), dict) else None
    if node_draft:
        node_id = str(node_draft.get("id") or node_draft.get("node_id") or "").strip()
        existing_nodes = set((sop.get("nodes") or {}).keys()) | set(runtime_node_catalog_items().keys())
        errors = []
        if not node_id:
            errors.append({"field": "node_draft.id", "code": "required", "message": "node_draft.id is required"})
        elif node_id in existing_nodes:
            errors.append({"field": "node_draft.id", "code": "node_exists", "message": f"node_id {node_id} already exists"})
        if str(node_draft.get("schema") or "") != "node-definition/v1":
            errors.append({"field": "node_draft.schema", "code": "invalid_schema", "message": "node_draft.schema must be node-definition/v1"})
        executor = node_draft.get("executor") if isinstance(node_draft.get("executor"), dict) else {}
        skill = node_draft.get("skill") if isinstance(node_draft.get("skill"), dict) else {}
        if not (executor.get("skill") or skill.get("id")):
            errors.append({"field": "node_draft.skill", "code": "required", "message": "node_draft skill id is required"})
        validation_base = {
            "schema_id": NODE_DRAFT_SCHEMA_VERSION,
            "status": "passed" if not errors else "failed",
            "errors": errors,
            "missing_fields": [error["field"] for error in errors if error["code"] == "required"],
            "production_dag_changed": False,
        }
        if errors:
            return {"draft_id": "", "draft_path": "", "node": {}, "validation": validation_base}
        wiki = Path(sop["wiki_local_path"])
        draft = copy.deepcopy(node_draft)
        draft["id"] = node_id
        draft_id = f"{node_id}-{int(time.time())}"
        draft_dir = wiki / "raw" / "node-drafts" / draft_id
        draft_dir.mkdir(parents=True, exist_ok=True)
        write_json(draft_dir / "request.json", spec.get("request") if isinstance(spec.get("request"), dict) else {
            "skill_install_command": ((draft.get("skill") or {}).get("install_command") if isinstance(draft.get("skill"), dict) else ""),
            "source": "node-builder",
        })
        write_json(draft_dir / "node-builder-evaluation.json", spec.get("node_builder_evaluation") if isinstance(spec.get("node_builder_evaluation"), dict) else {})
        write_json(draft_dir / "trace.json", spec.get("trace") if isinstance(spec.get("trace"), dict) else {})
        (draft_dir / "node.yaml").write_text(yaml.safe_dump(draft, allow_unicode=True, sort_keys=False), encoding="utf-8")
        validation = {**validation_base, "status": "passed", "node_builder": bool(spec.get("node_builder_evaluation"))}
        write_json(draft_dir / "validation.json", validation)
        return {
            "draft_id": draft_id,
            "draft_type": "create_node",
            "draft_path": str(draft_dir),
            "node": draft,
            "node_builder_evaluation": spec.get("node_builder_evaluation") if isinstance(spec.get("node_builder_evaluation"), dict) else {},
            "validation": validation,
        }
    existing_nodes = set((sop.get("nodes") or {}).keys()) | set(runtime_node_catalog_items().keys()) if isinstance(sop.get("nodes"), dict) else set(runtime_node_catalog_items().keys())
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


def node_builder_script():
    configured = os.environ.get("NODE_BUILDER_SCRIPT", "").strip()
    candidates = [
        Path(configured).expanduser() if configured else None,
        plugin_root() / "youtube-wiki/skills/sop-node-builder/scripts/node_builder.py",
        Path.home() / "agent-brain-plugins/youtube-wiki/skills/sop-node-builder/scripts/node_builder.py",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    return None


def node_builder_env(sop, data):
    context = node_run_config_context(data, sop)
    base_url = node_run_config_lookup(context, "NODE_BUILDER_LLM_BASE_URL", [
        *RUNTIME_CAPABILITY_ENV.get("NODE_BUILDER_LLM_BASE_URL", []),
        "WIKI_LLM_BASE_URL",
        *RUNTIME_CAPABILITY_ENV.get("WIKI_LLM_BASE_URL", []),
        "HERMES_MODEL_BASE_URL",
        *RUNTIME_CAPABILITY_ENV.get("HERMES_MODEL_BASE_URL", []),
        "EDGE_HANDOFF_LLM_BASE_URL",
        *RUNTIME_CAPABILITY_ENV.get("EDGE_HANDOFF_LLM_BASE_URL", []),
    ])
    api_key = node_run_config_lookup(context, "NODE_BUILDER_LLM_API_KEY", [
        *RUNTIME_CAPABILITY_ENV.get("NODE_BUILDER_LLM_API_KEY", []),
        "WIKI_LLM_API_KEY",
        *RUNTIME_CAPABILITY_ENV.get("WIKI_LLM_API_KEY", []),
        "HERMES_OPENAI_API_KEY",
        "OPENAI_API_KEY",
        *RUNTIME_CAPABILITY_ENV.get("HERMES_OPENAI_API_KEY", []),
        *RUNTIME_CAPABILITY_ENV.get("OPENAI_API_KEY", []),
        "EDGE_HANDOFF_LLM_API_KEY",
        *RUNTIME_CAPABILITY_ENV.get("EDGE_HANDOFF_LLM_API_KEY", []),
    ])
    model = node_run_config_lookup(context, "NODE_BUILDER_LLM_MODEL", [
        *RUNTIME_CAPABILITY_ENV.get("NODE_BUILDER_LLM_MODEL", []),
        "WIKI_LLM_MODEL",
        *RUNTIME_CAPABILITY_ENV.get("WIKI_LLM_MODEL", []),
        "HERMES_MODEL",
        *RUNTIME_CAPABILITY_ENV.get("HERMES_MODEL", []),
        "WIKI_DEEPSEEK_MODEL",
        *RUNTIME_CAPABILITY_ENV.get("WIKI_DEEPSEEK_MODEL", []),
        "EDGE_HANDOFF_LLM_MODEL",
        *RUNTIME_CAPABILITY_ENV.get("EDGE_HANDOFF_LLM_MODEL", []),
    ])
    if is_blank_value(model.get("value")):
        model = {"key": "NODE_BUILDER_LLM_MODEL", "value": os.environ.get("NODE_BUILDER_LLM_FALLBACK_MODEL", "deepseek-v4-pro"), "source": "default"}
    env = os.environ.copy()
    if not is_blank_value(base_url.get("value")):
        env["NODE_BUILDER_LLM_BASE_URL"] = str(base_url.get("value")).rstrip("/")
    if not is_blank_value(api_key.get("value")):
        env["NODE_BUILDER_LLM_API_KEY"] = str(api_key.get("value"))
    if not is_blank_value(model.get("value")):
        env["NODE_BUILDER_LLM_MODEL"] = str(model.get("value"))
    env.setdefault("NODE_BUILDER_LLM_TIMEOUT", "75" if data.get("async_job") else "24")
    env.setdefault("NODE_BUILDER_LLM_MAX_TOKENS", "4096")
    clamp_int_env(env, "NODE_BUILDER_LLM_MAX_TOKENS", 1024, 8192)
    return env, {
        "base_url": env_config_item(base_url.get("key") or "NODE_BUILDER_LLM_BASE_URL", "Node Builder LLM Base URL", required=True, value=str(base_url.get("value") or "").rstrip("/"), source=base_url.get("source") or "missing:NODE_BUILDER_LLM_BASE_URL"),
        "api_key": env_config_item(api_key.get("key") or "NODE_BUILDER_LLM_API_KEY", "Node Builder LLM API Key", required=True, value=api_key.get("value"), source=api_key.get("source") or "missing:NODE_BUILDER_LLM_API_KEY"),
        "model": env_config_item(model.get("key") or "NODE_BUILDER_LLM_MODEL", "Node Builder LLM Model", required=True, value=model.get("value"), source=model.get("source") or "missing:NODE_BUILDER_LLM_MODEL"),
        "settings_backend": context.get("settings_backend") or runtime_settings_backend(),
        "precedence": ["node-run-overrides", "instance-settings", "runtime-settings", "global-settings", "bridge-env", "runtime-env-file"],
    }


def evaluate_node_builder(sop, data):
    data = data if isinstance(data, dict) else {}
    script = node_builder_script()
    if not script:
        return 503, {"ok": False, "status": "blocked", "detail": "sop-node-builder script is not installed on this Runtime"}
    request_payload = {
        "runtime_id": runtime_info().get("runtime_id") or os.environ.get("SOP_RUNTIME_ID") or "",
        "instance_id": sop.get("instance_id") or sop.get("id") or "",
        "skill_install_command": data.get("skill_install_command") or "",
        "user_instruction": data.get("user_instruction") or data.get("instruction") or "",
        "fetch_metadata": data.get("fetch_metadata", True),
    }
    env, config = node_builder_env(sop, data)
    with tempfile.TemporaryDirectory(prefix="node-builder-") as temp_dir:
        request_path = Path(temp_dir) / "request.json"
        output_path = Path(temp_dir) / "evaluation.json"
        write_json(request_path, request_payload)
        command = ["python3", str(script), "--request-json", str(request_path), "--output-json", str(output_path), "--require-ai"]
        if data.get("allow_deterministic") or data.get("allow_fallback"):
            command.append("--allow-deterministic")
        timeout = int(env.get("NODE_BUILDER_EVALUATOR_TIMEOUT", env.get("NODE_BUILDER_LLM_TIMEOUT", "75")) or "75")
        try:
            completed = subprocess.run(command, text=True, capture_output=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired as exc:
            return 504, {
                "ok": False,
                "mode": "node-builder-agent-evaluation",
                "request": request_payload,
                "config": config,
                "evaluation": {
                    "status": "blocked",
                    "summary": f"Node Builder Agent timed out after {timeout}s.",
                    "node_draft": {},
                    "missing_fields": [],
                    "risks": [{"code": "node_builder_timeout", "message": "The LLM evaluation exceeded runtime timeout budget."}],
                    "assumptions": [],
                    "test_plan": ["Use async mode or a faster model."],
                    "agent": {"provider": "openai-compatible", "model": (config.get("model") or {}).get("value", ""), "used_ai": False},
                },
                "stderr": str(exc)[-4000:],
            }
        evaluation = read_json(output_path) if output_path.is_file() else {}
        if not evaluation:
            try:
                evaluation = json.loads(completed.stdout or "{}")
            except Exception:
                evaluation = {"status": "blocked", "summary": "Node Builder Agent did not return JSON.", "node_draft": {}, "risks": [{"code": "invalid_output", "message": (completed.stderr or completed.stdout or "")[-1000:]}]}
        return 200 if completed.returncode == 0 else 500, {
            "ok": completed.returncode == 0,
            "mode": "node-builder-agent-evaluation",
            "request": request_payload,
            "config": config,
            "evaluation": evaluation,
            "trace": evaluation.get("trace") if isinstance(evaluation, dict) else {},
            "stderr": (completed.stderr or "")[-4000:],
        }


def node_builder_evaluation_dir(sop):
    path = Path(sop["wiki_local_path"]) / "raw" / "node-drafts" / "builder-evaluations"
    path.mkdir(parents=True, exist_ok=True)
    return path


def node_builder_evaluation_path(sop, evaluation_id):
    safe_id = slugify(evaluation_id)
    return node_builder_evaluation_dir(sop) / f"{safe_id}.json"


def write_node_builder_evaluation(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_node_builder_evaluation(sop, evaluation_id):
    path = node_builder_evaluation_path(sop, evaluation_id)
    if not path.is_file():
        return None
    data = read_json(path) or {}
    data.setdefault("evaluation_id", slugify(evaluation_id))
    return data


def run_node_builder_evaluation_job(sop, data, evaluation_id, job_path):
    started_at = datetime.now(timezone.utc).isoformat()
    write_node_builder_evaluation(job_path, {
        "ok": False,
        "status": "running",
        "mode": "node-builder-agent-evaluation-job",
        "evaluation_id": evaluation_id,
        "sop_id": sop.get("id", ""),
        "instance_id": sop.get("instance_id") or sop.get("id") or "",
        "started_at": started_at,
    })
    job_data = dict(data)
    job_data["async_job"] = True
    try:
        http_status, result = evaluate_node_builder(sop, job_data)
        evaluation = result.get("evaluation") if isinstance(result, dict) else {}
        status = "done" if http_status < 500 and bool(result.get("ok")) else "failed"
        write_node_builder_evaluation(job_path, {
            "ok": bool(result.get("ok")),
            "status": status,
            "http_status": http_status,
            "mode": "node-builder-agent-evaluation-job",
            "evaluation_id": evaluation_id,
            "sop_id": sop.get("id", ""),
            "instance_id": sop.get("instance_id") or sop.get("id") or "",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "result": result if isinstance(result, dict) else {},
            "request": result.get("request") if isinstance(result, dict) else {},
            "config": result.get("config") if isinstance(result, dict) else {},
            "evaluation": evaluation if isinstance(evaluation, dict) else {},
            "trace": result.get("trace") if isinstance(result, dict) else {},
            "stderr": result.get("stderr") if isinstance(result, dict) else "",
        })
    except Exception as exc:
        write_node_builder_evaluation(job_path, {
            "ok": False,
            "status": "failed",
            "mode": "node-builder-agent-evaluation-job",
            "evaluation_id": evaluation_id,
            "sop_id": sop.get("id", ""),
            "instance_id": sop.get("instance_id") or sop.get("id") or "",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "error": str(exc),
            "evaluation": {
                "status": "blocked",
                "summary": "Node Builder Agent async evaluation failed.",
                "risks": [{"code": "node_builder_async_failed", "message": str(exc)}],
                "agent": {"used_ai": False},
            },
        })


def start_node_builder_evaluation_job(sop, data):
    command = str(data.get("skill_install_command") or "skill").strip()
    command_hash = hashlib.sha1(command.encode("utf-8", "ignore")).hexdigest()[:8]
    evaluation_id = f"node-builder-{command_hash}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{hashlib.sha1(os.urandom(16)).hexdigest()[:6]}"
    job_path = node_builder_evaluation_path(sop, evaluation_id)
    initial = {
        "ok": False,
        "status": "queued",
        "mode": "node-builder-agent-evaluation-job",
        "evaluation_id": evaluation_id,
        "sop_id": sop.get("id", ""),
        "instance_id": sop.get("instance_id") or sop.get("id") or "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_node_builder_evaluation(job_path, initial)
    thread = threading.Thread(
        target=run_node_builder_evaluation_job,
        args=(dict(sop), dict(data), evaluation_id, job_path),
        daemon=True,
    )
    thread.start()
    instance_id = str(sop.get("id") or sop.get("instance_id") or "")
    return {
        **initial,
        "poll_url": f"/api/sop/{quote(instance_id)}/node-builder/evaluations/{quote(evaluation_id)}",
    }


def node_draft_dir(sop, draft_id):
    safe_id = slugify(draft_id)
    return Path(sop["wiki_local_path"]) / "raw" / "node-drafts" / safe_id


def read_node_draft(sop, draft_id):
    draft_dir = node_draft_dir(sop, draft_id)
    if not draft_dir.exists():
        return None
    return {
        "draft_id": draft_dir.name,
        "draft_type": (read_json(draft_dir / "change_request.json") or {}).get("draft_type", "create_node"),
        "draft_path": str(draft_dir),
        "node": read_yaml(draft_dir / "node.yaml"),
        "request": read_json(draft_dir / "request.json") or {},
        "node_builder_evaluation": read_json(draft_dir / "node-builder-evaluation.json") or {},
        "change_request": read_json(draft_dir / "change_request.json") or {},
        "validation": read_json(draft_dir / "validation.json") or {},
        "draft_test": read_json(draft_dir / "draft-test.json") or {},
        "runtime_publish": read_json(draft_dir / "runtime-publish.json") or {},
        "persistence_plan": read_json(draft_dir / "official-patch.json") or {},
        "trace": read_json(draft_dir / "trace.json") or {},
    }


def test_node_draft(sop, draft_id, data=None):
    data = data if isinstance(data, dict) else {}
    draft = read_node_draft(sop, draft_id)
    if not draft:
        return {"status": "failed", "detail": "Node draft not found", "steps": []}
    node = draft.get("node") if isinstance(draft.get("node"), dict) else {}
    skill = node.get("skill") if isinstance(node.get("skill"), dict) else {}
    executor = node.get("executor") if isinstance(node.get("executor"), dict) else {}
    handoff = node.get("handoff") if isinstance(node.get("handoff"), dict) else {}
    outputs = node.get("outputs") if isinstance(node.get("outputs"), dict) else {}
    steps = []
    def add(step_id, title, ok, detail=""):
        steps.append({"id": step_id, "title": title, "status": "done" if ok else "failed", "detail": detail})
    add("validate-node-yaml", "Validate node-definition/v1", node.get("schema") == "node-definition/v1", "schema must be node-definition/v1")
    add("inspect-install-command", "Inspect skill install command", bool(str(skill.get("install_command") or "").strip()), "install command is required before real install")
    add("hermes-skill-check", "Check Hermes skill target", bool(executor.get("skill") or skill.get("id")), "executor.skill or skill.id is required")
    accepts = handoff.get("accepts") if isinstance(handoff.get("accepts"), dict) else {}
    add("handoff-contract-check", "Check handoff contract", "instruction" in accepts and "upstream_outputs_dir" in accepts, "must accept instruction and upstream_outputs_dir")
    add("manifest-contract-check", "Check outputs manifest contract", bool(outputs.get("manifest") or ((handoff.get("produces") or {}).get("manifest") if isinstance(handoff.get("produces"), dict) else "")), "manifest path is required")
    status = "passed" if all(step["status"] == "done" for step in steps) else "failed"
    result = {
        "status": status,
        "draft_id": draft_id,
        "node_id": node.get("id") or "",
        "mode": "draft-test",
        "executes_install": False,
        "executes_real_node": False,
        "steps": steps,
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "next_step": "publish-runtime" if status == "passed" else "fix draft and rerun Node Builder Agent",
    }
    write_json(node_draft_dir(sop, draft_id) / "draft-test.json", result)
    return result


def publish_node_draft_to_runtime(sop, draft_id, data=None):
    draft = read_node_draft(sop, draft_id)
    if not draft:
        return {"status": "failed", "detail": "Node draft not found"}
    node = draft.get("node") if isinstance(draft.get("node"), dict) else {}
    node_id = str(node.get("id") or node.get("node_id") or "").strip()
    if not node_id:
        return {"status": "failed", "detail": "node.id is required"}
    if node_id in (sop.get("nodes") or {}):
        return {"status": "failed", "detail": f"node_id {node_id} already exists in sop.yaml"}
    target_dir = runtime_node_catalog_dir() / node_id
    target_dir.mkdir(parents=True, exist_ok=True)
    node_to_write = copy.deepcopy(node)
    node_to_write["id"] = node_id
    node_to_write["source"] = "runtime-catalog"
    (target_dir / "node.yaml").write_text(yaml.safe_dump(node_to_write, allow_unicode=True, sort_keys=False), encoding="utf-8")
    _SOP_READ_CACHE.clear()
    visible = node_registry_item(sop, node_id) is not None
    result = {
        "status": "published" if visible else "warning",
        "node_id": node_id,
        "runtime_catalog_path": str(target_dir / "node.yaml"),
        "visible_in_nodes_api": visible,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "message": "Node is published to the current Runtime catalog. Source repo is unchanged.",
    }
    write_json(node_draft_dir(sop, draft_id) / "runtime-publish.json", result)
    return result


def node_draft_persistence_plan(sop, draft_id, data=None):
    draft = read_node_draft(sop, draft_id)
    if not draft:
        return {"status": "failed", "detail": "Node draft not found"}
    node = draft.get("node") if isinstance(draft.get("node"), dict) else {}
    node_id = str(node.get("id") or "").strip()
    skill = node.get("skill") if isinstance(node.get("skill"), dict) else {}
    skill_id = str(skill.get("id") or node_id).strip()
    if not node_id or not skill_id:
        return {"status": "failed", "detail": "node.id and skill.id are required"}
    skill_dir = f"youtube-wiki/skills/{skill_id}"
    node_yaml = yaml.safe_dump(node, allow_unicode=True, sort_keys=False)
    skill_md = "\n".join([
        "---",
        f"name: {skill_id}",
        f"description: Runtime-published SOP node for {node.get('title') or node_id}.",
        "---",
        "",
        f"# {node.get('title') or node_id}",
        "",
        "This node was generated by the Runtime Node Builder Agent.",
        "The source skill install command is stored in `node.yaml`.",
        "",
    ])
    patch = "\n".join([
        f"--- /dev/null",
        f"+++ b/{skill_dir}/node.yaml",
        *[f"+{line}" for line in node_yaml.splitlines()],
        f"--- /dev/null",
        f"+++ b/{skill_dir}/SKILL.md",
        *[f"+{line}" for line in skill_md.splitlines()],
        "",
    ])
    result = {
        "status": "generated",
        "draft_id": draft_id,
        "node_id": node_id,
        "target_repo": "agent-brain-plugins",
        "files": [f"{skill_dir}/node.yaml", f"{skill_dir}/SKILL.md"],
        "patch": patch,
        "instructions": [
            "在开发机 agent-brain-plugins 中应用该 patch。",
            "运行 Python/Shell 校验和相关单元测试。",
            "git commit && git push。",
            "Runtime 机器 git pull 后重启 bridge 或 re-init。",
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(node_draft_dir(sop, draft_id) / "official-patch.json", result)
    return result


def workflow_definition_project_targets(sop, workflow_id):
    plugin_dir = Path(os.environ.get(
        "YOUTUBE_WIKI_PLUGIN_DIR",
        str(Path.home() / "agent-brain-plugins" / "youtube-wiki"),
    )).expanduser()
    return {
        "runtime_sop_file": str(sop.get("sop_file") or ""),
        "project_template_sop_yaml": str(plugin_dir / "templates" / "wiki-repo" / "sop.yaml"),
        "project_workflow_id": workflow_id or workflow_binding(sop).get("workflow_id") or sop.get("id") or "",
        "save_owner": "agent-brain-plugins",
    }


def workflow_edge_draft_schema():
    return {
        "schema_id": WORKFLOW_EDGE_DRAFT_SCHEMA_VERSION,
        "title": "Workflow Edge Definition Draft",
        "description": "保存通过 Edge Handoff Agent 评估的 Edge 变更草稿；不会直接修改生产 sop.yaml。",
        "draft_types": [
            {
                "id": "save_evaluated_edge",
                "title": "Save Evaluated Edge",
                "description": "把 Edge Handoff Instruction、Agent 评估结果和 Node Execution Guide 保存为 repo-first change request。",
            }
        ],
        "required_evaluation": {
            "statuses": ["ready", "trial_ready"],
            "used_ai": True,
            "guide_required": True,
        },
        "safety": {
            "production_dag_changed": False,
            "writes": [
                "raw/workflow-drafts/{draft_id}/edge.yaml",
                "raw/workflow-drafts/{draft_id}/evaluation.json",
                "raw/workflow-drafts/{draft_id}/node_execution_guide.md",
                "raw/workflow-drafts/{draft_id}/change_request.json",
                "raw/workflow-drafts/{draft_id}/validation.json",
            ],
            "publish_enabled": False,
        },
    }


def edge_evaluation_used_ai(evaluation):
    agent = evaluation.get("agent") if isinstance(evaluation.get("agent"), dict) else {}
    return bool(agent.get("used_ai"))


def edge_evaluation_guide_prompt(evaluation):
    guide = evaluation.get("node_execution_guide") if isinstance(evaluation.get("node_execution_guide"), dict) else {}
    return str(guide.get("prompt") or "").strip()


def validate_workflow_edge_draft_input(sop, workflow_id, spec):
    errors = []
    edge = spec.get("edge") if isinstance(spec.get("edge"), dict) else {}
    evaluation = spec.get("evaluation") if isinstance(spec.get("evaluation"), dict) else {}
    upstream = str(spec.get("upstream_node_id") or edge.get("from") or edge.get("source") or "").strip()
    downstream = str(spec.get("downstream_node_id") or edge.get("to") or edge.get("target") or "").strip()
    instruction = str(spec.get("edge_handoff_instruction") or edge.get("instruction") or "").strip()
    nodes = sop.get("nodes") if isinstance(sop.get("nodes"), dict) else {}
    if not upstream:
        errors.append({"field": "upstream_node_id", "code": "required", "message": "upstream_node_id is required"})
    elif upstream not in nodes:
        errors.append({"field": "upstream_node_id", "code": "node_not_found", "message": f"upstream node {upstream!r} does not exist"})
    if not downstream:
        errors.append({"field": "downstream_node_id", "code": "required", "message": "downstream_node_id is required"})
    elif downstream not in nodes:
        errors.append({"field": "downstream_node_id", "code": "node_not_found", "message": f"downstream node {downstream!r} does not exist"})
    if upstream and downstream and upstream == downstream:
        errors.append({"field": "downstream_node_id", "code": "self_edge", "message": "upstream and downstream must be different nodes"})
    if not instruction:
        errors.append({"field": "edge_handoff_instruction", "code": "required", "message": "Edge Handoff Instruction is required before saving"})
    status = str(evaluation.get("status") or "").strip()
    if status not in {"ready", "trial_ready"}:
        errors.append({
            "field": "evaluation.status",
            "code": "not_approved",
            "message": "Only ready/trial_ready Edge Handoff Agent evaluations can be saved",
        })
    if not edge_evaluation_used_ai(evaluation):
        errors.append({
            "field": "evaluation.agent.used_ai",
            "code": "ai_required",
            "message": "Saved Edge drafts require a real AI Edge Handoff Agent evaluation",
        })
    if not edge_evaluation_guide_prompt(evaluation):
        errors.append({
            "field": "evaluation.node_execution_guide.prompt",
            "code": "guide_required",
            "message": "Node Execution Guide is required before saving this Edge",
        })
    return {
        "schema_id": WORKFLOW_EDGE_DRAFT_SCHEMA_VERSION,
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "missing_fields": [error["field"] for error in errors if error["code"] == "required"],
        "production_dag_changed": False,
        "workflow_id": workflow_id or workflow_binding(sop).get("workflow_id") or sop.get("id") or "",
    }


def proposed_workflow_edge_from_draft(sop, workflow_id, spec):
    edge = spec.get("edge") if isinstance(spec.get("edge"), dict) else {}
    evaluation = spec.get("evaluation") if isinstance(spec.get("evaluation"), dict) else {}
    upstream = str(spec.get("upstream_node_id") or edge.get("from") or edge.get("source") or "").strip()
    downstream = str(spec.get("downstream_node_id") or edge.get("to") or edge.get("target") or "").strip()
    edge_id = slugify(spec.get("edge_id") or edge.get("id") or f"{upstream}-to-{downstream}")
    instruction = str(spec.get("edge_handoff_instruction") or edge.get("instruction") or "").strip()
    relay_mode = str(spec.get("relay_mode") or edge.get("relayMode") or "auto_by_target_inputs")
    relay_mappings = spec.get("relay_mappings")
    if not isinstance(relay_mappings, list):
        relay_mappings = edge.get("relayMappings") if isinstance(edge.get("relayMappings"), list) else []
    relay_mappings = normalize_relay_mappings(relay_mappings)
    if relay_mode == "auto_by_target_inputs":
        relay_mappings = []
    if relay_mode != "auto_by_target_inputs" and not relay_mappings:
        relay_mappings = workflow_edge_evaluation_relay_mappings(spec)
    guide = evaluation.get("node_execution_guide") if isinstance(evaluation.get("node_execution_guide"), dict) else {}
    return {
        "id": edge_id,
        "from": upstream,
        "to": downstream,
        "relay": {
            "mode": relay_mode,
            "mappings": relay_mappings,
            "handoff_instruction": instruction,
            "node_execution_guide": {
                "format": guide.get("format") or "markdown",
                "prompt": edge_evaluation_guide_prompt(evaluation),
            },
            "evaluation": {
                "status": evaluation.get("status"),
                "score": evaluation.get("score"),
                "confidence": evaluation.get("confidence"),
                "summary": evaluation.get("summary"),
                "decision": evaluation.get("decision"),
                "evaluated_at": evaluation.get("evaluated_at"),
                "agent": mask_data(evaluation.get("agent") if isinstance(evaluation.get("agent"), dict) else {}),
            },
            "resolved_handoff": evaluation.get("resolved_handoff") if isinstance(evaluation.get("resolved_handoff"), dict) else {},
            "test_plan": evaluation.get("test_plan") if isinstance(evaluation.get("test_plan"), list) else [],
        },
    }


def create_workflow_edge_draft(sop, workflow_id, spec):
    validation = validate_workflow_edge_draft_input(sop, workflow_id, spec)
    if validation["errors"]:
        return {
            "draft_id": "",
            "draft_type": "save_evaluated_edge",
            "draft_path": "",
            "edge": {},
            "change_request": {},
            "validation": validation,
        }
    wiki = Path(sop["wiki_local_path"])
    proposed = proposed_workflow_edge_from_draft(sop, workflow_id, spec)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    draft_id = f"{proposed['id']}-edge-{timestamp}"
    draft_dir = wiki / "raw" / "workflow-drafts" / draft_id
    draft_dir.mkdir(parents=True, exist_ok=True)
    evaluation = spec.get("evaluation") if isinstance(spec.get("evaluation"), dict) else {}
    (draft_dir / "edge.yaml").write_text(yaml.safe_dump(proposed, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (draft_dir / "evaluation.json").write_text(json.dumps(mask_data(evaluation), ensure_ascii=False, indent=2), encoding="utf-8")
    (draft_dir / "node_execution_guide.md").write_text(edge_evaluation_guide_prompt(evaluation) + "\n", encoding="utf-8")
    targets = workflow_definition_project_targets(sop, workflow_id)
    change_request = {
        "version": 1,
        "draft_type": "save_evaluated_edge",
        "draft_id": draft_id,
        "workflow_id": validation["workflow_id"],
        "edge_id": proposed["id"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "save_target": "agent-brain-plugins",
        "targets": targets,
        "change_summary": [
            {
                "field": f"edges.{proposed['id']}",
                "before": "missing_or_unpublished",
                "after": mask_data(proposed),
            }
        ],
        "proposed": mask_data(proposed),
        "evaluation_summary": {
            "status": evaluation.get("status"),
            "score": evaluation.get("score"),
            "confidence": evaluation.get("confidence"),
            "summary": evaluation.get("summary"),
            "used_ai": edge_evaluation_used_ai(evaluation),
        },
        "notes": [
            "This draft does not modify runtime sop.yaml.",
            "Apply/publish must be handled by a repo-first change in agent-brain-plugins.",
            "The Node Execution Guide must be carried into downstream Hermes skill execution in the runtime execution phase.",
        ],
    }
    (draft_dir / "change_request.json").write_text(json.dumps(change_request, ensure_ascii=False, indent=2), encoding="utf-8")
    validation = {**validation, "targets": targets, "change_count": 1}
    (draft_dir / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "draft_id": draft_id,
        "draft_type": "save_evaluated_edge",
        "draft_path": str(draft_dir),
        "edge": proposed,
        "change_request": change_request,
        "validation": validation,
    }


def apply_workflow_edge_draft(sop, workflow_id, data):
    workflow_id = workflow_id or workflow_binding(sop).get("workflow_id") or sop.get("id") or ""
    draft_id = str((data or {}).get("draft_id") or "").strip()
    draft_path = str((data or {}).get("draft_path") or "").strip()
    if not draft_id and not draft_path:
        return {
            "status": "failed",
            "reason": "draft_id or draft_path is required",
            "errors": [
                {"code": "missing_draft", "message": "draft_id or draft_path is required"},
            ],
            "edge": {},
            "targets": {},
        }

    wiki = Path(sop["wiki_local_path"])
    candidates = []
    if draft_path:
        candidates.append(draft_path)
    if draft_id:
        candidates.append(str(wiki / "raw" / "workflow-drafts" / draft_id))
    resolved = None
    for raw_path in candidates:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = (wiki / path).resolve()
        if path.exists() and path.is_dir():
            resolved = str(path)
            break
    if not resolved:
        return {
            "status": "failed",
            "reason": "workflow edge draft not found",
            "errors": [{"code": "draft_not_found", "message": "workflow edge draft path is missing"}],
            "edge": {},
            "targets": {},
        }

    resolved_path = Path(resolved)
    draft = read_json(resolved_path / "change_request.json") or {}
    draft_workflow_id = str(draft.get("workflow_id") or "").strip()
    if draft_workflow_id and draft_workflow_id != workflow_id:
        return {
            "status": "failed",
            "reason": "workflow id mismatch",
            "errors": [{"code": "workflow_mismatch", "message": f"draft workflow {draft_workflow_id!r} does not match {workflow_id!r}"}],
            "edge": {},
            "targets": workflow_definition_project_targets(sop, workflow_id),
        }

    proposed = read_yaml(resolved_path / "edge.yaml")
    if not isinstance(proposed, dict):
        return {
            "status": "failed",
            "reason": "invalid draft payload",
            "errors": [{"code": "invalid_edge_draft", "message": "edge.yaml is missing or malformed"}],
            "edge": {},
            "targets": workflow_definition_project_targets(sop, workflow_id),
        }
    runtime_sop_path = resolved_path / "runtime_sop.yaml"
    runtime_sop_result_path = resolved_path / "runtime_sop_result.json"
    if not runtime_sop_path.exists() or not runtime_sop_result_path.exists():
        return {
            "status": "failed",
            "reason": "runtime SOP is required before formal SOP patch generation",
            "errors": [{
                "code": "runtime_sop_required",
                "message": "Generate Runtime SOP for this Edge draft before generating the formal repo-first SOP patch",
            }],
            "edge": proposed,
            "targets": workflow_definition_project_targets(sop, workflow_id),
        }
    source = str(proposed.get("from") or "").strip()
    target = str(proposed.get("to") or "").strip()
    edge_id = str(proposed.get("id") or f"{source}-to-{target}").strip()
    if not source or not target:
        return {
            "status": "failed",
            "reason": "invalid edge",
            "errors": [{"code": "invalid_edge", "message": "edge.from and edge.to are required"}],
            "edge": proposed,
            "targets": workflow_definition_project_targets(sop, workflow_id),
        }

    if source == target:
        return {
            "status": "failed",
            "reason": "invalid edge",
            "errors": [{"code": "self_edge", "message": "upstream and downstream must be different"}],
            "edge": proposed,
            "targets": workflow_definition_project_targets(sop, workflow_id),
        }

    targets = workflow_definition_project_targets(sop, workflow_id)
    template_path = Path(targets["project_template_sop_yaml"])
    if not template_path.exists():
        return {
            "status": "failed",
            "reason": "project template missing",
            "errors": [{"code": "template_missing", "message": "agent-brain-plugins template SOP is missing"}],
            "edge": proposed,
            "targets": targets,
        }

    original_text = template_path.read_text(encoding="utf-8")
    template_doc = read_yaml(template_path)
    if not isinstance(template_doc, dict):
        template_doc = {}

    nodes = template_doc.get("nodes") if isinstance(template_doc.get("nodes"), dict) else {}
    if source not in nodes:
        return {
            "status": "failed",
            "reason": "edge source node not found in template",
            "errors": [{"code": "source_node_not_found", "message": f"source node {source!r} not found in template"}],
            "edge": proposed,
            "targets": targets,
        }
    if target not in nodes:
        return {
            "status": "failed",
            "reason": "edge target node not found in template",
            "errors": [{"code": "target_node_not_found", "message": f"target node {target!r} not found in template"}],
            "edge": proposed,
            "targets": targets,
        }

    edges = template_doc.get("edges") if isinstance(template_doc.get("edges"), list) else []
    if not isinstance(edges, list):
        edges = []
    existing = next((i for i, item in enumerate(edges) if isinstance(item, dict) and str(item.get("id") or "").strip() == edge_id), None)
    replacement = copy.deepcopy(proposed)
    replacement.setdefault("id", edge_id)
    replacement["from"] = source
    replacement["to"] = target

    if existing is None:
        existing = next((
            i for i, item in enumerate(edges)
            if isinstance(item, dict) and str(item.get("from") or "").strip() == source and str(item.get("to") or "").strip() == target
        ), None)
    before_edges = copy.deepcopy(edges)
    before_snapshot = json.dumps(before_edges, ensure_ascii=False, sort_keys=True)
    if existing is None:
        edges.append(replacement)
        status = "patch_ready"
    else:
        if edges[existing] == replacement:
            status = "unchanged"
        else:
            edges[existing] = replacement
            status = "patch_ready"
    template_doc["edges"] = edges
    after_edges = copy.deepcopy(edges)
    after_snapshot = json.dumps(edges, ensure_ascii=False, sort_keys=True)

    candidate_text = yaml.safe_dump(template_doc, allow_unicode=True, sort_keys=False)
    patch_text = "" if status == "unchanged" else "".join(difflib.unified_diff(
        original_text.splitlines(keepends=True),
        candidate_text.splitlines(keepends=True),
        fromfile=str(template_path),
        tofile=f"{template_path} (candidate)",
    ))
    candidate_path = resolved_path / "candidate_sop.yaml"
    patch_path = resolved_path / "template_patch.diff"
    result_path = resolved_path / "apply_result.json"
    if status == "patch_ready":
        candidate_path.write_text(candidate_text, encoding="utf-8")
        patch_path.write_text(patch_text, encoding="utf-8")

    result = {
        "status": status,
        "workflow_id": workflow_id,
        "draft_id": draft_id or resolved_path.name,
        "draft_path": str(resolved_path),
        "targets": targets,
        "edge": replacement,
        "before": before_edges,
        "after": after_edges,
        "hash_before": hashlib.sha256(before_snapshot.encode("utf-8")).hexdigest()[:16] if before_snapshot else "",
        "hash_after": hashlib.sha256(after_snapshot.encode("utf-8")).hexdigest()[:16] if after_snapshot else "",
        "change_count": 0 if status == "unchanged" else 1,
        "production_dag_changed": False,
        "repo_first_required": True,
        "candidate_sop_path": str(candidate_path) if status == "patch_ready" else "",
        "patch_path": str(patch_path) if status == "patch_ready" else "",
        "patch": patch_text,
        "message": (
            "repo-first patch prepared; apply this diff in the development checkout, commit, push, then git pull on runtime machines"
            if status == "patch_ready" else
            "edge already matches the project template; no source change is required"
        ),
    }
    result_path.write_text(json.dumps(mask_data(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def generate_workflow_edge_runtime_sop(sop, workflow_id, data):
    workflow_id = workflow_id or workflow_binding(sop).get("workflow_id") or sop.get("id") or ""
    draft_id = str((data or {}).get("draft_id") or "").strip()
    draft_path = str((data or {}).get("draft_path") or "").strip()
    if not draft_id and not draft_path:
        return {
            "status": "failed",
            "reason": "draft_id or draft_path is required",
            "errors": [{"code": "missing_draft", "message": "draft_id or draft_path is required"}],
            "edge": {},
            "targets": workflow_definition_project_targets(sop, workflow_id),
        }

    wiki = Path(sop["wiki_local_path"])
    candidates = []
    if draft_path:
        candidates.append(draft_path)
    if draft_id:
        candidates.append(str(wiki / "raw" / "workflow-drafts" / draft_id))
    resolved = None
    for raw_path in candidates:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = (wiki / path).resolve()
        if path.exists() and path.is_dir():
            resolved = str(path)
            break
    if not resolved:
        return {
            "status": "failed",
            "reason": "workflow edge draft not found",
            "errors": [{"code": "draft_not_found", "message": "workflow edge draft path is missing"}],
            "edge": {},
            "targets": workflow_definition_project_targets(sop, workflow_id),
        }

    resolved_path = Path(resolved)
    draft = read_json(resolved_path / "change_request.json") or {}
    draft_workflow_id = str(draft.get("workflow_id") or "").strip()
    if draft_workflow_id and draft_workflow_id != workflow_id:
        return {
            "status": "failed",
            "reason": "workflow id mismatch",
            "errors": [{"code": "workflow_mismatch", "message": f"draft workflow {draft_workflow_id!r} does not match {workflow_id!r}"}],
            "edge": {},
            "targets": workflow_definition_project_targets(sop, workflow_id),
        }

    proposed = read_yaml(resolved_path / "edge.yaml")
    if not isinstance(proposed, dict):
        return {
            "status": "failed",
            "reason": "invalid draft payload",
            "errors": [{"code": "invalid_edge_draft", "message": "edge.yaml is missing or malformed"}],
            "edge": {},
            "targets": workflow_definition_project_targets(sop, workflow_id),
        }
    source = str(proposed.get("from") or "").strip()
    target = str(proposed.get("to") or "").strip()
    edge_id = str(proposed.get("id") or f"{source}-to-{target}").strip()
    runtime_sop_file = Path(str(sop.get("sop_file") or "")).expanduser()
    if not runtime_sop_file.exists():
        return {
            "status": "failed",
            "reason": "runtime sop missing",
            "errors": [{"code": "runtime_sop_missing", "message": "runtime sop.yaml is missing for this instance"}],
            "edge": proposed,
            "targets": workflow_definition_project_targets(sop, workflow_id),
        }

    original_text = runtime_sop_file.read_text(encoding="utf-8")
    runtime_doc = read_yaml(runtime_sop_file)
    if not isinstance(runtime_doc, dict):
        runtime_doc = {}
    nodes = runtime_doc.get("nodes") if isinstance(runtime_doc.get("nodes"), dict) else {}
    if source not in nodes:
        return {
            "status": "failed",
            "reason": "edge source node not found in runtime sop",
            "errors": [{"code": "source_node_not_found", "message": f"source node {source!r} not found in runtime sop"}],
            "edge": proposed,
            "targets": workflow_definition_project_targets(sop, workflow_id),
        }
    if target not in nodes:
        return {
            "status": "failed",
            "reason": "edge target node not found in runtime sop",
            "errors": [{"code": "target_node_not_found", "message": f"target node {target!r} not found in runtime sop"}],
            "edge": proposed,
            "targets": workflow_definition_project_targets(sop, workflow_id),
        }

    edges = runtime_doc.get("edges") if isinstance(runtime_doc.get("edges"), list) else []
    before_edges = copy.deepcopy(edges)
    existing = next((i for i, item in enumerate(edges) if isinstance(item, dict) and str(item.get("id") or "").strip() == edge_id), None)
    replacement = copy.deepcopy(proposed)
    replacement.setdefault("id", edge_id)
    replacement["from"] = source
    replacement["to"] = target
    if existing is None:
        existing = next((
            i for i, item in enumerate(edges)
            if isinstance(item, dict) and str(item.get("from") or "").strip() == source and str(item.get("to") or "").strip() == target
        ), None)
    if existing is None:
        edges.append(replacement)
    else:
        edges[existing] = replacement
    runtime_doc["edges"] = edges
    runtime_doc["wiki_local_path"] = sop.get("wiki_local_path", "")
    if sop.get("repo"):
        runtime_doc["repo"] = sop.get("repo", "")
    after_edges = copy.deepcopy(edges)
    candidate_text = yaml.safe_dump(runtime_doc, allow_unicode=True, sort_keys=False)
    runtime_sop_path = resolved_path / "runtime_sop.yaml"
    manifest_path = resolved_path / "runtime_sop_result.json"
    runtime_sop_path.write_text(candidate_text, encoding="utf-8")
    before_snapshot = json.dumps(before_edges, ensure_ascii=False, sort_keys=True)
    after_snapshot = json.dumps(after_edges, ensure_ascii=False, sort_keys=True)
    result = {
        "status": "runtime_sop_ready",
        "workflow_id": workflow_id,
        "draft_id": draft_id or resolved_path.name,
        "draft_path": str(resolved_path),
        "runtime_sop_path": str(runtime_sop_path),
        "runtime_sop_source": str(runtime_sop_file),
        "targets": workflow_definition_project_targets(sop, workflow_id),
        "edge": replacement,
        "before": before_edges,
        "after": after_edges,
        "hash_before": hashlib.sha256(before_snapshot.encode("utf-8")).hexdigest()[:16] if before_snapshot else "",
        "hash_after": hashlib.sha256(after_snapshot.encode("utf-8")).hexdigest()[:16] if after_snapshot else "",
        "change_count": 0 if before_edges == after_edges else 1,
        "active_runtime_sop_changed": False,
        "production_dag_changed": False,
        "repo_first_required": False,
        "message": "runtime SOP snapshot prepared for this draft; active sop.yaml is unchanged",
    }
    manifest_path.write_text(json.dumps(mask_data(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def resolve_workflow_draft_dir(sop, draft_id="", draft_path=""):
    wiki = Path(sop["wiki_local_path"]).expanduser().resolve()
    candidates = []
    if draft_path:
        candidates.append(str(draft_path))
    if draft_id:
        candidates.append(str(wiki / "raw" / "workflow-drafts" / str(draft_id)))
    for raw_path in candidates:
        path = Path(str(raw_path)).expanduser()
        if not path.is_absolute():
            path = (wiki / path).resolve()
        else:
            path = path.resolve()
        try:
            path.relative_to(wiki)
        except ValueError:
            continue
        if path.exists() and path.is_dir():
            return path
    return None


def workflow_draft_edge_specs(sop, workflow_id, data):
    data = data if isinstance(data, dict) else {}
    draft = data.get("draft") if isinstance(data.get("draft"), dict) else data
    evaluations = data.get("evaluations") if isinstance(data.get("evaluations"), dict) else {}
    edges = draft.get("edges") if isinstance(draft.get("edges"), list) else []
    specs = []
    for raw_edge in edges:
        if not isinstance(raw_edge, dict):
            continue
        edge_id = str(raw_edge.get("id") or "").strip()
        evaluation = raw_edge.get("evaluation") if isinstance(raw_edge.get("evaluation"), dict) else {}
        if edge_id and isinstance(evaluations.get(edge_id), dict):
            evaluation = evaluations[edge_id]
        spec = {
            "edge_id": edge_id,
            "edge": raw_edge,
            "upstream_node_id": raw_edge.get("from") or raw_edge.get("source"),
            "downstream_node_id": raw_edge.get("to") or raw_edge.get("target"),
            "edge_handoff_instruction": raw_edge.get("instruction") or raw_edge.get("edge_handoff_instruction") or "",
            "relay_mode": raw_edge.get("relayMode") or raw_edge.get("relay_mode") or "auto_by_target_inputs",
            "relay_mappings": raw_edge.get("relayMappings") if isinstance(raw_edge.get("relayMappings"), list) else raw_edge.get("relay_mappings"),
            "evaluation": evaluation,
        }
        validation = validate_workflow_edge_draft_input(sop, workflow_id, spec)
        proposal = proposed_workflow_edge_from_draft(sop, workflow_id, spec)
        specs.append({
            "edge_id": proposal.get("id") or edge_id,
            "source": proposal.get("from", ""),
            "target": proposal.get("to", ""),
            "spec": spec,
            "proposal": proposal,
            "validation": validation,
        })
    return specs


def validate_workflow_draft_input(sop, workflow_id, data):
    data = data if isinstance(data, dict) else {}
    draft = data.get("draft") if isinstance(data.get("draft"), dict) else data
    nodes = draft.get("nodes") if isinstance(draft.get("nodes"), list) else []
    edges = draft.get("edges") if isinstance(draft.get("edges"), list) else []
    known_nodes = sop.get("nodes") if isinstance(sop.get("nodes"), dict) else {}
    errors = []
    warnings = []
    seen_nodes = set()
    for index, item in enumerate(nodes):
        node_id = str((item or {}).get("nodeId") or (item or {}).get("node_id") or "").strip() if isinstance(item, dict) else ""
        if not node_id:
            errors.append({"field": f"nodes[{index}].nodeId", "code": "required", "message": "nodeId is required"})
            continue
        seen_nodes.add(node_id)
        if node_id not in known_nodes:
            errors.append({"field": f"nodes[{index}].nodeId", "code": "node_not_found", "message": f"node {node_id!r} does not exist"})
    edge_specs = workflow_draft_edge_specs(sop, workflow_id, data)
    if edges and not edge_specs:
        errors.append({"field": "edges", "code": "invalid_edges", "message": "No valid edge payloads were found"})
    for edge in edge_specs:
        validation_errors = (edge.get("validation") or {}).get("errors") or []
        if validation_errors:
            warnings.append({
                "field": f"edges.{edge.get('edge_id')}",
                "code": "edge_not_ready",
                "message": "Edge requires a ready AI evaluation and Node Execution Guide before Runtime SOP generation",
                "errors": validation_errors,
            })
        if seen_nodes and (edge.get("source") not in seen_nodes or edge.get("target") not in seen_nodes):
            warnings.append({
                "field": f"edges.{edge.get('edge_id')}",
                "code": "edge_node_outside_draft",
                "message": "Edge references a node that is not listed in draft.nodes",
            })
    status = "failed" if errors else "warning" if warnings else "passed"
    return {
        "schema_id": "workflow-draft-schema/v1",
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "workflow_id": workflow_id or workflow_binding(sop).get("workflow_id") or sop.get("id") or "",
        "node_count": len(nodes),
        "edge_count": len(edges),
        "ready_edge_count": sum(1 for edge in edge_specs if not (edge.get("validation") or {}).get("errors")),
    }


def create_workflow_draft(sop, workflow_id, data):
    workflow_id = workflow_id or workflow_binding(sop).get("workflow_id") or sop.get("id") or ""
    data = data if isinstance(data, dict) else {}
    draft = data.get("draft") if isinstance(data.get("draft"), dict) else data
    validation = validate_workflow_draft_input(sop, workflow_id, data)
    name = str(draft.get("name") or "workflow-draft").strip()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    draft_id = slugify(data.get("draft_id") or draft.get("draft_id") or f"{name}-{timestamp}") or f"workflow-draft-{timestamp}"
    wiki = Path(sop["wiki_local_path"])
    draft_dir = wiki / "raw" / "workflow-drafts" / draft_id
    draft_dir.mkdir(parents=True, exist_ok=True)
    edge_specs = workflow_draft_edge_specs(sop, workflow_id, data)
    ready_edges = [edge["proposal"] for edge in edge_specs if not (edge.get("validation") or {}).get("errors")]
    manifest = {
        "version": 1,
        "draft_type": "workflow_draft",
        "draft_id": draft_id,
        "workflow_id": workflow_id,
        "name": name,
        "goal": str(draft.get("goal") or ""),
        "created_at": _now_iso_utc(),
        "runtime_id": sop.get("runtime_id", ""),
        "instance_id": sop.get("instance_id") or sop.get("id", ""),
        "draft": mask_data(draft),
        "edge_count": len(edge_specs),
        "ready_edge_count": len(ready_edges),
        "runtime_sop_status": "not_generated",
        "active_runtime_sop_changed": False,
        "production_dag_changed": False,
    }
    write_json(draft_dir / "workflow_draft.json", manifest)
    (draft_dir / "edges.yaml").write_text(yaml.safe_dump(ready_edges, allow_unicode=True, sort_keys=False), encoding="utf-8")
    write_json(draft_dir / "edge_validations.json", [mask_data({
        "edge_id": edge.get("edge_id"),
        "source": edge.get("source"),
        "target": edge.get("target"),
        "validation": edge.get("validation"),
    }) for edge in edge_specs])
    write_json(draft_dir / "validation.json", validation)
    return {
        "status": "draft_saved",
        "draft_id": draft_id,
        "draft_type": "workflow_draft",
        "draft_path": str(draft_dir),
        "workflow_id": workflow_id,
        "validation": validation,
        "edge_count": len(edge_specs),
        "ready_edge_count": len(ready_edges),
        "runtime_sop_result": read_json(draft_dir / "runtime_sop_result.json") or {},
        "message": (
            "workflow draft saved; generate Runtime SOP before running"
            if validation["status"] != "failed" else
            "workflow draft saved with validation errors"
        ),
    }


def generate_workflow_draft_runtime_sop(sop, workflow_id, data):
    workflow_id = workflow_id or workflow_binding(sop).get("workflow_id") or sop.get("id") or ""
    data = data if isinstance(data, dict) else {}
    draft_id = str(data.get("draft_id") or data.get("draftId") or "").strip()
    draft_path = str(data.get("draft_path") or data.get("draftPath") or "").strip()
    draft_dir = resolve_workflow_draft_dir(sop, draft_id, draft_path)
    if not draft_dir:
        return {
            "status": "failed",
            "reason": "workflow draft not found",
            "errors": [{"code": "draft_not_found", "message": "workflow draft path is missing"}],
            "workflow_id": workflow_id,
        }
    validation = read_json(draft_dir / "validation.json") or {}
    if validation.get("status") != "passed":
        return {
            "status": "failed",
            "reason": "workflow draft is not ready",
            "errors": [{
                "code": "edge_evaluation_required",
                "message": "All edges must have ready AI Edge Handoff evaluations before Runtime SOP generation",
            }],
            "validation": validation,
            "workflow_id": workflow_id,
            "draft_id": draft_dir.name,
            "draft_path": str(draft_dir),
        }
    proposed_edges = read_yaml(draft_dir / "edges.yaml")
    if not isinstance(proposed_edges, list) or not proposed_edges:
        return {
            "status": "failed",
            "reason": "workflow draft has no ready edges",
            "errors": [{"code": "no_ready_edges", "message": "No ready Edge definitions were found for this workflow draft"}],
            "workflow_id": workflow_id,
            "draft_id": draft_dir.name,
            "draft_path": str(draft_dir),
        }
    runtime_sop_file = Path(str(sop.get("sop_file") or "")).expanduser()
    if not runtime_sop_file.exists():
        return {
            "status": "failed",
            "reason": "runtime sop missing",
            "errors": [{"code": "runtime_sop_missing", "message": "runtime sop.yaml is missing for this instance"}],
            "workflow_id": workflow_id,
            "draft_id": draft_dir.name,
            "draft_path": str(draft_dir),
        }
    original_text = runtime_sop_file.read_text(encoding="utf-8")
    runtime_doc = read_yaml(runtime_sop_file)
    if not isinstance(runtime_doc, dict):
        runtime_doc = {}
    nodes = runtime_doc.get("nodes") if isinstance(runtime_doc.get("nodes"), dict) else {}
    errors = []
    edges = runtime_doc.get("edges") if isinstance(runtime_doc.get("edges"), list) else []
    before_edges = copy.deepcopy(edges)
    change_count = 0
    for proposed in proposed_edges:
        if not isinstance(proposed, dict):
            continue
        source = str(proposed.get("from") or "").strip()
        target = str(proposed.get("to") or "").strip()
        edge_id = str(proposed.get("id") or f"{source}-to-{target}").strip()
        if source not in nodes:
            errors.append({"code": "source_node_not_found", "message": f"source node {source!r} not found in runtime sop", "edge_id": edge_id})
            continue
        if target not in nodes:
            errors.append({"code": "target_node_not_found", "message": f"target node {target!r} not found in runtime sop", "edge_id": edge_id})
            continue
        replacement = copy.deepcopy(proposed)
        replacement["id"] = edge_id
        replacement["from"] = source
        replacement["to"] = target
        existing = next((i for i, item in enumerate(edges) if isinstance(item, dict) and str(item.get("id") or "").strip() == edge_id), None)
        if existing is None:
            existing = next((
                i for i, item in enumerate(edges)
                if isinstance(item, dict) and str(item.get("from") or "").strip() == source and str(item.get("to") or "").strip() == target
            ), None)
        if existing is None:
            edges.append(replacement)
            change_count += 1
        elif edges[existing] != replacement:
            edges[existing] = replacement
            change_count += 1
    if errors:
        return {
            "status": "failed",
            "reason": "workflow draft edges do not match runtime sop nodes",
            "errors": errors,
            "workflow_id": workflow_id,
            "draft_id": draft_dir.name,
            "draft_path": str(draft_dir),
        }
    runtime_doc["edges"] = edges
    runtime_doc["wiki_local_path"] = sop.get("wiki_local_path", "")
    if sop.get("repo"):
        runtime_doc["repo"] = sop.get("repo", "")
    runtime_sop_path = draft_dir / "runtime_sop.yaml"
    manifest_path = draft_dir / "runtime_sop_result.json"
    runtime_sop_path.write_text(yaml.safe_dump(runtime_doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    before_snapshot = json.dumps(before_edges, ensure_ascii=False, sort_keys=True)
    after_snapshot = json.dumps(edges, ensure_ascii=False, sort_keys=True)
    result = {
        "status": "runtime_sop_ready",
        "workflow_id": workflow_id,
        "draft_id": draft_dir.name,
        "draft_path": str(draft_dir),
        "runtime_sop_path": str(runtime_sop_path),
        "runtime_sop_source": str(runtime_sop_file),
        "edge_count": len(proposed_edges),
        "change_count": change_count,
        "hash_before": hashlib.sha256(before_snapshot.encode("utf-8")).hexdigest()[:16] if before_snapshot else "",
        "hash_after": hashlib.sha256(after_snapshot.encode("utf-8")).hexdigest()[:16] if after_snapshot else "",
        "active_runtime_sop_changed": False,
        "production_dag_changed": False,
        "repo_first_required": False,
        "message": "workflow draft Runtime SOP snapshot prepared; active sop.yaml is unchanged",
    }
    manifest_path.write_text(json.dumps(mask_data(result), ensure_ascii=False, indent=2), encoding="utf-8")
    draft_manifest = read_json(draft_dir / "workflow_draft.json") or {}
    draft_manifest["runtime_sop_status"] = "runtime_sop_ready"
    draft_manifest["runtime_sop_path"] = str(runtime_sop_path)
    draft_manifest["updated_at"] = _now_iso_utc()
    draft_manifest["hash_after"] = result["hash_after"]
    write_json(draft_dir / "workflow_draft.json", draft_manifest)
    return result


def load_workflow_runtime_sop_snapshot(sop, data):
    data = data if isinstance(data, dict) else {}
    simulation_target = str(data.get("simulation_target") or data.get("simulationTarget") or "").strip()
    raw_path = str(data.get("runtime_sop_path") or data.get("runtimeSopPath") or "").strip()
    if simulation_target != "runtime-sop" and not raw_path:
        return sop, "", None
    if not raw_path:
        return None, "", {
            "ok": False,
            "status": "blocked",
            "detail": "runtime_sop_path is required when simulation_target is runtime-sop",
            "errors": [{"code": "runtime_sop_path_required", "message": "runtime_sop_path is required"}],
        }
    wiki = Path(sop["wiki_local_path"]).expanduser().resolve()
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (wiki / path).resolve()
    else:
        path = path.resolve()
    try:
        path.relative_to(wiki)
    except ValueError:
        return None, str(path), {
            "ok": False,
            "status": "blocked",
            "detail": "runtime_sop_path must stay inside the instance workspace",
            "errors": [{"code": "runtime_sop_path_outside_workspace", "message": "runtime_sop_path must stay inside the instance workspace"}],
        }
    if not path.exists():
        return None, str(path), {
            "ok": False,
            "status": "blocked",
            "detail": "runtime SOP snapshot is missing",
            "errors": [{"code": "runtime_sop_snapshot_missing", "message": "runtime_sop.yaml was not found for this Edge draft"}],
        }
    doc = read_yaml(path)
    if not isinstance(doc, dict):
        return None, str(path), {
            "ok": False,
            "status": "blocked",
            "detail": "runtime SOP snapshot is malformed",
            "errors": [{"code": "runtime_sop_snapshot_invalid", "message": "runtime_sop.yaml is malformed"}],
        }
    overlay = copy.deepcopy(sop)
    for key in ("nodes", "edges", "pipeline", "raw_id", "sop_type", "workflow_title", "name", "version"):
        if key in doc:
            overlay[key] = doc[key]
    overlay["runtime_sop_snapshot_path"] = str(path)
    return overlay, str(path), None


def patch_run_workflow_draft_metadata(sop, pipeline_id, metadata):
    if not pipeline_id:
        return {"patched": []}
    wiki = Path(sop["wiki_local_path"])
    patched = []
    targets = [
        wiki / "raw" / "pipeline-context.json",
        wiki / "raw" / "pipeline-runs" / pipeline_id / "context.json",
        wiki / "raw" / "pipeline-runs" / pipeline_id / "run.json",
    ]
    for path in targets:
        data = read_json(path)
        if not isinstance(data, dict):
            continue
        if path.name == "run.json":
            snapshot = data.get("workflow_snapshot") if isinstance(data.get("workflow_snapshot"), dict) else {}
            data["workflow_snapshot"] = {**snapshot, **metadata}
            data["workflow_draft_id"] = metadata.get("workflow_draft_id", "")
            data["run_source"] = "workflow-draft"
        else:
            current = data.get("workflow_draft") if isinstance(data.get("workflow_draft"), dict) else {}
            data["workflow_draft"] = {**current, **metadata}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            patched.append(str(path.relative_to(wiki)))
        except OSError:
            continue
    return {"patched": patched, **metadata}


def trigger_workflow_draft_run(sop, workflow_id, data):
    workflow_id = workflow_id or workflow_binding(sop).get("workflow_id") or sop.get("id") or ""
    data = data if isinstance(data, dict) else {}
    draft_id = str(data.get("draft_id") or data.get("draftId") or "").strip()
    draft_path = str(data.get("draft_path") or data.get("draftPath") or "").strip()
    draft_dir = resolve_workflow_draft_dir(sop, draft_id, draft_path)
    if not draft_dir:
        return 404, {
            "status": "error",
            "message": "workflow draft not found",
            "errors": [{"code": "draft_not_found", "message": "workflow draft path is missing"}],
        }
    runtime_sop_path = str(data.get("runtime_sop_path") or data.get("runtimeSopPath") or "").strip()
    if not runtime_sop_path:
        runtime_result = read_json(draft_dir / "runtime_sop_result.json") or {}
        runtime_sop_path = str(runtime_result.get("runtime_sop_path") or "")
    if not runtime_sop_path or not Path(runtime_sop_path).exists():
        runtime_result = generate_workflow_draft_runtime_sop(sop, workflow_id, {"draft_id": draft_dir.name, "draft_path": str(draft_dir)})
        if runtime_result.get("status") == "failed":
            return 422, runtime_result
        runtime_sop_path = str(runtime_result.get("runtime_sop_path") or "")

    _overlay, resolved_runtime_sop_path, snapshot_error = load_workflow_runtime_sop_snapshot(sop, {
        "simulation_target": "runtime-sop",
        "runtime_sop_path": runtime_sop_path,
    })
    if snapshot_error:
        return 422, snapshot_error

    input_data = data.get("input") if isinstance(data.get("input"), dict) else {}
    first_node_inputs = data.get("first_node_inputs") if isinstance(data.get("first_node_inputs"), dict) else {}
    flattened_first_inputs = {}
    for node_inputs in first_node_inputs.values():
        if isinstance(node_inputs, dict):
            flattened_first_inputs.update(node_inputs)
    url = str(
        data.get("url")
        or data.get("source_url")
        or input_data.get("url")
        or input_data.get("source_url")
        or flattened_first_inputs.get("url")
        or flattened_first_inputs.get("source_url")
        or ""
    ).strip()
    merged_input = {**flattened_first_inputs, **input_data}
    if url:
        merged_input.setdefault("url", url)
        merged_input.setdefault("source_url", url)
    request_body = {
        **data,
        "repo": data.get("repo") or sop.get("repo", ""),
        "input": merged_input,
        "url": url,
        "source_url": url,
        "workflow_id": workflow_id,
        "workflow_draft_id": draft_dir.name,
        "workflow_draft_path": str(draft_dir),
        "runtime_sop_path": resolved_runtime_sop_path,
        "intent": data.get("intent") or f"workflow draft run: {draft_dir.name}",
    }
    status, result = trigger_sop(sop, request_body)
    pipeline_id = str((result or {}).get("pipeline_id") or "")
    metadata = {
        "source": "workflow-draft",
        "workflow_id": workflow_id,
        "workflow_draft_id": draft_dir.name,
        "workflow_draft_path": str(draft_dir),
        "runtime_sop_path": resolved_runtime_sop_path,
    }
    if pipeline_id:
        result["workflow_draft"] = patch_run_workflow_draft_metadata(sop, pipeline_id, metadata)
        result["status_url"] = f"/api/sop/{sop['id']}/runs/{pipeline_id}"
    result["workflow_draft_id"] = draft_dir.name
    result["runtime_sop_path"] = resolved_runtime_sop_path
    result["workflow_id"] = workflow_id
    return status, result


def publish_workflow_draft_to_runtime(sop, workflow_id, data):
    workflow_id = workflow_id or workflow_binding(sop).get("workflow_id") or sop.get("id") or ""
    data = data if isinstance(data, dict) else {}
    draft_id = str(data.get("draft_id") or data.get("draftId") or "").strip()
    draft_path = str(data.get("draft_path") or data.get("draftPath") or "").strip()
    draft_dir = resolve_workflow_draft_dir(sop, draft_id, draft_path)
    if not draft_dir:
        return {
            "status": "failed",
            "reason": "workflow draft not found",
            "errors": [{"code": "draft_not_found", "message": "workflow draft path is missing"}],
            "workflow_id": workflow_id,
        }
    runtime_result = read_json(draft_dir / "runtime_sop_result.json") or {}
    runtime_sop_path = str(runtime_result.get("runtime_sop_path") or "")
    if not runtime_sop_path or not Path(runtime_sop_path).exists():
        runtime_result = generate_workflow_draft_runtime_sop(sop, workflow_id, {
            "draft_id": draft_dir.name,
            "draft_path": str(draft_dir),
        })
        if runtime_result.get("status") == "failed":
            return runtime_result
        runtime_sop_path = str(runtime_result.get("runtime_sop_path") or "")
    runtime_sop_file = Path(runtime_sop_path).expanduser()
    if not runtime_sop_file.exists():
        return {
            "status": "failed",
            "reason": "runtime SOP snapshot missing",
            "errors": [{"code": "runtime_sop_missing", "message": "runtime_sop.yaml is missing for this draft"}],
            "workflow_id": workflow_id,
            "draft_id": draft_dir.name,
            "draft_path": str(draft_dir),
        }
    draft_manifest = read_json(draft_dir / "workflow_draft.json") or {}
    draft_payload = draft_manifest.get("draft") if isinstance(draft_manifest.get("draft"), dict) else {}
    requested_id = (
        data.get("published_workflow_id")
        or data.get("publishedWorkflowId")
        or draft_payload.get("publishedWorkflowId")
        or draft_payload.get("published_workflow_id")
    )
    published_id = slugify(requested_id or f"{workflow_id}-{draft_dir.name}")
    if published_id == workflow_id:
        published_id = slugify(f"{workflow_id}-{draft_dir.name}")
    published_title = str(
        data.get("title")
        or draft_payload.get("title")
        or draft_manifest.get("name")
        or draft_payload.get("name")
        or published_id
    ).strip()
    published_description = str(
        data.get("description")
        or draft_payload.get("goal")
        or draft_manifest.get("goal")
        or "Runtime-local workflow published from Workflow Draft."
    ).strip()
    publish_dir = runtime_workflows_root(sop) / published_id
    publish_dir.mkdir(parents=True, exist_ok=True)
    published_sop_file = publish_dir / "sop.yaml"
    shutil.copyfile(runtime_sop_file, published_sop_file)
    doc = read_yaml(published_sop_file)
    if not isinstance(doc, dict):
        doc = {}
    nodes = doc.get("nodes") if isinstance(doc.get("nodes"), dict) else {}
    edges = workflow_edge_rows(doc)
    manifest = {
        "status": "published",
        "workflow_id": published_id,
        "source_workflow_id": workflow_id,
        "name": published_id,
        "title": published_title,
        "description": published_description,
        "version": f"runtime-local-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        "sop_type": doc.get("id") or doc.get("name") or workflow_id,
        "interpreter": "generic-dag",
        "workflow_type": "business",
        "definition_source": "runtime-local-published",
        "draft_id": draft_dir.name,
        "draft_path": str(draft_dir),
        "runtime_sop_source": str(runtime_sop_file),
        "runtime_sop_path": str(published_sop_file),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "instance_id": sop.get("instance_id") or sop.get("id", ""),
        "runtime_id": sop.get("runtime_id", ""),
        "published_at": _now_iso_utc(),
        "active_runtime_sop_changed": False,
        "production_dag_changed": False,
        "repo_first_required": False,
    }
    write_json(publish_dir / "workflow.json", manifest)
    draft_manifest["published_runtime_workflow"] = manifest
    draft_manifest["runtime_publish_status"] = "published"
    draft_manifest["updated_at"] = _now_iso_utc()
    write_json(draft_dir / "workflow_draft.json", draft_manifest)
    _SOP_READ_CACHE.clear()
    return {
        **manifest,
        "message": "Workflow Draft published as a Runtime-local workflow definition. Source SOP repo is unchanged.",
        "catalog_url": "/api/sop/v1/workflows",
    }


def list_workflow_edge_drafts(sop):
    drafts_dir = Path(sop["wiki_local_path"]) / "raw" / "workflow-drafts"
    drafts = []
    if drafts_dir.exists():
        for draft_dir in sorted(drafts_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if draft_dir.is_dir():
                workflow_draft = read_json(draft_dir / "workflow_draft.json") or {}
                drafts.append({
                    "draft_id": draft_dir.name,
                    "draft_type": workflow_draft.get("draft_type") or (read_json(draft_dir / "change_request.json") or {}).get("draft_type", "save_evaluated_edge"),
                    "draft_path": str(draft_dir),
                    "workflow_draft": workflow_draft,
                    "edge": read_yaml(draft_dir / "edge.yaml"),
                    "edges": read_yaml(draft_dir / "edges.yaml"),
                    "edge_validations": read_json(draft_dir / "edge_validations.json") or [],
                    "evaluation": read_json(draft_dir / "evaluation.json") or {},
                    "change_request": read_json(draft_dir / "change_request.json") or {},
                    "validation": read_json(draft_dir / "validation.json") or {},
                    "runtime_sop_result": read_json(draft_dir / "runtime_sop_result.json") or {},
                    "apply_result": read_json(draft_dir / "apply_result.json") or {},
                })
    return drafts


def edge_node_execution_guide(edge):
    if not isinstance(edge, dict):
        return {}
    relay = edge.get("relay") if isinstance(edge.get("relay"), dict) else {}
    guide = relay.get("node_execution_guide") if isinstance(relay.get("node_execution_guide"), dict) else {}
    prompt = str(guide.get("prompt") or "").strip()
    if not prompt:
        return {}
    return {
        "format": str(guide.get("format") or "markdown"),
        "prompt": prompt,
    }


def workflow_edge_draft_prompt(draft):
    edge = draft.get("edge") if isinstance(draft, dict) and isinstance(draft.get("edge"), dict) else {}
    guide = edge_node_execution_guide(edge)
    if guide.get("prompt"):
        return guide
    draft_path = str((draft or {}).get("draft_path") or "")
    if draft_path:
        guide_path = Path(draft_path) / "node_execution_guide.md"
        try:
            prompt = guide_path.read_text(encoding="utf-8").strip()
        except OSError:
            prompt = ""
        if prompt:
            return {"format": "markdown", "prompt": prompt}
    return {}


def resolve_node_execution_guide(sop, target_node_id, relay_selection=None):
    relay_selection = relay_selection if isinstance(relay_selection, dict) else {}
    edge = relay_selection.get("edge_contract") if isinstance(relay_selection.get("edge_contract"), dict) else {}
    source_node = str(relay_selection.get("source_node") or edge.get("from") or edge.get("source") or "").strip()
    target_node = str(target_node_id or relay_selection.get("target_node_id") or edge.get("to") or edge.get("target") or "").strip()
    if not source_node or not target_node:
        return {}

    formal_edge = workflow_edge_contract(sop, source_node, target_node)
    guide = edge_node_execution_guide(formal_edge)
    if guide.get("prompt"):
        return {
            **guide,
            "source": "sop-edge",
            "edge_id": formal_edge.get("id") or edge.get("id") or f"{source_node}-to-{target_node}",
            "source_node": source_node,
            "target_node": target_node,
            "approved": True,
        }

    for draft in list_workflow_edge_drafts(sop):
        draft_edge = draft.get("edge") if isinstance(draft.get("edge"), dict) else {}
        validation = draft.get("validation") if isinstance(draft.get("validation"), dict) else {}
        if validation.get("status") and validation.get("status") != "passed":
            continue
        if str(draft_edge.get("from") or draft_edge.get("source") or "") != source_node:
            continue
        if str(draft_edge.get("to") or draft_edge.get("target") or "") != target_node:
            continue
        guide = workflow_edge_draft_prompt(draft)
        if not guide.get("prompt"):
            continue
        change_request = draft.get("change_request") if isinstance(draft.get("change_request"), dict) else {}
        return {
            **guide,
            "source": "edge-draft",
            "draft_id": draft.get("draft_id") or "",
            "draft_path": draft.get("draft_path") or "",
            "edge_id": draft_edge.get("id") or edge.get("id") or f"{source_node}-to-{target_node}",
            "source_node": source_node,
            "target_node": target_node,
            "approved": True,
            "evaluation_summary": change_request.get("evaluation_summary") if isinstance(change_request.get("evaluation_summary"), dict) else {},
        }

    return {
        "source": "missing",
        "edge_id": edge.get("id") or f"{source_node}-to-{target_node}",
        "source_node": source_node,
        "target_node": target_node,
        "approved": False,
        "prompt": "",
        "format": "markdown",
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
        "edges": sop.get("edges") if isinstance(sop.get("edges"), list) else [],
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


def normalize_hermes_webhook_url(raw, route):
    raw = str(raw or "").strip().rstrip("/")
    route = str(route or "sop-runtime-hermes-smoke").strip().strip("/") or "sop-runtime-hermes-smoke"
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"
    if "/webhooks/" in raw:
        return raw
    return f"{raw}/webhooks/{route}"


def derived_hermes_webhook_from_runtime(route):
    """Derive the managed Hermes channel from the current runtime channel.

    create-runtime registers two sibling auto-domain channels:
    runtime-<ip>.<domain> and hermes-runtime-<ip>.<domain>. Older env files can
    still contain a stale WEBHOOK_PUBLIC_HOST, so the runtime-local smoke check
    should prefer the current channel-derived Hermes host when the runtime
    channel follows the managed runtime-ip naming convention.
    """
    info = runtime_info()
    parsed = urlparse(str(info.get("channel_url") or ""))
    host = parsed.netloc or parsed.path
    host = host.split("/")[0].strip()
    if not host:
        return "", ""
    first_label = host.split(".", 1)[0]
    if not re.fullmatch(r"runtime-\d+-\d+-\d+-\d+", first_label):
        return "", ""
    return normalize_hermes_webhook_url(f"hermes-{host}", route), "runtime-channel:derived-hermes-host"


def hermes_smoke_target_candidates(context, route):
    explicit = node_run_config_lookup(context, "HERMES_WEBHOOK_URL", RUNTIME_CAPABILITY_ENV.get("HERMES_WEBHOOK_URL", []))
    public = node_run_config_lookup(context, "WEBHOOK_PUBLIC_HOST", [
        *RUNTIME_CAPABILITY_ENV.get("WEBHOOK_PUBLIC_HOST", []),
        "HERMES_PUBLIC_HOST",
    ])
    derived_url, derived_source = derived_hermes_webhook_from_runtime(route)
    candidates = []
    if not is_blank_value(explicit.get("value")):
        candidates.append({
            "key": explicit.get("key") or "HERMES_WEBHOOK_URL",
            "value": explicit.get("value"),
            "source": explicit.get("source") or "unknown:HERMES_WEBHOOK_URL",
            "url": normalize_hermes_webhook_url(explicit.get("value"), route),
        })
    if derived_url:
        candidates.append({
            "key": "HERMES_WEBHOOK_URL",
            "value": derived_url,
            "source": derived_source,
            "url": derived_url,
        })
    if not is_blank_value(public.get("value")):
        candidates.append({
            "key": public.get("key") or "WEBHOOK_PUBLIC_HOST",
            "value": public.get("value"),
            "source": public.get("source") or "unknown:WEBHOOK_PUBLIC_HOST",
            "url": normalize_hermes_webhook_url(public.get("value"), route),
        })

    unique = []
    seen = set()
    for item in candidates:
        url = str(item.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        unique.append(item)
    return unique


def hermes_webhook_url():
    route = hermes_smoke_route()
    raw = (
        os.environ.get("HERMES_WEBHOOK_URL")
        or os.environ.get("WEBHOOK_PUBLIC_HOST")
        or os.environ.get("HERMES_PUBLIC_HOST")
        or ""
    )
    return normalize_hermes_webhook_url(raw, route)


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
    context = node_run_config_context({}, None)
    route_item = node_run_config_lookup(context, "HERMES_SMOKE_ROUTE", RUNTIME_CAPABILITY_ENV.get("HERMES_SMOKE_ROUTE", []))
    route = str(route_item.get("value") or "sop-runtime-hermes-smoke").strip().strip("/") or "sop-runtime-hermes-smoke"
    route_source = route_item.get("source") if not is_blank_value(route_item.get("value")) else "default"
    target_candidates = hermes_smoke_target_candidates(context, route)
    target_item = target_candidates[0] if target_candidates else {"key": "HERMES_WEBHOOK_URL", "value": "", "source": "missing:HERMES_WEBHOOK_URL", "url": ""}
    token_item = node_run_config_lookup(context, "HERMES_WEBHOOK_TOKEN", RUNTIME_CAPABILITY_ENV.get("HERMES_WEBHOOK_TOKEN", []))
    target = str(target_item.get("url") or "")
    token = str(token_item.get("value") or "")
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
        "route": route,
        "curl": hermes_manual_curl(target or "https://<WEBHOOK_PUBLIC_HOST>/webhooks/sop-runtime-hermes-smoke", payload),
        "token_present": bool(token),
        "target_candidates": [
            {
                "target_url": item.get("url"),
                "source": item.get("source"),
                "key": item.get("key"),
            }
            for item in target_candidates
        ],
        "config": {
            "target": env_config_item(
                target_item.get("key") or "HERMES_WEBHOOK_URL",
                "Hermes Webhook URL",
                required=True,
                value=target_item.get("value"),
                source=target_item.get("source") or "missing:HERMES_WEBHOOK_URL",
            ),
            "token": env_config_item(
                token_item.get("key") or "HERMES_WEBHOOK_TOKEN",
                "Hermes Webhook Token",
                required=True,
                value=token,
                source=token_item.get("source") or "missing:HERMES_WEBHOOK_TOKEN",
            ),
            "route": env_config_item(
                route_item.get("key") or "HERMES_SMOKE_ROUTE",
                "Hermes Smoke Route",
                required=False,
                value=route,
                source=route_source,
            ),
            "settings_backend": context.get("settings_backend") or runtime_settings_backend(),
            "precedence": ["node-run-overrides", "instance-settings", "runtime-settings", "global-settings", "bridge-env", "runtime-env-file"],
        },
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
    candidate_results = []
    http_status = 0
    content_type = ""
    response_body = ""
    error = ""
    attempts = 0
    for candidate in target_candidates:
        target = str(candidate.get("url") or "")
        if not target:
            continue
        http_status, content_type, response_body, error, attempts = hermes_post_with_retry(target, data, headers, attempts=1)
        candidate_results.append({
            "target_url": target,
            "source": candidate.get("source"),
            "http_status": http_status,
            "ok": http_status in {200, 201, 202, 204},
            "error": error,
        })
        if http_status in {200, 201, 202, 204}:
            target_item = candidate
            break
    latency_ms = round((time.monotonic() - started) * 1000)
    ok = http_status in {200, 201, 202, 204}
    return (200 if ok else 502), {
        **base,
        "target_url": str(target_item.get("url") or target),
        "curl": hermes_manual_curl(str(target_item.get("url") or target) or "https://<WEBHOOK_PUBLIC_HOST>/webhooks/sop-runtime-hermes-smoke", payload),
        "candidate_results": candidate_results,
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
        "workflow_id": sop.get("raw_id") or sop.get("workflow_id") or sop_id or sop.get("sop_type"),
        "workflow_name": sop.get("workflow_title") or sop.get("name") or sop_id,
        "workflow_version": sop.get("version", ""),
        "definition_source": "sop.yaml",
        "definition_path": "sop.yaml",
        "node_count": len(business_nodes),
        "enabled_node_count": len(business_nodes),
        "binding_status": "ready" if business_nodes else "invalid",
    }


def workflow_definition_from_sop(sop):
    binding = workflow_binding(sop)
    workflow_id = str(binding.get("workflow_id") or "").strip()
    if not workflow_id:
        return None
    workflow_type = "management" if workflow_id == RUNTIME_MANAGEMENT_WORKFLOW_ID or sop.get("sop_type") == RUNTIME_MANAGEMENT_WORKFLOW_ID else "business"
    interpreter = "runtime-management" if workflow_type == "management" else "generic-dag"
    return {
        "id": workflow_id,
        "workflow_id": workflow_id,
        "name": workflow_id,
        "title": binding.get("workflow_name") or workflow_id,
        "description": sop.get("description") or (
            "Runtime-local workflow definition loaded from this instance."
        ),
        "version": binding.get("workflow_version") or sop.get("version", ""),
        "sop_type": sop.get("sop_type") or workflow_id,
        "interpreter": interpreter,
        "workflow_type": workflow_type,
        "definition_source": binding.get("definition_source") or "sop.yaml",
        "definition_path": binding.get("definition_path") or "sop.yaml",
        "node_count": binding.get("node_count") or 0,
        "enabled_node_count": binding.get("enabled_node_count") or 0,
        "instance_id": sop.get("instance_id") or sop.get("id", ""),
        "runtime_id": sop.get("runtime_id", ""),
        "published": False,
    }


def runtime_workflows_root(sop):
    return Path(sop["wiki_local_path"]) / "raw" / "runtime-workflows"


def list_published_runtime_workflows_for_sop(sop):
    root = runtime_workflows_root(sop)
    workflows = []
    if not root.exists():
        return workflows
    for workflow_dir in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True):
        manifest = read_json(workflow_dir / "workflow.json") or {}
        sop_doc = read_yaml(workflow_dir / "sop.yaml")
        if not isinstance(manifest, dict):
            manifest = {}
        if not isinstance(sop_doc, dict):
            sop_doc = {}
        workflow_id = str(manifest.get("workflow_id") or workflow_dir.name).strip()
        if not workflow_id:
            continue
        nodes = sop_doc.get("nodes") if isinstance(sop_doc.get("nodes"), dict) else {}
        business_nodes = [
            node_id for node_id, config in nodes.items()
            if node_id != "retry" and (config or {}).get("mode") != "manual"
        ]
        workflows.append({
            "id": workflow_id,
            "workflow_id": workflow_id,
            "name": manifest.get("name") or workflow_id,
            "title": manifest.get("title") or manifest.get("name") or workflow_id,
            "description": manifest.get("description") or "Published Runtime-local Workflow Draft.",
            "version": manifest.get("version") or "runtime-local",
            "sop_type": manifest.get("sop_type") or sop_doc.get("id") or sop_doc.get("name") or workflow_id,
            "interpreter": manifest.get("interpreter") or "generic-dag",
            "workflow_type": manifest.get("workflow_type") or "business",
            "definition_source": "runtime-local-published",
            "definition_path": str(workflow_dir / "sop.yaml"),
            "node_count": len(business_nodes),
            "enabled_node_count": len(business_nodes),
            "instance_id": sop.get("instance_id") or sop.get("id", ""),
            "runtime_id": sop.get("runtime_id", ""),
            "published": True,
            "published_at": manifest.get("published_at") or "",
            "draft_id": manifest.get("draft_id") or "",
            "draft_path": manifest.get("draft_path") or "",
            "runtime_sop_path": str(workflow_dir / "sop.yaml"),
        })
    return workflows


def list_runtime_workflow_definitions(query=None):
    query = query or {}
    runtime = runtime_info()
    registry = read_registry()
    rows = []
    seen = set()
    for sop in load_sops():
        definition = workflow_definition_from_sop(sop)
        if definition and definition["workflow_id"] not in seen:
            rows.append(definition)
            seen.add(definition["workflow_id"])
        for published in list_published_runtime_workflows_for_sop(sop):
            workflow_id = published.get("workflow_id")
            if workflow_id and workflow_id not in seen:
                rows.append(published)
                seen.add(workflow_id)
    q = query_value(query, "q", "").lower().strip()
    if q:
        rows = [
            item for item in rows
            if q in " ".join(str(item.get(key) or "") for key in (
                "workflow_id", "title", "description", "sop_type", "definition_source", "instance_id"
            )).lower()
        ]
    page, page_size, offset = page_params(query, 50)
    items = rows[offset:offset + page_size]
    return {
        "runtime_id": runtime["runtime_id"],
        "runtime": runtime,
        "channel": {
            "name": registry.get("channel_name", ""),
            "url": registry.get("channel_url", ""),
            "spi_base_url": registry.get("spi_base_url", ""),
        },
        "workflows": items,
        "items": items,
        "page": page_meta(page, page_size, len(rows)),
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
    derived_status, status_evidence = derive_run_status(sop, data)
    failed_node = data.get("failed_node") or (status_evidence.get("blocking_failed_nodes") or [""])[0] or next(
        (node_id for node_id, status in (data.get("nodes") or {}).items() if status == "failed"),
        "",
    )
    data.update({
        "execution_id": pipeline_id,
        "pipeline_id": pipeline_id,
        "status": derived_status,
        "runtime_id": sop.get("runtime_id", ""),
        "instance_id": sop.get("instance_id", sop.get("id", "")),
        "workflow_id": data.get("workflow_id") or workflow["workflow_id"],
        "workflow_version": data.get("workflow_version") or workflow["workflow_version"],
        "workflow_snapshot": data.get("workflow_snapshot") or {},
        "input": data.get("input") if isinstance(data.get("input"), dict) else {
            "url": data.get("source_url", "")
        },
        "failed_node": failed_node,
        "status_evidence": status_evidence,
        "sidecar_failed_nodes": status_evidence.get("sidecar_failed_nodes") or [],
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


def sop_business_node_modes(sop):
    modes = {}
    for node_id, config in (sop.get("nodes") or {}).items():
        config = config or {}
        mode = str(config.get("mode") or "blocking")
        if node_id == "retry" or mode == "manual":
            continue
        modes[node_id] = mode
    return modes


def derive_run_status(sop, data):
    """Derive a stable top-level run status from node states.

    Historical runs can have run.status stuck at "running" even after node files
    show terminal failures. The API should reflect the stronger node evidence
    without rewriting the user's wiki repo.
    """
    data = data if isinstance(data, dict) else {}
    current = str(data.get("status") or "")
    if current == "cancelled":
        return current, {}
    raw_nodes = data.get("nodes") if isinstance(data.get("nodes"), dict) else {}
    node_states = data.get("node_states") if isinstance(data.get("node_states"), dict) else {}
    statuses = {}
    for node_id, value in raw_nodes.items():
        statuses[str(node_id)] = str(value or "waiting")
    for node_id, state in node_states.items():
        if isinstance(state, dict) and state.get("status"):
            statuses[str(node_id)] = str(state.get("status"))
    modes = sop_business_node_modes(sop)
    if not statuses:
        return current or "waiting", {}
    candidate_ids = [node_id for node_id in statuses if node_id in modes] if modes else list(statuses)
    blocking_ids = [node_id for node_id in candidate_ids if modes.get(node_id, "blocking") != "sidecar"]
    sidecar_ids = [node_id for node_id in candidate_ids if modes.get(node_id) == "sidecar"]
    blocking_failed = [node_id for node_id in blocking_ids if statuses.get(node_id) == "failed"]
    sidecar_failed = [node_id for node_id in sidecar_ids if statuses.get(node_id) == "failed"]
    running = [node_id for node_id in candidate_ids if statuses.get(node_id) == "running"]
    terminal = {"done", "skipped", "failed", "cancelled"}
    blocking_terminal = all(statuses.get(node_id) in terminal for node_id in blocking_ids) if blocking_ids else False
    blocking_done = all(statuses.get(node_id) in {"done", "skipped"} for node_id in blocking_ids) if blocking_ids else False
    evidence = {
        "blocking_failed_nodes": blocking_failed,
        "sidecar_failed_nodes": sidecar_failed,
        "running_nodes": running,
    }
    if blocking_failed:
        return "failed", evidence
    if current == "failed":
        return "failed", evidence
    if running:
        return "running", evidence
    if blocking_done:
        return "done", evidence
    if blocking_terminal:
        return "failed" if any(statuses.get(node_id) in {"failed", "cancelled"} for node_id in blocking_ids) else "done", evidence
    return current or "waiting", evidence


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
    visible_node_ids = set()
    for node_id, node in (sop.get("nodes") or {}).items():
        if node.get("mode") == "manual" or node_id == "retry":
            continue
        visible_node_ids.add(node_id)
        static = node_static_config(sop, node_id) or {}
        registry_item = node_registry_item(sop, node_id) or {}
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
            "inputs": registry_item.get("inputs") or node.get("inputs", {}),
            "outputs": registry_item.get("outputs") or node.get("outputs", {}),
            "optional_inputs": registry_item.get("optional_inputs") or node.get("optional_inputs", {}),
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
    seen_edges = set()
    for edge in workflow_edge_rows(sop):
        source = edge.get("from") or edge.get("source") or ""
        target = edge.get("to") or edge.get("target") or ""
        if source not in visible_node_ids or target not in visible_node_ids:
            continue
        item = public_edge_contract(edge)
        item["source"] = source
        item["target"] = target
        item["from"] = source
        item["to"] = target
        edges.append(item)
        seen_edges.add((source, target))
    for node in nodes:
        node_id = node["id"]
        for need in node.get("needs") or []:
            if need not in visible_node_ids or (need, node_id) in seen_edges:
                continue
            edges.append({"id": f"edge-{need}-to-{node_id}", "source": need, "target": node_id, "from": need, "to": node_id, "derived_from": "needs"})
            seen_edges.add((need, node_id))
    return {"sop_id": sop_id, "nodes": nodes, "edges": edges, "workflow_revision": workflow_revision_snapshot(sop)}


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
    derived_status, status_evidence = derive_run_status(sop, {
        **data,
        "nodes": node_states,
    })

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
        "status": derived_status,
        "node_count": node_count,
        "done_count": done_count,
        "failed_count": failed_count,
        "running_node": running_node,
        "failed_node": data.get("failed_node") or (status_evidence.get("blocking_failed_nodes") or status_evidence.get("sidecar_failed_nodes") or [""])[0],
        "status_evidence": status_evidence,
        "sidecar_failed_nodes": status_evidence.get("sidecar_failed_nodes") or [],
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
    if safe.startswith("node-test-"):
        result = read_generic_node_test_result(sop, safe)
        if not result:
            return {"pipeline_id": safe, "node_id": node_id, "status": "running", "pending": True}
        return result
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


def node_test_workspace(sop, test_id):
    return Path(sop["wiki_local_path"]) / "raw" / "node-tests" / test_id


def node_run_workspace(sop, node_run_id):
    return Path(sop["wiki_local_path"]) / "raw" / "node-runs" / node_run_id


def node_run_input_sources_dir(sop, node_run_id):
    return node_run_workspace(sop, node_run_id) / "inputs" / "sources"


def node_run_output_files_dir(sop, node_run_id):
    return node_run_workspace(sop, node_run_id) / "outputs"


def node_run_legacy_output_files_dir(sop, node_run_id):
    return node_run_workspace(sop, node_run_id) / "outputs" / "files"


def node_run_existing_output_dir(sop, node_run_id):
    primary = node_run_output_files_dir(sop, node_run_id)
    legacy = node_run_legacy_output_files_dir(sop, node_run_id)
    if (primary / "manifest.json").exists():
        return primary
    if (legacy / "manifest.json").exists() or legacy.exists():
        return legacy
    if primary.exists():
        return primary
    return primary


def node_run_manifest_path(sop, node_run_id, kind):
    if kind == "input":
        return node_run_input_sources_dir(sop, node_run_id) / "manifest.json"
    if kind == "output":
        return node_run_existing_output_dir(sop, node_run_id) / "manifest.json"
    return node_run_workspace(sop, node_run_id) / f"{kind}-manifest.json"


def node_run_agent_dir(sop, node_run_id):
    return node_run_workspace(sop, node_run_id) / "agent"


def node_run_agent_path(sop, node_run_id, filename):
    return node_run_agent_dir(sop, node_run_id) / filename


def node_run_skill_name(sop, node_id, plan=None):
    plan = plan if isinstance(plan, dict) else {}
    node_cfg = (sop.get("nodes") or {}).get(node_id) if isinstance(sop.get("nodes"), dict) else {}
    static = node_static_config(sop, node_id) or {}
    for source in (
        plan.get("executor") if isinstance(plan.get("executor"), dict) else {},
        static.get("executor") if isinstance(static.get("executor"), dict) else {},
        node_cfg.get("executor") if isinstance(node_cfg, dict) and isinstance(node_cfg.get("executor"), dict) else {},
        static,
        node_cfg if isinstance(node_cfg, dict) else {},
    ):
        skill = str((source or {}).get("skill") or "").strip()
        if skill:
            return skill
    route = str((node_cfg or {}).get("webhook_route") or (node_cfg or {}).get("route") or "").strip()
    return route or f"sop-{node_id}"


def node_run_executor_kind(sop, node_id, plan=None):
    override = str(os.environ.get("NODE_RUN_AGENT_EXECUTOR") or "").strip().lower()
    if override in {"hermes", "legacy-shell"}:
        return override
    plan = plan if isinstance(plan, dict) else {}
    node_cfg = (sop.get("nodes") or {}).get(node_id) if isinstance(sop.get("nodes"), dict) else {}
    static = node_static_config(sop, node_id) or {}
    executor = {}
    for source in (
        node_cfg.get("executor") if isinstance(node_cfg, dict) and isinstance(node_cfg.get("executor"), dict) else {},
        static.get("executor") if isinstance(static.get("executor"), dict) else {},
        plan.get("executor") if isinstance(plan.get("executor"), dict) else {},
    ):
        executor.update(source or {})
    executor_type = str(executor.get("type") or "").strip().lower()
    agent = str(executor.get("agent") or "").strip().lower()
    if executor_type in {"direct-skill", "legacy-shell", "shell"}:
        return "legacy-shell"
    if executor_type in {"agent-skill", "skill"} or agent == "hermes":
        return "hermes"
    return "hermes"


def node_run_command_preview(command):
    if not command:
        return ""
    return " ".join(shlex.quote(str(part)) for part in command)


def default_agent_executor_template():
    return "\n".join([
        "# Node Execution Request",
        "",
        "Runtime context:",
        "- runtime_id: {{runtime_id}}",
        "- instance_id: {{instance_id}}",
        "- workflow_id: {{workflow_id}}",
        "- node_id: {{node_id}}",
        "- node_run_id: {{node_run_id}}",
        "",
        "Input contract:",
        "- entry_inputs: {{entry_inputs}}",
        "- input_manifest_path: {{input_manifest_path}}",
        "- input_directory: {{input_directory}}",
        "- source_url: {{source_url}}",
        "- Only use files listed in the input manifest unless the selected skill already has a stricter rule.",
        "",
        "Relay context:",
        "{{relay_context_brief}}",
        "",
        "Approved Edge Handoff Guide:",
        "{{node_execution_guide_prompt}}",
        "",
        "Output contract:",
        "- output_directory: {{output_directory}}",
        "- output_manifest_path: {{output_manifest_path}}",
        "- receipt_path: {{receipt_path}}",
        "- Do not report success unless the declared output manifest and receipt are written or the runtime wrapper writes equivalent state that the adapter can verify.",
        "",
        "Execution command:",
        "```bash",
        "{{stage_command}}",
        "```",
        "If the execution command is empty, execute the selected skill directly from this request: read entry_inputs, input_directory, input_manifest and the approved handoff guide, then write outputs to output_directory and output_manifest_path.",
        "",
        "",
        "Execution rules:",
        "- Use the selected skill only: {{skill_name}}.",
        "- Run the command from the Instance workspace unless the skill has an equivalent deterministic wrapper.",
        "- Preserve the provided environment variables.",
        "- Do not invent paths.",
        "- If execution fails, return the failure and include stderr/log guidance.",
    ])


def node_run_agent_template():
    configured = os.environ.get("NODE_RUN_AGENT_REQUEST_TEMPLATE", "").strip()
    return configured or default_agent_executor_template()


def render_node_run_agent_request(sop, node_run_id, node_id, plan, context, stage_command, skill_name):
    wiki = Path(sop["wiki_local_path"]).expanduser().resolve()
    request_path = node_run_agent_path(sop, node_run_id, "request.md")
    response_path = node_run_agent_path(sop, node_run_id, "response.txt")
    receipt_path = node_run_agent_path(sop, node_run_id, "receipt.json")
    executor_path = node_run_agent_path(sop, node_run_id, "executor.json")
    output_manifest = node_run_manifest_path(sop, node_run_id, "output")
    input_manifest = node_run_manifest_path(sop, node_run_id, "input")
    node_run_context = (context or {}).get("node_run") if isinstance((context or {}).get("node_run"), dict) else {}
    execution_request_data = node_run_context.get("execution_request_data") if isinstance(node_run_context.get("execution_request_data"), dict) else {}
    guide = node_run_context.get("node_execution_guide") if isinstance(node_run_context.get("node_execution_guide"), dict) else {}
    guide_prompt = str(guide.get("prompt") or "").strip()
    if not guide_prompt:
        guide_prompt = "No approved Edge Handoff Guide was resolved for this run."
    values = {
        "runtime_id": plan.get("runtime_id") or "",
        "instance_id": plan.get("instance_id") or "",
        "workflow_id": plan.get("workflow_id") or "",
        "node_id": node_id,
        "node_run_id": node_run_id,
        "skill_name": skill_name,
        "entry_inputs": json.dumps(execution_request_data.get("entry_inputs") or {}, ensure_ascii=False, indent=2),
        "input_manifest_path": safe_relative_file(wiki, input_manifest) or str(input_manifest),
        "input_directory": safe_relative_file(wiki, node_run_input_sources_dir(sop, node_run_id)) or str(node_run_input_sources_dir(sop, node_run_id)),
        "output_manifest_path": safe_relative_file(wiki, output_manifest) or str(output_manifest),
        "output_directory": safe_relative_file(wiki, node_run_output_files_dir(sop, node_run_id)) or str(node_run_output_files_dir(sop, node_run_id)),
        "receipt_path": safe_relative_file(wiki, receipt_path) or str(receipt_path),
        "source_url": (context or {}).get("source_url") or "",
        "relay_context_brief": node_run_context.get("relay_context_brief") or (context or {}).get("node_run_relay_context_brief") or "No upstream relay context was provided for this run.",
        "node_execution_guide_prompt": guide_prompt,
        "stage_command": node_run_command_preview(stage_command),
    }
    body = node_run_agent_template()
    for key, value in values.items():
        body = body.replace("{{" + key + "}}", str(value))
    guard = f"Use skill {skill_name} to execute this Node Execution Request."
    rendered = f"{guard}\n\n{body.rstrip()}\n"
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(rendered, encoding="utf-8")
    executor_kind = node_run_executor_kind(sop, node_id, plan)
    executor = {
        "version": 1,
        "executor": executor_kind,
        "requested_skill": skill_name,
        "node_id": node_id,
        "node_run_id": node_run_id,
        "runtime_id": values["runtime_id"],
        "instance_id": values["instance_id"],
        "workflow_id": values["workflow_id"],
        "template_source": "env:NODE_RUN_AGENT_REQUEST_TEMPLATE" if os.environ.get("NODE_RUN_AGENT_REQUEST_TEMPLATE", "").strip() else "default",
        "template_version": "hermes-agent-executor.v1",
        "request_path": safe_relative_file(wiki, request_path),
        "response_path": safe_relative_file(wiki, response_path),
        "receipt_path": safe_relative_file(wiki, receipt_path),
        "input_manifest_path": values["input_manifest_path"],
        "output_manifest_path": values["output_manifest_path"],
        "node_execution_guide": mask_data(guide),
        "stage_command": stage_command,
        "stage_command_preview": values["stage_command"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(executor_path, executor)
    return {
        **executor,
        "executor_path": safe_relative_file(wiki, executor_path),
        "rendered_request": rendered,
    }


def node_run_agent_artifacts(sop, node_run_id, node_id):
    wiki = Path(sop["wiki_local_path"]).expanduser().resolve()
    artifacts = []
    for filename, output_name, artifact_type, title in (
        ("request.md", "agent_request", "node-run.agent.request", "Rendered Agent Request"),
        ("executor.json", "agent_executor", "node-run.agent.executor", "Agent Executor Metadata"),
        ("response.txt", "agent_response", "node-run.agent.response", "Hermes Agent Response"),
        ("receipt.json", "agent_receipt", "node-run.agent.receipt", "Agent Execution Receipt"),
    ):
        path = node_run_agent_path(sop, node_run_id, filename)
        if not path.is_file():
            continue
        record = artifact_record(sop, node_id, output_name, path, "node-run-agent")
        if record:
            record["type"] = artifact_type
            record["title"] = title
            record["path"] = safe_relative_file(wiki, path)
            artifacts.append(record)
    return artifacts


def node_run_audit_evidence_paths(sop, node_run_id):
    wiki = Path(sop["wiki_local_path"]).expanduser().resolve()
    node_run_id = sanitize_node_run_id(node_run_id)
    if not node_run_id:
        return []
    paths = []

    def add_relative(relative):
        safe = safe_artifact_path(wiki, relative)
        if safe and safe.is_file():
            rel = safe_relative_file(wiki, safe)
            if rel and rel not in paths:
                paths.append(rel)

    for relative in (
        f"raw/node-runs/{node_run_id}/input.json",
        f"raw/node-runs/{node_run_id}/request.json",
        f"raw/node-runs/{node_run_id}/result.json",
        f"raw/node-runs/{node_run_id}/events.jsonl",
        f"raw/node-runs/{node_run_id}/executor.log",
        f"raw/node-runs/{node_run_id}/agent/request.md",
        f"raw/node-runs/{node_run_id}/agent/executor.json",
        f"raw/node-runs/{node_run_id}/agent/response.txt",
        f"raw/node-runs/{node_run_id}/agent/receipt.json",
    ):
        add_relative(relative)

    input_dir = wiki / "raw" / "node-runs" / node_run_id / "inputs" / "sources"
    if input_dir.exists():
        for child in sorted(input_dir.rglob("*")):
            if child.is_file():
                rel = safe_relative_file(wiki, child)
                if rel and rel not in paths:
                    paths.append(rel)
    output_dir = wiki / "raw" / "node-runs" / node_run_id / "outputs"
    if output_dir.exists():
        for child in sorted(output_dir.rglob("*")):
            if child.is_file():
                rel = safe_relative_file(wiki, child)
                if rel and rel not in paths:
                    paths.append(rel)
    return paths


def persist_node_run_audit_evidence_to_git(sop, node_run_id):
    wiki = Path(sop["wiki_local_path"]).expanduser().resolve()
    repo = str(sop.get("repo") or "")
    if not repo or "/" not in repo or not (wiki / ".git").exists():
        return {"status": "skipped", "reason": "wiki repo is not configured"}
    paths = node_run_audit_evidence_paths(sop, node_run_id)
    if not paths:
        return {"status": "skipped", "reason": "no node run audit evidence found"}
    remote_ok, remote_error = configure_instance_repo_remote(wiki, repo)
    if not remote_ok:
        return {"status": "failed", "paths": paths, "error": remote_error}
    subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=str(wiki), capture_output=True, text=True, timeout=60)
    add = subprocess.run(["git", "add", "-f", "--", *paths], cwd=str(wiki), capture_output=True, text=True)
    if add.returncode != 0:
        return {"status": "failed", "paths": paths, "error": add.stderr[:300] or "git add failed"}
    diff = subprocess.run(["git", "diff", "--cached", "--quiet", "--", *paths], cwd=str(wiki), capture_output=True)
    if diff.returncode == 0:
        return {"status": "done", "paths": paths, "pushed": False, "reason": "no changes"}
    changed_files = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--", *paths],
        cwd=str(wiki),
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    commit_msg = f"chore: node-run audit evidence [run:{node_run_id}]"
    commit = subprocess.run(["git", "commit", "-m", commit_msg], cwd=str(wiki), capture_output=True, text=True)
    commit_hash = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(wiki), capture_output=True, text=True).stdout.strip()
    if commit.returncode != 0:
        return {"status": "failed", "paths": paths, "changed_files": changed_files, "error": commit.stderr[:300] or "git commit failed"}
    pushed = False
    push_error = ""
    for attempt in range(1, 4):
        push = subprocess.run(["git", "push", "origin", "main"], cwd=str(wiki), capture_output=True, text=True, timeout=60)
        if push.returncode == 0:
            pushed = True
            break
        push_error = push.stderr[:300]
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=str(wiki), capture_output=True, text=True, timeout=60)
    return {
        "status": "done" if pushed else "failed",
        "paths": paths,
        "changed_files": changed_files,
        "commit": commit_hash,
        "commit_message": commit_msg,
        "pushed": pushed,
        "error": "" if pushed else push_error,
    }


def write_node_run_agent_receipt(sop, node_run_id, node_id, payload):
    receipt_path = node_run_agent_path(sop, node_run_id, "receipt.json")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(receipt_path, payload)
    return receipt_path


def hermes_agent_command_args(skill_name, request_text):
    command = hermes_agent_command()
    if not command:
        return []
    base = shlex.split(command) if any(ch.isspace() for ch in command) else [command]
    return base + ["-s", skill_name, "-z", request_text]


def wait_for_real_node_completion(sop, node_run_id, node_id, timeout_seconds, started_at):
    wiki = Path(sop["wiki_local_path"]).expanduser().resolve()
    deadline = time.monotonic() + max(5, timeout_seconds)
    last_state = {}
    while time.monotonic() < deadline:
        workspace = run_workspace(sop, node_run_id)
        state = read_json(workspace / "nodes" / f"{node_id}.json") or {}
        if state:
            last_state = state
        status = str(state.get("status") or "").lower()
        if status in {"done", "failed", "skipped"}:
            return status, state
        manifest = node_run_manifest_path(sop, node_run_id, "output")
        if manifest.exists() and state.get("validation", {}).get("status") == "passed":
            return "done", state
        time.sleep(2)
    return "timeout", last_state


def sanitize_test_id(value):
    return re.sub(r"[^A-Za-z0-9._-]", "", str(value or ""))


def recent_run_summaries(sop, limit=20):
    root = Path(sop["wiki_local_path"]) / "raw" / "pipeline-runs"
    rows = []
    if not root.exists():
        return rows
    for run_dir in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True):
        run_json = read_json(run_dir / "run.json") or {}
        context = read_json(run_dir / "context.json") or {}
        node_statuses = {}
        nodes_dir = run_dir / "nodes"
        if nodes_dir.exists():
            for node_file in nodes_dir.glob("*.json"):
                node_data = read_json(node_file) or {}
                node_statuses[node_file.stem] = node_data.get("status") or node_data.get("state") or ""
        rows.append({
            "pipeline_id": run_dir.name,
            "status": run_json.get("status") or "",
            "source_url": context.get("source_url") or run_json.get("source_url") or "",
            "updated_at": run_json.get("updated_at") or run_json.get("finished_at") or run_json.get("started_at") or "",
            "nodes": node_statuses,
        })
        if len(rows) >= limit:
            break
    return rows


def node_output_from_state(sop, pipeline_id, upstream_node, output_name):
    run_dir = run_workspace(sop, pipeline_id)
    state = read_json(run_dir / "nodes" / f"{upstream_node}.json") or {}
    for key in ["actual_outputs", "outputs", "resolved_outputs", "detail"]:
        value = state.get(key)
        if isinstance(value, dict) and output_name in value:
            return value.get(output_name), f"run:{pipeline_id}:nodes/{upstream_node}.json:{key}"
    return None, ""


def format_output_template(template, pipeline_id):
    if not isinstance(template, str):
        return ""
    run_id = pipeline_id
    return template.replace("{pipeline_id}", pipeline_id).replace("{run_id}", run_id)


def generated_fixture_value(input_name, source, spec=None):
    spec = spec if isinstance(spec, dict) else {}
    example = spec.get("example")
    if not is_blank_value(example):
        return example
    name = str(input_name or "").lower()
    source_text = str(source or "").lower()
    if name in {"source_url", "url"} or source_text.endswith(".source_url"):
        return "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    if name in {"metadata_file"} or source_text.endswith(".metadata_file"):
        return "raw/youtube-metadata/node-test-fixture.json"
    if name in {"reports"} or source_text.endswith(".reports"):
        return "raw/notebooklm-analysis/node-test-fixture.md"
    if name in {"deep_research", "analysis_file"} or source_text.endswith(".analysis_file"):
        return "raw/youtube-deep-research/node-test-fixture/analysis.md"
    if name in {"index"} or source_text.endswith(".index"):
        return "index.md"
    return None


def is_blank_value(value):
    return value is None or (isinstance(value, str) and value == "")


def normalize_node_run_relay_mode(value, input_source=""):
    text = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "auto": "auto_by_target_inputs",
        "auto_by_inputs": "auto_by_target_inputs",
        "target_inputs": "auto_by_target_inputs",
        "selected": "selected_outputs",
        "manual": "selected_outputs",
        "all": "all_outputs",
        "package": "all_outputs",
        "whole_package": "all_outputs",
    }
    text = aliases.get(text, text)
    if text not in {"auto_by_target_inputs", "selected_outputs", "all_outputs"}:
        text = "auto_by_target_inputs" if input_source == "existing-node-run" else ""
    return text


def normalize_selected_outputs(value):
    if isinstance(value, list):
        rows = value
    elif isinstance(value, str):
        rows = re.split(r"[\s,]+", value)
    else:
        rows = []
    result = []
    for item in rows:
        text = str(item or "").strip()
        if text and re.match(r"^[A-Za-z0-9_.-]+$", text):
            result.append(text)
    return ordered_unique(result)


def normalize_relay_mappings(value):
    if not isinstance(value, list):
        return []
    rows = []
    for item in value:
        if not isinstance(item, dict):
            continue
        source_output = str(item.get("source_output") or item.get("sourceOutput") or item.get("output") or item.get("from") or "").strip()
        target_input = str(item.get("target_input") or item.get("targetInput") or item.get("input") or item.get("to") or "").strip()
        if ".outputs." in source_output:
            _source_node, source_output = source_ref_output(source_output)
        if ".inputs." in target_input:
            match = re.match(r"^[A-Za-z0-9_-]+\.inputs\.([A-Za-z0-9_.-]+)$", target_input)
            target_input = match.group(1) if match else target_input
        resolver = str(item.get("resolver") or item.get("resolver_id") or item.get("resolverId") or "").strip()
        if not source_output or not re.match(r"^[A-Za-z0-9_.-]+$", source_output):
            continue
        if target_input and not re.match(r"^[A-Za-z0-9_.-]+$", target_input):
            target_input = ""
        rows.append({
            "source_output": source_output,
            "target_input": target_input,
            "resolver": resolver,
        })
    return rows


def source_ref_output(source):
    match = re.match(r"^([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)$", str(source or ""))
    if not match:
        return "", ""
    return match.group(1), match.group(2)


def workflow_edge_rows(sop):
    rows = sop.get("edges") if isinstance(sop, dict) else []
    if not isinstance(rows, list):
        return []
    result = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        source = str(item.get("from") or item.get("source") or "").strip()
        target = str(item.get("to") or item.get("target") or "").strip()
        if not source or not target:
            continue
        edge_id = str(item.get("id") or f"edge-{source}-to-{target}").strip()
        result.append({**item, "id": edge_id, "from": source, "to": target})
    return result


def workflow_edge_contract(sop, source_node, target_node):
    for edge in workflow_edge_rows(sop):
        if edge.get("from") == source_node and edge.get("to") == target_node:
            return edge
    return {}


def workflow_revision_snapshot(sop):
    nodes = sop.get("nodes") if isinstance(sop, dict) else {}
    node_rows = {}
    if isinstance(nodes, dict):
        for node_id, node in sorted(nodes.items()):
            if not isinstance(node, dict):
                node_rows[node_id] = node
                continue
            node_rows[node_id] = {
                "title": node.get("title") or "",
                "skill": node.get("skill") or (node.get("executor") or {}).get("skill") or "",
                "mode": node.get("mode") or "",
                "needs": node.get("needs") or [],
                "inputs": node.get("inputs") or {},
                "optional_inputs": node.get("optional_inputs") or {},
                "outputs": node.get("outputs") or {},
                "capabilities": node.get("capabilities") or {},
            }
    edges = workflow_edge_rows(sop)
    payload = {"nodes": node_rows, "edges": edges}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "version": 1,
        "hash": hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16],
        "node_count": len(node_rows),
        "edge_count": len(edges),
        "nodes": node_rows,
        "edges": edges,
    }


def target_input_resolver_id(sop, target_node, target_input, extractor):
    extractor = extractor if isinstance(extractor, dict) else {}
    explicit = str(extractor.get("id") or extractor.get("resolver") or extractor.get("resolver_id") or "").strip()
    if explicit:
        return explicit
    static = node_static_config(sop, target_node) or {}
    specs = {
        **normalize_contract(static.get("optional_inputs") or {}, "input"),
        **normalize_contract(static.get("inputs") or {}, "input"),
    }
    spec = specs.get(target_input) if isinstance(specs, dict) else {}
    resolvers = node_input_resolvers(spec if isinstance(spec, dict) else {})
    kind = str(extractor.get("kind") or extractor.get("type") or "").strip()
    path = str(extractor.get("path") or "").strip()
    for resolver in resolvers:
        if kind and str(resolver.get("kind") or resolver.get("type") or "") != kind:
            continue
        if path and str(resolver.get("path") or "") != path:
            continue
        return str(resolver.get("id") or resolver.get("name") or resolver.get("kind") or "")
    for resolver in resolvers:
        if str(resolver.get("kind") or resolver.get("type") or "") == "direct":
            return str(resolver.get("id") or resolver.get("name") or "direct")
    return kind or "direct"


def edge_contract_relay_mappings(sop, edge):
    if not isinstance(edge, dict):
        return []
    relay = edge.get("relay") if isinstance(edge.get("relay"), dict) else {}
    bindings = relay.get("bindings") if isinstance(relay.get("bindings"), list) else []
    target_node = str(edge.get("to") or edge.get("target") or "").strip()
    rows = []
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        target_input = str(binding.get("target_input") or binding.get("input") or "").strip()
        source = binding.get("source") if isinstance(binding.get("source"), dict) else {}
        source_output = str(source.get("output") or binding.get("source_output") or "").strip()
        if not source_output or not target_input:
            continue
        extractor = source.get("extractor") if isinstance(source.get("extractor"), dict) else {}
        rows.append({
            "source_output": source_output,
            "target_input": target_input,
            "resolver": target_input_resolver_id(sop, target_node, target_input, extractor),
            "edge_binding_required": bool(binding.get("required", False)),
        })
    return rows


def public_edge_contract(edge):
    if not isinstance(edge, dict):
        return {}
    relay = edge.get("relay") if isinstance(edge.get("relay"), dict) else {}
    intent = relay.get("intent") if isinstance(relay.get("intent"), dict) else {}
    return {
        "id": edge.get("id") or "",
        "from": edge.get("from") or edge.get("source") or "",
        "to": edge.get("to") or edge.get("target") or "",
        "intent": {
            "title": intent.get("title") or "",
            "brief": intent.get("brief") or "",
        },
        "bindings": relay.get("bindings") if isinstance(relay.get("bindings"), list) else [],
        "resolver": relay.get("resolver") if isinstance(relay.get("resolver"), dict) else {},
        "validation": relay.get("validation") if isinstance(relay.get("validation"), dict) else {},
    }


def edge_handoff_evaluator_script():
    configured = os.environ.get("EDGE_HANDOFF_EVALUATOR_SCRIPT", "").strip()
    candidates = [
        Path(configured).expanduser() if configured else None,
        plugin_root() / "youtube-wiki/skills/sop-edge-handoff-evaluator/scripts/edge_handoff_evaluator.py",
        Path.home() / "agent-brain-plugins/youtube-wiki/skills/sop-edge-handoff-evaluator/scripts/edge_handoff_evaluator.py",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    return None


def edge_handoff_node_payload(sop, node_id):
    item = node_registry_item(sop, node_id) or {}
    cfg = (sop.get("nodes") or {}).get(node_id) if isinstance(sop.get("nodes"), dict) else {}
    cfg = cfg if isinstance(cfg, dict) else {}
    skill = item.get("skill") if isinstance(item.get("skill"), dict) else {}
    executor = item.get("executor") if isinstance(item.get("executor"), dict) else cfg.get("executor") if isinstance(cfg.get("executor"), dict) else {}
    return {
        "node_id": node_id,
        "title": item.get("title") or cfg.get("title") or node_id,
        "skill_id": skill.get("id") or executor.get("skill") or cfg.get("skill") or item.get("skill_id") or node_id,
        "skill_summary": edge_handoff_compact_text(skill.get("summary") or item.get("description") or cfg.get("description") or "", 600),
        "skill_readme": edge_handoff_compact_text(skill.get("summary") or item.get("skill_readme") or "", 800),
        "inputs": item.get("inputs") or normalize_contract(cfg.get("inputs") or {}, "input"),
        "optional_inputs": item.get("optional_inputs") or normalize_contract(cfg.get("optional_inputs") or {}, "input"),
        "outputs": item.get("outputs") or normalize_contract(cfg.get("outputs") or {}, "output"),
        "executor": executor,
        "capabilities": item.get("capabilities") or cfg.get("capabilities") or {},
        "infra": item.get("infra") or cfg.get("infra") or {},
    }


def edge_handoff_compact_text(value, limit=1800):
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated for Edge Handoff evaluation]"


def edge_handoff_skill_meta_text(value, limit=420):
    text = str(value or "").strip()
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end >= 0:
            return text[:end + 4].strip()
    return edge_handoff_compact_text(text, limit)


def edge_handoff_compact_node_context(node):
    node = node if isinstance(node, dict) else {}
    if not node:
        return {}
    result = dict(node)
    result["skill_summary"] = edge_handoff_skill_meta_text(result.get("skill_summary") or result.get("description") or "", 420)
    result["skill_readme"] = edge_handoff_skill_meta_text(result.get("skill_readme") or result.get("readme") or "", 420)
    return result


def edge_handoff_merge_node_payload(sop, node_id, provided):
    provided = provided if isinstance(provided, dict) else {}
    runtime = edge_handoff_node_payload(sop, node_id) if node_id else {}
    if not runtime:
        return provided
    result = {**runtime, **provided}
    # The runtime SOP/node registry is the source of truth for contracts. Frontend
    # payloads are often compact summaries and must not erase inputs/outputs.
    for key in ("inputs", "optional_inputs", "outputs", "executor", "capabilities", "infra"):
        if runtime.get(key):
            result[key] = runtime[key]
    for key in ("node_id", "title", "skill_id", "skill_summary", "skill_readme"):
        if runtime.get(key):
            result[key] = runtime[key]
    return result


def edge_handoff_request_payload(sop, workflow_id, data):
    data = data if isinstance(data, dict) else {}
    edge = data.get("edge") if isinstance(data.get("edge"), dict) else {}
    upstream_data = data.get("upstream") if isinstance(data.get("upstream"), dict) else {}
    downstream_data = data.get("downstream") if isinstance(data.get("downstream"), dict) else {}
    upstream_node_id = str(
        data.get("upstream_node_id")
        or edge.get("from")
        or edge.get("source")
        or upstream_data.get("node_id")
    ).strip()
    downstream_node_id = str(
        data.get("downstream_node_id")
        or edge.get("to")
        or edge.get("target")
        or downstream_data.get("node_id")
    ).strip()
    upstream = edge_handoff_merge_node_payload(sop, upstream_node_id, upstream_data)
    downstream = edge_handoff_merge_node_payload(sop, downstream_node_id, downstream_data)
    upstream = edge_handoff_compact_node_context(upstream)
    downstream = edge_handoff_compact_node_context(downstream)
    runtime_id = sop.get("runtime_id") or os.environ.get("SOP_RUNTIME_ID") or ""
    return {
        "runtime_id": data.get("runtime_id") or runtime_id,
        "instance_id": data.get("instance_id") or sop.get("id") or sop.get("instance_id") or "",
        "workflow_id": data.get("workflow_id") or workflow_id or workflow_binding(sop).get("workflow_id") or sop.get("id") or "",
        "edge_id": data.get("edge_id") or edge.get("id") or f"edge-{upstream_node_id}-to-{downstream_node_id}",
        "phase": data.get("phase") or "design",
        "workflow_goal": data.get("workflow_goal") or data.get("goal") or "",
        "edge_handoff_instruction": data.get("edge_handoff_instruction") or data.get("instruction") or edge.get("instruction") or "",
        "edge": edge,
        "upstream": upstream,
        "downstream": downstream,
    }


def workflow_edge_signature_payload(request_payload):
    request_payload = request_payload if isinstance(request_payload, dict) else {}
    edge = request_payload.get("edge") if isinstance(request_payload.get("edge"), dict) else {}
    return {
        "edge_id": request_payload.get("edge_id") or edge.get("id") or "",
        "workflow_id": request_payload.get("workflow_id") or "",
        "upstream_node_id": (request_payload.get("upstream") or {}).get("node_id") if isinstance(request_payload.get("upstream"), dict) else "",
        "downstream_node_id": (request_payload.get("downstream") or {}).get("node_id") if isinstance(request_payload.get("downstream"), dict) else "",
        "edge_handoff_instruction": request_payload.get("edge_handoff_instruction") or "",
        "relay_mode": edge.get("relayMode") or edge.get("relay_mode") or "",
        "relay_mappings": edge.get("relayMappings") or edge.get("relay_mappings") or [],
        "edge": edge,
    }


def workflow_edge_stable_signature(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def workflow_edge_instruction_signature(request_payload):
    return workflow_edge_stable_signature({
        "edge_handoff_instruction": (request_payload or {}).get("edge_handoff_instruction") or "",
    })


def workflow_edge_edge_signature(request_payload):
    return workflow_edge_stable_signature(workflow_edge_signature_payload(request_payload))


def edge_handoff_evaluator_env(sop, data):
    context = node_run_config_context(data, sop)
    base_url = node_run_config_lookup(context, "EDGE_HANDOFF_LLM_BASE_URL", [
        *RUNTIME_CAPABILITY_ENV.get("EDGE_HANDOFF_LLM_BASE_URL", []),
        "HERMES_MODEL_BASE_URL",
        *RUNTIME_CAPABILITY_ENV.get("HERMES_MODEL_BASE_URL", []),
        "WIKI_LLM_BASE_URL",
        *RUNTIME_CAPABILITY_ENV.get("WIKI_LLM_BASE_URL", []),
    ])
    api_key = node_run_config_lookup(context, "EDGE_HANDOFF_LLM_API_KEY", [
        *RUNTIME_CAPABILITY_ENV.get("EDGE_HANDOFF_LLM_API_KEY", []),
        "HERMES_OPENAI_API_KEY",
        "OPENAI_API_KEY",
        *RUNTIME_CAPABILITY_ENV.get("HERMES_OPENAI_API_KEY", []),
        *RUNTIME_CAPABILITY_ENV.get("OPENAI_API_KEY", []),
        "WIKI_LLM_API_KEY",
        *RUNTIME_CAPABILITY_ENV.get("WIKI_LLM_API_KEY", []),
    ])
    model = edge_handoff_model_lookup(context)
    env = os.environ.copy()
    if not is_blank_value(base_url.get("value")):
        env["EDGE_HANDOFF_LLM_BASE_URL"] = str(base_url.get("value")).rstrip("/")
    if not is_blank_value(api_key.get("value")):
        env["EDGE_HANDOFF_LLM_API_KEY"] = str(api_key.get("value"))
    if not is_blank_value(model.get("value")):
        env["EDGE_HANDOFF_LLM_MODEL"] = str(model.get("value"))
    async_job = bool(data.get("async_job"))
    # Public runtime channels have a short upstream timeout. Keep synchronous
    # Edge Handoff evaluation inside that budget so the UI receives structured
    # JSON instead of a network-level "Failed to fetch". Background jobs can
    # use a longer budget because the browser polls their persisted result.
    if async_job:
        env.setdefault("EDGE_HANDOFF_LLM_TIMEOUT", "75")
        env.setdefault("EDGE_HANDOFF_LLM_ATTEMPTS", "1")
        env.setdefault("EDGE_HANDOFF_EVALUATOR_TIMEOUT", "90")
        clamp_int_env(env, "EDGE_HANDOFF_LLM_TIMEOUT", 20, 75)
        clamp_int_env(env, "EDGE_HANDOFF_LLM_ATTEMPTS", 1, 1)
        clamp_int_env(env, "EDGE_HANDOFF_EVALUATOR_TIMEOUT", 30, 90)
    else:
        env.setdefault("EDGE_HANDOFF_LLM_TIMEOUT", "23")
        env.setdefault("EDGE_HANDOFF_LLM_ATTEMPTS", "1")
        env.setdefault("EDGE_HANDOFF_EVALUATOR_TIMEOUT", "24")
        clamp_int_env(env, "EDGE_HANDOFF_LLM_TIMEOUT", 8, 23)
        clamp_int_env(env, "EDGE_HANDOFF_LLM_ATTEMPTS", 1, 1)
        clamp_int_env(env, "EDGE_HANDOFF_EVALUATOR_TIMEOUT", 10, 24)
    env.setdefault("EDGE_HANDOFF_LLM_MAX_TOKENS", "4096")
    clamp_int_env(env, "EDGE_HANDOFF_LLM_MAX_TOKENS", 1024, 8192)
    return env, {
        "base_url": env_config_item(
            base_url.get("key") or "EDGE_HANDOFF_LLM_BASE_URL",
            "Edge Handoff LLM Base URL",
            required=True,
            value=str(base_url.get("value") or "").rstrip("/"),
            source=base_url.get("source") or "missing:EDGE_HANDOFF_LLM_BASE_URL",
        ),
        "api_key": env_config_item(
            api_key.get("key") or "EDGE_HANDOFF_LLM_API_KEY",
            "Edge Handoff LLM API Key",
            required=True,
            value=api_key.get("value"),
            source=api_key.get("source") or "missing:EDGE_HANDOFF_LLM_API_KEY",
        ),
        "model": env_config_item(
            model.get("key") or "EDGE_HANDOFF_LLM_MODEL",
            "Edge Handoff LLM Model",
            required=True,
            value=model.get("value"),
            source=model.get("source") or "missing:EDGE_HANDOFF_LLM_MODEL",
        ),
        "settings_backend": context.get("settings_backend") or runtime_settings_backend(),
        "precedence": ["node-run-overrides", "instance-settings", "runtime-settings", "global-settings", "bridge-env", "runtime-env-file"],
        "sync_budget": {
            "public_timeout_budget_seconds": 25,
            "mode": "async" if async_job else "sync",
            "llm_timeout_seconds": int(env.get("EDGE_HANDOFF_LLM_TIMEOUT", "75" if async_job else "23") or ("75" if async_job else "23")),
            "llm_attempts": int(env.get("EDGE_HANDOFF_LLM_ATTEMPTS", "1") or "1"),
            "evaluator_timeout_seconds": int(env.get("EDGE_HANDOFF_EVALUATOR_TIMEOUT", "90" if async_job else "24") or ("90" if async_job else "24")),
        },
    }


def edge_handoff_model_lookup(context):
    groups = [
        ["EDGE_HANDOFF_LLM_MODEL", *RUNTIME_CAPABILITY_ENV.get("EDGE_HANDOFF_LLM_MODEL", [])],
        ["HERMES_MODEL", *RUNTIME_CAPABILITY_ENV.get("HERMES_MODEL", [])],
        ["WIKI_DEEPSEEK_MODEL", *RUNTIME_CAPABILITY_ENV.get("WIKI_DEEPSEEK_MODEL", [])],
        ["WIKI_LLM_MODEL", *RUNTIME_CAPABILITY_ENV.get("WIKI_LLM_MODEL", [])],
    ]
    for group in groups:
        resolved = node_run_config_lookup(context, group[0], group[1:])
        if not is_blank_value(resolved.get("value")):
            if str(resolved.get("value") or "").strip() == "deepseek-v4-flash":
                return {
                    "key": "EDGE_HANDOFF_LLM_MODEL",
                    "value": os.environ.get("EDGE_HANDOFF_LLM_FALLBACK_MODEL", "deepseek-v4-pro"),
                    "source": f"{resolved.get('source') or resolved.get('key') or group[0]}:fallback-from-deepseek-v4-flash",
                }
            return resolved
    return {"key": "EDGE_HANDOFF_LLM_MODEL", "value": os.environ.get("EDGE_HANDOFF_LLM_FALLBACK_MODEL", "deepseek-v4-pro"), "source": "default"}


def ensure_int_env_at_least(env, key, minimum):
    try:
        current = int(str(env.get(key, "") or "0"))
    except Exception:
        current = 0
    if current < minimum:
        env[key] = str(minimum)


def clamp_int_env(env, key, minimum, maximum):
    try:
        current = int(str(env.get(key, "") or "0"))
    except Exception:
        current = 0
    if current < minimum:
        current = minimum
    if current > maximum:
        current = maximum
    env[key] = str(current)


def evaluate_edge_handoff(sop, workflow_id, data):
    request_payload = edge_handoff_request_payload(sop, workflow_id, data)
    if not (request_payload.get("upstream") or {}).get("node_id") or not (request_payload.get("downstream") or {}).get("node_id"):
        return 422, {
            "status": "blocked",
            "detail": "upstream_node_id and downstream_node_id are required",
            "request": request_payload,
        }
    script = edge_handoff_evaluator_script()
    if not script:
        return 503, {
            "status": "blocked",
            "detail": "sop-edge-handoff-evaluator script is not installed on this Runtime",
            "request": request_payload,
        }
    allow_deterministic = bool(data.get("allow_deterministic") or data.get("allow_fallback"))
    evaluator_env, evaluator_config = edge_handoff_evaluator_env(sop, data)
    with tempfile.TemporaryDirectory(prefix="edge-handoff-") as temp_dir:
        request_path = Path(temp_dir) / "request.json"
        output_path = Path(temp_dir) / "evaluation.json"
        request_path.write_text(json.dumps(request_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        command = ["python3", str(script), "--request-json", str(request_path), "--output-json", str(output_path), "--require-ai"]
        if allow_deterministic:
            command.append("--allow-deterministic")
        evaluator_timeout = int(evaluator_env.get("EDGE_HANDOFF_EVALUATOR_TIMEOUT", "90") or "90")
        try:
            completed = subprocess.run(command, text=True, capture_output=True, timeout=evaluator_timeout, env=evaluator_env)
        except subprocess.TimeoutExpired as exc:
            return 504, {
                "ok": False,
                "sop_id": sop.get("id", ""),
                "workflow_id": request_payload.get("workflow_id", ""),
                "edge_id": request_payload.get("edge_id", ""),
                "mode": "edge-handoff-agent-evaluation",
                "request": request_payload,
                "config": evaluator_config,
                "evaluation": {
                    "status": "blocked",
                    "summary": f"Edge Handoff Agent timed out after {evaluator_timeout}s.",
                    "blocking_reasons": [
                        {
                            "code": "edge_handoff_timeout",
                            "message": "The LLM evaluation did not finish within the public runtime timeout budget.",
                        }
                    ],
                    "required_user_inputs": [
                        {
                            "field": "EDGE_HANDOFF_LLM_MODEL",
                            "question": "Use a faster Edge Handoff model or reduce the prompt/output budget.",
                        }
                    ],
                    "agent": {
                        "provider": "openai-compatible",
                        "model": evaluator_config.get("model", {}).get("value", ""),
                        "used_ai": False,
                    },
                    "node_execution_guide": {"format": "markdown", "prompt": ""},
                    "test_plan": ["Fix Edge Handoff LLM timeout/model config and rerun evaluation."],
                    "resolved_handoff": {},
                },
                "stderr": str(exc)[-4000:],
            }
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if output_path.is_file():
            evaluation = read_json(output_path) or {}
        else:
            try:
                evaluation = json.loads(stdout)
            except Exception:
                evaluation = {
                    "status": "blocked",
                    "summary": "Edge Handoff evaluator did not return JSON.",
                    "blocking_reasons": [{"code": "invalid_evaluator_output", "message": stderr or stdout[-1000:]}],
                }
        http_status = 200 if completed.returncode == 0 else 500
        if isinstance(evaluation, dict):
            evaluation.setdefault("evaluated_instruction_signature", workflow_edge_instruction_signature(request_payload))
            evaluation.setdefault("evaluated_edge_signature", workflow_edge_edge_signature(request_payload))
            evaluation.setdefault("evaluated_instruction", request_payload.get("edge_handoff_instruction") or "")
        return http_status, {
            "ok": completed.returncode == 0,
            "sop_id": sop.get("id", ""),
            "workflow_id": request_payload.get("workflow_id", ""),
            "edge_id": request_payload.get("edge_id", ""),
            "mode": "edge-handoff-agent-evaluation",
            "request": request_payload,
            "config": evaluator_config,
            "evaluation": evaluation,
            "stderr": stderr[-4000:],
        }


def edge_handoff_evaluation_dir(sop):
    path = Path(sop["wiki_local_path"]) / "raw" / "workflow-drafts" / "edge-evaluations"
    path.mkdir(parents=True, exist_ok=True)
    return path


def edge_handoff_evaluation_path(sop, evaluation_id):
    safe_id = slugify(evaluation_id)
    return edge_handoff_evaluation_dir(sop) / f"{safe_id}.json"


def write_edge_handoff_evaluation(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_edge_handoff_evaluation(sop, evaluation_id):
    path = edge_handoff_evaluation_path(sop, evaluation_id)
    if not path.is_file():
        return None
    data = read_json(path) or {}
    data.setdefault("evaluation_id", slugify(evaluation_id))
    return data


def run_edge_handoff_evaluation_job(sop, workflow_id, data, evaluation_id, job_path):
    started_at = datetime.now(timezone.utc).isoformat()
    write_edge_handoff_evaluation(job_path, {
        "ok": False,
        "status": "running",
        "mode": "edge-handoff-agent-evaluation-job",
        "evaluation_id": evaluation_id,
        "sop_id": sop.get("id", ""),
        "workflow_id": workflow_id,
        "edge_id": data.get("edge_id") or (data.get("edge") or {}).get("id") or "",
        "started_at": started_at,
    })
    job_data = dict(data)
    job_data["async_job"] = True
    try:
        http_status, result = evaluate_edge_handoff(sop, workflow_id, job_data)
        evaluation = result.get("evaluation") if isinstance(result, dict) else {}
        status = "done" if http_status < 500 and bool(result.get("ok")) else "failed"
        write_edge_handoff_evaluation(job_path, {
            "ok": bool(result.get("ok")),
            "status": status,
            "http_status": http_status,
            "mode": "edge-handoff-agent-evaluation-job",
            "evaluation_id": evaluation_id,
            "sop_id": sop.get("id", ""),
            "workflow_id": workflow_id,
            "edge_id": result.get("edge_id") if isinstance(result, dict) else "",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "result": result,
            "evaluation": evaluation if isinstance(evaluation, dict) else {},
        })
    except Exception as exc:
        write_edge_handoff_evaluation(job_path, {
            "ok": False,
            "status": "failed",
            "mode": "edge-handoff-agent-evaluation-job",
            "evaluation_id": evaluation_id,
            "sop_id": sop.get("id", ""),
            "workflow_id": workflow_id,
            "edge_id": data.get("edge_id") or (data.get("edge") or {}).get("id") or "",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "error": str(exc),
            "evaluation": {
                "status": "blocked",
                "summary": "Edge Handoff Agent async evaluation failed.",
                "blocking_reasons": [{"code": "edge_handoff_async_failed", "message": str(exc)}],
                "agent": {"used_ai": False},
            },
        })


def start_edge_handoff_evaluation_job(sop, workflow_id, data):
    edge_id = str(data.get("edge_id") or (data.get("edge") or {}).get("id") or "edge").strip()
    evaluation_id = f"edge-eval-{slugify(edge_id)}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{hashlib.sha1(os.urandom(16)).hexdigest()[:6]}"
    job_path = edge_handoff_evaluation_path(sop, evaluation_id)
    initial = {
        "ok": False,
        "status": "queued",
        "mode": "edge-handoff-agent-evaluation-job",
        "evaluation_id": evaluation_id,
        "sop_id": sop.get("id", ""),
        "workflow_id": workflow_id,
        "edge_id": edge_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_edge_handoff_evaluation(job_path, initial)
    thread = threading.Thread(
        target=run_edge_handoff_evaluation_job,
        args=(dict(sop), workflow_id, dict(data), evaluation_id, job_path),
        daemon=True,
    )
    thread.start()
    return {
        **initial,
        "poll_url": f"/api/sop/{quote(str(sop.get('id') or sop.get('instance_id') or ''))}/workflows/{quote(str(workflow_id))}/edges/evaluations/{quote(evaluation_id)}",
    }


def workflow_edge_skill_meta(node):
    node = node if isinstance(node, dict) else {}
    executor = node.get("executor") if isinstance(node.get("executor"), dict) else {}
    return {
        "node_id": node.get("node_id") or "",
        "title": node.get("title") or node.get("node_id") or "",
        "skill_id": node.get("skill_id") or executor.get("skill") or node.get("node_id") or "",
        "description": node.get("skill_summary") or "",
        "version": node.get("version") or "",
        "inputs": node.get("inputs") if isinstance(node.get("inputs"), dict) else {},
        "optional_inputs": node.get("optional_inputs") if isinstance(node.get("optional_inputs"), dict) else {},
        "outputs": node.get("outputs") if isinstance(node.get("outputs"), dict) else {},
        "capabilities": node.get("capabilities") if isinstance(node.get("capabilities"), dict) else {},
        "side_effects": {
            "telegram": bool((node.get("capabilities") or {}).get("telegram")) or "tg-notify" in str(node.get("skill_id") or ""),
            "git": bool((node.get("capabilities") or {}).get("git")),
            "external_api": bool((node.get("capabilities") or {}).get("http") or (node.get("capabilities") or {}).get("worker")),
        },
    }


def workflow_edge_detail_payload(sop, workflow_id, data):
    request_payload = edge_handoff_request_payload(sop, workflow_id, data)
    upstream = request_payload.get("upstream") if isinstance(request_payload.get("upstream"), dict) else {}
    downstream = request_payload.get("downstream") if isinstance(request_payload.get("downstream"), dict) else {}
    edge = request_payload.get("edge") if isinstance(request_payload.get("edge"), dict) else {}
    return {
        "ok": True,
        "sop_id": sop.get("id", ""),
        "workflow_id": request_payload.get("workflow_id", ""),
        "edge_id": request_payload.get("edge_id", ""),
        "edge": edge,
        "upstream": workflow_edge_skill_meta(upstream),
        "downstream": workflow_edge_skill_meta(downstream),
        "request": request_payload,
    }


def contract_value_type(spec):
    spec = spec if isinstance(spec, dict) else {}
    return str(spec.get("value_type") or spec.get("content_type") or spec.get("kind") or spec.get("type") or "text")


def workflow_edge_generated_fixture_value(name, spec):
    spec = spec if isinstance(spec, dict) else {}
    value_type = contract_value_type(spec)
    lowered = f"{name} {value_type}".lower()
    if "url" in lowered:
        return "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    if "json" in lowered or "metadata" in lowered:
        return {
            "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "title": "Generated fixture video title",
            "description": "Synthetic metadata generated for Edge Handoff Simulation.",
        }
    if "markdown" in lowered or name.endswith("_file"):
        return "# Generated fixture\n\nThis is a synthetic upstream artifact for Edge Handoff Simulation."
    return f"generated fixture value for {name}"


def workflow_edge_generated_fixture(upstream):
    outputs = upstream.get("outputs") if isinstance(upstream.get("outputs"), dict) else {}
    items = []
    for output_name, spec in outputs.items():
        spec = spec if isinstance(spec, dict) else {}
        value = workflow_edge_generated_fixture_value(output_name, spec)
        value_type = contract_value_type(spec)
        kind = str(spec.get("kind") or spec.get("type") or ("file" if str(spec.get("path") or "").strip() else "scalar"))
        path = str(spec.get("path") or f"raw/node-runs/{{source_node_run_id}}/outputs/outputs/{output_name}.txt")
        items.append({
            "name": output_name,
            "kind": kind,
            "value_type": value_type,
            "path": path,
            "value": value,
            "source": "generated-fixture",
        })
    return {
        "source": "generated-fixture",
        "items": items,
    }


def workflow_edge_instruction_text(data, edge):
    edge = edge if isinstance(edge, dict) else {}
    return str(
        data.get("edge_handoff_instruction")
        or data.get("instruction")
        or edge.get("instruction")
        or ""
    ).strip()


def workflow_edge_evaluation_relay_mappings(data):
    data = data if isinstance(data, dict) else {}
    candidates = []
    for source in (
        data.get("resolved_handoff"),
        data.get("evaluation"),
        (data.get("evaluation") or {}).get("resolved_handoff") if isinstance(data.get("evaluation"), dict) else None,
    ):
        if not isinstance(source, dict):
            continue
        candidates.extend([
            source.get("relay_mappings"),
            source.get("relayMappings"),
            source.get("mappings"),
        ])
    for candidate in candidates:
        rows = normalize_relay_mappings(candidate)
        if rows:
            return rows
    return []


def workflow_edge_simulation_mappings(sop, data, upstream, downstream):
    data = data if isinstance(data, dict) else {}
    edge = data.get("edge") if isinstance(data.get("edge"), dict) else {}
    edge_relay = edge.get("relay") if isinstance(edge.get("relay"), dict) else {}
    relay_mode = str(
        data.get("relay_mode")
        or data.get("relayMode")
        or edge.get("relayMode")
        or edge.get("relay_mode")
        or edge_relay.get("mode")
        or "auto_by_target_inputs"
    ).strip()
    explicit = normalize_relay_mappings(data.get("relay_mappings") or data.get("relayMappings") or [])
    if explicit:
        return explicit
    edge_mappings = normalize_relay_mappings(edge.get("relayMappings") or edge.get("relay_mappings") or [])
    if edge_mappings:
        return edge_mappings
    relay_mappings = normalize_relay_mappings(edge_relay.get("mappings") or edge_relay.get("relay_mappings") or [])
    if relay_mappings:
        return relay_mappings
    edge_bindings = edge_contract_relay_mappings(sop, edge)
    if edge_bindings:
        return edge_bindings
    source_node = str(upstream.get("node_id") or "").strip()
    target_node = str(downstream.get("node_id") or "").strip()
    sop_edge = workflow_edge_contract(sop, source_node, target_node) if source_node and target_node else {}
    sop_edge_mappings = edge_contract_relay_mappings(sop, sop_edge)
    if sop_edge_mappings:
        return sop_edge_mappings
    outputs = upstream.get("outputs") if isinstance(upstream.get("outputs"), dict) else {}
    required = downstream.get("inputs") if isinstance(downstream.get("inputs"), dict) else {}
    optional = downstream.get("optional_inputs") if isinstance(downstream.get("optional_inputs"), dict) else {}
    mappings = []
    evaluation_mappings = workflow_edge_evaluation_relay_mappings(data)
    if evaluation_mappings:
        return evaluation_mappings
    target_inputs = {**optional, **required}
    for input_name, spec in target_inputs.items():
        spec = spec if isinstance(spec, dict) else {}
        source_node_id, declared_output = source_ref_output(spec.get("from"))
        source_output = ""
        if declared_output and declared_output in outputs and (not source_node_id or source_node_id == source_node):
            source_output = declared_output
        elif input_name in outputs:
            source_output = input_name
        elif input_name == "source_url" and "source_url" in outputs:
            source_output = "source_url"
        if source_output:
            mappings.append({
                "source_output": source_output,
                "target_input": input_name,
                "resolver": "direct",
            })
            continue
    return mappings


def workflow_edge_simulation_apply_resolver(source_item, target_spec, selected_resolver=""):
    source_item = source_item if isinstance(source_item, dict) else {}
    target_spec = target_spec if isinstance(target_spec, dict) else {}
    value = source_item.get("value")
    source_path = str(source_item.get("path") or "")
    resolvers = node_input_resolvers(target_spec)
    if selected_resolver:
        preferred = []
        fallback = []
        for resolver in resolvers:
            resolver_id = str(resolver.get("id") or resolver.get("name") or resolver.get("kind") or resolver.get("type") or "").strip()
            if resolver_id == selected_resolver:
                preferred.append(resolver)
            else:
                fallback.append(resolver)
        if preferred:
            resolvers = preferred
        elif selected_resolver in {"direct", "json_path", "regex", "manifest_item"}:
            resolvers = [{"kind": selected_resolver}]
    if not resolvers:
        resolvers = [{"kind": "direct"}]
    content = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value or "")
    last_reason = ""
    for resolver in resolvers:
        kind = str(resolver.get("kind") or resolver.get("type") or "").strip() or "direct"
        resolver_id = str(resolver.get("id") or resolver.get("name") or kind).strip()
        resolved = None
        if kind == "direct":
            resolved = source_path if str(target_spec.get("kind") or target_spec.get("type") or "").strip() in {"file", "files", "directory"} and source_path else value
        elif kind in {"whole_file", "text"}:
            resolved = content
        elif kind == "json_path":
            try:
                parsed = value if isinstance(value, (dict, list)) else json.loads(content)
                resolved = json_path_lookup(parsed, resolver.get("path"))
            except Exception:
                resolved = None
        elif kind == "regex":
            pattern = str(resolver.get("pattern") or "").strip()
            if pattern:
                try:
                    match = re.search(pattern, content)
                except re.error:
                    match = None
                if match:
                    resolved = match.group(1) if match.groups() else match.group(0)
        elif kind == "manifest_item":
            resolved = source_path or value
        if resolved is None:
            last_reason = f"resolver {resolver_id} did not match"
            continue
        if isinstance(resolved, (dict, list)):
            resolved = json.dumps(resolved, ensure_ascii=False)
        ok, reason = validate_resolved_input_value(resolved, target_spec)
        if ok:
            return resolved, resolver_id, ""
        last_reason = reason
    return "", selected_resolver or "", last_reason or "no resolver matched this input"


def workflow_edge_resolve_simulation_inputs(fixture, downstream, mappings):
    by_name = {str(item.get("name") or ""): item for item in fixture.get("items", []) if isinstance(item, dict)}
    required = downstream.get("inputs") if isinstance(downstream.get("inputs"), dict) else {}
    optional = downstream.get("optional_inputs") if isinstance(downstream.get("optional_inputs"), dict) else {}
    target_specs = {**optional, **required}
    rows = []
    missing = []
    for mapping in mappings:
        source_output = str(mapping.get("source_output") or "")
        target_input = str(mapping.get("target_input") or source_output)
        source_item = by_name.get(source_output)
        required_flag = target_input in required
        target_spec = target_specs.get(target_input) if isinstance(target_specs.get(target_input), dict) else {}
        resolved_value, resolver_id, reason = workflow_edge_simulation_apply_resolver(source_item, target_spec, str(mapping.get("resolver") or "")) if source_item else ("", str(mapping.get("resolver") or ""), "source output is not available")
        resolved = source_item is not None and not is_blank_value(resolved_value)
        row = {
            "target_input": target_input,
            "source_output": source_output,
            "resolver": resolver_id or mapping.get("resolver") or "direct",
            "required": required_flag,
            "resolved": resolved,
            "value": resolved_value if resolved else "",
            "value_preview": edge_handoff_compact_text(json.dumps(resolved_value, ensure_ascii=False) if isinstance(resolved_value, (dict, list)) else resolved_value, 240) if resolved else "",
            "source_path": source_item.get("path") if source_item else "",
            "target_spec": target_spec,
            "reason": "" if resolved else reason,
        }
        rows.append(row)
    by_target = {}
    for row in rows:
        target = str(row.get("target_input") or "")
        if not target:
            continue
        by_target.setdefault(target, []).append(row)
    target_resolutions = []
    fallback_failures = []
    mapped_targets = set(by_target.keys())
    for input_name in required.keys():
        if input_name not in mapped_targets:
            missing.append({
                "input": input_name,
                "reason": "No relay mapping resolved this required downstream input.",
            })
            target_resolutions.append({
                "target_input": input_name,
                "required": True,
                "resolved": False,
                "attempts": [],
                "reason": "No relay mapping resolved this required downstream input.",
            })
    for target_input, attempts in by_target.items():
        required_flag = target_input in required
        success = next((row for row in attempts if row.get("resolved")), None)
        failed = [row for row in attempts if not row.get("resolved")]
        if success:
            for row in failed:
                fallback_failures.append({
                    "target_input": target_input,
                    "source_output": row.get("source_output"),
                    "resolver": row.get("resolver"),
                    "reason": row.get("reason") or "fallback mapping did not resolve",
                    "blocking": False,
                })
        elif required_flag:
            reason = "; ".join([
                f"{row.get('source_output') or '?'} via {row.get('resolver') or 'resolver'}: {row.get('reason') or 'not resolved'}"
                for row in attempts
            ]) or "No relay mapping resolved this required downstream input."
            missing.append({
                "input": target_input,
                "reason": reason,
            })
        else:
            for row in failed:
                fallback_failures.append({
                    "target_input": target_input,
                    "source_output": row.get("source_output"),
                    "resolver": row.get("resolver"),
                    "reason": row.get("reason") or "optional mapping did not resolve",
                    "blocking": False,
                })
        target_resolutions.append({
            "target_input": target_input,
            "required": required_flag,
            "resolved": bool(success),
            "resolved_source_output": success.get("source_output") if success else "",
            "resolver": success.get("resolver") if success else "",
            "attempts": attempts,
            "reason": "" if success else (missing[-1].get("reason") if required_flag and missing else "not resolved"),
        })
    return rows, missing, fallback_failures, target_resolutions


def workflow_edge_relay_package(request_payload, fixture, resolved_inputs, missing, fallback_failures=None, target_resolutions=None):
    fallback_failures = fallback_failures if isinstance(fallback_failures, list) else []
    target_resolutions = target_resolutions if isinstance(target_resolutions, list) else []
    return {
        "edge_id": request_payload.get("edge_id") or "",
        "source_node": (request_payload.get("upstream") or {}).get("node_id") if isinstance(request_payload.get("upstream"), dict) else "",
        "target_node": (request_payload.get("downstream") or {}).get("node_id") if isinstance(request_payload.get("downstream"), dict) else "",
        "input_source": "generated-fixture",
        "status": "passed" if not missing else "blocked",
        "items": [
            {
                "source_output": row.get("source_output"),
                "target_input": row.get("target_input"),
                "resolver": row.get("resolver"),
                "source_path": row.get("source_path"),
                "value_preview": row.get("value_preview"),
                "resolved": row.get("resolved"),
            }
            for row in resolved_inputs
        ],
        "missing_inputs": missing,
        "fallback_failures": fallback_failures,
        "target_resolutions": target_resolutions,
        "fixture": fixture,
    }


def workflow_edge_hermes_request_preview(request_payload, relay_package, evaluation=None):
    evaluation = evaluation if isinstance(evaluation, dict) else {}
    guide = evaluation.get("node_execution_guide") if isinstance(evaluation.get("node_execution_guide"), dict) else {}
    guide_prompt = str(guide.get("prompt") or "").strip() or "No approved Node Execution Guide is available yet. Run Edge Handoff Agent before a real node run."
    downstream = request_payload.get("downstream") if isinstance(request_payload.get("downstream"), dict) else {}
    skill_name = str(downstream.get("skill_id") or downstream.get("node_id") or "downstream-skill")
    return "\n".join([
        f"Use skill {skill_name} to execute this Node Execution Request.",
        "",
        "# Edge Handoff Simulation Request",
        "",
        "Runtime context:",
        f"- runtime_id: {request_payload.get('runtime_id') or ''}",
        f"- instance_id: {request_payload.get('instance_id') or ''}",
        f"- workflow_id: {request_payload.get('workflow_id') or ''}",
        f"- edge_id: {request_payload.get('edge_id') or ''}",
        "",
        "Edge:",
        f"- upstream: {(request_payload.get('upstream') or {}).get('node_id') if isinstance(request_payload.get('upstream'), dict) else ''}",
        f"- downstream: {downstream.get('node_id') or ''}",
        f"- instruction: {request_payload.get('edge_handoff_instruction') or ''}",
        "",
        "Resolved relay package:",
        "```json",
        json.dumps(mask_data(relay_package), ensure_ascii=False, indent=2),
        "```",
        "",
        "Approved Edge Handoff Guide:",
        guide_prompt,
        "",
        "Simulation rule:",
        "- This is a handoff simulation only. Do not execute the real downstream node.",
    ]).strip() + "\n"


def workflow_edge_probe_prompt(request_payload, relay_package, evaluation=None):
    evaluation = evaluation if isinstance(evaluation, dict) else {}
    guide = evaluation.get("node_execution_guide") if isinstance(evaluation.get("node_execution_guide"), dict) else {}
    guide_prompt = str(guide.get("prompt") or "").strip() or "No approved Node Execution Guide is available yet."
    downstream = request_payload.get("downstream") if isinstance(request_payload.get("downstream"), dict) else {}
    upstream = request_payload.get("upstream") if isinstance(request_payload.get("upstream"), dict) else {}
    return "\n".join([
        "You are the downstream SOP Agent running a Handoff Probe.",
        "Do not execute the real downstream skill. Do not call external APIs. Do not write files.",
        "Only inspect the supplied upstream fixture, relay package, Edge instruction, and Node Execution Guide.",
        "Return strict JSON only with keys: status, understood_primary_input, understood_supporting_inputs, missing_inputs, risks, downstream_execution_plan, should_run_real_node, summary.",
        "",
        "Runtime context:",
        f"- runtime_id: {request_payload.get('runtime_id') or ''}",
        f"- instance_id: {request_payload.get('instance_id') or ''}",
        f"- workflow_id: {request_payload.get('workflow_id') or ''}",
        f"- edge_id: {request_payload.get('edge_id') or ''}",
        "",
        "Edge:",
        f"- upstream: {upstream.get('node_id') or ''}",
        f"- downstream: {downstream.get('node_id') or ''}",
        f"- instruction: {request_payload.get('edge_handoff_instruction') or ''}",
        "",
        "Downstream skill:",
        f"- skill_id: {downstream.get('skill_id') or downstream.get('node_id') or ''}",
        f"- title: {downstream.get('title') or ''}",
        f"- required_inputs: {json.dumps(downstream.get('inputs') or {}, ensure_ascii=False)}",
        "",
        "Relay package:",
        "```json",
        json.dumps(mask_data(relay_package), ensure_ascii=False, indent=2),
        "```",
        "",
        "Node Execution Guide:",
        guide_prompt,
    ]).strip() + "\n"


def deterministic_handoff_probe(request_payload, relay_package, evaluation=None, reason=""):
    missing = relay_package.get("missing_inputs") if isinstance(relay_package.get("missing_inputs"), list) else []
    resolved = relay_package.get("resolved_inputs") if isinstance(relay_package.get("resolved_inputs"), list) else []
    primary = resolved[0] if resolved else {}
    status = "blocked" if missing else "passed"
    risks = []
    if reason:
        risks.append(reason)
    if not (evaluation or {}).get("node_execution_guide"):
        risks.append("No approved Node Execution Guide was supplied to the probe.")
        if status == "passed":
            status = "needs_review"
    return {
        "status": status,
        "understood_primary_input": primary,
        "understood_supporting_inputs": resolved[1:],
        "missing_inputs": missing,
        "risks": risks,
        "downstream_execution_plan": "Use the resolved primary input from the relay package, then execute the downstream skill only after this probe is accepted.",
        "should_run_real_node": status == "passed",
        "summary": "Deterministic probe result based on resolved relay inputs.",
    }


def parse_probe_agent_json(raw_text):
    raw_text = str(raw_text or "").strip()
    if not raw_text:
        return {}
    try:
        parsed = json.loads(raw_text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        match = re.search(r"\{[\s\S]*\}", raw_text)
        if match:
            try:
                parsed = json.loads(match.group(0))
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
    return {}


def run_workflow_edge_handoff_probe(sop, data, request_payload, relay_package, evaluation, hermes_request):
    evaluator_env, evaluator_config = edge_handoff_evaluator_env(sop, data)
    missing_config = []
    base_url = str(
        evaluator_env.get("EDGE_HANDOFF_LLM_BASE_URL")
        or evaluator_env.get("HERMES_MODEL_BASE_URL")
        or evaluator_env.get("WIKI_LLM_BASE_URL")
        or evaluator_config.get("base_url", {}).get("value")
        or ""
    ).rstrip("/")
    api_key = str(
        evaluator_env.get("EDGE_HANDOFF_LLM_API_KEY")
        or evaluator_env.get("HERMES_OPENAI_API_KEY")
        or evaluator_env.get("OPENAI_API_KEY")
        or evaluator_env.get("WIKI_LLM_API_KEY")
        or ""
    ).strip()
    model = str(
        evaluator_env.get("EDGE_HANDOFF_LLM_MODEL")
        or evaluator_env.get("HERMES_MODEL")
        or evaluator_env.get("WIKI_DEEPSEEK_MODEL")
        or evaluator_env.get("WIKI_LLM_MODEL")
        or evaluator_config.get("model", {}).get("value")
        or ""
    ).strip() or "deepseek-v4-flash"
    if not base_url:
        missing_config.append("base_url")
    if not api_key:
        missing_config.append("api_key")
    probe_prompt = workflow_edge_probe_prompt(request_payload, relay_package, evaluation)
    if missing_config:
        probe = deterministic_handoff_probe(
            request_payload,
            relay_package,
            evaluation,
            reason=f"Handoff Probe LLM config missing: {', '.join(missing_config)}",
        )
        probe["status"] = "blocked"
        probe["should_run_real_node"] = False
        return probe, {
            "provider": "openai-compatible",
            "model": model,
            "used_ai": False,
            "config": evaluator_config,
            "error": f"Handoff Probe LLM config missing: {', '.join(missing_config)}",
            "prompt": probe_prompt,
            "response": "",
        }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a precise SOP handoff probe agent. Return strict JSON only."},
            {"role": "user", "content": probe_prompt},
        ],
        "temperature": 0,
        "max_tokens": int(evaluator_env.get("EDGE_HANDOFF_LLM_MAX_TOKENS", "2048") or "2048"),
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    started = time.time()
    def post_probe(payload):
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": evaluator_env.get("EDGE_HANDOFF_LLM_USER_AGENT") or evaluator_env.get("WIKI_LLM_USER_AGENT") or "curl/8.0.1",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=int(evaluator_env.get("EDGE_HANDOFF_LLM_TIMEOUT", "22") or "22")) as response:
            return response.read().decode("utf-8", errors="replace")
    try:
        try:
            response_text = post_probe(body)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            if exc.code in {400, 422} and "response_format" in detail:
                retry_body = dict(body)
                retry_body.pop("response_format", None)
                response_text = post_probe(retry_body)
            else:
                raise urllib.error.HTTPError(exc.url, exc.code, detail or exc.reason, exc.headers, None) from exc
    except Exception as exc:
        probe = deterministic_handoff_probe(request_payload, relay_package, evaluation, reason=str(exc))
        probe["status"] = "blocked"
        probe["should_run_real_node"] = False
        return probe, {
            "provider": "openai-compatible",
            "model": model,
            "used_ai": False,
            "config": evaluator_config,
            "error": str(exc),
            "prompt": probe_prompt,
            "response": "",
            "latency_ms": int((time.time() - started) * 1000),
        }
    parsed_response = {}
    try:
        response_json = json.loads(response_text)
        choices = response_json.get("choices") if isinstance(response_json, dict) else []
        if choices and isinstance(choices[0], dict):
            parsed_response = parse_probe_agent_json(((choices[0].get("message") or {}).get("content") or choices[0].get("text") or ""))
    except Exception:
        parsed_response = parse_probe_agent_json(response_text)
    if not parsed_response:
        parsed_response = deterministic_handoff_probe(request_payload, relay_package, evaluation, reason="Probe agent did not return parseable JSON.")
        parsed_response["status"] = "needs_review"
    status = str(parsed_response.get("status") or "").strip()
    if status not in {"passed", "needs_review", "blocked"}:
        parsed_response["status"] = "needs_review"
    parsed_response.setdefault("missing_inputs", relay_package.get("missing_inputs") or [])
    parsed_response.setdefault("risks", [])
    if parsed_response.get("status") == "passed" and not parsed_response.get("missing_inputs"):
        parsed_response["should_run_real_node"] = True
    else:
        parsed_response.setdefault("should_run_real_node", parsed_response.get("status") == "passed")
    if parsed_response.get("status") == "needs_review" and parsed_response.get("should_run_real_node") is True and not parsed_response.get("missing_inputs"):
        parsed_response["status"] = "passed"
    parsed_response.setdefault("summary", "Handoff Probe completed.")
    return parsed_response, {
        "provider": "openai-compatible",
        "model": model,
        "used_ai": True,
        "config": evaluator_config,
        "prompt": probe_prompt,
        "response": response_text[-8000:],
        "latency_ms": int((time.time() - started) * 1000),
    }


def simulate_workflow_edge_handoff(sop, workflow_id, data):
    data = data if isinstance(data, dict) else {}
    if workflow_id and not workflow_id_matches(sop, workflow_id):
        return 404, {
            "ok": False,
            "status": "blocked",
            "detail": f"Workflow {workflow_id!r} does not exist on this SOP instance.",
            "workflow_id": workflow_id,
        }
    sop, runtime_sop_path, snapshot_error = load_workflow_runtime_sop_snapshot(sop, data)
    if snapshot_error:
        return 422, snapshot_error
    request_payload = edge_handoff_request_payload(sop, workflow_id, data)
    upstream = request_payload.get("upstream") if isinstance(request_payload.get("upstream"), dict) else {}
    downstream = request_payload.get("downstream") if isinstance(request_payload.get("downstream"), dict) else {}
    if not upstream.get("node_id") or not downstream.get("node_id"):
        return 422, {
            "ok": False,
            "status": "blocked",
            "detail": "upstream_node_id and downstream_node_id are required",
            "request": request_payload,
        }
    input_source = str((data or {}).get("input_source") or (data or {}).get("source_mode") or "generated-fixture")
    if input_source == "generated_fixture":
        input_source = "generated-fixture"
    if input_source not in {"generated-fixture", "generated_fixture", "manual", "existing-node-run"}:
        input_source = "generated-fixture"
    if input_source != "generated-fixture":
        return 422, {
            "ok": False,
            "status": "blocked",
            "detail": "Only generated-fixture handoff simulation is implemented in this non-executing API.",
            "request": request_payload,
            "supported_input_sources": ["generated-fixture"],
        }
    fixture = workflow_edge_generated_fixture(upstream)
    mappings = workflow_edge_simulation_mappings(sop, data, upstream, downstream)
    resolved_inputs, missing, fallback_failures, target_resolutions = workflow_edge_resolve_simulation_inputs(fixture, downstream, mappings)
    relay_package = workflow_edge_relay_package(request_payload, fixture, resolved_inputs, missing, fallback_failures, target_resolutions)
    evaluation = data.get("evaluation") if isinstance(data.get("evaluation"), dict) else {}
    hermes_request = workflow_edge_hermes_request_preview(request_payload, relay_package, evaluation)
    probe_requested = str(data.get("mode") or data.get("simulation_mode") or data.get("probe_mode") or "").replace("_", "-") in {"handoff-probe", "probe"} or bool(data.get("handoff_probe"))
    status = "passed" if not missing else "blocked"
    probe_result = {}
    probe_trace = {}
    if probe_requested and not missing:
        probe_result, probe_trace = run_workflow_edge_handoff_probe(sop, data, request_payload, relay_package, evaluation, hermes_request)
        status = str(probe_result.get("status") or status)
    return 200, {
        "ok": status == "passed",
        "mode": "edge-handoff-probe" if probe_requested else "edge-handoff-simulation",
        "simulation_target": "runtime-sop" if runtime_sop_path else "edge-draft",
        "runtime_sop_path": runtime_sop_path,
        "simulation_id": f"{slugify(request_payload.get('edge_id') or 'edge')}-simulation-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        "status": status,
        "verdict": "can_handoff" if status == "passed" else ("needs_review" if status == "needs_review" else "missing_required_input"),
        "sop_id": sop.get("id", ""),
        "workflow_id": request_payload.get("workflow_id", ""),
        "edge_id": request_payload.get("edge_id", ""),
        "request": request_payload,
        "skill_meta": {
            "upstream": workflow_edge_skill_meta(upstream),
            "downstream": workflow_edge_skill_meta(downstream),
        },
        "generated_fixture": fixture,
        "relay_mappings": mappings,
        "relay_package": relay_package,
        "resolved_inputs": resolved_inputs,
        "target_resolutions": target_resolutions,
        "fallback_failures": fallback_failures,
        "missing_inputs": missing,
        "hermes_request_preview": {
            "format": "markdown",
            "prompt": hermes_request,
            "source": "edge-handoff-probe" if probe_requested else "edge-handoff-simulation",
            "executes_real_node": False,
        },
        "handoff_probe": probe_result,
        "probe_trace": probe_trace,
        "warnings": (
            ["Simulation did not satisfy every required downstream input."] if missing else []
        ) + ([
            "Some fallback relay mappings did not resolve, but required inputs were satisfied."
        ] if fallback_failures and not missing else []),
        "blocking_reasons": missing,
    }


def relay_context_brief(plan, input_validation=None):
    selection = plan.get("relay_selection") if isinstance(plan.get("relay_selection"), dict) else {}
    edge = selection.get("edge_contract") if isinstance(selection.get("edge_contract"), dict) else plan.get("edge_contract") if isinstance(plan.get("edge_contract"), dict) else {}
    intent = edge.get("intent") if isinstance(edge.get("intent"), dict) else {}
    relay_instruction = str(plan.get("relay_instruction") or "").strip()
    matched = selection.get("matched_items") if isinstance(selection.get("matched_items"), list) else []
    errors = (input_validation or {}).get("errors") if isinstance(input_validation, dict) else []
    lines = []
    if edge.get("id"):
        lines.append(f"Edge: {edge.get('id')} ({edge.get('from')} -> {edge.get('to')})")
    if intent.get("title"):
        lines.append(f"Intent: {intent.get('title')}")
    if intent.get("brief"):
        lines.append(f"Instruction: {intent.get('brief')}")
    if relay_instruction:
        lines.append(f"Run instruction: {relay_instruction}")
    if matched:
        lines.append("Bindings:")
        for item in matched:
            source = item.get("source_output") or item.get("output") or ""
            target = item.get("target_input") or item.get("input_name") or ""
            resolver = item.get("resolver") or "auto"
            source_path = item.get("source_path") or item.get("materialized_path") or ""
            lines.append(f"- {source} -> {target} via {resolver}; path={source_path}")
    if errors:
        lines.append("Resolution errors:")
        for error in errors:
            if isinstance(error, dict):
                lines.append(f"- {error.get('input') or 'input'}: {error.get('reason') or error.get('error') or 'failed'}")
    return "\n".join(lines).strip()


def relay_resolution_trace(plan, materialized_items, input_validation):
    selection = plan.get("relay_selection") if isinstance(plan.get("relay_selection"), dict) else {}
    edge = selection.get("edge_contract") if isinstance(selection.get("edge_contract"), dict) else plan.get("edge_contract") if isinstance(plan.get("edge_contract"), dict) else {}
    errors = input_validation.get("errors") if isinstance(input_validation, dict) else []
    errors_by_input = {}
    for error in errors or []:
        if isinstance(error, dict):
            errors_by_input.setdefault(str(error.get("input") or ""), []).append(error)
    resolutions = input_validation.get("resolutions") if isinstance(input_validation, dict) else []
    resolved_by_input = {}
    for row in resolutions or []:
        if isinstance(row, dict):
            resolved_by_input.setdefault(str(row.get("input") or ""), []).append(row)
    trace = []
    for item in materialized_items or []:
        target = str(item.get("target_input") or item.get("input_name") or item.get("source_output") or "").strip()
        source = str(item.get("source_output") or item.get("output") or "").strip()
        status = "failed" if errors_by_input.get(target) else "resolved" if resolved_by_input.get(target) or item.get("materialized_path") else "matched"
        trace.append({
            "edge_id": edge.get("id") or "",
            "source_node": item.get("source_node") or selection.get("source_node") or "",
            "source_run_id": item.get("source_run_id") or item.get("source_node_run_id") or selection.get("source_node_run_id") or "",
            "source_output": source,
            "source_path": item.get("source_path") or "",
            "target_node": plan.get("node_id") or selection.get("target_node_id") or "",
            "target_input": target,
            "resolver": item.get("resolver") or "",
            "status": status,
            "materialized_path": item.get("materialized_path") or "",
            "value_preview": item.get("value_preview") or item.get("resolved_value") or "",
            "errors": errors_by_input.get(target) or [],
        })
    for error in errors or []:
        target = str((error or {}).get("input") or "")
        if target and not any(row.get("target_input") == target for row in trace):
            trace.append({
                "edge_id": edge.get("id") or "",
                "target_node": plan.get("node_id") or selection.get("target_node_id") or "",
                "target_input": target,
                "status": "failed",
                "errors": [error],
            })
    return trace


def relay_context_payload(plan, materialized_items, input_validation):
    selection = plan.get("relay_selection") if isinstance(plan.get("relay_selection"), dict) else {}
    edge = selection.get("edge_contract") if isinstance(selection.get("edge_contract"), dict) else plan.get("edge_contract") if isinstance(plan.get("edge_contract"), dict) else {}
    trace = relay_resolution_trace(plan, materialized_items, input_validation)
    return {
        "edge_id": edge.get("id") or "",
        "source_node": selection.get("source_node") or edge.get("from") or "",
        "source_node_run_id": selection.get("source_node_run_id") or plan.get("source_node_run_id") or "",
        "target_node": plan.get("node_id") or selection.get("target_node_id") or edge.get("to") or "",
        "status": (input_validation or {}).get("status") or "unknown",
        "brief": relay_context_brief(plan, input_validation),
        "items": trace,
    }


def node_input_binding_specs(sop, node_id):
    config = (sop.get("nodes") or {}).get(node_id) or {}
    required = normalize_contract(config.get("inputs") or {}, "input")
    optional = normalize_contract(config.get("optional_inputs") or {}, "input")
    rows = []
    for required_flag, source_map in ((True, required), (False, optional)):
        for input_name, spec in source_map.items():
            source = spec.get("from") if isinstance(spec, dict) else spec
            source_node, source_output = source_ref_output(source)
            rows.append({
                "target_input": input_name,
                "required": required_flag,
                "source": str(source or ""),
                "source_node": source_node,
                "source_output": source_output,
                "spec": spec,
            })
    return rows


def node_run_relay_selection_plan(sop, target_node_id, source_node_run_id, relay_mode="", selected_outputs=None, relay_mappings=None):
    source_items = node_run_source_manifest_items(sop, source_node_run_id)
    selected_outputs = normalize_selected_outputs(selected_outputs or [])
    relay_mappings = normalize_relay_mappings(relay_mappings or [])
    relay_mode = normalize_node_run_relay_mode(relay_mode, "existing-node-run")
    source_node = next((str(item.get("source_node") or "") for item in source_items if item.get("source_node")), "")
    edge = workflow_edge_contract(sop, source_node, target_node_id) if source_node else {}
    edge_mappings = edge_contract_relay_mappings(sop, edge) if edge else []
    edge_applied = False
    if edge_mappings and not selected_outputs and not relay_mappings:
        relay_mappings = normalize_relay_mappings(edge_mappings)
        selected_outputs = normalize_selected_outputs([item.get("source_output") for item in relay_mappings])
        relay_mode = "selected_outputs"
        edge_applied = True
    bindings = node_input_binding_specs(sop, target_node_id)
    static = node_static_config(sop, target_node_id) or {}
    required_contract = normalize_contract(static.get("inputs") or {}, "input")
    required_target_inputs = [
        name for name, spec in required_contract.items()
        if bool((spec or {}).get("required", True))
    ]
    input_by_output = {}
    for binding in bindings:
        source_output = binding.get("source_output") or ""
        if not source_output:
            continue
        if source_node and binding.get("source_node") and binding.get("source_node") != source_node:
            continue
        input_by_output.setdefault(source_output, binding.get("target_input") or source_output)
    for binding in bindings:
        target_input = binding.get("target_input") or ""
        if target_input:
            input_by_output.setdefault(target_input, target_input)
    mapping_by_output = {
        item["source_output"]: item
        for item in relay_mappings
        if item.get("source_output")
    }

    if relay_mode == "all_outputs":
        allowed = {str(item.get("output") or item.get("source_output") or "") for item in source_items}
        reason = "explicit-all-outputs"
    elif relay_mode == "selected_outputs":
        allowed = set(mapping_by_output.keys()) or set(selected_outputs)
        reason = "edge-contract" if edge_applied else "explicit-selected-outputs"
    else:
        allowed = set(input_by_output.keys())
        reason = "target-input-binding"
        if not allowed and len(source_items) == 1:
            only = str(source_items[0].get("output") or source_items[0].get("source_output") or "")
            if only:
                allowed.add(only)
                reason = "single-source-output"

    matched = []
    skipped = []
    for item in source_items:
        output_name = str(item.get("output") or item.get("source_output") or "").strip()
        if relay_mode == "all_outputs" or (output_name and output_name in allowed):
            explicit = mapping_by_output.get(output_name) or {}
            if relay_mode == "all_outputs" and len(required_target_inputs) == 1:
                target_input = explicit.get("target_input") or required_target_inputs[0]
            else:
                target_input = explicit.get("target_input") or input_by_output.get(output_name)
            target_input = target_input or output_name
            matched.append({
                **item,
                "target_input": target_input,
                "input_name": target_input,
                "resolver": explicit.get("resolver") or "",
                "relay_match_reason": reason,
            })
        else:
            skipped.append({**item, "relay_skip_reason": "not-selected"})
    available = ordered_unique([str(item.get("output") or item.get("source_output") or "") for item in source_items if item.get("output") or item.get("source_output")])
    invalid_mappings = [
        item for item in relay_mappings
        if item.get("source_output") not in available
    ]
    return {
        "relay_mode": relay_mode,
        "source_node_run_id": sanitize_node_run_id(source_node_run_id),
        "source_node": source_node,
        "edge_contract": public_edge_contract(edge),
        "edge_applied": edge_applied,
        "selected_outputs": selected_outputs,
        "relay_mappings": relay_mappings,
        "target_node_id": target_node_id,
        "bindings": bindings,
        "matched_outputs": ordered_unique([str(item.get("output") or item.get("source_output") or "") for item in matched if item.get("output") or item.get("source_output")]),
        "matched_items": matched,
        "skipped_outputs": ordered_unique([str(item.get("output") or item.get("source_output") or "") for item in skipped if item.get("output") or item.get("source_output")]),
        "available_outputs": available,
        "invalid_mappings": invalid_mappings,
    }


def resolve_node_input(sop, input_name, spec, source_mode, base_run_id="", manual_inputs=None, source_node_run_id="", relay_mode="", selected_outputs=None):
    manual_inputs = manual_inputs if isinstance(manual_inputs, dict) else {}
    source = spec.get("from") if isinstance(spec, dict) else spec
    source = str(source or "")
    item = {
        "name": input_name,
        "source": source,
        "required": bool(spec.get("required", True)) if isinstance(spec, dict) else True,
        "resolved": False,
        "value": None,
        "provenance": "",
        "reason": "",
    }
    if input_name in manual_inputs and str(manual_inputs[input_name]).strip():
        item.update({"resolved": True, "value": manual_inputs[input_name], "provenance": "manual"})
        return item

    if source_mode == "existing-node-run":
        if source_node_run_id:
            source_node, source_output = source_ref_output(source)
            item.update({
                "resolved": False,
                "value": f"node-run:{source_node_run_id}",
                "provenance": f"node-run:{source_node_run_id}",
                "source_node_run_id": source_node_run_id,
                "source_output": source_output,
                "source_node": source_node,
                "target_input": input_name,
                "relay_mode": normalize_node_run_relay_mode(relay_mode, source_mode),
                "selected_outputs": normalize_selected_outputs(selected_outputs or []),
                "resolution_state": "pending_materialization",
                "reason": "Source Node Run selected; the target input will be resolved from materialized relay outputs before execution.",
            })
            return item
        item["reason"] = "Select a source Node Run so this run can materialize selected relay outputs."
        return item

    if source_mode == "existing-run" and base_run_id:
        context = run_context(sop, base_run_id)
        if source.startswith("context."):
            key = source.split(".", 1)[1]
            value = context.get(key)
            if is_blank_value(value) and key == "source_url":
                value = context.get("url")
            if not is_blank_value(value):
                item.update({"resolved": True, "value": value, "provenance": f"run:{base_run_id}:context.{key}"})
                return item
        match = re.match(r"^([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)$", source)
        if match:
            upstream, output = match.groups()
            value, provenance = node_output_from_state(sop, base_run_id, upstream, output)
            if is_blank_value(value) and output == "source_url":
                value = context.get("source_url") or context.get("url")
                provenance = f"run:{base_run_id}:context.source_url"
            if is_blank_value(value) and upstream == "notebooklm-research" and output == "reports":
                stage_b = context.get("stage_b") if isinstance(context.get("stage_b"), dict) else {}
                value = stage_b.get("output_files")
                provenance = f"run:{base_run_id}:context.stage_b.output_files"
            if is_blank_value(value) and upstream == "youtube-deep-research" and output == "analysis_file":
                stage_b2 = context.get("stage_b2") if isinstance(context.get("stage_b2"), dict) else {}
                value = stage_b2.get("analysis_file")
                provenance = f"run:{base_run_id}:context.stage_b2.analysis_file"
            if not is_blank_value(value):
                item.update({"resolved": True, "value": value, "provenance": provenance})
                return item
    if source_mode in {"generated-fixture", "deepseek-mock"}:
        value = generated_fixture_value(input_name, source, spec if isinstance(spec, dict) else {})
        if value is not None:
            item.update({
                "resolved": True,
                "value": value,
                "provenance": "generated-fixture" if source_mode == "generated-fixture" else "deepseek-mock-fallback",
            })
            return item
        if source_mode == "deepseek-mock":
            item["reason"] = "No deterministic fixture is available; DeepSeek mock generation is not enabled on this runtime."
            return item

    item["reason"] = "No source value resolved. Select an existing run, use generated fixture, or provide manual input."
    return item


def dedupe_node_input_items(items):
    rows = []
    seen = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        key = (
            str(item.get("name") or item.get("target_input") or ""),
            str(item.get("source_node_run_id") or ""),
            str(item.get("source_node") or ""),
            str(item.get("source_output") or ""),
            str(item.get("provenance") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        rows.append(item)
    return rows


def node_test_side_effects(node_id, config, static):
    executor = static.get("executor") if isinstance(static.get("executor"), dict) else {}
    capabilities = static.get("capabilities") if isinstance(static.get("capabilities"), dict) else {}
    skill = str(executor.get("skill") or config.get("skill") or "")

    def cap_enabled(name):
        value = capabilities.get(name)
        if isinstance(value, dict):
            return bool(value.get("enabled", True))
        return bool(value)

    executor_type = str(executor.get("type") or "").lower()
    external = executor_type in {"agent-skill", "http", "public-api"} or any(
        cap_enabled(name) for name in ("llm", "http", "worker", "notebooklm", "ssh")
    )
    llm = cap_enabled("llm")
    telegram = cap_enabled("telegram")
    return {
        "writes_workspace": bool(static.get("outputs")),
        "git_write": cap_enabled("git"),
        "telegram": telegram,
        "external_api": external,
        "llm": llm,
        "skill": skill,
        "default_mode": "preflight",
        "real_execution_enabled": node_real_execution_supported(node_id, {"nodes": {node_id: static}}),
    }


def build_node_test_plan(sop, node_id, body=None):
    body = body if isinstance(body, dict) else {}
    config, _config_source = node_config_for(sop, node_id)
    if not isinstance(config, dict):
        return None
    static = node_static_config(sop, node_id) or {}
    source_mode = str(body.get("input_source") or body.get("source_mode") or "generated-fixture")
    if source_mode not in {"existing-run", "existing-node-run", "generated-fixture", "manual", "deepseek-mock"}:
        source_mode = "generated-fixture"
    base_run_id = sanitize_test_id(body.get("pipeline_id") or body.get("run_id") or body.get("seed_from_run_id") or "")
    source_node_run_id = sanitize_node_run_id(body.get("source_node_run_id") or body.get("node_run_source_id") or body.get("from_node_run_id") or "")
    relay_mode = normalize_node_run_relay_mode(body.get("relay_mode") or body.get("relayMode"), source_mode)
    selected_outputs = normalize_selected_outputs(body.get("selected_outputs") or body.get("selectedOutputs") or [])
    relay_mappings = normalize_relay_mappings(body.get("relay_mappings") or body.get("relayMappings") or [])
    relay_instruction = str(body.get("relay_instruction") or body.get("relayInstruction") or "").strip()
    manual_inputs = body.get("manual_inputs") if isinstance(body.get("manual_inputs"), dict) else {}
    inputs = normalize_contract(static.get("inputs", {}), "input")
    optional_inputs = normalize_contract(static.get("optional_inputs", {}), "input")
    relay_selection = node_run_relay_selection_plan(sop, node_id, source_node_run_id, relay_mode, selected_outputs, relay_mappings) if source_mode == "existing-node-run" and source_node_run_id else {}
    if relay_selection:
        relay_mode = relay_selection.get("relay_mode") or relay_mode
        selected_outputs = relay_selection.get("selected_outputs") or selected_outputs
        relay_mappings = relay_selection.get("relay_mappings") or relay_mappings
    node_execution_guide = resolve_node_execution_guide(sop, node_id, relay_selection) if relay_selection else {}
    workflow_revision = workflow_revision_snapshot(sop)
    resolved_inputs = [resolve_node_input(sop, name, spec, source_mode, base_run_id, manual_inputs, source_node_run_id, relay_mode, selected_outputs) for name, spec in inputs.items()]
    resolved_optional = [resolve_node_input(sop, name, spec, source_mode, base_run_id, manual_inputs, source_node_run_id, relay_mode, selected_outputs) for name, spec in optional_inputs.items()]
    pending_materialization = dedupe_node_input_items([
        item for item in resolved_inputs + resolved_optional
        if item.get("resolution_state") == "pending_materialization"
    ])
    missing = dedupe_node_input_items([
        item for item in resolved_inputs
        if item.get("required") and not item.get("resolved") and item.get("resolution_state") != "pending_materialization"
    ])
    upstream = []
    for spec in list(inputs.values()) + list(optional_inputs.values()):
        source = str((spec or {}).get("from") if isinstance(spec, dict) else spec or "")
        match = re.match(r"^([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)$", source)
        if match:
            upstream.append({"node_id": match.group(1), "output": match.group(2), "source": source})
    recent = recent_run_summaries(sop, limit=20)
    for run in recent:
        statuses = run.get("nodes") or {}
        run["satisfies_upstream"] = all(statuses.get(item["node_id"]) == "done" for item in upstream)
    return {
        "sop_id": sop.get("id", ""),
        "workflow_id": sop.get("id") or sop.get("name") or "",
        "instance_id": sop.get("id", ""),
        "node_id": node_id,
        "node_title": static.get("title") or node_id,
        "mode": "preflight",
        "input_source": source_mode,
        "base_run_id": base_run_id,
        "source_node_run_id": source_node_run_id,
        "relay_mode": relay_mode,
        "selected_outputs": selected_outputs,
        "relay_mappings": relay_mappings,
        "relay_selection": mask_data(relay_selection),
        "edge_contract": (relay_selection or {}).get("edge_contract") or {},
        "node_execution_guide": mask_data(node_execution_guide),
        "workflow_revision": mask_data(workflow_revision),
        "relay_instruction": relay_instruction,
        "relay_context_brief": relay_context_brief({"relay_selection": relay_selection, "node_id": node_id, "relay_instruction": relay_instruction}) if relay_selection or relay_instruction else "",
        "manual_inputs": manual_inputs,
        "required_inputs": resolved_inputs,
        "optional_inputs": resolved_optional,
        "resolved_inputs": dedupe_node_input_items([item for item in resolved_inputs if item.get("resolved")]),
        "pending_materialization_inputs": pending_materialization,
        "missing_inputs": missing,
        "upstream_nodes": upstream,
        "available_existing_runs": recent,
        "side_effects": node_test_side_effects(node_id, config, static),
        "actions": {
            "preflight": {
                "method": "POST",
                "path": f"/api/sop/{sop.get('id', '')}/nodes/{node_id}/tests",
                "destructive": False,
                "enabled": True,
            },
            "real_execution": {
                "enabled": node_real_execution_supported(node_id, sop),
                "reason": (
                    "Standard stage wrapper is available for real Node Run execution."
                    if node_real_execution_supported(node_id, sop)
                    else "No standard stage wrapper is available for this node."
                ),
            },
        },
        "status": "needs_input" if missing else "ready",
    }


def node_test_step(step_id, title, status, summary="", detail=None):
    return {
        "id": step_id,
        "title": title,
        "status": status,
        "summary": summary,
        "detail": detail if isinstance(detail, dict) else {},
    }


def build_node_test_steps(sop, plan):
    wiki = Path(sop.get("wiki_local_path", ""))
    missing = plan.get("missing_inputs") or []
    upstream = plan.get("upstream_nodes") or []
    side_effects = plan.get("side_effects") or {}
    steps = [
        node_test_step(
            "load-definition",
            "Load node definition",
            "done",
            f"Loaded {plan.get('node_id')} from workflow definition.",
            {"workflow_id": plan.get("workflow_id"), "node_id": plan.get("node_id")},
        ),
        node_test_step(
            "resolve-instance",
            "Resolve instance workspace",
            "done" if wiki.exists() else "failed",
            str(wiki) if wiki.exists() else "Instance workspace path is not available.",
            {"wiki_local_path": str(wiki)},
        ),
        node_test_step(
            "resolve-inputs",
            "Resolve required inputs",
            "needs_input" if missing else "done",
            f"{len(plan.get('resolved_inputs') or [])} resolved, {len(missing)} missing.",
            {
                "resolved_inputs": plan.get("resolved_inputs") or [],
                "missing_inputs": missing,
                "input_source": plan.get("input_source"),
                "base_run_id": plan.get("base_run_id"),
            },
        ),
        node_test_step(
            "check-upstream",
            "Check upstream dependencies",
            "done" if not missing else "skipped",
            "No upstream dependency." if not upstream else ", ".join(f"{item.get('node_id')}.{item.get('output')}" for item in upstream),
            {"upstream_nodes": upstream, "available_existing_runs": plan.get("available_existing_runs") or []},
        ),
        node_test_step(
            "check-side-effects",
            "Check side effects",
            "done",
            "Real node execution is disabled for this generic preflight.",
            side_effects,
        ),
        node_test_step(
            "build-execution-plan",
            "Build dry-run execution plan",
            "skipped" if missing else "done",
            "Inputs are incomplete." if missing else "Preflight can be used as the execution plan baseline.",
            {"real_execution_enabled": False, "mode": "preflight"},
        ),
    ]
    if steps[1]["status"] == "failed":
        steps[-1]["status"] = "skipped"
    return steps


def node_test_events_from_steps(test_id, node_id, steps, timestamp):
    events = []
    for index, step in enumerate(steps, start=1):
        events.append({
            "sequence": index,
            "event": f"node_test.step.{step.get('status')}",
            "test_id": test_id,
            "node_id": node_id,
            "step_id": step.get("id"),
            "ts": timestamp,
            "data": {
                "title": step.get("title"),
                "summary": step.get("summary"),
            },
        })
    return events


def node_test_status_from_steps(steps):
    statuses = [str(step.get("status") or "") for step in steps]
    if "failed" in statuses:
        return "failed"
    if "needs_input" in statuses:
        return "needs_input"
    if "running" in statuses:
        return "running"
    return "done"


def create_node_preflight_test(sop, node_id, body):
    plan = build_node_test_plan(sop, node_id, body)
    if plan is None:
        return 404, {"status": "error", "message": f"Node {node_id!r} not found"}
    token = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    suffix = hashlib.sha1(json.dumps(body if isinstance(body, dict) else {}, sort_keys=True).encode("utf-8")).hexdigest()[:6]
    test_id = f"node-test-{node_id}-{token}-{suffix}"
    workspace = node_test_workspace(sop, test_id)
    now = datetime.now(timezone.utc).isoformat()
    steps = build_node_test_steps(sop, plan)
    events = node_test_events_from_steps(test_id, node_id, steps, now)
    status = node_test_status_from_steps(steps)
    artifacts = [{
        "id": "node-test-result",
        "producer": node_id,
        "type": "node-test.result",
        "format": "json",
        "path": f"raw/node-tests/{test_id}/result.json",
        "title": "Node test result",
        "resolution": "recorded",
    }]
    result = {
        "test_id": test_id,
        "pipeline_id": test_id,
        "node_id": node_id,
        "status": status,
        "mode": "preflight",
        "started_at": now,
        "finished_at": now,
        "pending": False,
        "reason": "Missing required inputs" if status == "needs_input" else "Preflight failed" if status == "failed" else "",
        "steps": steps,
        "events": events,
        "artifacts": artifacts,
        "detail": plan,
    }
    write_json(workspace / "input.json", body if isinstance(body, dict) else {})
    write_json(workspace / "result.json", result)
    return 200, result


def read_generic_node_test_result(sop, test_id):
    safe = sanitize_test_id(test_id)
    if not safe.startswith("node-test-"):
        return None
    result = read_json(node_test_workspace(sop, safe) / "result.json")
    if not isinstance(result, dict):
        return None
    result["detail"] = mask_data(result.get("detail") or {})
    return result


def list_generic_node_tests(sop, node_id, limit=10):
    root = Path(sop["wiki_local_path"]) / "raw" / "node-tests"
    tests = []
    if not root.exists():
        return tests
    for test_dir in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True):
        result = read_json(test_dir / "result.json")
        if not isinstance(result, dict):
            continue
        if result.get("node_id") != node_id:
            continue
        tests.append({
            "test_id": result.get("test_id") or test_dir.name,
            "pipeline_id": result.get("pipeline_id") or result.get("test_id") or test_dir.name,
            "node_id": node_id,
            "status": result.get("status"),
            "mode": result.get("mode"),
            "started_at": result.get("started_at"),
            "finished_at": result.get("finished_at"),
            "reason": result.get("reason"),
        })
        if len(tests) >= limit:
            break
    return tests


def sanitize_node_run_id(value):
    return re.sub(r"[^A-Za-z0-9._-]", "", str(value or ""))


class NodeRunInputResolutionError(RuntimeError):
    def __init__(self, message, detail=None):
        super().__init__(message)
        self.detail = detail if isinstance(detail, dict) else {}


def workflow_id_matches(sop, workflow_id):
    workflow_id = str(workflow_id or "")
    if not workflow_id:
        return True
    binding = workflow_binding(sop)
    accepted = {
        str(binding.get("workflow_id") or ""),
        str(binding.get("workflow_name") or ""),
        str(sop.get("id") or ""),
        str(sop.get("raw_id") or ""),
        str(sop.get("sop_type") or ""),
        str(sop.get("name") or ""),
    }
    for item in list_published_runtime_workflows_for_sop(sop):
        accepted.add(str(item.get("workflow_id") or ""))
        accepted.add(str(item.get("name") or ""))
    return workflow_id in {item for item in accepted if item}


def env_config_item(key, label="", required=False, default_value=None, value=None, source="runtime-env"):
    if value is None:
        value = os.environ.get(key)
    present = not is_blank_value(value)
    return {
        "key": key,
        "label": label or key,
        "required": bool(required),
        "present": present,
        "source": source if present else f"missing:{key}",
        "masked_value": display_config_value(key, value) if present else "",
        "value": None if is_secret_key(key) else value if present else default_value,
    }


def node_run_config_context(body=None, sop=None):
    body = body if isinstance(body, dict) else {}
    overrides = body.get("overrides") if isinstance(body.get("overrides"), dict) else {}
    capability_overrides = body.get("capability_overrides") if isinstance(body.get("capability_overrides"), dict) else {}
    env_file = os.environ.get("YOUTUBE_WIKI_ENV_FILE", str(Path.home() / ".agent-brain-plugins.env"))
    settings = read_runtime_management_config()
    values = settings.get("values", {})
    runtime = runtime_info()
    runtime_id = primary_runtime_setting_id(sop, runtime)
    runtime_aliases = runtime_setting_id_aliases(sop, runtime)
    instance_id = str((sop or {}).get("instance_id") or (sop or {}).get("id") or "")
    return {
        "overrides": normalize_runtime_settings_values({str(k): str(v) for k, v in overrides.items() if not is_blank_value(v)}),
        "capability_overrides": capability_overrides,
        "runtime_id": runtime_id,
        "runtime_aliases": runtime_aliases,
        "instance_settings_values": scoped_runtime_setting_values_for_aliases(values, "instance", runtime_aliases, instance_id),
        "runtime_settings_values": scoped_runtime_setting_values_for_aliases(values, "runtime", runtime_aliases, instance_id),
        "global_settings_values": scoped_runtime_setting_values(values, "global", runtime_id, instance_id),
        "settings_backend": settings.get("backend", runtime_settings_backend()),
        "runtime_env_file": str(Path(env_file).expanduser()),
        "runtime_env_file_values": normalize_runtime_settings_values(read_env_file_values(env_file)),
        "bridge_env": normalize_runtime_settings_values(os.environ),
    }


def node_run_config_lookup(context, key, aliases=None):
    aliases = aliases or []
    candidates = [key, *aliases]
    sources = [
        ("node-run-overrides", context.get("overrides") or {}),
        ("instance-settings", context.get("instance_settings_values") or {}),
        ("runtime-settings", context.get("runtime_settings_values") or {}),
        ("global-settings", context.get("global_settings_values") or {}),
        ("bridge-env", context.get("bridge_env") or {}),
        ("runtime-env-file", context.get("runtime_env_file_values") or {}),
    ]
    for source_name, values in sources:
        for candidate in candidates:
            value = values.get(candidate) if hasattr(values, "get") else None
            if not is_blank_value(value):
                return {
                    "key": candidate,
                    "value": str(value),
                    "source": f"{source_name}:{candidate}",
                }
    return {"key": key, "value": "", "source": f"missing:{key}"}


def node_capability_defaults(sop, node_id):
    item = node_registry_item(sop, node_id) or {}
    capabilities = item.get("capabilities") if isinstance(item.get("capabilities"), dict) else {}
    result = {}
    for key in ("git", "telegram"):
        value = capabilities.get(key) if isinstance(capabilities.get(key), dict) else {}
        result[key] = dict(value)
    return result


def sanitize_managed_paths(paths):
    clean = []
    for raw in paths or []:
        text = str(raw or "").strip()
        if not text:
            continue
        path = Path(text)
        if path.is_absolute() or ".." in path.parts:
            continue
        if text not in clean:
            clean.append(text)
    return clean


def normalize_node_run_capability_overrides(sop, node_id, body=None):
    body = body if isinstance(body, dict) else {}
    raw = body.get("capability_overrides") if isinstance(body.get("capability_overrides"), dict) else {}
    defaults = node_capability_defaults(sop, node_id)
    result = {}
    for capability in ("git", "telegram"):
        item = raw.get(capability) if isinstance(raw.get(capability), dict) else {}
        default = defaults.get(capability) if isinstance(defaults.get(capability), dict) else {}
        enabled = item.get("enabled")
        if enabled is None:
            enabled = default.get("enabled", True)
        save_scope = str(item.get("save_scope") or item.get("scope") or "run")
        if save_scope not in {"run", "instance", "project"}:
            save_scope = "run"
        normalized = {
            "enabled": bool(enabled),
            "required": bool(item.get("required", default.get("required", False))),
            "managed_by": item.get("managed_by") or default.get("managed_by") or "runtime-harness",
            "save_scope": save_scope,
        }
        if capability == "git":
            paths = item.get("paths")
            if isinstance(paths, str):
                paths = [line.strip() for line in paths.splitlines()]
            if paths is None:
                paths = default.get("paths") or default.get("managed_paths") or []
            normalized["paths"] = sanitize_managed_paths(paths)
        result[capability] = normalized
    return result


def configure_instance_repo_remote(wiki_path, repo):
    if not repo or "/" not in str(repo):
        return False, "repo is missing"
    owner = str(repo).split("/", 1)[0]
    candidate_keys = []
    if owner == "skkeoriw":
        candidate_keys.append("SKKEORIW_GITHUB_TOKEN")
    if owner == "divanoo65":
        candidate_keys.append("DIVANOO65_GITHUB_TOKEN")
    candidate_keys.extend(["GITHUB_TOKEN", "GH_TOKEN"])
    env_file = os.environ.get("YOUTUBE_WIKI_ENV_FILE", str(Path.home() / ".agent-brain-plugins.env"))
    env_file_values = read_env_file_values(env_file)
    token = next((
        os.environ.get(key) or env_file_values.get(key)
        for key in candidate_keys
        if os.environ.get(key) or env_file_values.get(key)
    ), "")
    if not token:
        return False, "GitHub token is not available"
    remote = f"https://x-access-token:{quote(token, safe='')}@github.com/{repo}.git"
    result = subprocess.run(["git", "remote", "set-url", "origin", remote], cwd=str(wiki_path), capture_output=True, text=True)
    if result.returncode != 0:
        return False, result.stderr[:300] or "failed to set origin remote"
    return True, ""


def write_yaml_file(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def apply_instance_capability_override(sop, node_id, overrides):
    sop_file = Path(str(sop.get("sop_file") or "")).expanduser()
    wiki_path = Path(str(sop.get("wiki_local_path") or sop_file.parent)).expanduser()
    if not sop_file.exists():
        return {"status": "skipped", "reason": "sop.yaml is missing"}
    doc = read_yaml(sop_file)
    nodes = doc.setdefault("nodes", {})
    node = nodes.setdefault(node_id, {})
    capabilities = node.setdefault("capabilities", {})
    changed = {}
    for capability, item in (overrides or {}).items():
        if not isinstance(item, dict) or item.get("save_scope") != "instance":
            continue
        target = capabilities.setdefault(capability, {})
        before = json.dumps(target, sort_keys=True, ensure_ascii=False)
        target["enabled"] = bool(item.get("enabled", True))
        target["required"] = bool(item.get("required", False))
        target["managed_by"] = item.get("managed_by") or target.get("managed_by") or "runtime-harness"
        if capability == "git":
            paths = sanitize_managed_paths(item.get("paths") or [])
            if paths:
                target["paths"] = paths
            target["editable"] = True
            target["save_scope"] = "instance-override"
        if capability == "telegram":
            target["editable"] = True
            target["save_scope"] = "instance-override"
        after = json.dumps(target, sort_keys=True, ensure_ascii=False)
        if before != after:
            changed[capability] = mask_data(target)
    if not changed:
        return {"status": "unchanged", "changed": {}}
    write_yaml_file(sop_file, doc)
    remote_ok, remote_error = configure_instance_repo_remote(wiki_path, sop.get("repo", ""))
    subprocess.run(["git", "add", "--", "sop.yaml"], cwd=str(wiki_path), capture_output=True, text=True)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet", "--", "sop.yaml"], cwd=str(wiki_path), capture_output=True)
    if diff.returncode == 0:
        return {"status": "unchanged", "changed": changed}
    message = f"chore: update node {node_id} capability defaults"
    commit = subprocess.run(["git", "commit", "-m", message], cwd=str(wiki_path), capture_output=True, text=True)
    commit_hash = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(wiki_path), capture_output=True, text=True).stdout.strip()
    pushed = False
    push_error = ""
    if commit.returncode == 0 and remote_ok:
        push = subprocess.run(["git", "push", "origin", "main"], cwd=str(wiki_path), capture_output=True, text=True, timeout=60)
        pushed = push.returncode == 0
        push_error = "" if pushed else push.stderr[:300]
    return {
        "status": "saved" if commit.returncode == 0 else "failed",
        "scope": "instance",
        "sop_file": str(sop_file),
        "repo": sop.get("repo", ""),
        "changed": changed,
        "commit": commit_hash,
        "pushed": pushed,
        "error": "" if commit.returncode == 0 and (pushed or not remote_ok) else (commit.stderr[:300] if commit.returncode != 0 else push_error or remote_error),
    }


def create_project_definition_change_request(sop, node_id, overrides, node_run_id):
    project_changes = {
        key: value for key, value in (overrides or {}).items()
        if isinstance(value, dict) and value.get("save_scope") == "project"
    }
    if not project_changes:
        return {"status": "skipped"}
    wiki_path = Path(str(sop.get("wiki_local_path") or "")).expanduser()
    request_id = sanitize_node_run_id(node_run_id) or f"node-definition-change-{int(time.time())}"
    target = wiki_path / "raw" / "node-definition-change-requests" / f"{request_id}.json"
    payload = {
        "status": "pending-development",
        "reason": "Project defaults live in agent-brain-plugins and must be changed through the repo-first development workflow.",
        "node_id": node_id,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "requested_changes": mask_data(project_changes),
        "target_files": [
            f"youtube-wiki/skills/sop-{node_id}/node.yaml",
            "youtube-wiki/templates/wiki-repo/sop.yaml",
        ],
    }
    write_json(target, payload)
    return {"status": "created", "path": str(target.relative_to(wiki_path)), **payload}


def node_run_definition_scope_reports(sop, node_id, node_run_id, overrides):
    instance_report = apply_instance_capability_override(sop, node_id, overrides)
    project_report = create_project_definition_change_request(sop, node_id, overrides, node_run_id)
    return {
        "instance_override": instance_report,
        "project_default_request": project_report,
    }


def node_run_config_item(context, key, label="", required=False, default_value=None, aliases=None):
    resolved = node_run_config_lookup(context, key, aliases)
    return env_config_item(
        resolved["key"],
        label or key,
        required=required,
        default_value=default_value,
        value=resolved["value"],
        source=resolved["source"],
    )


def node_run_config_int(context, key, default):
    resolved = node_run_config_lookup(context, key)
    try:
        value = int(resolved["value"]) if not is_blank_value(resolved["value"]) else int(default)
    except Exception:
        value = int(default)
    return {
        "key": key,
        "label": key.replace("_", " ").title(),
        "required": False,
        "present": not is_blank_value(resolved["value"]),
        "source": resolved["source"] if not is_blank_value(resolved["value"]) else "default",
        "value": value,
    }


def node_run_config_source_summary(context):
    env_file_values = context.get("runtime_env_file_values") or {}
    overrides = context.get("overrides") or {}
    capability_overrides = context.get("capability_overrides") or {}
    return {
        "runtime_env_file": context.get("runtime_env_file") or "",
        "runtime_env_file_present": bool(env_file_values),
        "runtime_env_file_keys": sorted(k for k in env_file_values if k in RUNTIME_CAPABILITY_ENV or k in RUNTIME_CONFIG_CATEGORIES),
        "node_run_override_keys": sorted(overrides.keys()),
        "capability_override_keys": sorted(capability_overrides.keys()),
        "settings_backend": context.get("settings_backend") or runtime_settings_backend(),
        "instance_settings_keys": sorted((context.get("instance_settings_values") or {}).keys()),
        "runtime_settings_keys": sorted((context.get("runtime_settings_values") or {}).keys()),
        "global_settings_keys": sorted((context.get("global_settings_values") or {}).keys()),
        "precedence": ["node-run-overrides", "instance-settings", "runtime-settings", "global-settings", "bridge-env", "runtime-env-file", "defaults"],
    }


def parse_int_env(key, default):
    try:
        return int(os.environ.get(key, str(default)) or default)
    except Exception:
        return default


def sop_definition(sop):
    return read_yaml(Path(sop.get("sop_file") or ""))


def resolve_telegram_config(sop, node_id, static, context):
    definition = sop_definition(sop)
    notify = definition.get("notify") if isinstance(definition.get("notify"), dict) else {}
    telegram = notify.get("telegram") if isinstance(notify.get("telegram"), dict) else {}
    token_env = str(telegram.get("token_env") or "YOUTUBE_WIKI_TG_TOKEN")
    chat = node_run_config_lookup(context, "YOUTUBE_WIKI_TG_CHAT_ID", RUNTIME_CAPABILITY_ENV.get("YOUTUBE_WIKI_TG_CHAT_ID", []))
    chat_id = chat.get("value")
    chat_source = chat.get("source") or "missing:YOUTUBE_WIKI_TG_CHAT_ID"
    if is_blank_value(chat_id) and not is_blank_value(telegram.get("chat_id")):
        chat_id = telegram.get("chat_id")
        chat_source = "instance-sop:notify.telegram.chat_id"
    token = node_run_config_lookup(context, token_env)
    capabilities = (node_registry_item(sop, node_id) or {}).get("capabilities") or {}
    tg_cap = capabilities.get("telegram") if isinstance(capabilities.get("telegram"), dict) else {}
    override = ((context.get("capability_overrides") or {}).get("telegram") or {}) if isinstance(context.get("capability_overrides"), dict) else {}
    enabled = bool(override.get("enabled", tg_cap.get("enabled", (static.get("infra") or {}).get("tg_notify", True))))
    required = bool(override.get("required", tg_cap.get("required", False)))
    token_present = not is_blank_value(token.get("value"))
    chat_present = not is_blank_value(chat_id)
    if not enabled:
        status = "disabled"
    elif token_present and chat_present:
        status = "ready"
    elif required:
        status = "failed"
    else:
        status = "warning"
    return {
        "capability": "telegram",
        "label": "Telegram progress notification",
        "enabled": enabled,
        "required": required,
        "managed_by": override.get("managed_by") or tg_cap.get("managed_by") or "runtime-harness",
        "save_scope": override.get("save_scope") or "run",
        "status": status,
        "token_env": token_env,
        "token": env_config_item(token.get("key") or token_env, "Telegram Bot Token", required=enabled and required, value=token.get("value"), source=token.get("source") or f"missing:{token_env}"),
        "chat_id": {
            "key": "YOUTUBE_WIKI_TG_CHAT_ID",
            "label": "Telegram Chat ID",
            "required": enabled and required,
            "present": chat_present,
            "source": chat_source if chat_present else "missing:YOUTUBE_WIKI_TG_CHAT_ID",
            "masked_value": mask_value(chat_id) if chat_present else "",
            "value": str(chat_id) if chat_present else "",
        },
        "probe": {
            "enabled": enabled,
            "explicit_confirmation_required": True,
            "side_effect": "sends a Telegram test message",
        },
    }


def resolve_youtube_research_worker_config(node_id, context):
    if node_id != "youtube-deep-research":
        return None
    workflow_token = node_run_config_lookup(context, "YOUTUBE_RESEARCH_WORKFLOW_TOKEN", RUNTIME_CAPABILITY_ENV.get("YOUTUBE_RESEARCH_WORKFLOW_TOKEN", []))
    content_token = node_run_config_lookup(context, "YOUTUBE_CONTENT_API_TOKEN", RUNTIME_CAPABILITY_ENV.get("YOUTUBE_CONTENT_API_TOKEN", []))
    token = workflow_token if not is_blank_value(workflow_token.get("value")) else content_token
    base_url = node_run_config_lookup(context, "YOUTUBE_RESEARCH_WORKFLOW_URL", RUNTIME_CAPABILITY_ENV.get("YOUTUBE_RESEARCH_WORKFLOW_URL", []))
    timeout = node_run_config_int(context, "YOUTUBE_RESEARCH_WORKFLOW_TIMEOUT", 1200)
    interval = node_run_config_int(context, "YOUTUBE_RESEARCH_WORKFLOW_POLL_INTERVAL", 10)
    missing = []
    if not base_url.get("value"):
        missing.append("YOUTUBE_RESEARCH_WORKFLOW_URL")
    if not token.get("value"):
        missing.append("YOUTUBE_RESEARCH_WORKFLOW_TOKEN")
    return {
        "capability": "youtube-research-worker",
        "label": "YouTube Deep Research Worker",
        "status": "ready" if not missing else "failed",
        "base_url": env_config_item(base_url.get("key") or "YOUTUBE_RESEARCH_WORKFLOW_URL", "Worker URL", required=True, value=str(base_url.get("value") or "").rstrip("/"), source=base_url.get("source") or "missing:YOUTUBE_RESEARCH_WORKFLOW_URL"),
        "token": env_config_item(token.get("key") or "YOUTUBE_RESEARCH_WORKFLOW_TOKEN", "Worker API Token", required=True, value=token.get("value"), source=token.get("source") or "missing:YOUTUBE_RESEARCH_WORKFLOW_TOKEN"),
        "timeout": {
            **timeout,
            "label": "Worker Poll Timeout",
            "unit": "seconds",
        },
        "poll_interval": {
            **interval,
            "label": "Worker Poll Interval",
            "unit": "seconds",
        },
        "missing": missing,
    }


def resolve_wiki_llm_config(sop, node_id, context):
    if node_id != "wiki-build":
        return None
    capabilities = (node_registry_item(sop, node_id) or {}).get("capabilities") or {}
    llm_cap = capabilities.get("llm") if isinstance(capabilities.get("llm"), dict) else {}
    enabled = bool(llm_cap.get("enabled", True))
    required = bool(llm_cap.get("required", True))
    provider = node_run_config_lookup(context, "WIKI_LLM_PROVIDER", RUNTIME_CAPABILITY_ENV.get("WIKI_LLM_PROVIDER", []))
    base_url = node_run_config_lookup(context, "WIKI_LLM_BASE_URL", RUNTIME_CAPABILITY_ENV.get("WIKI_LLM_BASE_URL", []))
    api_key = node_run_config_lookup(context, "WIKI_LLM_API_KEY", RUNTIME_CAPABILITY_ENV.get("WIKI_LLM_API_KEY", []))
    model = node_run_config_lookup(context, "WIKI_LLM_MODEL", RUNTIME_CAPABILITY_ENV.get("WIKI_LLM_MODEL", []))
    if is_blank_value(model.get("value")):
        model = node_run_config_lookup(context, "WIKI_DEEPSEEK_MODEL", RUNTIME_CAPABILITY_ENV.get("WIKI_DEEPSEEK_MODEL", []))
    if is_blank_value(model.get("value")):
        model = node_run_config_lookup(context, "WIKI_GEMINI_MODEL", RUNTIME_CAPABILITY_ENV.get("WIKI_GEMINI_MODEL", []))

    provider_value = str(provider.get("value") or "openai-compatible").strip() or "openai-compatible"
    provider_source = provider.get("source") if not is_blank_value(provider.get("value")) else "default"
    missing = []
    if enabled and is_blank_value(base_url.get("value")):
        missing.append("WIKI_LLM_BASE_URL")
    if enabled and is_blank_value(api_key.get("value")):
        missing.append("WIKI_LLM_API_KEY")
    if enabled and is_blank_value(model.get("value")):
        missing.append("WIKI_LLM_MODEL")
    if not enabled:
        status = "disabled"
    elif missing and required:
        status = "failed"
    elif missing:
        status = "warning"
    else:
        status = "ready"
    return {
        "capability": "llm",
        "label": "Wiki LLM Gateway",
        "enabled": enabled,
        "required": required,
        "status": status,
        "managed_by": llm_cap.get("managed_by") or "runtime-harness",
        "provider": env_config_item(
            provider.get("key") or "WIKI_LLM_PROVIDER",
            "Wiki LLM Provider",
            required=False,
            value=provider_value,
            source=provider_source,
        ),
        "base_url": env_config_item(
            base_url.get("key") or "WIKI_LLM_BASE_URL",
            "Wiki LLM Gateway Base URL",
            required=enabled and required,
            value=str(base_url.get("value") or "").rstrip("/"),
            source=base_url.get("source") or "missing:WIKI_LLM_BASE_URL",
        ),
        "api_key": env_config_item(
            api_key.get("key") or "WIKI_LLM_API_KEY",
            "Wiki LLM Gateway API Key",
            required=enabled and required,
            value=api_key.get("value"),
            source=api_key.get("source") or "missing:WIKI_LLM_API_KEY",
        ),
        "model": env_config_item(
            model.get("key") or "WIKI_LLM_MODEL",
            "Wiki LLM Model",
            required=enabled and required,
            value=model.get("value"),
            source=model.get("source") or "missing:WIKI_LLM_MODEL",
        ),
        "missing": missing,
    }


def resolve_git_config(_sop, context):
    token = node_run_config_lookup(context, "GITHUB_TOKEN", ["GH_TOKEN", *RUNTIME_CAPABILITY_ENV.get("GITHUB_TOKEN", [])])
    override = ((context.get("capability_overrides") or {}).get("git") or {}) if isinstance(context.get("capability_overrides"), dict) else {}
    enabled = bool(override.get("enabled", True))
    return {
        "capability": "git",
        "label": "GitHub workspace persistence",
        "enabled": enabled,
        "required": False,
        "status": "disabled" if not enabled else "ready" if token.get("value") else "warning",
        "managed_by": override.get("managed_by") or "runtime-harness",
        "save_scope": override.get("save_scope") or "run",
        "paths": sanitize_managed_paths(override.get("paths") or []),
        "token": env_config_item(token.get("key") or "GITHUB_TOKEN", "GitHub Token", required=False, value=token.get("value"), source=token.get("source") or "missing:GITHUB_TOKEN"),
    }


def node_run_config_summary(sop, node_id, static, context):
    configs = {
        "telegram": resolve_telegram_config(sop, node_id, static, context),
        "github": resolve_git_config(sop, context),
    }
    worker = resolve_youtube_research_worker_config(node_id, context)
    if worker:
        configs["youtube_research_worker"] = worker
    llm = resolve_wiki_llm_config(sop, node_id, context)
    if llm:
        configs["llm"] = llm
    return configs


def node_run_fix_suggestions(configs, missing_inputs):
    suggestions = []
    for item in missing_inputs or []:
        suggestions.append({
            "target": "node-run-inputs",
            "title": f"Provide {item.get('name')}",
            "reason": item.get("reason") or "Required input is missing.",
            "action": "Switch input source, select an existing Workflow Run, pick an artifact, or enter a manual value.",
        })
    telegram = configs.get("telegram") or {}
    if telegram.get("enabled") and telegram.get("status") in {"warning", "failed"}:
        suggestions.append({
            "target": "instance-settings",
            "title": "Fix Telegram progress notification",
            "reason": "Telegram token or chat id is missing for this Instance/Runtime context.",
            "action": "Update Instance Settings for per-instance TG, or Runtime/Global Settings for shared defaults. Use explicit probe after saving.",
        })
    worker = configs.get("youtube_research_worker") or {}
    if worker.get("status") == "failed":
        suggestions.append({
            "target": "runtime-settings",
            "title": "Fix YouTube Deep Research Worker config",
            "reason": ", ".join(worker.get("missing") or []) or "Worker config is incomplete.",
            "action": "Set YOUTUBE_RESEARCH_WORKFLOW_URL and YOUTUBE_RESEARCH_WORKFLOW_TOKEN on the Runtime, then retry the Node Run.",
        })
    llm = configs.get("llm") or {}
    if llm.get("status") == "failed":
        suggestions.append({
            "target": "runtime-settings",
            "title": "Fix Wiki LLM Gateway config",
            "reason": ", ".join(llm.get("missing") or []) or "LLM gateway config is incomplete.",
            "action": "Set WIKI_LLM_BASE_URL, WIKI_LLM_API_KEY and WIKI_LLM_MODEL in Settings or Instance overrides, then retry the Node Run.",
        })
    git = configs.get("github") or {}
    if git.get("status") == "warning":
        suggestions.append({
            "target": "runtime-settings",
            "title": "Check GitHub persistence token",
            "reason": "GitHub token is not visible to this Runtime process.",
            "action": "Update Runtime Settings or the runtime env file before running a node that writes artifacts.",
        })
    return suggestions


def node_run_environment_snapshot(plan):
    configs = plan.get("resolved_config") or {}
    rows = []
    seen = set()

    def add_item(capability, parent_status, item):
        if not isinstance(item, dict) or "key" not in item:
            return
        key = str(item.get("key") or "")
        if not key:
            return
        source = str(item.get("source") or "")
        row_id = f"{capability}:{key}:{source}"
        if row_id in seen:
            return
        seen.add(row_id)
        present = bool(item.get("present", not is_blank_value(item.get("value"))))
        raw_display = item.get("masked_value")
        if is_blank_value(raw_display):
            raw_display = display_config_value(key, item.get("value")) if present else ""
        rows.append({
            "id": row_id,
            "capability": capability,
            "key": key,
            "label": item.get("label") or key,
            "source": source or ("missing:" + key if not present else "unknown"),
            "source_kind": (source.split(":", 1)[0] if source else ("missing" if not present else "unknown")),
            "present": present,
            "required": bool(item.get("required", False)),
            "secret": is_secret_key(key),
            "value": raw_display if present else "",
            "status": parent_status or ("ready" if present else "missing"),
            "unit": item.get("unit") or "",
            "category": RUNTIME_CONFIG_CATEGORIES.get(key, capability),
        })

    def walk(capability, parent_status, value):
        if isinstance(value, dict):
            add_item(capability, parent_status, value)
            for child in value.values():
                walk(capability, parent_status, child)
        elif isinstance(value, list):
            for child in value:
                walk(capability, parent_status, child)

    for capability, config in configs.items():
        config = config if isinstance(config, dict) else {}
        capability_name = str(config.get("capability") or capability)
        if capability_name == "github":
            capability_name = "git"
        walk(capability_name, str(config.get("status") or ""), config)
    return rows


def node_run_capability_results(plan, real_execution=None):
    real_execution = real_execution if isinstance(real_execution, dict) else {}
    def normalize_capability_map(value):
        result = {}
        source = value if isinstance(value, dict) else {}
        for raw_key, raw_item in source.items():
            item = raw_item if isinstance(raw_item, dict) else {}
            key = str(item.get("capability") or raw_key)
            if key == "github":
                key = "git"
            result[key] = {**item, "_source_key": raw_key}
        return result

    real_caps = normalize_capability_map(real_execution.get("capabilities"))
    probes = normalize_capability_map(plan.get("capability_probes"))
    configs = normalize_capability_map(plan.get("resolved_config"))
    rows = []
    for key in sorted(set(real_caps) | set(probes) | set(configs)):
        runtime = real_caps.get(key) if isinstance(real_caps.get(key), dict) else {}
        probe = probes.get(key) if isinstance(probes.get(key), dict) else {}
        config = configs.get(key) if isinstance(configs.get(key), dict) else {}
        detail = runtime or probe or config
        status = str(runtime.get("status") or probe.get("status") or config.get("status") or "unknown")
        reason = str(
            runtime.get("error")
            or runtime.get("reason")
            or runtime.get("message")
            or config.get("reason")
            or ""
        )
        rows.append({
            "key": key,
            "capability": runtime.get("capability") or probe.get("capability") or config.get("capability") or key,
            "label": runtime.get("label") or probe.get("label") or config.get("label") or key.replace("_", " "),
            "status": status,
            "enabled": bool(runtime.get("enabled", probe.get("enabled", config.get("enabled", True)))),
            "required": bool(runtime.get("required", probe.get("required", config.get("required", False)))),
            "source": "runtime-result" if runtime else "config-resolution",
            "reason": reason,
            "managed_by": runtime.get("managed_by") or config.get("managed_by") or "",
            "detail": mask_data(detail),
        })
    return rows


def node_run_issue_rows(plan, capability_results):
    issues = []
    seen = set()

    def add_issue(issue):
        issue_id = str(issue.get("id") or f"{issue.get('target', 'issue')}:{issue.get('title', '')}")
        if issue_id in seen:
            return
        seen.add(issue_id)
        issues.append({**issue, "id": issue_id})

    for item in plan.get("fix_suggestions") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "Suggested fix")
        target = str(item.get("target") or "configuration")
        severity = "error" if any(word in title.lower() for word in ("fix", "missing", "failed")) else "warning"
        add_issue({
            "target": target,
            "severity": severity,
            "title": title,
            "message": str(item.get("reason") or ""),
            "action": str(item.get("action") or ""),
            "source": "config-resolution",
            "related_capability": "",
            "related_config_keys": [],
        })

    env_by_capability = {}
    for item in node_run_environment_snapshot(plan):
        env_by_capability.setdefault(item.get("capability"), []).append(item.get("key"))

    target_by_capability = {
        "telegram": "instance-settings",
        "github": "runtime-settings",
        "git": "runtime-settings",
        "youtube-research-worker": "runtime-settings",
        "youtube_research_worker": "runtime-settings",
        "llm": "runtime-settings",
    }
    for row in capability_results:
        status = str(row.get("status") or "")
        if status not in {"failed", "warning", "blocked", "needs_input"}:
            continue
        capability = str(row.get("key") or row.get("capability") or "")
        target = target_by_capability.get(capability, "runtime-settings")
        add_issue({
            "target": target,
            "severity": "error" if status in {"failed", "blocked", "needs_input"} else "warning",
            "title": f"{row.get('label') or capability} needs attention",
            "message": str(row.get("reason") or f"Capability status is {status}."),
            "action": "Review the resolved configuration for this capability, update the owning Runtime or Instance settings, then retry this Node Run.",
            "source": str(row.get("source") or "runtime-result"),
            "related_capability": capability,
            "related_config_keys": sorted(set(env_by_capability.get(capability, []))),
        })
    return issues


def node_run_runtime_context(sop):
    runtime = runtime_info()
    return {
        "runtime_id": sop.get("runtime_id") or runtime.get("runtime_id") or "",
        "channel_url": sop.get("channel_url") or runtime.get("channel_url") or "",
        "spi_base_url": sop.get("spi_base_url") or runtime.get("spi_base_url") or "",
        "registry_path": runtime.get("registry_path") or str(REGISTRY_PATH),
        "health": runtime.get("health") or {},
        "hermes_webhook_url": (runtime.get("metadata") or {}).get("hermes_webhook_url") if isinstance(runtime.get("metadata"), dict) else "",
    }


def node_run_instance_context(sop):
    summary = instance_summary(sop, include_latest=False)
    return {
        "instance_id": summary.get("instance_id") or sop.get("id") or "",
        "status": summary.get("status") or "",
        "repo": summary.get("repo") or "",
        "repo_branch": summary.get("repo_branch") or "main",
        "wiki_local_path": summary.get("wiki_local_path") or sop.get("wiki_local_path") or "",
        "workspace_status": summary.get("workspace_status") or "",
        "registry_status": "enabled" if summary.get("enabled") is not False else "disabled",
        "run_index_path": summary.get("run_index_path") or "",
        "run_index_status": summary.get("run_index_status") or "",
        "capabilities": summary.get("capabilities") or {},
    }


def build_node_run_plan(sop, workflow_id, node_id, body=None):
    body = body if isinstance(body, dict) else {}
    if not workflow_id_matches(sop, workflow_id):
        return None
    config, config_source = node_config_for(sop, node_id)
    if not isinstance(config, dict):
        return None
    static = node_static_config(sop, node_id) or {}
    input_source = str(body.get("input_source") or body.get("source_mode") or "generated-fixture")
    if input_source == "artifact":
        input_source = "manual"
    if input_source not in {"existing-run", "existing-node-run", "generated-fixture", "manual", "deepseek-mock"}:
        input_source = "generated-fixture"
    test_plan = build_node_test_plan(sop, node_id, {
        **body,
        "input_source": input_source,
    }) or {}
    mode = str(body.get("mode") or "preflight")
    if mode not in {"preflight", "probe", "dry-run", "real-node"}:
        mode = "preflight"
    binding = workflow_binding(sop)
    runtime = runtime_info()
    capability_overrides = normalize_node_run_capability_overrides(sop, node_id, body)
    body = {**body, "capability_overrides": capability_overrides}
    config_context = node_run_config_context(body, sop)
    configs = node_run_config_summary(sop, node_id, static, config_context)
    return {
        **test_plan,
        "runtime_id": sop.get("runtime_id") or runtime.get("runtime_id") or "",
        "runtime_channel_url": sop.get("channel_url") or runtime.get("channel_url") or "",
        "instance_id": sop.get("instance_id") or sop.get("id", ""),
        "workflow_id": workflow_id or binding.get("workflow_id") or sop.get("sop_type") or sop.get("id", ""),
        "workflow_name": binding.get("workflow_name") or sop.get("workflow_title") or "",
        "node_id": node_id,
        "node_source": config_source,
        "node_title": static.get("title") or node_id,
        "mode": mode,
        "input_source": input_source,
        "resolved_config": configs,
        "runtime_context": node_run_runtime_context(sop),
        "instance_context": node_run_instance_context(sop),
        "definition_defaults": node_capability_defaults(sop, node_id),
        "capability_overrides": capability_overrides,
        "definition_scope_reports": body.get("_definition_scope_reports") if isinstance(body.get("_definition_scope_reports"), dict) else {},
        "config_sources": node_run_config_source_summary(config_context),
        "capability_probes": {
            key: {
                "capability": value.get("capability") or key,
                "label": value.get("label") or key,
                "status": value.get("status") or "unknown",
                "required": bool(value.get("required", False)),
                "enabled": value.get("enabled", True),
            }
            for key, value in configs.items()
        },
        "fix_suggestions": node_run_fix_suggestions(configs, test_plan.get("missing_inputs") or []),
        "node_capabilities": (node_registry_item(sop, node_id) or {}).get("capabilities") or {},
    }


def node_run_step(step_id, title, status, summary="", detail=None):
    return node_test_step(step_id, title, status, summary, detail)


def annotate_node_run_steps(steps, started_at):
    try:
        base = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
    except Exception:
        base = datetime.now(timezone.utc)
    for index, step in enumerate(steps):
        step_started = base + timedelta(milliseconds=index * 35)
        step.setdefault("started_at", step_started.isoformat())
        if step.get("status") not in {"running", "waiting"}:
            elapsed = 24 if step.get("status") == "skipped" else 35
            step.setdefault("elapsed_ms", elapsed)
            step.setdefault("finished_at", (step_started + timedelta(milliseconds=elapsed)).isoformat())
        else:
            step.setdefault("elapsed_ms", 0)
    return steps


def outer_step_status(steps, step_id):
    step = node_run_step_by_id(steps, step_id)
    return str((step or {}).get("status") or "")


def status_is_problem(status):
    return status in {"failed", "blocked", "needs_input"}


def lifecycle_status_from_capabilities(capabilities):
    status = "done"
    for item in (capabilities or {}).values():
        if not isinstance(item, dict):
            continue
        item_status = str(item.get("status") or "")
        required = bool(item.get("required", False))
        if item_status == "failed" and required:
            return "failed"
        if item_status == "failed" and status != "failed":
            status = "warning"
    return status


def forward_next_summary(real_execution):
    stdout = str(((real_execution or {}).get("detail") or {}).get("stdout_tail") or "")
    matches = [line.strip() for line in stdout.splitlines() if "forward_next" in line]
    return matches[-1] if matches else ""


def node_run_lifecycle_steps(plan, steps, real_execution=None, started_at="", finished_at="", pending=False):
    """Generic lifecycle exposed as Node Run inner flow.

    This intentionally does not infer skill-internal steps. Every node shares the
    same visible lifecycle; detailed business progress belongs in logs/events.
    """
    real_execution = real_execution if isinstance(real_execution, dict) else {}
    resolved_inputs = plan.get("resolved_inputs") or []
    missing_inputs = plan.get("missing_inputs") or []
    pending_inputs = plan.get("pending_materialization_inputs") or []
    execute_step = node_run_step_by_id(steps, "execute-or-dry-run") or {}
    config_step = node_run_step_by_id(steps, "resolve-config") or {}
    validation = real_execution.get("validation") or {}
    capabilities = real_execution.get("capabilities") if isinstance(real_execution.get("capabilities"), dict) else {}
    command = ((real_execution.get("detail") or {}).get("command") or execute_step.get("detail", {}).get("command") or [])
    returncode = ((real_execution.get("detail") or {}).get("returncode") if real_execution else None)
    timed_out = bool(((real_execution.get("detail") or {}).get("timed_out")) if real_execution else False)
    problem_statuses = [
        outer_step_status(steps, "load-definition"),
        outer_step_status(steps, "resolve-context"),
        outer_step_status(steps, "resolve-inputs"),
        outer_step_status(steps, "resolve-config"),
    ]

    if any(status_is_problem(status) for status in problem_statuses):
        pre_status = "failed" if "failed" in problem_statuses else "needs_input"
    elif outer_step_status(steps, "execute-or-dry-run") in {"running", "waiting"}:
        pre_status = "done"
    else:
        pre_status = "done"

    if pending:
        doing_status = "running"
    elif real_execution:
        doing_status = "done" if real_execution.get("status") == "done" else "failed"
    elif plan.get("mode") != "real-node":
        doing_status = "skipped"
    else:
        doing_status = str(execute_step.get("status") or "waiting")

    if pending:
        post_status = "waiting"
    elif doing_status == "failed":
        post_status = "failed"
    elif real_execution:
        if validation.get("status") == "failed":
            post_status = "failed"
        else:
            post_status = lifecycle_status_from_capabilities(capabilities)
    else:
        post_status = "skipped" if plan.get("mode") != "real-node" else "waiting"

    log_path = real_execution.get("log_path") or ((real_execution.get("detail") or {}).get("log_path") if real_execution else "")
    actual_outputs = real_execution.get("actual_outputs") or {}
    artifacts = real_execution.get("artifacts") or []
    return [
        {
            "id": "pre",
            "title": "执行前",
            "status": pre_status,
            "summary": "Node Run 上下文、输入和运行配置已准备，并交给 stage runner on_start。",
            "started_at": started_at or "",
            "finished_at": real_execution.get("started_at") or "",
            "detail": {
                "mode": plan.get("mode"),
                "input_source": plan.get("input_source"),
                "resolved_inputs": resolved_inputs,
                "pending_materialization_inputs": pending_inputs,
                "missing_inputs": missing_inputs,
                "config_status": config_step.get("status"),
                "command": command,
                "stage_hook": "on_start",
            },
        },
        {
            "id": "doing",
            "title": "执行中",
            "status": doing_status,
            "summary": (
                "Skill / agent 业务执行完成。"
                if doing_status == "done"
                else "Skill / agent 业务仍在执行。"
                if doing_status == "running"
                else "Skill / agent 业务执行失败。"
                if doing_status == "failed"
                else "当前模式未执行真实业务逻辑。"
            ),
            "started_at": real_execution.get("started_at") or "",
            "finished_at": real_execution.get("finished_at") or "",
            "elapsed_ms": real_execution.get("elapsed_ms"),
            "detail": {
                "command": command,
                "returncode": returncode,
                "timed_out": timed_out,
                "log_path": log_path,
                "stdout_tail": ((real_execution.get("detail") or {}).get("stdout_tail") if real_execution else ""),
                "stderr_tail": ((real_execution.get("detail") or {}).get("stderr_tail") if real_execution else ""),
            },
        },
        {
            "id": "post",
            "title": "执行后",
            "status": post_status,
            "summary": "stage_runner on_done/on_failed、输出校验、Git/TG capability 和 bridge 结果收集已归档。",
            "started_at": real_execution.get("finished_at") or "",
            "finished_at": finished_at or "",
            "detail": {
                "stage_hooks": ["on_done/on_failed", "forward_next"],
                "validation": validation,
                "actual_outputs": actual_outputs,
                "business_artifact_count": len(artifacts),
                "capabilities": capabilities,
                "forward_next": forward_next_summary(real_execution),
            },
        },
    ]


def build_node_run_steps(sop, plan):
    wiki = Path(sop.get("wiki_local_path", ""))
    missing = plan.get("missing_inputs") or []
    pending_inputs = plan.get("pending_materialization_inputs") or []
    configs = plan.get("resolved_config") or {}
    worker = configs.get("youtube_research_worker") or {}
    llm = configs.get("llm") or {}
    telegram = configs.get("telegram") or {}
    git = configs.get("github") or configs.get("git") or {}
    mode = plan.get("mode") or "preflight"
    real_supported = node_real_execution_supported(plan.get("node_id"), sop)
    config_status = "done"
    config_notes = []
    if worker.get("status") == "failed":
        config_status = "failed"
        config_notes.append("youtube research worker config missing")
    if llm.get("enabled") and llm.get("status") == "failed":
        config_status = "failed"
        config_notes.append("wiki llm gateway config missing")
    if telegram.get("enabled") and telegram.get("status") in {"warning", "failed"}:
        if telegram.get("required"):
            config_status = "failed"
        elif config_status != "failed":
            config_status = "warning"
        config_notes.append("telegram progress notification needs attention")
    steps = [
        node_run_step(
            "create-run",
            "Create node run workspace",
            "done",
            "Node Run record is allocated independently from Workflow Run.",
            {"storage": "raw/node-runs/{node_run_id}"},
        ),
        node_run_step(
            "load-definition",
            "Load node definition",
            "done",
            f"Loaded {plan.get('node_id')} from {plan.get('workflow_id')}.",
            {"workflow_id": plan.get("workflow_id"), "node_id": plan.get("node_id"), "node_title": plan.get("node_title")},
        ),
        node_run_step(
            "resolve-context",
            "Resolve Runtime / Instance / Workflow context",
            "done" if wiki.exists() else "failed",
            f"{plan.get('runtime_id')} · {plan.get('instance_id')} · {plan.get('workflow_id')}" if wiki.exists() else "Instance workspace is not available.",
            {
                "runtime": plan.get("runtime_context") or {},
                "instance": plan.get("instance_context") or {},
                "workflow_id": plan.get("workflow_id"),
            },
        ),
        node_run_step(
            "resolve-inputs",
            "Resolve node inputs",
            "needs_input" if missing else "done",
            f"{len(plan.get('resolved_inputs') or [])} resolved, {len(pending_inputs)} pending relay, {len(missing)} missing.",
            {
                "input_source": plan.get("input_source"),
                "base_run_id": plan.get("base_run_id"),
                "source_node_run_id": plan.get("source_node_run_id"),
                "relay_mode": plan.get("relay_mode"),
                "selected_outputs": plan.get("selected_outputs") or [],
                "relay_mappings": plan.get("relay_mappings") or [],
                "relay_selection": plan.get("relay_selection") or {},
                "resolved_inputs": plan.get("resolved_inputs") or [],
                "pending_materialization_inputs": pending_inputs,
                "missing_inputs": missing,
            },
        ),
        node_run_step(
            "resolve-config",
            "Resolve execution config",
            config_status,
            "; ".join(config_notes) if config_notes else "Runtime, Instance and capability config resolved.",
            {
                "resolved_config": configs,
                "definition_defaults": plan.get("definition_defaults") or {},
                "capability_overrides": plan.get("capability_overrides") or {},
                "definition_scope_reports": plan.get("definition_scope_reports") or {},
            },
        ),
        node_run_step(
            "generate-agent-request",
            "Generate Agent Request",
            "waiting" if mode == "real-node" and real_supported and not missing and config_status != "failed" else "skipped",
            "Waiting to render the Hermes skill request." if mode == "real-node" and real_supported and not missing and config_status != "failed" else "No Agent Request is generated outside real-node execution.",
            {
                "executor": "hermes",
                "skill": node_run_skill_name(sop, plan.get("node_id"), plan),
                "template_version": "hermes-agent-executor.v1",
            },
        ),
        node_run_step(
            "probe-capabilities",
            "Probe attached capabilities",
            "done" if mode == "probe" and config_status in {"done", "warning"} else "skipped" if mode != "probe" else "failed",
            "Capability probes are explicit. Telegram send probes require user confirmation." if mode != "probe" else "Readiness probes evaluated without hidden side effects.",
            plan.get("capability_probes") or {},
        ),
        node_run_step(
            "build-execution-plan",
            "Build node execution plan",
            "skipped" if missing or config_status == "failed" else "done",
            "Inputs or required config are incomplete." if missing or config_status == "failed" else f"Prepared {mode} execution plan.",
            {
                "mode": mode,
                "real_execution_enabled": mode == "real-node" and real_supported,
                "reason": (
                    "Real execution will call the configured Agent Executor for this node."
                    if mode == "real-node" and real_supported
                    else "Real execution is only enabled for nodes with an explicit executor adapter."
                ),
            },
        ),
        node_run_step(
            "execute-or-dry-run",
            "Execute or dry-run node",
            "waiting" if mode == "real-node" and real_supported and not missing and config_status != "failed" else "blocked" if mode == "real-node" else "skipped",
            "Waiting for Hermes Agent Skill execution." if mode == "real-node" and real_supported and not missing and config_status != "failed" else "Real node execution is not available for this node." if mode == "real-node" else "No business node was executed in this diagnostic run.",
            {"mode": mode, "side_effects": plan.get("side_effects") or {}},
        ),
        node_run_step(
            "validate-outputs",
            "Validate declared outputs",
            "waiting" if mode == "real-node" and real_supported and not missing and config_status != "failed" else "skipped",
            "Waiting for real node outputs." if mode == "real-node" and real_supported and not missing and config_status != "failed" else "No business execution occurred, so output validation is informational only.",
            {"declared_outputs": normalize_contract((node_static_config(sop, plan.get("node_id")) or {}).get("outputs", {}), "output")},
        ),
        node_run_step(
            "persist-to-github",
            "Persist to GitHub",
            "waiting" if mode == "real-node" and real_supported and bool(git.get("enabled", True)) and not missing and config_status != "failed" else "skipped",
            "Waiting for runtime harness to push selected paths to the Instance repo." if bool(git.get("enabled", True)) else "GitHub persistence is not attached for this Node Run.",
            {
                "repository": (plan.get("instance_context") or {}).get("repo", ""),
                "paths": git.get("paths") or ((plan.get("capability_overrides") or {}).get("git") or {}).get("paths") or [],
                "save_scope": git.get("save_scope") or "run",
            },
        ),
        node_run_step(
            "send-telegram-notification",
            "Send Telegram notification",
            "waiting" if mode == "real-node" and real_supported and bool(telegram.get("enabled", True)) and not missing and config_status != "failed" else "skipped",
            "Waiting for runtime harness to send the Instance Telegram notification." if bool(telegram.get("enabled", True)) else "Telegram notification is not attached for this Node Run.",
            {
                "chat_id": ((telegram.get("chat_id") or {}).get("masked_value") if isinstance(telegram.get("chat_id"), dict) else ""),
                "save_scope": telegram.get("save_scope") or "run",
            },
        ),
    ]
    return steps


def node_run_events_from_steps(node_run_id, node_id, steps, timestamp):
    events = []
    for index, step in enumerate(steps, start=1):
        events.append({
            "sequence": index,
            "event": f"node_run.step.{step.get('status')}",
            "node_run_id": node_run_id,
            "node_id": node_id,
            "step_id": step.get("id"),
            "ts": timestamp,
            "data": {"title": step.get("title"), "summary": step.get("summary")},
        })
    return events


def node_run_status_from_steps(steps):
    statuses = [str(step.get("status") or "") for step in steps]
    if "failed" in statuses:
        return "failed"
    if "blocked" in statuses:
        return "blocked"
    if "needs_input" in statuses:
        return "needs_input"
    if "running" in statuses:
        return "running"
    if "waiting" in statuses:
        return "running"
    return "done"


def real_node_stage_script(node_id, sop=None):
    safe_node = str(node_id or "").strip()
    if not safe_node or not re.match(r"^[A-Za-z0-9_-]+$", safe_node):
        return None
    plugin_dir = plugin_root() / "youtube-wiki"
    static = node_static_config(sop, safe_node) if isinstance(sop, dict) else {}
    executor = (static or {}).get("executor") if isinstance((static or {}).get("executor"), dict) else {}
    skill_name = str(executor.get("skill") or (static or {}).get("skill") or f"sop-{safe_node}").strip()
    skill_dirs = []
    for directory in (plugin_dir / "skills" / skill_name, plugin_dir / "skills" / f"sop-{safe_node}"):
        if directory not in skill_dirs:
            skill_dirs.append(directory)
    entry = str(executor.get("entry") or "").strip()
    candidates = []
    if entry:
        entry_path = Path(entry)
        if entry_path.is_absolute():
            candidates.append(entry_path)
        else:
            candidates.extend([directory / entry for directory in skill_dirs])
    for directory in skill_dirs:
        candidates.extend([
            directory / "scripts" / f"run_{safe_node.replace('-', '_')}.sh",
            directory / "scripts" / f"run_{safe_node}.sh",
            directory / "scripts" / f"run_{safe_node.replace('-', '_')}.py",
            directory / "scripts" / f"run_{safe_node}.py",
        ])
    return next((path for path in candidates if path.exists()), None)


def node_real_execution_supported(node_id, sop=None):
    if real_node_stage_script(node_id, sop) is not None:
        return True
    if isinstance(sop, dict):
        static = node_static_config(sop, node_id) or {}
        executor = static.get("executor") if isinstance(static.get("executor"), dict) else {}
        executor_type = str(executor.get("type") or "").strip().lower()
        skill_name = str(executor.get("skill") or "").strip()
        agent = str(executor.get("agent") or "").strip().lower()
        if skill_name and (executor_type in {"agent-skill", "skill"} or agent == "hermes"):
            return True
    return False


def node_stage_command(script, wiki, run_id, pipeline_id):
    if not script:
        return []
    executable = "python3" if str(script).endswith(".py") else "bash"
    return [executable, str(script), str(wiki), run_id, pipeline_id]


def node_run_step_by_id(steps, step_id):
    return next((step for step in steps if step.get("id") == step_id), None)


def update_node_run_step(steps, step_id, status, summary="", detail=None, started_at=None, finished_at=None, elapsed_ms=None):
    step = node_run_step_by_id(steps, step_id)
    if not step:
        return
    step["status"] = status
    if summary:
        step["summary"] = summary
    if isinstance(detail, dict):
        step["detail"] = detail
    if started_at:
        step["started_at"] = started_at
    if finished_at:
        step["finished_at"] = finished_at
    if elapsed_ms is not None:
        step["elapsed_ms"] = elapsed_ms


def real_node_execution_timeout(plan):
    configured = os.environ.get("NODE_RUN_REAL_TIMEOUT_SECONDS", "")
    if configured:
        try:
            return max(60, int(configured))
        except ValueError:
            pass
    worker = ((plan.get("resolved_config") or {}).get("youtube_research_worker") or {}).get("timeout") or {}
    try:
        return max(60, int(worker.get("value") or 1200) + 300)
    except (TypeError, ValueError):
        return 1500


def node_run_resolved_input_item(plan, name):
    for item in plan.get("resolved_inputs") or []:
        if item.get("name") == name and item.get("resolved"):
            return item
    return {}


def normalize_node_run_input_paths(value):
    values = value if isinstance(value, list) else [value]
    paths = []
    for raw in values:
        text = str(raw or "").strip()
        if not text or text.startswith(("http://", "https://")):
            continue
        path = Path(text)
        if path.is_absolute() or ".." in path.parts:
            continue
        paths.append(path.as_posix())
    return ordered_unique(paths)


def ensure_generated_report_fixture(wiki, relative_path, source_url):
    path = safe_artifact_path(wiki, relative_path)
    if not path or path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    title = "Rick Astley - Never Gonna Give You Up"
    path.write_text(
        "\n".join([
            f"# {title}",
            "",
            f"source_url: {source_url}",
            "",
            "## 摘要",
            "",
            "这是一份 Node Run 生成的测试分析报告，用于验证 wiki-build 节点能消费 notebooklm-research 的 reports 契约。",
            "",
            "## 关键实体",
            "",
            "- Rick Astley：英国歌手。",
            "- Never Gonna Give You Up：1987 年流行歌曲和音乐视频。",
            "",
            "## 关键概念",
            "",
            "- 音乐视频传播",
            "- 流行文化引用",
            "- 互联网迷因",
            "",
        ]),
        encoding="utf-8",
    )


def ensure_generated_deep_research_fixture(wiki, relative_path, source_url):
    path = safe_artifact_path(wiki, relative_path)
    if not path or path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join([
            "# YouTube 深度研究补充：Rick Astley - Never Gonna Give You Up",
            "",
            f"- YouTube: {source_url}",
            "",
            "## 摘要",
            "",
            "这是一份 Node Run 生成的测试深度研究报告，用于验证下游 wiki-build 能消费任意上游 Node Run 产物。",
            "",
            "## 关键要点",
            "",
            "- 音乐视频在流行文化中的长期传播。",
            "- 互联网语境会重新激活旧内容的知识价值。",
            "",
        ]),
        encoding="utf-8",
    )


def node_run_manifest_value_type(path):
    suffix = Path(str(path or "")).suffix.lower()
    if suffix in {".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".csv", ".log"}:
        return TEXT_FORMATS.get(suffix, suffix.lstrip("."))
    return "binary" if suffix else "text"


def node_run_target_file(target_dir, index, source_path="", default_suffix=".txt"):
    suffix = Path(str(source_path or "")).suffix.lower() or default_suffix
    if suffix and not suffix.startswith("."):
        suffix = f".{suffix}"
    return target_dir / f"{index:04d}{suffix or default_suffix}"


def write_node_run_io_manifest(path, *, kind, node_run_id, node_id, source_mode, items, extra=None):
    payload = {
        "version": 1,
        "kind": kind,
        "node_run_id": node_run_id,
        "node_id": node_id,
        "source_mode": source_mode,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": items,
    }
    if isinstance(extra, dict):
        payload.update({k: v for k, v in extra.items() if v not in (None, "")})
    write_json(path, payload)
    return payload


def node_run_source_manifest_items(sop, source_node_run_id):
    wiki = Path(sop["wiki_local_path"]).expanduser().resolve()
    source_node_run_id = sanitize_node_run_id(source_node_run_id)
    if not source_node_run_id:
        return []

    output_dir = node_run_existing_output_dir(sop, source_node_run_id)
    manifest = read_json(output_dir / "manifest.json") or {}
    rows = []
    raw_items = manifest.get("items") if isinstance(manifest.get("items"), list) else []
    if not raw_items and isinstance(manifest.get("produced"), list):
        raw_items = manifest.get("produced")
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_path = str(item.get("path") or "").strip()
        if not item_path:
            continue
        path_obj = Path(item_path)
        if path_obj.is_absolute() or ".." in path_obj.parts:
            continue
        source_rel = item_path if item_path.startswith("raw/") else safe_relative_file(wiki, output_dir / item_path)
        source_file = safe_artifact_path(wiki, source_rel)
        if not source_file or not source_file.is_file():
            continue
        rows.append({
            **item,
            "source_path": source_rel,
            "source_node": item.get("source_node") or manifest.get("node_id") or "",
            "source_run_id": item.get("source_run_id") or source_node_run_id,
        })
    if rows:
        return rows

    if output_dir.exists():
        for path in sorted(output_dir.rglob("*")):
            if not path.is_file() or path.name in {"manifest.json", "report.json"}:
                continue
            rel = safe_relative_file(wiki, path)
            if rel:
                rows.append({
                    "path": path.relative_to(output_dir).as_posix(),
                    "source": "node-run",
                    "source_run_id": source_node_run_id,
                    "source_path": rel,
                    "value_type": node_run_manifest_value_type(path),
                })
    if rows:
        return rows

    result = read_json(node_run_workspace(sop, source_node_run_id) / "result.json") or {}
    source_node = str(result.get("node_id") or "")
    for artifact in result.get("business_artifacts") or result.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        source_rel = str(artifact.get("path") or "").strip()
        source_file = safe_artifact_path(wiki, source_rel)
        if not source_file or not source_file.is_file():
            continue
        rows.append({
            "path": source_file.name,
            "source": "node-run",
            "source_node": source_node,
            "source_run_id": source_node_run_id,
            "source_path": source_rel,
            "value_type": artifact.get("format") or node_run_manifest_value_type(source_file),
            "output": artifact.get("output") or "",
        })
    return rows


def copy_node_run_input_file(wiki, source_rel, target_dir, index, item=None):
    source_file = safe_artifact_path(wiki, source_rel)
    if not source_file or not source_file.is_file():
        return None
    target = node_run_target_file(target_dir, index, source_file.name, ".txt")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_file, target)
    target_rel = target.relative_to(target_dir).as_posix()
    preview = ""
    value_type = str((item or {}).get("value_type") or node_run_manifest_value_type(target))
    if value_type in {"text", "markdown", "json", "jsonl", "yaml", "yml", "csv", "log"}:
        try:
            preview = target.read_text(encoding="utf-8", errors="replace")[:1000]
        except OSError:
            preview = ""
    return {
        "path": target_rel,
        "source": str((item or {}).get("source") or "file"),
        "value_type": value_type,
        "source_path": source_rel,
        "source_node": (item or {}).get("source_node") or "",
        "source_run_id": (item or {}).get("source_run_id") or "",
        "source_output": (item or {}).get("output") or (item or {}).get("source_output") or "",
        "input_name": (item or {}).get("input_name") or "",
        "target_input": (item or {}).get("target_input") or (item or {}).get("input_name") or "",
        "value_preview": preview,
    }


def write_node_run_input_text(target_dir, index, text, item=None, suffix=".txt"):
    target = node_run_target_file(target_dir, index, suffix, suffix)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(text or ""), encoding="utf-8")
    return {
        "path": target.relative_to(target_dir).as_posix(),
        "source": str((item or {}).get("source") or "manual"),
        "value_type": "text",
        "source_path": "",
        "source_node": (item or {}).get("source_node") or "",
        "source_run_id": (item or {}).get("source_run_id") or "",
        "source_output": (item or {}).get("source_output") or "",
        "input_name": (item or {}).get("input_name") or "",
        "target_input": (item or {}).get("target_input") or (item or {}).get("input_name") or "",
        "value_preview": str(text or "")[:1000],
    }


def node_run_input_resolved_values(items):
    values = {}
    for item in items or []:
        key = str(item.get("target_input") or item.get("input_name") or item.get("source_output") or "").strip()
        if not key:
            continue
        preview = item.get("value_preview")
        if isinstance(preview, str) and preview:
            values.setdefault(key, preview.strip())
    return values


def json_path_lookup(data, path):
    text = str(path or "").strip()
    if not text.startswith("$."):
        return None
    current = data
    for part in text[2:].split("."):
        if isinstance(current, dict) and part in current:
            current = current.get(part)
        else:
            return None
    return current


def node_input_resolvers(spec):
    resolvers = spec.get("resolvers") if isinstance(spec, dict) else []
    if isinstance(resolvers, dict):
        resolvers = [resolvers]
    if not isinstance(resolvers, list):
        resolvers = []
    rows = []
    for item in resolvers:
        if isinstance(item, str):
            rows.append({"kind": item})
        elif isinstance(item, dict):
            rows.append(dict(item))
    return rows


def validate_resolved_input_value(value, spec):
    text = str(value or "").strip()
    if not text:
        return False, "resolved value is blank"
    value_type = str((spec or {}).get("value_type") or (spec or {}).get("type") or "").strip().lower()
    if value_type == "url" and not re.match(r"^https?://\S+$", text):
        return False, "resolved value is not a valid url"
    if value_type == "json":
        try:
            json.loads(text)
        except Exception:
            return False, "resolved value is not valid json"
    return True, ""


def resolve_scalar_input_from_item(wiki, item, spec):
    materialized = str(item.get("materialized_path") or "").strip()
    path = safe_artifact_path(wiki, materialized)
    if not path or not path.is_file():
        return "", "", f"materialized input file is missing: {materialized}"
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return "", "", str(exc)
    resolvers = node_input_resolvers(spec)
    if not resolvers:
        return "", "", "input contract has no resolver"
    selected_resolver = str(item.get("resolver") or "").strip()
    if selected_resolver:
        preferred = []
        fallback = []
        for resolver in resolvers:
            resolver_id = str(resolver.get("id") or resolver.get("name") or resolver.get("kind") or resolver.get("type") or "").strip()
            if resolver_id == selected_resolver:
                preferred.append(resolver)
            else:
                fallback.append(resolver)
        resolvers = preferred or resolvers
    for resolver in resolvers:
        kind = str(resolver.get("kind") or resolver.get("type") or "").strip()
        resolver_id = str(resolver.get("id") or resolver.get("name") or kind).strip()
        value = None
        if kind == "direct":
            value = content.strip()
        elif kind in {"whole_file", "text"}:
            value = content
        elif kind == "json_path":
            try:
                value = json_path_lookup(json.loads(content), resolver.get("path"))
            except Exception:
                value = None
        elif kind == "regex":
            pattern = str(resolver.get("pattern") or "").strip()
            if pattern:
                try:
                    match = re.search(pattern, content)
                except re.error:
                    match = None
                if match:
                    value = match.group(1) if match.groups() else match.group(0)
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        ok, reason = validate_resolved_input_value(value, spec)
        if ok:
            return str(value).strip(), resolver_id, ""
        last_reason = reason
    return "", "", locals().get("last_reason") or "no resolver matched this input"


def validate_materialized_node_inputs(sop, node_id, items, source_mode):
    wiki = Path(sop["wiki_local_path"]).expanduser().resolve()
    static = node_static_config(sop, node_id) or {}
    required = normalize_contract(static.get("inputs", {}), "input")
    optional = normalize_contract(static.get("optional_inputs", {}), "input")
    contracts = {**optional, **required}
    required_names = [name for name, spec in required.items() if bool((spec or {}).get("required", True))]
    resolved_values = {}
    errors = []
    resolutions = []
    items_by_input = {}
    for item in items or []:
        target = str(item.get("target_input") or item.get("input_name") or item.get("source_output") or "").strip()
        if target:
            items_by_input.setdefault(target, []).append(item)
    for input_name in ordered_unique([*contracts.keys(), *items_by_input.keys()]):
        spec = contracts.get(input_name) or {"kind": "file", "required": False}
        kind = str(spec.get("input_kind") or spec.get("kind") or spec.get("type") or "auto").strip().lower()
        candidates = items_by_input.get(input_name) or []
        if not candidates:
            if input_name in required_names:
                errors.append({
                    "input": input_name,
                    "reason": "required input has no mapped source output",
                    "source_mode": source_mode,
                })
            continue
        if kind in {"scalar", "string", "text", "url", "number"} and not node_input_resolvers(spec):
            value = ""
            if candidates:
                first = candidates[0]
                preview = first.get("value_preview")
                if isinstance(preview, str) and preview.strip():
                    value = preview.strip()
                    first["resolved_value"] = value
                    resolved_values[input_name] = value
                    resolutions.append({
                        "input": input_name,
                        "source_outputs": [item.get("source_output") or item.get("output") for item in candidates],
                        "materialized_paths": [item.get("materialized_path") for item in candidates if item.get("materialized_path")],
                        "value_preview": value[:1000],
                    })
                    continue
            if input_name in required_names:
                errors.append({
                    "input": input_name,
                    "reason": "required scalar/text input was not materialized",
                    "source_outputs": [item.get("source_output") or item.get("output") for item in candidates],
                })
        elif kind in {"scalar", "string", "text", "url", "number", "auto"} and node_input_resolvers(spec):
            value = ""
            resolver_id = ""
            reason = ""
            for item in candidates:
                value, resolver_id, reason = resolve_scalar_input_from_item(wiki, item, spec)
                if value:
                    item["resolved_value"] = value
                    item["resolver"] = item.get("resolver") or resolver_id
                    resolved_values[input_name] = value
                    resolutions.append({
                        "input": input_name,
                        "source_output": item.get("source_output") or item.get("output") or "",
                        "materialized_path": item.get("materialized_path") or "",
                        "resolver": resolver_id,
                        "value_preview": value[:1000],
                    })
                    break
            if not value:
                errors.append({
                    "input": input_name,
                    "reason": reason or "mapped source output could not satisfy scalar input contract",
                    "source_outputs": [item.get("source_output") or item.get("output") for item in candidates],
                })
        else:
            paths = [str(item.get("materialized_path") or "").strip() for item in candidates if item.get("materialized_path")]
            if paths:
                resolved_values[input_name] = paths if len(paths) > 1 else paths[0]
                resolutions.append({
                    "input": input_name,
                    "source_outputs": [item.get("source_output") or item.get("output") for item in candidates],
                    "materialized_paths": paths,
                })
            elif input_name in required_names:
                errors.append({"input": input_name, "reason": "required file input was not materialized"})
    return {
        "status": "failed" if errors else "passed",
        "errors": errors,
        "resolved_values": resolved_values,
        "resolutions": resolutions,
    }


def materialize_resolved_input_value(wiki, target_dir, index, resolved, source_url):
    value = resolved.get("value")
    values = value if isinstance(value, list) else [value]
    rows = []
    for raw in values:
        if is_blank_value(raw):
            continue
        text = str(raw).strip()
        if resolved.get("provenance") in {"generated-fixture", "deepseek-mock-fallback"}:
            if resolved.get("name") == "reports":
                ensure_generated_report_fixture(wiki, text, source_url)
            if resolved.get("name") in {"deep_research", "analysis_file"}:
                ensure_generated_deep_research_fixture(wiki, text, source_url)
        source_file = safe_artifact_path(wiki, text)
        if source_file and source_file.is_file():
            row = copy_node_run_input_file(wiki, text, target_dir, index + len(rows), {
                "source": resolved.get("provenance") or "resolved-input",
                "input_name": resolved.get("name") or "",
                "source_path": text,
            })
            if row:
                rows.append(row)
            continue
        if source_file and source_file.is_dir():
            for child in sorted(source_file.rglob("*")):
                if not child.is_file():
                    continue
                rel = safe_relative_file(wiki, child)
                row = copy_node_run_input_file(wiki, rel, target_dir, index + len(rows), {
                    "source": resolved.get("provenance") or "resolved-input",
                    "input_name": resolved.get("name") or "",
                    "source_path": rel,
                })
                if row:
                    rows.append(row)
            continue
        row = write_node_run_input_text(target_dir, index + len(rows), text, {
            "source": resolved.get("provenance") or "resolved-input",
            "input_name": resolved.get("name") or "",
        })
        rows.append(row)
    return rows


def materialize_node_run_inputs(sop, node_run_id, node_id, plan, ctx):
    wiki = Path(sop["wiki_local_path"]).expanduser().resolve()
    target_dir = node_run_input_sources_dir(sop, node_run_id)
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    source_item = node_run_resolved_input_item(plan, "source_url") or node_run_resolved_input_item(plan, "url")
    source_url = source_item.get("value") if source_item else ""
    if is_blank_value(source_url) and (plan.get("input_source") or "generated-fixture") != "existing-node-run":
        source_url = ctx.get("source_url") or ctx.get("url") or "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    items = []
    source_mode = plan.get("input_source") or "generated-fixture"
    source_node_run_id = sanitize_node_run_id(plan.get("source_node_run_id") or "")
    manual_resolved_items = [
        item for item in list(plan.get("resolved_inputs") or []) + list(plan.get("optional_inputs") or [])
        if item.get("resolved") and item.get("provenance") == "manual"
    ]
    manual_targets = {str(item.get("name") or item.get("target_input") or "").strip() for item in manual_resolved_items}
    if source_mode == "existing-node-run":
        for resolved in manual_resolved_items:
            rows = materialize_resolved_input_value(wiki, target_dir, len(items) + 1, resolved, str(source_url or ""))
            items.extend(rows)
        relay_mode = plan.get("relay_mode") or "auto_by_target_inputs"
        selected_outputs = plan.get("selected_outputs") or []
        relay_mappings = plan.get("relay_mappings") or []
        relay_selection = plan.get("relay_selection") if isinstance(plan.get("relay_selection"), dict) else {}
        if not relay_selection.get("matched_items"):
            relay_selection = node_run_relay_selection_plan(sop, node_id, source_node_run_id, relay_mode, selected_outputs, relay_mappings)
        for source_item in relay_selection.get("matched_items") or []:
            target_input = str(source_item.get("target_input") or source_item.get("input_name") or source_item.get("source_output") or "").strip()
            if target_input in manual_targets:
                continue
            source_rel = str(source_item.get("source_path") or "")
            row = copy_node_run_input_file(wiki, source_rel, target_dir, len(items) + 1, {
                **source_item,
                "source": "node-run",
            })
            if row:
                items.append(row)
        plan["relay_selection"] = mask_data(relay_selection)
    else:
        resolved_items = list(plan.get("resolved_inputs") or []) + [
            item for item in (plan.get("optional_inputs") or [])
            if item.get("resolved")
        ]
        for resolved in resolved_items:
            rows = materialize_resolved_input_value(wiki, target_dir, len(items) + 1, resolved, str(source_url or ""))
            items.extend(rows)

    if not items and source_url:
        items.append(write_node_run_input_text(target_dir, 1, source_url, {
            "source": source_mode,
            "input_name": "source_url",
        }))

    for item in items:
        rel = safe_relative_file(wiki, target_dir / item.get("path", ""))
        if rel:
            item["materialized_path"] = rel
    input_validation = validate_materialized_node_inputs(sop, node_id, items, source_mode)
    relay_items = [
        item for item in items
        if item.get("source") == "node-run" or item.get("source_node_run_id") or item.get("source_output")
    ]
    relay_context = relay_context_payload(plan, relay_items, input_validation) if source_mode == "existing-node-run" else {}
    resolution_trace = relay_context.get("items") or []
    brief = relay_context.get("brief") or relay_context_brief(plan, input_validation)

    manifest_path = node_run_manifest_path(sop, node_run_id, "input")
    manifest = write_node_run_io_manifest(
        manifest_path,
        kind="input",
        node_run_id=node_run_id,
        node_id=node_id,
        source_mode=source_mode,
        items=items,
        extra={
            "source_node_run_id": source_node_run_id,
            "relay_mode": plan.get("relay_mode") or "",
            "selected_outputs": plan.get("selected_outputs") or [],
            "matched_outputs": (plan.get("relay_selection") or {}).get("matched_outputs") or [],
            "relay_mappings": plan.get("relay_mappings") or [],
            "input_validation": input_validation,
            "edge_contract": (plan.get("relay_selection") or {}).get("edge_contract") or plan.get("edge_contract") or {},
            "node_execution_guide": plan.get("node_execution_guide") or {},
            "workflow_revision": plan.get("workflow_revision") or {},
            "relay_context": relay_context,
            "relay_context_brief": brief,
            "resolution_trace": resolution_trace,
        },
    )
    input_files = []
    for item in items:
        rel = safe_relative_file(wiki, target_dir / item.get("path", ""))
        if rel:
            input_files.append(rel)
    return {
        "source_url": (input_validation.get("resolved_values") or {}).get("source_url") or node_run_input_resolved_values(items).get("source_url") or node_run_input_resolved_values(items).get("url") or source_url,
        "manifest": manifest,
        "manifest_path": safe_relative_file(wiki, manifest_path),
        "directory": safe_relative_file(wiki, target_dir),
        "files": ordered_unique(input_files),
        "markdown_files": [path for path in ordered_unique(input_files) if Path(path).suffix.lower() == ".md"],
        "text_files": [path for path in ordered_unique(input_files) if Path(path).suffix.lower() in {".md", ".txt"}],
        "resolved_values": {**node_run_input_resolved_values(items), **(input_validation.get("resolved_values") or {})},
        "relay_selection": plan.get("relay_selection") or {},
        "input_validation": input_validation,
        "edge_contract": (plan.get("relay_selection") or {}).get("edge_contract") or plan.get("edge_contract") or {},
        "node_execution_guide": plan.get("node_execution_guide") or {},
        "workflow_revision": plan.get("workflow_revision") or {},
        "relay_context": relay_context,
        "relay_context_brief": brief,
        "resolution_trace": resolution_trace,
    }


def node_run_materialized_paths_for_inputs(input_info, input_names, suffixes=None):
    names = set(input_names or [])
    suffixes = {item.lower() for item in (suffixes or [])}
    rows = []
    manifest = input_info.get("manifest") if isinstance(input_info.get("manifest"), dict) else {}
    for item in manifest.get("items") or []:
        if not isinstance(item, dict):
            continue
        target = str(item.get("target_input") or item.get("input_name") or item.get("source_output") or "").strip()
        if names and target not in names:
            continue
        relative = str(item.get("materialized_path") or "").strip()
        if not relative:
            continue
        if suffixes and Path(relative).suffix.lower() not in suffixes:
            continue
        rows.append(relative)
    return ordered_unique(rows)


def apply_resolved_inputs_to_pipeline_context(sop, wiki, ctx, plan, node_id, node_run_id):
    input_info = materialize_node_run_inputs(
        {**sop, "wiki_local_path": str(wiki), **({"id": plan.get("instance_id")} if plan.get("instance_id") else {})},
        node_run_id,
        node_id,
        plan,
        ctx,
    )
    input_validation = input_info.get("input_validation") if isinstance(input_info.get("input_validation"), dict) else {}
    if input_validation.get("status") == "failed":
        raise NodeRunInputResolutionError(
            "Required node inputs could not be resolved from the selected relay outputs.",
            {
                "input_validation": input_validation,
                "input_manifest": input_info.get("manifest_path"),
                "input_directory": input_info.get("directory"),
                "relay_selection": input_info.get("relay_selection") or {},
                "edge_contract": input_info.get("edge_contract") or {},
                "node_execution_guide": input_info.get("node_execution_guide") or {},
                "workflow_revision": input_info.get("workflow_revision") or {},
                "relay_context": input_info.get("relay_context") or {},
                "relay_context_brief": input_info.get("relay_context_brief") or "",
                "resolution_trace": input_info.get("resolution_trace") or [],
            },
        )
    source_item = node_run_resolved_input_item(plan, "source_url") or node_run_resolved_input_item(plan, "url")
    source_url = input_info.get("source_url") or (source_item.get("value") if source_item else "")
    if isinstance(source_url, str) and source_url.startswith("node-run:"):
        source_url = ""
    if is_blank_value(source_url) and (plan.get("input_source") or "generated-fixture") != "existing-node-run":
        source_url = ctx.get("source_url") or ctx.get("url") or "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    if not is_blank_value(source_url):
        ctx["source_url"] = source_url
    ctx["source_type"] = ctx.get("source_type") or "youtube"
    ctx["node_run_resolved_inputs"] = {
        item.get("name"): item.get("value")
        for item in list(plan.get("resolved_inputs") or []) + list(plan.get("optional_inputs") or [])
        if item.get("name")
    }
    if input_info.get("resolved_values"):
        ctx["node_run_resolved_inputs"].update(input_info.get("resolved_values") or {})
    ctx["node_run_input_manifest"] = input_info.get("manifest_path")
    ctx["node_run_input_files"] = input_info.get("files") or []
    ctx["node_run_relay_context"] = input_info.get("relay_context") or {}
    ctx["node_run_relay_context_brief"] = input_info.get("relay_context_brief") or ""
    ctx["node_run_resolution_trace"] = input_info.get("resolution_trace") or []
    ctx["workflow_revision"] = input_info.get("workflow_revision") or plan.get("workflow_revision") or {}

    source_mode = plan.get("input_source") or "generated-fixture"
    reports_item = node_run_resolved_input_item(plan, "reports")
    report_paths = node_run_materialized_paths_for_inputs(input_info, {"reports"}, {".md", ".txt"})
    if not report_paths and source_mode != "existing-node-run":
        report_paths = normalize_node_run_input_paths(reports_item.get("value") if reports_item else [])
    if report_paths:
        if (reports_item or {}).get("provenance") in {"generated-fixture", "deepseek-mock-fallback"}:
            for relative in report_paths:
                ensure_generated_report_fixture(wiki, relative, source_url)
        stage_b = ctx.get("stage_b") if isinstance(ctx.get("stage_b"), dict) else {}
        stage_b["output_files"] = report_paths
        ctx["stage_b"] = stage_b

    deep_item = node_run_resolved_input_item(plan, "deep_research") or node_run_resolved_input_item(plan, "analysis_file")
    deep_paths = node_run_materialized_paths_for_inputs(input_info, {"deep_research", "analysis_file"}, {".md", ".txt"})
    if not deep_paths and source_mode != "existing-node-run":
        deep_paths = normalize_node_run_input_paths(deep_item.get("value") if deep_item else [])
    if deep_paths:
        stage_b2 = ctx.get("stage_b2") if isinstance(ctx.get("stage_b2"), dict) else {}
        stage_b2["analysis_file"] = deep_paths[0]
        stage_b2["output_files"] = deep_paths
        ctx["stage_b2"] = stage_b2

    return ctx, input_info


def write_node_execution_request(sop, wiki, node_run_id, node_id, plan, input_info):
    source_node_run_id = sanitize_node_run_id(plan.get("source_node_run_id") or "")
    handoff_sources = []
    if source_node_run_id:
        source_output_dir = node_run_existing_output_dir(sop, source_node_run_id)
        handoff_sources.append({
            "from_node": ((plan.get("relay_selection") or {}).get("source_node") or ""),
            "from_run_id": source_node_run_id,
            "outputs_dir": safe_relative_file(wiki, source_output_dir),
            "manifest": safe_relative_file(wiki, source_output_dir / "manifest.json"),
        })
    request_path = node_run_workspace(sop, node_run_id) / "request.json"
    entry_inputs = {}
    resolved_values = input_info.get("resolved_values") if isinstance(input_info.get("resolved_values"), dict) else {}
    for key, value in resolved_values.items():
        if not is_blank_value(value):
            entry_inputs[key] = value
    request = {
        "schema": "node-execution-request/v1",
        "runtime_id": plan.get("runtime_id") or runtime_info().get("runtime_id", ""),
        "instance_id": plan.get("instance_id") or sop.get("id") or sop.get("instance_id") or "",
        "workflow_id": plan.get("workflow_id") or sop.get("id") or sop.get("name") or "",
        "node_id": node_id,
        "run_id": node_run_id,
        "entry_inputs": entry_inputs,
        "handoff_sources": handoff_sources,
        "handoff_instruction": plan.get("relay_instruction") or (plan.get("node_execution_guide") or {}).get("prompt") or "",
        "input_manifest": input_info.get("manifest_path") or "",
        "input_directory": input_info.get("directory") or "",
        "outputs_dir": safe_relative_file(wiki, node_run_output_files_dir(sop, node_run_id)),
        "output_manifest": safe_relative_file(wiki, node_run_output_files_dir(sop, node_run_id) / "manifest.json"),
        "edge_contract": input_info.get("edge_contract") or plan.get("edge_contract") or {},
        "node_execution_guide": input_info.get("node_execution_guide") or plan.get("node_execution_guide") or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(request_path, request)
    return safe_relative_file(wiki, request_path), request


def prepare_real_node_context(sop, node_run_id, node_id, plan):
    wiki = Path(sop["wiki_local_path"]).expanduser()
    ctx_file = wiki / "raw" / "pipeline-context.json"
    ctx = read_json(ctx_file) or {}
    if not isinstance(ctx, dict):
        ctx = {}
    ctx, input_info = apply_resolved_inputs_to_pipeline_context(sop, wiki, ctx, plan, node_id, node_run_id)
    request_path, execution_request = write_node_execution_request(sop, wiki, node_run_id, node_id, plan, input_info)
    ctx.update({
        "pipeline_id": node_run_id,
        "node_run": {
            "node_run_id": node_run_id,
            "node_id": node_id,
            "mode": plan.get("mode"),
            "input_source": plan.get("input_source"),
            "base_run_id": plan.get("base_run_id"),
            "source_node_run_id": plan.get("source_node_run_id"),
            "input_manifest": input_info.get("manifest_path"),
            "input_directory": input_info.get("directory"),
            "input_files": input_info.get("files") or [],
            "execution_request": request_path,
            "execution_request_data": execution_request,
            "edge_contract": input_info.get("edge_contract") or plan.get("edge_contract") or {},
            "node_execution_guide": input_info.get("node_execution_guide") or plan.get("node_execution_guide") or {},
            "workflow_revision": input_info.get("workflow_revision") or plan.get("workflow_revision") or {},
            "relay_context": input_info.get("relay_context") or {},
            "relay_context_brief": input_info.get("relay_context_brief") or "",
            "resolution_trace": input_info.get("resolution_trace") or [],
            "capability_overrides": plan.get("capability_overrides") or {},
            "definition_scope_reports": plan.get("definition_scope_reports") or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    })
    write_json(ctx_file, ctx)
    return ctx


def write_shell_env_file(path, values):
    rows = []
    for key, value in sorted((values or {}).items()):
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(key or "")):
            continue
        if is_blank_value(value):
            continue
        rows.append(f"export {key}={shlex.quote(str(value))}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows).rstrip() + ("\n" if rows else ""), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return len(rows)


def node_run_resolved_env_values(sop, plan):
    context = node_run_config_context({
        "capability_overrides": (plan or {}).get("capability_overrides") or {},
    }, sop)
    values = {}
    for env_key, aliases in RUNTIME_CAPABILITY_ENV.items():
        resolved = node_run_config_lookup(context, env_key, aliases)
        if not is_blank_value(resolved.get("value")):
            values[env_key] = str(resolved.get("value"))

    telegram = ((plan or {}).get("resolved_config") or {}).get("telegram") or {}
    token_env = str(telegram.get("token_env") or "YOUTUBE_WIKI_TG_TOKEN")
    if token_env and token_env != "YOUTUBE_WIKI_TG_TOKEN":
        token = node_run_config_lookup(context, token_env)
        if is_blank_value(token.get("value")):
            token = node_run_config_lookup(context, "YOUTUBE_WIKI_TG_TOKEN", RUNTIME_CAPABILITY_ENV.get("YOUTUBE_WIKI_TG_TOKEN", []))
        if not is_blank_value(token.get("value")):
            values[token_env] = str(token.get("value"))
    return values


def node_run_subprocess_env(sop, node_run_id, plan):
    values = node_run_resolved_env_values(sop, plan)
    safe_id = sanitize_node_run_id(node_run_id) or f"node-run-{int(time.time())}"
    input_dir = node_run_input_sources_dir(sop, safe_id)
    output_dir = node_run_output_files_dir(sop, safe_id)
    override_dir = Path(os.environ.get("YOUTUBE_WIKI_NODE_RUN_ENV_DIR") or (Path.home() / ".cache" / "youtube-wiki" / "node-run-env")).expanduser()
    override_file = override_dir / f"{safe_id}.env"
    written = write_shell_env_file(override_file, values)
    env = {
        **os.environ,
        **values,
        "PATH": f"{Path.home() / '.local/bin'}:{Path.home() / 'bin'}:{os.environ.get('PATH', '')}",
        "YOUTUBE_WIKI_NODE_RUN": "1",
        "YOUTUBE_WIKI_NODE_RUN_ID": safe_id,
        "YOUTUBE_WIKI_NODE_EXECUTION_REQUEST": str(node_run_workspace(sop, safe_id) / "request.json"),
        "YOUTUBE_WIKI_NODE_RUN_INPUT_DIR": str(input_dir),
        "YOUTUBE_WIKI_NODE_RUN_INPUT_MANIFEST": str(input_dir / "manifest.json"),
        "YOUTUBE_WIKI_NODE_RUN_OUTPUT_DIR": str(output_dir),
        "YOUTUBE_WIKI_NODE_RUN_OUTPUT_MANIFEST": str(output_dir / "manifest.json"),
    }
    if written:
        env["YOUTUBE_WIKI_NODE_RUN_ENV_FILE"] = str(override_file)
    return env


def ordered_unique(values):
    seen = set()
    rows = []
    for value in values or []:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        rows.append(text)
    return rows


def safe_relative_file(wiki, path):
    try:
        candidate = Path(path).expanduser().resolve()
        return candidate.relative_to(wiki).as_posix()
    except Exception:
        return ""


def files_under_relative_dir(wiki, relative_dir):
    root = safe_artifact_path(wiki, relative_dir)
    if not root or not root.exists():
        return []
    if root.is_file():
        rel = safe_relative_file(wiki, root)
        return [rel] if rel else []
    rows = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = safe_relative_file(wiki, path)
        if not rel or rel.endswith("/runtime.env") or rel == "raw/pipeline-context.json":
            continue
        rows.append(rel)
    return rows


def collect_node_run_output_categories(sop, node_run_id, node_id, actual_outputs):
    wiki = Path(sop["wiki_local_path"]).expanduser().resolve()
    core_files = []
    for value in (actual_outputs or {}).values():
        values = value if isinstance(value, list) else [value]
        for relative in values:
            path = safe_artifact_path(wiki, relative)
            if path and path.is_file():
                rel = safe_relative_file(wiki, path)
                if rel:
                    core_files.append(rel)
    core_files = ordered_unique(core_files)

    raw_roots = []
    for relative in core_files:
        parts = Path(relative).parts
        if node_run_id in parts:
            index = parts.index(node_run_id)
            raw_roots.append(Path(*parts[:index + 1]) / "raw")
    for path in wiki.glob(f"raw/**/{node_run_id}/raw"):
        rel = safe_relative_file(wiki, path)
        if rel:
            raw_roots.append(Path(rel))

    core_set = set(core_files)
    raw_files = []
    for root in raw_roots:
        raw_files.extend(files_under_relative_dir(wiki, root.as_posix()))
    raw_files = ordered_unique(path for path in raw_files if path not in core_set)

    run_records = []
    run_records.extend(files_under_relative_dir(wiki, f"raw/pipeline-runs/{node_run_id}"))
    run_records.extend(files_under_relative_dir(wiki, f"raw/node-runs/{node_run_id}"))
    stage_events = safe_artifact_path(wiki, f"logs/stage-events/{node_run_id}.jsonl")
    if stage_events and stage_events.is_file():
        rel = safe_relative_file(wiki, stage_events)
        if rel:
            run_records.append(rel)
    run_records = ordered_unique(path for path in run_records if path not in core_set and path not in set(raw_files))
    node_run_outputs = files_under_relative_dir(wiki, f"raw/node-runs/{node_run_id}/outputs")
    node_run_outputs = ordered_unique(node_run_outputs)

    return {
        "core_outputs": {
            "title": "核心输出",
            "description": "节点声明 outputs 对应的业务结果。",
            "files": core_files,
            "count": len(core_files),
        },
        "raw_files": {
            "title": "节点原始文件",
            "description": "节点 Skill/Worker 产生的原始响应、字幕和中间文件。",
            "files": raw_files,
            "count": len(raw_files),
        },
        "run_records": {
            "title": "运行记录",
            "description": "Node Run 工作台、事件、状态和 capability 记录。",
            "files": run_records,
            "count": len(run_records),
        },
        "node_run_outputs": {
            "title": "统一输出目录",
            "description": "本次 Node Run 写入标准 outputs 目录的可中继产物，供下游节点作为目录输入。",
            "files": node_run_outputs,
            "count": len(node_run_outputs),
        },
    }


def node_run_actual_output_file_paths(actual_outputs):
    paths = []
    for value in (actual_outputs or {}).values():
        values = value if isinstance(value, list) else [value]
        for item in values:
            text = str(item or "").strip()
            if not text or "://" in text:
                continue
            paths.append(text)
    return ordered_unique(paths)


def node_run_core_output_rows(sop, node_run_id, node_id, declared_outputs, actual_outputs, artifacts):
    wiki = Path(sop["wiki_local_path"]).expanduser().resolve()
    artifact_by_path = {
        str(artifact.get("path") or ""): artifact
        for artifact in artifacts or []
        if isinstance(artifact, dict) and artifact.get("path")
    }
    rows = []
    for name, spec in (declared_outputs or {}).items():
        value = (actual_outputs or {}).get(name)
        output_type = str((spec or {}).get("type") or "").lower()
        if isinstance(value, list):
            files = []
            file_artifacts = []
            for relative in value:
                relative = str(relative or "").strip()
                if not relative:
                    continue
                files.append(relative)
                artifact = artifact_by_path.get(relative)
                if artifact:
                    file_artifacts.append(artifact)
            rows.append({
                "name": name,
                "kind": "files" if len(files) != 1 else "file",
                "type": output_type or ("files" if len(files) != 1 else "file"),
                "value": files,
                "files": files,
                "artifacts": file_artifacts,
                "declared": spec,
            })
            continue
        if isinstance(value, str) and value and "://" not in value:
            path = safe_artifact_path(wiki, value)
            if path and path.is_file():
                artifact = artifact_by_path.get(value) or artifact_record(sop, node_id, name, path, "actual-output")
                rows.append({
                    "name": name,
                    "kind": "file",
                    "type": output_type or "file",
                    "value": value,
                    "files": [value],
                    "artifacts": [artifact] if artifact else [],
                    "declared": spec,
                })
                continue
        rows.append({
            "name": name,
            "kind": "scalar",
            "type": output_type or "scalar",
            "value": value,
            "files": [],
            "artifacts": [],
            "declared": spec,
        })
    return rows


def node_run_business_artifacts_from_core(core_outputs):
    seen = set()
    rows = []
    for output in core_outputs or []:
        for artifact in output.get("artifacts") or []:
            if not isinstance(artifact, dict):
                continue
            key = artifact.get("path") or artifact.get("id")
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append(artifact)
    return rows


def node_run_relay_package(sop, node_run_id, node_id):
    wiki = Path(sop["wiki_local_path"]).expanduser().resolve()
    output_dir = node_run_existing_output_dir(sop, node_run_id)
    manifest_path = output_dir / "manifest.json"
    records = node_run_output_manifest_records(sop, node_run_id)
    items = []
    for record in records:
        artifact = artifact_record(sop, node_id, str(record.get("output") or "files"), record.get("file"), "node-run-output-manifest")
        items.append({
            "output": record.get("output") or "",
            "path": record.get("path") or "",
            "relative_path": record.get("path", "").replace(safe_relative_file(wiki, output_dir).rstrip("/") + "/", "", 1) if record.get("path") else "",
            "value_type": record.get("value_type") or node_run_manifest_value_type(record.get("file")),
            "source": record.get("source") or "",
            "source_node": record.get("source_node") or "",
            "source_run_id": record.get("source_run_id") or "",
            "source_path": record.get("source_path") or "",
            "artifact": artifact,
        })
    return {
        "kind": "relay-package",
        "output_directory": safe_relative_file(wiki, output_dir),
        "manifest_path": safe_relative_file(wiki, manifest_path),
        "item_count": len(items),
        "items": items,
    }


def node_run_execution_evidence(sop, node_run_id, node_id, artifacts):
    wiki = Path(sop["wiki_local_path"]).expanduser().resolve()
    evidence = []
    evidence.extend(node_run_agent_artifacts(sop, node_run_id, node_id))
    for relative, output_name, title in (
        (f"raw/node-runs/{node_run_id}/result.json", "node_run_result", "Node Run Result"),
        (f"raw/node-runs/{node_run_id}/events.jsonl", "node_run_events", "Node Run Events"),
        (f"raw/node-runs/{node_run_id}/executor.log", "executor_log", "Executor Log"),
        (f"logs/stage-events/{node_run_id}.jsonl", "stage_events", "Stage Events"),
    ):
        path = safe_artifact_path(wiki, relative)
        if not path or not path.is_file():
            continue
        record = artifact_record(sop, node_id, output_name, path, "execution-evidence")
        if record:
            record["title"] = title
            evidence.append(record)
    existing = {item.get("path") for item in evidence if isinstance(item, dict)}
    for artifact in artifacts or []:
        if not isinstance(artifact, dict):
            continue
        path = artifact.get("path")
        artifact_type = str(artifact.get("type") or "")
        resolution = str(artifact.get("resolution") or "")
        if path in existing:
            continue
        if artifact_type.startswith("node-run.") or resolution in {"node-run-agent", "execution-evidence"}:
            evidence.append(artifact)
            existing.add(path)
    return {"artifacts": evidence, "count": len(evidence)}


def hydrate_node_run_result_views(sop, result):
    if not isinstance(result, dict):
        return result
    node_run_id = sanitize_node_run_id(result.get("node_run_id") or result.get("pipeline_id") or "")
    node_id = str(result.get("node_id") or "")
    if not node_run_id or not node_id:
        return result
    static = node_static_config(sop, node_id) or {}
    declared_outputs = normalized_contract(static.get("outputs") or {}, "output")
    actual_outputs = result.get("actual_outputs") if isinstance(result.get("actual_outputs"), dict) else {}
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), list) else []
    core_outputs = node_run_core_output_rows(sop, node_run_id, node_id, declared_outputs, actual_outputs, artifacts)
    result["core_outputs"] = core_outputs
    result["business_artifacts"] = node_run_business_artifacts_from_core(core_outputs)
    result["relay_package"] = node_run_relay_package(sop, node_run_id, node_id)
    result["execution_evidence"] = node_run_execution_evidence(sop, node_run_id, node_id, artifacts)
    return result


def node_run_output_manifest_artifacts(sop, node_run_id, node_id):
    wiki = Path(sop["wiki_local_path"]).expanduser().resolve()
    output_dir = node_run_existing_output_dir(sop, node_run_id)
    manifest = read_json(output_dir / "manifest.json") or {}
    items = manifest.get("items") if isinstance(manifest.get("items"), list) else []
    if not items and isinstance(manifest.get("produced"), list):
        items = manifest.get("produced")
    artifacts = []
    if not items and output_dir.exists():
        items = [
            {"path": path.relative_to(output_dir).as_posix(), "output": "files"}
            for path in sorted(output_dir.rglob("*"))
            if path.is_file() and path.name != "manifest.json"
        ]
    for item in items:
        if not isinstance(item, dict):
            continue
        item_path = str(item.get("path") or "").strip()
        if not item_path:
            continue
        path_obj = Path(item_path)
        if path_obj.is_absolute() or ".." in path_obj.parts:
            continue
        rel = item_path if item_path.startswith("raw/") else safe_relative_file(wiki, output_dir / item_path)
        path = safe_artifact_path(wiki, rel)
        if not path or not path.is_file():
            continue
        record = artifact_record(sop, node_id, str(item.get("output") or "files"), path, "node-run-output-manifest")
        if record:
            record["metadata"] = {**(record.get("metadata") or {}), "manifest_item": mask_data(item)}
            artifacts.append(record)
    manifest_rel = safe_relative_file(wiki, output_dir / "manifest.json")
    manifest_path = safe_artifact_path(wiki, manifest_rel)
    if manifest_path and manifest_path.is_file():
        record = artifact_record(sop, node_id, "manifest", manifest_path, "node-run-output-manifest")
        if record:
            artifacts.append(record)
    return artifacts


def node_run_output_manifest_records(sop, node_run_id):
    wiki = Path(sop["wiki_local_path"]).expanduser().resolve()
    output_dir = node_run_existing_output_dir(sop, node_run_id)
    manifest = read_json(output_dir / "manifest.json") or {}
    items = manifest.get("items") if isinstance(manifest.get("items"), list) else []
    if not items and isinstance(manifest.get("produced"), list):
        items = manifest.get("produced")
    records = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_path = str(item.get("path") or "").strip()
        output_name = str(item.get("output") or "").strip()
        if not item_path or not output_name:
            continue
        path_obj = Path(item_path)
        if path_obj.is_absolute() or ".." in path_obj.parts:
            continue
        rel = item_path if item_path.startswith("raw/") else safe_relative_file(wiki, output_dir / item_path)
        path = safe_artifact_path(wiki, rel)
        if not path or not path.is_file():
            continue
        records.append({
            **item,
            "output": output_name,
            "path": rel,
            "file": path,
        })
    return records


def node_run_manifest_output_value(records, output_name, spec):
    matches = [record for record in records if record.get("output") == output_name]
    if not matches:
        return None
    output_type = str((spec or {}).get("type") or "").lower()
    if output_type in {"string", "text"}:
        first = matches[0].get("file")
        if first and first.is_file():
            return first.read_text(encoding="utf-8", errors="replace").strip()
        return ""
    paths = [record.get("path") for record in matches if record.get("path")]
    return ordered_unique(paths)


def node_run_input_info(sop, node_run_id, node_id):
    wiki = Path(sop["wiki_local_path"]).expanduser().resolve()
    manifest_path = node_run_manifest_path(sop, node_run_id, "input")
    manifest = read_json(manifest_path) or {}
    return {
        "input_directory": safe_relative_file(wiki, node_run_input_sources_dir(sop, node_run_id)),
        "input_manifest": safe_relative_file(wiki, manifest_path),
        "input_manifest_data": mask_data(manifest) if isinstance(manifest, dict) else {},
        "input_artifacts": artifacts_with_preview(sop, node_run_input_manifest_artifacts(sop, node_run_id, node_id)),
    }


def node_run_input_manifest_artifacts(sop, node_run_id, node_id):
    wiki = Path(sop["wiki_local_path"]).expanduser().resolve()
    input_dir = node_run_input_sources_dir(sop, node_run_id)
    manifest = read_json(input_dir / "manifest.json") or {}
    items = manifest.get("items") if isinstance(manifest.get("items"), list) else []
    artifacts = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_path = str(item.get("path") or "").strip()
        if not item_path:
            continue
        path_obj = Path(item_path)
        if path_obj.is_absolute() or ".." in path_obj.parts:
            continue
        rel = item_path if item_path.startswith("raw/") else safe_relative_file(wiki, input_dir / item_path)
        path = safe_artifact_path(wiki, rel)
        if not path or not path.is_file():
            continue
        output_name = str(item.get("source_output") or item.get("output") or item.get("input_name") or "input")
        record = artifact_record(sop, node_id, output_name, path, "node-run-input-manifest")
        if not record:
            continue
        source_path = str(item.get("source_path") or "").strip()
        if source_path:
            record["title"] = Path(source_path).name
        record["metadata"] = {
            **(record.get("metadata") or {}),
            "manifest_item": mask_data(item),
            "source_path": source_path,
            "source_node": item.get("source_node") or "",
            "source_run_id": item.get("source_run_id") or "",
            "source_output": item.get("source_output") or item.get("output") or "",
            "input_name": item.get("input_name") or "",
            "input_path": rel,
        }
        artifacts.append(record)
    return artifacts


def collect_real_node_outputs(sop, node_run_id, node_id, run_id):
    wiki = Path(sop["wiki_local_path"]).expanduser().resolve()
    workspace = run_workspace(sop, node_run_id)
    node_state = read_json(workspace / "nodes" / f"{node_id}.json") or {}
    capabilities = read_json(workspace / "nodes" / node_id / "capabilities.json") or {}
    context = (
        read_json(workspace / "context.json")
        or read_json(wiki / "raw" / "pipeline-context.json")
        or {}
    )
    static = node_static_config(sop, node_id) or {}
    declared_outputs = normalized_contract(static.get("outputs") or node_state.get("declared_outputs") or {}, "output")
    actual_outputs = {}
    artifacts = []

    recorded_outputs = node_state.get("actual_outputs") if isinstance(node_state.get("actual_outputs"), dict) else {}
    manifest_records = node_run_output_manifest_records(sop, node_run_id)
    for name, spec in declared_outputs.items():
        recorded_value = recorded_outputs.get(name)
        paths = recorded_value
        if isinstance(paths, str) and str(spec.get("type") or "").lower() not in {"string", "text"}:
            paths = [paths]
        records = []
        if isinstance(paths, list):
            for relative in paths:
                path = safe_artifact_path(wiki, relative)
                if path and path.is_file():
                    record = artifact_record(sop, node_id, name, path, "recorded")
                    if record:
                        records.append(record)
        if records:
            actual_outputs[name] = [record["path"] for record in records]
            artifacts.extend(records)
            continue

        manifest_value = node_run_manifest_output_value(manifest_records, name, spec)
        if manifest_value is not None and manifest_value != "" and manifest_value != []:
            actual_outputs[name] = manifest_value
            for record in node_run_output_manifest_artifacts(sop, node_run_id, node_id):
                if record.get("output") == name:
                    artifacts.append(record)
            continue

        if isinstance(recorded_value, str) and str(spec.get("type") or "").lower() in {"string", "text"}:
            actual_outputs[name] = recorded_value
            continue

        if not records:
            records = resolve_output_artifacts(
                sop,
                node_run_id,
                node_id,
                name,
                spec,
                context if isinstance(context, dict) else {},
                run_id,
                include_context=True,
                include_pattern=True,
            )
        actual_outputs[name] = [record["path"] for record in records]
        artifacts.extend(records)
    manifest_artifacts = node_run_output_manifest_artifacts(sop, node_run_id, node_id)
    existing_artifact_paths = {artifact.get("path") for artifact in artifacts if isinstance(artifact, dict)}
    artifacts.extend([artifact for artifact in manifest_artifacts if artifact.get("path") not in existing_artifact_paths])

    missing = [
        name for name, value in actual_outputs.items()
        if value is None or value == "" or value == []
    ]
    core_outputs = node_run_core_output_rows(sop, node_run_id, node_id, declared_outputs, actual_outputs, artifacts)
    return {
        "declared_outputs": declared_outputs,
        "actual_outputs": actual_outputs,
        "artifacts": artifacts,
        "core_outputs": core_outputs,
        "business_artifacts": node_run_business_artifacts_from_core(core_outputs),
        "relay_package": node_run_relay_package(sop, node_run_id, node_id),
        "execution_evidence": node_run_execution_evidence(sop, node_run_id, node_id, artifacts),
        "output_categories": collect_node_run_output_categories(sop, node_run_id, node_id, actual_outputs),
        "input_manifest": safe_relative_file(wiki, node_run_manifest_path(sop, node_run_id, "input")),
        "input_directory": safe_relative_file(wiki, node_run_input_sources_dir(sop, node_run_id)),
        "output_manifest": safe_relative_file(wiki, node_run_manifest_path(sop, node_run_id, "output")),
        "output_directory": safe_relative_file(wiki, node_run_output_files_dir(sop, node_run_id)),
        "validation": {
            "status": "passed" if not missing else "failed",
            "missing_outputs": missing,
            "unexpected_outputs": [],
        },
        "capabilities": capabilities if isinstance(capabilities, dict) else {},
        "node_state": node_state,
    }


def execute_real_node_run(sop, node_run_id, node_id, plan):
    if not node_real_execution_supported(node_id, sop):
        return {
            "status": "blocked",
            "summary": "Real node execution is not available for this node.",
            "detail": {"node_id": node_id},
            "actual_outputs": {},
            "artifacts": [],
            "validation": {"status": "skipped", "missing_outputs": [], "unexpected_outputs": []},
        }

    wiki = Path(sop["wiki_local_path"]).expanduser()
    script = real_node_stage_script(node_id, sop)
    executor_kind = node_run_executor_kind(sop, node_id, plan)
    if executor_kind == "legacy-shell" and (not script or not script.exists()):
        return {
            "status": "failed",
            "summary": "Stage wrapper script was not found.",
            "detail": {"script": str(script or "")},
            "actual_outputs": {},
            "artifacts": [],
            "validation": {"status": "failed", "missing_outputs": [], "unexpected_outputs": []},
        }

    started = datetime.now(timezone.utc)
    log_path = node_run_workspace(sop, node_run_id) / "executor.log"
    stage_command = node_stage_command(script, wiki, node_run_id, node_run_id)
    timeout = real_node_execution_timeout(plan)
    context = {}
    agent_request = {}
    receipt = {}
    stdout = ""
    stderr = ""
    returncode = 1
    timed_out = False
    command_for_detail = []
    input_resolution_error = {}
    try:
        context = prepare_real_node_context(sop, node_run_id, node_id, plan)
        env = node_run_subprocess_env(sop, node_run_id, plan)
        skill_name = node_run_skill_name(sop, node_id, plan)
        agent_request = render_node_run_agent_request(
            sop,
            node_run_id,
            node_id,
            plan,
            context,
            stage_command,
            skill_name,
        )
        if executor_kind == "legacy-shell":
            command_for_detail = stage_command
            completed = subprocess.run(
                stage_command,
                cwd=str(wiki),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        else:
            request_text = agent_request.get("rendered_request") or node_run_agent_path(sop, node_run_id, "request.md").read_text(encoding="utf-8")
            hermes_args = hermes_agent_command_args(skill_name, request_text)
            if not hermes_args:
                raise RuntimeError("Hermes CLI is not installed or is not on PATH for this Runtime")
            command_for_detail = hermes_args[:]
            command_for_detail[-1] = "<request.md>"
            completed = subprocess.run(
                hermes_args,
                cwd=str(wiki),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        returncode = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        try:
            node_run_agent_path(sop, node_run_id, "response.txt").write_text(
                (stdout + ("\n" if stdout and stderr else "") + stderr),
                encoding="utf-8",
            )
        except OSError:
            pass
        if executor_kind == "hermes" and returncode == 0:
            wait_for_real_node_completion(sop, node_run_id, node_id, min(timeout, 300), started)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        stderr = (stderr + f"\nnode real execution timed out after {timeout}s").strip()
        returncode = 124
    except NodeRunInputResolutionError as exc:
        input_resolution_error = exc.detail
        stderr = str(exc)
        returncode = 2
    except Exception as exc:
        stderr = str(exc)
        returncode = 1

    finished = datetime.now(timezone.utc)
    elapsed_ms = int((finished - started).total_seconds() * 1000)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text((stdout + ("\n" if stdout and stderr else "") + stderr), encoding="utf-8")
    except OSError:
        pass

    output_info = collect_real_node_outputs(sop, node_run_id, node_id, node_run_id)
    execution_ok = returncode == 0 and output_info["validation"].get("status") == "passed"
    status = "done" if execution_ok else "failed"
    summary = (
        "Real node execution finished and declared outputs were found."
        if execution_ok
        else "Required node inputs could not be resolved from the selected relay outputs." if input_resolution_error
        else "Real node execution failed." if returncode != 0
        else "Real node execution finished but declared outputs are missing."
    )
    receipt = {
        "version": 1,
        "executor": executor_kind,
        "requested_skill": agent_request.get("requested_skill") or node_run_skill_name(sop, node_id, plan),
        "executed_skill": agent_request.get("requested_skill") or node_run_skill_name(sop, node_id, plan),
        "node_id": node_id,
        "node_run_id": node_run_id,
        "request_id": node_run_id,
        "status": status,
        "returncode": returncode,
        "timed_out": timed_out,
        "validation": output_info["validation"],
        "input_manifest": output_info["input_manifest"],
        "output_manifest": output_info["output_manifest"],
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "legacy_fallback": executor_kind == "legacy-shell",
        "error": "" if status == "done" else (stderr[-1000:] or "declared outputs missing"),
    }
    try:
        write_node_run_agent_receipt(sop, node_run_id, node_id, receipt)
    except Exception:
        pass
    agent_artifacts = node_run_agent_artifacts(sop, node_run_id, node_id)
    output_info["artifacts"].extend([
        artifact for artifact in agent_artifacts
        if artifact.get("path") not in {item.get("path") for item in output_info["artifacts"] if isinstance(item, dict)}
    ])
    output_info["execution_evidence"] = node_run_execution_evidence(sop, node_run_id, node_id, output_info["artifacts"])
    return {
        "status": status,
        "summary": summary,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "elapsed_ms": elapsed_ms,
        "log_path": str(log_path),
        "actual_outputs": output_info["actual_outputs"],
        "artifacts": output_info["artifacts"],
        "business_artifacts": output_info["business_artifacts"],
        "core_outputs": output_info["core_outputs"],
        "relay_package": output_info["relay_package"],
        "execution_evidence": output_info["execution_evidence"],
        "output_categories": output_info["output_categories"],
        "input_directory": output_info["input_directory"],
        "input_manifest": output_info["input_manifest"],
        "output_directory": output_info["output_directory"],
        "output_manifest": output_info["output_manifest"],
        "validation": output_info["validation"],
        "capabilities": output_info["capabilities"],
        "agent_request": mask_data({k: v for k, v in agent_request.items() if k != "rendered_request"} | {
            "rendered_request": agent_request.get("rendered_request", ""),
            "executor": executor_kind,
            "receipt": receipt,
        }),
        "detail": {
            "executor": executor_kind,
            "command": command_for_detail or stage_command,
            "stage_command": stage_command,
            "returncode": returncode,
            "timeout_seconds": timeout,
            "timed_out": timed_out,
            "log_path": str(log_path),
            "context_path": str(wiki / "raw" / "pipeline-context.json"),
            "context": context,
            "stdout_tail": stdout[-8000:],
            "stderr_tail": stderr[-8000:],
            "input_resolution_error": input_resolution_error,
            "agent_request": mask_data({k: v for k, v in agent_request.items() if k != "rendered_request"} | {
                "rendered_request": agent_request.get("rendered_request", ""),
                "receipt": receipt,
            }),
            "node_state": output_info["node_state"],
            "capabilities": output_info["capabilities"],
            "actual_outputs": output_info["actual_outputs"],
            "core_outputs": output_info["core_outputs"],
            "relay_package": output_info["relay_package"],
            "execution_evidence": output_info["execution_evidence"],
            "output_categories": output_info["output_categories"],
            "validation": output_info["validation"],
        },
    }


def apply_real_node_execution_to_steps(steps, execution):
    status = execution.get("status")
    agent_request = execution.get("agent_request") if isinstance(execution.get("agent_request"), dict) else {}
    detail = execution.get("detail") if isinstance(execution.get("detail"), dict) else {}
    input_resolution_error = detail.get("input_resolution_error") if isinstance(detail.get("input_resolution_error"), dict) else {}
    if input_resolution_error:
        update_node_run_step(
            steps,
            "resolve-inputs",
            "failed",
            "Selected relay outputs do not satisfy this node's input contract.",
            input_resolution_error,
        )
        update_node_run_step(
            steps,
            "generate-agent-request",
            "skipped",
            "Agent Request was not generated because input resolution failed.",
            input_resolution_error,
        )
        update_node_run_step(
            steps,
            "execute-or-dry-run",
            "skipped",
            "Skipped because required node inputs could not be resolved.",
            input_resolution_error,
            started_at=execution.get("started_at"),
            finished_at=execution.get("finished_at"),
            elapsed_ms=execution.get("elapsed_ms"),
        )
        update_node_run_step(
            steps,
            "validate-outputs",
            "skipped",
            "Skipped because input resolution failed.",
            {"input_resolution_error": input_resolution_error},
        )
        update_node_run_step(
            steps,
            "persist-to-github",
            "skipped",
            "Skipped because input resolution failed.",
            input_resolution_error,
        )
        update_node_run_step(
            steps,
            "send-telegram-notification",
            "skipped",
            "Skipped because input resolution failed.",
            input_resolution_error,
        )
        return
    if agent_request:
        update_node_run_step(
            steps,
            "generate-agent-request",
            "done" if agent_request.get("request_path") else "warning",
            "Hermes Agent Request was rendered and saved for this Node Run.",
            agent_request,
        )
    execute_status = "done" if status == "done" else "failed" if status == "failed" else "blocked"
    update_node_run_step(
        steps,
        "execute-or-dry-run",
        execute_status,
        execution.get("summary") or "Hermes Agent Skill execution finished.",
        detail,
        started_at=execution.get("started_at"),
        finished_at=execution.get("finished_at"),
        elapsed_ms=execution.get("elapsed_ms"),
    )
    validation = execution.get("validation") or {}
    validation_status = validation.get("status")
    update_node_run_step(
        steps,
        "validate-outputs",
        "done" if validation_status == "passed" else "skipped" if input_resolution_error else "failed" if status == "failed" else "warning",
        "Declared outputs were found." if validation_status == "passed" else "Skipped because input resolution failed." if input_resolution_error else "Declared outputs are missing.",
        {**validation, **({"input_resolution_error": input_resolution_error} if input_resolution_error else {})},
    )
    capabilities = execution.get("capabilities") if isinstance(execution.get("capabilities"), dict) else {}
    git = capabilities.get("git") if isinstance(capabilities.get("git"), dict) else {}
    if git:
        git_failed = git.get("status") == "failed"
        git_required = bool(git.get("required", False))
        update_node_run_step(
            steps,
            "persist-to-github",
            "done" if git.get("status") == "done" else "failed" if git_failed and git_required else "warning" if git_failed else "skipped" if git.get("status") == "disabled" else "warning",
            git.get("reason") or git.get("error") or "GitHub persistence capability finished.",
            git,
        )
    telegram = capabilities.get("telegram") if isinstance(capabilities.get("telegram"), dict) else {}
    if telegram:
        telegram_failed = telegram.get("status") == "failed"
        telegram_required = bool(telegram.get("required", False))
        update_node_run_step(
            steps,
            "send-telegram-notification",
            "done" if telegram.get("status") == "done" else "failed" if telegram_failed and telegram_required else "warning" if telegram_failed else "skipped" if telegram.get("status") == "disabled" else "warning",
            telegram.get("error") or telegram.get("reason") or "Telegram notification capability finished.",
            telegram,
        )

def build_node_run_result_payload(sop, node_run_id, node_id, body, plan, steps, inner_steps, events,
                                  artifacts, started_at, finished_at, real_execution=None, pending=False):
    status = node_run_status_from_steps(steps)
    reason = ""
    if status in {"failed", "blocked", "needs_input", "warning"}:
        reason = next((step.get("summary") for step in steps if step.get("status") in {"failed", "blocked", "needs_input", "warning"}), "")
    environment_snapshot = node_run_environment_snapshot(plan)
    capability_results = node_run_capability_results(plan, real_execution)
    issues = node_run_issue_rows(plan, capability_results)
    result = {
        "node_run_id": node_run_id,
        "pipeline_id": node_run_id,
        "runtime_id": plan.get("runtime_id"),
        "instance_id": plan.get("instance_id"),
        "workflow_id": plan.get("workflow_id"),
        "node_id": node_id,
        "node_title": plan.get("node_title"),
        "status": status,
        "mode": plan.get("mode"),
        "input_source": plan.get("input_source"),
        "relay_mode": plan.get("relay_mode") or "",
        "selected_outputs": plan.get("selected_outputs") or [],
        "relay_mappings": plan.get("relay_mappings") or [],
        "source_node_run_id": plan.get("source_node_run_id") or "",
        "relay_selection": plan.get("relay_selection") or {},
        "edge_contract": plan.get("edge_contract") or (plan.get("relay_selection") or {}).get("edge_contract") or {},
        "node_execution_guide": plan.get("node_execution_guide") or {},
        "workflow_revision": plan.get("workflow_revision") or {},
        "relay_context": {},
        "relay_context_brief": plan.get("relay_context_brief") or "",
        "resolution_trace": [],
        "started_at": started_at,
        "finished_at": "" if pending else finished_at,
        "elapsed_ms": sum(int(step.get("elapsed_ms") or 0) for step in steps),
        "created_from": plan.get("base_run_id") or plan.get("input_source"),
        "retry_of": sanitize_node_run_id(body.get("retry_of") if isinstance(body, dict) else ""),
        "pending": bool(pending),
        "reason": reason,
        "steps": steps,
        "inner_steps": inner_steps,
        "events": events,
        "artifacts": artifacts,
        "actual_outputs": (real_execution or {}).get("actual_outputs") or {},
        "core_outputs": (real_execution or {}).get("core_outputs") or [],
        "relay_package": (real_execution or {}).get("relay_package") or {},
        "execution_evidence": (real_execution or {}).get("execution_evidence") or {},
        "output_categories": (real_execution or {}).get("output_categories") or {},
        "input_directory": (real_execution or {}).get("input_directory") or "",
        "input_manifest": (real_execution or {}).get("input_manifest") or "",
        "output_directory": (real_execution or {}).get("output_directory") or "",
        "output_manifest": (real_execution or {}).get("output_manifest") or "",
        "validation": (real_execution or {}).get("validation") or {},
        "capabilities": (real_execution or {}).get("capabilities") or {},
        "agent_request": (real_execution or {}).get("agent_request") or {},
        "business_artifacts": (real_execution or {}).get("business_artifacts") or [],
        "runtime_context": plan.get("runtime_context") or {},
        "instance_context": plan.get("instance_context") or {},
        "definition_defaults": plan.get("definition_defaults") or {},
        "capability_overrides": plan.get("capability_overrides") or {},
        "definition_scope_reports": plan.get("definition_scope_reports") or {},
        "environment_snapshot": environment_snapshot,
        "capability_results": capability_results,
        "issues": issues,
        "detail": mask_data({**plan, "inner_steps": inner_steps, "real_execution": real_execution or {}}),
    }
    matched_items = ((plan.get("relay_selection") or {}).get("matched_items") or []) if isinstance(plan.get("relay_selection"), dict) else []
    if matched_items and not result["resolution_trace"]:
        result["resolution_trace"] = relay_resolution_trace(plan, matched_items, {})
        result["relay_context"] = relay_context_payload(plan, matched_items, {"status": "matched"})
        result["relay_context_brief"] = result["relay_context"].get("brief") or result.get("relay_context_brief") or ""
    hydrate_node_run_input_artifacts(sop, result)
    input_resolution = result.get("input_resolution") if isinstance(result.get("input_resolution"), dict) else {}
    if input_resolution:
        result["edge_contract"] = input_resolution.get("edge_contract") or result.get("edge_contract") or {}
        result["node_execution_guide"] = input_resolution.get("node_execution_guide") or result.get("node_execution_guide") or {}
        result["workflow_revision"] = input_resolution.get("workflow_revision") or result.get("workflow_revision") or {}
        result["relay_context"] = input_resolution.get("relay_context") or result.get("relay_context") or {}
        result["relay_context_brief"] = input_resolution.get("relay_context_brief") or result.get("relay_context_brief") or ""
        result["resolution_trace"] = input_resolution.get("resolution_trace") or result.get("resolution_trace") or []
    real_context = (((real_execution or {}).get("detail") or {}).get("context") or {}).get("node_run") if isinstance(((real_execution or {}).get("detail") or {}).get("context"), dict) else {}
    if isinstance(real_context, dict):
        result["edge_contract"] = real_context.get("edge_contract") or result.get("edge_contract") or {}
        result["node_execution_guide"] = real_context.get("node_execution_guide") or result.get("node_execution_guide") or {}
        result["workflow_revision"] = real_context.get("workflow_revision") or result.get("workflow_revision") or {}
        result["relay_context"] = real_context.get("relay_context") or result.get("relay_context") or {}
        result["relay_context_brief"] = real_context.get("relay_context_brief") or result.get("relay_context_brief") or ""
        result["resolution_trace"] = real_context.get("resolution_trace") or result.get("resolution_trace") or []
    hydrate_node_run_result_views(sop, result)
    return result


def hydrate_node_run_input_artifacts(sop, result):
    if not isinstance(result, dict):
        return result
    node_run_id = sanitize_node_run_id(result.get("node_run_id") or result.get("pipeline_id") or "")
    node_id = str(result.get("node_id") or "")
    if not node_run_id or not node_id:
        return result
    info = node_run_input_info(sop, node_run_id, node_id)
    input_artifacts = info.get("input_artifacts") or []
    if input_artifacts:
        result["input_artifacts"] = input_artifacts
    if info.get("input_directory"):
        result["input_directory"] = info.get("input_directory")
    if info.get("input_manifest"):
        result["input_manifest"] = info.get("input_manifest")
    if info.get("input_manifest_data"):
        result["input_resolution"] = info.get("input_manifest_data")
    for step in result.get("steps") or []:
        if not isinstance(step, dict) or step.get("id") != "resolve-inputs":
            continue
        detail = step.get("detail") if isinstance(step.get("detail"), dict) else {}
        step["detail"] = {
            **detail,
            "input_directory": info.get("input_directory") or detail.get("input_directory") or "",
            "input_manifest": info.get("input_manifest") or detail.get("input_manifest") or "",
            "input_resolution": info.get("input_manifest_data") or detail.get("input_resolution") or {},
            "materialized_inputs": input_artifacts,
        }
    return result


def hydrate_node_run_agent_request(sop, result):
    if not isinstance(result, dict):
        return result
    node_run_id = sanitize_node_run_id(result.get("node_run_id") or result.get("pipeline_id") or "")
    node_id = str(result.get("node_id") or "")
    if not node_run_id or not node_id:
        return result
    wiki = Path(sop["wiki_local_path"]).expanduser().resolve()
    request_path = node_run_agent_path(sop, node_run_id, "request.md")
    executor_path = node_run_agent_path(sop, node_run_id, "executor.json")
    response_path = node_run_agent_path(sop, node_run_id, "response.txt")
    receipt_path = node_run_agent_path(sop, node_run_id, "receipt.json")
    agent = result.get("agent_request") if isinstance(result.get("agent_request"), dict) else {}
    if request_path.is_file() and not agent.get("rendered_request"):
        try:
            agent["rendered_request"] = request_path.read_text(encoding="utf-8")
        except OSError:
            pass
    executor = read_json(executor_path) or {}
    receipt = read_json(receipt_path) or {}
    if executor:
        agent.update({k: v for k, v in executor.items() if k not in {"rendered_request"}})
    if receipt:
        agent["receipt"] = receipt
    if request_path.exists():
        agent["request_path"] = safe_relative_file(wiki, request_path)
    if executor_path.exists():
        agent["executor_path"] = safe_relative_file(wiki, executor_path)
    if response_path.exists():
        agent["response_path"] = safe_relative_file(wiki, response_path)
    if receipt_path.exists():
        agent["receipt_path"] = safe_relative_file(wiki, receipt_path)
    if agent:
        result["agent_request"] = mask_data(agent)
        for step in result.get("steps") or []:
            if isinstance(step, dict) and step.get("id") == "generate-agent-request":
                detail = step.get("detail") if isinstance(step.get("detail"), dict) else {}
                step["detail"] = {**detail, **result["agent_request"]}
                if request_path.exists() and step.get("status") in {"waiting", "running"}:
                    step["status"] = "done"
                    step["summary"] = "Hermes Agent Request was rendered and saved for this Node Run."
    return result


def persist_node_run_result(sop, node_run_id, body, result, events):
    workspace = node_run_workspace(sop, node_run_id)
    write_json(workspace / "input.json", body if isinstance(body, dict) else {})
    write_json(workspace / "result.json", result)
    write_jsonl(workspace / "events.jsonl", events)
    if isinstance(result, dict) and result.get("mode") == "real-node" and not result.get("pending"):
        persist_node_run_audit_evidence_to_git(sop, node_run_id)


def complete_real_node_run_async(sop, workflow_id, node_id, node_run_id, body, started_at):
    try:
        plan = build_node_run_plan(sop, workflow_id, node_id, body)
        if plan is None:
            return
        steps = build_node_run_steps(sop, plan)
        real_execution = execute_real_node_run(sop, node_run_id, node_id, plan)
        apply_real_node_execution_to_steps(steps, real_execution)
        finished_at = datetime.now(timezone.utc).isoformat()
        steps = annotate_node_run_steps(steps, started_at)
        events = node_run_events_from_steps(node_run_id, node_id, steps, finished_at)
        inner_steps = node_run_lifecycle_steps(
            plan,
            steps,
            real_execution=real_execution,
            started_at=started_at,
            finished_at=finished_at,
        )
        artifacts = [{
            "id": "node-run-result",
            "producer": node_id,
            "type": "node-run.result",
            "format": "json",
            "path": f"raw/node-runs/{node_run_id}/result.json",
            "title": "Node Run diagnostic result",
            "resolution": "recorded",
        }, *(real_execution.get("artifacts") or [])]
        result = build_node_run_result_payload(
            sop,
            node_run_id,
            node_id,
            body,
            plan,
            steps,
            inner_steps,
            events,
            artifacts,
            started_at,
            finished_at,
            real_execution=real_execution,
            pending=False,
        )
        persist_node_run_result(sop, node_run_id, body, result, events)
    except Exception as exc:
        finished_at = datetime.now(timezone.utc).isoformat()
        plan = build_node_run_plan(sop, workflow_id, node_id, body) or {
            "runtime_id": "",
            "instance_id": sop.get("instance_id") or sop.get("id", ""),
            "workflow_id": workflow_id,
            "node_title": node_id,
            "mode": (body or {}).get("mode") if isinstance(body, dict) else "real-node",
            "input_source": (body or {}).get("input_source") if isinstance(body, dict) else "",
        }
        steps = annotate_node_run_steps(build_node_run_steps(sop, plan), started_at)
        update_node_run_step(
            steps,
            "execute-or-dry-run",
            "failed",
            "Real node execution crashed before completion.",
            {"error": str(exc)},
            finished_at=finished_at,
        )
        events = node_run_events_from_steps(node_run_id, node_id, steps, finished_at)
        result = build_node_run_result_payload(
            sop,
            node_run_id,
            node_id,
            body,
            plan,
            steps,
            [],
            events,
            [],
            started_at,
            finished_at,
            real_execution={"status": "failed", "summary": str(exc), "detail": {"error": str(exc)}},
            pending=False,
        )
        persist_node_run_result(sop, node_run_id, body, result, events)


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def read_jsonl(path):
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rows.append(json.loads(line))
    except Exception:
        pass
    return rows


def create_node_run(sop, workflow_id, node_id, body):
    config, _config_source = node_config_for(sop, node_id)
    if not workflow_id_matches(sop, workflow_id) or not isinstance(config, dict):
        return 404, {"status": "error", "message": f"Node {node_id!r} or workflow {workflow_id!r} not found"}
    token = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    digest = hashlib.sha1(json.dumps(body if isinstance(body, dict) else {}, sort_keys=True).encode("utf-8")).hexdigest()[:6]
    node_run_id = sanitize_node_run_id(body.get("node_run_id") if isinstance(body, dict) else "") or f"node-run-{node_id}-{token}-{digest}"
    body = body if isinstance(body, dict) else {}
    capability_overrides = normalize_node_run_capability_overrides(sop, node_id, body)
    definition_scope_reports = node_run_definition_scope_reports(sop, node_id, node_run_id, capability_overrides)
    sop_file = Path(str(sop.get("sop_file") or "")).expanduser()
    if definition_scope_reports.get("instance_override", {}).get("status") in {"saved", "unchanged"} and sop_file.exists():
        updated = read_yaml(sop_file)
        for key in ("nodes", "pipeline", "notify", "repo", "repo_branch", "name", "title", "version"):
            if key in updated:
                sop[key] = updated[key]
    body = {
        **body,
        "capability_overrides": capability_overrides,
        "_definition_scope_reports": definition_scope_reports,
    }
    plan = build_node_run_plan(sop, workflow_id, node_id, body)
    if plan is None:
        return 404, {"status": "error", "message": f"Node {node_id!r} or workflow {workflow_id!r} not found"}
    now = datetime.now(timezone.utc).isoformat()
    steps = build_node_run_steps(sop, plan)
    real_execution = None
    execute_step = node_run_step_by_id(steps, "execute-or-dry-run") or {}
    if plan.get("mode") == "real-node" and execute_step.get("status") == "waiting":
        if isinstance(body, dict) and body.get("sync") is True:
            real_execution = execute_real_node_run(sop, node_run_id, node_id, plan)
            apply_real_node_execution_to_steps(steps, real_execution)
        else:
            update_node_run_step(
                steps,
                "execute-or-dry-run",
                "running",
                "Real stage wrapper is running in the background.",
                {"mode": "real-node", "async": True},
            )
            update_node_run_step(
                steps,
                "validate-outputs",
                "waiting",
                "Waiting for the real node to produce declared outputs.",
            )
            finished_at = datetime.now(timezone.utc).isoformat()
            steps = annotate_node_run_steps(steps, now)
            events = node_run_events_from_steps(node_run_id, node_id, steps, finished_at)
            inner_steps = node_run_lifecycle_steps(
                plan,
                steps,
                started_at=now,
                finished_at=finished_at,
                pending=True,
            )
            artifacts = [{
                "id": "node-run-result",
                "producer": node_id,
                "type": "node-run.result",
                "format": "json",
                "path": f"raw/node-runs/{node_run_id}/result.json",
                "title": "Node Run diagnostic result",
                "resolution": "recorded",
            }]
            result = build_node_run_result_payload(
                sop,
                node_run_id,
                node_id,
                body,
                plan,
                steps,
                inner_steps,
                events,
                artifacts,
                now,
                finished_at,
                pending=True,
            )
            persist_node_run_result(sop, node_run_id, body, result, events)
            threading.Thread(
                target=complete_real_node_run_async,
                args=(sop, workflow_id, node_id, node_run_id, body, now),
                daemon=True,
            ).start()
            return 200, result
    finished_at = datetime.now(timezone.utc).isoformat()
    steps = annotate_node_run_steps(steps, now)
    events = node_run_events_from_steps(node_run_id, node_id, steps, finished_at)
    inner_steps = node_run_lifecycle_steps(
        plan,
        steps,
        real_execution=real_execution,
        started_at=now,
        finished_at=finished_at,
    )
    artifacts = [{
        "id": "node-run-result",
        "producer": node_id,
        "type": "node-run.result",
        "format": "json",
        "path": f"raw/node-runs/{node_run_id}/result.json",
        "title": "Node Run diagnostic result",
        "resolution": "recorded",
    }]
    if isinstance(real_execution, dict):
        artifacts.extend(real_execution.get("artifacts") or [])
    result = build_node_run_result_payload(
        sop,
        node_run_id,
        node_id,
        body,
        plan,
        steps,
        inner_steps,
        events,
        artifacts,
        now,
        finished_at,
        real_execution=real_execution,
        pending=False,
    )
    persist_node_run_result(sop, node_run_id, body, result, events)
    return 200, result


def read_node_run_result(sop, node_id, node_run_id):
    safe = sanitize_node_run_id(node_run_id)
    if not safe.startswith("node-run-"):
        return None
    result = read_json(node_run_workspace(sop, safe) / "result.json")
    if not isinstance(result, dict) or result.get("node_id") != node_id:
        return None
    hydrate_node_run_input_artifacts(sop, result)
    hydrate_node_run_agent_request(sop, result)
    hydrate_node_run_capability_history(sop, result)
    hydrate_node_run_result_views(sop, result)
    result["detail"] = mask_data(result.get("detail") or {})
    return result


def hydrate_node_run_capability_history(sop, result):
    if not isinstance(result, dict):
        return result
    node_run_id = sanitize_node_run_id(result.get("node_run_id") or result.get("pipeline_id") or "")
    node_id = str(result.get("node_id") or "")
    if not node_run_id or not node_id:
        return result
    capabilities = result.get("capabilities") if isinstance(result.get("capabilities"), dict) else {}
    telegram = capabilities.get("telegram") if isinstance(capabilities.get("telegram"), dict) else {}
    if not telegram.get("history"):
        events = read_stage_event_rows(sop, node_run_id)
        history = []
        for event in events:
            event_name = str(event.get("event") or "")
            if event_name not in {"tg_notify_sent", "tg_notify_failed", "telegram.sent", "telegram.failed"}:
                continue
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            stage = str(event.get("stage") or event.get("node_id") or data.get("stage") or "")
            if stage and stage != node_id:
                continue
            trigger = str(event.get("trigger") or data.get("trigger") or "")
            ok = event.get("ok") if "ok" in event else data.get("ok")
            status = "done" if bool(ok) else "failed"
            item = {
                "capability": "telegram",
                "status": status,
                "trigger": trigger,
                "sent_at": event.get("ts") or event.get("recorded_at") or "",
                "api_ok": bool(ok),
            }
            if trigger and trigger == telegram.get("trigger"):
                for key in ("message_preview", "error"):
                    if telegram.get(key):
                        item[key] = telegram.get(key)
            history.append(item)
        if history:
            telegram = {**telegram, "history": history}
            capabilities = {**capabilities, "telegram": telegram}
            result["capabilities"] = capabilities
    if capabilities:
        merged_results = []
        seen = set()
        for item in result.get("capability_results") or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or item.get("capability") or "")
            cap = capabilities.get(key) if isinstance(capabilities.get(key), dict) else {}
            merged_results.append({**item, "detail": {**(item.get("detail") or {}), **cap}})
            seen.add(key)
        for key, cap in capabilities.items():
            if key in seen or not isinstance(cap, dict):
                continue
            merged_results.append({
                "key": key,
                "capability": key,
                "label": cap.get("label") or key,
                "status": cap.get("status") or "unknown",
                "enabled": bool(cap.get("enabled", True)),
                "required": bool(cap.get("required", False)),
                "source": "runtime-result",
                "reason": cap.get("error") or cap.get("reason") or "",
                "managed_by": cap.get("managed_by") or "",
                "detail": cap,
            })
        if merged_results:
            result["capability_results"] = merged_results
    return result


def read_stage_event_rows(sop, run_id):
    wiki = Path(str((sop or {}).get("wiki_local_path") or "")).expanduser()
    primary = read_jsonl(wiki / "logs" / "stage-events" / f"{run_id}.jsonl")
    if primary:
        return primary
    return read_jsonl(wiki / "raw" / "pipeline-runs" / run_id / "events.jsonl")


def list_node_runs(sop, node_id, limit=20):
    root = Path(sop["wiki_local_path"]) / "raw" / "node-runs"
    rows = []
    if not root.exists():
        return rows
    for run_dir in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True):
        result = read_json(run_dir / "result.json")
        if not isinstance(result, dict) or result.get("node_id") != node_id:
            continue
        rows.append({
            "node_run_id": result.get("node_run_id") or run_dir.name,
            "pipeline_id": result.get("pipeline_id") or result.get("node_run_id") or run_dir.name,
            "runtime_id": result.get("runtime_id"),
            "instance_id": result.get("instance_id"),
            "workflow_id": result.get("workflow_id"),
            "node_id": node_id,
            "node_title": result.get("node_title"),
            "status": result.get("status"),
            "mode": result.get("mode"),
            "input_source": result.get("input_source"),
            "started_at": result.get("started_at"),
            "finished_at": result.get("finished_at"),
            "elapsed_ms": result.get("elapsed_ms"),
            "created_from": result.get("created_from"),
            "retry_of": result.get("retry_of"),
            "reason": result.get("reason"),
        })
        if len(rows) >= limit:
            break
    return rows


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
    request_body = inject_node_test_instance_config(
        {**base, **overrides, "management_action": action, "action": action},
        node_id,
    )
    request_body = inject_runtime_management_config(request_body)

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
    runtime_sop_path = str(body.get("runtime_sop_path") or body.get("runtimeSopPath") or "").strip()
    if runtime_sop_path:
        _overlay, resolved_runtime_sop_path, snapshot_error = load_workflow_runtime_sop_snapshot(sop, {
            "simulation_target": "runtime-sop",
            "runtime_sop_path": runtime_sop_path,
        })
        if snapshot_error:
            return 422, snapshot_error
        env["YOUTUBE_WIKI_RUNTIME_SOP_FILE"] = resolved_runtime_sop_path
    command = ["youtube-wiki", "trigger", "--repo", repo, "--wiki-path", sop["wiki_local_path"], "--url", url]
    intent = str(body.get("intent") or "").strip()
    if intent:
        command.extend(["--intent", intent])
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
    static = node_static_config(sop, node_id) or {}
    if static.get("retryable") is False:
        return 409, {"status": "error", "message": "该节点定义为不可重试", "node_id": node_id}

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

    env = {**os.environ}
    log_path = Path("/tmp") / f"retry-{node_id}-{run_id}.log"
    launched = False

    script = real_node_stage_script(node_id, sop)
    if script and script.exists():
        try:
            with open(log_path, "ab") as log:
                subprocess.Popen(
                    node_stage_command(script, wiki, run_id, pipeline_id),
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
            if path == ["api", "sop", "v1", "workflows"]:
                cache_key = f"workflows:v1:{parsed.query}"
                return json_response(self, 200, cached_read(cache_key, lambda: list_runtime_workflow_definitions(query)))
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
            if path == ["api", "sop", "settings", "registry"]:
                return json_response(self, 200, setting_registry_preview(query=query))
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
                if len(path) == 5 and path[3] == "settings" and path[4] == "registry":
                    return json_response(self, 200, setting_registry_preview(sop, query=query))
                if len(path) == 5 and path[3] == "config" and path[4] in {"capabilities", "resolved"}:
                    node_id = str((query.get("node_id") or [""])[0] or "")
                    workflow_id = str((query.get("workflow_id") or [""])[0] or "")
                    return json_response(self, 200, capability_config_resolution(sop, node_id, workflow_id=workflow_id, query=query))
                if len(path) == 7 and path[3] == "nodes" and path[5] == "settings" and path[6] == "registry":
                    if node_registry_item(sop, path[4]) is None:
                        return json_response(self, 404, {"detail": f"Node {path[4]!r} not found"})
                    return json_response(self, 200, setting_registry_preview(sop, node_id=path[4], query=query))
                if len(path) == 7 and path[3] == "nodes" and path[5] == "config" and path[6] in {"capabilities", "resolved"}:
                    if node_registry_item(sop, path[4]) is None:
                        return json_response(self, 404, {"detail": f"Node {path[4]!r} not found"})
                    workflow_id = str((query.get("workflow_id") or [""])[0] or "")
                    return json_response(self, 200, capability_config_resolution(sop, path[4], workflow_id=workflow_id, query=query))
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
                # GET /api/sop/{instance}/workflows/{workflow_id}/nodes/{node_id}/runs — Node Run history
                if len(path) == 8 and path[3] == "workflows" and path[5] == "nodes" and path[7] == "runs":
                    if not workflow_id_matches(sop, path[4]):
                        return json_response(self, 404, {"detail": f"Workflow {path[4]!r} not found"})
                    if node_registry_item(sop, path[6]) is None:
                        return json_response(self, 404, {"detail": f"Node {path[6]!r} not found"})
                    try:
                        limit = int((query.get("limit") or ["20"])[0])
                    except Exception:
                        limit = 20
                    return json_response(self, 200, {
                        "sop_id": sop.get("id", ""),
                        "instance_id": sop.get("instance_id") or sop.get("id", ""),
                        "workflow_id": path[4],
                        "node_id": path[6],
                        "runs": list_node_runs(sop, path[6], limit=max(1, min(limit, 100))),
                    })
                # GET /api/sop/{instance}/workflows/{workflow_id}/nodes/{node_id}/runs/{node_run_id}
                if len(path) in {9, 10} and path[3] == "workflows" and path[5] == "nodes" and path[7] == "runs":
                    if not workflow_id_matches(sop, path[4]):
                        return json_response(self, 404, {"detail": f"Workflow {path[4]!r} not found"})
                    result = read_node_run_result(sop, path[6], path[8])
                    if result is None:
                        return json_response(self, 404, {"detail": f"Node run {path[8]!r} not found"})
                    if len(path) == 10 and path[9] == "events":
                        events = read_jsonl(node_run_workspace(sop, path[8]) / "events.jsonl")
                        return json_response(self, 200, {
                            "node_run_id": path[8],
                            "node_id": path[6],
                            "events": events or result.get("events") or [],
                        })
                    if len(path) == 9:
                        return json_response(self, 200, result)
                # GET /api/sop/{instance}/nodes/{node_id}/runs — Node Run history alias
                if len(path) == 6 and path[3] == "nodes" and path[5] == "runs":
                    if node_registry_item(sop, path[4]) is None:
                        return json_response(self, 404, {"detail": f"Node {path[4]!r} not found"})
                    return json_response(self, 200, {
                        "sop_id": sop.get("id", ""),
                        "instance_id": sop.get("instance_id") or sop.get("id", ""),
                        "workflow_id": workflow_binding(sop).get("workflow_id", ""),
                        "node_id": path[4],
                        "runs": list_node_runs(sop, path[4]),
                    })
                # GET /api/sop/{instance}/nodes/{node_id}/runs/{node_run_id}
                if len(path) in {7, 8} and path[3] == "nodes" and path[5] == "runs":
                    result = read_node_run_result(sop, path[4], path[6])
                    if result is None:
                        return json_response(self, 404, {"detail": f"Node run {path[6]!r} not found"})
                    if len(path) == 8 and path[7] == "events":
                        events = read_jsonl(node_run_workspace(sop, path[6]) / "events.jsonl")
                        return json_response(self, 200, {
                            "node_run_id": path[6],
                            "node_id": path[4],
                            "events": events or result.get("events") or [],
                        })
                    if len(path) == 7:
                        return json_response(self, 200, result)
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
                                draft = read_node_draft(sop, draft_dir.name)
                                if draft:
                                    drafts.append(draft)
                    return json_response(self, 200, {"sop_id": sop.get("id", ""), "drafts": drafts})
                # GET /api/sop/{instance}/node-builder/evaluations/{evaluation_id}
                if len(path) == 6 and path[3] == "node-builder" and path[4] == "evaluations":
                    job = read_node_builder_evaluation(sop, path[5])
                    return json_response(self, 200 if job else 404, job or {"detail": "Node Builder evaluation not found"})
                # GET /api/sop/{instance}/node-drafts/{draft_id} — draft detail
                if len(path) == 5 and path[3] == "node-drafts":
                    draft = read_node_draft(sop, path[4])
                    return json_response(self, 200 if draft else 404, draft or {"detail": "Node draft not found"})
                # GET /api/sop/{instance}/workflow-drafts/schema — workflow edge draft schema
                if len(path) == 5 and path[3] == "workflow-drafts" and path[4] == "schema":
                    return json_response(self, 200, {
                        "sop_id": sop.get("id", ""),
                        "schema": workflow_edge_draft_schema(),
                    })
                # GET /api/sop/{instance}/workflow-drafts — list workflow edge drafts
                if len(path) == 4 and path[3] == "workflow-drafts":
                    return json_response(self, 200, {
                        "sop_id": sop.get("id", ""),
                        "drafts": list_workflow_edge_drafts(sop),
                    })
                # GET /api/sop/{instance}/workflows/{workflow_id}/edges/evaluations/{evaluation_id}
                if (len(path) == 8 and path[3] == "workflows"
                        and path[5] == "edges" and path[6] == "evaluations"):
                    job = read_edge_handoff_evaluation(sop, path[7])
                    return json_response(self, 200 if job else 404, job or {"detail": "Edge Handoff evaluation not found"})
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
                # GET /api/sop/{instance}/nodes/{node_id}/test-plan — generic preflight plan
                if len(path) == 6 and path[3] == "nodes" and path[5] == "test-plan":
                    plan = build_node_test_plan(sop, path[4], {})
                    if plan is None:
                        return json_response(self, 404, {"detail": f"Node {path[4]!r} not found"})
                    return json_response(self, 200, plan)
                # GET /api/sop/{instance}/nodes/{node_id}/tests — generic node test history
                if len(path) == 6 and path[3] == "nodes" and path[5] == "tests":
                    if node_registry_item(sop, path[4]) is None:
                        return json_response(self, 404, {"detail": f"Node {path[4]!r} not found"})
                    return json_response(self, 200, {
                        "sop_id": sop.get("id", ""),
                        "node_id": path[4],
                        "tests": list_generic_node_tests(sop, path[4]),
                    })
                # GET /api/sop/{instance}/nodes/{node_id}/tests/{test_id}
                if len(path) == 7 and path[3] == "nodes" and path[5] == "tests":
                    result = read_generic_node_test_result(sop, path[6])
                    if result is None or result.get("node_id") != path[4]:
                        return json_response(self, 404, {"detail": f"Node test {path[6]!r} not found"})
                    return json_response(self, 200, result)
                # GET /api/sop/{instance}/nodes/{node_id}/test-result/{pipeline_id}
                if len(path) == 7 and path[3] == "nodes" and path[5] == "test-result":
                    result = read_node_test_result(sop, path[4], path[6])
                    if result is None:
                        return json_response(self, 400, {"detail": "invalid nodetest pipeline_id"})
                    return json_response(self, 200, result)
                # GET /api/sop/{instance}/node-tests/{test_id}
                if len(path) == 5 and path[3] == "node-tests":
                    result = read_generic_node_test_result(sop, path[4])
                    if result is None:
                        return json_response(self, 404, {"detail": f"Node test {path[4]!r} not found"})
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

        # POST /api/sop/{instance}/config/capabilities|values  → save scoped resolved config
        if len(path) == 5 and path[:2] == ["api", "sop"] and path[3] == "config" and path[4] in {"capabilities", "values"}:
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            values = data.get("values") if isinstance(data.get("values"), dict) else {}
            scope = str(data.get("scope") or "instance")
            node_id = str(data.get("node_id") or "")
            try:
                result = save_capability_config(sop, values, scope=scope, node_id=node_id)
            except ValueError as exc:
                return json_response(self, 400, {"detail": str(exc)})
            return json_response(self, 200, result)

        # POST /api/sop/{instance}/nodes/{node_id}/config/capabilities|values  → save scoped config for a node context
        if (len(path) == 7 and path[:2] == ["api", "sop"]
                and path[3] == "nodes" and path[5] == "config" and path[6] in {"capabilities", "values"}):
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            if node_registry_item(sop, path[4]) is None:
                return json_response(self, 404, {"detail": f"Node {path[4]!r} not found"})
            values = data.get("values") if isinstance(data.get("values"), dict) else {}
            scope = str(data.get("scope") or "instance")
            try:
                result = save_capability_config(sop, values, scope=scope, node_id=path[4])
            except ValueError as exc:
                return json_response(self, 400, {"detail": str(exc)})
            return json_response(self, 200, result)

        # POST /api/sop/{instance}/node-builder/evaluate  → Runtime Node Builder Agent evaluation.
        if len(path) == 5 and path[:2] == ["api", "sop"] and path[3] == "node-builder" and path[4] == "evaluate":
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            if bool(data.get("async") or data.get("async_job")):
                return json_response(self, 202, start_node_builder_evaluation_job(sop, data))
            http_code, result = evaluate_node_builder(sop, data)
            return json_response(self, http_code, result)

        # POST /api/sop/{instance}/edge-handoff/evaluate  → Runtime Edge Handoff Agent evaluation.
        if len(path) == 5 and path[:2] == ["api", "sop"] and path[3] == "edge-handoff" and path[4] == "evaluate":
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            workflow_id = str(data.get("workflow_id") or workflow_binding(sop).get("workflow_id") or sop.get("id") or "")
            if bool(data.get("async") or data.get("async_job")):
                return json_response(self, 202, start_edge_handoff_evaluation_job(sop, workflow_id, data))
            http_code, result = evaluate_edge_handoff(sop, workflow_id, data)
            return json_response(self, http_code, result)

        # POST /api/sop/{instance}/workflows/{workflow_id}/edges/evaluate
        if (len(path) == 7 and path[:2] == ["api", "sop"]
                and path[3] == "workflows" and path[5] == "edges" and path[6] == "evaluate"):
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            if bool(data.get("async") or data.get("async_job")):
                return json_response(self, 202, start_edge_handoff_evaluation_job(sop, path[4], data))
            http_code, result = evaluate_edge_handoff(sop, path[4], data)
            return json_response(self, http_code, result)

        # POST /api/sop/{instance}/workflows/{workflow_id}/edges/simulate
        if (len(path) == 7 and path[:2] == ["api", "sop"]
                and path[3] == "workflows" and path[5] == "edges" and path[6] == "simulate"):
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            http_code, result = simulate_workflow_edge_handoff(sop, path[4], data)
            return json_response(self, http_code, result)

        # POST /api/sop/{instance}/workflows/{workflow_id}/drafts  → save full Workflow Draft
        if (len(path) == 6 and path[:2] == ["api", "sop"]
                and path[3] == "workflows" and path[5] == "drafts"):
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            draft = create_workflow_draft(sop, path[4], data)
            status = 422 if (draft.get("validation") or {}).get("status") == "failed" else 201
            return json_response(self, status, draft)

        # POST /api/sop/{instance}/workflows/{workflow_id}/drafts/runtime-sop
        if (len(path) == 7 and path[:2] == ["api", "sop"]
                and path[3] == "workflows" and path[5] == "drafts" and path[6] == "runtime-sop"):
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            result = generate_workflow_draft_runtime_sop(sop, path[4], data)
            if result.get("status") == "failed":
                return json_response(self, 422, result)
            return json_response(self, 200, result)

        # POST /api/sop/{instance}/workflows/{workflow_id}/drafts/{draft_id}/runtime-sop
        if (len(path) == 8 and path[:2] == ["api", "sop"]
                and path[3] == "workflows" and path[5] == "drafts" and path[7] == "runtime-sop"):
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            result = generate_workflow_draft_runtime_sop(sop, path[4], {**data, "draft_id": path[6]})
            if result.get("status") == "failed":
                return json_response(self, 422, result)
            return json_response(self, 200, result)

        # POST /api/sop/{instance}/workflows/{workflow_id}/drafts/{draft_id}/runs
        if (len(path) == 8 and path[:2] == ["api", "sop"]
                and path[3] == "workflows" and path[5] == "drafts" and path[7] == "runs"):
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            http_code, result = trigger_workflow_draft_run(sop, path[4], {**data, "draft_id": path[6]})
            return json_response(self, http_code, result)

        # POST /api/sop/{instance}/workflows/{workflow_id}/drafts/{draft_id}/publish-runtime
        if (len(path) == 8 and path[:2] == ["api", "sop"]
                and path[3] == "workflows" and path[5] == "drafts" and path[7] == "publish-runtime"):
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            result = publish_workflow_draft_to_runtime(sop, path[4], {**data, "draft_id": path[6]})
            if result.get("status") == "failed":
                return json_response(self, 422, result)
            return json_response(self, 200, result)

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

        # POST /api/sop/{instance}/workflows/{workflow_id}/nodes/{node_id}/runs — node-level diagnostic run
        if (len(path) == 8 and path[:2] == ["api", "sop"]
                and path[3] == "workflows" and path[5] == "nodes" and path[7] == "runs"):
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            http_code, result = create_node_run(sop, path[4], path[6], data)
            return json_response(self, http_code, result)

        # POST /api/sop/{instance}/nodes/{node_id}/runs — node-level diagnostic run alias
        if (len(path) == 6 and path[:2] == ["api", "sop"]
                and path[3] == "nodes" and path[5] == "runs"):
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            workflow_id = str(data.get("workflow_id") or workflow_binding(sop).get("workflow_id") or "")
            http_code, result = create_node_run(sop, workflow_id, path[4], data)
            return json_response(self, http_code, result)

        # POST /api/sop/{instance}/nodes/{node_id}/tests — generic node preflight
        if (len(path) == 6 and path[:2] == ["api", "sop"]
                and path[3] == "nodes" and path[5] == "tests"):
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            http_code, result = create_node_preflight_test(sop, path[4], data)
            return json_response(self, http_code, result)

        # POST /api/sop/{instance}/node-drafts
        if len(path) == 4 and path[:2] == ["api", "sop"] and path[3] == "node-drafts":
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            draft = create_node_draft(sop, data)
            status = 422 if (draft.get("validation") or {}).get("status") == "failed" else 201
            return json_response(self, status, draft)

        # POST /api/sop/{instance}/node-drafts/{draft_id}/test-draft
        if len(path) == 6 and path[:2] == ["api", "sop"] and path[3] == "node-drafts" and path[5] == "test-draft":
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            result = test_node_draft(sop, path[4], data)
            status = 200 if result.get("status") == "passed" else 422
            return json_response(self, status, result)

        # POST /api/sop/{instance}/node-drafts/{draft_id}/publish-runtime
        if len(path) == 6 and path[:2] == ["api", "sop"] and path[3] == "node-drafts" and path[5] == "publish-runtime":
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            result = publish_node_draft_to_runtime(sop, path[4], data)
            status = 200 if result.get("status") in {"published", "warning"} else 422
            return json_response(self, status, result)

        # POST /api/sop/{instance}/node-drafts/{draft_id}/persistence-plan
        if len(path) == 6 and path[:2] == ["api", "sop"] and path[3] == "node-drafts" and path[5] == "persistence-plan":
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            result = node_draft_persistence_plan(sop, path[4], data)
            status = 200 if result.get("status") == "generated" else 422
            return json_response(self, status, result)

        # POST /api/sop/{instance}/workflows/{workflow_id}/edges/drafts
        if (len(path) == 7 and path[:2] == ["api", "sop"]
                and path[3] == "workflows" and path[5] == "edges" and path[6] == "drafts"):
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            draft = create_workflow_edge_draft(sop, path[4], data)
            status = 422 if (draft.get("validation") or {}).get("status") == "failed" else 201
            return json_response(self, status, draft)

        # POST /api/sop/{instance}/workflows/{workflow_id}/edges/apply
        if (len(path) == 7 and path[:2] == ["api", "sop"]
                and path[3] == "workflows" and path[5] == "edges" and path[6] == "apply"):
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            result = apply_workflow_edge_draft(sop, path[4], data)
            status = 422 if result.get("status") == "failed" else 200
            if result.get("status") == "failed":
                return json_response(self, 422, result)
            return json_response(self, 200, result)

        # POST /api/sop/{instance}/workflows/{workflow_id}/edges/runtime-sop
        if (len(path) == 7 and path[:2] == ["api", "sop"]
                and path[3] == "workflows" and path[5] == "edges" and path[6] == "runtime-sop"):
            sop = find_sop(path[2])
            if not sop:
                return json_response(self, 404, {"detail": "SOP not found"})
            result = generate_workflow_edge_runtime_sop(sop, path[4], data)
            if result.get("status") == "failed":
                return json_response(self, 422, result)
            return json_response(self, 200, result)

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
