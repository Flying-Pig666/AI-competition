# -*- coding: utf-8 -*-
"""
eval_collect.py —— 评测集采集工具（竞赛"数据来源可核验"要求）

从巨潮资讯网(证监会指定信息披露平台)检索并下载真实公告 PDF,
构建字段级 F1 评测所需的样本集。每条样本记录来源 URL 与检索参数,
全部留痕可追溯。

用法:
  python eval_collect.py                    # 默认采集最近一个月的质押公告
  python eval_collect.py --keyword 质押 --months 3 --limit 15
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8")

import requests

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "data", "eval_set")
SEARCH_URL = "http://www.cninfo.com.cn/new/fulltextSearch/full"
DOWNLOAD_BASE = "http://static.cninfo.com.cn/"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def search(keyword, sdate, edate, page=1):
    """巨潮全文检索,返回公告列表"""
    r = requests.post(SEARCH_URL, headers=HEADERS, timeout=20, data={
        "searchkey": keyword, "sdate": sdate, "edate": edate,
        "isfulltext": "false", "sortName": "pubdate",
        "sortType": "desc", "pageNum": page})
    r.raise_for_status()
    return (r.json().get("announcements") or [])


def clean_title(t):
    return re.sub(r"<[^>]+>", "", t or "").replace(" ", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", default="质押")
    ap.add_argument("--months", type=int, default=1)
    ap.add_argument("--limit", type=int, default=12)
    args = ap.parse_args()

    edate = datetime.now()
    sdate = edate - timedelta(days=30 * args.months)
    os.makedirs(OUT_DIR, exist_ok=True)
    meta_path = os.path.join(OUT_DIR, "eval_meta.jsonl")

    print(f"=== 评测集采集: 关键词[{args.keyword}] "
          f"{sdate:%Y-%m-%d} ~ {edate:%Y-%m-%d} ===")
    seen, got = set(), 0
    page = 1
    while got < args.limit and page <= 5:
        anns = search(args.keyword, sdate.strftime("%Y-%m-%d"),
                      edate.strftime("%Y-%m-%d"), page)
        if not anns:
            break
        for a in anns:
            if got >= args.limit:
                break
            code, name = a.get("secCode"), a.get("secName")
            title = clean_title(a.get("announcementTitle"))
            url_path = a.get("adjunctUrl")
            # 只要"质押/解除质押/展期"类纯公告,过滤年报等长文档
            if not url_path or "质押" not in title:
                continue
            if any(k in title for k in ("年度报告", "半年度报告", "季度报告")):
                continue
            key = (code, title)
            if key in seen:
                continue
            seen.add(key)
            fname = f"{code}_{name}_{title[:24]}.pdf"
            fname = re.sub(r'[\\/:*?"<>|]', "_", fname)
            fpath = os.path.join(OUT_DIR, fname)
            try:
                pr = requests.get(DOWNLOAD_BASE + url_path, headers=HEADERS,
                                  timeout=60)
                pr.raise_for_status()
                with open(fpath, "wb") as f:
                    f.write(pr.content)
                with open(meta_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "file": fname, "sec_code": code, "sec_name": name,
                        "title": title, "source_url": DOWNLOAD_BASE + url_path,
                        "announcement_time": datetime.fromtimestamp(
                            (a.get("announcementTime") or 0) / 1000).isoformat(),
                        "download_time": datetime.now().isoformat(),
                    }, ensure_ascii=False) + "\n")
                got += 1
                print(f"  ✓ [{got}] {code} {name} {title[:30]} "
                      f"({len(pr.content) // 1024} KB)")
                time.sleep(1)  # 礼貌限速
            except Exception as e:
                print(f"  ✗ {code} {name} 下载失败: {str(e)[:80]}")
        page += 1
    print(f"\n完成: 下载 {got} 份公告 → {OUT_DIR}")
    print(f"元数据留痕: {meta_path}")


if __name__ == "__main__":
    main()
