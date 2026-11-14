# CLAUDE.md

**先读 [DEVELOPMENT.md](DEVELOPMENT.md)**：架构、每个模块的实现、状态文件结构、运维排障、开发配方、已踩的坑都在里面，读完不需要再通读代码。变更日志与证据在 [EXPERIMENT.md](EXPERIMENT.md)。

## 必须记住的规则
- 用户是中文使用者：**默认用中文回复**。代码/注释/提交信息用英文，页面文案三语（zh/en/fr）。
- 只在分支 `claude/zealous-faraday-yzun8q` 开发并推送；**没被要求不要建 PR**。机器人数据在 `data` 分支（`git fetch origin data && git show origin/data:state.json`）。
- 用户邮箱是隐私，不要发到任何外部服务。不要把未证实的传闻当事实喂给 AI 或写进页面。
- AI 交易员（C/D/E/F）不限制持仓只数、单只仓位和选股，只保留基础风控；各交易员决策要相互独立。
- 改动后：`python bot/run.py --demo` 通过 → JS 语法检查通过（命令见 DEVELOPMENT.md §13）→ 在 EXPERIMENT.md 记一笔（发现/依据/改动）→ 提交并推送。
- 改任何 AI 提示词的行为含义要 bump `PROMPT_VER`；改规则/文案要三语同步（`ACCOUNTS`(Python) + `AC`(JS) + `TPL`）；新增账户的改动清单见 DEVELOPMENT.md §10.2。
- 不要改 `STATE_VERSION`（会重置线上实验）；日常升级写进 `migrate()`。
- 批量/带引号的修改写成 patch 脚本（`assert old in s` 再替换），别在 shell heredoc 里嵌套引号；别把 `pkill` 和 `git` 串在同一条命令里。
- 沙箱访问不了 Yahoo/Google/Pages：抓取代码只能在真实运行后读 `data` 分支验证；用 `send_later` 安排复查。
- 状态与排障先看：`health.ai`（`last_429`、`dead`）、`ai_usage`、各账户 `ai.done/attempts/deferred/paused`、`macro.failed_at`、`events.errors`。
