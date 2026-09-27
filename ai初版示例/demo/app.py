# -*- coding: utf-8 -*-
"""
app.py —— 驼研·信鉴 演示网页(Streamlit)

三个页面:
  ① 公告提取演示:选样本/上传 PDF → 跑管线 → 展示结构化结果+校验+审查卷宗
  ② 信用风险预警:四家公司红旗规则触发情况与综合评级
  ③ 评测总览:30 份评测集结果 + LLM/规则消融对照

运行:streamlit run app.py
"""
import glob
import json
import os
import sys

import pandas as pd
import streamlit as st

DEMO_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DEMO_DIR)
OUT_DIR = os.path.join(DEMO_DIR, "output")
EVAL_DIR = os.path.join(DEMO_DIR, "data", "eval_set")

st.set_page_config(page_title="驼研·信鉴", page_icon="🐫", layout="wide")

# ---------------------------------------------------------------------------
# 数据加载(有缓存,秒开)
# ---------------------------------------------------------------------------

@st.cache_data
def load_result(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@st.cache_data
def load_review(stem):
    p = os.path.join(OUT_DIR, stem + ".review.json")
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


def fmt_int(v):
    return f"{v:,}" if isinstance(v, (int, float)) else "—"


# ---------------------------------------------------------------------------
# 页面 1:公告提取演示
# ---------------------------------------------------------------------------

def page_extract():
    st.header("📄 公告结构化提取演示")
    st.caption("上传或选择一份质押公告,系统自动完成:解析 → AI 提取 → "
               "程序验算 → 原文溯源 → 多智能体互相监督审查")

    samples = sorted(glob.glob(os.path.join(DEMO_DIR, "data", "*.pdf")) +
                     glob.glob(os.path.join(EVAL_DIR, "*.pdf")))
    names = [os.path.basename(p) for p in samples]

    col1, col2 = st.columns([2, 1])
    with col1:
        choice = st.selectbox("选择内置样本(含 30 份评测集公告)", names)
    with col2:
        uploaded = st.file_uploader("或上传自己的 PDF", type=["pdf"])

    if uploaded:
        tmp = os.path.join(OUT_DIR, "_upload_" + uploaded.name)
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(tmp, "wb") as f:
            f.write(uploaded.getbuffer())
        path, stem = tmp, "_upload_" + os.path.splitext(uploaded.name)[0]
    else:
        path = samples[names.index(choice)]
        stem = os.path.splitext(os.path.basename(path))[0]

    result_path = os.path.join(OUT_DIR, stem + ".result.json")
    has_cache = os.path.isfile(result_path)

    c1, c2 = st.columns([1, 3])
    with c1:
        run_live = st.button("▶️ 现场重新运行(调用大模型)", type="primary")
    with c2:
        if has_cache:
            st.info("检测到已有结果,默认加载缓存(秒开);点左侧按钮可现场重跑。")
        else:
            st.warning("该文件还没有跑过,请点击左侧按钮现场运行。")

    if run_live:
        import run as pipeline
        from tracing import Trace
        trace = Trace(os.path.join(OUT_DIR, stem + ".trace.jsonl"))
        with st.spinner("管线运行中:解析 → 提取 → 校验 → 溯源 → 多智能体审查……"):
            try:
                pipeline.run_document(path, trace)
                st.cache_data.clear()
                st.success("运行完成!")
            except Exception as e:
                st.error(f"运行失败:{e}")
                return

    if not os.path.isfile(result_path):
        return
    d = load_result(result_path)

    # ---- 文档信息 ----
    m = d["doc_meta"]
    st.subheader("📋 文档信息")
    cols = st.columns(5)
    cols[0].metric("证券简称", m.get("sec_name") or "—")
    cols[1].metric("证券代码", m.get("sec_code") or "—")
    cols[2].metric("公告类型", m.get("doc_type") or "—")
    cols[3].metric("公告日期", m.get("announcement_date") or "—")
    cols[4].metric("解析方式", m.get("parse_method") or "—")

    # ---- 提取记录 ----
    if d.get("records"):
        st.subheader("📑 质押 / 解押 / 展期记录")
        rows = [{
            "类型": r.get("record_type"), "出质人": r.get("pledgor"),
            "股数": fmt_int(r.get("shares")),
            "占其所持%": r.get("pct_of_held"), "占总股本%": r.get("pct_of_total"),
            "质权人": r.get("pledgee") or "—",
            "起始": r.get("pledge_start_date") or "—",
            "到期/解除": r.get("release_date") or r.get("pledge_end_date") or "—",
            "用途": r.get("purpose") or "—",
        } for r in d["records"]]
        st.dataframe(pd.DataFrame(rows), use_container_width=True)

    if d.get("cumulative"):
        st.subheader("📊 累计质押情况")
        rows = [{
            "股东": c.get("shareholder"), "持股数": fmt_int(c.get("holding_shares")),
            "持股比例%": c.get("holding_pct"),
            "质押前": fmt_int(c.get("pre_pledge_shares")),
            "质押后": fmt_int(c.get("post_pledge_shares")),
            "占其所持%": c.get("pct_of_held"), "占总股本%": c.get("pct_of_total"),
        } for c in d["cumulative"]]
        st.dataframe(pd.DataFrame(rows), use_container_width=True)

    if d.get("risk_items"):
        st.subheader("⚠️ 风险提示(到期质押)")
        rows = [{"区间": i.get("horizon"), "到期股数": fmt_int(i.get("shares")),
                 "融资余额(元)": fmt_int(i.get("finance_balance"))}
                for i in d["risk_items"]]
        st.dataframe(pd.DataFrame(rows), use_container_width=True)

    # ---- 一致性校验 ----
    st.subheader("✅ 一致性校验(勾稽验算 + 原文溯源)")
    n_pass = sum(1 for c in d["checks"] if c["passed"])
    st.progress(n_pass / max(len(d["checks"]), 1),
                text=f"通过 {n_pass}/{len(d['checks'])} 项")
    for c in d["checks"]:
        icon = "🟢" if c["passed"] else "🔴"
        with st.expander(f"{icon} {c['name']}", expanded=not c["passed"]):
            st.write(c["detail"])
            st.caption(f"期望:{c.get('expected','—')} | 实测:{c.get('computed','—')}")

    # ---- 多智能体审查卷宗 ----
    st.subheader("🤖 多智能体审查(质疑-裁决对抗)")
    rv = load_review(stem)
    if rv is None:
        st.caption("该结果生成时未开启审查(或缓存较旧),点击「现场重新运行」即可体验。")
    else:
        status_map = {"clean": ("✅ 未发现错误", "green"),
                      "corrected": ("🔧 发现并已修正错误", "orange"),
                      "needs_human_review": ("⚠️ 有争议项,需人工复核", "red")}
        label, color = status_map.get(rv["final_status"], (rv["final_status"], "gray"))
        st.markdown(f"**最终结论:: {label}**")
        for rnd in rv["rounds"]:
            st.markdown(f"**第 {rnd['round']} 轮** — 质疑员 `{rnd.get('critic_model','?')}` "
                        f"/ 裁决员 `{rnd.get('adjudicator_model','-')}`")
            if rnd.get("n_criticisms") == 0:
                st.write("质疑员未提出质疑,审查通过。")
            for v in rnd.get("verdicts", []):
                mark = {"sustained": "🔴 质疑成立", "rejected": "⚪ 质疑驳回",
                        "sustained_not_applied": "🟡 成立但未自动采纳(转人工)"}.get(
                            v["verdict"], v["verdict"])
                with st.expander(f"{mark} `{v['field_path']}`"):
                    st.write("**质疑理由**:", v.get("claim"))
                    st.write("**裁决理由**:", v.get("reason"))
                    if v.get("critic_evidence"):
                        st.caption("质疑证据:" + str(v["critic_evidence"])[:300])
                    if v.get("adjudicator_evidence"):
                        st.caption("裁决证据:" + str(v["adjudicator_evidence"])[:300])
                    if v.get("applied"):
                        st.success(f"已修正:{v.get('old_value')} → {v.get('corrected_value')}")

    with st.expander("🔍 查看原始 JSON"):
        st.json(d)


# ---------------------------------------------------------------------------
# 页面 2:信用风险预警
# ---------------------------------------------------------------------------

def page_risk():
    st.header("🚩 信用风险预警(红旗规则引擎 R1-R9)")
    risk_files = sorted(glob.glob(os.path.join(OUT_DIR, "risk", "*_risk.json")))
    if not risk_files:
        st.warning("未找到预警结果,请先运行 fin_analysis.py")
        return
    opts = {}
    for p in risk_files:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        opts[f"{d['name']}({d['code']})"] = d
    sel = st.selectbox("选择公司", list(opts.keys()))
    d = opts[sel]

    verdict_color = {"高风险": "red", "关注": "orange", "稳健": "green"}.get(
        d["verdict"], "gray")
    cols = st.columns(3)
    cols[0].markdown(f"### 综合评级: :{verdict_color}[{d['verdict']}]")
    cols[1].metric("🔴 红色警报", d["red_count"])
    cols[2].metric("🟠 橙色提示", d["orange_count"])
    if d["code"] == "600518":
        st.error("康美药业:系统在 2017-2018 年报即发出红色预警,"
                 "比 2019 年造假曝光早 1-2 年。")
    if d["code"] == "600519":
        st.success("贵州茅台:健康对照组,零红色警报——不乱喊狼来了。")

    rows = []
    for f in d["red_flags"]:
        rows.append({"规则": f["rule_id"] + " " + f["rule"],
                     "级别": "🔴" if f["severity"] == "red" else "🟠",
                     "期间": f["period"], "说明": f["explanation"]})
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, height=420)

    with st.expander("查看预警证据明细(全部可回溯至落盘财报数据)"):
        for f in d["red_flags"]:
            st.markdown(f"**{f['rule_id']} {f['rule']}** · {f['period']} · "
                        f"{'🔴' if f['severity']=='red' else '🟠'}")
            st.json(f["evidence"])
            st.caption("数据来源:" + f.get("source", "—"))


