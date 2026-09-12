# -*- coding: utf-8 -*-
"""
extractor.py —— 提取层（对应竞赛要求的 Tool / Prompt 模块）
两条提取路径：
1) LLM 提取（优先）：调用国产大模型 DeepSeek（OpenAI 兼容协议），
   Prompt 见 prompts/pledge_extract.md，输出受 JSON 结构约束；
2) 规则兜底：未配置 DEEPSEEK_API_KEY 或 LLM 调用失败时，
   用正则 + 表格结构解析完成提取，保证管线任何时候都可运行、可复现。
"""
import json
import os
import re

from schema import (DocumentMeta, PledgeRecord, CumulativePledge,
                    RiskItem, ExtractionResult)

PROMPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "prompts", "pledge_extract.md")


# ------------------------------------------------------------------ LLM 路径
def extract_with_llm(full_text: str, tables: list, trace=None) -> dict:
    """调用 DeepSeek（OpenAI 兼容协议）按 Prompt 完成结构化提取，返回 dict"""
    import requests
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    if not api_key:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY，自动回退规则提取")
    prompt = open(PROMPT_PATH, encoding="utf-8").read().replace("{{TEXT}}", full_text)
    if trace:
        trace.log("tool_call", tool="extract_with_llm", model=model,
                  api=base, prompt_chars=len(prompt))
    resp = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model,
              "messages": [{"role": "user", "content": prompt}],
              "temperature": 0,
              "response_format": {"type": "json_object"}},
        timeout=180)
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    if trace:
        trace.log("llm_response", chars=len(content), preview=content[:200])
    return _parse_json_lenient(content)


