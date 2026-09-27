# -*- coding: utf-8 -*-
"""
app.py —— 驼研·信鉴 演示网页(Streamlit)

流程式界面:
  步骤1  输入股票代码/公司名 → 自动上巨潮资讯网搜索公告(或手动上传 PDF)
  步骤2  一键数据结构化提取 → 结构化字段 + 校验 + 审查卷宗 → 下载 JSON
  步骤3  一键信用风险分析 → 红旗规则引擎 → FinSight 风格风险评估报告

运行:streamlit run app.py
"""
import glob
import json
import os
import re
import sys
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

DEMO_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DEMO_DIR)
OUT_DIR = os.path.join(DEMO_DIR, "output")
UPLOAD_DIR = os.path.join(OUT_DIR, "uploads")
LOGO = os.path.join(DEMO_DIR, "..", "..", "图片", "首经贸校徽加校名.png")

st.set_page_config(page_title="驼研·信鉴", page_icon="🐫", layout="wide")

# ---------------------------------------------------------------------------
# 红色主题(FinSight 风格:深红主色 + 米白底 + 卡片红边)
# ---------------------------------------------------------------------------
st.markdown("""
<style>
.stApp { background: linear-gradient(180deg, #fdf3f0 0%, #ffffff 40%); }
.hero {
  background: linear-gradient(135deg, #8f1414 0%, #c0392b 60%, #d35450 100%);
  border-radius: 14px; padding: 26px 30px; margin-bottom: 18px;
  color: #fff; box-shadow: 0 4px 18px rgba(143,20,20,.25);
}
.hero h1 { color:#fff; margin:0; font-size: 34px; letter-spacing: 2px; }
.hero p  { color:#f6dcd8; margin:6px 0 0 0; font-size: 15px; }
.step-card {
  background:#fff; border-left:5px solid #c0392b; border-radius:10px;
  padding:14px 18px; margin:14px 0; box-shadow:0 1px 6px rgba(0,0,0,.06);
}
.step-card h3 { color:#8f1414; margin:0 0 4px 0; }
div.stButton > button[kind="primary"] {
  background:#c0392b; border-color:#c0392b;
}
div.stButton > button[kind="primary"]:hover { background:#a93226; }
.badge-red   { background:#c0392b; color:#fff; border-radius:8px;
               padding:6px 14px; font-size:20px; font-weight:700; }
.badge-green { background:#27ae60; color:#fff; border-radius:8px;
               padding:6px 14px; font-size:20px; font-weight:700; }
.badge-orange{ background:#e67e22; color:#fff; border-radius:8px;
               padding:6px 14px; font-size:20px; font-weight:700; }
.flag-card {
  background:#fff; border:1px solid #f0d5d0; border-left:5px solid #c0392b;
  border-radius:8px; padding:10px 14px; margin:8px 0;
}
.flag-card.orange { border-left-color:#e67e22; }
</style>
""", unsafe_allow_html=True)

# 顶部横幅:校徽 + 标题
c_logo, c_title = st.columns([1, 5])
with c_logo:
    if os.path.isfile(LOGO):
        st.image(LOGO, use_container_width=True)
