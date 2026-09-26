# -*- coding: utf-8 -*-
"""
llm_client.py —— LLM 调用客户端（OpenAI 兼容协议）
设计要点：
1) 配置三级回退：环境变量 DEEPSEEK_* → FinSight-main/.env 的 DS_* → 抛异常
   （上层捕获后回退规则提取路径，保证管线任何时候可运行、可复现）
2) 端点差异自适应：如遇"仅允许特定 temperature"的端点（如 kimi-for-coding
   仅允许 temperature=1），自动探测、切换并会话级记忆
3) 全程 trace 留痕（对应竞赛"执行过程可追溯"要求）
"""
import os
import re

import requests

_ENV_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "..", "FinSight-main", ".env"),
]

_temp_override = None  # 会话级记忆：端点强制要求的 temperature


def _load_env_file() -> dict:
    """从候选 .env 文件读取配置（不覆盖已存在的环境变量）"""
    for p in _ENV_CANDIDATES:
        if os.path.isfile(p):
            cfg = {}
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        cfg[k.strip()] = v.strip().strip('"').strip("'")
            return cfg
    return {}


def get_config():
    """返回 (api_key, base_url, model)；未配置时 api_key 为空串"""
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    base = os.environ.get("DEEPSEEK_BASE_URL", "")
    model = os.environ.get("DEEPSEEK_MODEL", "")
    if not key:
        cfg = _load_env_file()
        key = cfg.get("DS_API_KEY", "")
        base = base or cfg.get("DS_BASE_URL", "")
        model = model or cfg.get("DS_MODEL_NAME", "")
    return key, base or "https://api.deepseek.com", model or "deepseek-chat"


def chat_json(messages: list, trace=None, timeout: int = 240) -> str:
    """调用 chat/completions（JSON 输出模式），返回文本内容。
    自动适配端点 temperature 限制；失败抛异常由上层回退规则路径。"""
    global _temp_override
    key, base, model = get_config()
    if not key:
        raise RuntimeError("未配置 LLM API Key（DEEPSEEK_API_KEY 或 .env 的 DS_API_KEY）")
    url = f"{base.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {key}"}

    def _send(temp):
        body = {"model": model, "messages": messages,
                "response_format": {"type": "json_object"}}
        if temp is not None:
            body["temperature"] = temp
        return requests.post(url, headers=headers, json=body, timeout=timeout)

    resp = _send(_temp_override if _temp_override is not None else 0)
    if resp.status_code == 400 and "temperature" in resp.text:
        # 端点只允许特定 temperature（如 kimi-for-coding 仅允许 1）
        m = re.search(r"only ([\d.]+) is allowed", resp.text)
        _temp_override = float(m.group(1)) if m else 1.0
        if trace:
            trace.log("llm_quirk_adapt", quirk="temperature", forced=_temp_override)
        resp = _send(_temp_override)
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    if trace:
        trace.log("llm_response", model=data.get("model", model),
                  usage=data.get("usage"), chars=len(content),
                  preview=content[:200])
    return content
