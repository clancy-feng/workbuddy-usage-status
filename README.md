> **Skill Overview** 
> 
> **WorkBuddy Usage Status** turns WorkBuddy's own local usage data into an offline, auditable dashboard — token spend, thinking time, thinking efficiency, model distribution, error count, and credit consumption. All data stays on your machine under `~/.workbuddy/`; no network, no external APIs. The generated dashboard is a single self-contained HTML file (Chart.js inlined), so it renders anywhere with zero dependencies.
> 
> **How to invoke**
> 
> - **Chat trigger:** say any of these in a WorkBuddy conversation and the skill is auto-detected: "workbuddy usage stats" / "telemetry" / "usage" / "token consumption" / "thinking efficiency" / "model cost" / "cost control" / "how much has workbuddy used".
> - **CLI:** `python3 scripts/usage_extractor.py` (options: `--out ./report`, `--home /other/.workbuddy`). Python 3.10+, standard library only.
> 
> The full Chinese documentation is preserved below.

---

#WorkBuddy Usage Status

把 WorkBuddy 自己的本地使用数据，变成一份**离线、可监督**的 Dashboard。
看的是：token 消耗、思考用时、思考效率、模型分布、错误数、credit 消耗。

> 数据全在本机 `~/.workbuddy/`，不联网、不调外部 API。
> 生成的 Dashboard 是**单文件、零外网依赖**（Chart.js 已内联），预览面板/双击均可直接出图。

---

## 1. 你能看到什么

- WorkBuddy 一共花了多少 token / credit？思考了多久？
- 哪个会话、哪个模型最费？效率最低的是谁？
- 哪天用量飙升？错误集中在哪些会话/模型？
- 用作“使用监督 / 用量控制”的量化依据。

---

## 2. 装到另一台机器（搬运）

整个 `workbuddy-usage-status/` 文件夹复制到目标机器的用户级 skill 目录 ~/.workbuddy/skills/

~/.workbuddy/skills/workbuddy-usage-status/

├── SKILL.md

├── README.md

└── scripts/

├── usage_extractor.py      # 抽取+聚合+内联注入（仅标准库）

├── dashboard_template.html     # 看板模板，被抽取器注入数据与 Chart.js

└── chart.umd.min.js            # 随包已含的 Chart.js（离线内联用，发布版必带）

重启 WorkBuddy 后，skill 会被自动识别。

---

## 3. 怎么用（两种入口）

装好 skill（见第 2 节）并重启 WorkBuddy 后，有两种用法。

### 入口 A：对话触发（推荐，最省事）

在 WorkBuddy 对话里直接说下面任意一句，skill 会被自动识别并引导生成看板：

- “workbuddy 使用统计” / “telemetry” / “usage”
- “token 消耗看板” / “思考效率” / “模型成本”
- “使用监督” / “用量控制” / “workbuddy 自己用了多少”
- “会话耗时” / “workbuddy /cost” / “用了多少额度” / “烧了多少钱”

它本质上就是帮你跑下面「入口 B」那条命令，并告诉你生成的 HTML 在哪。

### 入口 B：命令行直接跑

在任意目录执行（Python 3.10+，仅标准库）：
bash

生成到当前目录（默认）
python3 scripts/usage_extractor.py

生成到指定目录
python3 scripts/usage_extractor.py --out ./report

指定数据根（一般不用，默认 ~/.workbuddy）
python3 scripts/usage_extractor.py --home /other/.workbuddy

### 看结果

脚本在「输出目录」（即你运行命令时所在目录，或 `--out` 指定的目录）生成 3 个文件：

| 文件                                      | 说明                                      |
| --------------------------------------- | --------------------------------------- |
| `workbuddy-usage-status-dashboard.html` | **自包含单文件**，数据 + Chart.js 均已内联，双击即看，无需联网 |
| `usage-status.json`                     | 聚合后的原始数据，可二次处理                          |
| `usage-status.js`                       | `window.USAGE_STATUS = {...}`，备用        |

打开 `workbuddy-usage-status-dashboard.html` 即可看到：KPI 卡 + **每日积分消耗（credit）图** + 每日思考用时图 + 各模型 Token 占比 + 模型效率 + 效率散点 + **Top 10 会话表**（按 token 消耗取前 10）+ 每日错误。

---

## 4. 刷新数据

数据是**快照**。要更新就再跑一次脚本，重新打开 HTML：

python3 scripts/usage_extractor.py --out ./report

（想每天自动刷新，可用 WorkBuddy 的“自动化/定时任务”每天跑这条命令。）

---

## 5. 指标是怎么算的

| 指标        | 算法                                                                           | 数据来源                        |
| --------- | ---------------------------------------------------------------------------- | --------------------------- |
| 思考用时      | 每条 trace 里 `type=generation` 的 span 时长之和                                     | `traces/*/trace_*.json`     |
| 思考效率      | 输出 token ÷ 思考秒数（tok/s）                                                       | `traces/*/trace_*.json`     |
| token 消耗  | `totalTokens`（输入+输出+缓存）按会话/模型/天聚合                                            | `traces/*/trace_*.json`     |
| credit 消耗 | `session_usage.credit_json` 会话级汇总；已做**「每日积分消耗（credit）」折线图**（替代原每日 Token 趋势图） | `workbuddy.db`              |
| Top 会话    | 按 token 消耗降序取 **前 10** 个会话，列出标题/token/思考时长/credit/错误数                        | `traces/*` + `workbuddy.db` |

---

## 6. 已知限制

1. **credit 只能到会话级**：`credit_json` 的 key 是模型哈希，没有哈希→名称映射，无法拆到模型名；token 可以按真实模型名拆分。**每日积分图是近似分布**：抽取器把会话 credit 归因到该会话「首次出现」的那一天，所以大会话的全部积分会集中算在首日，后续天不体现。这是当前数据源能给出的极限粒度。
2. **快照式**：手动/定时跑。要实时监督需包成常驻服务（直接查 DB+traces）。
3. **已彻底离线**：Chart.js 随包内联进生成的 HTML，零外网依赖，预览面板/双击均可正常出图。`chart.umd.min.js` 为发布版必带文件，**抽取器强依赖它**；若该文件缺失，脚本直接报错退出（不会生成空白 HTML），请确认它与 `usage_extractor.py` 同在 `scripts/` 下。
4. **首跑耗时**：全量解析 traces（可能上千文件、上 GB）约 10–30 秒，一次性。

---

## 7. 故障排查

| 现象                | 原因 / 处理                                                                                           |
| ----------------- | ------------------------------------------------------------------------------------------------- |
| 打开 HTML 显示“数据未加载” | 没先跑脚本；或脚本报错中断。重跑 `usage_extractor.py` 看 stderr                                                    |
| 图表空白但数字在          | 极少见：若报错"缺少 chart.umd.min.js"，确认该文件与 usage_extractor.py 同在 scripts/ 下后重跑；正常情况 Chart.js 已随包内联，零外网依赖 |
| 数据库被占用            | 抽取器以只读模式（`mode=ro`）打开，正常不影响正在运行的 WorkBuddy                                                        |
| 数据明显偏少            | 这台机器 traces 少/刚装；或 `--home` 指错了目录                                                                 |
