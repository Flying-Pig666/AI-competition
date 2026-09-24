# -*- coding: utf-8 -*-
"""
validator.py —— 校验层（对应竞赛"格式规范性、结果一致性"考察点）
跨字段勾稽一致性校验：
1) 完整性      ：公告类型 ↔ 提取到的记录类型是否匹配
2) 累计勾稽    ：本次质押后 = 本次质押前 − 解除 + 新增（展期不改变股数）
3) 占其所持勾稽：质押股数 / 持股数量 ≈ 占其所持比例
4) 占总股本勾稽：质押股数 / 总股本 ≈ 占总股本比例（总股本由持股数量÷持股比例反推）
5) 风险提示勾稽：未来一年内到期股数 == 累计质押后股数
6) 表格合计勾稽：表格"合计"行 == 明细行加总
"""
from schema import CheckResult

TOL = 0.01  # 相对误差容忍 1%（公告比例只保留 2-4 位小数，存在舍入）


def _check(checks, name, passed, detail="", expected=None, computed=None):
    checks.append(CheckResult(name=name, passed=passed, detail=detail,
                              expected=expected, computed=computed))


def _rel_err(a, b):
    if not a or not b:
        return None
    return abs(a - b) / abs(b)


def _same_shareholder(pledgor, shareholder):
    """OCR 变体下的股东名匹配（双向包含）"""
    if not pledgor or not shareholder:
        return False
    return pledgor in shareholder or shareholder in pledgor


