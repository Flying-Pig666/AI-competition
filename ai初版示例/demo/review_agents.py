# -*- coding: utf-8 -*-
"""
review_agents.py —— 多智能体审查层(质疑-裁决对抗机制)

设计思想(参考 FinSight 的 outline_critique/outline_refinement 批评循环与
VLM 图表反馈循环,并针对"数据提取"场景重新设计):

  提取员(LLM#1,主力模型)  ──产出──▶  结构化结果
       │                                │
       ▼                                ▼
  程序校验层(规则警察)         质疑员 Critic(LLM#2,异构模型,
  勾稽等式/原文溯源,硬证据      独立会话,只挑毛病,质疑必须引用原文)
       │                                │
       └──────────┬─────────────────────┘
                  ▼
        裁决员 Adjudicator(LLM#3)
        对每条质疑回原文亲自核对:成立→给修正值;不成立→驳回给理由
        (质疑员的证据也可能是编造的,裁决员不得偏信任何一方)
                  │
                  ▼
        应用修正 → 重跑程序校验 → 写入审查卷宗

关键设计:
1) 角色对抗:质疑员的任务就是找茬,与提取员"完成任务"的目标相反,
   独立会话避免自我审查的确认偏差;
2) 异构交叉:质疑员默认走备用线路(与提取员不同模型),不同模型的
   幻觉模式不同,互相覆盖盲区;
3) 证据强制:质疑与裁决都必须逐字引用原文,程序层会校验引用的
   原文是否真实存在——AI 的意见也要过规则这道关;
4) 共享卷宗(review dossier):智能体之间不直接对话,而是读写同一份
   卷宗(JSON + 人类可读 Markdown),卷宗即单一事实来源与留痕。
"""
import json
import os

import llm_client

DEMO_DIR = os.path.dirname(os.path.abspath(__file__))
MAX_REVIEW_ROUNDS = 2  # 质疑-裁决最多循环轮数

# ---------------------------------------------------------------------------
# 提示词(FinSight 治理风格:角色明确、只挑毛病、证据强制、输出格式约束)
# ---------------------------------------------------------------------------

CRITIC_PROMPT = """你是一位以挑剔著称的金融数据审计师,受聘独立审查一份上市公司公告的
数据提取结果。你的唯一目标是找出提取结果中的错误——不允许表扬,不允许提
改进建议,只允许指出有真凭实据的具体错误。

## 允许质疑的问题类型
1. 数字与公告原文不一致(抄错行、串行、单位错误、数字误植)
2. 遗漏原文中明确存在的质押/解除质押/展期记录(整条缺失)
3. 股东名称、质权人、日期、证券代码与原文不符
4. 个体口径与"控股股东及一致行动人合并"口径混淆

## 质疑格式(严格遵守)
每条质疑必须包含:
- field_path: 出错字段的路径,格式为 节名.序号.字段名,
  例如 records.0.shares(第一条记录的股数)、cumulative.1.pre_pledge_shares、
  doc_meta.announcement_date;整条记录缺失时用 records.MISSING。
  只允许指向数据字段,不得指向 checks(校验结果本身不是数据,对它质疑无意义)
- current_value: 当前提取值(记录缺失时填 null)
- claim: 你认为正确的值;记录缺失时简述缺失的内容
- evidence: 公告原文中支持你质疑的片段(必须逐字引用,不得改写)
- severity: high(数字性错误)/ medium(口径或遗漏)/ low(格式瑕疵)

## 重要提示:勾稽报警的排查方法
当程序校验报告"累计勾稽"失败(质押前 − 解除 + 新增 ≠ 质押后)时,
根因几乎都是某个累计字段提取错误。此时请回原文(尤其是"累计质押情况"
表格及"剩余被质押股份"等表述)核对:该股东【质押前累计质押数】的真实
值是多少?据此质疑 cumulative 中对应字段,并给出原文证据。
注意区分:"解除后剩余被质押数"≠"质押前累计数"——若原文说
"解除 X 股后剩余 Y 股",则质押前累计 = X + Y。

## 纪律
- 没有原文证据的怀疑一律不得提出;宁缺毋滥,结果没错就输出空列表
- 不得质疑"原文本身没有的信息"——公告没说就是没说,不算提取错误
- 输出仅为一个 JSON 对象: {"criticisms": [...]},不要任何其他文字

{{PROG_CHECKS}}

## 公告原文
{{TEXT}}

## 待审查的提取结果
{{RESULT}}"""

