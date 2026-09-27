# -*- coding: utf-8 -*-
"""
llm_client.py —— LLM 调用客户端（OpenAI 兼容协议）
设计要点：
1) 配置四级回退：环境变量 DEEPSEEK_* → Streamlit st.secrets（云端部署）
   → FinSight-main/.env 的 DS_* → 抛异常
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

_temp_override = {}  # 端点级记忆：base_url → 该端点强制要求的 temperature


def _load_st_secrets() -> dict:
    """Streamlit Cloud 部署时从 st.secrets 读配置；本地/终端环境静默返回空。
    惰性 import:llm_client 同时被终端管线(run.py)使用,不能强依赖 streamlit。"""
    try:
        import streamlit as st
        return dict(st.secrets)
    except Exception:
        return {}


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
    """返回 (api_key, base_url, model)；未配置时 api_key 为空串。
    优先级：环境变量 DEEPSEEK_* > st.secrets > .env 的 DEEPSEEK_*/DS_*
    （DeepSeek 主力，temperature=0 可复现；DS_* 为 Kimi 等备用线路）"""
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    base = os.environ.get("DEEPSEEK_BASE_URL", "")
    model = os.environ.get("DEEPSEEK_MODEL", "")
    for src in (_load_st_secrets(), _load_env_file()):
        if key:
            break
        if src.get("DEEPSEEK_API_KEY"):
            key = src["DEEPSEEK_API_KEY"]
            base = base or src.get("DEEPSEEK_BASE_URL", "")
            model = model or src.get("DEEPSEEK_MODEL", "")
        elif src.get("DS_API_KEY"):
            key = src["DS_API_KEY"]
            base = base or src.get("DS_BASE_URL", "")
            model = model or src.get("DS_MODEL_NAME", "")
    return key, base or "https://api.deepseek.com", model or "deepseek-chat"


def get_alt_config():
    """返回备用线路 (api_key, base_url, model)，供多智能体审查的"质疑员"
    使用异构模型（与提取员不同模型，幻觉模式不同,互相覆盖盲区）。
    无备用配置时退回主力线路（角色对抗仍然成立，只是同模型）。"""
    for src in (_load_st_secrets(), _load_env_file()):
        key = src.get("DS_API_KEY", "")
        if key:
            return (key, src.get("DS_BASE_URL", "") or "https://api.moonshot.cn/v1",
                    src.get("DS_MODEL_NAME", "") or "kimi-for-coding")
    return get_config()


def chat_json(messages: list, trace=None, timeout: int = 240, cfg=None) -> str:
    """调用 chat/completions（JSON 输出模式），返回文本内容。
    cfg 可指定 (key, base, model) 覆盖默认线路（多智能体异构交叉用）。
    自动适配端点 temperature 限制；失败抛异常由上层回退规则路径。"""
    global _temp_override
    key, base, model = cfg if cfg else get_config()
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

    resp = _send(_temp_override.get(base, 0))
    if resp.status_code == 400 and "temperature" in resp.text:
        # 端点只允许特定 temperature（如 kimi-for-coding 仅允许 1）
        m = re.search(r"only ([\d.]+) is allowed", resp.text)
        forced = float(m.group(1)) if m else 1.0
        _temp_override[base] = forced
        if trace:
            trace.log("llm_quirk_adapt", quirk="temperature", forced=forced,
                      base_url=base)
        resp = _send(forced)
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    if trace:
        trace.log("llm_response", model=data.get("model", model),
                  usage=data.get("usage"), chars=len(content),
                  preview=content[:200])
    return content
