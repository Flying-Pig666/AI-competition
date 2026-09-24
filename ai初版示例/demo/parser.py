# -*- coding: utf-8 -*-
"""
parser.py —— 文档解析层（对应竞赛要求的"数据处理"模块）
支持两类输入：
1) 文本层 PDF（现代电子公告）→ pypdf 直接抽取文本；
2) 扫描件（无文本层 PDF 页 / 图片文件）→ 渲染后 RapidOCR 识别。
同时用 pdfplumber 提取表格结构，处理合并单元格与多级表头。
"""
import os
import sys

import fitz  # pymupdf：页面渲染
import pdfplumber
from pypdf import PdfReader

sys.stdout.reconfigure(encoding="utf-8")


def parse_pdf(path: str, trace=None) -> dict:
    """解析 PDF：逐页抽文本，文本层过少的页自动判定为扫描页并走 OCR。
    返回 {"method": "text_layer"|"mixed", "pages": [每页文本],
          "lines": [每页 OCR 行框坐标（文本层页为 None）]}"""
    pages = []
    methods = []
    lines_all = []
    reader = PdfReader(path)
    for i, page in enumerate(reader.pages):
        try:
            text = (page.extract_text() or "").strip()
        except Exception as e:
            text = ""
            if trace:
                trace.log("parse_warning", page=i + 1, reason=str(e)[:150])
        if len(text) < 40:  # 该页无有效文本层 → 扫描页
            if trace:
                trace.log("page_is_scanned", page=i + 1, chars=len(text))
            img = _render_page(path, i)
            text, lines = _ocr_image(img)
            os.remove(img)
            methods.append("ocr")
        else:
            lines = None
            methods.append("text_layer")
        pages.append(text)
        lines_all.append(lines)
    method = "text_layer" if all(m == "text_layer" for m in methods) else "mixed"
    if trace:
        trace.log("parse_pdf", file=os.path.basename(path), pages=len(pages),
                  method=method, chars_per_page=[len(p) for p in pages])
    return {"method": method, "pages": pages, "lines": lines_all}


def parse_scanned(images: list, trace=None) -> dict:
    """扫描件输入（图片列表，逐页 OCR）。
    返回 {"method": "ocr", "pages": [...], "lines": [每页 OCR 行框坐标]}"""
    pages = []
    lines_all = []
    for img in sorted(images):
        text, lines = _ocr_image(img)
        pages.append(text)
        lines_all.append(lines)
    if trace:
        trace.log("parse_scanned", files=[os.path.basename(i) for i in images],
                  pages=len(pages), chars_per_page=[len(p) for p in pages])
    return {"method": "ocr", "pages": pages, "lines": lines_all}


def extract_tables(path: str, trace=None) -> list:
    """提取表格结构（pdfplumber）。每个表格：{"page": 页码, "rows": [[cell,...]]}"""
    tables = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages):
            for tb in page.extract_tables():
                tables.append({"page": i + 1, "rows": tb})
    if trace:
        trace.log("parse_tables", file=os.path.basename(path), count=len(tables),
                  dims=[(len(t["rows"]), max(len(r) for r in t["rows"])) for t in tables])
    return tables


def _render_page(pdf_path: str, page_idx: int, dpi: int = 150) -> str:
    """渲染 PDF 某页为 PNG（用于 OCR）"""
    doc = fitz.open(pdf_path)
    pix = doc[page_idx].get_pixmap(dpi=dpi)
    tmp = f"{pdf_path}._p{page_idx + 1}.png"
    pix.save(tmp)
    doc.close()
    return tmp


_ocr = None


def _ocr_image(img_path: str):
    """RapidOCR 识别图片，返回 (拼接文本, [{'text','x0','y0','x1','y1'}, ...])"""
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()
    result, _ = _ocr(img_path)
    if not result:
        return "", []
    texts, boxes = [], []
    for item in result:
        box, txt = item[0], item[1]
        texts.append(txt)
        boxes.append({"text": txt,
                      "x0": box[0][0], "y0": box[0][1],
                      "x1": box[2][0], "y1": box[2][1]})
    return "\n".join(texts), boxes
