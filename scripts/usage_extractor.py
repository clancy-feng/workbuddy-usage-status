

"""
WorkBuddy 本地 usage-status 抽取器
（面向中文 WorkBuddy 用户 zh-CN 设计；英文能力说明见 SKILL.md 的 description EN 段）

读取（全部只读 mode=ro）~/.workbuddy 下：
  - workbuddy.db  (sessions + session_usage: token预算/上下文上限/credit消耗)
  - traces/*/trace_*.json  (每次请求的时长/token拆分/思考用时/模型/工具调用/错误)
  - projects/*/*.jsonl  (仅按 sessionId 提取 <user_query> 提问摘要，供看板下钻的提问列)

写入：
  - 输出目录：usage-status.json（原始聚合数据）、usage-status.js（window.USAGE_STATUS = {...}，
    供 HTML 直接 <script> 引入以避开 file:// 的 fetch 跨域限制）、dashboard HTML、chart.umd.min.js、
    usage-full-<时间戳>.csv
  - ~/.workbuddy/usage-archive/：逐请求归档 + 会话汇总 + 每日总量覆盖层（traceId 去重；--no-archive 关闭）

网络：
  - 默认零外部请求；仅当显式传入 --billing-token-file 时向官方用量 API 发起一次 HTTPS 请求
"""
import sqlite3, json, os, glob, datetime, sys, argparse, shutil, re
import urllib.request, ssl
from collections import Counter, defaultdict

HOME = os.path.expanduser("~/.workbuddy")
DB = os.path.join(HOME, "workbuddy.db")
TRACES = os.path.join(HOME, "traces", "*", "trace_*.json")


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


skipped_trace_files = []   
bad_credit_sessions = 0    



err_msg_counter = Counter()       
err_type_counter = Counter()       
err_tool_counter = Counter()       
err_by_model = {}                 
day_model_tokens = {}             
err_by_session = {}               
err_by_day = {}                   
error_samples = []                


parser = argparse.ArgumentParser(description="WorkBuddy 本地 usage-status 抽取器")
parser.add_argument("--seed", default=None,
                    help="旧快照种子（usage-status.json 或历史 dashboard HTML）：恢复已被清理日期的每日总量")
parser.add_argument("--no-archive", action="store_true",
                    help="禁用本地归档合并（默认开启：自动累积历史，对抗 WorkBuddy 30 天 trace 清理）")
parser.add_argument("--out", default=os.getcwd(),
                    help="输出目录 (默认: 当前工作目录)")
parser.add_argument("--home", default=HOME,
                    help="WorkBuddy 数据根目录 (默认: ~/.workbuddy)")
parser.add_argument("--credit-xlsx", default=None,
                    help="可选：用量明细 xlsx 路径（来自 workbuddy.cn 用量导出）。提供后，对应日期窗口内的每日 "
                         "credit 以服务端精确值覆盖本地估算；仅覆盖有数据的日期，其余日期仍为本地估算。"
                         "低调可选参数，不进默认流程，按需使用。")
parser.add_argument("--billing-token-file", default=None,
                    help="可选：用户手动从浏览器导出的用量 API 鉴权头文件（如 DevTools 复制的 `Cookie: ...` 整行，"
                         "或 `Authorization: Bearer ...`）。提供后，skill 以该 token 调用官方用量 API 拉取精确 credit"
                         "（opt-in，绝不自动读取宿主 App 凭据）。与 --credit-xlsx 同时提供时，API 优先。")
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
    
    if isinstance(ts, (int, float)):
        return ts
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000
    except Exception:
        return None


def day_key(ts_ms):
    if not ts_ms:
        return "unknown"
    
    return datetime.datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")


