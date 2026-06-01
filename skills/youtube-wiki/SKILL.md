---
name: youtube-wiki
description: "当用户说"youtube-wiki"、"触发 youtube wiki"、"研究这个 YouTube 视频"、"run youtube wiki pipeline" 时自动触发。Trigger and inspect the private youtube-wiki SOP pipeline from a YouTube URL through the service machine's local youtube-wiki CLI."
---

# Youtube Wiki

## 作用

Trigger and inspect the private youtube-wiki SOP pipeline from a YouTube URL through the service machine's local youtube-wiki CLI.

## 执行

```bash
~/.claude/skills/youtube-wiki/scripts/run.sh --mode=trigger --repo=skkeoriw/llm-wiki-210-smoke --url="https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

## 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `--mode` | 是 | 调用模式，可选值：`init`, `trigger`, `status`, `validate`, `list` |
| `--repo` | 否 | Initialize a wiki repository on the service machine. |
| `--repo` | 否 | Trigger the youtube-wiki SOP for a YouTube URL. |
| `--url` | 否 | Trigger the youtube-wiki SOP for a YouTube URL. |
| `--intent` | 否 | Trigger the youtube-wiki SOP for a YouTube URL. |
| `--watch` | 否 | Trigger the youtube-wiki SOP for a YouTube URL. |
| `--timeout` | 否 | Trigger the youtube-wiki SOP for a YouTube URL. |
| `--repo` | 否 | Check or watch pipeline status. |
| `--pipeline-id` | 否 | Check or watch pipeline status. |
| `--watch` | 否 | Check or watch pipeline status. |
| `--timeout` | 否 | Check or watch pipeline status. |
| `--url` | 否 | Run an end-to-end validation video through Stage A/B/C/D. |
| `--timeout` | 否 | Run an end-to-end validation video through Stage A/B/C/D. |

## 直接执行

```bash
bash <(curl -fsSL https://skill.vyibc.com/youtube-wiki.sh)
```

