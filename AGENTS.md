# Auto YouTube Wiki Skill Agent 工作规则

适用于 `auto-youtube-wiki-skill` 仓库内的 CLI、skill、验证脚本和发布脚本修改。

## 必须遵守

- 所有修改必须先在本机仓库完成，测试通过后再 `commit`、`push`。
- 如果一次任务还修改了其他仓库，例如 `agent-brain-plugins`、`sop-ui`、`cloudflare-youtube-pipeline`，每个有本轮改动的仓库都必须分别测试、commit、push，不能只提交其中一个。
- push 前必须运行 `git status --short`。已有的非本轮脏改动不能混入本次 commit；如果保留，最终回复必须明确列出这些未提交文件和原因。
- 最终回复必须按仓库列出：`repo@commit`、push 结果、测试/构建命令、部署/验证结果。若某个涉及仓库没有 push，必须说明阻塞原因。
- 禁止把 token、SSH 私钥、Cloudflare key、密码写进仓库。
- 不要触发真实 YouTube workflow，除非用户明确要求。默认只运行 CLI、脚本、inventory、fleet 或静态验证。

## 开发验证

按改动范围选择最小测试集。常用验证：

```bash
cd /root/auto-youtube-wiki-skill
bash -n scripts/*.sh
python3 -m pytest tests
```

涉及 tunnel/runtime inventory 时，优先使用仓库内验证脚本；涉及真实 Runtime 的操作必须先确认目标机器和 channel，避免误操作 210、222 或非当前目标机器。
