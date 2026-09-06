---
name: "workbuddy-usage-status"
slug: workbuddy-usage-status
displayName: "WorkBuddy 使用状态看板"
version: 1.3.2
description: "离线可视化 WorkBuddy 本机使用数据，以 token 消耗为主指标、credit 为本地估算，涵盖思考效率、模型分布与性价比、日期区间筛选、错误监控、用量高峰探查，生成本地使用信息看板。仅当用户**明确**想查看、生成或导出**自己 WorkBuddy 本机/本账号**的使用状态 / 使用统计 / 工作信息看板时调用；不用于其他产品或系统的用量统计，也不为任意数据生成通用看板。纯本地、默认零外网依赖、可搬运；可选 --credit-xlsx 用用量导出精确覆盖 credit，或可选 --billing-token-file（用户手动导出 token，opt-in）调用官方用量 API 拉取精确 credit。 EN: Offline dashboard for WorkBuddy local usage analytics, with token as primary metric and credit as local estimate, covering thinking efficiency, model distribution & cost-performance, date-range filtering, error monitoring, usage-spike inspection. Triggers only when the user explicitly wants to view, generate, or export their own WorkBuddy local/account usage status / stats / activity dashboard; not for other products' usage analytics, nor for building generic dashboards from arbitrary data. Fully local, default zero-network; optionally --billing-token-file (user-supplied token, opt-in) calls the official usage API for precise credit, or --credit-xlsx overrides credit with precise export values."
agent_created: true
license: MIT
summary: "离线可视化 WorkBuddy 本机使用数据，以 token 消耗为主指标、credit 为本地估算，涵盖思考效率、模型分布与性价比、日期区间筛选、错误监控、用量高峰探查，生成本地使用信息看板。仅当用户**明确**想查看、生成或导出**自己 WorkBuddy 本机/本账号**的使用状态 / 使用统计 / 工作信息看板时调用；不用于其他产品或系统的用量统计，也不为任意数据生成通用看板。纯本地、默认零外网依赖、可搬运；可选 --credit-xlsx 用用量导出精确覆盖 credit，或可选 --billing-token-file（用户手动导出 token，opt-in）调用官方用量 API 拉取精确 credit。 EN: Offline dashboard for WorkBuddy local usage analytics, with token as primary metric and credit as local estimate, covering thinking efficiency, model distribution & cost-performance, date-range filtering, error monitoring, usage-spike inspection. Triggers only when the user explicitly wants to view, generate, or export their own WorkBuddy local/account usage status / stats / activity dashboard; not for other products' usage analytics, nor for building generic dashboards from arbitrary data. Fully local, default zero-network; optionally --billing-token-file (user-supplied token, opt-in) calls the official usage API for precise credit, or --credit-xlsx overrides credit with precise export values."
allowed-tools: python3, read_file, write_file
permissions:
  - file_read
  - file_write
  - network
metadata:
  clawdbot:
    emoji: "📊"
    requires:
      bins:
        - python3
    requires.env: []
---
#

## 💖 支持这个项目

> 📊 已被 **1600+** WorkBuddy 用户下载使用，覆盖 SkillHub & ClawHub 双平台。

如果这个工具帮到了你，欢迎：

- ⭐ 去 GitHub 点个 Star（这是对我最大的鼓励）
- 🐛 遇到问题提 Issue
- 📢 分享给你的 WorkBuddy 用户朋友

**GitHub**：<https://github.com/clancy-feng/workbuddy-usage-status>

## 界面预览

