> **Skill Overview**
> 
> WorkBuddy Usage Status turns WorkBuddy's own local usage data into an offline, auditable dashboard — token spend, thinking time, thinking efficiency, model distribution, error count, and credit consumption. It ranks model cost-performance (credit per 1k tokens) with switching suggestions so you can pick the cheapest model, and now supports a date-range filter so you can zoom into any period. All model-share charts are limited to the top 10 models with the rest grouped as "Other". All data stays on your machine under `~/.workbuddy/`; no network, no external APIs. The generated dashboard is a single self-contained HTML file (Chart.js inlined), so it renders anywhere with zero dependencies.
> 
> **What it does**: Offline dashboard for WorkBuddy's local usage data — token / credit consumption, thinking efficiency, model distribution & cost-performance, date-range filtering, error monitoring, and usage-spike analysis. Purely local, zero network dependency.
> 
> **How to install**
> 
> ```
> clawhub install workbuddy-usage-status
> ```
> 
> **How to invoke**
> 
> - **Chat trigger (natural language):** Describe what you want in plain English or Chinese — WorkBuddy detects this skill by *meaning*, not a fixed keyword list. Anything expressing *viewing, generating, or exporting your WorkBuddy usage status / stats / activity dashboard* will trigger it. Examples:
>   
>   "generate a WorkBuddy usage dashboard" · "view my recent WorkBuddy usage status" · "show token / credit consumption and model distribution" · "which model is the most cost-effective" · "filter usage by date range" · "which day had the highest usage"
>   
>   Scope is limited to WorkBuddy's own local usage data.
> 
> - **CLI:** `python3 scripts/usage_extractor.py` (options: `--out ./report`, `--home /other/.workbuddy`). Python 3.10+, standard library only. Windows users please replace `python3` with `python`.
> 
> The full Chinese documentation is preserved below.

---

把 WorkBuddy 自己的本地使用数据，变成一份离线可查的 Dashboard。

可以看到：token 消耗（主角指标，本地精确）、思考用时、思考效率、模型分布、错误数、credit 消耗（本地估算，可选导入用量导出精确化）。

## ✨ 核心功能特性

- Token/Credit 全链路可视化：按模型、按日期、按会话统计，一眼定位"烧钱大户"

- 用量高峰探查：自动按 credit 排序列出最高的几天，逐日拆解主导会话、模型 token 构成、错误率、最大单次请求——精确到"哪一天花了多少"，帮你快速定位重活集中日，不做"正常/异常"判定

- 思考效率量化：输出 token ÷ 思考秒数（tok/s），横向对比模型性价比

- 错误集中监控：快速定位报错频繁的会话/模型，降低调试成本

- 零依赖离线运行：Chart.js 已内联，单文件 HTML 双击即看，无需联网

- 只读无侵入：以只读模式访问 WorkBuddy 数据，不影响正在运行的程序

- 跨平台兼容：支持 Windows/macOS/Linux，Python 3.10+ 即可运行

---

## 1. 你能看到什么

- WorkBuddy 一共花了多少 token / credit？思考了多久？

- 哪个会话、哪个模型最费？效率最低的是谁？

- 哪天用量飙升？错误集中在哪些会话/模型？

- 用作"使用监督 / 用量控制"的量化依据。

---

## 2. 如何安装

> 💡 安装引导：国内用户优先选 SkillHub 一键安装，全球用户/OpenClaw 生态用户优先选 ClawHub 安装。

### 方式一：通过 WorkBuddy 对话安装

把 SkillHub 提供的 prompt 发给你的 WorkBuddy 即可：

请根据 https://skillhub.cn/install/skillhub.md，安装 workbuddy-usage-status。

### 方式二：通过 ClawHub 安装

```
clawhub install workbuddy-usage-status
```

### 方式三：手动安装到本地

```
git clone https://gitee.com/beclancy/workbuddy-usage-status.git ~/.workbuddy/skills/workbuddy-usage-status
```

---

## 3. 怎么用

装好 skill（见第 2 节）并重启 WorkBuddy 后，有两种用法。

### 入口 A：对话触发

在 WorkBuddy 对话里用自然语言描述你的需求即可，skill 会根据语义自动识别并引导生成看板。例如：

- "生成一个 WorkBuddy 使用信息看板"

- "查看一下最近 workbuddy 的使用状态"

- "我想看看 workbuddy 的工作信息看板，包括 token 消耗和模型分布"

### 入口 B：命令行直接跑

在任意目录执行（Python 3.10+，仅标准库）：

```
# 生成到当前目录（默认）
python3 scripts/usage_extractor.py

# 生成到指定目录
python3 scripts/usage_extractor.py --out ./report

# 指定数据根（一般不用，默认 ~/.workbuddy）
python3 scripts/usage_extractor.py --home /other/.workbuddy

# 可选：用用量导出 xlsx 精确覆盖对应日期窗口的 credit（低调可选参数，不进默认流程）
# xlsx 来自 workbuddy.cn 用量页 → 选日期范围 → 导出；最多含 1 个月
python3 scripts/usage_extractor.py --credit-xlsx ~/Downloads/request-usage-2026-08-10.xlsx
```

Windows 用户请将上述命令中的 `python3` 替换为 `python`。

### 看结果

脚本在「输出目录」（即你运行命令时所在目录，或 `--out` 指定的目录）生成 3 个文件：

| 文件 | 说明 |
| ---- | ---- |
| `workbuddy-usage-status-dashboard.html` | 自包含单文件，数据 + Chart.js 均已内联，双击即看，无需联网 |
| `usage-status.json` | 聚合后的原始数据，可二次处理 |
| `usage-status.js` | `window.USAGE_STATUS = {...}`，备用 |

