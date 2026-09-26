# -*- coding: utf-8 -*-
"""
grounding.py —— 原文溯源校验（对应竞赛"结果一致性"考察点，抗幻觉）
把提取结果的关键字段值逐一回原文（逐页）搜索：
  找得到 → 记录页码与证据片段；找不到 → 计入 missing，拉低覆盖率。
LLM 幻觉出的字段（原文中不存在的值）在该机制下无处遁形。
"""
import re


def _norm(s: str) -> str:
    """归一化：去空白符与千分位逗号，统一全角逗号"""
    return re.sub(r"[\s,，'']+", "", s or "")


def _variants(field: str, value) -> list:
    """同一字段值在公告原文中可能出现的写法变体"""
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, int):
        return [f"{value:,}", str(value)]
    if isinstance(value, float):
        vs = [f"{value}%", f"{value}"]
        if float(value).is_integer():
            vs.append(str(int(value)))
        return vs
    s = str(value).strip()
    if not s:
        return []
    vs = [s]
    # 日期变体：YYYY-MM-DD → YYYY.M.D / YYYY年M月D日
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        vs += [f"{y}.{mo}.{d}", f"{y}年{mo}月{d}日"]
    return vs


# 各类对象参与溯源的字段（过短/低区分度的字段不查，避免误命中）
_GROUND_FIELDS = {
    "records": ["pledgor", "pledgee", "shares", "pct_of_held",
                "pct_of_total", "pledge_start_date", "pledge_end_date",
                "release_date", "purpose"],
    "cumulative": ["shareholder", "holding_shares", "pre_pledge_shares",
                   "post_pledge_shares"],
    "risk_items": ["shares", "finance_balance"],
}


def _search(pages_norm: list, needle: str):
    """在逐页归一化文本中找 needle，返回 (页码, 命中片段) 或 (None, None)"""
    n = _norm(needle)
    if len(n) < 2:  # 过短容易误命中，跳过
        return None, None
    for i, page in enumerate(pages_norm):
        if n in page:
            return i + 1, n
    return None, None


def run_grounding(result, pages: list, trace=None) -> dict:
    """对提取结果逐字段溯源，返回 {'coverage': float, 'found': int,
    'missing': int, 'items': [...]}"""
    pages_norm = [_norm(p) for p in pages]
    items, found, missing = [], 0, 0
    for section, fields in _GROUND_FIELDS.items():
        for idx, obj in enumerate(getattr(result, section, []) or []):
            item = {"section": section, "index": idx,
                    "label": getattr(obj, "pledgor", None)
                             or getattr(obj, "shareholder", None)
                             or getattr(obj, "horizon", None),
                    "found": {}, "missing": []}
            for f in fields:
                v = getattr(obj, f, None)
                if v is None:
                    continue
                variants = _variants(f, v)
                if not any(len(_norm(x)) >= 2 for x in variants):
                    continue  # 值为 0 或单字符等无法可靠搜索的,不参与统计
                for variant in variants:
                    page, snippet = _search(pages_norm, variant)
                    if page:
                        item["found"][f] = {"page": page, "snippet": snippet[:80]}
                        found += 1
                        break
                else:
                    item["missing"].append({f: v})
                    missing += 1
            if item["found"] or item["missing"]:
                items.append(item)
    total = found + missing
    report = {"coverage": found / total if total else 1.0,
              "found": found, "missing": missing, "items": items}
    if trace:
        trace.log("grounding", coverage=round(report["coverage"], 4),
                  found=found, missing=missing, items=items)
    return report
