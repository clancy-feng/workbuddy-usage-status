# 数据与方法说明（Data Guide）

本文档解释 `workbuddy-usage-status` 看板里每一个数字是怎么算出来的、数据从哪来、各个阈值/参数的含义，供参考；看板页面本身只保留必要的简短提示。

---

## 1. 数据来源

看板只读本地文件，不修改、不上传 `workbuddy.db`。两个数据源：

| 数据源  | 路径（默认 `~/.workbuddy/`）                           | 提供什么                               |
| ---- | ------------------------------------------------ | ---------------------------------- |
| 会话库  | `workbuddy.db`（`sessions` 表 + `session_usage` 表） | 会话标题/状态/模型、credit 消耗汇总             |
| 调用轨迹 | `traces/*/trace_*.json`（每个文件 = 一次请求）             | 每次请求的时长、token 拆分、思考用时、模型、工具调用数、错误数 |

两个口径相互独立：

- Token / 思考用时 / 错误 / 工具调用：来自 `traces/`，按"一次请求"逐条统计。
- Credit（积分）：来自 `workbuddy.db → session_usage.credit_json`，是 WorkBuddy 自己的计费汇总，本 skill 只读取、不做二次换算。

因此"某个会话 token 很多但 credit=0"是正常现象（见第 6 节）。

---

## 2. 基础指标口径

### 2.1 一次请求（trace）记录了什么

- Token 总量 `tokens` = trace 顶层 `totalTokens`。
- 输入 / 输出 / 缓存 token = `modelInfo.totalInputTokens` / `totalOutputTokens` / `totalCachedTokens`。
- 思考用时 `thinking_sec` = 该 trace 里所有 `type=generation`（模型推理）的 span 时长之和（毫秒转秒）；工具调用（tool/mcp/function）时长不计入，它是"模型推理/思考"的代理指标。
- 工具调用次数 `calls` = `modelInfo.callCount`。
- 错误数 `errors` = trace 里 `status=error` 或带 `error` 字段的 span 数量（span 级，一次请求可能多个 span 报错）。
- 模型名 `model` = `modelInfo.models` 拼接；无则记 `unknown`。
- 会话归属 `session_id` = trace 的 `sessionId`。统计仅计入有实际 token 消耗的 trace；无 token 的工作流记账记录（零用量噪声）不进入用量基数。在有效 trace 上，`sessionId` 与 `workbuddy.db` 的 `sessions` 表 100% 对应——所有真实用量都能精确归到某个会话。

### 2.2 聚合口径

- 按天：以请求 `startedAt` 转本地时区的日期（`%Y-%m-%d`）分组。
- 按模型：以 trace 的 `model` 字段聚合。
- 按会话：以 `session_id` 聚合；会话标题/状态/模型取自 `sessions` 表（`custom_title` 优先于 `title`）。
- Credit **归首日**：本地 `credit_json` 没有逐日时间戳，无法精确拆分到天。一个跨多天的会话，其 credit 整体归因到它首次出现的那一天；如需要精确到分，见第 10 节 `--credit-xlsx`。

---

## 3. 顶部 KPI（总览数字）

| KPI       | 计算方式                                  |
| --------- | ------------------------------------- |
| 总请求数      | `traces` 文件总数                         |
| 总 Token   | 所有请求 `tokens` 之和                      |
| 思考用时      | 所有请求 `thinking_sec` 之和，换算成小时（`/3600`） |
| 平均效率      | `总输出 token / 总思考秒数`（tok/s）            |
| Credit 消耗 | 所有会话 `session_usage.credit` 之和        |
| 错误数       | 所有请求 `errors` 之和                      |

---

## 4. 各图表 / 卡片的计算方法与参数

### 4.1 每日积分消耗（credit）

- 每日 credit = 当天首次出现且有 credit 的会话，其会话级 credit 之和。
- **这是本地估算**：「归首日」近似值，非精确值，因本地 `credit_json` 与服务端有偏差，且本地无逐日时间戳。
- 精确化：若运行脚本时传入 `--credit-xlsx <路径>`（用量导出 xlsx），则对应日期窗口内的每日 credit 以 xlsx 中的服务端精确值覆盖，看板该卡片 / KPI 标注「精确（用量导出）」；未覆盖日期仍为本地估算。

### 4.2 每日思考用时

- 每日 = 当天所有请求的 `thinking_sec` 之和（小时为单位展示）。

