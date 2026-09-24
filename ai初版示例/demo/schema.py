# -*- coding: utf-8 -*-
"""
schema.py —— 标准数据结构定义
选题一要求"按照规定的数据结构输出"。本模块定义股权质押公告的结构化输出标准：
无论 LLM 提取还是规则兜底，最终结果都必须符合本 Schema（pydantic 强校验）。
"""
from typing import Optional, List
from pydantic import BaseModel, Field


class DocumentMeta(BaseModel):
    """文档元信息"""
    file_name: str = Field(..., description="源文件名")
    doc_type: str = Field(..., description="公告类型：质押公告 / 解除质押公告 / 质押及解除质押公告 / 质押展期公告")
    sec_code: Optional[str] = Field(None, description="证券代码，如 000863")
    sec_name: Optional[str] = Field(None, description="证券简称")
    announcement_no: Optional[str] = Field(None, description="公告编号")
    announcement_date: Optional[str] = Field(None, description="公告日期，格式 YYYY-MM-DD")
    parse_method: str = Field("text_layer", description="解析方式：text_layer 文本层 / ocr 扫描件")


class PledgeRecord(BaseModel):
    """单笔质押 / 解除质押 / 展期记录"""
    record_type: str = Field(..., description="新增质押 | 解除质押 | 展期")
    pledgor: Optional[str] = Field(None, description="出质人（股东名称）")
    is_controller: Optional[bool] = Field(None, description="是否为实际控制人/控股股东（是→True）")
    pledgee: Optional[str] = Field(None, description="质权人")
    shares: Optional[int] = Field(None, description="股份数量（股，整数）")
    pct_of_held: Optional[float] = Field(None, description="占其所持股份比例（%，数值）")
    pct_of_total: Optional[float] = Field(None, description="占公司总股本比例（%，数值）")
    is_restricted: Optional[bool] = Field(None, description="是否为限售股")
    is_supplementary: Optional[bool] = Field(None, description="是否为补充质押")
    pledge_start_date: Optional[str] = Field(None, description="质押起始日 YYYY-MM-DD")
    pledge_end_date: Optional[str] = Field(None, description="质押到期日 YYYY-MM-DD；如为'至办理解除质押登记手续之日止'则保留原文表述")
    original_end_date: Optional[str] = Field(None, description="原质押到期日（仅展期记录）YYYY-MM-DD")
    release_date: Optional[str] = Field(None, description="解除质押日期 YYYY-MM-DD")
    purpose: Optional[str] = Field(None, description="质押用途")
    evidence: Optional[str] = Field(None, description="字段来源的原文证据（表格行 / 正文片段）")


class CumulativePledge(BaseModel):
    """累计质押情况（截至公告披露日）"""
    shareholder: str = Field(..., description="股东名称")
    holding_shares: Optional[int] = Field(None, description="持股数量（股）")
    holding_pct: Optional[float] = Field(None, description="持股比例（%）")
    pre_pledge_shares: Optional[int] = Field(None, description="本次质押前累计质押股份数量（股）")
    post_pledge_shares: Optional[int] = Field(None, description="本次质押后累计质押股份数量（股）")
    pct_of_held: Optional[float] = Field(None, description="占其所持股份比例（%）")
    pct_of_total: Optional[float] = Field(None, description="占公司总股本比例（%）")
    evidence: Optional[str] = Field(None, description="原文证据")


class RiskItem(BaseModel):
    """风险提示中的到期质押信息"""
    horizon: str = Field(..., description="未来半年内 / 未来一年内")
    shares: Optional[int] = Field(None, description="到期质押股份数量（股）")
    finance_balance: Optional[int] = Field(None, description="对应融资余额（元）")
    evidence: Optional[str] = Field(None, description="原文证据")


class CheckResult(BaseModel):
    """一致性校验结果（对应竞赛"结果一致性"考察点）"""
    name: str = Field(..., description="校验项名称")
    passed: bool = Field(..., description="是否通过")
    detail: str = Field("", description="校验说明")
    expected: Optional[str] = Field(None, description="预期值")
    computed: Optional[str] = Field(None, description="实际计算值")


class ExtractionResult(BaseModel):
    """最终输出：一份公告的完整结构化提取结果"""
    doc_meta: DocumentMeta
    records: List[PledgeRecord] = Field(default_factory=list)
    cumulative: List[CumulativePledge] = Field(default_factory=list)
    risk_items: List[RiskItem] = Field(default_factory=list)
    checks: List[CheckResult] = Field(default_factory=list)

    def to_json(self) -> str:
        return self.model_dump_json(indent=2, ensure_ascii=False)
