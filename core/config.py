"""
全局配置：大模型接入信息与运行参数。

支持任意 OpenAI 兼容接口（DeepSeek / 通义千问 / Moonshot / 智谱 / OpenAI ...），
只需配置 base_url + api_key + model 即可。

优先级：环境变量 > 配置文件 config.json > 内置默认值
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config.json"
# 错题集等本地数据；云部署时可用 DATA_DIR 环境变量指到挂载盘
DATA_DIR = Path(os.environ.get("DATA_DIR") or (BASE_DIR / "data"))


DEFAULT_SYSTEM_PROMPT = """你是一位耐心的编程启蒙老师，专门帮助零基础新手看懂并修好代码报错。

你的讲解必须遵守以下规则：
1. 说话对象是"完全没学过编程课"的新手，禁止堆砌术语；必须出现的术语要立刻用生活化比喻解释。
2. 必须逐行翻译报错信息，把英文原文拆开解释每个单词/符号的含义。
3. 必须指出"错在哪一行的哪个字符/哪个词"，不要笼统地说"语法有问题"。
4. 给出修改后的完整可运行代码（不要只给片段），并说明"改了哪里、为什么这样改"。
5. 如果存在多个错误，按"先解决第一个"的顺序讲，因为后面的错误常常是前面引起的连锁反应。
6. 不要编造错误。如果工具已经真实运行过代码，你看到的是真实结果，必须基于真实结果讲解。
7. 最后用一句话总结这次要记住的知识点（方便新手做错题本）。

输出统一使用 Markdown，中文回答。"""


@dataclass
class LLMConfig:
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    model: str = "deepseek-chat"
    temperature: float = 0.3
    timeout: int = 120
    system_prompt: str = DEFAULT_SYSTEM_PROMPT

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.model and self.base_url)


def load_config() -> LLMConfig:
    cfg = LLMConfig()

    if CONFIG_FILE.exists():
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            for k, v in raw.items():
                if hasattr(cfg, k) and v not in (None, ""):
                    setattr(cfg, k, v)
        except Exception:
            pass

    # 环境变量覆盖
    env_map = {
        "base_url": ("LLM_BASE_URL", "OPENAI_BASE_URL"),
        "api_key": ("LLM_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY"),
        "model": ("LLM_MODEL", "OPENAI_MODEL"),
    }
    for field, envs in env_map.items():
        for env in envs:
            val = os.environ.get(env)
            if val:
                setattr(cfg, field, val)
                break

    return cfg


def save_config(cfg: LLMConfig) -> None:
    CONFIG_FILE.write_text(
        json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8"
    )
