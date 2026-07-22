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

# 输出目录: 默认当前工作目录, 可用 --out 覆盖
parser = argparse.ArgumentParser(description="WorkBuddy 本地 usage-status 抽取器")
parser.add_argument("--out", default=os.getcwd(),
                    help="输出目录 (默认: 当前工作目录)")
parser.add_argument("--home", default=HOME,
                    help="WorkBuddy 数据根目录 (默认: ~/.workbuddy)")
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
    return datetime.datetime.utcfromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")


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
                pass
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
    except Exception:
        continue
    tr = data.get("trace", {})
    mi = tr.get("modelInfo", {}) or {}
    spans = data.get("spans", []) or []

    started = parse_ts(tr.get("startedAt") or tr.get("started_at"))
    ended = parse_ts(tr.get("endedAt") or tr.get("ended_at"))
    duration_ms = tr.get("duration") or (int(ended - started) if started and ended else 0)
    total_tokens = tr.get("totalTokens") or 0
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
    sb["thinking_sec"] = round(sb["thinking_sec"], 1)
    # credit 每个会话只计一次(来自 session_usage 的会话级汇总)
    cr = sess_credit.get(sb["session_id"], {}).get("credit", 0)
    sb["credit"] = round(cr, 2)
    # 把会话 credit 归因到其首次出现的那一天(避免跨天重复)
    fd = sess_first.get(sb["session_id"])
    if fd and fd in by_day:
        by_day[fd]["credit"] += cr
    sess_list.append(sb)
sess_list.sort(key=lambda x: x["tokens"], reverse=True)

total_tokens = sum(r["tokens"] for r in requests)
total_input = sum(r["input"] for r in requests)
total_output = sum(r["output"] for r in requests)
total_cached = sum(r["cached"] for r in requests)
total_thinking = round(sum(r["thinking_sec"] for r in requests), 1)
total_credit = round(sum(sess_credit[s]["credit"] for s in sess_credit), 2)
total_errors = sum(r["errors"] for r in requests)
dates = [r["date"] for r in requests if r["date"] != "unknown"]

summary = {
    "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
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
    "model_count": len(model_list),
    "note": "credit 按会话汇总(模型哈希无法反解为名称);思考用时=generation span 时长之和,为模型推理/思考的代理指标。",
}

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
}


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
OUT_HTML = os.path.join(OUT_DIR, "workbuddy-usage-status-dashboard.html")
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

    inline = '<script>window.USAGE_STATUS = ' + json.dumps(out, ensure_ascii=False) + ';</script>'
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
print(f"输出: {OUT_JSON}")
print(f"输出: {OUT_JS}")
