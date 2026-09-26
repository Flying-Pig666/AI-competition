# -*- coding: utf-8 -*-
"""
fin_data.py —— 数据采集层（对应竞赛要求的"数据处理"模块，选题二的数据底座）

按股票代码拉取 A 股上市公司关键财务数据（akshare，公开数据源），落盘为 CSV。
每次采集生成 fetch_meta.json（数据源、采集时间、行数）——保证数据来源可核验、
结果可复现（对应竞赛"可验证、可追溯、可复现"要求）。

双源策略（对抗单一接口失效）：
1) 同花顺财务摘要(stock_financial_abstract_ths)：净利润、扣非净利润、
   营业总收入、每股经营现金流等（扣非净利润是识别"非经常性损益"的关键）
2) 新浪财务指标(stock_financial_analysis_indicator)：ROE、毛利率、
   资产负债率等 86 项指标

用法：
  python fin_data.py              # 采集全部案例公司
  python fin_data.py 600519       # 只采集指定公司
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(encoding="utf-8")

import akshare as ak
import warnings
warnings.filterwarnings("ignore")

OUT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "data", "financials")

# 案例公司清单（见《备赛作战计划.md》）
COMPANIES = {
    "000863": "三湘印象",    # 工程主案例（质押+解押公告已在手）
    "601997": "贵阳银行",    # 展期公告案例
    "600518": "康美药业",    # 爆雷回溯案例（300亿造假,利润与现金流背离教科书）
    "600519": "贵州茅台",    # 健康对照组
}

START_YEAR = "2019"  # 爆雷回溯需要覆盖造假年份(康美造假集中在2016-2018,但接口早期数据稀疏,尽量往前)


def fetch_one(code: str, name: str) -> dict:
    """采集一家公司的双源财务数据，返回元数据"""
    out_dir = os.path.join(OUT_ROOT, f"{code}_{name}")
    os.makedirs(out_dir, exist_ok=True)
    meta = {"code": code, "name": name,
            "fetch_time": datetime.now(timezone(timedelta(hours=8))).isoformat(),
            "sources": []}

    # 源 1：同花顺财务摘要（按报告期）
    try:
        df = ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期")
        path = os.path.join(out_dir, "abstract_ths.csv")
        df.to_csv(path, index=False, encoding="utf-8-sig")
        meta["sources"].append({"name": "同花顺财务摘要", "file": "abstract_ths.csv",
                                "rows": len(df), "status": "ok"})
        print(f"  ✓ {name} 同花顺摘要: {len(df)} 行")
    except Exception as e:
        meta["sources"].append({"name": "同花顺财务摘要", "status": "fail",
                                "error": str(e)[:150]})
        print(f"  ✗ {name} 同花顺摘要失败: {str(e)[:100]}")

    # 源 2：新浪财务指标（ROE/毛利率/资产负债率等）
    try:
        df = ak.stock_financial_analysis_indicator(symbol=code, start_year=START_YEAR)
        path = os.path.join(out_dir, "indicators_sina.csv")
        df.to_csv(path, index=False, encoding="utf-8-sig")
        meta["sources"].append({"name": "新浪财务指标", "file": "indicators_sina.csv",
                                "rows": len(df), "cols": len(df.columns),
                                "status": "ok"})
        print(f"  ✓ {name} 新浪指标: {len(df)} 行 × {len(df.columns)} 列")
    except Exception as e:
        meta["sources"].append({"name": "新浪财务指标", "status": "fail",
                                "error": str(e)[:150]})
        print(f"  ✗ {name} 新浪指标失败: {str(e)[:100]}")

    with open(os.path.join(out_dir, "fetch_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


def main():
    codes = sys.argv[1:] or list(COMPANIES)
    print(f"=== 财务数据采集（数据源: akshare {ak.__version__}）===")
    results = {}
    for code in codes:
        name = COMPANIES.get(code, "未知")
        print(f"[{code} {name}]")
        results[code] = fetch_one(code, name)
    n_ok = sum(1 for m in results.values()
               if any(s.get("status") == "ok" for s in m["sources"]))
    print(f"\n完成: {n_ok}/{len(codes)} 家公司至少一个数据源成功 → {OUT_ROOT}")


if __name__ == "__main__":
    main()
