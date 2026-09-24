# -*- coding: utf-8 -*-
"""
run.py —— 编排入口（对应竞赛要求的"智能体编排"模块）
用法：
  python run.py <PDF文件路径>           # 文本层 PDF 公告
  python run.py <扫描件图片目录路径>      # 扫描件（逐页 OCR）
流程：解析 → 提取（LLM 优先，规则兜底）→ 校验 → 落盘 + 控制台报告
环境变量（可选）：
  DEEPSEEK_API_KEY   DeepSeek API Key（配置后启用 LLM 提取路径）
  DEEPSEEK_BASE_URL  默认 https://api.deepseek.com
  DEEPSEEK_MODEL     默认 deepseek-chat
"""
import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

import extractor
import parser as docparser
import validator
from tracing import Trace

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


def run_document(path, trace):
    """对一份公告执行完整管线，返回 (ExtractionResult, 提取来源, 输出JSON路径)"""
    stem = os.path.splitext(os.path.basename(path))[0]
    if os.path.isdir(path):
        trace.log("file_access", action="open_dir", path=path)
        imgs = [os.path.join(path, f) for f in os.listdir(path)
                if f.lower().endswith((".png", ".jpg", ".jpeg"))]
        parsed = docparser.parse_scanned(imgs, trace)
        tables = []
    else:
        trace.log("file_access", action="open_file", path=path,
                  bytes=os.path.getsize(path))
        parsed = docparser.parse_pdf(path, trace)
        tables = docparser.extract_tables(path, trace)
    full_text = "\n".join(parsed["pages"])

    # 提取：LLM 优先，失败自动回退规则路径
    try:
        raw = extractor.extract_with_llm(full_text, tables, trace)
        source = "llm(deepseek)"
    except Exception as e:
        trace.log("llm_fallback", reason=str(e)[:200])
        raw = extractor.extract_by_rules(full_text, tables, trace,
                                         ocr_lines=parsed.get("lines"))
        source = "rules"

    result = extractor.build_result(raw, os.path.basename(path),
                                    parsed["method"])
    validator.run_checks(result, tables, trace)

    os.makedirs(OUT_DIR, exist_ok=True)
    out_json = os.path.join(OUT_DIR, stem + ".result.json")
    with open(out_json, "w", encoding="utf-8") as f:
        f.write(result.to_json())
    trace.log("output_written", file=out_json, extract_source=source)
    return result, source, out_json


def print_report(result, source, out_json, trace_path):
    m = result.doc_meta
    bar = "=" * 68
    print(bar)
    print(f"公告：{m.sec_name}（{m.sec_code}）  {m.doc_type}")
    print(f"公告编号：{m.announcement_no}    公告日期：{m.announcement_date}")
    print(f"解析方式：{m.parse_method}    提取来源：{source}")
    print(bar)
    if result.records:
        print("【质押 / 解押 / 展期记录】")
        for r in result.records:
            shares = f"{r.shares:,}" if r.shares else "-"
            print(f"  · {r.record_type} | {r.pledgor} | {shares} 股 | "
                  f"占其所持 {r.pct_of_held}% | 占总股本 {r.pct_of_total}%")
            if r.pledgee:
                end = r.pledge_end_date or r.original_end_date or "-"
                print(f"      质权人：{r.pledgee}    起始：{r.pledge_start_date or '-'}    "
                      f"到期：{end}")
            if r.release_date:
                print(f"      解除日期：{r.release_date}")
    else:
        print("【质押 / 解押 / 展期记录】未提取到（原因见校验报告）")
    if result.cumulative:
        print("【累计质押（截至公告披露日）】")
        for c in result.cumulative:
            print(f"  · {c.shareholder}：持股 {c.holding_shares:,} 股（{c.holding_pct}%）| "
                  f"质押前 {c.pre_pledge_shares:,} → 质押后 {c.post_pledge_shares:,} 股 | "
                  f"占其所持 {c.pct_of_held}% | 占总股本 {c.pct_of_total}%")
    if result.risk_items:
        print("【风险提示 · 到期质押】")
        for i in result.risk_items:
            print(f"  · {i.horizon}：{i.shares:,} 股，对应融资余额 {i.finance_balance:,} 元")
    print(bar)
    print("【一致性校验】")
    n_pass = sum(1 for c in result.checks if c.passed)
    for c in result.checks:
        print(f"  {'✓' if c.passed else '✗'} {c.name}：{c.detail}")
    print(f"  通过 {n_pass}/{len(result.checks)} 项")
    print(bar)
    print(f"结构化结果：{out_json}")
    print(f"可追溯日志：{trace_path}")
    print(bar)


def main():
    ap = argparse.ArgumentParser(description="股权质押公告数据结构化提取（选题一案例）")
    ap.add_argument("inputs", nargs="+", help="PDF 文件或扫描件图片目录，可多个")
    args = ap.parse_args()

    for path in args.inputs:
        stem = os.path.splitext(os.path.basename(path.rstrip("/\\")))[0]
        trace_path = os.path.join(OUT_DIR, stem + ".trace.jsonl")
        os.makedirs(OUT_DIR, exist_ok=True)
        trace = Trace(trace_path)
        trace.log("run_start", input=path, env={
            "llm_enabled": bool(os.environ.get("DEEPSEEK_API_KEY")),
            "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")})
        result, source, out_json = run_document(path, trace)
        print_report(result, source, out_json, trace_path)
        print()


if __name__ == "__main__":
    main()
