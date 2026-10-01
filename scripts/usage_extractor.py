

"""
WorkBuddy 本地 usage-status 抽取器
（面向中文 WorkBuddy 用户 zh-CN 设计；英文能力说明见 SKILL.md 的 description EN 段）

读取（全部只读 mode=ro）~/.workbuddy 下：
  - workbuddy.db  (sessions + session_usage: token预算/上下文上限/credit消耗)
  - traces/*/trace_*.json  (每次请求的时长/token拆分/思考用时/模型/工具调用/错误)
  - projects/*/*.jsonl  (每行的 providerData：提问摘要 <user_query>，以及该次模型调用的
    模型名与精确 credit，用于把 credit 逐次归到真实日期、真实模型)

写入：
  - 输出目录：usage-status.json（原始聚合数据）、usage-status.js（window.USAGE_STATUS = {...}，
    供 HTML 直接 <script> 引入以避开 file:// 的 fetch 跨域限制）、dashboard HTML、chart.umd.min.js、
    usage-full-<时间戳>.csv 与 usage-full-<时间戳>.xlsx（同源；CSV 每行的分区在 xlsx 里对应一个工作表）
  - ~/.workbuddy/usage-archive/：逐请求归档 + 会话汇总 + 每日总量覆盖层（traceId 去重；--no-archive 关闭）

网络与读取范围：
  - 全程零网络请求；不读取宿主 App 的凭据、账号与登录态。
  - 读取系统界面语言，仅用于决定 CSV 表头的语种。
"""
import sqlite3, json, os, glob, datetime, sys, argparse, shutil, re
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
                    help="可选：用量明细 xlsx 路径（来自 workbuddy.cn 用量导出）。定位为参考与补充："
                         "不覆盖逐日 credit；只在本地缺少逐次明细的日期上补入。")
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
        # 归档独有记录的用量回填会话维度。这批 trace 已被 30 天清理，此前只进了逐日汇总
        # 与明细表，会话维度（看板 KPI、Top 10 会话表）因此系统性偏低。
        for r in archived_only:
            _sid = r.get("session_id") or ""
            if not _sid:
                continue
            _sb = by_session.setdefault(
                _sid,
                {"session_id": _sid, "requests": 0, "tokens": 0, "thinking_sec": 0.0,
                 "credit": 0.0, "errors": 0, "models": set()},
            )
            _sb["requests"] += 1
            _sb["tokens"] += r.get("tokens") or 0
            _sb["thinking_sec"] += r.get("thinking_sec") or 0
            _sb["errors"] += r.get("errors") or 0
            _sb["models"].add(r.get("model") or "unknown")
            _rd = r.get("date") or ""
            if _rd and (_sid not in sess_first or _rd < sess_first[_sid]):
                sess_first[_sid] = _rd
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
# 逐次调用的精确 credit：读会话文件每行的 providerData.rawUsage.credit 与 providerData.model，
# 与该行 timestamp 配对，按真实日期/模型归集。与提问原文共用同一次文件遍历。
pc_day = {}          # {日期: credit}
pc_sess = {}         # {会话ID: credit}
pc_model = {}        # {模型: credit}
pc_calls = 0         # 解析到 credit 的调用条数
# 逐次调用明细，作为费率拟合与单次提问榜的统一数据源：
# (日期, 月份, 小时, 模型, 会话, 提问ID, 工作区, 非缓存输入, 缓存输入, 输出, 积分)
pc_list = []
if os.path.isdir(proj_dir):
    cand = []
    for root, dirs, fs in os.walk(proj_dir):
        for fn in fs:
            if os.path.splitext(fn)[1] == ".jsonl":
                cand.append(os.path.join(root, fn))
    print(f"  会话文件 {len(cand)} 个：一并扫描逐次 credit 与提问原文", flush=True)
    for fp in cand:
        sid = os.path.splitext(os.path.basename(fp))[0]
        items = []
        want_q = sid in need_sids
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"rawUsage"' in line:
                        try:
                            o2 = json.loads(line)
                        except Exception:
                            o2 = None
                        if isinstance(o2, dict):
                            pd2 = o2.get("providerData")
                            if isinstance(pd2, dict):
                                ru2 = pd2.get("rawUsage")
                                if isinstance(ru2, dict):
                                    try:
                                        c2 = float(ru2.get("credit") or 0)
                                    except Exception:
                                        c2 = 0.0
                                    try:
                                        _ts2 = int(o2.get("timestamp") or 0)
                                    except Exception:
                                        _ts2 = 0
                                    try:
                                        _pr2 = int(ru2.get("prompt_tokens") or 0)
                                        _ca2 = int(ru2.get("prompt_cache_hit_tokens") or 0)
                                        _ou2 = int(ru2.get("completion_tokens") or 0)
                                    except Exception:
                                        _pr2 = _ca2 = _ou2 = 0
                                    m2 = pd2.get("model") or pd2.get("requestModelId") or "unknown"
                                    _d2, _mo2, _h2 = "", "", -1
                                    if _ts2:
                                        try:
                                            _lt = datetime.datetime.fromtimestamp(_ts2 / 1000.0)
                                            _d2 = _lt.strftime("%Y-%m-%d")
                                            _mo2 = _lt.strftime("%Y-%m")
                                            _h2 = _lt.hour
                                        except Exception:
                                            pass
                                    pc_calls += 1
                                    pc_sess[sid] = pc_sess.get(sid, 0.0) + c2
                                    pc_model[m2] = pc_model.get(m2, 0.0) + c2
                                    if _d2:
                                        pc_day[_d2] = pc_day.get(_d2, 0.0) + c2
                                    pc_list.append((_d2, _mo2, _h2, m2, sid,
                                                    (pd2.get("conversationRequestId") or ""),
                                                    (o2.get("cwd") or ""),
                                                    (_pr2 - _ca2 if _pr2 > _ca2 else 0),
                                                    _ca2, _ou2, c2))
                    if (not want_q) or "<user_query>" not in line or '"role":"user"' not in line:
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
print(f"  逐次 credit：解析 {pc_calls} 次模型调用，覆盖 {len(pc_day)} 天 / {len(pc_sess)} 个会话 / {len(pc_model)} 个模型；逐次明细 {len(pc_list)} 条已备用于费率与归因分析。", flush=True)
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


