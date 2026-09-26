# -*- coding: utf-8 -*-
"""
eval_run.py —— 批量评测脚本（竞赛"评测方案"的载体）

对 data/eval_set/ 中的全部公告批量运行提取管线，汇总：
  提取来源(llm/rules)、一致性校验通过率、原文溯源覆盖率，
产出 output/eval/eval_summary.json 与控制台报告 —— 用未见样本检验泛化能力。

用法:
  python eval_run.py                # 评测 eval_set 下全部 PDF
"""
import glob
import json
import os
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tracing import Trace
import run as runner

EVAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "data", "eval_set")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "output", "eval")


def main():
    files = sorted(glob.glob(os.path.join(EVAL_DIR, "*.pdf")))
    if not files:
        print("eval_set 为空,请先运行 eval_collect.py")
        return
    os.makedirs(OUT_DIR, exist_ok=True)
    rows = []
    for i, path in enumerate(files, 1):
        stem = os.path.splitext(os.path.basename(path))[0]
        trace = Trace(os.path.join(OUT_DIR, stem + ".trace.jsonl"))
        print(f"[{i}/{len(files)}] {stem[:40]}", flush=True)
        try:
            result, source, _ = runner.run_document(path, trace)
            n_pass = sum(1 for c in result.checks if c.passed)
            cov = next((c.computed for c in result.checks
                        if c.name == "原文溯源覆盖率"), None)
            rows.append({
                "file": os.path.basename(path),
                "sec_code": result.doc_meta.sec_code,
                "sec_name": result.doc_meta.sec_name,
                "doc_type": result.doc_meta.doc_type,
                "parse_method": result.doc_meta.parse_method,
                "extract_source": source,
                "checks_passed": n_pass, "checks_total": len(result.checks),
                "grounding_coverage": cov,
                "records": len(result.records),
            })
            print(f"    → {source} | 校验 {n_pass}/{len(result.checks)} | "
                  f"溯源 {cov} | 记录 {len(result.records)} 条", flush=True)
        except Exception as e:
            rows.append({"file": os.path.basename(path),
                         "error": str(e)[:150]})
            print(f"    ✗ 失败: {str(e)[:100]}", flush=True)
    summary = {
        "eval_time": datetime.now().isoformat(),
        "total_docs": len(files),
        "results": rows,
        "stats": {
            "ran_ok": sum(1 for r in rows if "error" not in r),
            "llm_source": sum(1 for r in rows if r.get("extract_source") == "llm"),
            "full_checks_pass": sum(1 for r in rows
                                    if r.get("checks_total") and
                                    r["checks_passed"] == r["checks_total"]),
        },
    }
    out = os.path.join(OUT_DIR, "eval_summary.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    s = summary["stats"]
    print(f"\n=== 评测汇总: {s['ran_ok']}/{len(files)} 跑通, "
          f"LLM 路径 {s['llm_source']}, 校验全过 {s['full_checks_pass']} ===")
    print(f"明细: {out}")


if __name__ == "__main__":
    main()
