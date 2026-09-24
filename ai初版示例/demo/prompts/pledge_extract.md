# 股权质押公告结构化提取 Prompt（选题一 · 数据结构化提取）

你是金融投研数据团队的资深数据提取专家。请从下面的上市公司公告全文（可能来自
文本层 PDF 或扫描件 OCR，可能含有错别字）中提取股权质押相关信息，严格按照给定
JSON 结构输出。

## 输出 JSON 结构

{
  "doc_meta": {
    "sec_code": "证券代码，如 000863",
    "sec_name": "证券简称",
    "announcement_no": "公告编号",
    "announcement_date": "公告日期 YYYY-MM-DD",
    "doc_type": "质押公告 | 解除质押公告 | 质押及解除质押公告 | 质押展期公告"
  },
  "records": [
    {
      "record_type": "新增质押 | 解除质押 | 展期",
      "pledgor": "出质人名称",
      "is_controller": true,
      "pledgee": "质权人",
      "shares": 51000000,
      "pct_of_held": 30.67,
      "pct_of_total": 4.32,
      "is_restricted": false,
      "is_supplementary": false,
      "pledge_start_date": "YYYY-MM-DD",
      "pledge_end_date": "YYYY-MM-DD 或保留原文表述",
      "release_date": "YYYY-MM-DD（仅解除质押填）",
      "purpose": "质押用途",
      "evidence": "该记录对应的原文片段"
    }
  ],
  "cumulative": [
    {
      "shareholder": "股东名称",
      "holding_shares": 166275402,
      "holding_pct": 14.08,
      "pre_pledge_shares": 151000000,
      "post_pledge_shares": 127000000,
      "pct_of_held": 76.38,
      "pct_of_total": 10.76,
      "evidence": "原文片段"
    }
  ],
  "risk_items": [
    {"horizon": "未来半年内 | 未来一年内", "shares": 127000000,
     "finance_balance": 132000000, "evidence": "原文片段"}
  ]
}

## 格式规范（必须严格遵守）

1. 日期统一为 YYYY-MM-DD：2026.9.4 → 2026-09-04；2023 年 3 月 7 日 → 2023-03-07
2. 股份数量为整数股，去掉千分位逗号：51,000,000 → 51000000
3. 比例为数值，不带 % 号：30.67% → 30.67；不带 % 号但属比例列的值原样保留
4. 到期日为"质押期限至其办理解除质押登记手续之日止"等非日期表述时，原样保留
5. 每个字段都要给出 evidence（原文证据片段），表格记录用整行原文
6. 表格与正文信息冲突时以表格为准，并在 evidence 中注明
7. "合计"行不提取为记录，只提取具体股东行
8. 只输出一个 JSON 对象，不要输出任何解释文字或 markdown 围栏

## 少样本示例

输入："证券代码：000863 证券简称：三湘印象 公告编号：2026-042 三湘印象股份
有限公司关于实际控制人部分股份质押和解除质押的公告……股东黄辉为实际控制人，
本次质押股份数量 51,000,000 股，占其所持股份比例 30.67%，占公司总股本比例
4.32%，质押起始日 2026 年 9 月 4 日，质押到期日为质押期限至其办理解除质押
登记手续之日止，质权人为浙商银行股份有限公司上海分行，质押用途为自身生产
经营。"

输出：
{
  "doc_meta": {"sec_code": "000863", "sec_name": "三湘印象",
    "announcement_no": "2026-042", "announcement_date": "2026-09-08",
    "doc_type": "质押及解除质押公告"},
  "records": [
    {"record_type": "新增质押", "pledgor": "黄辉", "is_controller": true,
     "pledgee": "浙商银行股份有限公司上海分行", "shares": 51000000,
     "pct_of_held": 30.67, "pct_of_total": 4.32, "is_restricted": false,
     "is_supplementary": false, "pledge_start_date": "2026-09-04",
     "pledge_end_date": "质押期限至其办理解除质押登记手续之日止",
     "purpose": "自身生产经营",
     "evidence": "黄辉 | 是 | 51,000,000 | 30.67% | 4.32% | 否 | 否 | 2026.9.4 | 质押期限至其办理解除质押登记手续之日止 | 浙商银行股份有限公司上海分行 | 自身生产经营"}
  ],
  "cumulative": [],
  "risk_items": []
}

## 待提取的公告全文

{{TEXT}}
