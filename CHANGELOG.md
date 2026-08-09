# Changelog

所有版本保持单文件 HTML、Chart.js 内联、零外网依赖、纯本地只读（不修改 `workbuddy.db`）。

## [1.1.0] - 2026-08-09

### 新增 · 日期区间筛选（Date Range Filter）

- 看板顶部新增「起始日期 / 结束日期」选择器 + 重置按钮。
- 筛选后联动更新：KPI 数字、每日 credit/思考用时/错误数折线图、模型 Token 占比、模型思考效率、Top 会话表、模型性价比排行及优化建议。

### 新增 · 模型性价比排行（Model Cost Ranking）

- 看板新增「模型性价比排行」表格，按 `credit_per_1k_tokens` 升序（越靠前越省）；展示会话数 / 总 token / 总 credit / 每千 token credit。
- 自动产出优化建议（如「在可比任务量下切换至 deepseek-v4-flash 相比 hy3 预计节省约 67% credit」），自动排除 `preview`/`agent` 变体与 `auto`，仅比较样本量 ≥1000 万 token 的模型。
- 自动标记 `credit=0` 且 token 不少的模型为「限免/促销」，优化建议排除零 credit 模型并单独提示可能处于促销期。

### 新增 · 用量高峰探查（替代原「异常警报」）

- 新增 `spike_days`：按每日 credit 自动选出明显高用量日（高于所有日中位数的 2 倍且 ≥50，最多 6 天；满足条件的高用量日不足 3 天时，兜底取 credit 最高的 3 天）。
- 对每一天逐日拆解：主导会话（标题/模型/当日 credit/token，按会话首次出现日归因，与每日 credit 口径一致）、模型 token 构成 Top5、错误率、平均 calls/请求、最大单次请求 token——精确到天，回答"哪一天花了多少、由什么任务造成"。
- 主导会话采用 **50 倍比例规则** 过滤：仅显示在当日峰值会话 credit 的 1/50 及以上的会话（峰值与最小显示值差距 ≤50 倍），自动隐藏个位数等小额噪音；零积分会话与不足峰值 1/50 的小额会话合并一行说明（不丢失信息）。

### 改进 · 图表与呈现

- 「各模型 Token 占比」改为 Top 10 + 其他，避免图例爆炸。
- 「各模型思考效率」不再合并「其他」，避免聚合后效率虚高、排名失真。
- 「Top 会话」固定为 **Top 10 会话**（按 token 消耗）。
- 卡片顺序调整：「用量高峰探查」置于「Top 10 会话」之下，阅读顺序 总览 → 明细 → 高峰日拆解。
- 「模型 token 构成（Top5）」渲染时过滤 token=0 的条目（如 `unknown 0`），不再展示无意义零值。

### 新增 · 中英双语 + 语言切换

- 右上角语言切换按钮（中/EN），基于 localStorage 记忆；卡片标题、提示、KPI、表头、用量高峰拆解、优化建议、底部说明均已双语化，默认中文。
- Dashboard 标题改为「Workbuddy使用数据看板」（英文 WorkBuddy Usage Data Dashboard）。

### 修复 · 口径与单位

- 「每日思考用时」卡片标题补回（小时）单位。
- KPI 区新增「总 Credit」一项，6 项填满栅格。
- 页面说明类文字精简；数据口径 / 计算方法 / 参数含义统一迁移至独立的 `data-guide.md`，页面底部改为指针「报告数据说明见 data-guide.md」。

### 修复 · 安全加固

- 内联数据转义：dashboard HTML 内联的 `window.USAGE_STATUS` 现在对 `</` 做 `<\/` 转义，防止会话标题/模型名中的 `</script>` 冲破 script 边界（本地存储型 XSS 防护）。
- 读取范围披露：`SKILL.md` 隐私声明新增 `--home` 旗标说明——默认仅读取 `~/.workbuddy/`，该旗标可指向其他目录（仅用于迁移/测试），仍只读取其下的 `workbuddy.db` 与 `traces/`。
- 触发词收窄：删除 description 中与 WorkBuddy 用量无关、易误触发的泛财务短语（'telemetry' / '用了多少额度' / '烧了多少钱' / '哪个模型最省' / '模型性价比'），保留 WorkBuddy 命名空间内的精确触发词。

### 数据归因方法

- 改用 `sessions.model`（真实模型名）× `session_usage.used`（会话总 credit）× 会话总 token，在**会话级**计算，绕开原 `credit_json` 哈希死路（实测哈希无法反解为模型名）。
- `credit` 来自 `session_usage.used`，已含 WorkBuddy 内部倍率 / 折扣 / 限免，skill 不做二次换算。

### 已知限制

- 用量统计仅计入有 token 消耗的有效 trace，零 token 工作流记账（噪声）排除；有效 trace 的 `sessionId` 与 `sessions` 表 100% 对应，会话级归因完整，无「覆盖率稀释」问题。宏观总量/每日趋势/模型占比来自有效 trace 与 `session_usage` 表，完整准确。
- `credit_json` 仍只能到会话级，无法精确拆分到单次 generation span。

---

## [1.0.0] - 2026-07-22

### 初始发布

- 离线可视化 WorkBuddy 本地使用数据：总览 KPI（请求数 / 会话数 / 总 token / 思考用时 / 总 credit / 错误数）、每日 token 与 credit 趋势图、按模型分布、Top 10 会话（按 token）、每日错误数。
- 抽取器 `usage_extractor.py` 仅用 Python 标准库；生成的 HTML 单文件、Chart.js 内联、零外网依赖。
- 可搬运到其他 WorkBuddy 机器使用。
