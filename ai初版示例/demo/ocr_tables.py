# -*- coding: utf-8 -*-
"""
ocr_tables.py —— 扫描件表格重建（轻量版面表格识别）
扫描件经 OCR 后没有结构信息。本模块利用 OCR 行框坐标（x0,y0,x1,y1）重建表格：
1) 以"数字/百分比/日期/合计"碎片为行锚点，按 y 聚类成行，再迭代吸收邻近
   文本碎片（换行单元格、股东名等）扩展为完整数据行；
2) 表头碎片按 x 重叠聚合成"列"，纵向拼接还原完整表头文字；
3) 每个数据行按 x 中心落入的列区间分配单元格；
4) 输出与 pdfplumber 相同格式的 tables，供提取层复用同一套解析逻辑。
"""
import re
from statistics import median

# 数据碎片：是/否、纯数字、百分比、日期
DATA_PAT = re.compile(r"^(是|否)$|^[\d,.\s]+%?$|\d{4}[\s.]*\d{1,2}[\s.]*\d{1,2}")
# 节标题等非表头行（如 "1、本次股份质押的基本情况"）
SECTION_PAT = re.compile(r"^\d+、|基本情况|事项如下")


def reconstruct(lines, page_no):
    """lines: [{'text','x0','y0','x1','y1'}, ...]（同一页的 OCR 行）
    返回 [{'page': n, 'rows': [[...]], 'from': 'ocr'}]"""
    if not lines:
        return []
    heights = [l["y1"] - l["y0"] for l in lines]
    mh = median(heights) if heights else 20

    def is_anchor(l):
        return DATA_PAT.search(l["text"]) or l["text"].strip() == "合计"

    # 1) 行锚点聚类（数字/日期等碎片在行内 y 分布很集中）
    anchors = sorted([l for l in lines if is_anchor(l)], key=lambda l: l["y0"])
    clusters, cluster = [], []
    last_bottom = None
    for l in anchors:
        if last_bottom is not None and l["y0"] - last_bottom > 1.2 * mh:
            clusters.append(cluster)
            cluster = []
        cluster.append(l)
        last_bottom = l["y1"] if last_bottom is None else max(last_bottom, l["y1"])
    if cluster:
        clusters.append(cluster)
    if not clusters:
        return []

    # 2) 表带切分：相邻锚点簇间隙 > 4*mh 视为两张表
    #    （表格内行距可因换行单元格拉大，需放宽；表间有表头+节标题，间隔更大）
    bands, band = [], []
    prev_bottom = None
    for cl in clusters:
        top = min(l["y0"] for l in cl)
        if prev_bottom is not None and top - prev_bottom > 4 * mh:
            bands.append(band)
            band = []
        band.append(cl)
        prev_bottom = max(l["y1"] for l in cl)
    if band:
        bands.append(band)

    # 3) 逐表重建
    tables = []
    for band in bands:
        # 表头/数据分界：锚点最高处上方 0.3*mh；换行单元格可上探 4*mh
        data_top = min(l["y0"] for cl in band for l in cl) - 0.3 * mh
        data_floor = data_top - 4 * mh
        # 数据侧碎片：位于 data_floor 以下的才允许进入数据行（表头碎片不吸收）
        data_side = [l for l in lines if l["y0"] >= data_floor]
        # 本表锚点（含全部簇）；扩展时不得吸收其他簇的锚点
        band_anchors = {id(l) for cl in band for l in cl}
        band_x0 = min(l["x0"] for cl in band for l in cl)
        band_x1 = max(l["x1"] for cl in band for l in cl)
        band_w = band_x1 - band_x0
        # 3a) 扩展每行：只吸收数据侧碎片；不吸收他行锚点与全宽正文行；
        #     碎片行间独占（如横跨两行的单元格内容只归属先处理的上方行）
        rows = []
        claimed = set()
        for cl in band:
            top = min(l["y0"] for l in cl)
            bottom = max(l["y1"] for l in cl)
            row, ids = list(cl), {id(l) for l in cl}
            changed = True
            while changed:
                changed = False
                for l in data_side:
                    if id(l) in ids or id(l) in claimed:
                        continue
                    if id(l) in band_anchors:  # 其他数据行的锚点，不吸收
                        continue
                    if (l["x1"] - l["x0"]) > 0.5 * band_w:  # 全宽正文行，不吸收
                        continue
                    if l["y1"] >= top - mh and l["y0"] <= bottom + mh:
                        row.append(l)
                        ids.add(id(l))
                        top = min(top, l["y0"])
                        bottom = max(bottom, l["y1"])
                        changed = True
            claimed |= ids
            rows.append(sorted(row, key=lambda l: l["x0"]))
        x0_min = min(l["x0"] for row in rows for l in row)
        x1_max = max(l["x1"] for row in rows for l in row)
        bw = x1_max - x0_min
        # 3b) 表头碎片：位于 data_floor 上方 20*mh 以内、非节标题、宽度 < 35% 表宽
        cand = [l for l in lines
                if l["y0"] < data_floor and l["y0"] >= data_top - 20 * mh
                and x0_min - 20 <= l["x0"] and l["x1"] <= x1_max + 20
                and (l["x1"] - l["x0"]) < 0.35 * bw
                and not SECTION_PAT.search(l["text"])]
        if not cand:
            continue
        # 3c) 列聚合：x 重叠超过 40%*较小宽度则并入
        cols = []
        for l in sorted(cand, key=lambda l: l["x0"]):
            w = l["x1"] - l["x0"]
            for col in cols:
                ov = min(l["x1"], col["x1"]) - max(l["x0"], col["x0"])
                if ov > 0.4 * min(w, col["w"]):
                    col["frags"].append(l)
                    col["x0"] = min(col["x0"], l["x0"])
                    col["x1"] = max(col["x1"], l["x1"])
                    col["w"] = col["x1"] - col["x0"]
                    break
            else:
                cols.append({"x0": l["x0"], "x1": l["x1"], "w": w,
                             "frags": [l]})
        cols.sort(key=lambda c: c["x0"])
        # 3d) 表头行：每列碎片按 y 排序拼接（去除 OCR 多余空格）
        headers = ["".join(f["text"] for f in sorted(col["frags"],
                                                     key=lambda f: f["y0"])).replace(" ", "")
                   for col in cols]
        # 3e) 数据行：每列取 x 中心落入列区间的碎片，按 y 拼接
        out_rows = []
        for row in rows:
            cells = []
            for col in cols:
                frags = [l for l in row
                         if col["x0"] - 5 <= (l["x0"] + l["x1"]) / 2 <= col["x1"] + 5]
                txt = "".join(f["text"] for f in sorted(frags,
                                                        key=lambda f: f["y0"])).replace(" ", "")
                cells.append(txt or None)
            out_rows.append(cells)
        out_rows.insert(0, headers)
        tables.append({"page": page_no, "rows": out_rows, "from": "ocr"})
    return tables
