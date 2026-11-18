"""
大模型客户端（OpenAI 兼容协议，只用标准库，无需额外依赖）。

兼容：DeepSeek、通义千问(DashScope 兼容模式)、Moonshot、智谱、OpenAI、Ollama 等。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import LLMConfig


@dataclass
class LLMReply:
    ok: bool
    text: str = ""
    error: str = ""


class LLMClient:
    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg

    @property
    def ready(self) -> bool:
        return self.cfg.enabled

    def chat(self, user_prompt: str, system_prompt: str = "", temperature: float | None = None) -> LLMReply:
        if not self.ready:
            return LLMReply(ok=False, error="未配置大模型 API Key，已使用内置知识库作答。")

        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": system_prompt or self.cfg.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.cfg.temperature if temperature is None else temperature,
            "stream": False,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.cfg.api_key}",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8", "replace"))
            text = body["choices"][0]["message"]["content"]
            return LLMReply(ok=True, text=text)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:500]
            except Exception:
                pass
            return LLMReply(ok=False, error=f"接口返回 HTTP {e.code}：{detail or e.reason}")
        except urllib.error.URLError as e:
            return LLMReply(ok=False, error=f"连接失败：{e.reason}（请检查网络与 base_url）")
        except Exception as e:
            return LLMReply(ok=False, error=f"调用出错：{type(e).__name__}: {e}")
