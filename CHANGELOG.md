# Changelog

所有版本保持单文件 HTML、Chart.js 内联、零外网依赖、纯本地只读（不修改 `workbuddy.db`）。

## [1.2.0] - 2026-08-08

### 新增 · 模型性价比排行（Model Cost Ranking）
- 看板新增「模型性价比排行」表格，按 `credit_per_1k_tokens` 升序排列（越靠前越省）。
- 每个模型展示：会话数、总 token、总 credit、每千 token 消耗的 credit。
- 自动产出优化建议，例如：`在可比任务量下切换至 deepseek-v4-flash 相比 hy3 预计节省约 67% 的 credit`（自动排除 `preview`/`agent` 变体与 `auto`，只比较样本量 ≥1000 万 token 的模型）。
- 归因方法：用 `sessions.model`（会话级模型名）× `session_usage.used`（会话总 credit）× 会话总 token，在**会话级**计算，不再依赖 `credit_json` 的模型哈希。

### 新增 · 异常检测（Anomaly Detection）
- **异常日**：对每日总 token 计算 7 日滚动均值与标准差，标记 `|当日值 − 均值| > 2 × 标准差` 的日期（滚动窗口不足时 std=0，不误报）。
- **超额会话**：对每个会话计算 token 消耗，标记超过历史 95 分位的会话。
- 看板顶部新增「异常警报」卡片：列出异常日（含 z 值）与超额会话（含超出比例），并给出处置建议；无异常时显示 `✅ 一切正常`。

### 变更 · 数据归因方法
- **放弃**原设想的「`credit_json` 哈希 → 模型名反查」路径：实测 1110 个哈希并非模型标识，DB 与 trace 均无哈希→模型映射。
- **改用** `sessions.model` 列（真实模型名，值为 `hy3` / `deepseek-v4-flash` / `deepseek-v4-pro` / `hy3-preview-agent` / `auto`）做会话级归因，绕开哈希死路。

### 文档
- `SKILL.md`：功能描述、数据源说明（新增 `sessions.model` 归因）、已知限制同步；触发词补充 `model cost` / `cost control` / `AI cost monitoring`。
- `README.md`：英文概述补充新功能；调用触发词列表补充成本监控类。

### 已知限制（延续）
- trace → session 关联靠 `trace.sessionId`，实测覆盖率约 44%（3092 个 trace 中 1361 个带 sessionId），模型排名基于有会话归属的部分数据，非 100% 全量。
- `credit_json` 仍只能到会话级，无法精确拆分到单次 generation span。

---

## [1.0.0] - 2026-07-22

### 初始发布
- 离线可视化 WorkBuddy 本地使用数据：总览 KPI（请求数 / 会话数 / 总 token / 思考用时 / 总 credit / 错误数）、每日 token 与 credit 趋势图、按模型分布、Top 10 会话（按 token）、每日错误数。
- 抽取器 `usage_extractor.py` 仅用 Python 标准库；生成的 HTML 单文件、Chart.js 内联、零外网依赖。
- 可搬运到其他 WorkBuddy 机器使用。