def _parse_json_lenient(content: str) -> dict:
    """宽容解析 LLM 输出（容忍 markdown 围栏等杂质）"""
    m = re.search(r"\{.*\}", content, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return json.loads(content)


# ------------------------------------------------------------------ 规则路径
def extract_by_rules(full_text: str, tables: list, trace=None,
                     ocr_lines: list = None) -> dict:
    """规则兜底提取：正则（元信息、风险提示）+ 表格结构解析（质押/解押/展期/累计）。
    扫描件（无 pdfplumber 表格）时用 OCR 行框坐标重建表格结构。"""
    if not tables and ocr_lines:
        from ocr_tables import reconstruct
        tables = []
        for i, page_lines in enumerate(ocr_lines):
            if page_lines:
                tables.extend(reconstruct(page_lines, i + 1))
        if trace:
            trace.log("ocr_table_reconstruct", tables=len(tables))
    if trace:
        trace.log("tool_call", tool="extract_by_rules", chars=len(full_text),
                  tables=len(tables))
    meta = _meta_by_rules(full_text)
    records, cumulative, table_totals = _tables_by_rules(tables)
    risk = _risk_by_rules(full_text)
    raw = {"doc_meta": meta, "records": records,
           "cumulative": cumulative, "risk_items": risk}
    raw["_table_totals"] = table_totals  # 供校验层做"合计行加总"校验
    return raw


def _meta_by_rules(full_text: str) -> dict:
    def re1(*pats):
        for p in pats:
            m = re.search(p, full_text)
            if m:
                return m.group(1)
        return None

    sec_code = re1(r"证券代码[：:]\s*(\d{6})")
    sec_name = re1(r"证券简称[：:]\s*(\S+)")
    anno_no = re1(r"公告编号[：:]\s*(\S+)")
    # 公告日期：正文中最后一次出现的日期（特此公告段落在文末；格式 2026.9.8 / 2026年9月8日 / 2026 年 9 月 8 日）
    dates = re.findall(r"(\d{4})\s*[年.]\s*(\d{1,2})\s*[月.]\s*(\d{1,2})\s*日?",
                       full_text)
    ann_date = None
    if dates:
        y, m, d = (int(x) for x in dates[-1])
        ann_date = f"{y:04d}-{m:02d}-{d:02d}"
    # 标题与公告类型
    title = ""
    for line in full_text.splitlines():
        line = line.strip()
        if "关于" in line and line.endswith("公告"):
            title = line
            break
    if "展期" in title:
        doc_type = "质押展期公告"
    elif "质押" in title and "解除质押" in title:
        doc_type = "质押及解除质押公告"
    elif "解除质押" in title:
        doc_type = "解除质押公告"
    else:
        doc_type = "质押公告"
    return {"sec_code": sec_code, "sec_name": sec_name,
            "announcement_no": anno_no, "announcement_date": ann_date,
            "doc_type": doc_type, "title": title}


# 表头关键词 → 标准字段名（按顺序匹配，先命中的生效）
_RECORD_COLS = [
    ("股东名称", "pledgor"),
    ("是否为", "is_controller"),
    ("解除质押股份数量", "shares"),
    ("本次质押股份数量", "shares"),
    ("本次质押股数", "shares"),
    ("占其所持", "pct_of_held"),
    ("占公司总", "pct_of_total"),
    ("限售", "is_restricted"),
    ("补充质押", "is_supplementary"),
    ("补充", "is_supplementary"),
    ("原质押到期日", "original_end_date"),
    ("原质押起始日", "pledge_start_date"),
    ("质押起始日", "pledge_start_date"),
    ("质押起始", "pledge_start_date"),  # OCR 碎片可能缺"日"字
    ("起始日", "pledge_start_date"),
    ("展期后", "pledge_end_date"),
    ("(?<!原)质押到期日", "pledge_end_date"),
    ("解除日期", "release_date"),
    ("质权人", "pledgee"),
    ("资金用途", "purpose"),
    ("质押用途", "purpose"),
    ("质押用", "purpose"),  # OCR 碎片可能缺"途"字
    ("用途", "purpose"),
]

_CUMULATIVE_COLS = [
    ("股东名称", "shareholder"),
    ("持股数量", "holding_shares"),
    ("持股比例", "holding_pct"),
    ("本次质押前", "pre_pledge_shares"),
    ("本次质押后", "post_pledge_shares"),
    ("占其所持", "pct_of_held"),
    ("占公司总", "pct_of_total"),
]


def _flatten_header(rows):
    """多级表头按列纵向拼接：把前若干表头行的单元格合并为每列一个完整名称"""
    ncols = max(len(r) for r in rows)
    heads = [""] * ncols
    for r in rows:
        for c in range(ncols):
            cell = (r[c] or "").replace("\n", "") if c < len(r) else ""
            heads[c] += cell
    return heads


def _classify_table(heads):
    joined = "".join(heads)
    if "解除日期" in joined or "解除质押股份数量" in joined:
        return "release"
    if "展期" in joined and "累计" not in joined:
        return "extension"
    if "本次质押股数" in joined or "本次质押股份数量" in joined:
        return "new_pledge"
    if "持股数量" in joined and "本次质押前" in joined:
        return "cumulative"
    return None


def _map_cols(heads, col_defs):
    """表头关键词（正则）→ 列索引。返回 {标准字段名: 列索引}"""
    mapping = {}
    for c, head in enumerate(heads):
        for kw, field in col_defs:
            if field in mapping:  # 每个字段只取第一个匹配列
                continue
            if re.search(kw, head):
                mapping[field] = c
    return mapping


def _parse_shares(s):
    """'51,000,000' / '5000万' / '1.2亿' → int"""
    if s is None:
        return None
    s = str(s).replace(",", "").strip()
    m = re.match(r"^([\d.]+)\s*([万亿])$", s)
    if m:
        v = float(m.group(1))
        return int(v * (100000000 if m.group(2) == "亿" else 10000))
    m = re.match(r"^[\d.]+$", s)
    return int(float(s)) if m else None


def _parse_pct(s):
    """'30.67%' / '8.0030' → float"""
    if s is None:
        return None
    s = str(s).replace("%", "").strip()
    m = re.match(r"^[\d.]+$", s)
    return float(s) if m else None


def _parse_date(s):
    """'2026.9.4' / '2023年3月7日' / '2023 年 3 月 7 日' → 'YYYY-MM-DD'"""
    if s is None:
        return None
    m = re.search(r"(\d{4})\s*[年.]\s*(\d{1,2})\s*[月.]\s*(\d{1,2})\s*日?", str(s))
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return str(s).strip()


def _parse_bool(s):
    if s is None:
        return None
    s = str(s).strip()
    if s.startswith("是"):
        return True
    if s.startswith("否"):
        return False
    return None


_DATA_CELL = re.compile(r"^(是|否)$|^[\d,]+$|^[\d.]+%?$|\d{4}\s*[年.]")


def _is_data_row(row):
    """表头行通常不含数字/日期/是否值，数据行必含至少一个"""
    return any(_DATA_CELL.search((c or "").strip()) for c in row)


def _header_depth(rows):
    """判定表头占几行：第 2 行是数据行则表头 1 行，否则 2 行"""
    if len(rows) >= 2 and _is_data_row(rows[1]):
        return 1
    return 2


def _tables_by_rules(tables):
    records, cumulative, table_totals = [], [], []
    for tb in tables:
        rows = tb["rows"]
        if not rows:
            continue
        depth = _header_depth(rows)
        heads = _flatten_header(rows[:depth])
        kind = _classify_table(heads)
        if kind is None:
            continue
        data_rows = [r for r in rows[depth:] if r and (r[0] or "").strip() != "合计"
                     and any((c or "").strip() for c in r)]
        total_rows = [r for r in rows if r and (r[0] or "").strip() == "合计"]
        if kind == "cumulative":
            cmap = _map_cols(heads, _CUMULATIVE_COLS)
            for r in data_rows:
                rec = {f: _cell(r, c) for f, c in cmap.items()}
                rec["shareholder"] = _cell(r, cmap.get("shareholder"))
                cumulative.append(_normalize_cumulative(rec, r))
            if total_rows:
                table_totals.append({"kind": kind, "total_row": total_rows[0],
                                     "col_map": cmap, "data_rows": data_rows})
        else:
            rmap = _map_cols(heads, _RECORD_COLS)
            for r in data_rows:
                rec = {f: _cell(r, c) for f, c in rmap.items()}
                rec["record_type"] = {"release": "解除质押",
                                      "extension": "展期",
                                      "new_pledge": "新增质押"}[kind]
                records.append(_normalize_record(rec, r))
            if total_rows:
                table_totals.append({"kind": kind, "total_row": total_rows[0],
                                     "col_map": rmap, "data_rows": data_rows})
    return records, cumulative, table_totals


def _cell(row, idx):
    if idx is None or idx >= len(row):
        return None
    v = row[idx]
    return (v or "").replace("\n", "").strip() or None


def _row_evidence(row):
    return " | ".join((c or "").replace("\n", "").strip() for c in row if (c or "").strip())


def _normalize_record(rec, raw_row):
    """数值/日期/布尔格式规范化（对应'格式规范性'考察点）"""
    return {
        "record_type": rec.get("record_type"),
        "pledgor": rec.get("pledgor"),
        "is_controller": _parse_bool(rec.get("is_controller")),
        "pledgee": rec.get("pledgee"),
        "shares": _parse_shares(rec.get("shares")),
        "pct_of_held": _parse_pct(rec.get("pct_of_held")),
        "pct_of_total": _parse_pct(rec.get("pct_of_total")),
        "is_restricted": _parse_bool(rec.get("is_restricted")),
        "is_supplementary": _parse_bool(rec.get("is_supplementary")),
        "pledge_start_date": _parse_date(rec.get("pledge_start_date")),
        "pledge_end_date": _parse_date(rec.get("pledge_end_date")),
        "original_end_date": _parse_date(rec.get("original_end_date")),
        "release_date": _parse_date(rec.get("release_date")),
        "purpose": rec.get("purpose"),
        "evidence": _row_evidence(raw_row),
    }


def _normalize_cumulative(rec, raw_row):
    return {
        "shareholder": rec.get("shareholder"),
        "holding_shares": _parse_shares(rec.get("holding_shares")),
        "holding_pct": _parse_pct(rec.get("holding_pct")),
        "pre_pledge_shares": _parse_shares(rec.get("pre_pledge_shares")),
        "post_pledge_shares": _parse_shares(rec.get("post_pledge_shares")),
        "pct_of_held": _parse_pct(rec.get("pct_of_held")),
        "pct_of_total": _parse_pct(rec.get("pct_of_total")),
        "evidence": _row_evidence(raw_row),
    }


def _risk_by_rules(full_text: str) -> list:
    """风险提示段：未来半年/一年内到期的质押股数与融资余额"""
    items = []
    for m in re.finditer(
            r"未来(半年内|一年内)到期的质押股份累计\s*([\d,]+)\s*股"
            r"[^。]{0,150}?融资余额\s*([\d,]+)\s*元", full_text):
        items.append({
            "horizon": "未来" + m.group(1),
            "shares": int(m.group(2).replace(",", "")),
            "finance_balance": int(m.group(3).replace(",", "")),
            "evidence": m.group(0)[:120],
        })
    return items


# ------------------------------------------------------------------ 结果组装
def build_result(raw: dict, file_name: str, parse_method: str) -> ExtractionResult:
    """把 LLM / 规则输出组装为符合 Schema 的结果对象（pydantic 强校验）"""
    meta = raw.get("doc_meta") or {}
    doc_meta = DocumentMeta(
        file_name=file_name,
        doc_type=meta.get("doc_type") or "质押公告",
        sec_code=meta.get("sec_code"),
        sec_name=meta.get("sec_name"),
        announcement_no=meta.get("announcement_no"),
        announcement_date=meta.get("announcement_date"),
        parse_method=parse_method,
    )
    result = ExtractionResult(
        doc_meta=doc_meta,
        records=[PledgeRecord(**r) for r in raw.get("records") or []],
        cumulative=[CumulativePledge(**c) for c in raw.get("cumulative") or []],
        risk_items=[RiskItem(**i) for i in raw.get("risk_items") or []],
    )
    result.__dict__["_table_totals"] = raw.get("_table_totals") or []
    return result
