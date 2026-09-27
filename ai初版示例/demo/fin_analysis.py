# -*- coding: utf-8 -*-
"""
fin_analysis.py —— 财务分析层 + 风险预警层（选题二核心，驼研·信鉴）

设计原则（FinRobot 原则）：所有指标一律用代码对落盘 CSV 计算，
不让 LLM 口算任何数字 —— 保证"同比/环比计算准确性"考察点满分。

红旗规则引擎：对每家公司的年报序列逐期触发规则，输出
  规则编号、严重程度(red/orange)、所属期间、证据数值、数据来源文件，
并联动选题一的质押提取结果（output/*.result.json）生成"质押链式风险"预警 ——
提取结果直接成为预警输入，两个选题在此咬合。

用法：
  python fin_analysis.py            # 分析全部已采集公司，生成汇总报告
输出：
  output/risk/{code}_{name}_risk.json     每家公司结构化预警结果
  output/risk/风险预警汇总报告.md          跨公司对比 Markdown 报告
"""
import glob
import json
import os
import sys
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd

DEMO_DIR = os.path.dirname(os.path.abspath(__file__))
FIN_DIR = os.path.join(DEMO_DIR, "data", "financials")
PLEDGE_DIR = os.path.join(DEMO_DIR, "output")
OUT_DIR = os.path.join(DEMO_DIR, "output", "risk")

# 公司元信息：industry 用于行业口径豁免（银行业现金流/杠杆口径特殊）
COMPANY_META = {
    "000863": {"name": "三湘印象", "industry": "地产文化"},
    "601997": {"name": "贵阳银行", "industry": "银行"},
    "600518": {"name": "康美药业", "industry": "医药"},
    "600519": {"name": "贵州茅台", "industry": "白酒"},
}


# ----------------------------------------------------------------- 数值解析
def _num(s):
    """'33.40亿' / '1,234.56万' / '-0.64' / '20.5%' / '--' → float 或 None"""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip().replace(",", "").replace("%", "")
    if s in ("", "--", "None", "nan"):
        return None
    mult = 1.0
    if s.endswith("亿"):
        mult, s = 1e8, s[:-1]
    elif s.endswith("万"):
        mult, s = 1e4, s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None


def _pct(s):
    """增长率列解析:'20.5%' / 20.5 → float"""
    return _num(s)


def _col(df, *keywords):
    """按关键词找列名（容忍接口列名微调）"""
    for kw in keywords:
        for c in df.columns:
            if kw in c:
                return c
    return None


def _annual(df, date_col):
    """只保留年报行(12-31),并按年升序"""
    d = df[df[date_col].astype(str).str.endswith("12-31")].copy()
    d["year"] = d[date_col].astype(str).str[:4].astype(int)
    return d.sort_values("year").reset_index(drop=True)


# ----------------------------------------------------------------- 数据加载
# 评估窗口:只用 2016 年及以后的年报。
# 同花顺摘要数据可回溯到 19xx 年(公司上市起),但太久远的经营状况
# 对当下信用判断意义极小,还会产生"远古信号"噪声;窗口起点固定 2016,
# 确保康美 2017-2018 回溯验证信号始终在窗口内,不随年份漂移。
YEAR_MIN = 2016


def load_company(code):
    meta = COMPANY_META.get(code, {"name": "未知", "industry": "综合"})
    base = os.path.join(FIN_DIR, f"{code}_{meta['name']}")
    ths = sin = None
    p = os.path.join(base, "abstract_ths.csv")
    if os.path.isfile(p):
        ths = _annual(pd.read_csv(p), "报告期")
        ths = ths[ths["year"] >= YEAR_MIN].reset_index(drop=True)
    p = os.path.join(base, "indicators_sina.csv")
    if os.path.isfile(p):
        sin = _annual(pd.read_csv(p), _col(pd.read_csv(p), "日期") or "日期")
        sin = sin[sin["year"] >= YEAR_MIN].reset_index(drop=True)
    return meta, ths, sin