print("[2.9] 模型费率拟合（按 模型 × 月份，月内再分 标准/低谷 两档）...", flush=True)
# 费率是不可加总的价格，只能按条件查找，所以单独成表，不参与任何求和。
# 门槛：进主表需 样本 >= 30 且 标准档 R² >= 0.95；低谷折扣的识别另需两档 R² >= 0.90 且折扣 <= 0.70。
_RATE_MIN_N = 30
_RATE_MIN_R2 = 0.95
_LOW_MIN_R2 = 0.90
_LOW_MAX_RATIO = 0.70


def _fit3(rows_, idx=slice(7, 10)):
    """无截距三元最小二乘：积分 = a×非缓存输入 + b×缓存输入 + c×输出。返回积分/百万 token。"""
    n = len(rows_)
    if n < 4:
        return None
    X = [[r[i] for i in range(idx.start, idx.stop)] for r in rows_]
    y = [r[10] for r in rows_]
    A = [[0.0] * 3 for _ in range(3)]
    rhs = [0.0] * 3
    for i in range(n):
        xi = X[i]
        for p in range(3):
            for q in range(3):
                A[p][q] += xi[p] * xi[q]
            rhs[p] += xi[p] * y[i]
    M = [A[i][:] + [rhs[i]] for i in range(3)]
    for c in range(3):
        piv = max(range(c, 3), key=lambda r: abs(M[r][c]))
        if abs(M[piv][c]) < 1e-18:
            return None
        M[c], M[piv] = M[piv], M[c]
        pv = M[c][c]
        for k in range(c, 4):
            M[c][k] /= pv
        for r in range(3):
            if r != c and M[r][c] != 0:
                f = M[r][c]
                for k in range(c, 4):
                    M[r][k] -= f * M[c][k]
    bta = [M[i][3] for i in range(3)]
    yh = [sum(bb * xx for bb, xx in zip(bta, xi)) for xi in X]
    my = sum(y) / n
    ss = sum((v - my) ** 2 for v in y)
    rss = sum((v - h) ** 2 for v, h in zip(y, yh))
    return {"a": bta[0] * 1e6, "b": bta[1] * 1e6, "c": bta[2] * 1e6,
            "r2": (1 - rss / ss) if ss > 0 else None, "n": n}


def _hranges(hs):
    """把小时集合压成区间文本，如 {0..8,12..23} -> '00-08、12-23'。"""
    hs = sorted(hs)
    if not hs:
        return ""
    out = []
    s = p = hs[0]
    for h in hs[1:]:
        if h == p + 1:
            p = h
            continue
        out.append((s, p))
        s = p = h
    out.append((s, p))
    return "、".join(("%02d" % x) if x == y else ("%02d-%02d" % (x, y)) for x, y in out)


# 只用真正扣过费的调用拟合：限免期的零积分调用会把斜率拉平（hy4-preview 实测 R² 从 1.00 掉到 0.22）
_pc_charged = [r for r in pc_list if r[10] > 0 and r[1] and r[3] != "unknown"]
_gm = {}
for r in _pc_charged:
    _gm.setdefault((r[3], r[1]), []).append(r)