def run_checks(result, tables=None, trace=None):
    checks = []
    recs = result.records
    cums = result.cumulative

    # 1. 完整性：公告类型 ↔ 记录类型
    dt = result.doc_meta.doc_type
    kinds = {r.record_type for r in recs}
    kinds_str = "、".join(sorted(kinds)) or "（无记录）"
    if "展期" in dt and "展期" not in kinds:
        _check(checks, "完整性-记录类型", False,
               f"公告类型为'{dt}'，但未提取到展期记录", expected="展期", computed=kinds_str)
    elif "解除质押" in dt and "解除质押" not in kinds:
        _check(checks, "完整性-记录类型", False,
               f"公告类型为'{dt}'，但未提取到解除质押记录", expected="解除质押", computed=kinds_str)
    elif "质押及" in dt and ("新增质押" not in kinds or "解除质押" not in kinds):
        _check(checks, "完整性-记录类型", False,
               f"公告类型为'{dt}'，但提取到的记录类型不完整", expected="新增质押+解除质押", computed=kinds_str)
    else:
        _check(checks, "完整性-记录类型", True,
               f"公告类型'{dt}'与记录类型{{{kinds_str}}}匹配")

    if not cums:
        detail = "未提取到累计质押表，跳过勾稽校验（扫描件需 LLM 提取或版面表格识别）"
        _check(checks, "累计勾稽", False, detail)
        _check(checks, "占其所持勾稽", False, detail)
        _check(checks, "占总股本勾稽", False, detail)
        if trace:
            trace.log("validation", checks=[c.model_dump() for c in checks])
        result.checks = checks
        return

    for c in cums:
        mine = [r for r in recs if _same_shareholder(r.pledgor, c.shareholder)]
        label = c.shareholder

        # 2. 累计勾稽
        new_sum = sum(r.shares or 0 for r in mine if r.record_type == "新增质押")
        rel_sum = sum(r.shares or 0 for r in mine if r.record_type == "解除质押")
        if c.pre_pledge_shares is not None and c.post_pledge_shares is not None:
            if not mine:
                # 无新增/解除记录（如纯展期）：前后应不变
                ok = c.pre_pledge_shares == c.post_pledge_shares
                _check(checks, f"累计勾稽-{label}", ok,
                       f"无新增/解除记录，本次质押前 {c.pre_pledge_shares:,} == 本次质押后 {c.post_pledge_shares:,}",
                       expected=str(c.pre_pledge_shares), computed=str(c.post_pledge_shares))
            else:
                computed = c.pre_pledge_shares - rel_sum + new_sum
                ok = computed == c.post_pledge_shares
                _check(checks, f"累计勾稽-{label}", ok,
                       f"{c.pre_pledge_shares:,} − {rel_sum:,}(解除) + {new_sum:,}(新增) = {computed:,}，"
                       f"公告披露为 {c.post_pledge_shares:,}",
                       expected=str(c.post_pledge_shares), computed=str(computed))

        # 3. 占其所持比例勾稽
        if c.holding_shares:
            for r in mine:
                if r.shares and r.pct_of_held is not None:
                    computed = r.shares / c.holding_shares * 100
                    err = _rel_err(computed, r.pct_of_held)
                    ok = err is not None and err <= TOL
                    _check(checks, f"占其所持勾稽-{label}", ok,
                           f"{r.record_type} {r.shares:,}股 ÷ 持股 {c.holding_shares:,}股 = "
                           f"{computed:.4f}%，公告披露 {r.pct_of_held}%（误差 {err:.2%}）",
                           expected=str(r.pct_of_held), computed=f"{computed:.4f}%")

        # 4. 占总股本比例勾稽（总股本 = 持股数量 ÷ 持股比例）
        if c.holding_shares and c.holding_pct:
            total_shares = c.holding_shares / (c.holding_pct / 100)
            for r in mine:
                if r.shares and r.pct_of_total is not None:
                    computed = r.shares / total_shares * 100
                    err = _rel_err(computed, r.pct_of_total)
                    ok = err is not None and err <= TOL
                    _check(checks, f"占总股本勾稽-{label}", ok,
                           f"{r.record_type} {r.shares:,}股 ÷ 总股本 {total_shares:,.0f}股"
                           f"（由持股比例反推） = {computed:.4f}%，公告披露 {r.pct_of_total}%",
                           expected=str(r.pct_of_total), computed=f"{computed:.4f}%")

    # 5. 风险提示勾稽：未来一年内到期股数 == 累计质押后股数
    for item in result.risk_items:
        if not item.shares:
            continue
        matched = next((c for c in cums if c.post_pledge_shares == item.shares), None)
        if matched:
            _check(checks, f"风险提示勾稽-{matched.shareholder}", True,
                   f"{item.horizon}到期 {item.shares:,}股 与 累计质押后 {matched.post_pledge_shares:,}股一致",
                   expected=str(item.shares), computed=str(matched.post_pledge_shares))
        else:
            _check(checks, f"风险提示勾稽-{item.horizon}", False,
                   f"{item.horizon}到期 {item.shares:,}股 未在任何股东累计质押中匹配到",
                   expected=str(item.shares), computed="无匹配")

    # 6. 表格"合计"行勾稽（复用提取层的表格解析结果）
    from extractor import _parse_shares, _parse_pct
    for tt in getattr(result, "_table_totals", []):
        total_row, cmap, data_rows, kind = tt["total_row"], tt["col_map"], tt["data_rows"], tt["kind"]

        def cell(row, ci):
            return (row[ci] or "").replace("\n", "").strip() if ci < len(row) else ""

        def col_sum(f):
            ci = cmap[f]
            return sum(v for v in (_parse_shares(cell(r, ci)) for r in data_rows)
                       if v is not None)

        if kind == "cumulative":
            # 股数列：明细加总 == 合计行
            for f in ("holding_shares", "pre_pledge_shares", "post_pledge_shares"):
                if f not in cmap:
                    continue
                total_v = cell(total_row, cmap[f])
                if not total_v or total_v == "-":
                    continue
                computed, expected = col_sum(f), _parse_shares(total_v)
                ok = computed == expected
                _check(checks, f"合计行加总-{f}", ok,
                       f"明细加总 {computed:,} vs 合计行 {expected:,}",
                       expected=str(expected), computed=str(computed))
            # 加权占比：合计占其所持 = Σ质押后 ÷ Σ持股（比例不能直接相加）
            if {"post_pledge_shares", "holding_shares", "pct_of_held"} <= set(cmap):
                post_sum, hold_sum = col_sum("post_pledge_shares"), col_sum("holding_shares")
                expected = _parse_pct(cell(total_row, cmap["pct_of_held"]))
                if hold_sum and expected is not None:
                    computed = post_sum / hold_sum * 100
                    ok = abs(computed - expected) <= 0.01
                    _check(checks, "合计行加权-占其所持比例", ok,
                           f"Σ质押后 {post_sum:,} ÷ Σ持股 {hold_sum:,} = {computed:.4f}% vs 合计行 {expected}%",
                           expected=str(expected), computed=f"{computed:.4f}%")
            if {"post_pledge_shares", "holding_pct", "pct_of_total"} <= set(cmap):
                hold_pct_t = _parse_pct(cell(total_row, cmap["holding_pct"]))
                expected = _parse_pct(cell(total_row, cmap["pct_of_total"]))
                if hold_pct_t and expected is not None:
                    total_shares = col_sum("holding_shares") / (hold_pct_t / 100)
                    computed = col_sum("post_pledge_shares") / total_shares * 100
                    ok = abs(computed - expected) <= 0.01
                    _check(checks, "合计行加权-占总股本比例", ok,
                           f"Σ质押后 ÷ 总股本（由Σ持股÷持股比例反推 {total_shares:,.0f}股） = "
                           f"{computed:.4f}% vs 合计行 {expected}%",
                           expected=str(expected), computed=f"{computed:.4f}%")
        else:
            # 记录表：股数与比例列均可直接加总
            for f in ("shares", "pct_of_held", "pct_of_total"):
                if f not in cmap:
                    continue
                total_v = cell(total_row, cmap[f])
                if not total_v or total_v == "-":
                    continue
                if f == "shares":
                    computed, expected = col_sum(f), _parse_shares(total_v)
                    ok = computed == expected
                    _check(checks, f"合计行加总-{f}", ok,
                           f"明细加总 {computed:,} vs 合计行 {expected:,}",
                           expected=str(expected), computed=str(computed))
                else:
                    ci = cmap[f]
                    vals = [v for v in (_parse_pct(cell(r, ci)) for r in data_rows)
                            if v is not None]
                    computed, expected = round(sum(vals), 4), _parse_pct(total_v)
                    ok = abs(computed - expected) <= 0.01
                    _check(checks, f"合计行加总-{f}", ok,
                           f"明细加总 {computed} vs 合计行 {expected}",
                           expected=str(expected), computed=str(computed))

    if trace:
        trace.log("validation", checks=[c.model_dump() for c in checks])
    result.checks = checks