打开 `workbuddy-usage-status-dashboard.html` 即可看到：KPI 卡 + 每日积分消耗（credit）图 + 每日思考用时图 + 各模型 Token 占比 + 模型效率 + 效率散点 + Top 10 会话表（按 token 消耗取前 10）+ 每日错误。

---

## 4. 报告刷新

数据是快照。要更新就再跑一次脚本，重新打开 HTML：

```
python3 scripts/usage_extractor.py --out ./report
```

（想每天自动刷新，可用 WorkBuddy 的"自动化/定时任务"每天跑这条命令。）

---

## 5. 指标来源及算法（详情见 data-guide.md）

| 指标 | 算法 | 数据来源 |
| ---- | ---- | ---- |
| 思考用时 | 每条 trace 里 `type=generation` 的 span 时长之和 | `traces/*/trace_*.json` |
| 思考效率 | 输出 token ÷ 思考秒数（tok/s） | `traces/*/trace_*.json` |
| token 消耗 | `totalTokens`（输入+输出+缓存）按会话/模型/天聚合 | `traces/*/trace_*.json` |
| credit 消耗 | `session_usage.credit_json` 会话级汇总；看板按「归首日」归因到会话首次出现日；提供用量导出 xlsx 可覆盖为精确值 | `workbuddy.db` |
| Top 会话 | 按 token 消耗降序取前 10 个会话，列出标题/token/思考时长/credit/错误数 | `traces/*` + `workbuddy.db` |

---

## 6. 已知限制

1. **token 是权威主指标，本地精确，无跨天归属问题；credit 是次要估算指标**：本地 `credit_json` 没有逐日时间戳，抽取器把一个会话的 credit 归因到它「首次出现」的那天，所以仍是估算。需要精确到分，用 `--credit-xlsx` 导入用量记录进一步分析。

2. 快照式：手动/定时跑。要实时监督需包成常驻服务（直接查 DB+traces）。

3. 首跑耗时：全量解析 traces（可能上千文件、上 GB）约 10–30 秒，一次性。

---

## 7. 故障排查

| 现象 | 原因 / 处理 |
| ---- | ---- |
| 打开 HTML 显示"数据未加载" | 没先跑脚本；或脚本报错中断。重跑 `usage_extractor.py` 看 stderr |
| 图表空白但数字在 | 极少见：若报错"缺少 chart.umd.min.js"，确认该文件与 usage_extractor.py 同在 scripts/ 下后重跑；正常情况 Chart.js 已随包内联，零外网依赖 |
| 数据库被占用 | 抽取器以只读模式（`mode=ro`）打开，正常不影响正在运行的 WorkBuddy |
| 数据明显偏少 | 这台机器 traces 少/刚装；或 `--home` 指错了目录 |

---

## ❓ 常见问题（FAQ）

**Q：看板里的数字和 WorkBuddy 自己显示的对不上？**
A：本看板只读 `~/.workbuddy` 下的本地数据（traces + workbuddy.db），与 WorkBuddy 自身统计口径可能不同——本工具只统计「有 token 消耗的请求」，排除零用量的工作流记账噪声。以本看板口径为准，详见 data-guide.md。

**Q：为什么某天 credit 特别高、相邻几天却是 0？**
A：因为本地 `credit_json` 没有逐日时间戳，看板把整个会话的 credit 归因到它「首次出现」的那天（归首日）。所以一个跨多天的会话，credit 只在它第一次出现的那天计入「每日 credit」，后续天不体现——这正好避免了把 credit 编造到免费/无消费的日子里（例如用免费 HY3 续跑的旧会话，后续天只有 token、零 credit）。这是当前本地数据源的粒度极限；要精确到分，用 `--credit-xlsx` 导入用量导出（见已知限制第 1 条）。

**Q：跑完脚本数字很少 / 怀疑报告不完整？**
A：脚本对损坏或无法解析的 trace 文件会跳过，并在结尾打印「⚠ 数据完整性提示」，看板顶部也会显示黄色提示条（列出被跳过的文件名）。提示存在即说明这些 trace 已损坏、相关时段数据会缺失；可去 `~/.workbuddy/traces` 下核对对应文件。

**Q：为什么不能实时刷新、一直挂着看？**
A：看板是按需生成的静态单文件 HTML，设计上零外网、不常驻进程。要定期更新，可用 WorkBuddy 的「自动化 / 定时任务」每天跑一次抽取命令（见第 4 节）。

**Q：第一次跑很慢？**
A：全量解析 traces（可能上千文件）只需一次，约 10–30 秒，之后每次都很快（见已知限制第 3 条）。

**Q：对话里怎么说才能触发这个 skill？**
A：用自然语言描述「查看 / 生成 WorkBuddy 使用状态」即可，无需记关键词。

---

## 8. 适用使用场景

- AI 工具成本管控：监控 WorkBuddy 的 token/credit 消耗，避免预算超支

- 模型性价比对比：通过思考效率（tok/s）横向对比不同模型的实际表现

- 项目用量审计：统计单个项目/会话的 AI 资源消耗，核算项目成本

- Agent 工作效率评估：量化 WorkBuddy 的思考时长、错误率，优化 Agent 配置

- 合规与隐私审计：离线可视化本地数据，满足企业/个人的数据隐私要求

---

## 📝 更新日志

详细版本变更记录请查看 CHANGELOG.md。

当前最新版本：v1.2.0（2026-08-10）

---

## 👤 关于作者

本技能由 WorkBuddy 重度用户开发，专注 AI 工具用量可视化方向。

- 小红书：@AI管家老冯 - 分享 WorkBuddy 使用技巧与技能更新动态

- GitHub：clancy-feng

- SkillHub：workbuddy-usage-status

- ClawHub：workbuddy-usage-status
