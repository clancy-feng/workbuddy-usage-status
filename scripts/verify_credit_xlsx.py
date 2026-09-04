#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_credit_xlsx.py — 限免模型核实工具（只读，不修改任何产物）

用途：
  把 WorkBuddy 官方用量导出 xlsx（https://www.workbuddy.cn/profile/plans-usage）
  的 credit 按【模型】汇总，输出每模型官方精确 credit，用于和本地估算对比，
  判断某个模型（如 hy3）是否“计量了但没真扣费”（phantom credit）。

用法：
  python verify_credit_xlsx.py 路径/usage-export.xlsx

输出：
  - 每模型 官方 credit 合计（按 xlsx 中“模型”列分组；若 xlsx 无模型列则只给总计）
  - 覆盖的日期范围
  - 与本地 hy3 估算（约 7127.28）的对比提示

说明：
  本脚本只做“核实”，不修改 dashboard / usage_extractor / 标注逻辑。
  依赖标准库（zipfile / xml.etree），无需 openpyxl。
"""
import sys, os
import zipfile
import xml.etree.ElementTree as ET
import re
from collections import defaultdict

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

def col_letter(ref):
    m = re.match(r"([A-Z]+)", ref or "")
    return m.group(1) if m else None

def read_xlsx(path):
    """返回 (rows, header) ；rows 为 [{col: val}, ...]"""
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        try:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.iter(NS + "si"):
                shared.append("".join(t.text or "" for t in si.iter(NS + "t")))
        except Exception:
            pass
    sheet_path = "xl/worksheets/sheet1.xml"
    if sheet_path not in z.namelist():
        cands = [n for n in z.namelist() if n.startswith("xl/worksheets/sheet")]
        if not cands:
            raise RuntimeError("xlsx 未找到 worksheet")
        sheet_path = sorted(cands)[0]
    root = ET.fromstring(z.read(sheet_path))
    rows = []
    for row in root.iter(NS + "row"):
        cells = {}
        for c in row.iter(NS + "c"):
            ref = c.get("r")
            col = col_letter(ref)
            t = c.get("t")
            v = c.find(NS + "v")
            isn = c.find(NS + "is")
            val = None
            if t == "s" and v is not None:
                try:
                    val = shared[int(v.text)]
                except Exception:
                    val = None
            elif isn is not None:
                val = "".join(tt.text or "" for tt in isn.iter(NS + "t"))
            elif v is not None:
                val = v.text
            if col:
                cells[col] = val
        rows.append(cells)
    return rows

def find_col(header, names_needed):
    for col, val in header.items():
        if val and any(nm.lower() in str(val).lower() for nm in names_needed):
            return col
    return None

MODEL_COL_NAMES = ["模型", "model", "modelname", "模型名称"]
CREDIT_COL_NAMES = ["积分消耗", "credit", "消耗积分", "cost"]
TIME_COL_NAMES = ["时间", "time", "日期", "date"]

def main():
    if len(sys.argv) < 2:
        print("用法: python verify_credit_xlsx.py 路径/usage-export.xlsx")
        sys.exit(1)
    path = sys.argv[1]
    if not os.path.isfile(path):
        print("文件不存在:", path)
        sys.exit(1)

    try:
        rows = read_xlsx(path)
    except Exception as e:
        print("xlsx 读取失败:", e)
        sys.exit(1)
    if not rows:
        print("xlsx 为空")
        sys.exit(1)

    header = rows[0]
    model_c = find_col(header, MODEL_COL_NAMES)
    credit_c = find_col(header, CREDIT_COL_NAMES)
    time_c = find_col(header, TIME_COL_NAMES)

    print("检测到列:")
    print(f"  模型列 : {header.get(model_c) if model_c else '(未找到)'}")
    print(f"  credit列: {header.get(credit_c) if credit_c else '(未找到)'}")
    print(f"  时间列 : {header.get(time_c) if time_c else '(未找到)'}")

    if not credit_c:
        print("缺少 credit/积分消耗 列，无法汇总。")
        sys.exit(1)

    by_model = defaultdict(float)
    by_model_count = defaultdict(int)
    total = 0.0
    dates = []
    no_model = 0
    for cells in rows[1:]:
        cv = cells.get(credit_c)
        if cv in (None, ""):
            continue
        try:
            cval = float(str(cv).replace(",", ""))
        except Exception:
            continue
        total += cval
        mv = cells.get(model_c) if model_c else None
        if mv:
            by_model[mv] += cval
            by_model_count[mv] += 1
        else:
            no_model += 1
        if time_c:
            tv = cells.get(time_c)
            if tv:
                d = re.match(r"(\d{4}-\d{2}-\d{2})", str(tv))
                if d:
                    dates.append(d.group(1))

    print("\n=== 每模型 官方 credit 合计（xlsx 精确值）===")
    if by_model:
        for m in sorted(by_model, key=lambda x: -by_model[x]):
            print(f"  {m:28s}  credit={by_model[m]:12.2f}   请求数={by_model_count[m]}")
    else:
        print("  (xlsx 中无模型列，无法按模型拆分)")
    print(f"\n总计 credit = {total:.2f}")
    if dates:
        print(f"覆盖日期范围: {min(dates)} ~ {max(dates)}  ({len(set(dates))} 天)")
    if no_model:
        print(f"注: {no_model} 行无模型信息，未计入按模型拆分。")

    # 与本地 hy3 估算对比
    local_hy3 = 7127.28
    print("\n=== 与本地估算对比 ===")
    print(f"  本地 hy3 估算 credit ≈ {local_hy3:.2f}（12 个非零会话合计）")
    off = by_model.get("hy3")
    if off is not None:
        diff = off - local_hy3
        print(f"  官方 hy3 credit   = {off:.2f}")
        if off < 1.0:
            print("  → 官方≈0：说明本地 7000+ 是 phantom（计量了但没真扣），hy3 确属限免。")
        elif abs(diff) < local_hy3 * 0.2:
            print("  → 官方与本地接近：hy3 确有约 7000 真实扣费，并非限免（可能是记忆有误/免费期已结束）。")
        else:
            print(f"  → 官方与本地差异较大（差 {diff:.2f}）：需进一步核对口径。")
    else:
        print("  xlsx 中未找到 hy3 行或模型列，请确认导出时包含了 hy3 用量。")

if __name__ == "__main__":
    main()