### 4.3 各模型 Token 占比

- 按 `model` 聚合 `tokens`，降序。
- Top10 之外合并为「其他」：只展示 token 最多的前 10 个模型标签，其余归入一个"其他"扇区。

### 4.4 主要模型思考效率

- 公式：效率 = 输出 token / 思考秒数（`output / thinking_sec`），单位 tok/s，越高越"省时"。
- 仅列 Top10：效率最高 / token 突出的前 10 个具体模型标签。
- 最小样本门槛 `MIN_EFF_SAMPLES = 10`：一个模型**思考次数 < 10** 时不计入效率排名。

### 4.5 思考效率散点图

- 每个点 = 一次请求，横轴=思考用时，纵轴=输出 token（取 token 最大的前 300 次请求以控制体积）。
- 读图：右下方 = 高产出 + 低耗时 = 高效率。

### 4.6 Top 10 会话

- 按 token 总量降序取前 10 个会话。
- 列：标题 / 模型 / 请求数 / Token / 思考(分) / Credit / 错误 / 状态。

### 4.7 每日错误数

- 每日 = 当天所有请求的 `errors`（span 级）之和。

### 4.8 时间轴自适应粒度（日 / 周 / 月）

看板顶部四张时序图（每日 Token / 每日 credit / 每日思考用时 / 每日错误数）的横轴，会**根据当前筛选范围的跨度自动切换桶粒度**，避免数据累积到两三年后横坐标过密、标签重叠：

- 跨度 ≤ 120 天 → **按日**（每日本地时区归日精确值）
- 120 天 < 跨度 ≤ 730 天 → **按周**（以周一为周起始，ISO 周聚合）
- 跨度 > 730 天 → **按月**（YYYY-MM 聚合）

聚合方式：先按请求 `startedAt` 本地时区归日，再按所选粒度把该周期内的 token / credit / 思考秒 / 错误数**求和**得到周期总量。卡片标题（"每日 / 每周 / 每月 …"）与副标题（"聚合粒度：按日 / 按周 / 按月"）会同步更新，明确当前展示的是每日值还是周期总量。缩放日期筛选范围时粒度会实时重算。

---

## 5. 用量高峰探查（自动定位明显高的使用日）

### 5.1 选哪几天（"几天？怎么选的"）

1. 先算每日 token 总量的中位数 `median_tok`（请求级精确到天）。
2. 阈值 `thr = max(median_tok × 2, 500 万)`：token 高于中位数 2 倍且至少 500 万的日子，才算"明显高"。
3. 满足的日子按 token 倒序，最多取 6 天。
4. 兜底：若满足条件的不足 3 天，则直接取 token 最高的 3 天。

说明：排序依据从 credit 改为 token，因为 token 按请求 `startedAt` 精确到天，而 credit 是归首日估算、无法精确到天。用 token 定位"使用高峰日"才是真实口径。

### 5.2 每天的拆解字段

对每个入选日，逐日给出：

- 当天会话（按 token）：当天有请求的全部会话（不再限"首次出现日"），按当天 token 降序，模型列显示当天实际请求的全部模型（按 token 降序，逗号分隔；会话内跨模型时与右侧构成对齐）。
- 模型 token 构成（Top5）：当天所有请求按 `model` 聚合 token，取前 5（token=0 的不显示）。
- 错误率 = 当天 `errors / 请求数 × 100%`。
- 均 calls/请求 = 当天总 `calls / 请求数`（高 → 可能反复调用）。
- 最大单请求 = 当天单次请求的最大 `tokens`。

会话表的 token 口径与「模型 token 构成」一致（都是当天全部请求），左右 token 总额相等，可对账。

### 5.3 会话显示上限

- 当天会话按 token 倒序，最多显示 8 个；超过 8 个时，其余在表下标注「另有 N 个会话（合计 X token）未列入」。

---

## 6. 模型性价比排行（credit / 10万 token）

### 6.1 归因方法

- 用 `sessions.model`（会话级模型名）聚合 credit 与 token，绕开 `credit_json` 无法反查模型的问题。
- 公式：`credit_per_100k = credit / tokens × 100000`，数值越低越省。
- 过滤：`unknown` 模型、token ≤ 0 的行不进入排行（无法归因或无效）。
- `'auto'`：会话未锁定具体模型（展示用占位，不计入排行）。

### 6.2 零 credit 标记

