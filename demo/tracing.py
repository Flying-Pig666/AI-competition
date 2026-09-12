# -*- coding: utf-8 -*-
"""
tracing.py —— 可追溯日志模块
以 JSONL 追加记录每一次运行的全过程事件：
文件访问、解析方式、工具调用、LLM 请求/响应、规则提取、校验结论、结果落盘。
对应竞赛要求"完整记录文件访问、工具调用、计算过程和结果生成情况，
确保数据来源可核验、执行过程可追溯、运行结果可复现"。
"""
import json
import time


class Trace:
    def __init__(self, path: str):
        self.path = path
        open(path, "w", encoding="utf-8").close()  # 每次运行重开日志，保证可复现
        self.seq = 0

    def log(self, event: str, **detail) -> dict:
        self.seq += 1
        rec = {"seq": self.seq,
               "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
               "event": event,
               **detail}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        return rec