model_rate = []
for (_mname, _mo), _rs in _gm.items():
    _base = _fit3(_rs)
    if not _base:
        continue
    _e = {"model": _mname, "period": _mo, "n": _base["n"],
          "a": round(_base["a"], 2), "b": round(_base["b"], 2), "c": round(_base["c"], 2),
          "r2": (round(_base["r2"], 4) if _base["r2"] is not None else None),
          "low_discount": None, "low_hours": "", "low": None}
    # 时段识别：逐小时算「实际积分 ÷ 该月整体基准积分」的中位数，明显低于最高档的判为低谷
    _byh = {}
    for r in _rs:
        _b0 = (_base["a"] * r[7] + _base["b"] * r[8] + _base["c"] * r[9]) / 1e6
        if _b0 > 0 and r[2] >= 0:
            _byh.setdefault(r[2], []).append(r[10] / _b0)
    _meds = {}
    for h, v in _byh.items():
        if len(v) >= 5:
            v.sort()
            _meds[h] = v[len(v) // 2]
    if _meds:
        _lo = min(_meds.values())
        if _lo > 0:
            _ph = sorted(h for h, v in _meds.items() if v / _lo > 1.3)   # 标准时段
            if _ph:
                _bp = _fit3([r for r in _rs if r[2] in _ph])
                _bo = _fit3([r for r in _rs if r[2] not in _ph])
                if (_bp and _bo and _bp["a"] > 0 and _bp["r2"] is not None
                        and _bo["r2"] is not None and _bp["r2"] >= _LOW_MIN_R2
                        and _bo["r2"] >= _LOW_MIN_R2
                        and (_bo["a"] / _bp["a"]) <= _LOW_MAX_RATIO):
                    _e.update({"a": round(_bp["a"], 2), "b": round(_bp["b"], 2),
                               "c": round(_bp["c"], 2), "r2": round(_bp["r2"], 4),
                               "n": _bp["n"],
                               "low_discount": round(_bo["a"] / _bp["a"], 3),
                               "low_hours": _hranges(set(range(24)) - set(_ph)) + " 时",
                               "low": {"a": round(_bo["a"], 2), "b": round(_bo["b"], 2),
                                       "c": round(_bo["c"], 2), "r2": round(_bo["r2"], 4),
                                       "n": _bo["n"]}})
    model_rate.append(_e)

_latest = {}
for _e in model_rate:
    _prev = _latest.get(_e["model"], "")
    _latest[_e["model"]] = _e["period"] if _e["period"] > _prev else _prev
for _e in model_rate:
    if _e["model"].lower().startswith("auto"):
        _e["reliable"], _e["reason"] = False, "routing"
    elif _e["n"] < _RATE_MIN_N:
        _e["reliable"], _e["reason"] = False, "sample"
    elif _e["r2"] is None or _e["r2"] < _RATE_MIN_R2:
        _e["reliable"], _e["reason"] = False, "varying"
    else:
        _e["reliable"], _e["reason"] = True, ""
    _e["is_current"] = (_e["period"] == _latest.get(_e["model"]))

# 全程零积分的模型：没有费率可言，单列一类，与「免费就是费率为 0 的生效区间」这一表达保持一致
for _m in sorted({r[3] for r in pc_list}):
    if _m in _latest or _m == "unknown":
        continue
    _cnt = sum(1 for r in pc_list if r[3] == _m)
    if _cnt:
        model_rate.append({"model": _m, "period": "", "n": _cnt, "a": None, "b": None,
                           "c": None, "r2": None, "low_discount": None, "low_hours": "",
                           "low": None, "reliable": False, "reason": "free",
                           "is_current": False})
model_rate.sort(key=lambda x: (not x["is_current"], x["model"], x["period"]))
_reliable_n = sum(1 for e in model_rate if e["reliable"])
print(f"  费率：{len(_gm)} 个 模型×月份 分组，{_reliable_n} 组达到样本≥{_RATE_MIN_N} 且 R²≥{_RATE_MIN_R2}；"
      f"{sum(1 for e in model_rate if e['low_discount'])} 组识别出低谷折扣。", flush=True)


print("[2.95] 单次提问成本榜 ...", flush=True)
# ---------- 单次提问成本：按 conversationRequestId 聚合 ----------
# 一次提问会触发多次模型调用，积分是它们的和。用中位数与集中度比用总量更能说明该优化哪里。
_asks = {}
for _r in pc_list:
    _cid = _r[5]
    if not _cid:
        continue
    _a = _asks.get(_cid)
    if _a is None:
        _a = {"crid": _cid, "sid": _r[4], "cwd": _r[6], "date": _r[0], "hour": _r[2],
              "calls": 0, "credit": 0.0, "tokens": 0, "models": {}}
        _asks[_cid] = _a
    _a["calls"] += 1
    _a["credit"] += _r[10]
    try:
        _a["tokens"] += int(_r[7]) + int(_r[8]) + int(_r[9])
    except Exception:
        pass
    _a["models"][_r[3]] = _a["models"].get(_r[3], 0.0) + _r[10]

_ask_list = list(_asks.values())
_ask_total = sum(a["credit"] for a in _ask_list)
_ask_list.sort(key=lambda x: -x["credit"])
for _a in _ask_list:
    _a["models"] = ",".join(m for m, _ in sorted(_a["models"].items(), key=lambda kv: -kv[1]))
    _a["session_title"] = (sess_meta.get(_a["sid"], {}).get("title") or "")[:40]
    _cw = (_a.get("cwd") or "").rstrip("\\/")
    _a["workspace"] = _cw.split("\\")[-1].split("/")[-1] if _cw else ""
    _a["credit"] = round(_a["credit"], 2)
    _a["share"] = round(_a["credit"] / _ask_total * 100, 2) if _ask_total else 0.0

_ask_credits = [a["credit"] for a in _ask_list]
_ask_n = len(_ask_credits)
ask_top = _ask_list[:20]
ask_stats = {
    "count": _ask_n,
    "credit": round(_ask_total, 2),
    "median": round(_ask_credits[_ask_n // 2], 2) if _ask_n else 0.0,
    "max": round(_ask_credits[0], 2) if _ask_n else 0.0,
    "max_calls": max((a["calls"] for a in _ask_list), default=0),
    "median_calls": (sorted(a["calls"] for a in _ask_list)[_ask_n // 2] if _ask_n else 0),
}
for _pct, _key in ((0.01, "top1pct"), (0.10, "top10pct")):
    _k = max(1, int(_ask_n * _pct)) if _ask_n else 0
    ask_stats[_key + "_n"] = _k
    ask_stats[_key + "_share"] = (round(sum(_ask_credits[:_k]) / _ask_total * 100, 1)
                                  if (_k and _ask_total) else 0.0)
print(f"  单次提问：{_ask_n} 次，中位 {ask_stats['median']} 积分，最贵 {ask_stats['max']} 积分；"
      f"最贵 {ask_stats.get('top10pct_n', 0)} 次占总量 {ask_stats.get('top10pct_share', 0)}%。", flush=True)

print("[3/4] 聚合指标 ...", flush=True)
days = sorted(by_day.keys())
day_list = []
for dk in days:
    b = by_day[dk]
    b["sessions"] = len(b["sessions"])
    b["thinking_sec"] = round(b["thinking_sec"], 1)
    b["credit"] = round(b["credit"], 2)
    day_list.append(b)




credit_source = "percall_local"
credit_note = ("逐次实测：每日 credit 由本地会话文件里每一次模型调用的 providerData.rawUsage.credit "
               "按该次调用发生的时间归集而成，是真实日值，并可再按模型拆分。"
               "改动之前生成的旧快照，其 credit 是按会话首次出现日挂出来的旧口径，已不再采用。")

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
    
    sid = sb["session_id"]
    # 会话 credit 优先取逐次实测值；本地无对话文件可解析的会话回退到数据库账本
    cr = pc_sess[sid] if sid in pc_sess else sess_credit.get(sid, {}).get("credit", 0)
    sb["credit"] = round(cr, 2)
    
    
    
    
    
    fd = sess_first.get(sid)
    sb["first_date"] = fd or ""
    sess_list.append(sb)

# 只有逐次明细、没有 trace 的会话。它们的 trace 已被 30 天清理，但 credit 来自本地对话
# 文件，仍然准确，同样要进入会话维度，否则看板的 credit 合计会漏掉这一部分。
_pc_first = {}
for _r in pc_list:
    _s, _dt = _r[4], _r[0]
    if _s and _dt and (_s not in _pc_first or _dt < _pc_first[_s]):
        _pc_first[_s] = _dt
for sid, _cr in pc_sess.items():
    if sid in by_session:
        continue
    _meta = sess_meta.get(sid, {}) or {}
    sess_list.append({
        "session_id": sid,
        "title": (_meta.get("title") or "")[:60],
        "status": _meta.get("status", ""),
        "models": "",
        "model": _meta.get("model", "") or "unknown",
        "requests": 0, "tokens": 0, "thinking_sec": 0.0,
        "credit": round(_cr, 2),
        "errors": 0,
        "first_date": _pc_first.get(sid) or sess_first.get(sid, ""),
    })
sess_list.sort(key=lambda x: x["tokens"], reverse=True)

# ---------- 逐日 credit：以逐次实测值为准 ----------
# 旧口径把整个会话的 credit 挂到会话首次出现日，会造出“当天 token 很少却扣了很多分”的假峰。
# 现改为逐次归日；没有逐次明细的日期不写入 credit，避免引入旧口径的错值。
pc_days = set(pc_day)
for _d, _c in pc_day.items():
    _row = by_day.get(_d)
    if _row is not None:
        _row["credit"] = round(_c, 2)
    else:
        by_day[_d] = {"date": _d, "requests": 0, "tokens": 0, "input": 0, "output": 0,
                      "cached": 0, "thinking_sec": 0.0, "credit": round(_c, 2),
                      "errors": 0, "sessions": 0}
# pc=True 表示该日 credit 来自逐次实测；--credit-xlsx 只补 pc 为假、即本地无明细的日期
for _k in by_day:
    by_day[_k]["pc"] = _k in pc_days
day_list[:] = [by_day[k] for k in sorted(by_day.keys())]

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
                # 覆盖层独有日期：本地既无 trace 也无逐次明细，只补总量字段。
                # credit 不采用旧快照值——旧快照的 credit 是按会话首现日挂出来的旧口径。
                row = {k: srow.get(k, 0) for k in ("requests", "tokens", "input", "output", "cached", "thinking_sec", "errors", "sessions")}
                row["credit"] = 0.0
                row["date"] = d
                by_day[d] = row
                restored_days.append(d)
            elif srow.get("tokens", 0) > cur.get("tokens", 0):
                for k in ("requests", "tokens", "input", "output", "cached", "thinking_sec", "errors", "sessions"):
                    if k in srow:
                        cur[k] = srow[k]
                # credit 只在没有逐次实测值的日期上由覆盖层补入，避免把旧口径错值盖回来
                if d not in pc_days and "credit" in srow:
                    cur["credit"] = srow["credit"]
                corrected_days.append(d)
        if restored_days or corrected_days:
            day_list[:] = [by_day[k] for k in sorted(by_day.keys())]
            print(f"  每日覆盖层应用：恢复 {len(restored_days)} 天、修正 {len(corrected_days)} 天", flush=True)
_valid_days = [k for k in by_day if re.match(r"^\d{4}-\d{2}-\d{2}$", k)]





xlsx_date_min = None
xlsx_date_max = None
if args.credit_xlsx:
    print("[3.5] 读取用量导出 xlsx (--credit-xlsx) ...", flush=True)
    xmap = read_credit_xlsx(args.credit_xlsx)
    if xmap:
        covered = 0
        for b in day_list:
            # 参考补充地位：只在本地没有逐次明细的日期上补入，不覆盖逐次实测值
            if b["date"] in xmap and b["date"] not in pc_days:
                b["credit"] = round(xmap[b["date"]], 2)
                covered += 1
        _overlap = len([d for d in xmap if d in pc_days])
        if covered:
            credit_note = (f"每日 credit 以逐次实测值为准；xlsx 仅作参考补充，"
                           f"另补入 {covered} 天本地无逐次明细的日期。xlsx 最多含 1 个月。")
        else:
            credit_note = (f"每日 credit 以逐次实测值为准；提供的 xlsx 中有 {_overlap} 天与本地明细重叠，"
                           f"未采用以免覆盖实测值。")
        xlsx_dates = sorted(xmap.keys())
        xlsx_date_min = xlsx_dates[0]
        xlsx_date_max = xlsx_dates[-1]

        if covered:
            print(f"  xlsx 参考补充：补入 {covered} 天本地无逐次明细的日期；窗口 {xlsx_date_min}~{xlsx_date_max}。", flush=True)
        else:
            print("  xlsx 与本地缺失日期不重叠，未采用（每日 credit 仍为逐次实测值）。", flush=True)
    else:
        print("  xlsx 读取失败或未识别到必要列，已跳过（不影响逐次实测的每日 credit）。", flush=True)





print("[2.6] 模型成本（逐次实测口径）...", flush=True)
# credit 与 token 都取自 providerData.rawUsage，同源同口径；模型归属按每次调用自带的模型名。
# 旧版按会话级模型标签分组、分母用 trace 的 totalTokens：两者口径不同，且跨模型的会话会把积分
# 算到标签头上（实测 hy3 被记 4145.89 积分、实际为 0；deepseek-v4-flash 少算 97%）。
_mc = {}
for _r in pc_list:
    if _r[3] == "unknown":
        continue
    _e = _mc.setdefault(_r[3], {"model": _r[3], "calls": 0, "miss": 0, "cached": 0,
                                "out": 0, "tokens": 0, "credit": 0.0})
    _e["calls"] += 1
    _e["miss"] += _r[7]
    _e["cached"] += _r[8]
    _e["out"] += _r[9]
    _e["tokens"] += _r[7] + _r[8] + _r[9]
    _e["credit"] += _r[10]

# 把当前生效费率联结进来，让同一张表既能看“花了多少”又能看“贵在哪一档”
_rate_cur = {e["model"]: e for e in model_rate if e["is_current"] and e["period"]}

# 展示门槛：token 达到最大模型 1/100 以上才进表。
# 用意是滤掉「只试过几次、不构成成本讨论」的长尾模型——它们会撑长表格，
# 又不影响任何成本结论。同一门槛同时用于下方优化建议，保证「表里出现的模型」
# 与「参与建议的模型」是同一批，不再出现表里有费率却不参与建议的割裂。
_model_top_tokens = max((_e["tokens"] for _e in _mc.values()), default=0)
_MODEL_TOKEN_FLOOR = _model_top_tokens / 100.0

model_cost_list = []
for _m, _e in _mc.items():
    if _e["tokens"] <= 0 or _e["tokens"] < _MODEL_TOKEN_FLOOR:
        continue
    _e["credit_per_million"] = round(_e["credit"] / _e["tokens"] * 1e6, 2)
    _e["cache_rate"] = (round(_e["cached"] / (_e["cached"] + _e["miss"]) * 100, 1)
                        if (_e["cached"] + _e["miss"]) else None)
    _e["tokens_per_call"] = round(_e["tokens"] / _e["calls"]) if _e["calls"] else 0
    _e["zero_credit"] = (_e["credit"] <= 0.0)
    _re = _rate_cur.get(_m)
    _e["rate"] = ({"period": _re["period"], "a": _re["a"], "b": _re["b"], "c": _re["c"],
                   "r2": _re["r2"], "n": _re["n"], "low_discount": _re["low_discount"],
                   "low_hours": _re["low_hours"], "low": _re["low"],
                   "reliable": _re["reliable"], "reason": _re["reason"]} if _re else None)
    model_cost_list.append(_e)
# 免费模型（积分恒为 0）排在最后；其余按每百万 token 积分升序，越低越省
model_cost_list.sort(key=lambda x: (x["credit"] <= 0, x["credit_per_million"]))


model_tips = []
substantial = [
    m for m in model_cost_list
    if m["model"] != "unknown"
    and not m["model"].lower().startswith("auto")
    and "preview" not in m["model"]
    and "agent" not in m["model"]
    and m["credit"] > 0.0
]
if len(substantial) >= 2:
    cheapest = min(substantial, key=lambda x: x["credit_per_million"])
    priciest = max(substantial, key=lambda x: x["credit_per_million"])
    if priciest["credit_per_million"] > 0:
        save = ((priciest["credit_per_million"] - cheapest["credit_per_million"])
                / priciest["credit_per_million"] * 100)
        if save >= 5:
            model_tips.append(
                f"在可比任务量下，切换至「{cheapest['model']}」"
                f"(每百万 token 积分={cheapest['credit_per_million']}) 相比「{priciest['model']}」"
                f"(每百万 token 积分={priciest['credit_per_million']}) 预计节省约 {save:.0f}% 的积分；"
                f"前提是两个模型处理的工作负载可互相迁移。"
                f"注意这里的数字是实际发生的平均值，受任务形态影响，不是模型单价。")

for m in model_cost_list:
    if m["zero_credit"]:
        model_tips.append(
            f"「{m['model']}」全程积分消耗为 0（token {m['tokens'] / 1e6:.1f}M），"
            f"处于限免期；不适合作为长期成本基准。")





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
    "version": "1.5.1",
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
    "by_session": sess_list[:500],
    "requests_sample": requests_trim,
    "requests_slim": requests_slim,
    "by_model_cost": model_cost_list,
    "model_rate": model_rate,
    "ask_top": ask_top,
    "ask_stats": ask_stats,
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
# 有逐次明细 credit、但本地已无请求级 token 明细的日期（trace 已清理）：
# credit 按实际发生日计入且数值正确，但这几天 token 缺失；需说明，否则会被误认为数据出错。
_credit_only = [b for b in day_list if b.get("pc") and not b.get("tokens")]
if _credit_only:
    _co_amount = round(sum(b.get("credit", 0) for b in _credit_only), 2)
    _co_pct = (round(_co_amount / total_credit * 100, 1) if total_credit else 0.0)
    _co_days = sorted(b["date"] for b in _credit_only)
    warnings.append({
        "type": "credit_only_days",
        "count": len(_credit_only),
        "amount": _co_amount,
        "pct": _co_pct,
        "days": _co_days,
        "detail": (f"以下 {len(_credit_only)} 天的 token 明细已被 WorkBuddy 的 30 天清理机制删除，"
                   f"积分不受影响（合计 {_co_amount}，占全部积分的 {_co_pct}%）。"
                   f"这几天在 token 图上偏低或为 0 属正常现象。日期：{'、'.join(_co_days)}。"),
    })
for sid, ent in sess_credit.items():
    if (sid in by_session) or (sid in pc_sess):
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
        "detail": f"另有 {_orphan_sessions} 个历史会话的 credit 合计 {round(_orphan_credit, 2)}，本地既无逐次明细也无请求记录，未计入每日趋势。",
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
    if "__CHART_JS_ASSET_TAG__" in tpl:
        tpl = tpl.replace("__CHART_JS_ASSET_TAG__", chart_tag)

    
    
    
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
        "sec_daily": "## 每日汇总", "sec_model": "## 按模型", "sec_sessions": "## 会话清单（前500）", "sec_calls": "## 调用明细",
        "sec_err_top": "## 错误-高频", "sec_err_type": "## 错误-按类型", "sec_err_tool": "## 错误-按工具",
        "sec_err_model": "## 错误-按模型", "sec_err_sess": "## 错误-按会话", "sec_err_samples": "## 错误-近期样本",
        "date": "日期", "reqs": "请求数", "token": "Token", "input": "输入", "output": "输出", "cache": "缓存命中",
        "think_sec": "思考秒", "credit": "credit", "errors": "错误", "sessions": "会话数",
        "model": "模型", "calls_n": "调用数", "eff": "效率tok/s", "session": "会话", "title_c": "标题",
        "think_min": "思考(分)", "status": "状态", "first_date": "首现日期",
        "time": "时间", "duration": "时长秒", "tools": "工具数", "prompt": "提问",
        "msg": "错误信息", "count": "次数", "share": "占比", "type": "类型", "tool": "工具", "top_err": "最高频错误",
        "sec_rate": "## 模型成本与费率（当前生效）", "sec_asks": "## 单次提问成本榜（前20）",
        "per_million": "每百万token积分", "miss_c": "非缓存输入", "cache_c": "缓存输入", "out_c": "输出",
        "low_disc": "低谷折扣", "r2": "拟合R²", "period": "生效期间", "asks_n": "模型调用数",
        "workspace": "工作区", "ask_cr": "提问积分", "share_cr": "占总积分",
        "sec_rate_hist": "## 模型费率全部阶段",
        "sec_ask_stat": "## 单次提问集中度",
        "reliable": "是否可作费率", "low_hours": "低谷时段", "reason": "不可用原因", "sample_n": "样本数",
        "item": "项目", "value": "数值", "amount": "贡献积分",
        "ask_total": "提问总数", "calls_of": "调用次数", "median_cr": "提问积分中位", "max_cr": "提问积分最大",
        "max_calls": "单次最多调用数", "median_calls": "调用数中位", "ask_tok": "提问token",
        "top1_n": "最贵1%次数", "top1_share": "最贵1%占比", "top10_n": "最贵10%次数", "top10_share": "最贵10%占比",
        "delta": "积分变化",
    },
    "en": {
        "title": "# WorkBuddy usage full export (generated at {ts})",
        "caliber": "# Cache-hit caliber: {c}",
        "sec_daily": "## Daily summary", "sec_model": "## By model", "sec_sessions": "## Sessions (top 500)", "sec_calls": "## Call details",
        "sec_err_top": "## Errors - top", "sec_err_type": "## Errors - by type", "sec_err_tool": "## Errors - by tool",
        "sec_err_model": "## Errors - by model", "sec_err_sess": "## Errors - by session", "sec_err_samples": "## Errors - recent samples",
        "date": "Date", "reqs": "Requests", "token": "Tokens", "input": "Input", "output": "Output", "cache": "Cache hit",
        "think_sec": "Think sec", "credit": "credit", "errors": "Errors", "sessions": "Sessions",
        "model": "Model", "calls_n": "Calls", "eff": "Eff tok/s", "session": "Session", "title_c": "Title",
        "think_min": "Think(min)", "status": "Status", "first_date": "First seen",
        "time": "Time", "duration": "Duration(s)", "tools": "Tools", "prompt": "Prompt",
        "msg": "Error message", "count": "Count", "share": "Share", "type": "Type", "tool": "Tool", "top_err": "Top error",
        "sec_rate": "## Model cost & rates (currently effective)", "sec_asks": "## Costliest single requests (top 20)",
        "per_million": "Credit per 1M tokens", "miss_c": "Uncached input", "cache_c": "Cached input", "out_c": "Output",
        "low_disc": "Off-peak discount", "r2": "Fit R2", "period": "Effective period", "asks_n": "Model calls",
        "workspace": "Workspace", "ask_cr": "Credit", "share_cr": "Share of total",
        "sec_rate_hist": "## Model rates - all periods",
        "sec_ask_stat": "## Prompt cost concentration",
        "reliable": "Usable as rate", "low_hours": "Off-peak hours", "reason": "Unavailable reason", "sample_n": "Samples",
        "item": "Item", "value": "Value", "amount": "Credit contribution",
        "ask_total": "Total prompts", "calls_of": "Calls", "median_cr": "Median prompt credit", "max_cr": "Max prompt credit",
        "max_calls": "Max calls in one prompt", "median_calls": "Median calls", "ask_tok": "Prompt tokens",
        "top1_n": "Top 1% count", "top1_share": "Top 1% share", "top10_n": "Top 10% count", "top10_share": "Top 10% share",
        "delta": "Credit change",
    },
}
_csv_lang = _detect_lang()
CSV_L = _CSV_LABELS[_csv_lang]


# ---------- xlsx 写出（仅用标准库 zipfile + XML，无第三方依赖） ----------
# 与全量 CSV 同源：CSV 的每个分区对应一个工作表。
def _write_xlsx(path, sheets, title=""):
    import zipfile
    from xml.sax.saxutils import escape as _xesc

    def _colref(i):
        s = ""
        i += 1
        while i:
            i, r = divmod(i - 1, 26)
            s = chr(65 + r) + s
        return s

    def _txt(v):
        s = "" if v is None else str(v)
        s = s.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
        return _xesc("".join(ch for ch in s if ch >= " "))

    def _cell_xml(ref, v):
        if v is None:
            return ""
        if isinstance(v, bool):
            return '<c r="%s" t="inlineStr"><is><t>%s</t></is></c>' % (ref, "TRUE" if v else "FALSE")
        if isinstance(v, (int, float)):
            if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
                return '<c r="%s" t="inlineStr"><is><t>%s</t></is></c>' % (ref, _txt(v))
            return '<c r="%s"><v>%s</v></c>' % (ref, repr(v) if isinstance(v, float) else v)
        s = _txt(v)
        if s == "":
            return ""
        return '<c r="%s" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (ref, s)

    # 工作表名净化：Excel 限制 31 字符、禁用 : \ / ? * [ ]、不区分大小写去重
    _used = set()
    _names = []
    for _sh in sheets:
        _nm = re.sub(r"[:\\/?*\[\]]", " ", str(_sh.get("name") or "")).strip().strip("'").strip() or "Sheet"
        _nm = _nm[:31]
        _base, _k = _nm, 2
        while _nm.lower() in _used:
            _suf = "_%d" % _k
            _nm = _base[:31 - len(_suf)] + _suf
            _k += 1
        _used.add(_nm.lower())
        _names.append(_nm)

    _sheet_xml = []
    for _sh in sheets:
        _rows = _sh.get("rows") or []
        _w = []
        for _r in _rows[:200]:
            for _i, _v in enumerate(_r[:64]):
                _s = "" if _v is None else str(_v)
                _n = sum(2 if ord(_c) > 127 else 1 for _c in _s)
                if _i >= len(_w):
                    _w.append(_n)
                elif _n > _w[_i]:
                    _w[_i] = _n
        _cols = ""
        if _w:
            _cols = "<cols>" + "".join(
                '<col min="%d" max="%d" width="%d" customWidth="1"/>'
                % (_i + 1, _i + 1, min(max(_n + 2, 8), 60)) for _i, _n in enumerate(_w)) + "</cols>"
        _body = []
        for _ri, _r in enumerate(_rows):
            _cs = "".join(_cell_xml(_colref(_ci) + str(_ri + 1), _v) for _ci, _v in enumerate(_r))
            _body.append('<row r="%d">%s</row>' % (_ri + 1, _cs) if _cs else '<row r="%d"/>' % (_ri + 1))
        _maxc = max((len(_r) for _r in _rows), default=0)
        _dim = ('A1:' + _colref(_maxc - 1) + str(max(len(_rows), 1))) if _maxc else 'A1'
        _sheet_xml.append(
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<dimension ref="%s"/>' % _dim +
            '<sheetViews><sheetView workbookViewId="0">'
            '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
            '</sheetView></sheetViews>'
            '<sheetFormatPr defaultRowHeight="15"/>' + _cols +
            '<sheetData>' + "".join(_body) + '</sheetData></worksheet>')

    _ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
           '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
           '<Default Extension="xml" ContentType="application/xml"/>'
           '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
           '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
           '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
           + "".join('<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                     % (_i + 1) for _i in range(len(_sheet_xml))) + '</Types>')

    _rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
             '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
             '</Relationships>')

    _core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
             ' xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/"'
             ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
             '<dc:title>%s</dc:title>'
             '<dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created>'
             '</cp:coreProperties>'
             % (_xesc(title or ""), datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")))

    _wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
           ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
           + "".join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (_xesc(_names[_i]), _i + 1, _i + 1)
                     for _i in range(len(_names))) + '</sheets></workbook>')

    _n = len(_sheet_xml)
    _wbrels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               + "".join('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>'
                         % (_i + 1, _i + 1) for _i in range(_n))
               + '<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                 % (_n + 1)
               + '</Relationships>')

    _styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
               '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
               '<fills count="2"><fill><patternFill patternType="none"/></fill>'
               '<fill><patternFill patternType="gray125"/></fill></fills>'
               '<borders count="1"><border/></borders>'
               '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
               '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
               '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
               '</styleSheet>')

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as _z:
        _z.writestr("[Content_Types].xml", _ct)
        _z.writestr("_rels/.rels", _rels)
        _z.writestr("docProps/core.xml", _core)
        _z.writestr("xl/workbook.xml", _wb)
        _z.writestr("xl/_rels/workbook.xml.rels", _wbrels)
        _z.writestr("xl/styles.xml", _styles)
        for _i, _sx in enumerate(_sheet_xml):
            _z.writestr("xl/worksheets/sheet%d.xml" % (_i + 1), _sx)