# ----------------------------------------------------------------- 红旗规则
def rule_engine(code, meta, ths, sin, pledge_results):
    """逐规则扫描，返回 flags 列表"""
    flags = []
    is_bank = meta["industry"] == "银行"
    name = meta["name"]

    def add(rule_id, rule_name, severity, period, evidence, explanation, source):
        flags.append({"rule_id": rule_id, "rule": rule_name, "severity": severity,
                      "period": period, "evidence": evidence,
                      "explanation": explanation, "source": source})

    if ths is not None and len(ths):
        c_profit = _col(ths, "净利润")           # 净利润(亿元字符串)
        c_deduct = _col(ths, "扣非净利润")
        c_rev = _col(ths, "营业总收入")
        c_ocfps = _col(ths, "每股经营现金流")
        c_eps = _col(ths, "基本每股收益")
        src_ths = f"data/financials/{code}_{name}/abstract_ths.csv"

        profits = [_num(v) for v in ths[c_profit]] if c_profit else [None] * len(ths)
        deducts = [_num(v) for v in ths[c_deduct]] if c_deduct else [None] * len(ths)
        revs = [_num(v) for v in ths[c_rev]] if c_rev else [None] * len(ths)
        ocfs = [_num(v) for v in ths[c_ocfps]] if c_ocfps else [None] * len(ths)
        epss = [_num(v) for v in ths[c_eps]] if c_eps else [None] * len(ths)
        years = ths["year"].tolist()

        for i, y in enumerate(years):
            p, d, r, o, e = profits[i], deducts[i], revs[i], ocfs[i], epss[i]
            # R1 利润现金流背离:净利润为正(>1000万,滤除微利噪声)但每股经营现金流为负(银行豁免)
            if not is_bank and p and p > 1e7 and o is not None and o < 0:
                add("R1", "利润与现金流背离", "red", f"{y}年报",
                    {"净利润": p, "每股经营现金流": o},
                    f"{y} 年账面净利润 {_yi(p)}，但每股经营现金流 {o:.2f} 元为负——"
                    "利润没有现金支撑，是财务造假/资金链紧张的教科书信号",
                    src_ths)
            # R2 非经常性损益占比异常:|净利润-扣非|/|净利润| > 30%(净利润≥5000万才评估,避免小基数噪声)
            if p and d is not None and abs(p) >= 5e7:
                gap = abs(p - d) / abs(p)
                if gap > 0.3:
                    add("R2", "非经常性损益占比异常", "red" if gap > 0.5 else "orange",
                        f"{y}年报", {"净利润": p, "扣非净利润": d, "差异率": f"{gap:.0%}"},
                        f"{y} 年净利润与扣非净利润差异率达 {gap:.0%}，"
                        "利润成色存疑(竞赛考点:非经常性损益识别)", src_ths)
            # R7 利润含金量不足:EPS>0 且 每股经营现金流/EPS < 0.3(同源同单位,比值干净;银行豁免)
            if not is_bank and e is not None and e > 0 and o is not None:
                ratio = o / e
                if ratio < 0.3:
                    add("R7", "利润含金量不足", "orange", f"{y}年报",
                        {"基本每股收益": e, "每股经营现金流": o, "比值": f"{ratio:.2f}"},
                        f"{y} 年每股经营现金流仅为每股收益的 {ratio:.2f} 倍(<0.3)，"
                        "利润含金量低", src_ths)
            # R4 业绩变脸:净利润同比降幅
            if i > 0 and profits[i - 1] and p is not None and profits[i - 1] > 0:
                chg = (p - profits[i - 1]) / profits[i - 1]
                if chg <= -0.3:
                    add("R4", "业绩变脸", "red" if chg <= -0.5 else "orange",
                        f"{y}年报", {"上年净利润": profits[i - 1], "本年净利润": p,
                                     "同比": f"{chg:.0%}"},
                        f"{y} 年净利润同比 {chg:.0%}(上年 {_yi(profits[i-1])} → 本年 {_yi(p)})",
                        src_ths)
            # R3 营收利润背离:营收降而利润升(或增速差 > 30pp)
            if i > 0 and revs[i - 1] and r is not None and profits[i - 1] and p is not None:
                rev_chg = (r - revs[i - 1]) / abs(revs[i - 1])
                pf_chg = (p - profits[i - 1]) / abs(profits[i - 1])
                if rev_chg < 0 < pf_chg and abs(pf_chg - rev_chg) > 0.3:
                    add("R3", "营收与利润背离", "orange", f"{y}年报",
                        {"营收同比": f"{rev_chg:.0%}", "净利润同比": f"{pf_chg:.0%}"},
                        f"{y} 年营收下降 {rev_chg:.0%} 但净利润增长 {pf_chg:.0%}，"
                        "利润增长缺乏收入支撑", src_ths)
        # R5 持续亏损:连续两年净利润为负
        for i in range(1, len(years)):
            if profits[i] is not None and profits[i - 1] is not None \
                    and profits[i] < 0 and profits[i - 1] < 0:
                add("R5", "持续亏损", "red", f"{years[i-1]}-{years[i]}年报",
                    {"前两年净利润": profits[i - 1], "前一年净利润": profits[i]},
                    f"连续两年亏损({_yi(profits[i-1])}、{_yi(profits[i])})，"
                    "存在 ST 风险", src_ths)
                break  # 一家公司报一次即可

    if sin is not None and len(sin):
        c_debt = _col(sin, "资产负债率")
        src_sin = f"data/financials/{code}_{name}/indicators_sina.csv"
        for _, row in sin.iterrows():
            y = int(row["year"])
            # R6 高杠杆:资产负债率(银行豁免)
            v = _num(row.get(c_debt)) if c_debt else None
            if not is_bank and v is not None and v > 60:
                add("R6", "高杠杆(资产负债率)", "red" if v > 70 else "orange",
                    f"{y}年报", {"资产负债率": f"{v:.1f}%"},
                    f"{y} 年资产负债率 {v:.1f}%"
                    + ("，已超 100% 资不抵债" if v >= 100 else ""),
                    src_sin)

    # R8 质押链式风险:联动选题一提取结果(两个选题的咬合点)
    for pr in pledge_results:
        if pr.get("doc_meta", {}).get("sec_code") != code:
            continue
        src_p = f"output/{os.path.basename(pr['_file'])}"
        for c in pr.get("cumulative", []):
            ratio = c.get("pct_of_held")
            if ratio is None:
                continue
            if ratio > 50:
                sev = "red" if ratio > 70 else "orange"
                add("R8", "股东高比例质押", sev,
                    pr["doc_meta"].get("announcement_date") or "公告",
                    {"股东": c.get("shareholder"), "质押占其所持": f"{ratio}%",
                     "质押后股数": c.get("post_pledge_shares")},
                    f"{c.get('shareholder')} 已质押其所持股份的 {ratio}%，"
                    "股价下跌时存在平仓/控制权变动风险(质押公告提取结果直接驱动本预警)",
                    src_p)
        for it in pr.get("risk_items", []):
            bal = it.get("finance_balance") or 0
            if "半年" in (it.get("horizon") or "") and bal > 0:
                add("R9", "质押到期压力", "orange",
                    pr["doc_meta"].get("announcement_date") or "公告",
                    {"期间": it.get("horizon"), "到期股数": it.get("shares"),
                     "融资余额": bal},
                    f"{it.get('horizon')}到期质押 {it.get('shares', 0):,} 股，"
                    f"对应融资余额 {_yi(bal)}，短期偿付压力需关注", src_p)
    # 去重:同一公司可能有文本版+扫描版两份提取结果,避免重复预警
    seen, dedup = set(), []
    for f in flags:
        k = (f["rule_id"], f["period"],
             json.dumps(f["evidence"], ensure_ascii=False, sort_keys=True))
        if k not in seen:
            seen.add(k)
            dedup.append(f)
    return dedup