ADJUDICATOR_PROMPT = """你是金融数据合规委员会的资深裁决官。一位审计师对下面的数据提取结果
提出了若干条质疑,你的职责是逐条裁决。

## 裁决纪律(重要)
- 你必须亲自在公告原文中寻找依据,不得偏信提取结果,也不得偏信审计师——
  审计师引用的"证据"可能是编造或断章取义的
- 若质疑成立(原文证据支持审计师):verdict = "sustained",
  并给出 corrected_value(必须与原文完全一致;股数为整数、日期为 YYYY-MM-DD、
  比例为不带 % 的数值)
- 若质疑不成立:verdict = "rejected",一句话说明理由
- 裁决同样必须给出 evidence:支持你裁决的原文片段(逐字引用)

## 输出格式
仅为一个 JSON 对象,verdicts 与 criticisms 一一对应、顺序一致:
{"verdicts": [{"field_path": "...", "verdict": "sustained | rejected",
"corrected_value": <值或 null>, "evidence": "原文片段", "reason": "一句话"}]}

## 公告原文
{{TEXT}}

## 被质疑的提取结果
{{RESULT}}

## 审计师的质疑清单
{{CRITICISMS}}"""


# ---------------------------------------------------------------------------
# 字段路径解析与修正应用
# ---------------------------------------------------------------------------

def _parse_field_path(path: str):
    """把 'cumulative.1.pre_pledge_shares' 解析为 (cumulative, 1, pre_pledge_shares);
    'doc_meta.announcement_date' 解析为 (doc_meta, None, announcement_date)。
    非法路径返回 None。"""
    parts = str(path).strip().split(".")
    if len(parts) == 3 and parts[0] in ("records", "cumulative", "risk_items"):
        try:
            return parts[0], int(parts[1]), parts[2]
        except ValueError:
            return None
    if len(parts) == 2 and parts[0] == "doc_meta":
        return parts[0], None, parts[1]
    return None


def _apply_value(result, parsed, value):
    """按解析后的路径修正 result(pydantic 对象)的叶子字段,返回是否成功"""
    section, idx, field = parsed
    target = getattr(result, section, None)
    if target is None:
        return False
    if idx is not None:
        if idx >= len(target):
            return False
        target = target[idx]
    if not hasattr(target, field):
        return False
    old = getattr(target, field)
    # 类型对齐:原字段是数字则尽量转数字,失败则拒绝修正(防止引入脏数据)
    if isinstance(old, (int, float)) and not isinstance(old, bool):
        try:
            value = int(float(value)) if isinstance(old, int) else float(value)
        except (TypeError, ValueError):
            return False
    setattr(target, field, value)
    return True


# ---------------------------------------------------------------------------
# 质疑员 / 裁决员
# ---------------------------------------------------------------------------

def _short_text(full_text: str, limit: int = 24000) -> str:
    """控制上下文长度,避免超出模型窗口(公告一般不长,截断足够)"""
    return full_text if len(full_text) <= limit else full_text[:limit] + "\n……(后略)"