![preview](https://raw.githubusercontent.com/clancy-feng/workbuddy-usage-status/refs/heads/main/assets/dashboard-preview-1.png)

## 技能简介

把 WorkBuddy 自己的本地使用数据，变成一份离线可查的 Dashboard。

可以看到：token 消耗、思考用时、思考效率、模型分布、错误数、credit 消耗。

## ✨ 核心功能特性

- Token/Credit 全链路可视化：按模型、按日期、按会话统计，一眼定位"烧钱大户"
- 用量高峰探查：自动按 credit 排序列出最高的几天，逐日拆解主导会话、模型 token 构成、错误率、最大单次请求——精确到"哪一天花了多少"，帮你快速定位消耗集中日
- 思考效率量化：输出 token ÷ 思考秒数（tok/s），横向对比模型性价比
- 错误集中监控：快速定位报错频繁的会话/模型，降低调试成本，可导出错误详情。
- 离线运行：Chart.js 已内联，单文件 HTML 双击即看
- 只读无侵入：以只读模式访问 WorkBuddy 数据，不影响正在运行的程序
- 跨平台兼容：支持 Windows/macOS/Linux，Python 3.10+ 即可运行

## WorkBuddy Usage Status —— Agent 执行指令

本文件是给 AI 的执行说明书，用户视角的安装、读图、故障排查见 `README.md`；指标算法口径见 `DATA-GUIDE.md`。

## 触发条件

当用户**明确指向 WorkBuddy 自身**、表达以下意图时调用本技能。description 已含触发词，此处强化判断与收紧边界：

- 想查看 / 生成 / 导出**自己的 WorkBuddy** 使用统计、工作量、成本看板；
- 关心 token 消耗、模型分布与性价比、思考效率、错误监控、用量高峰日等任一维度在 WorkBuddy 本机 trace 中的数据；
- 想对账某段时间 WorkBuddy 用了多少积分（credit）。

**反向触发词**：用户意图指向以下任一情况时，本技能不适用，应直接告知用户而非静默跳过：

- 想查看 / 统计**其他产品**（如 Cursor、VS Code、Trae、Claude 等任意第三方系统）的用量、数据、分析；
- 仅泛指"导出我的数据 / 做个统计图表 / 生成看板"，未明确指向 WorkBuddy 本机用量；
- 想为任意数据集生成通用可视化 / 报表；本技能只读 `~/.workbuddy`，不具备通用图表能力。

遇到上述情况，回复要点：本技能只读取并可视化 WorkBuddy 本机（`~/.workbuddy`）的使用数据，不涉及其他产品或通用数据；请确认是否要分析 WorkBuddy 自身用量，或改用对应产品的工具。

## 执行步骤

抽取器仅依赖 Python 标准库，运行前无需 pip 安装任何包。在技能目录下运行：

```
python3 scripts/usage_extractor.py [--out <输出目录>] [--home <数据根>] [--credit-xlsx <路径>] [--billing-token-file <路径>]
```

- `--out <dir>`：输出目录，默认当前工作目录。
- `--home <dir>`：指定数据根目录，默认 `~/.workbuddy`；此参数仅用于迁移或测试，会改变实际读取路径，日常使用不要加。
- `--credit-xlsx <path>`：传入从 `workbuddy.cn` 用量页导出的 xlsx，用服务端精确 credit 覆盖对应日期窗口；用于"查清某月精确花费/对账"。默认不主动使用，仅当用户明确要求精确 credit 时再加。
- `--billing-token-file <path>`：Path A（opt-in，默认关闭）。用户从自己浏览器 DevTools 手动复制用量 API 的鉴权头（如 `Cookie: ...` 整行，或 `Authorization: Bearer ...`）存入本地文件后传入，skill 以该 token 调用官方用量 API（`/billing/meter/get-user-request-usage`）拉取逐请求精确 credit，效果同 `--credit-xlsx` 但无需先导出 xlsx。**token 必须由用户显式提供，skill 绝不自动读取宿主 App 凭据存储**；不传此参数时零网络。详见 README「精确化 credit（可选）」与 `CHANGELOG.md` 安全等级评估。
  > ⚠️ **用户侧 token 安全提醒**：该 token 文件等同于你的 WorkBuddy 会话凭证，**请当作密码保管**——① 不要提交到任何 Git 仓库 / 云盘 / 聊天工具；② 限制文件权限（如 `chmod 600`），用完即删或在 workbuddy.cn 退出登录使其失效；③ 不要分享给他人，也不要长期留存明文。skill 只在使用该参数时联网一次，且绝不自动读取宿主 App 的凭据存储。

执行后在该目录生成 3 个文件：

- `workbuddy-usage-status-dashboard-<时间戳>.html` —— 自包含单文件，数据与 Chart.js 均已内联，双击/预览即可看，零外网依赖；文件名带生成时间戳，每次生成独立文件，不覆盖旧报告；
- `usage-status.json` —— 原始聚合数据，供二次处理；
- `usage-status.js` —— `window.USAGE_STATUS = {...}`，供 HTML 通过 `<script>` 直接引入，以此避开 `file://` 的 fetch 跨域限制。

## 交付方式

生成完成后，在输出目录找到最新生成的 `workbuddy-usage-status-dashboard-*.html`，按文件名时间戳取最大者，用 `present_files` 打开预览交回给用户。

## 约束与口径

- 只读不写：仅以只读模式读 `~/.workbuddy` 下的 `workbuddy.db` 与 `traces/`，只读参数固定为 mode=ro；不修改 WorkBuddy 自身数据、不上传任何数据、不读取任何 API key/密码。默认零外部请求；**仅当用户显式传入 `--billing-token-file` 时**才向官方用量 API（`workbuddy.cn`）发起一次出站 HTTPS 请求，且鉴权凭据由用户提供（绝不自动读取宿主 App 凭据存储）。不传该参数时完全离线。
- 指标口径：token 为权威主指标，本地 trace 带精确时间戳，按请求本地时区归日，精确；credit 为会话级估算，本地无逐日时间戳，按归首日近似，非精确值，精确值只能由 `--credit-xlsx` 给出。各指标的具体算法、聚合口径与已知限制见 `DATA-GUIDE.md`，不要凭空编造数字。
- 数据完整性：抽取器顶部警告条已列出被跳过/解析失败的 trace 与会话，报告可能不完整属正常现象，如实告知用户即可。
- 产物有界：每次运行仅生成上述 3 个固定文件（dashboard HTML / `usage-status.json` / `usage-status.js`），规模由本地 `~/.workbuddy` 数据量天然限定，不存在无界输出。

## 相关文档

- `README.md`：用户视角的安装、使用场景、读图指南、故障排查。
- `DATA-GUIDE.md`：指标计算的唯一真相源，含算法、聚合口径、图表参数、归因方法、已知限制。
- `CHANGELOG.md`：版本变更记录与安全等级评估。

> 本技能所有引用文档（`README.md` / `DATA-GUIDE.md` / `CHANGELOG.md`）均随skill发布、与 `SKILL.md` 同源，不存在其他外部依赖。