def _yi(v):
    """格式化亿元"""
    if v is None:
        return "-"
    if abs(v) >= 1e8:
        return f"{v / 1e8:.2f} 亿"
    if abs(v) >= 1e4:
        return f"{v / 1e4:.2f} 万"
    return f"{v:.2f}"


def verdict(flags):
    """综合结论:有 red → 高风险;≥2 orange → 关注;否则 稳健"""
    reds = sum(1 for f in flags if f["severity"] == "red")
    oranges = sum(1 for f in flags if f["severity"] == "orange")
    if reds:
        return "高风险", reds, oranges
    if oranges >= 2:
        return "关注", reds, oranges
    return "稳健", reds, oranges


# ----------------------------------------------------------------- 主流程
def load_pledge_results():
    """读取选题一全部提取结果(质押公告 JSON)"""
    out = []
    for p in glob.glob(os.path.join(PLEDGE_DIR, "*.result.json")):
        try:
            d = json.load(open(p, encoding="utf-8"))
            d["_file"] = p
            out.append(d)
        except Exception:
            continue
    return out


def analyze(code):
    meta, ths, sin = load_company(code)
    if ths is None and sin is None:
        print(f"[{code}] 无本地财务数据,跳过(请先运行 fin_data.py)")
        return None
    pledge = load_pledge_results()
    flags = rule_engine(code, meta, ths, sin, pledge)
    v, reds, oranges = verdict(flags)
    result = {
        "code": code, "name": meta["name"], "industry": meta["industry"],
        "analysis_time": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "verdict": v, "red_count": reds, "orange_count": oranges,
        "red_flags": flags,
        "data_sources": ["abstract_ths.csv(同花顺)", "indicators_sina.csv(新浪)",
                         "output/*.result.json(选题一提取结果)"],
        "note": "全部指标由代码对落盘数据计算,未经 LLM 口算;银行业现金流/杠杆口径特殊已豁免 R1/R6/R7",
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{code}_{meta['name']}_risk.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


def render_report(results):
    """生成跨公司 Markdown 汇总报告(可直接进计划书/视频)"""
    L = ["# 驼研·信鉴 —— 上市公司信用风险预警分析报告(自动生成)", ""]
    L.append(f"> 生成时间:{datetime.now(timezone(timedelta(hours=8))):%Y-%m-%d %H:%M}  ")
    L.append("> 数据来源:akshare 公开接口落盘 CSV(同花顺/新浪)+ 选题一公告提取结果;"
             "全部指标经代码计算并标注来源,可逐条复核。", )
    L += ["", "## 一、汇总", "",
          "| 公司 | 行业 | 红色预警 | 风险提示 | 综合结论 |",
          "|---|---|---|---|---|"]
    for r in results:
        L.append(f"| {r['name']}({r['code']}) | {r['industry']} | "
                 f"{r['red_count']} 项 | {r['orange_count']} 项 | **{r['verdict']}** |")
    L += ["", "## 二、分公司预警明细", ""]
    for r in results:
        L.append(f"### {r['name']}({r['code']})—— {r['verdict']}")
        L.append("")
        if not r["red_flags"]:
            L.append("未触发任何预警规则。")
        else:
            show = r["red_flags"][-20:]  # 报告只列最近 20 条,完整清单见 JSON
            if len(r["red_flags"]) > 20:
                L.append(f"*(共 {len(r['red_flags'])} 条,仅列最近 20 条;"
                         f"完整清单见 {r['code']}_{r['name']}_risk.json)*")
                L.append("")
            L += ["| 级别 | 规则 | 期间 | 证据 | 解读 |", "|---|---|---|---|---|"]
            for f in show:
                ev = "、".join(f"{k}={v}" for k, v in f["evidence"].items())
                L.append(f"| {'🔴' if f['severity'] == 'red' else '🟠'} "
                         f"{f['rule']} | {f['period']} | {ev} | {f['explanation']} |")
        L.append("")
    L += ["## 三、规则说明", "",
          "- R1 利润与现金流背离 / R2 非经常性损益占比异常 / R3 营收与利润背离 / "
          "R4 业绩变脸 / R5 持续亏损 / R6 高杠杆 / R7 利润含金量不足 —— "
          "对应竞赛选题二'异常信号识别'考点",
          "- R8 股东高比例质押 / R9 质押到期压力 —— 由选题一公告提取结果直接驱动,"
          "体现'提取→分析→预警'一体化闭环",
          "- 银行业(R1/R6/R7)按行业口径豁免,避免误报",
          ""]
    path = os.path.join(OUT_DIR, "风险预警汇总报告.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return path


def main():
    codes = sys.argv[1:] or list(COMPANY_META)
    print("=== 驼研·信鉴 风险预警分析 ===")
    results = [r for c in codes if (r := analyze(c))]
    for r in results:
        print(f"\n[{r['code']} {r['name']}] 综合结论: {r['verdict']}"
              f"(红 {r['red_count']} / 橙 {r['orange_count']})")
        for f in r["red_flags"]:
            mark = "🔴" if f["severity"] == "red" else "🟠"
            print(f"  {mark} [{f['rule_id']}] {f['rule']} @ {f['period']}: "
                  f"{f['explanation'][:60]}")
    if results:
        path = render_report(results)
        print(f"\n汇总报告: {path}")


if __name__ == "__main__":
    main()