def run_critic(full_text: str, result_json: str, failed_checks: list = None,
               trace=None):
    """质疑员:独立会话 + 异构模型(备用线路),只挑毛病。
    failed_checks:程序校验层未通过的检查项文本——程序发现问题(勾稽失衡)、
    质疑员拿着线索去原文定位正确数字,三层机制由此咬合。
    返回 (criticisms 列表, 使用的模型名)。"""
    cfg = llm_client.get_alt_config()
    if failed_checks:
        prog = ("## 程序校验层已发现的异常(供你参考;仍需你亲自引原文为证,"
                "程序说你错不等于你真错)\n" + "\n".join(f"- {c}" for c in failed_checks))
    else:
        prog = "## 程序校验层结论\n全部通过,未发现异常。"
    prompt = (CRITIC_PROMPT.replace("{{TEXT}}", _short_text(full_text))
                           .replace("{{RESULT}}", result_json)
                           .replace("{{PROG_CHECKS}}", prog))
    raw = llm_client.chat_json([{"role": "user", "content": prompt}], trace=trace,
                               cfg=cfg)
    data = json.loads(raw)
    criticisms = data.get("criticisms", [])
    if not isinstance(criticisms, list):
        criticisms = []
    # 过滤格式不全的质疑(缺 field_path 或 evidence 的,视为无效质疑)
    criticisms = [c for c in criticisms
                  if isinstance(c, dict) and c.get("field_path") and c.get("evidence")]
    return criticisms, cfg[2]


def run_adjudicator(full_text: str, result_json: str, criticisms: list, trace=None):
    """裁决员:主力模型,逐条裁决质疑。返回 (verdicts 列表, 使用的模型名)。"""
    cfg = llm_client.get_config()
    prompt = (ADJUDICATOR_PROMPT.replace("{{TEXT}}", _short_text(full_text))
              .replace("{{RESULT}}", result_json)
              .replace("{{CRITICISMS}}", json.dumps(criticisms, ensure_ascii=False,
                                                    indent=1)))
    raw = llm_client.chat_json([{"role": "user", "content": prompt}], trace=trace,
                               cfg=cfg)
    data = json.loads(raw)
    verdicts = data.get("verdicts", [])
    if not isinstance(verdicts, list):
        verdicts = []
    return verdicts, cfg[2]


def _evidence_is_real(evidence: str, full_text: str) -> bool:
    """程序层核验:裁决引用的原文证据必须真实存在于原文(压缩空白后包含)。
    AI 的意见也要过规则这道关——证据造假则该裁决不予采纳。"""
    if not evidence or len(evidence.strip()) < 4:
        return False
    norm = lambda s: "".join(str(s).split()).replace(",", "").replace("，", "")
    return norm(evidence) in norm(full_text)


# ---------------------------------------------------------------------------
# 编排:质疑-裁决循环 + 共享卷宗
# ---------------------------------------------------------------------------