# ---------------------------------------------------------------------------
# 页面 3:评测总览
# ---------------------------------------------------------------------------

def page_eval():
    st.header("📈 评测总览(30 份未见公告盲测)")
    sp = os.path.join(OUT_DIR, "eval", "eval_summary.json")
    cp = os.path.join(OUT_DIR, "eval", "compare_llm_vs_rules.json")
    if not os.path.isfile(sp):
        st.warning("未找到评测汇总,请先运行 eval_run.py")
        return
    s = json.load(open(sp, encoding="utf-8"))

    cols = st.columns(4)
    cols[0].metric("跑通率", f"{s['stats']['ran_ok']}/{s['total_docs']}")
    cols[1].metric("校验全过", f"{s['stats']['full_checks_pass']} 份")
    tot_p = sum(r["checks_passed"] for r in s["results"])
    tot_c = sum(r["checks_total"] for r in s["results"])
    cols[2].metric("校验项通过率", f"{tot_p}/{tot_c}({tot_p/tot_c:.1%})")
    covs = [float(r["grounding_coverage"].rstrip("%")) for r in s["results"]]
    cols[3].metric("平均溯源覆盖率", f"{sum(covs)/len(covs):.1f}%")

    rows = [{"公司": r["sec_name"], "代码": r["sec_code"], "类型": r["doc_type"],
             "校验": f"{r['checks_passed']}/{r['checks_total']}",
             "溯源": r["grounding_coverage"], "记录数": r["records"]}
            for r in s["results"]]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, height=400)

    if os.path.isfile(cp):
        c = json.load(open(cp, encoding="utf-8"))["summary"]
        st.subheader("消融对照:AI 提取 vs 纯规则提取")
        df = pd.DataFrame({
            "指标": ["校验全过(文档级)", "平均每份提取记录", "平均每份累计表条目"],
            "AI 提取(本系统)": [f"{c['llm']['check_full_pass']}/{c['n_docs']}",
                          c["llm"]["avg_records"], c["llm"]["avg_cumulative"]],
            "纯规则提取": [f"{c['rules']['check_full_pass']}/{c['n_docs']}",
                        c["rules"]["avg_records"], c["rules"]["avg_cumulative"]],
        })
        st.table(df)
        st.caption("结论:AI 负责「提得出」,校验体系负责「提得对」,二者缺一不可。")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main():
    st.sidebar.title("🐫 驼研·信鉴")
    st.sidebar.caption("公告与财报多源结构化提取的\n上市公司信用风险预警智能体")
    page = st.sidebar.radio("功能", ["公告提取演示", "信用风险预警", "评测总览"])
    st.sidebar.divider()
    st.sidebar.caption("DeepSeek(提取/裁决)+ Kimi(质疑)\n数据均已落盘,离线可演示")
    {"公告提取演示": page_extract, "信用风险预警": page_risk,
     "评测总览": page_eval}[page]()


if __name__ == "__main__":
    main()
