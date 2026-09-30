> **Skill Overview**
> 
> WorkBuddy Usage Status turns WorkBuddy's own local usage data into an offline dashboard — token spend, thinking time, thinking efficiency, model distribution, error count, and credit consumption. It ranks model cost-performance and shows per-model rates so you can pick the cheapest model, and supports a date-range filter so you can zoom into any period. All model-share charts are limited to the top 10 models with the rest grouped as "Other". All data stays on your machine under `~/.workbuddy/`; **default zero network, no external APIs**. The generated dashboard is one HTML file plus a local Chart.js copy written into the same folder, so it renders offline with zero dependencies.
> 
> **What it does**: Offline dashboard for WorkBuddy's local usage data — token / credit consumption, thinking efficiency, model distribution & cost-performance, date-range filtering, error monitoring, and usage-spike analysis.
> 
> **Recent updates**:
> 
> - **Per-call credit** — credit now comes from every model call logged in local session files, attributed to the day the call happened. No more "all credit on the session's first day".
> - **Model cost & rates** — one table, two groups of columns: cost reality shows calls, tokens, credit, and credit per million tokens; rate structure shows non-cached input, cached input, and output unit prices, plus off-peak discount. Both are back-calculated from local per-call detail.
> - **Costliest single prompts** — the 20 priciest prompts by credit, with concentration stats, so you can find the few calls that actually cost money.
> - **Cache hit-rate panel** — overall hit rate, a daily trend line, and a per-model ranking.
> - **Full-dataset CSV and xlsx on every run** — timestamped `usage-full-<timestamp>.csv` and `usage-full-<timestamp>.xlsx` are written next to the dashboard. The CSV carries all 14 sections in a single file for scripted reuse; the xlsx splits the same sections into one worksheet each, so it opens in Excel and online spreadsheet viewers. Headers and section names follow your OS language.
> - **Auto-archiving against WorkBuddy's 30-day trace cleanup** — every run merges live traces with a local archive, so history keeps accumulating; a dashboard banner reminds you to run at least once every 30 days. An optional `--seed <old snapshot>` imports an older snapshot's daily totals.
> - **Drill-down** — top-10 sessions expand into per-turn tables with an optional masked prompt column; usage-peak cards expand into that day's session table and model Top 5.
> 
> **How to install**
> 
> ```
> clawhub install workbuddy-usage-status
> ```
> 
> **How to use**
> 
> - **Chat trigger:** Describe what you want in plain English or Chinese — WorkBuddy detects this skill by *meaning*, not a fixed keyword list. Anything expressing *viewing, generating, or exporting your WorkBuddy usage status / stats / activity dashboard* will trigger it. Examples:
>   
>   "generate a WorkBuddy usage dashboard" · "view my recent WorkBuddy usage status" · "show token / credit consumption and model distribution" · "which model is the most cost-effective" · "filter usage by date range" · "which day had the highest usage"
>   
>   Scope is limited to WorkBuddy's own local usage data.
>   
>   **Not triggered:** This skill does **not** apply when you want usage/stats of *other products* such as Cursor, VS Code, Trae, or Claude; when you only say "export my data / make me a chart / build a dashboard" without specifying *WorkBuddy's local usage*; or when you want a generic visualization/report from arbitrary datasets.
> 
> - **CLI:**
>   
>   ```
>   python3 scripts/usage_extractor.py
>   ```
>   
>   Options: `--out ./report`, `--home /other/.workbuddy`, `--credit-xlsx <file>`, `--seed <old snapshot>`, `--no-archive`. Python 3.10+, standard library only. Windows users please replace `python3` with `python`.
> 
> ⭐ If this dashboard helped you see your WorkBuddy usage clearly, please give it a Star to support independent development: [github.com/clancy-feng/workbuddy-usage-status](https://github.com/clancy-feng/workbuddy-usage-status)
> 
> The full Chinese documentation is preserved below.

---

> **语言说明**：本文档英文概览在上，中文正文在下，二者是同一份说明的双语呈现；技能的触发描述另见 `SKILL.md` 的 `description`。

把 WorkBuddy 自己的本地使用数据，变成一份离线可查的 Dashboard。

可以看到：token 消耗、思考用时、思考效率、模型分布、错误数、积分消耗、模型费率、单次提问成本。

## ✨ 核心功能特性

- Token/Credit 全链路可视化：按模型、按日期、按会话统计，一眼定位"烧钱大户"
- 用量高峰探查：按 token 列出最高的几天，单行显示 token、credit、请求数、会话数、错误率，点「明细」展开当天会话表与模型 Top5。
- 模型成本与费率：同一张表并排两类信息。一类是实际发生的用量与花费，含调用数、token、积分、每百万 token 积分，按每次调用的真实模型名归属；另一类是从本地逐次明细反推出来的单价，含非缓存输入、缓存输入、输出三档单价与低谷折扣。只列出用量达到最高模型百分之一以上的模型；其中调用数不足 30 或费率有波动的模型不标注费率，并在行内写明原因。
- 单次提问成本榜：按积分列出最贵的 20 次提问，并给出集中度统计。
- 思考效率量化：输出 token ÷ 思考秒数，即 tok/s，横向对比模型效率。
- 错误集中监控：快速定位报错频繁的会话/模型，降低调试成本。
- 离线运行：Chart.js 随 Skill 安装附带。
- 缓存命中率面板：整体与按模型命中率趋势，看清"谁在帮你省钱"。
- 三级细节显示：从 Top10 会话点「查看」，轮次表含时间 / 模型 / 状态 / Token / 输入 / 缓存命中 / 调用数 / 工具数 / 思考时长 / 错误；提问列默认隐藏，勾选「显示提问」展开；每轮再点「明细」显示事件摘要。
- 全量数据双格式同步生成：每次运行自动产出 `usage-full-<时间戳>.csv` 与 `usage-full-<时间戳>.xlsx`，含逐笔调用明细与提问原文。CSV 把全部分区装在一个文件里，便于脚本批量取数；xlsx 把每个分区放进独立工作表，可在 Excel 与在线表格工具里直接翻查。表头与分区名语言随操作系统语言。
- 自动归档合并：每次运行自动把历史运行数据并入本地归档，可用`--no-archive` 关闭。
- 只读无侵入：以只读模式访问 WorkBuddy 数据，不影响正在运行的程序。
- 跨平台兼容：支持 Windows/macOS/Linux，Python 3.10+ 即可运行。

---

## 1. 能看到什么

- 从开始用 WorkBuddy 到现在一共花了多少 token / credit？思考了多久？
- 哪个会话、哪个模型消耗最大？模型效率怎么样？
- 哪天用量飙升？错误集中在哪些会话/模型？
- 用作"使用监督 / 用量控制"的量化依据。

---

## 2. 如何安装

> 💡 安装引导：国内用户优先选 SkillHub 一键安装，全球用户/OpenClaw 生态用户优先选 ClawHub 安装。

### 方式一：通过 WorkBuddy 安装（国内推荐）

在 WorkBuddy 技能市场中搜索 `workbuddy-usage-status / workbuddy 使用状态看板`，点击「安装」即可。

### 方式二：通过 ClawHub 安装

```
clawhub install workbuddy-usage-status
```

---

## 3. 用法

装好 skill 并重启 WorkBuddy 后，有两种用法。

### 入口 A：对话触发

在 WorkBuddy 对话里用自然语言描述你的需求即可，skill 会根据语义自动识别并引导生成看板。例如：

- "生成一个 WorkBuddy 使用信息看板"
- "查看一下最近 WorkBuddy 的使用状态"
- "我想看看 WorkBuddy 的工作信息看板，包括 token 消耗和模型分布"
- "看一下 WorkBuddy 的使用数据"

### 入口 B：命令行直接跑

在任意目录执行。需要 Python 3.10+，仅标准库：

```
# 生成到当前目录（默认）
python3 scripts/usage_extractor.py

# 生成到指定目录
python3 scripts/usage_extractor.py --out ./report

# 指定数据根，一般不用，默认 ~/.workbuddy
python3 scripts/usage_extractor.py --home /other/.workbuddy

# 可选：用用量导出 xlsx 作参考补充，只在本地缺少逐次明细的日期上补入
# xlsx 来自 workbuddy.cn 用量页 → 选日期范围 → 导出，最多 1 个月
python3 scripts/usage_extractor.py --credit-xlsx ~/Downloads/request-usage-2026-08-10.xlsx

# 可选：用旧快照恢复已被 30 天清理的日期的每日总量
# 旧快照可以是 usage-status.json 或历史 dashboard HTML
# 导入一次即持久生效，存入本地归档 ~/.workbuddy/usage-archive/，无需重复传入
python3 scripts/usage_extractor.py --seed ~/old/workbuddy-usage-status-dashboard-20260909-224125.html
```

Windows 用户请将上述命令中的 `python3` 替换为 `python`。

### 看结果

> ⚠ **重要提示：30 天缓存期**WorkBuddy 对本机 traces 只保留 **30 天**。本 skill 每次运行自动归档累积历史（`~/.workbuddy/usage-archive/`），看板始终是全量视图——**但归档只在运行时发生**：请至少每 30 天运行一次（建议配置每日定时自动化）；断档超 30 天期间的 trace 无法追溯，首次运行只能看到最近 30 天。

脚本在「输出目录」，即你运行命令时所在目录或 `--out` 指定的目录，生成 6 个文件：

| 文件                                            | 说明                                                              |
| --------------------------------------------- | --------------------------------------------------------------- |
| `workbuddy-usage-status-dashboard-<时间戳>.html` | 生成的报告文件，文件名带生成时间戳，每次生成独立文件，可保留多份对比                              |
| `usage-status.json`                           | 聚合后的原始数据，可二次处理                                                  |
| `usage-status.js`                             | `window.USAGE_STATUS = {...}`，备用                                |
| `chart.umd.min.js`                            | 图表引擎文件，由脚本自动复制到输出目录，需与 HTML 同目录存放                               |
| `usage-full-<时间戳>.csv`                        | 全量数据 CSV，14 个分区装在同一个文件里，含模型费率全部阶段、单次提问集中度；适合脚本批量取数              |
| `usage-full-<时间戳>.xlsx`                       | 全量数据 xlsx，与 CSV 同源，14 个分区对应 14 个工作表，首行冻结；适合在 Excel 或在线表格工具里直接翻查 |

> ⚠ **产物敏感性提醒**：`usage-status.json` / `usage-status.js` / dashboard HTML / 全量 CSV 与 xlsx 中均含**会话标题与用户提问原文摘要**，提问最长 300 字，看板内展示默认脱敏，数据文件内为原文截断。分享或提交到仓库前请先检查敏感性。

打开最新生成的 `workbuddy-usage-status-dashboard-*.html` 即可看到：KPI 卡 + 积分消耗图 + 思考用时图 + 各模型 Token 占比 + 模型效率 + 效率散点 + Top 10 Token消耗会话表 + 每日错误 + 模型成本与费率表 + 单次提问成本榜。四张时序图的横轴会按所选日期范围的跨度自动在「日 / 周 / 月」之间切换，≤120 天按日，120–730 天按周，>730 天按月。日期选择在修改起止日期后看板立即刷新。

### credit 的来源与可选补充

**现默认就已精确到每一次调用。** 每天、每个模型的 credit 来自本地会话文件里每一次模型调用的精确积分，按该次调用真正发生的时间归集到当日，不再用之前那种「把一个会话的 credit 整体挂到它首次出现的那天」的做法。

> ⚠ **1.5.0 之前生成的旧快照和旧归档，其 credit 不可再用。** 旧快照里的 credit 是按会话首现日挂出来的旧口径，与现在的逐日实测值不在同一个口径上。本 skill 在应用历史快照时只取其中的 token 等总量字段，不再采用其 credit。

#### 可选补充：用量导出 xlsx

1. 打开 `https://www.workbuddy.cn/profile/plans-usage`，即用量明细表。
2. 选日期范围，最多 1 个月，点导出，得到 xlsx。
3. 运行：`python usage_extractor.py --credit-xlsx 路径/xxx.xlsx`

**补充逻辑**

- 只补入**本地没有逐次明细**的日期。
- xlsx 最多含 1 个月；长期趋势以本地逐次明细为准。

#### 无法归入每日趋势的部分

- **本地无对话文件的历史会话**：账本里记有 credit，但本地既无逐次明细也无请求记录，无法归到任何一天。看板会以提示条报出这类会话的数量与合计。
- **有 credit 但无 token 明细的日期**：这些天的积分已按实际发生日计入趋势，数值正确；但它们的 token 明细已被 30 天清理机制删除，所以这些天只有积分、没有 token。看 token 曲线时，这几天的数值偏低甚至为 0 属于正常，不是数据出错。看板提示条会列出具体是哪几天。

## 4. 报告刷新

数据是静态快照，想更新就再跑一次脚本，重新打开 HTML：

```
python3 scripts/usage_extractor.py --out ./report
```

如想每天自动刷新，可用 WorkBuddy 的"自动化/定时任务"每天跑这条命令。

---

## 5. 指标来源及算法

每个指标的具体算法见 DATA-GUIDE.md。

| 指标        | 算法                                                                                                                     | 数据来源                                   |
| --------- | ---------------------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| 思考用时      | 每条 trace 里 `type=generation` 的 span 时长之和                                                                               | `traces/*/trace_*.json`                |
| 思考效率      | 输出 token ÷ 思考秒数，即 tok/s                                                                                                | `traces/*/trace_*.json`                |
| token 消耗  | `totalTokens`（输入+输出+缓存）按会话/模型/天聚合                                                                                      | `traces/*/trace_*.json`                |
| 缓存命中率     | `totalCachedTokens ÷ totalInputTokens`，若实测 cached>input 自动切换为 `cached/(in+cached)`；按天/模型聚合；模型排行仅收录调用数 ≥ 10             | `traces/*/trace_*.json`                |
| credit 消耗 | 逐次实测：每次模型调用的 `providerData.rawUsage.credit` 按调用时间归日，可再按模型拆分；本地无明细的会话回退账本并单独提示                                          | `projects/*/*.jsonl`，回退 `workbuddy.db` |
| 模型成本与费率   | 成本实况＝该模型积分 ÷ 该模型 token × 100 万；费率结构＝按模型与月份做无截距三元最小二乘，月内再识别低谷时段。表内只列 token ≥ 最大模型的 1/100 的模型；费率进主表需调用数 ≥ 30 且 R² ≥ 0.95 | `projects/*/*.jsonl`                   |
| 单次提问成本    | 按 `conversationRequestId` 汇总该次提问触发的全部模型调用积分，另给集中度统计                                                                    | `projects/*/*.jsonl`                   |
| Top 会话    | 按 token 消耗降序取前 10 个会话，列出标题/token/思考时长/credit/错误数                                                                       | `traces/*` + `workbuddy.db`            |

---

## 6. 已知限制

1. 首跑耗时：首次全量解析 traces 需 10–30 秒。

2. **全程零网络请求**：脚本不发起任何出站请求。

3. **WorkBuddy 现只保留最近 30 天的 traces**：超期的逐笔明细会被删除。本 skill 每次运行会自动归档到 `~/.workbuddy/usage-archive/`，之后被清理也不影响看板——但归档只在运行时发生：**请至少每 30 天运行一次**，建议配置每日定时自动化。断档超 30 天期间的逐笔明细无法追溯，首次运行只能看到最近 30 天。被清理日期的每日总量可用 `--seed <旧快照>` 恢复。

---

## 7. 故障排查

| 现象                | 原因 / 处理                                                             |
| ----------------- | ------------------------------------------------------------------- |
| 打开 HTML 显示"数据未加载" | 脚本报错中断。重跑 `usage_extractor.py` 看 stderr                             |
| 图表空白但数字在          | 若报错"缺少 chart.umd.min.js"，确认该文件与 usage_extractor.py 同在 scripts/ 下后重跑 |
| 数据明显偏少            | 这台机器 traces 少或刚装；或 `--home` 指错了目录                                   |

---

## ❓ 常见问题

**Q：看板里的数字和 WorkBuddy 自己显示的对不上？**

A：本看板只读取 `~/.workbuddy` 下的本地数据文件，与 WorkBuddy 自身统计口径可能不同——本工具只统计「有 token 消耗的请求」，排除零用量的工作流记账噪声。以本看板口径为准，详见 DATA-GUIDE.md。

**Q：为什么某天的积分特别高？为什么某天有积分却没有 token？**

A：积分按每一次模型调用真正发生的时间归日，所以某天高就是那天确实扣了这么多。若某天有积分数值却没有 token，是因为积分明细存在本地会话文件里，而请求级 token 明细放在 traces 目录、已被 WorkBuddy 的 30 天清理机制删除。这类日期会在看板提示条里明确列出，属于正常现象，不是数据出错。详见 `DATA-GUIDE.md` §4.1。

**Q：跑完脚本数字很少，怀疑报告不完整？**

A：脚本对损坏或无法解析的 trace 文件会跳过，并在结尾打印「⚠ 数据完整性提示」，看板顶部也会显示黄色提示条，列出被跳过的文件名。提示存在即说明这些 trace 已损坏、相关时段数据会缺失；可去 `~/.workbuddy/traces` 下核对对应文件。

**Q：为什么不能实时刷新、一直挂着看？**

A：看板是按需生成的静态 HTML，配套 chart.umd.min.js 需同目录存放，设计上零外网、不常驻进程。要定期更新，可用 WorkBuddy 的「自动化 / 定时任务」每天跑一次抽取命令，见第 4 节。

**Q：第一次跑很慢？**

A：全量解析 traces 可能涉及上千文件，只需一次，约 10–30 秒，之后每次都很快。见已知限制第 2 条。

**Q：为什么看板只覆盖最近 30 天？更早的数据去哪了？**

A：WorkBuddy 会自动清理 30 天前的本地 traces，已清理日期的对话本身不受影响，只是逐次调用的明细没了。本 skill 每次运行会先把数据归档到 `~/.workbuddy/usage-archive/`，之后被清理也不影响看板；但**归档只在运行时发生**——超过 30 天没运行，断档期间的明细无法追溯。补救：用清理前生成的历史快照执行 `--seed <快照>`，可恢复更早日期的每日总量；并建议配置每日定时自动化，避免再断档。

**Q：对话里怎么说才能触发这个 skill？**

A：用自然语言描述「查看 / 生成 WorkBuddy 使用状态」即可，无需记关键词。

---

## 8. 适用使用场景

- AI 工具成本管控：监控 WorkBuddy 的 token/credit 消耗，避免预算超支
- 模型性价比对比：通过模型费率与思考效率横向对比不同模型的实际表现
- 项目用量统计：统计单个项目或会话的 AI 资源消耗，核算项目成本
- Agent 工作效率评估：量化 WorkBuddy 的思考时长、错误率，优化 Agent 配置
- 本地数据可视化：数据不出本机，适合对数据外发有要求的场景

---

## 📝 更新日志

详细版本变更记录请查看 CHANGELOG.md。

当前最新版本：v1.5.0（2026-09-30）
---

## 👤 关于作者

本技能由 WorkBuddy 深度用户开发，专注 AI 工具用量可视化方向。

- 小红书：@AI监工老冯 - 分享 WorkBuddy 使用技巧与技能更新动态
- GitHub：clancy-feng
- SkillHub：workbuddy-usage-status
- ClawHub：workbuddy-usage-status

---

## 💖 支持这个项目

> 📊 已被 **4000+** WorkBuddy 用户下载使用，覆盖 SkillHub & ClawHub 双平台。

如果这个工具帮到了你，欢迎：

- ⭐ 去 GitHub 点个 Star
- 🐛 遇到问题提 Issue
- 📢 分享给你的 WorkBuddy 用户朋友

**GitHub**：<https://github.com/clancy-feng/workbuddy-usage-status>

---

🏆 SkillHub TRACE 评分 4.8/5.0 · ClawHub 搜索 "WorkBuddy" 排名第一
