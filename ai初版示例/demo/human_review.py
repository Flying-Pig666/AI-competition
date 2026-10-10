# -*- coding: utf-8 -*-
"""
human_review.py —— 人工复核层(Human-in-the-loop,第四道关)

管线上的四道把关:
  ① 程序勾稽校验(规则警察) ② 原文溯源(证据警察)
  ③ 多智能体质疑-裁决(AI 互查) ④ 人工复核(本模块,最终拍板)

前三道关自动运行;凡是机器"拿不准"的地方,本模块把它们整理成
人能看懂的问题清单(每个问题附:哪里不对、机器查到哪一步、建议怎么核对),
人只需对每个问题三选一:
  ✅ 确认无误           —— 提取值与公告一致,无疑义
  🏳️ 公告本身有误/矛盾  —— 数据忠于原文,矛盾记在公告头上(如惠发食品案例)
  ✏️ 我来修正           —— 人给出正确值,走与 AI 修正相同的安全通道
                           (字段路径白名单 + 类型对齐),随后自动重跑
                           全部勾稽校验与原文溯源

所有人工决定连同时间戳写回结果 JSON 的 human_review 段,全程留痕。
"""
import re

# 字段名 → 中文标签(给人看的下拉选项)
FIELD_LABELS = {
    "shares": "股数",
    "pct_of_held": "占其所持比例(%)",
    "pct_of_total": "占总股本比例(%)",
    "holding_shares": "持股数量",
    "holding_pct": "持股比例(%)",
    "pre_pledge_shares": "质押前累计质押数",
    "post_pledge_shares": "质押后累计质押数",
    "finance_balance": "融资余额(元)",
    "pledgor": "出质人/股东",
    "pledgee": "质权人",
    "pledge_start_date": "质押起始日",
    "pledge_end_date": "质押到期日",
    "release_date": "解除质押日期",
    "purpose": "质押用途",
    "announcement_date": "公告日期",
}

# 允许人工修正的字段(与 review_agents 白名单同一口径:只动数据叶子字段)
_EDITABLE = {
    "records": ["shares", "pct_of_held", "pct_of_total", "pledgor", "pledgee",
                "pledge_start_date", "pledge_end_date", "release_date", "purpose"],
    "cumulative": ["holding_shares", "pre_pledge_shares", "post_pledge_shares",
                   "pct_of_held", "pct_of_total"],
    "risk_items": ["shares", "finance_balance"],
}

SECTION_LABELS = {"records": "质押记录", "cumulative": "累计质押",
                  "risk_items": "到期压力"}


def collect_questions(result: dict, dossier: dict = None) -> list:
    """从提取结果 + 审查卷宗生成"待人工确认问题清单"。

    问题来源三类:
      A. 程序勾稽校验失败项  —— 等式失衡,一定有地方不对
      B. 原文溯源覆盖率不足  —— 部分字段在原文找不到出处
      C. 多智能体审查存疑项  —— AI 之间未达成一致/修正被程序拦下
    每个问题: {id, kind, title, detail, hint}
    """
    questions = []
    checks = result.get("checks", [])

    # A. 勾稽校验失败(溯源覆盖率单独作为 B 类处理)
    for c in checks:
        if c.get("passed") or c.get("name") == "原文溯源覆盖率":
            continue
        questions.append({
            "id": f"chk_{len(questions)}",
            "kind": "check_failed",
            "title": f"勾稽校验未通过:{c['name']}",
            "detail": c.get("detail", ""),
            "hint": ("请对照上方原文高亮标注核对该等式涉及的数字:"
                     "若提取值抄错了,选「我来修正」并填入原文中的正确数字;"
                     "若公告自身数据矛盾(等式用原文数字也算不平),"
                     "选「公告本身有误」——忠于原文同样是正确答案。"),
        })

    # B. 原文溯源不足
    cov = next((c for c in checks if c.get("name") == "原文溯源覆盖率"), None)
    if cov and not cov.get("passed"):
        questions.append({
            "id": f"gnd_{len(questions)}",
            "kind": "ungrounded",
            "title": "部分字段在公告原文中找不到出处",
            "detail": cov.get("detail", ""),
            "hint": ("未溯源最常见的原因是单位换算(如 132,000,000 元 ↔ 1.32 亿)"
                     "或 OCR 噪声,属正常现象;请在上方原文里找到对应表述,"
                     "确认数值一致后选「确认无误」即可;确实对不上再修正。"),
        })

    # C. 多智能体审查存疑(质疑成立但修正被程序防护拦下)
    if dossier:
        for it in dossier.get("rejected", []):
            if it.get("verdict") != "sustained_not_applied":
                continue
            questions.append({
                "id": f"rev_{len(questions)}",
                "kind": "review_dispute",
                "title": f"多智能体审查存疑:{it.get('field_path')}",
                "detail": (f"质疑员主张:{it.get('claim') or '—'};"
                           f"裁决员意见:{it.get('reason') or '—'};"
                           f"裁决所引原文:{it.get('adjudicator_evidence') or '—'}"),
                "hint": ("质疑员与裁决员认为这里有问题,但修正值未通过程序"
                         "证据核验(该数字在原文中不存在),故未自动采纳——"
                         "需要你回原文亲自拍板。"),
            })
    return questions


def editable_fields(result: dict) -> list:
    """列出全部允许人工修正的字段 → [(field_path, 显示标签, 当前值)]"""
    out = []
    for sec, fields in _EDITABLE.items():
        for i, item in enumerate(result.get(sec, [])):
            who = item.get("pledgor") or item.get("shareholder") \
                or item.get("horizon") or f"第{i + 1}条"
            for f in fields:
                if f not in item:
                    continue
                label = (f"{SECTION_LABELS[sec]}{i + 1}({who})·"
                         f"{FIELD_LABELS.get(f, f)}")
                out.append((f"{sec}.{i}.{f}", label, item.get(f)))
    return out


def parse_human_value(raw: str, old_value):
    """把用户输入的文本解析成合适类型:数字字段尽量转数字(容忍千分位逗号),
    日期/文本字段保持字符串。返回解析后的值。"""
    s = str(raw).strip()
    if isinstance(old_value, bool):
        return s
    if isinstance(old_value, (int, float)) or re.fullmatch(r"[\d,]+(\.\d+)?", s):
        try:
            num = float(s.replace(",", ""))
            # 原字段是 int 或输入无小数点 → int
            if isinstance(old_value, int) or num.is_integer():
                return int(num)
            return num
        except ValueError:
            pass
    return s