def review_extraction(result, full_text: str, trace=None):
    """对提取结果执行多智能体审查(质疑→裁决→修正→最多 MAX_REVIEW_ROUNDS 轮)。
    直接原地修正 result,返回审查卷宗 dict。"""
    dossier = {"rounds": [], "corrections": [], "rejected": [],
               "final_status": "clean"}
    # 质疑员模型只需取一次(结果 JSON 每轮会随修正更新)
    critic_model = llm_client.get_alt_config()[2]
    adj_model = llm_client.get_config()[2]

    for rnd in range(1, MAX_REVIEW_ROUNDS + 1):
        result_json = result.to_json()
        failed_checks = [f"{c.name}:{c.detail}" for c in result.checks
                         if not c.passed and c.name != "原文溯源覆盖率"]
        criticisms, _ = run_critic(full_text, result_json,
                                   failed_checks=failed_checks, trace=trace)
        if trace:
            trace.log("review_critic", round=rnd, model=critic_model,
                      n_criticisms=len(criticisms))
        if not criticisms:
            dossier["rounds"].append({"round": rnd, "critic_model": critic_model,
                                      "n_criticisms": 0})
            break

        verdicts, _ = run_adjudicator(full_text, result_json, criticisms,
                                      trace=trace)
        round_rec = {"round": rnd, "critic_model": critic_model,
                     "adjudicator_model": adj_model,
                     "criticisms": criticisms, "verdicts": []}
        n_applied = 0
        for c, v in zip(criticisms, verdicts):
            entry = {"field_path": c.get("field_path"),
                     "claim": c.get("claim"),
                     "critic_evidence": c.get("evidence"),
                     "severity": c.get("severity"),
                     "verdict": v.get("verdict"),
                     "reason": v.get("reason"),
                     "adjudicator_evidence": v.get("evidence")}
            if v.get("verdict") == "sustained":
                parsed = _parse_field_path(c.get("field_path", ""))
                evidence_ok = _evidence_is_real(v.get("evidence", ""), full_text)
                applied = False
                old = None
                if parsed and evidence_ok and v.get("corrected_value") is not None:
                    sec, idx, fld = parsed
                    tgt = getattr(result, sec)
                    tgt = tgt[idx] if idx is not None else tgt
                    old = getattr(tgt, fld)
                    applied = _apply_value(result, parsed, v["corrected_value"])
                entry.update({"evidence_verified": evidence_ok,
                              "applied": applied, "old_value": old,
                              "corrected_value": v.get("corrected_value")})
                if applied:
                    n_applied += 1
                    dossier["corrections"].append(entry)
                else:
                    # 质疑成立但证据核验失败/路径非法/类型不符 → 不予采纳,人工复核
                    entry["verdict"] = "sustained_not_applied"
                    dossier["rejected"].append(entry)
            else:
                dossier["rejected"].append(entry)
            round_rec["verdicts"].append(entry)
        if trace:
            trace.log("review_adjudicate", round=rnd, model=adj_model,
                      n_sustained=sum(1 for x in round_rec["verdicts"]
                                      if str(x["verdict"]).startswith("sustained")),
                      n_applied=n_applied)
        dossier["rounds"].append(round_rec)
        if n_applied == 0:
            break  # 本轮没有实际修正,无需再来一轮

    if dossier["corrections"]:
        dossier["final_status"] = "corrected"
    elif any(x["verdict"] == "sustained_not_applied" for x in dossier["rejected"]):
        dossier["final_status"] = "needs_human_review"
    return dossier


def dossier_to_markdown(dossier: dict, doc_name: str) -> str:
    """把审查卷宗渲染成人类可读的 Markdown(演示与留痕用)"""
    lines = [f"# 多智能体审查卷宗:{doc_name}", ""]
    status_zh = {"clean": "✅ 未发现错误", "corrected": "🔧 发现并已修正错误",
                 "needs_human_review": "⚠️ 有争议项,需人工复核"}
    lines.append(f"**最终结论:{status_zh.get(dossier['final_status'], dossier['final_status'])}**")
    lines.append("")
    for rnd in dossier["rounds"]:
        lines.append(f"## 第 {rnd['round']} 轮(质疑员:{rnd.get('critic_model', '?')}"
                     f" / 裁决员:{rnd.get('adjudicator_model', '-')})")
        if rnd.get("n_criticisms") == 0:
            lines.append("质疑员未提出质疑,审查通过。")
        for v in rnd.get("verdicts", []):
            mark = {"sustained": "🔴 成立", "sustained_not_applied": "🟡 成立但未采纳(需人工)",
                    "rejected": "⚪ 驳回"}.get(v["verdict"], v["verdict"])
            lines.append(f"- `{v['field_path']}` {mark}:{v.get('reason') or v.get('claim')}")
            if v["verdict"] == "sustained" and v.get("applied"):
                lines.append(f"  - 修正:{v.get('old_value')} → {v.get('corrected_value', '(见卷宗JSON)')}")
        lines.append("")
    return "\n".join(lines)


def save_dossier(dossier: dict, doc_name: str, out_dir: str):
    """卷宗落盘:JSON(程序用)+ Markdown(人看)"""
    stem = os.path.splitext(os.path.basename(doc_name))[0]
    jpath = os.path.join(out_dir, stem + ".review.json")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(dossier, f, ensure_ascii=False, indent=2)
    mpath = os.path.join(out_dir, stem + ".review.md")
    with open(mpath, "w", encoding="utf-8") as f:
        f.write(dossier_to_markdown(dossier, doc_name))
    return jpath, mpath
