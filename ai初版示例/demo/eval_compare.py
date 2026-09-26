# -*- coding: utf-8 -*-
"""
eval_compare.py —— 对照实验:LLM 提取路径 vs 规则提取路径(消融实验)

对 eval_set 每份公告:
  · 规则路径:现场运行(快速、无 API 调用)
  · LLM 路径:读取 eval_run.py 已产出的 output/<stem>.result.json
产出 output/eval/compare_llm_vs_rules.json 与控制台对照表 ——
回答"LLM 到底比纯规则强在哪"(论文消融实验章节素材)。

用法:先跑 eval_run.py(产生 LLM 结果),再跑 python eval_compare.py
"""
import glob
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import extractor
import parser as docparser
import validator

DEMO_DIR = os.path.dirname(os.path.abspath(__file__))
EVAL_DIR = os.path.join(DEMO_DIR, "data", "eval_set")
RUN_OUT = os.path.join(DEMO_DIR, "output")
OUT_DIR = os.path.join(RUN_OUT, "eval")


def _stats_from_result(result):
    n_pass = sum(1 for c in result.checks if c.passed)
    cov = next((c.computed for c in result.checks if c.name == "原文溯源覆盖率"), None)
    return {"records": len(result.records), "cumulative": len(result.cumulative),
            "checks_passed": n_pass, "checks_total": len(result.checks),
            "grounding": cov}


def run_rule_path(path):
    """对一份公告只跑规则路径,返回统计"""
    parsed = docparser.parse_pdf(path)
    tables = docparser.extract_tables(path)
    full_text = "\n".join(parsed["pages"])
    raw = extractor.extract_by_rules(full_text, tables,
                                     ocr_lines=parsed.get("lines"))
    result = extractor.build_result(raw, os.path.basename(path), parsed["method"])
    validator.run_checks(result, tables, full_text=full_text)
    return _stats_from_result(result)


def main():
    files = sorted(glob.glob(os.path.join(EVAL_DIR, "*.pdf")))
    rows = []
    for path in files:
        stem = os.path.splitext(os.path.basename(path))[0]
        row = {"file": os.path.basename(path)}
        # LLM 路径:读已有结果
        p = os.path.join(RUN_OUT, stem + ".result.json")
        if os.path.isfile(p):
            d = json.load(open(p, encoding="utf-8"))
            n_pass = sum(1 for c in d["checks"] if c["passed"])
            cov = next((c.get("computed") for c in d["checks"]
                        if c["name"] == "原文溯源覆盖率"), None)
            row["llm"] = {"records": len(d["records"]),
                          "cumulative": len(d["cumulative"]),
                          "checks_passed": n_pass,
                          "checks_total": len(d["checks"]),
                          "grounding": cov}
        else:
            row["llm"] = None
        # 规则路径:现场跑
        try:
            row["rules"] = run_rule_path(path)
        except Exception as e:
            row["rules"] = {"error": str(e)[:100]}
        rows.append(row)
        l = row["llm"] or {}
        r = row["rules"]
        print(f"{stem[:34]:36s} LLM:{l.get('checks_passed','?')}/{l.get('checks_total','?')}"
              f" 记录{l.get('records','?')} | 规则:{r.get('checks_passed','?')}"
              f"/{r.get('checks_total','?')} 记录{r.get('records','?')}", flush=True)

    # 汇总
    def _avg(key_fn):
        vals = [key_fn(r) for r in rows]
        vals = [v for v in vals if isinstance(v, (int, float))]
        return round(sum(vals) / len(vals), 2) if vals else None

    summary = {
        "n_docs": len(rows),
        "llm": {
            "avg_records": _avg(lambda r: (r["llm"] or {}).get("records")),
            "avg_cumulative": _avg(lambda r: (r["llm"] or {}).get("cumulative")),
            "check_full_pass": sum(1 for r in rows if r["llm"]
                                   and r["llm"]["checks_passed"] == r["llm"]["checks_total"]),
        },
        "rules": {
            "avg_records": _avg(lambda r: r["rules"].get("records")),
            "avg_cumulative": _avg(lambda r: r["rules"].get("cumulative")),
            "check_full_pass": sum(1 for r in rows if r["rules"].get("checks_total")
                                   and r["rules"]["checks_passed"] == r["rules"]["checks_total"]),
        },
    }
    out = os.path.join(OUT_DIR, "compare_llm_vs_rules.json")
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, ensure_ascii=False, indent=2)
    print(f"\n=== 对照实验汇总({summary['n_docs']} 份) ===")
    print(f"LLM 路径: 平均提取记录 {summary['llm']['avg_records']} 条/份, "
          f"累计表 {summary['llm']['avg_cumulative']} 条/份, 校验全过 {summary['llm']['check_full_pass']} 份")
    print(f"规则路径: 平均提取记录 {summary['rules']['avg_records']} 条/份, "
          f"累计表 {summary['rules']['avg_cumulative']} 条/份, 校验全过 {summary['rules']['check_full_pass']} 份")
    print(f"明细: {out}")


if __name__ == "__main__":
    main()