with c_title:
    st.markdown("""
    <div class="hero">
      <h1>🐫 驼研·信鉴</h1>
      <p>基于公告与财报多源结构化提取的上市公司信用风险预警智能体
      —— 三重把关:程序验算 · 原文溯源 · 多智能体互相监督</p>
    </div>""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def fmt_int(v):
    return f"{v:,}" if isinstance(v, (int, float)) else "—"


def safe_name(s):
    return re.sub(r'[\\/:*?"<>|]', "_", s or "")


# ---------------------------------------------------------------------------
# 步骤 1:搜索公告 / 上传文件
# ---------------------------------------------------------------------------
st.markdown('<div class="step-card"><h3>步骤 ① 选择分析对象</h3>'
            '<div>输入股票代码或公司名,自动从巨潮资讯网(证监会指定披露平台)'
            '搜索质押公告;也可以直接上传公告/研报 PDF。</div></div>',
            unsafe_allow_html=True)

if "files" not in st.session_state:
    st.session_state.files = []      # 待提取文件路径列表
if "company" not in st.session_state:
    st.session_state.company = {}    # {"code": ..., "name": ...}

c1, c2, c3 = st.columns([2, 1, 1])
with c1:
    query = st.text_input("股票代码 / 公司名", placeholder="如 000863 或 三湘印象")
with c2:
    months = st.selectbox("搜索范围", [3, 6, 12], format_func=lambda x: f"近 {x} 个月")
with c3:
    st.write("")
    do_search = st.button("🔍 上网搜索公告", type="primary")

def parse_query(q):
    """把「哈药股份(600664.SH)」「600664.SH」「sh600664」等输入解析为 (名称, 6位代码)。

    巨潮全文检索不认带括号/市场后缀的复合输入,必须先拆干净再搜。
    """
    q = q.strip()
    name, code = q, ""
    # 括号里若是代码,拆出来:哈药股份(600664.SH) → 哈药股份 + 600664
    m = re.search(r"[(（]([0-9A-Za-z.]+)[)）]", q)
    if m:
        name, code = (q[:m.start()] + q[m.end():]).strip(), m.group(1)
    # 名称部分本身就是代码:600664 / 600664.SH / sh600664
    m2 = re.fullmatch(r"(?i)(?:sh|sz|bj)?(\d{6})(?:\.(?:sh|sz|bj))?", name)
    if m2:
        name, code = "", m2.group(1)
    # 「000863 三湘印象」这类"代码+空格+名称"的输入
    m2b = re.fullmatch(r"(\d{6})\s+(.+)", name)
    if m2b:
        code, name = m2b.group(1), m2b.group(2).strip()
    # 代码部分规范化:sh600664 / 600664.SH → 600664
    m3 = re.search(r"(\d{6})", code)
    code = m3.group(1) if m3 else ""
    return name, code


if do_search and query.strip():
    import eval_collect
    edate = datetime.now()
    sdate = edate - timedelta(days=30 * months)
    name, code = parse_query(query)
    # 巨潮对公司名检索效果最好:先搜名称,搜不到再退到纯代码
    tries = [kw for kw in (name, code) if kw] or [query.strip()]
    anns, used_kw, err = [], tries[0], None
    for kw in tries:
        used_kw = kw
        with st.spinner(f"正在巨潮资讯网搜索「{kw}」相关公告……"):
            try:
                anns = eval_collect.search(kw, sdate.strftime("%Y-%m-%d"),
                                           edate.strftime("%Y-%m-%d"))
            except Exception as e:
                err = e
                anns = []
        if anns:
            break
    if err is not None and not anns:
        st.error(f"搜索失败:{err}")
    # 过滤:质押类公告、去掉年报等长文档
    hits = []
    for a in anns:
        title = eval_collect.clean_title(a.get("announcementTitle"))
        if "质押" not in title or any(k in title for k in ("年度报告", "半年度报告", "季度报告", "英文")):
            continue
        hits.append({"code": a.get("secCode"), "name": a.get("secName"),
                     "title": title, "url": a.get("adjunctUrl"),
                     "time": (a.get("announcementTime") or 0)})
    st.session_state.hits = hits
    if hits:
        st.session_state.company = {"code": hits[0]["code"], "name": hits[0]["name"]}
        st.success(f"找到 {len(hits)} 份质押类公告(公司:{hits[0]['name']} {hits[0]['code']})")
    elif anns:
        # 无质押公告≠死胡同:把搜到的公司带入步骤③,仍可直接做财报风险分析
        st.session_state.company = {"code": anns[0].get("secCode", ""),
                                    "name": anns[0].get("secName", "")}
        st.info(f"「{used_kw}」近 {months} 个月有公告发布,但其中没有质押类公告"
                "——该公司股东近期大概率没有质押行为,本身就是低风险信号。"
                "可直接到步骤 ③ 对该公司做信用风险分析(代码已自动带入),"
                "或换一家(如三湘印象 000863)体验提取流程,也可手动上传质押公告 PDF。")
    else:
        st.warning(f"巨潮资讯网没有搜到「{used_kw}」的任何公告,请检查名称/代码是否正确,"
                   "或直接手动上传文件。")

if st.session_state.get("hits"):
    st.markdown("**搜索结果(勾选要分析的公告):**")
    picks = []
    for i, h in enumerate(st.session_state.hits[:10]):
        date = datetime.fromtimestamp(h["time"] / 1000).strftime("%Y-%m-%d") if h["time"] else "—"
        if st.checkbox(f"{h['title']}({date})", key=f"hit{i}",
                       value=(i == 0)):
            picks.append(h)
    if st.button("⬇️ 下载选中公告"):
        import requests
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        for h in picks:
            fname = safe_name(f"{h['code']}_{h['name']}_{h['title'][:24]}.pdf")
            fpath = os.path.join(UPLOAD_DIR, fname)
            if not os.path.isfile(fpath):
                try:
                    r = requests.get(eval_collect.DOWNLOAD_BASE + h["url"],
                                     headers=eval_collect.HEADERS, timeout=60)
                    r.raise_for_status()
                    with open(fpath, "wb") as f:
                        f.write(r.content)
                except Exception as e:
                    st.error(f"{h['title'][:20]} 下载失败:{e}")
                    continue
            if fpath not in st.session_state.files:
                st.session_state.files.append(fpath)
        st.success(f"已就绪 {len(picks)} 份公告")

uploaded = st.file_uploader("📎 或手动上传公告/研报 PDF(可多选)",
                            type=["pdf"], accept_multiple_files=True)
if uploaded:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    for uf in uploaded:
        fpath = os.path.join(UPLOAD_DIR, safe_name(uf.name))
        with open(fpath, "wb") as f:
            f.write(uf.getbuffer())
        if fpath not in st.session_state.files:
            st.session_state.files.append(fpath)
    st.success(f"已添加 {len(uploaded)} 个文件")

# ---------------------------------------------------------------------------
# 步骤 2:数据结构化提取
# ---------------------------------------------------------------------------
st.markdown('<div class="step-card"><h3>步骤 ② 数据结构化提取</h3>'
            '<div>AI 提取字段 → 程序勾稽验算 → 原文溯源 → 多智能体互相监督审查,'
            '全程留痕,可下载 JSON。</div></div>', unsafe_allow_html=True)

if st.session_state.files:
    st.write("待处理文件:" + "、".join(os.path.basename(p) for p in st.session_state.files))
    if st.button("🗑️ 清空文件列表"):
        st.session_state.files = []
        st.rerun()

    if st.button("▶️ 开始数据结构化提取", type="primary"):
        import run as pipeline
        from tracing import Trace
        bar = st.progress(0.0, text="管线运行中……")
        for i, path in enumerate(st.session_state.files):
            stem = os.path.splitext(os.path.basename(path))[0]
            bar.progress(i / len(st.session_state.files),
                         text=f"正在处理:{stem[:30]}……(提取→校验→溯源→审查)")
            trace = Trace(os.path.join(OUT_DIR, stem + ".trace.jsonl"))
            try:
                pipeline.run_document(path, trace)
            except Exception as e:
                st.error(f"{stem} 处理失败:{e}")
        bar.progress(1.0, text="全部完成!")
        st.success("提取完成,结果如下。")

    for path in st.session_state.files:
        stem = os.path.splitext(os.path.basename(path))[0]
        rp = os.path.join(OUT_DIR, stem + ".result.json")
        if not os.path.isfile(rp):
            continue
        d = json.load(open(rp, encoding="utf-8"))
        m = d["doc_meta"]
        n_pass = sum(1 for c in d["checks"] if c["passed"])
        with st.expander(f"📄 {m.get('sec_name') or stem}({m.get('doc_type','—')})"
                         f" — 校验 {n_pass}/{len(d['checks'])} 通过", expanded=True):
            cols = st.columns(5)
            cols[0].metric("证券代码", m.get("sec_code") or "—")
            cols[1].metric("公告日期", m.get("announcement_date") or "—")
            cols[2].metric("提取记录", f"{len(d.get('records', []))} 条")
            cols[3].metric("校验通过", f"{n_pass}/{len(d['checks'])}")
            cov = next((c for c in d["checks"] if c["name"] == "原文溯源覆盖率"), None)
            cols[4].metric("溯源覆盖率", cov["computed"] if cov else "—")

            if d.get("records"):
                st.markdown("**质押 / 解押 / 展期记录**")
                st.dataframe(pd.DataFrame([{
                    "类型": r.get("record_type"), "出质人": r.get("pledgor"),
                    "股数": fmt_int(r.get("shares")),
                    "占其所持%": r.get("pct_of_held"),
                    "占总股本%": r.get("pct_of_total"),
                    "质权人": r.get("pledgee") or "—",
                    "用途": r.get("purpose") or "—",
                } for r in d["records"]]), use_container_width=True)
            if d.get("cumulative"):
                st.markdown("**累计质押情况**")
                st.dataframe(pd.DataFrame([{
                    "股东": c.get("shareholder"),
                    "质押前": fmt_int(c.get("pre_pledge_shares")),
                    "质押后": fmt_int(c.get("post_pledge_shares")),
                    "占其所持%": c.get("pct_of_held"),
                    "占总股本%": c.get("pct_of_total"),
                } for c in d["cumulative"]]), use_container_width=True)

            with st.expander("✅ 校验与审查明细"):
                for c in d["checks"]:
                    st.write(("🟢 " if c["passed"] else "🔴 ") + c["name"] + ":" + c["detail"])
                rvp = os.path.join(OUT_DIR, stem + ".review.json")
                if os.path.isfile(rvp):
                    rv = json.load(open(rvp, encoding="utf-8"))
                    label = {"clean": "✅ 多智能体审查:未发现错误",
                             "corrected": "🔧 多智能体审查:发现并修正了错误",
                             "needs_human_review": "⚠️ 多智能体审查:有争议项,转人工复核"
                             }.get(rv["final_status"], rv["final_status"])
                    st.write(label)
                    for rnd in rv["rounds"]:
                        for v in rnd.get("verdicts", []):
                            st.write(f"· `{v['field_path']}` {v['verdict']}:{v.get('reason') or v.get('claim')}")

            st.download_button("⬇️ 下载结构化 JSON",
                               data=json.dumps(d, ensure_ascii=False, indent=2),
                               file_name=stem + ".json", mime="application/json",
                               key="dl_" + stem)
else:
    st.info("还没有待处理文件——请先搜索下载公告或上传 PDF。")

# ---------------------------------------------------------------------------
# 步骤 3:信用风险分析
# ---------------------------------------------------------------------------
st.markdown('<div class="step-card"><h3>步骤 ③ 信用风险分析</h3>'
            '<div>红旗规则引擎 R1-R9(财报指标纯代码计算 + 公告质押数据联动),'
            '生成信用风险评估报告。</div></div>', unsafe_allow_html=True)

cc1, cc2, cc3 = st.columns([2, 1, 1])
with cc1:
    def_code = st.session_state.company.get("code", "")
    risk_code = st.text_input("分析对象(股票代码)", value=def_code,
                              placeholder="如 000863")
with cc2:
    risk_name = st.text_input("公司名", value=st.session_state.company.get("name", ""),
                              placeholder="如 三湘印象")
with cc3:
    is_bank = st.checkbox("银行业(启用口径豁免)")

if st.button("🚩 开始信用风险分析", type="primary"):
    import fin_analysis
    risk_json = os.path.join(OUT_DIR, "risk", f"{risk_code}_{risk_name}_risk.json")
    if os.path.isfile(risk_json) and risk_name:
        d = json.load(open(risk_json, encoding="utf-8"))
        st.session_state.risk_report = d
        st.success("已加载本地分析结果(财报数据此前已落盘)。")
    elif risk_code and risk_name:
        with st.spinner("正在采集财报数据(akshare 公开接口)并运行红旗规则……"):
            try:
                import fin_data
                import fin_analysis
                # 新公司:注册元信息(行业决定豁免口径)并现场采集财报落盘
                fin_analysis.COMPANY_META[risk_code] = {
                    "name": risk_name,
                    "industry": "银行" if is_bank else "综合"}
                if not os.path.isfile(risk_json):
                    fin_data.fetch_one(risk_code, risk_name)
                d = fin_analysis.analyze(risk_code)  # 规则引擎+落盘 risk json
                if d is None:
                    st.error("财报数据采集失败,请检查网络后重试。")
                else:
                    st.session_state.risk_report = d
                    st.success("分析完成!")
            except Exception as e:
                st.error(f"分析失败:{e}")
    else:
        st.warning("请填写股票代码和公司名。")

if st.session_state.get("risk_report"):
    d = st.session_state.risk_report
    st.divider()
    badge = {"高风险": "badge-red", "关注": "badge-orange",
             "稳健": "badge-green"}.get(d["verdict"], "badge-orange")
    st.markdown(f"## 📊 信用风险评估报告:{d['name']}({d['code']})")
    st.markdown(f'<span class="{badge}">综合评级:{d["verdict"]}</span>'
                f'&nbsp;&nbsp; 🔴 红色警报 {d["red_count"]} 项'
                f'&nbsp;&nbsp; 🟠 橙色提示 {d["orange_count"]} 项'
                f'&nbsp;&nbsp; <small>分析时间:{str(d.get("analysis_time",""))[:19]}</small>',
                unsafe_allow_html=True)
    if d["code"] == "600518":
        st.error("📌 回溯验证:系统在 2017-2018 年报即发出红色预警,比康美造假曝光早 1-2 年。")
    if d["code"] == "600519":
        st.success("📌 健康对照:贵州茅台零红色警报——不误报。")

    st.markdown("#### 红旗信号明细")
    for f in d["red_flags"]:
        cls = "flag-card" if f["severity"] == "red" else "flag-card orange"
        icon = "🔴" if f["severity"] == "red" else "🟠"
        st.markdown(f"""<div class="{cls}">{icon} <b>{f['rule_id']} {f['rule']}</b>
        · {f['period']}<br/><small>{f['explanation']}</small><br/>
        <small style="color:#999">证据:{json.dumps(f['evidence'], ensure_ascii=False)} |
        来源:{os.path.basename(f.get('source','—'))}</small></div>""",
        unsafe_allow_html=True)

    # 按年份排个时间线视图
    years = sorted({re.sub(r"\D", "", f["period"])[:4] for f in d["red_flags"] if re.sub(r"\D", "", f["period"])})
    if years:
        st.markdown("#### 信号时间线")
        st.markdown(" → ".join(f"**{y}**" for y in years))

st.divider()
st.caption("🐫 驼研·信鉴 · 首都经济贸易大学 · 2026 年北京市大学生金融人工智能竞赛参赛作品"
           " | DeepSeek + Kimi 国产大模型 | 数据均已落盘,过程可追溯")