# ---------- 全量 CSV 同步输出（内置浏览器无法下载 blob 时直接取文件） ----------
try:
    def _csv_cell(v):
        s2 = "" if v is None else str(v)
        if re.search(r'[",\r\n]', s2):
            s2 = '"' + s2.replace('"', '""') + '"'
        return s2
    _csv_lines = []
    _xlsx_sheets = []      # 与 CSV 同源收集，按 "## 分区名" 切分成工作表
    _cur_sheet = [None]

    def _csv_push(arr):
        _csv_lines.append(",".join(_csv_cell(x) for x in arr))
        if arr == "":
            _cur_sheet[0] = None
        elif len(arr) == 1 and isinstance(arr[0], str) and arr[0].startswith("## "):
            _cur_sheet[0] = {"name": arr[0][3:].strip(), "rows": []}
            _xlsx_sheets.append(_cur_sheet[0])
        elif _cur_sheet[0] is not None:
            _cur_sheet[0]["rows"].append(list(arr))
    _csv_push([CSV_L["title"].format(ts=datetime.datetime.now().isoformat(timespec="seconds"))])
    _csv_push([CSV_L["caliber"].format(c=(summary.get("cache_caliber") or "cached/in"))])
    _csv_push("")
    _csv_push([CSV_L["sec_daily"]]); _csv_push([CSV_L["date"],CSV_L["reqs"],CSV_L["token"],CSV_L["input"],CSV_L["output"],CSV_L["cache"],CSV_L["think_sec"],CSV_L["credit"],CSV_L["errors"],CSV_L["sessions"]])
    for d in day_list:
        _csv_push([d.get("date"),d.get("requests"),d.get("tokens"),d.get("input"),d.get("output"),d.get("cached"),d.get("thinking_sec"),d.get("credit"),d.get("errors"),d.get("sessions")])
    _csv_push(""); _csv_push([CSV_L["sec_model"]]); _csv_push([CSV_L["model"],CSV_L["reqs"],CSV_L["token"],CSV_L["input"],CSV_L["output"],CSV_L["calls_n"],CSV_L["think_sec"],CSV_L["errors"],CSV_L["eff"]])
    for m in (out.get("by_model") or []):
        _csv_push([m.get("model"),m.get("requests"),m.get("tokens"),m.get("input"),m.get("output"),m.get("calls"),m.get("thinking_sec"),m.get("errors"),m.get("efficiency_tok_per_sec")])
    _tot_cr = (out.get("summary") or {}).get("total_credit") or 0
    _csv_push(""); _csv_push([CSV_L["sec_rate"]])
    _csv_push([CSV_L["model"],CSV_L["calls_n"],CSV_L["token"],CSV_L["credit"],CSV_L["per_million"],
               CSV_L["miss_c"],CSV_L["cache_c"],CSV_L["out_c"],CSV_L["low_disc"],CSV_L["r2"],
               CSV_L["period"],CSV_L["share_cr"]])
    for m in (out.get("by_model_cost") or []):
        _rt = m.get("rate") or {}
        _ld = _rt.get("low_discount")
        _low = ("半价" if (_ld and abs(_ld - 0.5) < 0.03) else ("约%.1f折" % (_ld * 10) if _ld else ""))
        _csv_push([m.get("model"), m.get("calls"), m.get("tokens"), round(m.get("credit", 0), 2),
                   m.get("credit_per_million"), _rt.get("a", ""), _rt.get("b", ""), _rt.get("c", ""),
                   _low, _rt.get("r2", ""), _rt.get("period", ""),
                   (round(m.get("credit", 0) / _tot_cr * 100, 1) if _tot_cr else "")])
    _csv_push(""); _csv_push([CSV_L["sec_rate_hist"]])
    _csv_push([CSV_L["model"],CSV_L["period"],CSV_L["sample_n"],CSV_L["miss_c"],CSV_L["cache_c"],CSV_L["out_c"],
               CSV_L["low_disc"],CSV_L["low_hours"],CSV_L["r2"],CSV_L["reliable"],CSV_L["reason"]])
    for _mr in (out.get("model_rate") or []):
        _mld = _mr.get("low_discount")
        _mlow = ("半价" if (_mld and abs(_mld - 0.5) < 0.03) else ("约%.1f折" % (_mld * 10) if _mld else ""))
        _csv_push([_mr.get("model"), _mr.get("period"), _mr.get("n"),
                   _mr.get("a", ""), _mr.get("b", ""), _mr.get("c", ""),
                   _mlow, _mr.get("low_hours", ""), _mr.get("r2", ""),
                   ("是" if _mr.get("reliable") else "否"), _mr.get("reason", "")])
    _csv_push(""); _csv_push([CSV_L["sec_sessions"]]); _csv_push([CSV_L["session"],CSV_L["title_c"],CSV_L["model"],CSV_L["reqs"],CSV_L["token"],CSV_L["think_min"],CSV_L["credit"],CSV_L["errors"],CSV_L["status"],CSV_L["first_date"]])
    for x in (out.get("by_session") or []):
        _th = round(x.get("thinking_sec", 0) / 60, 1) if x.get("thinking_sec") else 0
        _csv_push([x.get("session_id"),x.get("title"),x.get("model"),x.get("requests"),x.get("tokens"),_th,x.get("credit"),x.get("errors"),x.get("status"),x.get("first_date")])
    _csv_push(""); _csv_push([CSV_L["sec_asks"]])
    _csv_push([CSV_L["date"],CSV_L["time"],CSV_L["session"],CSV_L["title_c"],CSV_L["model"],
               CSV_L["asks_n"],CSV_L["token"],CSV_L["ask_cr"],CSV_L["share_cr"],CSV_L["workspace"]])
    for x in (out.get("ask_top") or []):
        _csv_push([x.get("date"), ("%02d:00" % x.get("hour") if x.get("hour") is not None and x.get("hour") >= 0 else ""),
                   x.get("sid"), x.get("session_title"), x.get("models"), x.get("calls"),
                   x.get("tokens", ""), x.get("credit"), x.get("share"), x.get("workspace")])
    _as = out.get("ask_stats") or {}
    if _as:
        _csv_push(""); _csv_push([CSV_L["sec_ask_stat"]])
        _csv_push([CSV_L["item"], CSV_L["value"]])
        for _k, _lab in (("count", "ask_total"), ("credit", "credit"), ("median", "median_cr"), ("max", "max_cr"),
                         ("max_calls", "max_calls"), ("median_calls", "median_calls"),
                         ("top1pct_n", "top1_n"), ("top1pct_share", "top1_share"),
                         ("top10pct_n", "top10_n"), ("top10pct_share", "top10_share")):
            if _k in _as:
                _csv_push([CSV_L[_lab], _as[_k]])
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
    _stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    OUT_CSVF = os.path.join(OUT_DIR, "usage-full-" + _stamp + ".csv")
    with open(OUT_CSVF, "w", encoding="utf-8-sig", newline="") as f:
        f.write("\r\n".join(_csv_lines))
    print("已生成全量 CSV:", os.path.basename(OUT_CSVF), f"（表头语言: {_csv_lang}；与看板同目录；内置浏览器无法下载时直接取用）", flush=True)
    if not _xlsx_sheets:
        print("全量 xlsx 未生成：没有收集到分区数据。", flush=True)
    else:
        OUT_XLSXF = os.path.join(OUT_DIR, "usage-full-" + _stamp + ".xlsx")
        try:
            _write_xlsx(OUT_XLSXF, _xlsx_sheets,
                        title=CSV_L["title"].format(ts=datetime.datetime.now().isoformat(timespec="seconds")))
            print("已生成全量 xlsx:", os.path.basename(OUT_XLSXF),
                  f"（{len(_xlsx_sheets)} 个工作表，与 CSV 分区一一对应）", flush=True)
        except Exception as e:
            print("全量 xlsx 生成失败（CSV 与看板不受影响）:", e, flush=True)
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
# 产物敏感性提醒：提问原文只做截断、不做打码；看板内展示用的那套打码不适用于数据文件。
# 每次运行结束都要让用户看到，不能只写在文档里。
print("\n⚠ 敏感性提示：本次生成的 usage-status.json / usage-status.js / 全量 CSV 与 xlsx 含会话标题与提问原文。"
      "提问原文只截断到 300 字、未脱敏，看板内展示所用的打码不适用于这些文件。"
      "分享或提交到仓库前请先检查。", flush=True)
