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
import grounding
import parser as docparser
import review_agents
import validator
from schema import CheckResult
from tracing import Trace

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


def _validate_and_ground(result, tables, pages, trace, full_text):
    """程序校验(勾稽等) + 原文溯源,并把溯源覆盖率登记为校验项。
    多智能体审查修正字段后需重跑本函数,保证报告与最终数据一致。"""
    result.checks = []
    validator.run_checks(result, tables, trace, full_text=full_text)
    report = grounding.run_grounding(result, pages, trace)
    cov = report["coverage"]
    miss = [list(m.keys())[0] for it in report["items"] for m in it["missing"]]
    result.checks.append(CheckResult(
        name="原文溯源覆盖率",
        passed=cov >= 0.8,
        detail=(f"{cov:.0%} 的已提取字段在原文中找到证据（{report['found']} 项命中）"
                + (f"；未溯源字段: {'、'.join(miss[:6])}（多为单位换算或表述差异，需人工复核）"
                   if miss else "")),
        expected="≥80%", computed=f"{cov:.2%}"))


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

    # 提取：LLM 优先（含校验-重试闭环），失败自动回退规则路径
    try:
        raw = extractor.extract_with_llm(full_text, tables, trace)
        source = "llm"
    except Exception as e:
        trace.log("llm_fallback", reason=str(e)[:200])
        raw = extractor.extract_by_rules(full_text, tables, trace,
                                         ocr_lines=parsed.get("lines"))
        source = "rules"

    result = extractor.build_result(raw, os.path.basename(path),
                                    parsed["method"])
    _validate_and_ground(result, tables, parsed["pages"], trace, full_text)

    # 多智能体审查(质疑-裁决对抗机制):质疑员(异构模型,只挑毛病)→
    # 裁决员(回原文逐条裁决)→ 修正生效则重跑程序校验与溯源,全程写入审查卷宗。
    # 默认开启,环境变量 MULTI_AGENT_REVIEW=0 可关闭;仅 LLM 路径有意义。
    dossier = None
    if source == "llm" and os.environ.get("MULTI_AGENT_REVIEW", "1") != "0":
        try:
            dossier = review_agents.review_extraction(result, full_text, trace)
            if dossier["corrections"]:
                _validate_and_ground(result, tables, parsed["pages"], trace,
                                     full_text)
            jpath, mpath = review_agents.save_dossier(dossier, path, OUT_DIR)
            trace.log("review_done", status=dossier["final_status"],
                      corrections=len(dossier["corrections"]), dossier=jpath)
        except Exception as e:
            trace.log("review_error", error=str(e)[:200])

    os.makedirs(OUT_DIR, exist_ok=True)
    out_json = os.path.join(OUT_DIR, stem + ".result.json")
    with open(out_json, "w", encoding="utf-8") as f:
        f.write(result.to_json())
    trace.log("output_written", file=out_json, extract_source=source)
    return result, source, out_json, dossier


def print_report(result, source, out_json, trace_path, dossier=None):
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
            shares = f"{i.shares:,}" if i.shares is not None else "-"
            bal = f"{i.finance_balance:,}" if i.finance_balance is not None else "-"
            print(f"  · {i.horizon}：{shares} 股，对应融资余额 {bal} 元")
    print(bar)
    print("【一致性校验】")
    n_pass = sum(1 for c in result.checks if c.passed)
    for c in result.checks:
        print(f"  {'✓' if c.passed else '✗'} {c.name}：{c.detail}")
    print(f"  通过 {n_pass}/{len(result.checks)} 项")
    if dossier:
        status_zh = {"clean": "未发现错误", "corrected": "发现并修正了错误",
                     "needs_human_review": "有争议项待人工复核"}
        print(bar)
        print("【多智能体审查(质疑-裁决对抗)】")
        print(f"  结论:{status_zh.get(dossier['final_status'], dossier['final_status'])}"
              f" | 修正 {len(dossier['corrections'])} 处"
              f" | 驳回/存疑 {len(dossier['rejected'])} 条")
        for c in dossier["corrections"][:5]:
            print(f"  🔧 {c['field_path']}: {c.get('old_value')} → {c.get('corrected_value')}"
                  f" ({c.get('reason') or ''})")
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
        from llm_client import get_config
        _key, _base, _model = get_config()
        trace.log("run_start", input=path, env={
            "llm_enabled": bool(_key), "model": _model, "base_url": _base})
        result, source, out_json, dossier = run_document(path, trace)
        print_report(result, source, out_json, trace_path, dossier)
        print()


if __name__ == "__main__":
    main()
