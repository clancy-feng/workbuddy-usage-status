---
name: "workbuddy-usage-status"
version: 1.1.0
description: "离线可视化 WorkBuddy 本机使用数据（token / credit 消耗、思考效率、模型分布与性价比、日期区间筛选、错误监控、用量高峰探查），生成本地使用信息看板。当用户想查看、生成或导出 WorkBuddy 使用状态 / 使用统计 / 工作信息看板时调用。纯本地、零外网依赖、可搬运。 EN: Offline dashboard for WorkBuddy local usage analytics (token / credit consumption, thinking efficiency, model distribution & cost-performance, date-range filtering, error monitoring, usage-spike inspection). Triggers when the user wants to view, generate, or export their WorkBuddy usage status / stats / activity dashboard. Fully local, zero network dependency, portable."
agent_created: true
allowed-tools: python3, read_file, write_file
metadata:
  clawdbot:
    emoji: "📊"
    requires:
      bins:
        - python3
    requires.env: []
---

# WorkBuddy Usage Status

读取 **本机** `~/.workbuddy/` 下的本地数据，生成一份离线可用的使用监督 Dashboard。
无需任何外部 API、不需要登录、不联网取数——所有数据都在你自己的机器上。

## 适用场景

- 直观看到 WorkBuddy 的 token 消耗、思考用时、思考效率、各模型成本
- 使用监督/用量控制：哪个会话/模型最费、错误集中在哪、哪天用量飙升

## 数据源（已验证存在）

| 来源                                            | 内容                                                                                          | 对应指标                                              |
| --------------------------------------------- | ------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| `~/.workbuddy/workbuddy.db` → `sessions`      | 会话标题、模型、状态、创建/更新时间                                                                          | 按会话/项目归集                                          |
| `~/.workbuddy/workbuddy.db` → `session_usage` | `used`(token 预算)、`size`(上下文上限)、`credit_json`(会话级 credit 消耗汇总)                               | token 消耗 / 费用 / 模型级 credit 性价比（关联 sessions.model） |
| `~/.workbuddy/traces/*/trace_*.json`          | 每次请求的 `duration`、token 拆分(input/output/cached)、`callCount`、`modelInfo.models`，以及 `spans` 时序 | **思考用时**、**思考效率**、模型分布、工具调用、错误数                   |

## 如何安装

#### 方式一：通过 WorkBuddy 对话安装

把 SkillHub 提供的 prompt 发给你的 WorkBuddy 即可：
请根据 https://skillhub.cn/install/skillhub.md ，安装 workbuddy-usage-status。

### 方式二：通过 Clawhub 安装

clawhub install workbuddy-usage-status

#### 方式三：手动安装到本地

git clone https://gitee.com/beclancy/workbuddy-usage-status.git ~/.workbuddy/skills/workbuddy-usage-status

## 用法

装好后，在 WorkBuddy 对话里说"workbuddy 使用统计"，或命令行跑 `python3 scripts/usage_extractor.py`，运行后会在输出目录生成 3 个文件，直接打开 `workbuddy-usage-status-dashboard.html` 即可：

- `workbuddy-usage-status-dashboard.html` —— **自包含单文件**，数据 + Chart.js 均已内联，双击/预览即可看，零外网依赖
- `usage-status.json` / `usage-status.js` —— 原始聚合数据，供二次处理

## 指标定义

- **思考用时**：每条 trace 里 `type=generation` 的 span 时长之和（模型推理/思考的代理指标，单位秒/小时）
- **思考效率**：输出 token ÷ 思考秒数（tok/s，越高越“省时”）
- **token 消耗**：`totalTokens`（输入+输出+缓存），按会话/模型/天聚合
- **credit 消耗**：来自 `session_usage.credit_json` 的会话级汇总

## 已知限制

1. 模型级 credit 性价比已通过关联 `sessions.model`（会话选模字段）实现，用量统计以「有效 trace」（有实际 token 消耗的 trace）为基数。credit 值来自 `session_usage.credit_json`（会话级汇总），已包含 WorkBuddy 内部倍率/折扣/限免；本 skill 只做读取和聚合，不做二次换算。
2. 当前为**快照式**：手动/定时跑脚本生成，要实时监督需包成常驻服务。
3. 解析全量 traces（可能上千文件、上 GB）约需 10–30 秒，属一次性开销。

## 可移植性

- 脚本只依赖 Python 标准库（`sqlite3/json/glob/os/datetime/argparse`），**不需要 pip 安装任何包**。
- 数据源路径用 `os.path.expanduser("~/.workbuddy")`，任何装了 WorkBuddy 的机器路径一致。
- Dashboard 彻底离线，Chart.js 随包内联进 HTML，零外网依赖，预览/双击均可正常出图。
- 模板 `dashboard_template.html` 与脚本同目录打包，输出落到 `--out`（默认 cwd），与脚本位置无关。
- 搬运方式：把整个 `workbuddy-usage-status/` 文件夹复制到目标机器的 `~/.workbuddy/skills/` 下即可。

## 安全与隐私声明

- **数据范围**：默认仅读取当前用户本机 `~/.workbuddy/` 下的 `workbuddy.db` 和 `traces/`，不访问其他任何目录
- **可选数据根**：脚本支持 `--home <dir>` 把读取根指向其他目录（默认 `~/.workbuddy`），仅用于迁移/测试场景；该旗标会改变实际读取路径，请仅在可信目录使用，且仍只读取该目录下的 `workbuddy.db` 与 `traces/`
- **网络行为**：零外部请求，不调用任何 API、不上传任何数据
- **写入行为**：仅在指定的输出目录写入 3 个文件（HTML/JSON/JS），不修改 WorkBuddy 自身的任何数据
- **数据库访问**：以只读模式（`mode=ro`）打开 `workbuddy.db`，不影响正在运行的 WorkBuddy
- **凭据处理**：不读取、不解析、不输出任何 API key / token / 密码
