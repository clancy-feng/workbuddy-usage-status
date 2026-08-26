---
name: "workbuddy-usage-status"
slug: workbuddy-usage-status
displayName: "WorkBuddy 使用状态看板"
version: 1.2.5
description: "离线可视化 WorkBuddy 本机使用数据，以 token 消耗为主指标、credit 为本地估算，涵盖思考效率、模型分布与性价比、日期区间筛选、错误监控、用量高峰探查，生成本地使用信息看板。仅当用户**明确**想查看、生成或导出**自己 WorkBuddy 本机/本账号**的使用状态 / 使用统计 / 工作信息看板时调用；不用于其他产品或系统的用量统计，也不为任意数据生成通用看板。纯本地、零外网依赖、可搬运。可选 --credit-xlsx 用用量导出精确覆盖 credit。 EN: Offline dashboard for WorkBuddy local usage analytics, with token as primary metric and credit as local estimate, covering thinking efficiency, model distribution & cost-performance, date-range filtering, error monitoring, usage-spike inspection. Triggers only when the user explicitly wants to view, generate, or export their own WorkBuddy local/account usage status / stats / activity dashboard; not for other products' usage analytics, nor for building generic dashboards from arbitrary data. Fully local, zero network dependency, portable. Optional --credit-xlsx overrides credit with precise export values."
agent_created: true
license: MIT
summary: "离线可视化 WorkBuddy 本机使用数据，以 token 消耗为主指标、credit 为本地估算，涵盖思考效率、模型分布与性价比、日期区间筛选、错误监控、用量高峰探查，生成本地使用信息看板。仅当用户**明确**想查看、生成或导出**自己 WorkBuddy 本机/本账号**的使用状态 / 使用统计 / 工作信息看板时调用；不用于其他产品或系统的用量统计，也不为任意数据生成通用看板。纯本地、零外网依赖、可搬运。可选 --credit-xlsx 用用量导出精确覆盖 credit。 EN: Offline dashboard for WorkBuddy local usage analytics, with token as primary metric and credit as local estimate, covering thinking efficiency, model distribution & cost-performance, date-range filtering, error monitoring, usage-spike inspection. Triggers only when the user explicitly wants to view, generate, or export their own WorkBuddy local/account usage status / stats / activity dashboard; not for other products' usage analytics, nor for building generic dashboards from arbitrary data. Fully local, zero network dependency, portable. Optional --credit-xlsx overrides credit with precise export values."
allowed-tools: python3, read_file, write_file
metadata:
  clawdbot:
    emoji: "📊"
    requires:
      bins:
        - python3
    requires.env: []
---

# WorkBuddy Usage Status —— Agent 执行指令

本技能是给 AI 的执行说明书，用户视角的安装、读图、故障排查见 `README.md`；指标算法口径见 `DATA-GUIDE.md`。

## 触发条件

当用户**明确指向 WorkBuddy 自身**、表达以下意图时调用本技能。description 已含触发词，此处强化判断与收紧边界：

- 想查看 / 生成 / 导出**自己的 WorkBuddy** 使用统计、工作量、成本看板；
- 关心 token 消耗、模型分布与性价比、思考效率、错误监控、用量高峰日等任一维度在 WorkBuddy 本机 trace 中的数据；
- 想对账某段时间 WorkBuddy 用了多少积分（credit）。

**反向触发词（明确不适用）**：用户意图指向以下任一情况时，本技能不适用，应直接告知用户而非静默跳过：

- 想查看 / 统计**其他产品**（如 Cursor、VS Code、Trae、Claude 等任意第三方系统）的用量、数据、分析；
- 仅泛指"导出我的数据 / 做个统计图表 / 生成看板"，未明确指向 WorkBuddy 本机用量；
- 想为任意数据集生成通用可视化 / 报表；本技能只读 `~/.workbuddy`，不具备通用图表能力。

遇到上述情况，回复要点：本技能只读取并可视化 WorkBuddy 本机（`~/.workbuddy`）的使用数据，不涉及其他产品或通用数据；请确认是否要分析 WorkBuddy 自身用量，或改用对应产品的工具。

## 执行步骤

抽取器仅依赖 Python 标准库，运行前无需 pip 安装任何包。在技能目录下运行：

```
python3 scripts/usage_extractor.py [--out <输出目录>] [--home <数据根>] [--credit-xlsx <路径>]
```

- `--out <dir>`：输出目录，默认当前工作目录。
- `--home <dir>`：指定数据根目录，默认 `~/.workbuddy`；此参数仅用于迁移或测试，会改变实际读取路径，日常使用不要加。
- `--credit-xlsx <path>`：传入从 `workbuddy.cn` 用量页导出的 xlsx，用服务端精确 credit 覆盖对应日期窗口；用于"查清某月精确花费/对账"。默认不主动使用，仅当用户明确要求精确 credit 时再加。

执行后在该目录生成 3 个文件：

- `workbuddy-usage-status-dashboard-<时间戳>.html` —— 自包含单文件，数据与 Chart.js 均已内联，双击/预览即可看，零外网依赖；文件名带生成时间戳，每次生成独立文件，不覆盖旧报告；
- `usage-status.json` —— 原始聚合数据，供二次处理；
- `usage-status.js` —— `window.USAGE_STATUS = {...}`，供 HTML 通过 `<script>` 直接引入，以此避开 `file://` 的 fetch 跨域限制。

## 交付方式

生成完成后，在输出目录找到最新生成的 `workbuddy-usage-status-dashboard-*.html`，按文件名时间戳取最大者，用 `present_files` 打开预览交回给用户。无需解释全部图表，交给用户自行浏览即可。

## 约束与口径

- 只读不写：仅以只读模式读 `~/.workbuddy` 下的 `workbuddy.db` 与 `traces/`，只读参数固定为 mode=ro；不修改 WorkBuddy 自身数据、零外部请求、不上传任何数据、不读取任何 API key/密码。
- 指标口径：token 为权威主指标，本地 trace 带精确时间戳，按请求本地时区归日，精确；credit 为会话级估算，本地无逐日时间戳，按归首日近似，非精确值，精确值只能由 `--credit-xlsx` 给出。各指标的具体算法、聚合口径与已知限制见 `DATA-GUIDE.md`，不要凭空编造数字。
- 数据完整性：抽取器顶部警告条已列出被跳过/解析失败的 trace 与会话，报告可能不完整属正常现象，如实告知用户即可。

## 相关文档

- `README.md`：用户视角的安装、使用场景、读图指南、故障排查。
- `DATA-GUIDE.md`：指标计算的唯一真相源，含算法、聚合口径、图表参数、归因方法、已知限制。
