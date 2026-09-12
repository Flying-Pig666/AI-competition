# 股权质押公告数据结构化提取 —— 选题一最小可行案例（MVP）

从真实上市公司公告（文本层 PDF / 扫描件）中提取质押、解除质押、展期关键字段，
按标准 Schema 输出结构化 JSON，并完成跨字段勾稽一致性校验与全程可追溯日志。

## 项目结构

```
demo/
├── run.py            # 编排入口（智能体编排模块）
├── schema.py         # 标准数据结构定义（pydantic 强校验）
├── parser.py         # 文档解析层：文本层 PDF / 扫描件 OCR
├── ocr_tables.py     # 扫描件表格重建（OCR 行框坐标 → 表格结构）
├── extractor.py      # 提取层：LLM 提取（DeepSeek）+ 规则兜底
├── validator.py      # 校验层：格式规范化 + 勾稽一致性校验
├── tracing.py        # 可追溯日志（JSONL）
├── prompts/
│   └── pledge_extract.md   # LLM 提取 Prompt（比赛要求的 Prompt 模块）
├── data/             # 样本数据（真实公告，来自巨潮资讯网）
│   ├── 000863_三湘印象_质押和解除质押.pdf
│   ├── 601997_贵阳银行_质押展期.pdf
│   └── 扫描件_三湘印象/   # 由 PDF 渲染成的扫描件（逐页 PNG）
└── output/           # 运行产物：<文件名>.result.json + .trace.jsonl
```

## 运行方式

```bash
cd demo
python run.py data\000863_三湘印象_质押和解除质押.pdf   # 文本层 PDF
python run.py data\601997_贵阳银行_质押展期.pdf
python run.py data\扫描件_三湘印象                      # 扫描件目录（逐页 OCR）
```

### 启用 LLM 提取路径（推荐）

默认无 API Key 时自动回退规则提取。配置 DeepSeek Key 后走 LLM 提取：

```bash
set DEEPSEEK_API_KEY=sk-xxxx          # 或 export DEEPSEEK_API_KEY=...
python run.py data\000863_三湘印象_质押和解除质押.pdf
```

可选环境变量：`DEEPSEEK_BASE_URL`（默认 https://api.deepseek.com）、
`DEEPSEEK_MODEL`（默认 deepseek-chat）。Prompt 在 `prompts/pledge_extract.md`。

## 输出说明

每份公告产出两个文件：

1. **`*.result.json`** —— 结构化提取结果：
   - `doc_meta`：证券代码/简称、公告编号、公告日期、公告类型、解析方式
   - `records`：每笔质押/解除质押/展期记录（股东、质权人、股数、比例、
     日期、用途、是否控股股东/限售股/补充质押 + 原文证据）
   - `cumulative`：累计质押情况（持股数量、质押前后数量、占比）
   - `risk_items`：风险提示中未来半年/一年到期质押股数与融资余额
   - `checks`：一致性校验结论
2. **`*.trace.jsonl`** —— 可追溯日志：文件访问、解析方式、OCR、表格重建、
   工具调用、LLM 请求/响应、校验结论、结果落盘，逐事件记录。

## 校验项（对应竞赛"结果一致性"考察点）

1. 完整性：公告类型 ↔ 提取到的记录类型
2. 累计勾稽：本次质押后 = 本次质押前 − 解除 + 新增（展期不改股数）
3. 占其所持勾稽：质押股数 ÷ 持股数量 ≈ 公告披露比例
4. 占总股本勾稽：质押股数 ÷ 总股本（由持股比例反推）≈ 公告披露比例
5. 风险提示勾稽：未来一年内到期股数 == 累计质押后股数
6. 表格合计行勾稽：合计行 == 明细行加总 / 加权占比

## 已知限制（后续升级方向）

- 规则路径依赖表格结构；扫描件的表格由坐标重建，微小 OCR 噪声可能影响
  个别字段（如单字"是/否"单元格、竖排文本单元格识别不全）
- LLM 路径（接入 DeepSeek 后）可整体替换规则路径，直接从全文提取，
  对无表格结构公告的泛化能力更强
- 可扩展：PP-Structure 版面分析、更多公告类型（中标/股权变动）、
  跨公告勾稽（累计质押的连续性核对）、Web 界面与评测集
