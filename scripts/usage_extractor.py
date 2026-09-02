#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy 本地 usage-status 抽取器
读取 ~/.workbuddy 下的:
  - workbuddy.db  (sessions + session_usage: token预算/上下文上限/credit消耗)
  - traces/*/trace_*.json  (每次请求的时长/token拆分/思考用时/模型/工具调用/错误)
输出:
  - usage-status.json   原始聚合数据
  - usage-status.js     window.USAGE_STATUS = {...}  (供 HTML 直接 <script> 引入, 避开 file:// 的 fetch 跨域限制)
"""
import sqlite3, json, os, glob, datetime, sys, argparse

HOME = os.path.expanduser("~/.workbuddy")
DB = os.path.join(HOME, "workbuddy.db")
TRACES = os.path.join(HOME, "traces", "*", "trace_*.json")

# 脚本所在目录(模板与脚本一起搬运, 与 cwd 无关)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 数据完整性计数器：把被静默跳过的记录暴露出来，避免用户误以为报告完整
skipped_trace_files = []   # (路径, 错误) 损坏或无法解析的 trace 文件
bad_credit_sessions = 0    # credit_json 解析失败的会话数

# 输出目录: 默认当前工作目录, 可用 --out 覆盖
parser = argparse.ArgumentParser(description="WorkBuddy 本地 usage-status 抽取器")
parser.add_argument("--out", default=os.getcwd(),
                    help="输出目录 (默认: 当前工作目录)")
parser.add_argument("--home", default=HOME,
                    help="WorkBuddy 数据根目录 (默认: ~/.workbuddy)")
parser.add_argument("--credit-xlsx", default=None,
                    help="可选：用量明细 xlsx 路径（来自 workbuddy.cn 用量导出）。提供后，对应日期窗口内的每日 "
                         "credit 以服务端精确值覆盖本地估算；仅覆盖有数据的日期，其余日期仍为本地估算。"
                         "低调可选参数，不进默认流程，按需使用。")
args = parser.parse_args()
HOME = args.home
DB = os.path.join(HOME, "workbuddy.db")
TRACES = os.path.join(HOME, "traces", "*", "trace_*.json")
OUT_DIR = args.out
os.makedirs(OUT_DIR, exist_ok=True)
OUT_JSON = os.path.join(OUT_DIR, "usage-status.json")
OUT_JS = os.path.join(OUT_DIR, "usage-status.js")


def ms_to_sec(ms):
    return round(ms / 1000.0, 3) if ms else 0.0


def parse_ts(ts):
    # 支持 "2026-07-13T03:50:09.584Z" 与整数毫秒
    if isinstance(ts, (int, float)):
        return ts
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000
    except Exception:
        return None


def day_key(ts_ms):
    if not ts_ms:
        return "unknown"
    # 按本地时区归日（原为 utcfromtimestamp，临近午夜的请求会被算到前一天，已修正）
    return datetime.datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")


def read_credit_xlsx(path):
    """stdlib-only 最小 xlsx 读取器（不依赖 openpyxl），返回 {day: credit_sum}。
    期望列（表头文字，中/英均可）：RequestID / 积分消耗 / 时间（或 requestId / credit / time）。
    时间按 'YYYY-MM-DD HH:MM:SS' 解析为本地日期。返回空 dict 表示读取失败或无重叠必要列。
    """
    import zipfile
    import xml.etree.ElementTree as ET
    import re
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

    def col_letter(ref):
        m = re.match(r"([A-Z]+)", ref or "")
        return m.group(1) if m else None

    try:
        z = zipfile.ZipFile(path)
    except Exception as e:
        print("  xlsx 打开失败:", e, flush=True)
        return {}
    names = set(z.namelist())

    # 共享字符串表
    shared = []
    if "xl/sharedStrings.xml" in names:
        try:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.iter(ns + "si"):
                shared.append("".join(t.text or "" for t in si.iter(ns + "t")))
        except Exception:
            pass

    # 取第一个 worksheet
    sheet_path = "xl/worksheets/sheet1.xml"
    if sheet_path not in names:
        cands = [n for n in names if n.startswith("xl/worksheets/sheet")]
        if not cands:
            print("  xlsx 未找到 worksheet", flush=True)
            return {}
        sheet_path = sorted(cands)[0]
    try:
        root = ET.fromstring(z.read(sheet_path))
    except Exception as e:
        print("  xlsx 解析失败:", e, flush=True)
        return {}

    rows = []
    for row in root.iter(ns + "row"):
        cells = {}
        for c in row.iter(ns + "c"):
            ref = c.get("r")
            col = col_letter(ref)
            t = c.get("t")
            v = c.find(ns + "v")
            isn = c.find(ns + "is")
            val = None
            if t == "s" and v is not None:
                try:
                    val = shared[int(v.text)]
                except Exception:
                    val = None
            elif isn is not None:
                val = "".join(tt.text or "" for tt in isn.iter(ns + "t"))
            elif v is not None:
                val = v.text
            if col:
                cells[col] = val
        rows.append(cells)
    if not rows:
        return {}

    header = rows[0]

    def find_col(names_needed):
        for col, val in header.items():
            if val and any(nm.lower() in str(val).lower() for nm in names_needed):
                return col
        return None

    rid_c = find_col(["requestid", "RequestID"])
    cr_c = find_col(["积分消耗", "credit"])
    tm_c = find_col(["时间", "time"])
    if not (cr_c and tm_c):
        print("  xlsx 缺少必要列（积分消耗/credit、时间/time）", flush=True)
        return {}

    result = {}
    for cells in rows[1:]:
        tv = cells.get(tm_c)
        if not tv:
            continue
        try:
            day = datetime.datetime.strptime(str(tv), "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d")
        except Exception:
            day = str(tv)[:10]
            if len(day) != 10:
                continue
        cv = cells.get(cr_c)
        try:
            credit = float(cv)
        except Exception:
            continue
        result[day] = result.get(day, 0.0) + credit
    return result


# ---------- 1. 读取 DB: session 元信息 + 用量 ----------
print("[1/4] 读取 workbuddy.db ...", flush=True)
sess_meta = {}
sess_credit = {}
try:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = c.cursor()
    for r in cur.execute(
        "SELECT id,cwd,title,custom_title,status,created_at,updated_at,model,permission_mode,mode,project_id FROM sessions"
    ):
        (sid, cwd, title, ctitle, status, ca, ua, model, pm, mode, pid) = r
        sess_meta[sid] = {
            "title": ctitle or title or "",
            "status": status,
            "created_at": ca,
            "updated_at": ua,
            "model": model,
            "mode": mode,
        }
    for r in cur.execute("SELECT session_id,used,size,updated_at,credit_json FROM session_usage"):
        (sid, used, size, ua, cj) = r
        credit_total = 0.0
        if cj:
            try:
                credit_total = sum(float(v) for v in json.loads(cj).values())
            except Exception:
                bad_credit_sessions += 1
        sess_credit[sid] = {
            "used": used or 0,
            "size": size or 0,
            "credit": round(credit_total, 2),
        }
    c.close()
except Exception as e:
    print("  DB 读取失败(不影响 traces 部分):", e, flush=True)


# ---------- 2. 解析 traces ----------
print("[2/4] 解析 traces (可能较慢) ...", flush=True)
requests = []
by_day = {}
by_model = {}
by_session = {}
sess_first = {}
sess_min = {}

files = sorted(glob.glob(TRACES))
total = len(files)
for i, fp in enumerate(files):
    if i % 200 == 0:
        print(f"  进度 {i}/{total}", flush=True)
    try:
        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
    except Exception as e:
        skipped_trace_files.append((fp, str(e)[:80]))
        continue
    tr = data.get("trace", {})
    mi = tr.get("modelInfo", {}) or {}
    spans = data.get("spans", []) or []

    started = parse_ts(tr.get("startedAt") or tr.get("started_at"))
    ended = parse_ts(tr.get("endedAt") or tr.get("ended_at"))
    duration_ms = tr.get("duration") or (int(ended - started) if started and ended else 0)
    total_tokens = tr.get("totalTokens") or 0
    # 仅统计有实际 token 消耗的会话级运行；无 token 的工作流记账（零用量噪声）不计入用量基数
    if total_tokens <= 0:
        continue
    in_tok = mi.get("totalInputTokens") or 0
    out_tok = mi.get("totalOutputTokens") or 0
    cached_tok = mi.get("totalCachedTokens") or 0
    calls = mi.get("callCount") or 0
    models = mi.get("models") or []
    model_name = ",".join(models) if models else "unknown"
    session_id = tr.get("sessionId") or ""
    status = tr.get("status") or "ok"

    # 思考用时 = 所有 generation(模型推理) span 的时长之和
    thinking_ms = 0
    tool_ms = 0
    tool_count = 0
    err_count = 0
    for s in spans:
        st = s.get("status")
        if st == "error" or s.get("error"):
            err_count += 1
        t = s.get("type")
        d = s.get("duration") or 0
        if t == "generation":
            thinking_ms += d
        elif t in ("tool", "mcp", "function"):
            tool_ms += d
            tool_count += 1
    thinking_sec = ms_to_sec(thinking_ms)

    rec = {
        "id": tr.get("traceId"),
        "session_id": session_id,
        "started_at": started,
        "date": day_key(started),
        "duration_ms": duration_ms,
        "status": status,
        "tokens": total_tokens,
        "input": in_tok,
        "output": out_tok,
        "cached": cached_tok,
        "calls": calls,
        "model": model_name,
        "thinking_ms": thinking_ms,
        "thinking_sec": thinking_sec,
        "tool_ms": tool_ms,
        "tool_count": tool_count,
        "span_count": tr.get("spanCount") or len(spans),
        "errors": err_count,
    }
    requests.append(rec)

    # 日聚合
    dk = rec["date"]
    b = by_day.setdefault(
        dk,
        {"date": dk, "requests": 0, "tokens": 0, "input": 0, "output": 0,
         "cached": 0, "thinking_sec": 0.0, "credit": 0.0, "errors": 0, "sessions": set()},
    )
    b["requests"] += 1
    b["tokens"] += total_tokens
    b["input"] += in_tok
    b["output"] += out_tok
    b["cached"] += cached_tok
    b["thinking_sec"] += thinking_sec
    b["errors"] += err_count
    if session_id:
        b["sessions"].add(session_id)
        # 记录该会话首次出现日期, 用于后续 credit 单次归因(避免按请求重复累加)
        if session_id not in sess_min or (started and started < sess_min[session_id]):
            sess_min[session_id] = started
            sess_first[session_id] = dk

    # 模型聚合
    mb = by_model.setdefault(
        model_name,
        {"model": model_name, "requests": 0, "tokens": 0, "input": 0,
         "output": 0, "calls": 0, "thinking_sec": 0.0, "errors": 0},
    )
    mb["requests"] += 1
    mb["tokens"] += total_tokens
    mb["input"] += in_tok
    mb["output"] += out_tok
    mb["calls"] += calls
    mb["thinking_sec"] += thinking_sec
    mb["errors"] += err_count

    # 会话聚合
    sb = by_session.setdefault(
        session_id,
        {"session_id": session_id, "requests": 0, "tokens": 0, "thinking_sec": 0.0,
         "credit": 0.0, "errors": 0, "models": set()},
    )
    sb["requests"] += 1
    sb["tokens"] += total_tokens
    sb["thinking_sec"] += thinking_sec
    sb["errors"] += err_count
    sb["models"].add(model_name)


# ---------- 3. 收尾聚合 ----------
print("[3/4] 聚合指标 ...", flush=True)
days = sorted(by_day.keys())
day_list = []
for dk in days:
    b = by_day[dk]
    b["sessions"] = len(b["sessions"])
    b["thinking_sec"] = round(b["thinking_sec"], 1)
    b["credit"] = round(b["credit"], 2)
    day_list.append(b)

# ---- 3.5 可选：用用量导出 xlsx 的精确 credit 覆盖对应日期窗口 ----
# 注意：覆盖必须放在「会话级 credit 按 token 占比分摊」(sess_list 循环) 之后，
# 否则分摊逻辑会再次把本地 credit 累加到被覆盖的日期上，造成重复累加。
credit_source = "local_estimate"
credit_note = ("本地估算：会话级 credit 无逐日时间戳，默认归到会话「首次出现日」（不编造到后续免费/无消费日）；"
               "趋势形状近似、非精确值。提供用量导出 xlsx 可覆盖为精确值。")

model_list = []
for mb in by_model.values():
    eff = round(mb["output"] / mb["thinking_sec"], 1) if mb["thinking_sec"] else 0.0
    mb["efficiency_tok_per_sec"] = eff
    mb["thinking_sec"] = round(mb["thinking_sec"], 1)
    model_list.append(mb)
model_list.sort(key=lambda x: x["tokens"], reverse=True)

sess_list = []
for sb in by_session.values():
    meta = sess_meta.get(sb["session_id"], {})
    sb["title"] = meta.get("title", "")[:60]
    sb["status"] = meta.get("status", "")
    sb["models"] = ",".join(sorted(sb["models"]))
    sb["model"] = meta.get("model", "") or "unknown"
    sb["thinking_sec"] = round(sb["thinking_sec"], 1)
    # credit 每个会话只计一次(来自 session_usage 的会话级汇总)
    cr = sess_credit.get(sb["session_id"], {}).get("credit", 0)
    sb["credit"] = round(cr, 2)
    # 本地无逐日 credit 时间戳，无法精确拆分到天。
    # 默认把整个会话的 credit 归因到它「首次出现」的那一天(归首日)：
    # 这是本地能做的「最不坏」方案——credit 绑在会话起点，不会把 credit 编造到
    # 后续免费/无消费的日子里(例如用免费 HY3 续跑的旧会话)。
    # 精确每日 credit 只能由 --credit-xlsx 覆盖给出。
    sid = sb["session_id"]
    fd = sess_first.get(sid)
    sb["first_date"] = fd or ""
    if fd and fd in by_day:
        by_day[fd]["credit"] += cr
    sess_list.append(sb)
sess_list.sort(key=lambda x: x["tokens"], reverse=True)

# ---- 3.5 可选：用用量导出 xlsx 的精确 credit 覆盖对应日期窗口 ----
# 必须放在 sess_list 循环之后（见上方说明），避免分摊逻辑重复累加本地 credit。
# xlsx_date_min/max：xlsx 实际覆盖的日期窗口，供前端把默认展示范围收敛到该窗口
# （不锁死筛选器，用户仍可拉回看全量 token 历史）。
xlsx_date_min = None
xlsx_date_max = None
if args.credit_xlsx:
    print("[3.5] 读取用量导出 xlsx (--credit-xlsx) ...", flush=True)
    xmap = read_credit_xlsx(args.credit_xlsx)
    if xmap:
        covered = 0
        for b in day_list:
            if b["date"] in xmap:
                b["credit"] = round(xmap[b["date"]], 2)
                covered += 1
        if covered:
            credit_source = "xlsx_precise"
            credit_note = (f"credit 已用用量导出 xlsx 精确覆盖 {covered} 天（窗口内为服务端精确值）；"
                           f"未覆盖日期仍为本地估算。xlsx 最多含 1 个月，历史长期趋势仍看 token。")
            xlsx_dates = sorted(xmap.keys())
            xlsx_date_min = xlsx_dates[0]
            xlsx_date_max = xlsx_dates[-1]
            print(f"  xlsx 覆盖 {covered} 天，credit 已更新为精确值；日期窗口 {xlsx_date_min}~{xlsx_date_max}。", flush=True)
        else:
            credit_note = "提供的 xlsx 未包含与本地数据重叠的日期，credit 仍为本地估算。"
            print("  xlsx 与本地数据无日期重叠，credit 维持本地估算。", flush=True)
    else:
        print("  xlsx 读取失败或未识别到必要列，credit 维持本地估算。", flush=True)

# ---------- 2.6 模型性价比 (会话级 model 聚合, 关联 sessions.model) ----------
print("[2.6] 模型性价比 ...", flush=True)
model_cost = {}
for sb in sess_list:
    m = sb.get("model") or "unknown"
    mc = model_cost.setdefault(
        m,
        {"model": m, "sessions": 0, "tokens": 0, "credit": 0.0},
    )
    mc["sessions"] += 1
    mc["tokens"] += sb["tokens"]
    mc["credit"] += sb["credit"]
model_cost_list = []
for mc in model_cost.values():
    if mc["model"] == "unknown" or mc["tokens"] <= 0:
        continue
    c1k = (mc["credit"] / mc["tokens"] * 100000.0) if mc["tokens"] else 0.0
    mc["credit_per_100k"] = round(c1k, 2)
    # 标记限时免费/促销模型：credit 为 0 但 token 不少（可能 WorkBuddy 内部倍率为 0）
    mc["zero_credit"] = (mc["credit"] <= 0.0 and mc["tokens"] >= 1_000_000)
    model_cost_list.append(mc)
model_cost_list.sort(key=lambda x: x["credit_per_100k"])

# 优化建议: 在可比任务量(≥1000万token)的通用模型间, 比较最便宜与最贵
model_tips = []
substantial = [
    m for m in model_cost_list
    if m["model"] not in ("auto", "unknown")
    and "preview" not in m["model"]
    and "agent" not in m["model"]
    and m["tokens"] >= 10_000_000
    and m["credit"] > 0.0          # 排除限时免费/促销模型，避免把“当前零成本”当成长期基准
]
if len(substantial) >= 2:
    cheapest = min(substantial, key=lambda x: x["credit_per_100k"])
    priciest = max(substantial, key=lambda x: x["credit_per_100k"])
    if priciest["credit_per_100k"] > 0:
        save = (priciest["credit_per_100k"] - cheapest["credit_per_100k"]) / priciest["credit_per_100k"] * 100
        if save >= 5:
            model_tips.append(
                f"在可比任务量下(均≥1000万token)，切换至「{cheapest['model']}」"
                f"(credit/10万token={cheapest['credit_per_100k']}) 相比「{priciest['model']}」"
                f"(credit/10万token={priciest['credit_per_100k']}) 预计节省约 {save:.0f}% 的 credit；"
                f"前提是两个模型处理的工作负载可互相迁移。")
# 单独提示零 credit 模型
for m in model_cost_list:
    if m["zero_credit"] and m["tokens"] >= 10_000_000:
        model_tips.append(
            f"「{m['model']}」当前 credit/10万token=0（消耗 credit {m['credit']:.2f}），"
            f"可能处于限免/促销期；不建议把它作为长期成本基准。")

# ---------- 2.7 用量高峰探查 (非异常判定, 仅定位高用量日并拆解成因) ----------
# 用户原话: 不需要"正常/异常"二分, 但要能自动找出几个明显高的使用日,
# 并像案例分析那样拆解"那天发生了什么"(主导会话/模型构成/错误率/最大单请求)。
# 精确到天(而非模糊窗口), 因为图里看不出哪天用得最多。
print("[2.7] 用量高峰探查 ...", flush=True)
daily_credit = {b["date"]: b["credit"] for b in day_list if b["date"] != "unknown"}
# 排序改为按当天 token 总量（请求级精确到天）；credit 归首日估算不精确，不作排序依据。
daily_tokens = {}
for r in requests:
    if r["date"] != "unknown":
        daily_tokens[r["date"]] = daily_tokens.get(r["date"], 0) + r["tokens"]
tok_vals = sorted(daily_tokens.values())
median_tok = tok_vals[len(tok_vals) // 2] if tok_vals else 0
thr = max(median_tok * 2.0, 5_000_000)     # 高于中位数 2 倍才算"明显高"，兜底 500 万 token
cand = sorted([(d, v) for d, v in daily_tokens.items() if v >= thr],
              key=lambda x: x[1], reverse=True)
top_days = [d for d, _ in cand[:6]]
if len(top_days) < 3:                      # 兜底: 样本不足时也至少给 top3
    top_days = [d for d, _ in sorted(daily_tokens.items(), key=lambda x: x[1], reverse=True)[:3]]

spike_days = []
for d in top_days:
    day_reqs = [r for r in requests if r["date"] == d]
    # 当天有请求的全部会话（不再限首日）：model=当天实际请求模型（按 token 取主要），token=当天请求 token 之和。
    # 会话表的 token 口径与「模型 token 构成」一致（都是当天全部请求），左右可对账。
    sess_tok = {}
    sess_mdl = {}
    for r in day_reqs:
        sid = r["session_id"]
        if not sid:
            continue
        sess_tok[sid] = sess_tok.get(sid, 0) + r["tokens"]
        sess_mdl.setdefault(sid, {})
        sess_mdl[sid][r["model"]] = sess_mdl[sid].get(r["model"], 0) + r["tokens"]
    day_sess = []
    for sid, tok in sess_tok.items():
        meta = sess_meta.get(sid, {})
        mdl_order = sorted(sess_mdl.get(sid, {}).items(), key=lambda kv: -kv[1])
        model_str = ", ".join(m for m, _ in mdl_order) if mdl_order else "unknown"
        day_sess.append({
            "session_id": sid[:12],
            "title": (meta.get("title", "") or "")[:40],
            "model": model_str,           # 当天实际请求的全部模型（按 token 降序），与右侧构成对齐
            "tokens": tok,
        })
    day_sess.sort(key=lambda x: x["tokens"], reverse=True)
    shown_sessions = day_sess[:8]
    n_hidden = max(0, len(day_sess) - 8)
    hidden_tokens = sum(x["tokens"] for x in day_sess[8:])
    mdl = {}
    for r in day_reqs:
        mdl[r["model"]] = mdl.get(r["model"], 0) + r["tokens"]
    mdl_sorted = sorted(mdl.items(), key=lambda x: x[1], reverse=True)[:5]
    n = len(day_reqs)
    errs = sum(r["errors"] for r in day_reqs)
    calls = sum(r["calls"] for r in day_reqs)
    max_tok = max((r["tokens"] for r in day_reqs), default=0)
    spike_days.append({
        "date": d,
        "tokens": sum(r["tokens"] for r in day_reqs),
        "credit": round(daily_credit.get(d, 0.0), 2),   # 归首日估算，仅作参考
        "requests": n,
        "sessions": len(sess_tok),
        "errors": errs,
        "err_rate": round(errs / n * 100, 1) if n else 0.0,
        "avg_calls": round(calls / n, 1) if n else 0.0,
        "max_request_tokens": max_tok,
        "model_token_top": [{"model": m, "tokens": t} for m, t in mdl_sorted],
        "top_sessions": shown_sessions,
        "n_hidden": n_hidden,
        "hidden_tokens": hidden_tokens,
    })

total_tokens = sum(r["tokens"] for r in requests)
total_input = sum(r["input"] for r in requests)
total_output = sum(r["output"] for r in requests)
total_cached = sum(r["cached"] for r in requests)
total_thinking = round(sum(r["thinking_sec"] for r in requests), 1)
total_credit = round(sum(b["credit"] for b in day_list), 2)
total_errors = sum(r["errors"] for r in requests)
dates = [r["date"] for r in requests if r["date"] != "unknown"]

summary = {
    "version": "1.2.6",
    "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    "credit_source": credit_source,
    "credit_note": credit_note,
    "total_requests": len(requests),
    "total_sessions": len(sess_list),
    "total_tokens": total_tokens,
    "total_input": total_input,
    "total_output": total_output,
    "total_cached": total_cached,
    "total_thinking_sec": total_thinking,
    "total_thinking_hours": round(total_thinking / 3600.0, 2),
    "avg_thinking_sec_per_request": round(total_thinking / len(requests), 1) if requests else 0,
    "total_credit": total_credit,
    "total_errors": total_errors,
    "avg_efficiency_tok_per_sec": round(total_output / total_thinking, 1) if total_thinking else 0,
    "date_min": min(dates) if dates else None,
    "date_max": max(dates) if dates else None,
    "credit_xlsx_date_min": xlsx_date_min,
    "credit_xlsx_date_max": xlsx_date_max,
    "model_count": len(model_list),
}

# 用于前端日期筛选的轻量全量请求快照（只保留必要字段，控制体积）
requests_slim = [
    {"date": r["date"], "tokens": r["tokens"], "output": r["output"],
     "thinking_sec": r["thinking_sec"], "model": r["model"], "errors": r["errors"]}
    for r in requests
]

# 只保留 top 300 请求用于散点图, 控制文件体积
requests_trim = sorted(requests, key=lambda x: x["tokens"], reverse=True)[:300]
for r in requests_trim:
    r["started_at"] = int(r["started_at"]) if r["started_at"] else None

out = {
    "summary": summary,
    "by_day": day_list,
    "by_model": model_list,
    "by_session": sess_list[:200],
    "requests_sample": requests_trim,
    "requests_slim": requests_slim,
    "by_model_cost": model_cost_list,
    "model_tips": model_tips,
    "spike_days": spike_days,
}

# 数据完整性提示：把被静默跳过的记录暴露出来，避免用户误以为报告完整
warnings = []
if skipped_trace_files:
    names = "；".join(os.path.basename(x[0]) for x in skipped_trace_files[:5])
    more = " 等" if len(skipped_trace_files) > 5 else ""
    warnings.append({
        "type": "skipped_traces",
        "count": len(skipped_trace_files),
        "detail": f"已跳过 {len(skipped_trace_files)} 个损坏/无法解析的 trace 文件（报告可能不完整）：{names}{more}",
    })
if bad_credit_sessions:
    warnings.append({
        "type": "bad_credit",
        "count": bad_credit_sessions,
        "detail": f"{bad_credit_sessions} 个会话的 credit_json 解析失败，相关会话 credit 计为 0（不影响 token 与每日趋势）。",
    })
out["warnings"] = warnings


# ---------- 4. 写出 ----------
print("[4/4] 写出 usage-status.json / usage-status.js ...", flush=True)
with open(OUT_JSON, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
with open(OUT_JS, "w", encoding="utf-8") as f:
    f.write("window.USAGE_STATUS = ")
    json.dump(out, f, ensure_ascii=False)
    f.write(";")

# 自包含 HTML: 把数据 + Chart.js 都内联进模板, 去掉所有外部依赖(预览/双击均可离线打开)
TPL = os.path.join(SCRIPT_DIR, "dashboard_template.html")
CHART_JS = os.path.join(SCRIPT_DIR, "chart.umd.min.js")
# 自包含 HTML 文件名带时间戳：每次生成独立文件，不覆盖旧报告，便于保留多份对比。
OUT_HTML = os.path.join(OUT_DIR, "workbuddy-usage-status-dashboard-"
                        + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".html")
try:
    tpl = open(TPL, "r", encoding="utf-8").read()

    # 内联 Chart.js: 强依赖随包携带的 chart.umd.min.js（发布版必带，不回退 CDN，避免无网环境双击空白）
    chart_tag = ""
    if os.path.exists(CHART_JS):
        chart_src = open(CHART_JS, "r", encoding="utf-8").read()
        chart_tag = "<script>" + chart_src + "</script>"
    else:
        sys.exit("错误：缺少随包文件 chart.umd.min.js，无法生成离线 HTML。\n"
                 "请确认该文件与 usage_extractor.py 同在 scripts/ 目录下。")
    if "<!--CHART_JS-->" in tpl:
        tpl = tpl.replace("<!--CHART_JS-->", chart_tag)

    # 转义 "<" 为 "\u003c"（OWASP 推荐的内联 JSON 做法）：彻底防止会话标题/模型名中的
    # "</script>" 或 "</SCRIPT>"（HTML 标签名大小写不敏感）冲破 script 边界（本地存储型 XSS 防护）。
    # "\u003c" 在 JS 字符串中仍解析为 "<"，数据值不变。
    inline = '<script>window.USAGE_STATUS = ' + json.dumps(out, ensure_ascii=False).replace("<", "\\u003c") + ';</script>'
    if "<!--USAGE_DATA-->" in tpl:
        html = tpl.replace("<!--USAGE_DATA-->", inline)
    else:
        html = tpl.replace("</head>", inline + "</head>", 1)
    open(OUT_HTML, "w", encoding="utf-8").write(html)
    offline = "离线内联"
    print(f"已生成自包含 HTML({offline}):", OUT_HTML)
except Exception as e:
    print("HTML 内联跳过:", e)

print("\n=== 完成 ===")
print(f"请求数: {summary['total_requests']}  会话数: {summary['total_sessions']}")
print(f"总 token: {summary['total_tokens']:,}  总思考用时: {summary['total_thinking_hours']} h")
print(f"总 credit: {summary['total_credit']}  错误: {summary['total_errors']}")
print(f"日期范围: {summary['date_min']} ~ {summary['date_max']}")
if warnings:
    print("\n⚠ 数据完整性提示：")
    for w in warnings:
        print(f"  - {w['detail']}")
print(f"输出: {OUT_JSON}")
print(f"输出: {OUT_JS}")