def _read_xlsx_rows(path):
    """stdlib-only 最小 xlsx 读取器（不依赖 openpyxl）。
    返回 (rows, header)；rows 为 [{列字母: 值}, ...]，header 即 rows[0]。
    失败时返回 (None, None)。
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
        return None, None
    names = set(z.namelist())

    
    shared = []
    if "xl/sharedStrings.xml" in names:
        try:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.iter(ns + "si"):
                shared.append("".join(t.text or "" for t in si.iter(ns + "t")))
        except Exception:
            pass

    
    sheet_path = "xl/worksheets/sheet1.xml"
    if sheet_path not in names:
        cands = [n for n in names if n.startswith("xl/worksheets/sheet")]
        if not cands:
            print("  xlsx 未找到 worksheet", flush=True)
            return None, None
        sheet_path = sorted(cands)[0]
    try:
        root = ET.fromstring(z.read(sheet_path))
    except Exception as e:
        print("  xlsx 解析失败:", e, flush=True)
        return None, None

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
        return None, None
    return rows, rows[0]


def find_xlsx_col(header, names_needed):
    """在表头里按关键字找列字母（中英文均可）。"""
    for col, val in header.items():
        if val and any(nm.lower() in str(val).lower() for nm in names_needed):
            return col
    return None


def read_credit_xlsx(path):
    """读取用量导出 xlsx，返回 {day: credit_sum}。
    期望列（表头文字，中/英均可）：RequestID / 积分消耗 / 时间（或 requestId / credit / time）。
    时间按 'YYYY-MM-DD HH:MM:SS' 解析为本地日期。返回空 dict 表示读取失败或无重叠必要列。
    """
    rows, header = _read_xlsx_rows(path)
    if rows is None:
        return {}

    rid_c = find_xlsx_col(header, ["requestid", "RequestID"])
    cr_c = find_xlsx_col(header, ["积分消耗", "credit"])
    tm_c = find_xlsx_col(header, ["时间", "time"])
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


def read_credit_xlsx_by_model(path):
    """读取官方用量导出 xlsx，按【模型】汇总服务端精确 credit。

    官方导出列（页面表头顺序）：时间 / 积分消耗 / 模型 / 客户端 / Request
    （"包含输入提示词"为可选项，默认不含；本函数不读取提示词内容）。

    返回 (by_model, by_model_cnt)；
      by_model     = {model: credit}   服务端精确值
      by_model_cnt = {model: 请求数}
    无「模型」列或读取失败时返回 ({}, {})。
    """
    rows, header = _read_xlsx_rows(path)
    if rows is None:
        return {}, {}
    cr_c = find_xlsx_col(header, ["积分消耗", "credit"])
    md_c = find_xlsx_col(header, ["模型", "model"])
    if not cr_c:
        print("  xlsx 缺少 credit 列（积分消耗/credit），无法按模型汇总。", flush=True)
        return {}, {}
    if not md_c:
        print("  xlsx 无「模型」列，无法按模型汇总官方 credit（每日 credit 不受影响）。", flush=True)
        return {}, {}

    by_model = {}
    by_model_cnt = {}
    for cells in rows[1:]:
        cv = cells.get(cr_c)
        if cv in (None, ""):
            continue
        try:
            credit = float(str(cv).replace(",", ""))
        except Exception:
            continue
        mv = (cells.get(md_c) or "").strip() or "(未知模型)"
        by_model[mv] = by_model.get(mv, 0.0) + credit
        by_model_cnt[mv] = by_model_cnt.get(mv, 0) + 1
    return by_model, by_model_cnt






BILLING_API_URL = "https://www.workbuddy.cn/billing/meter/get-user-request-usage"

def aggregate_billing_rows(rows):
    """把官方 API 返回 data.data[] 聚合为 (day_credit, by_model, by_model_cnt, date_min, date_max)。
    data.data[] 每项：{requestId, credit, model, client, requestTime, ...}
    requestTime 形如 'YYYY-MM-DD HH:MM:SS'，按前 10 位归日。
    """
    day_map = {}
    by_model = {}
    by_model_cnt = {}
    dates = []
    for it in rows:
        rt = it.get("requestTime") or ""
        day = rt[:10]
        if len(day) != 10:
            continue
        try:
            credit = float(it.get("credit") or 0)
        except Exception:
            credit = 0.0
        day_map[day] = day_map.get(day, 0.0) + credit
        m = (it.get("model") or "").strip() or "(未知模型)"
        by_model[m] = by_model.get(m, 0.0) + credit
        by_model_cnt[m] = by_model_cnt.get(m, 0) + 1
        dates.append(day)
    dmin = min(dates) if dates else None
    dmax = max(dates) if dates else None
    return day_map, by_model, by_model_cnt, dmin, dmax

def fetch_billing_usage(token_file, start, end):
    """调用官方用量 API（opt-in）。返回 aggregate_billing_rows 的元组；失败抛异常由调用方回退。
    token_file 内容：用户从浏览器 DevTools 复制的鉴权头（如 `Cookie: xxx` 整行，
    或 `Authorization: Bearer xxx`）。首行 `Key: Value` 解析为请求头；无冒号则当作 Cookie 值。
    """
    raw = open(token_file, encoding="utf-8").read().strip()
    if not raw:
        raise ValueError("token 文件为空")
    
    header_key, header_val = "Cookie", raw
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            header_key, header_val = k.strip(), v.strip()
        break
    body = json.dumps({
        "startTime": f"{start} 00:00:00",
        "endTime": f"{end} 23:59:59",
        "pageNum": 1,
        "pageSize": 3000,
    }).encode("utf-8")
    req = urllib.request.Request(
        BILLING_API_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", header_key: header_val},
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("code") != 0:
        raise RuntimeError("用量 API 返回错误: %r" % (payload.get("msg"),))
    return aggregate_billing_rows(payload.get("data", {}).get("data", []))



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

    
    thinking_ms = 0
    tool_ms = 0
    tool_count = 0
    err_count = 0
    gen_count = 0
    gen_ms = 0
    tool_items = []
    err_items = []
    dk = day_key(started)   
    
    
    day_model_tokens[(dk, model_name)] = day_model_tokens.get((dk, model_name), 0) + total_tokens
    for s in spans:
        st = s.get("status")
        em = s.get("error")
        if st == "error" or em:
            err_count += 1
            
            emsg = em if isinstance(em, str) else (json.dumps(em, ensure_ascii=False) if em else (st or "error"))
            if len(emsg) > 300:
                emsg = emsg[:300]
            etype = s.get("type") or ""
            etool = s.get("toolName") or ""
            err_msg_counter[emsg] += 1
            err_type_counter[etype or "unknown"] += 1
            if etool:
                err_tool_counter[etool] += 1
            err_by_model.setdefault(model_name, Counter())[emsg] += 1
            if session_id:
                err_by_session.setdefault(session_id, Counter())[emsg] += 1
            err_by_day.setdefault(dk, Counter())[emsg] += 1
            if len(err_items) < 20:
                err_items.append([str(etype)[:30], str(etool)[:30], str(emsg)[:120]])
        t = s.get("type")
        d = s.get("duration") or 0
        if t == "generation":
            thinking_ms += d
            gen_count += 1
            gen_ms += d
        elif t in ("tool", "mcp", "function"):
            tool_ms += d
            tool_count += 1
            if len(tool_items) < 20:
                tool_items.append([str(s.get("toolName") or s.get("name") or t)[:40], int(d)])
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
        "gen_n": gen_count,
        "gen_ms": gen_ms,
        "tl": tool_items,
        "el": err_items,
    }
    requests.append(rec)

    
    if err_count and len(error_samples) < 50:
        err_examples = [s for s in spans if s.get("status") == "error" or s.get("error")]
        if err_examples:
            e0 = err_examples[0]
            e0msg = e0.get("error")
            e0msg = e0msg if isinstance(e0msg, str) else (json.dumps(e0msg, ensure_ascii=False) if e0msg else (e0.get("status") or "error"))
            if len(e0msg) > 300:
                e0msg = e0msg[:300]
            error_samples.append({
                "date": dk,
                "session_id": (session_id or "")[:12],
                "model": model_name,
                "tool": e0.get("toolName") or "",
                "type": e0.get("type") or "",
                "msg": e0msg,
            })

    
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
        
        if session_id not in sess_min or (started and started < sess_min[session_id]):
            sess_min[session_id] = started
            sess_first[session_id] = dk

    
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





# ---------- [2.7.5] 自动归档合并（traceId 去重，透明常驻） ----------
ARCHIVE_DIR = os.path.join(HOME, "usage-archive")
archive_existed = False
archived_only = []
if args.no_archive:
    print("[2.7.5] 归档已禁用（--no-archive）", flush=True)
else:
    try:
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        aj = os.path.join(ARCHIVE_DIR, "requests.jsonl")
        archive_existed = os.path.exists(aj)
        arch_recs = {}
        if archive_existed:
            with open(aj, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                        if o.get("id"):
                            arch_recs[o["id"]] = o
                    except Exception:
                        continue
        live_ids = {r["id"] for r in requests if r.get("id")}
        new_from_live = [r for r in requests if r.get("id") and r["id"] not in arch_recs]
        for r in new_from_live:
            arch_recs[r["id"]] = r
        archived_only = [o for o in arch_recs.values() if o["id"] not in live_ids]
        # 归档独有的错误重放进全局错误统计（el = [type, tool, msg]）
        for r in archived_only:
            _dk = r.get("date") or ""
            _mdl = r.get("model") or "unknown"
            _sid = r.get("session_id") or ""
            for it in (r.get("el") or []):
                _e = (list(it) + ["", "", ""])[:3]
                err_msg_counter[_e[2]] += 1
                err_type_counter[_e[0] or "unknown"] += 1
                if _e[1]:
                    err_tool_counter[_e[1]] += 1
                err_by_model.setdefault(_mdl, Counter())[_e[2]] += 1
                if _sid:
                    err_by_session.setdefault(_sid, Counter())[_e[2]] += 1
                if _dk:
                    err_by_day.setdefault(_dk, Counter())[_e[2]] += 1
        requests = list(arch_recs.values())
        # 会话级合并：credit 单调取 max；title 当前优先；first_date 取更早
        asj = os.path.join(ARCHIVE_DIR, "sessions.json")
        arch_sess = {}
        if os.path.exists(asj):
            try:
                arch_sess = json.load(open(asj, encoding="utf-8"))
            except Exception:
                arch_sess = {}
        for sid, ent in arch_sess.items():
            if sid not in sess_meta and ent.get("title"):
                sess_meta[sid] = {"title": ent["title"]}
            cur_cr = sess_credit.get(sid, {}).get("credit", 0.0)
            if (ent.get("credit") or 0) > cur_cr:
                sess_credit[sid] = {"credit": ent["credit"]}
            fd_a = ent.get("first_date") or ""
            if fd_a and (sid not in sess_first or fd_a < sess_first[sid]):
                sess_first[sid] = fd_a
        with open(aj + ".tmp", "w", encoding="utf-8") as fh:
            for o in arch_recs.values():
                fh.write(json.dumps(o, ensure_ascii=False) + "\n")
        os.replace(aj + ".tmp", aj)
        cur_sess = {}
        for r in requests:
            sid = r.get("session_id") or ""
            if not sid:
                continue
            cur_sess[sid] = {
                "title": (sess_meta.get(sid, {}) or {}).get("title", ""),
                "credit": sess_credit.get(sid, {}).get("credit", 0.0),
                "first_date": sess_first.get(sid, ""),
            }
        merged_sess = dict(arch_sess)
        for sid, ent in cur_sess.items():
            old = merged_sess.get(sid, {})
            _fds = [x for x in (ent.get("first_date"), old.get("first_date")) if x]
            merged_sess[sid] = {
                "title": ent.get("title") or old.get("title", ""),
                "credit": max(ent.get("credit") or 0.0, old.get("credit") or 0.0),
                "first_date": (min(_fds) if _fds else ""),
            }
        with open(asj + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(merged_sess, fh, ensure_ascii=False)
        os.replace(asj + ".tmp", asj)
        print(f"[2.7.5] 归档合并：原归档 {len(arch_recs) - len(new_from_live)} + 本次新增 {len(new_from_live)} = 合计 {len(arch_recs)} 条"
              f"（其中 {len(archived_only)} 条的 trace 已被清理，靠归档保留）", flush=True)
    except Exception as e:
        print("[2.7.5] 归档合并失败（跳过，不影响本次输出）:", e, flush=True)
        archived_only = []

print("[2.8] 轮次扩展：提问原文（jsonl）+ 缓存口径 + 调用明细 ...", flush=True)
sess_prompt = {}
need_sids = {r["session_id"] for r in requests if r["session_id"] and not r.get("q")}
proj_dir = os.path.join(HOME, "projects")
if os.path.isdir(proj_dir) and need_sids:
    cand = []
    for root, dirs, fs in os.walk(proj_dir):
        for fn in fs:
            base, ext = os.path.splitext(fn)
            if ext == ".jsonl" and base in need_sids:
                cand.append(os.path.join(root, fn))
    for fp in cand:
        sid = os.path.splitext(os.path.basename(fp))[0]
        items = []
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if "<user_query>" not in line or '"role":"user"' not in line:
                        continue
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    txt = o.get("content")
                    parts = []
                    if isinstance(txt, list):
                        for c in txt:
                            if isinstance(c, dict):
                                t2 = c.get("text") or ""
                                if isinstance(t2, str):
                                    parts.append(t2)
                    elif isinstance(txt, str):
                        parts.append(txt)
                    full = "\n".join(parts)
                    m = re.search(r"<user_query>(.*?)</user_query>", full, re.S)
                    if not m:
                        continue
                    q = m.group(1).strip()
                    if not q:
                        continue
                    try:
                        ts2 = int(o.get("timestamp") or 0)
                    except Exception:
                        ts2 = 0
                    if ts2:
                        items.append((ts2, q[:300]))
        except Exception:
            continue
        if items:
            items.sort()
            sess_prompt[sid] = items
print(f"  提问原文：{len(sess_prompt)}/{len(need_sids)} 个会话命中。", flush=True)

for r in requests:
    if r.get("session_id") not in sess_prompt:
        continue
    items = sess_prompt[r["session_id"]]
    q = ""
    if items and r.get("started_at"):
        lo, hi = 0, len(items) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if items[mid][0] <= r["started_at"]:
                lo = mid + 1
            else:
                hi = mid - 1
        if hi >= 0:
            q = items[hi][1]
    r["q"] = q

_cache_denom_inclusive = not any(r["cached"] > r["input"] for r in requests)
cache_model_map = {}
for r in requests:
    cm = cache_model_map.setdefault(r["model"], {"model": r["model"], "cached": 0, "input": 0, "calls": 0})
    cm["cached"] += r["cached"]
    cm["input"] += r["input"]
    cm["calls"] += 1
cache_model = []
for cm in cache_model_map.values():
    if cm["calls"] < 10:
        continue
    _den = cm["input"] if _cache_denom_inclusive else (cm["input"] + cm["cached"])
    cm["rate"] = round(cm["cached"] / _den * 100.0, 1) if _den else None
    cache_model.append(cm)
cache_model.sort(key=lambda x: (x["cached"] or 0), reverse=True)

_total_input_all = sum(r["input"] for r in requests)
_total_cached_all = sum(r["cached"] for r in requests)
_den_all = _total_input_all if _cache_denom_inclusive else (_total_input_all + _total_cached_all)
overall_cache_rate = round(_total_cached_all / _den_all * 100.0, 1) if _den_all else None
cache_caliber = "cached/in" if _cache_denom_inclusive else "cached/(in+cached)"
print(f"  缓存命中率（{cache_caliber}）：{overall_cache_rate}%", flush=True)

requests_full = []
for r in requests:
    requests_full.append({
        "sid": r["session_id"] or "",
        "ts": int(r["started_at"] / 1000) if r["started_at"] else 0,
        "date": r["date"],
        "dur": int(r["duration_ms"] / 1000),
        "st": r["status"],
        "tk": r["tokens"],
        "inp": r["input"],
        "out": r["output"],
        "ca": r["cached"],
        "calls": r["calls"],
        "model": r["model"],
        "think": r["thinking_sec"],
        "tools": r["tool_count"],
        "errs": r["errors"],
        "gen_n": r.get("gen_n", 0),
        "gen_ms": int(r.get("gen_ms", 0)),
        "tl": r.get("tl", []),
        "el": r.get("el", []),
        "q": r.get("q", ""),
    })
sessions_map = {sid: (m.get("title") or "")[:60] for sid, m in sess_meta.items()}


print("[3/4] 聚合指标 ...", flush=True)
days = sorted(by_day.keys())
day_list = []
for dk in days:
    b = by_day[dk]
    b["sessions"] = len(b["sessions"])
    b["thinking_sec"] = round(b["thinking_sec"], 1)
    b["credit"] = round(b["credit"], 2)
    day_list.append(b)




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
    
    cr = sess_credit.get(sb["session_id"], {}).get("credit", 0)
    sb["credit"] = round(cr, 2)
    
    
    
    
    
    sid = sb["session_id"]
    fd = sess_first.get(sid)
    sb["first_date"] = fd or ""
    if fd and fd in by_day:
        by_day[fd]["credit"] += cr
    sess_list.append(sb)
sess_list.sort(key=lambda x: x["tokens"], reverse=True)

# ---------- 每日总量覆盖层（持久化于归档 + --seed 导入，每次运行自动应用） ----------
restored_days, corrected_days = [], []
overlay_path = os.path.join(ARCHIVE_DIR, "daily_overlay.json")
daily_overlay = {}
if not args.no_archive:
    try:
        if os.path.exists(overlay_path):
            daily_overlay = json.load(open(overlay_path, encoding="utf-8"))
    except Exception:
        daily_overlay = {}
    if args.seed and os.path.exists(args.seed):
        try:
            seed_days = {}
            if args.seed.lower().endswith(".html"):
                _h = open(args.seed, "r", encoding="utf-8").read()
                _m = re.search(r"window\.USAGE_STATUS = (\{.*?\});</script>", _h, re.S)
                if _m:
                    seed_days = {d["date"]: d for d in json.loads(_m.group(1)).get("by_day", []) if d.get("date")}
            else:
                _j = json.load(open(args.seed, encoding="utf-8"))
                seed_days = {d["date"]: d for d in _j.get("by_day", []) if d.get("date")}
            for d, srow in seed_days.items():
                cur_o = daily_overlay.get(d)
                if (cur_o is None) or (srow.get("tokens", 0) > cur_o.get("tokens", 0)):
                    daily_overlay[d] = srow
            with open(overlay_path + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(daily_overlay, fh, ensure_ascii=False)
            os.replace(overlay_path + ".tmp", overlay_path)
            print(f"  种子导入：合并 {len(seed_days)} 天进入每日覆盖层（现共 {len(daily_overlay)} 天）", flush=True)
        except Exception as e:
            print("  种子导入失败（跳过）:", e, flush=True)
    if daily_overlay:
        for d, srow in daily_overlay.items():
            cur = by_day.get(d)
            if cur is None:
                row = {k: srow.get(k, 0) for k in ("requests", "tokens", "input", "output", "cached", "thinking_sec", "credit", "errors", "sessions")}
                row["date"] = d
                by_day[d] = row
                restored_days.append(d)
            elif srow.get("tokens", 0) > cur.get("tokens", 0):
                for k in ("requests", "tokens", "input", "output", "cached", "thinking_sec", "credit", "errors", "sessions"):
                    if k in srow:
                        cur[k] = srow[k]
                corrected_days.append(d)
        if restored_days or corrected_days:
            day_list[:] = [by_day[k] for k in sorted(by_day.keys())]
            print(f"  每日覆盖层应用：恢复 {len(restored_days)} 天、修正 {len(corrected_days)} 天", flush=True)
_valid_days = [k for k in by_day if re.match(r"^\d{4}-\d{2}-\d{2}$", k)]





xlsx_date_min = None
xlsx_date_max = None
model_cost_official = []   
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
        else:
            credit_note = "提供的 xlsx 未包含与本地数据重叠的日期，credit 仍为本地估算。"
        xlsx_dates = sorted(xmap.keys())
        xlsx_date_min = xlsx_dates[0]
        xlsx_date_max = xlsx_dates[-1]

        
        
        
        
        xmodel, xmodel_cnt = read_credit_xlsx_by_model(args.credit_xlsx)
        if xmodel:
            
            win_tokens = {}
            for (d, m), tk in day_model_tokens.items():
                if xlsx_date_min <= d <= xlsx_date_max:
                    win_tokens[m] = win_tokens.get(m, 0) + tk
            for m, cr in sorted(xmodel.items(), key=lambda kv: kv[1]):
                tk = win_tokens.get(m, 0)
                model_cost_official.append({
                    "model": m,
                    "requests": xmodel_cnt.get(m, 0),
                    "tokens": tk,
                    "credit": round(cr, 2),
                    "credit_per_100k": (round(cr / tk * 100000.0, 2) if tk else None),
                    
                    "zero_credit": (cr <= 0.0 and tk >= 1_000_000),
                })
            print(f"  xlsx 按模型汇总 {len(xmodel)} 个模型（服务端精确 credit）。", flush=True)
        if covered:
            print(f"  xlsx 覆盖 {covered} 天，credit 已更新为精确值；日期窗口 {xlsx_date_min}~{xlsx_date_max}。", flush=True)
        else:
            print("  xlsx 与本地数据无日期重叠，每日 credit 维持本地估算。", flush=True)
    else:
        print("  xlsx 读取失败或未识别到必要列，credit 维持本地估算。", flush=True)



billing_date_min = None
billing_date_max = None
if args.billing_token_file:
    print("[3.6] 用量 API（--billing-token-file，用户手动提供 token）...", flush=True)
    try:
        _data_dates = [r["date"] for r in requests if r["date"] != "unknown"]
        api_start = (min(_data_dates) if _data_dates
                     else (datetime.date.today() - datetime.timedelta(days=30)).strftime("%Y-%m-%d"))
        api_end = max(_data_dates) if _data_dates else datetime.date.today().strftime("%Y-%m-%d")
        day_map, by_model, by_model_cnt, bmin, bmax = fetch_billing_usage(
            args.billing_token_file, api_start, api_end)
        if day_map:
            covered = 0
            for b in day_list:
                if b["date"] in day_map:
                    b["credit"] = round(day_map[b["date"]], 2)
                    covered += 1
            if covered:
                credit_source = "api_precise"
                credit_note = (f"credit 已用官方用量 API 精确覆盖 {covered} 天"
                               f"（窗口内为服务端精确值，由用户手动提供的 token 拉取）；"
                               f"未覆盖日期仍为本地估算。")
            billing_date_min, billing_date_max = bmin, bmax
            
            if by_model:
                win_tokens = {}
                for (d, m), tk in day_model_tokens.items():
                    if (billing_date_min or "0000") <= d <= (billing_date_max or "9999"):
                        win_tokens[m] = win_tokens.get(m, 0) + tk
                model_cost_official = []
                for m, cr in sorted(by_model.items(), key=lambda kv: kv[1]):
                    tk = win_tokens.get(m, 0)
                    model_cost_official.append({
                        "model": m,
                        "requests": by_model_cnt.get(m, 0),
                        "tokens": tk,
                        "credit": round(cr, 2),
                        "credit_per_100k": (round(cr / tk * 100000.0, 2) if tk else None),
                        "zero_credit": (cr <= 0.0 and tk >= 1_000_000),
                    })
                print(f"  API 按模型汇总 {len(by_model)} 个模型（服务端精确 credit）。", flush=True)
            print(f"  API 覆盖 {covered} 天，credit 已更新为精确值；窗口 {billing_date_min}~{billing_date_max}。", flush=True)
        else:
            print("  API 返回空数据，credit 维持本地估算。", flush=True)
    except Exception as e:
        print("  用量 API 拉取失败，credit 维持本地估算：", e, flush=True)


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
    
    mc["zero_credit"] = (mc["credit"] <= 0.0 and mc["tokens"] >= 1_000_000)
    model_cost_list.append(mc)
model_cost_list.sort(key=lambda x: x["credit_per_100k"])


model_tips = []
substantial = [
    m for m in model_cost_list
    if m["model"] not in ("auto", "unknown")
    and "preview" not in m["model"]
    and "agent" not in m["model"]
    and m["tokens"] >= 10_000_000
    and m["credit"] > 0.0          
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

for m in model_cost_list:
    if m["zero_credit"] and m["tokens"] >= 10_000_000:
        model_tips.append(
            f"「{m['model']}」当前 credit/10万token=0（消耗 credit {m['credit']:.2f}），"
            f"可能处于限免/促销期；不建议把它作为长期成本基准。")





print("[2.7] 用量高峰探查 ...", flush=True)
daily_credit = {b["date"]: b["credit"] for b in day_list if b["date"] != "unknown"}

daily_tokens = {}
for r in requests:
    if r["date"] != "unknown":
        daily_tokens[r["date"]] = daily_tokens.get(r["date"], 0) + r["tokens"]
tok_vals = sorted(daily_tokens.values())
median_tok = tok_vals[len(tok_vals) // 2] if tok_vals else 0
thr = max(median_tok * 2.0, 5_000_000)     
cand = sorted([(d, v) for d, v in daily_tokens.items() if v >= thr],
              key=lambda x: x[1], reverse=True)
top_days = [d for d, _ in cand[:6]]
if len(top_days) < 3:                      
    top_days = [d for d, _ in sorted(daily_tokens.items(), key=lambda x: x[1], reverse=True)[:3]]

spike_days = []
for d in top_days:
    day_reqs = [r for r in requests if r["date"] == d]
    
    
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
            "model": model_str,           
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
        "credit": round(daily_credit.get(d, 0.0), 2),   
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
        "top_error_msg": (err_by_day.get(d).most_common(1)[0][0] if err_by_day.get(d) else ""),
    })

total_tokens = sum(r["tokens"] for r in requests)
total_input = sum(r["input"] for r in requests)
total_output = sum(r["output"] for r in requests)
total_cached = sum(r["cached"] for r in requests)
total_thinking = round(sum(r["thinking_sec"] for r in requests), 1)
total_credit = round(sum(b["credit"] for b in day_list), 2)
total_errors = sum(r["errors"] for r in requests)
dates = [r["date"] for r in requests if r["date"] != "unknown"]




error_top_messages = [{"msg": m, "count": c} for m, c in err_msg_counter.most_common(10)]
error_by_type = [{"type": t, "count": c} for t, c in err_type_counter.most_common()]
error_by_tool = [{"tool": t, "count": c} for t, c in err_tool_counter.most_common(10)]
error_by_model_list = sorted(
    [{"model": m, "count": sum(c.values()), "top_msg": (c.most_common(1)[0][0] if c else "")}
     for m, c in err_by_model.items()],
    key=lambda x: -x["count"])[:10]
error_by_session_list = sorted(
    [{"session_id": sid[:12],
      "title": (sess_meta.get(sid, {}).get("title", "") or "")[:40],
      "count": sum(c.values()), "top_msg": (c.most_common(1)[0][0] if c else "")}
     for sid, c in err_by_session.items()],
    key=lambda x: -x["count"])[:10]
error_detail = {
    "total": total_errors,
    "top_messages": error_top_messages,
    "by_type": error_by_type,
    "by_tool": error_by_tool,
    "by_model": error_by_model_list,
    "by_session": error_by_session_list,
    "samples": error_samples,
}

summary = {
    "version": "1.4.1",
    "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    "credit_source": credit_source,
    "credit_note": credit_note,
    "total_requests": len(requests),
    "total_sessions": len(sess_list),
    "total_tokens": total_tokens,
    "total_input": total_input,
    "total_output": total_output,
    "total_cached": total_cached,
    "cache_rate": overall_cache_rate,
    "cache_caliber": cache_caliber,
    "total_thinking_sec": total_thinking,
    "total_thinking_hours": round(total_thinking / 3600.0, 2),
    "avg_thinking_sec_per_request": round(total_thinking / len(requests), 1) if requests else 0,
    "total_credit": total_credit,
    "total_errors": total_errors,
    "top_error_msg": (error_top_messages[0]["msg"] if error_top_messages else ""),
    "top_error_pct": (round(error_top_messages[0]["count"] / total_errors * 100, 1) if total_errors else 0.0),
    "avg_efficiency_tok_per_sec": round(total_output / total_thinking, 1) if total_thinking else 0,
    "date_min": (min(_valid_days) if _valid_days else (min(dates) if dates else None)),
    "date_max": (max(_valid_days) if _valid_days else (max(dates) if dates else None)),
    "credit_xlsx_date_min": xlsx_date_min,
    "credit_xlsx_date_max": xlsx_date_max,
    "billing_date_min": billing_date_min,
    "billing_date_max": billing_date_max,
    "model_count": len(model_list),
}


requests_slim = [
    {"date": r["date"], "tokens": r["tokens"], "output": r["output"],
     "thinking_sec": r["thinking_sec"], "model": r["model"], "errors": r["errors"]}
    for r in requests
]


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
    "model_cost_official": model_cost_official,
    "model_tips": model_tips,
    "spike_days": spike_days,
    "error_detail": error_detail,
    "cache_model": cache_model,
    "requests_full": requests_full,
    "sessions_map": sessions_map,
}


warnings = []
if skipped_trace_files:
    names = "；".join(os.path.basename(x[0]) for x in skipped_trace_files[:5])
    more = " 等" if len(skipped_trace_files) > 5 else ""
    warnings.append({
        "type": "skipped_traces",
        "count": len(skipped_trace_files),
        "names": names + more,
        "detail": f"已跳过 {len(skipped_trace_files)} 个损坏/无法解析的 trace 文件（报告可能不完整）：{names}{more}",
    })
if bad_credit_sessions:
    warnings.append({
        "type": "bad_credit",
        "count": bad_credit_sessions,
        "detail": f"{bad_credit_sessions} 个会话的 credit_json 解析失败，相关会话 credit 计为 0（不影响 token 与每日趋势）。",
    })
if args.seed and (restored_days or corrected_days):
    warnings.append({
        "type": "seed_restore",
        "count": len(restored_days) + len(corrected_days),
        "restored": len(restored_days),
        "corrected": len(corrected_days),
        "detail": f"已从旧快照恢复 {len(restored_days)} 天、修正 {len(corrected_days)} 天的每日总量；这些天的部分逐笔明细因 WorkBuddy 30 天 trace 清理不可恢复。",
    })
if (not archive_existed) and (not args.no_archive):
    warnings.append({
        "type": "archive_first_run",
        "count": 1,
        "detail": "本次运行已建立本地归档（~/.workbuddy/usage-archive）。WorkBuddy 对 trace 仅保留 30 天——请至少每 30 天运行一次本技能（建议配置每日定时自动化），断档超 30 天期间的 trace 将无法追溯。",
    })
_orphan_credit, _orphan_sessions = 0.0, 0
for sid, ent in sess_credit.items():
    if sid in by_session:
        continue
    _cr = ent.get("credit", 0) or 0
    if _cr:
        _orphan_sessions += 1
        _orphan_credit += _cr
if _orphan_credit > 0.5:
    warnings.append({
        "type": "orphan_credit",
        "count": _orphan_sessions,
        "amount": round(_orphan_credit, 2),
        "detail": f"另有 {_orphan_sessions} 个历史会话的 credit 合计 {round(_orphan_credit, 2)}，其逐笔明细已不在本地，未在每日趋势中逐日展示。",
    })
out["warnings"] = warnings



print("[4/4] 写出 usage-status.json / usage-status.js ...", flush=True)
with open(OUT_JSON, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
with open(OUT_JS, "w", encoding="utf-8") as f:
    f.write("window.USAGE_STATUS = ")
    json.dump(out, f, ensure_ascii=False)
    f.write(";")


TPL = os.path.join(SCRIPT_DIR, "dashboard_template.html")
CHART_JS = os.path.join(SCRIPT_DIR, "chart.umd.min.js")

OUT_HTML = os.path.join(OUT_DIR, "workbuddy-usage-status-dashboard-"
                        + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".html")
try:
    tpl = open(TPL, "r", encoding="utf-8").read()

    
    chart_tag = ""
    if os.path.exists(CHART_JS):
        chart_tag = '<script src="chart.umd.min.js"></script>'
    else:
        sys.exit("错误：缺少随包文件 chart.umd.min.js，无法生成离线 HTML。\n"
                 "请确认该文件与 usage_extractor.py 同在 scripts/ 目录下。")
    if "<!--CHART_JS_ASSET_TAG-->" in tpl:
        tpl = tpl.replace("<!--CHART_JS_ASSET_TAG-->", chart_tag)

    
    
    
    inline = '<script>window.USAGE_STATUS = ' + json.dumps(out, ensure_ascii=False).replace("<", "\\u003c") + ';</script>'
    if "<!--USAGE_DATA-->" in tpl:
        html = tpl.replace("<!--USAGE_DATA-->", inline)
    else:
        html = tpl.replace("</head>", inline + "</head>", 1)
    open(OUT_HTML, "w", encoding="utf-8").write(html)
    shutil.copy2(CHART_JS, os.path.join(OUT_DIR, "chart.umd.min.js"))
    print("已生成看板 HTML（chart.js 外链，需与 chart.umd.min.js 同目录存放）:", OUT_HTML)
except Exception as e:
    print("HTML 生成跳过:", e)

# ---------- CSV 表头语言：自动跟随操作系统界面语言（零开关） ----------
def _detect_lang():
    try:
        if sys.platform == "win32":
            import ctypes
            _lid = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
            return "zh" if (_lid & 0x3FF) == 0x04 else "en"
    except Exception:
        pass
    try:
        import locale as _loc
        _l = _loc.getlocale()[0] or ""
        if _l:
            return "zh" if _l.lower().startswith("zh") else "en"
    except Exception:
        pass
    return "zh"

_CSV_LABELS = {
    "zh": {
        "title": "# WorkBuddy 用量全量导出（生成时间 {ts}）",
        "caliber": "# 缓存命中率口径: {c}",
        "sec_daily": "## 每日汇总", "sec_model": "## 按模型", "sec_sessions": "## 会话清单（前200）", "sec_calls": "## 调用明细",
        "sec_err_top": "## 错误-高频", "sec_err_type": "## 错误-按类型", "sec_err_tool": "## 错误-按工具",
        "sec_err_model": "## 错误-按模型", "sec_err_sess": "## 错误-按会话", "sec_err_samples": "## 错误-近期样本",
        "date": "日期", "reqs": "请求数", "token": "Token", "input": "输入", "output": "输出", "cache": "缓存命中",
        "think_sec": "思考秒", "credit": "credit", "errors": "错误", "sessions": "会话数",
        "model": "模型", "calls_n": "调用数", "eff": "效率tok/s", "session": "会话", "title_c": "标题",
        "think_min": "思考(分)", "status": "状态", "first_date": "首现日期",
        "time": "时间", "duration": "时长秒", "tools": "工具数", "prompt": "提问",
        "msg": "错误信息", "count": "次数", "share": "占比", "type": "类型", "tool": "工具", "top_err": "最高频错误",
    },
    "en": {
        "title": "# WorkBuddy usage full export (generated at {ts})",
        "caliber": "# Cache-hit caliber: {c}",
        "sec_daily": "## Daily summary", "sec_model": "## By model", "sec_sessions": "## Sessions (top 200)", "sec_calls": "## Call details",
        "sec_err_top": "## Errors - top", "sec_err_type": "## Errors - by type", "sec_err_tool": "## Errors - by tool",
        "sec_err_model": "## Errors - by model", "sec_err_sess": "## Errors - by session", "sec_err_samples": "## Errors - recent samples",
        "date": "Date", "reqs": "Requests", "token": "Tokens", "input": "Input", "output": "Output", "cache": "Cache hit",
        "think_sec": "Think sec", "credit": "credit", "errors": "Errors", "sessions": "Sessions",
        "model": "Model", "calls_n": "Calls", "eff": "Eff tok/s", "session": "Session", "title_c": "Title",
        "think_min": "Think(min)", "status": "Status", "first_date": "First seen",
        "time": "Time", "duration": "Duration(s)", "tools": "Tools", "prompt": "Prompt",
        "msg": "Error message", "count": "Count", "share": "Share", "type": "Type", "tool": "Tool", "top_err": "Top error",
    },
}
_csv_lang = _detect_lang()
CSV_L = _CSV_LABELS[_csv_lang]

# ---------- 全量 CSV 同步输出（内置浏览器无法下载 blob 时直接取文件） ----------
try:
    def _csv_cell(v):
        s2 = "" if v is None else str(v)
        if re.search(r'[",\r\n]', s2):
            s2 = '"' + s2.replace('"', '""') + '"'
        return s2
    _csv_lines = []
    def _csv_push(arr):
        _csv_lines.append(",".join(_csv_cell(x) for x in arr))
    _csv_push([CSV_L["title"].format(ts=datetime.datetime.now().isoformat(timespec="seconds"))])
    _csv_push([CSV_L["caliber"].format(c=(summary.get("cache_caliber") or "cached/in"))])
    _csv_push("")
    _csv_push([CSV_L["sec_daily"]]); _csv_push([CSV_L["date"],CSV_L["reqs"],CSV_L["token"],CSV_L["input"],CSV_L["output"],CSV_L["cache"],CSV_L["think_sec"],CSV_L["credit"],CSV_L["errors"],CSV_L["sessions"]])
    for d in day_list:
        _csv_push([d.get("date"),d.get("requests"),d.get("tokens"),d.get("input"),d.get("output"),d.get("cached"),d.get("thinking_sec"),d.get("credit"),d.get("errors"),d.get("sessions")])
    _csv_push(""); _csv_push([CSV_L["sec_model"]]); _csv_push([CSV_L["model"],CSV_L["reqs"],CSV_L["token"],CSV_L["input"],CSV_L["output"],CSV_L["calls_n"],CSV_L["think_sec"],CSV_L["errors"],CSV_L["eff"]])
    for m in (out.get("by_model") or []):
        _csv_push([m.get("model"),m.get("requests"),m.get("tokens"),m.get("input"),m.get("output"),m.get("calls"),m.get("thinking_sec"),m.get("errors"),m.get("efficiency_tok_per_sec")])
    _csv_push(""); _csv_push([CSV_L["sec_sessions"]]); _csv_push([CSV_L["session"],CSV_L["title_c"],CSV_L["model"],CSV_L["reqs"],CSV_L["token"],CSV_L["think_min"],CSV_L["credit"],CSV_L["errors"],CSV_L["status"],CSV_L["first_date"]])
    for x in (out.get("by_session") or []):
        _th = round(x.get("thinking_sec", 0) / 60, 1) if x.get("thinking_sec") else 0
        _csv_push([x.get("session_id"),x.get("title"),x.get("model"),x.get("requests"),x.get("tokens"),_th,x.get("credit"),x.get("errors"),x.get("status"),x.get("first_date")])
    _csv_push(""); _csv_push([CSV_L["sec_calls"]]); _csv_push([CSV_L["date"],CSV_L["time"],CSV_L["session"],CSV_L["model"],CSV_L["status"],CSV_L["token"],CSV_L["input"],CSV_L["output"],CSV_L["cache"],CSV_L["calls_n"],CSV_L["tools"],CSV_L["think_sec"],CSV_L["duration"],CSV_L["errors"],CSV_L["prompt"]])
    for r in (out.get("requests_full") or []):
        _ts = datetime.datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S") if r.get("ts") else ""
        _csv_push([r.get("date"),_ts,r.get("sid"),r.get("model"),r.get("st"),r.get("tk"),r.get("inp"),r.get("out"),r.get("ca"),r.get("calls"),r.get("tools"),r.get("think"),r.get("dur"),r.get("errs"),r.get("q")])
    _ed = out.get("error_detail") or {}
    _csv_push(""); _csv_push([CSV_L["sec_err_top"]]); _csv_push([CSV_L["msg"],CSV_L["count"],CSV_L["share"]])
    for x in (_ed.get("top_messages") or []):
        _sh = round(x.get("count", 0) / _ed["total"] * 100, 2) if _ed.get("total") else ""
        _csv_push([x.get("msg"),x.get("count"),_sh])
    _csv_push(""); _csv_push([CSV_L["sec_err_type"]]); _csv_push([CSV_L["type"],CSV_L["count"]])
    for x in (_ed.get("by_type") or []): _csv_push([x.get("type"),x.get("count")])
    _csv_push(""); _csv_push([CSV_L["sec_err_tool"]]); _csv_push([CSV_L["tool"],CSV_L["count"]])
    for x in (_ed.get("by_tool") or []): _csv_push([x.get("tool"),x.get("count")])
    _csv_push(""); _csv_push([CSV_L["sec_err_model"]]); _csv_push([CSV_L["model"],CSV_L["count"],CSV_L["top_err"]])
    for x in (_ed.get("by_model") or []): _csv_push([x.get("model"),x.get("count"),x.get("top_msg")])
    _csv_push(""); _csv_push([CSV_L["sec_err_sess"]]); _csv_push([CSV_L["session"],CSV_L["count"],CSV_L["top_err"]])
    for x in (_ed.get("by_session") or []): _csv_push([x.get("title") or x.get("session_id") or "",x.get("count"),x.get("top_msg")])
    _csv_push(""); _csv_push([CSV_L["sec_err_samples"]]); _csv_push([CSV_L["date"],CSV_L["session"],CSV_L["model"],CSV_L["tool"],CSV_L["msg"]])
    for x in (_ed.get("samples") or []): _csv_push([x.get("date"),x.get("session_id"),x.get("model"),x.get("tool"),x.get("msg")])
    OUT_CSVF = os.path.join(OUT_DIR, "usage-full-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".csv")
    with open(OUT_CSVF, "w", encoding="utf-8-sig", newline="") as f:
        f.write("\r\n".join(_csv_lines))
    print("已生成全量 CSV:", os.path.basename(OUT_CSVF), f"（表头语言: {_csv_lang}；与看板同目录；内置浏览器无法下载时直接取用）", flush=True)
except Exception as e:
    print("全量 CSV 生成失败（不影响看板）:", e, flush=True)

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