- `zero_credit = True` 当且仅当：该模型 `credit ≤ 0` 且 `tokens ≥ 100 万`。
- 含义：该模型当前 credit/10万token=0，可能处于限免 / 促销期。这类模型，不进入"优化建议"对比，也不该被当成长期成本基准。

### 6.3 优化建议

- 在可比任务量（各模型 token ≥ 1000 万）的通用模型（排除 `auto`/`unknown`/`preview`/`agent`、且 credit>0）中，取 credit/10万token 最便宜与最贵的两条。
- 预计节省 = `(最贵 - 最便宜) / 最贵 × 100%`；≥ 5% 才给出建议文案。
- 前提提示：两个模型处理的工作负载可互相迁移。

---

## 7. 参数 / 阈值速查表

| 参数 / 阈值                     | 含义 / 当前值                   |
| --------------------------- | -------------------------- |
| `median_tok × 2` 与 `500 万`  | 高用量日门槛：取两者较大值              |
| `top 6` / 兜底 `top 3`        | 入选高用量日上限 / 兜底数量            |
| `会话 top 8`                  | 每天拆解的会话显示上限，超出在表下标注        |
| `Top10`                     | Token 占比扇区、效率图、Top 会话的取数上限 |
| `token ≥ 100 万`             | 判定零 credit 模型、可比样本的最低任务量   |
| `credit/10万token` 节省 `≥ 5%` | 触发优化建议的最小差异                |
| `前 300 次请求`                 | 散点图取样上限（控体积）               |
| `前 200 会话`                  | `by_session` 输出上限（控体积）     |

---

## 8. 已知数据统计限制

1. Credit 与 Token 独立统计：credit 走 `workbuddy.db` 计费口径，token 走 `traces` 实际用量；模型限免期 token 照常计、credit=0。
2. 用量统计以「有效 trace」为基数——无覆盖率缺口：有效 trace = 有实际 token 消耗的 trace，已排除工作流记账噪声数据。在有效 trace 上，`sessionId` 与 `sessions` 表 100% 对应，因此会话/模型下钻、总量、每日趋势、错误数均为完整真实用量。
3. 思考用时是代理指标，为generation span 时长之和，不含工具调用与等待。
4. 错误率是 span 级：一次请求多个 span 报错会重复计入，错误率可能 > 单个请求失败率。
5. 跨日会话 credit **归首日**：因本地无逐日时间戳、只能「归首日」近似（不编造到免费/无消费日）；精确值需用 `--credit-xlsx` 提供官方用量报告实现。
6. 

---

## 9. 输出文件

脚本在同目录（或 `--out` 指定目录）生成：

- `usage-status.json`：原始聚合数据（调试 / 二次处理用）。
- `usage-status.js`：`window.USAGE_STATUS = {...}`，供 HTML 直接 `<script>` 引入，避开 `file://` 的 fetch 跨域。
- `workbuddy-usage-status-dashboard-<时间戳>.html`：自包含离线看板（数据 + Chart.js 全部内联，双击即可离线打开，零外网依赖）；文件名带生成时间戳，每次生成独立文件，不覆盖旧报告，便于保留多份对比。

运行：`python usage_extractor.py [--out 目录] [--home ~/.workbuddy]`

---

## 10. 可选：用用量导出 xlsx 精确化 credit（--credit-xlsx）

### 10.1 从官方网站得到用量明细

1. 打开 `https://www.workbuddy.cn/profile/plans-usage`（用量明细表）。
2. 选日期范围（最多 1 个月），点导出，得到 xlsx。
3. 运行：`python usage_extractor.py --credit-xlsx 路径/xxx.xlsx`

### 10.2 覆盖逻辑

- 读取 xlsx 中每条请求的 `积分消耗` + `时间`，按天汇总成精确每日 credit。
- 用这些精确值覆盖本地数据中对应日期的 `by_day[day].credit`；看板该卡片与 KPI 标注「精确（用量导出）」。
- 未覆盖日期仍为本地「归首日」估算（标注「本地估算」）。

### 10.3 边界与限制

- 只覆盖 1 个月：导出上限如此，长期趋势仍以 token 为准。
- 只能到"天"，不能到"会话"：xlsx 无 sessionId / traceId，无法把精确 credit 归因到具体会话；模型性价比排行仍用本地会话级 credit（估算）。
- 若 xlsx 与本地数据无日期重叠，credit 维持本地估算，并在结尾提示。
