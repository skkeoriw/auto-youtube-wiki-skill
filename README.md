# youtube-wiki

Trigger and inspect the private youtube-wiki SOP pipeline from a YouTube URL through the service machine's local youtube-wiki CLI.

---

## 1. 直接执行 CLI

不需要安装 skill，一条命令直接调用：

```bash
bash <(curl -fsSL https://skill.vyibc.com/youtube-wiki.sh) --mode=trigger --repo=skkeoriw/llm-wiki-210-smoke --url="https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

---

## 2. 安装为 Claude Code Skill

```bash
bash <(curl -fsSL 'https://skill.vyibc.com/install-youtube-wiki.sh')
```

安装后 skill 会写入：

- `~/.claude/skills/youtube-wiki/SKILL.md`
- `~/.claude/skills/youtube-wiki/scripts/run.sh`

### 安装完成后如何使用

对 Claude 说以下任意一句，skill 会自动触发：

- `youtube-wiki`
- `触发 youtube wiki`
- `研究这个 YouTube 视频`
- `run youtube wiki pipeline`

---

## 3. 支持的调用模式

| 模式 | 说明 |
|------|------|
| `init` | Initialize a wiki repository on the service machine. |
| `trigger` | Trigger the youtube-wiki SOP for a YouTube URL. |
| `status` | Check or watch pipeline status. |
| `validate` | Run an end-to-end validation video through Stage A/B/C/D. |
| `list` | List wiki SOP repositories available on the service machine. |

---

## 本地脚本服务说明

本 skill 将本地 shell 脚本包装成 HTTP 服务，再通过 auto-domain 暴露到公网。

**在服务所在机器上，执行标准 setup 脚本：**

```bash
./scripts/setup-service.sh \
  --name=youtube-wiki \
  --repo=skkeoriw/llm-wiki-210-smoke
```

脚本会启动本地 HTTP bridge、注册 auto-domain 隧道，并把可复制的
Skill/CLI 命令以 JSON metadata 写入 tunnel-admin。

Runtime 通道注册默认使用受控的 managed auto-domain 源码：

```text
https://github.com/skkeoriw/auto-domain-cli.git#main
```

`setup-service.sh` 每次创建 tunnel 前都会同步该源码缓存。如果缓存目录不是
Git 仓库、指向了错误 repo，或存在本地脏改动，脚本会重建/清理后再注册
tunnel。除非显式设置 `AUTO_DOMAIN_ALLOW_LOCAL_RUNNER=1`，不要使用本机临时
auto-domain runner 创建 Runtime 通道。

之后在任何地方执行 skill，都会调用到这台机器上的本地脚本：

```bash
bash <(curl -fsSL https://skill.vyibc.com/youtube-wiki.sh) --mode=...
```

- `scripts/local-script.sh` — 本地重量级脚本，编辑此文件实现业务逻辑
- `scripts/bridge.py` — Python HTTP bridge，无需修改
- `scripts/start-local-service.sh` — bridge 启动脚本
- `scripts/setup-service.sh` — 标准服务部署脚本，负责 bridge + tunnel metadata

---

## 4. 调用示例

### 触发 YouTube 研究流程

```bash
bash <(curl -fsSL https://skill.vyibc.com/youtube-wiki.sh) --mode=trigger --repo=skkeoriw/llm-wiki-210-smoke --url='https://www.youtube.com/watch?v=dQw4w9WgXcQ'
```

### 触发并等待 A/B/C/D 完成

```bash
bash <(curl -fsSL https://skill.vyibc.com/youtube-wiki.sh) --mode=trigger --repo=skkeoriw/llm-wiki-210-smoke --url='https://www.youtube.com/watch?v=dQw4w9WgXcQ' --watch=true --timeout=900
```

### 查询进度

```bash
bash <(curl -fsSL https://skill.vyibc.com/youtube-wiki.sh) --mode=status --repo=skkeoriw/llm-wiki-210-smoke --pipeline-id='<pipeline_id>'
```

---

## 5. 发布

本地发布（需在仓库目录下）：

```bash
./scripts/publish-skill.sh
```

从 GitHub `main` 远程发布：

```bash
bash <(curl -fsSL https://skill.vyibc.com/publish-youtube-wiki.sh)
```

---

## 6. 仓库结构

```text
README.md
scripts/
  youtube-wiki.sh                    # CLI 直接执行入口
  start-local-service.sh             # 启动本地脚本服务
  setup-service.sh                   # 部署 bridge + auto-domain
  publish-youtube-wiki.sh             # 远程一键发布
  publish-skill.sh             # 本地发布
  upload-file.sh               # R2 上传工具
skills/
  youtube-wiki/
    SKILL.md                   # Claude Code skill 定义
    scripts/run.sh             # 唯一核心执行逻辑
```

`scripts/youtube-wiki.sh` 和安装后的 `skills/youtube-wiki/scripts/run.sh` 来自同一份脚本。
