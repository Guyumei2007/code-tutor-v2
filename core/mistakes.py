"""
错题集：把"这次问过的错误"存下来，方便以后复习。

设计考虑（针对新手学习场景）：
1. 存的是"错误现场快照"——当时的代码、真实报错、病因、正确写法，四者齐全，
   复习时不需要再回想上下文。
2. 相同代码 + 相同报错自动去重，避免反复点收藏把列表刷爆。
3. 支持"已掌握"标记和复习次数，形成一个最简单的间隔复习闭环。
4. 纯 JSON 存储，随时可以用记事本打开，也能导出成 Markdown 打印。

存储位置：data/mistakes.json
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DATA_DIR

MAX_ITEMS = 500               # 防止文件无限膨胀
MAX_EXPLAIN = 30000           # 单条讲解最多存这么多字

# C/C++ 的报错类型是编译器的内部叫法，展示时换成中文
KIND_ZH = {
    "compile_error": "编译错误",
    "compile_warning": "编译警告",
    "compile_note": "编译提示",
    "compile_fatal error": "致命错误",
}


def kind_label(kind: str) -> str:
    return KIND_ZH.get(kind or "", kind or "错误")


class MistakeBook:
    def __init__(self, path: Path | None = None):
        self.path = path or (DATA_DIR / "mistakes.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # 读写
    # ------------------------------------------------------------------ #
    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw.get("items", [])
            if isinstance(raw, list):
                return raw
        except (json.JSONDecodeError, OSError):
            # 文件损坏时不覆盖，改名备份，避免用户数据凭空消失
            try:
                self.path.rename(self.path.with_suffix(".corrupt.json"))
            except OSError:
                pass
        return []

    def _save(self, items: list[dict[str, Any]]) -> None:
        payload = {
            "version": 1,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "count": len(items),
            "items": items,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)   # 原子替换，避免写一半断电导致损坏

    # ------------------------------------------------------------------ #
    # 去重指纹
    # ------------------------------------------------------------------ #
    @staticmethod
    def _fingerprint(code: str, stderr: str) -> str:
        norm = " ".join((code or "").split()) + "||" + " ".join((stderr or "").split())
        return hashlib.sha1(norm.encode("utf-8", "replace")).hexdigest()[:16]

    # ------------------------------------------------------------------ #
    # 新增
    # ------------------------------------------------------------------ #
    def add(self, diagnosis: dict[str, Any], note: str = "", tags: list[str] | None = None) -> dict[str, Any]:
        """从一次诊断结果中提炼错题。diag 就是 /api/diagnose 的返回值。"""
        first_run = diagnosis.get("first_run") or {}
        parsed = diagnosis.get("parsed") or {}
        issues = parsed.get("issues") or []

        code = diagnosis.get("code") or ""
        stderr = first_run.get("stderr") or ""
        fp = self._fingerprint(code, stderr)

        first_err = next((x for x in issues if x.get("severity") == "error"), None)
        if first_err is None and issues:
            first_err = issues[0]

        kb = diagnosis.get("knowledge_hit") or {}

        # 正确写法：优先用 AI 为这道题生成的修复代码；
        # 没有 AI 时退而用知识库里的"同类错误通用示例"，保证错题集有参考价值。
        fixed_code = diagnosis.get("fixed_code") or ""
        fixed_from = "ai" if fixed_code else ""
        if not fixed_code:
            example = kb.get("example_good") or ""
            if example:
                fixed_code = example
                fixed_from = "knowledge"

        item = {
            "id": uuid.uuid4().hex[:12],
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "fingerprint": fp,
            "language": diagnosis.get("language") or "",
            "language_label": diagnosis.get("language_label") or "",
            "code": code,
            "stdin": diagnosis.get("stdin") or "",
            "stage": first_run.get("stage") or "",
            "exit_code": first_run.get("exit_code"),
            "stdout": (first_run.get("stdout") or "")[:4000],
            "stderr": stderr[:8000],
            "error_kind": (first_err or {}).get("kind") or "",
            "error_message": (first_err or {}).get("message") or "",
            "error_line": (first_err or {}).get("line"),
            "error_column": (first_err or {}).get("column"),
            "knowledge_title": kb.get("title") or "",
            "knowledge_meaning": kb.get("meaning") or "",
            "knowledge_fixes": kb.get("fixes") or [],
            "knowledge_causes": kb.get("causes") or [],
            "explanation": (diagnosis.get("explanation") or "")[:MAX_EXPLAIN],
            "fixed_code": fixed_code,
            "fixed_from": fixed_from,
            "fixed_ok": bool(diagnosis.get("fixed_ok")),
            "verdict": diagnosis.get("verdict") or "",
            "note": note or "",
            "tags": tags or [],
            "mastered": False,
            "review_count": 0,
            "last_reviewed_at": "",
        }

        with self._lock:
            items = self._load()

            # 同一道题不重复收藏，但把备注合并进去
            for old in items:
                if old.get("fingerprint") == fp:
                    if note:
                        old["note"] = (old.get("note", "") + "\n" + note).strip()
                    if tags:
                        merged = list(dict.fromkeys((old.get("tags") or []) + tags))
                        old["tags"] = merged
                    old["duplicated"] = True
                    self._save(items)
                    return old

            items.insert(0, item)
            if len(items) > MAX_ITEMS:
                items = items[:MAX_ITEMS]
            self._save(items)
        return item

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def list(self, include_body: bool = False, only_unmastered: bool = False) -> dict[str, Any]:
        with self._lock:
            items = self._load()

        if only_unmastered:
            items = [x for x in items if not x.get("mastered")]

        total = len(items)
        mastered = sum(1 for x in items if x.get("mastered"))

        if not include_body:
            items = [self._brief(x) for x in items]

        return {"items": items, "total": total, "mastered": mastered}

    @staticmethod
    def _brief(item: dict[str, Any]) -> dict[str, Any]:
        """列表用的精简版，去掉大字段，减少传输量。"""
        return {
            k: v for k, v in item.items()
            if k not in ("explanation", "stdout", "fixed_code", "knowledge_meaning")
        }

    def get(self, item_id: str) -> dict[str, Any] | None:
        with self._lock:
            for x in self._load():
                if x.get("id") == item_id:
                    return x
        return None

    # ------------------------------------------------------------------ #
    # 更新 / 删除
    # ------------------------------------------------------------------ #
    def update(self, item_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {"note", "tags", "mastered"}
        with self._lock:
            items = self._load()
            for x in items:
                if x.get("id") != item_id:
                    continue
                for k, v in patch.items():
                    if k in allowed:
                        x[k] = v
                if patch.get("mark_reviewed"):
                    x["review_count"] = int(x.get("review_count") or 0) + 1
                    x["last_reviewed_at"] = datetime.now().isoformat(timespec="seconds")
                self._save(items)
                return x
        return None

    def delete(self, item_id: str) -> bool:
        with self._lock:
            items = self._load()
            new_items = [x for x in items if x.get("id") != item_id]
            if len(new_items) == len(items):
                return False
            self._save(new_items)
            return True

    def clear(self, only_mastered: bool = True) -> int:
        with self._lock:
            items = self._load()
            if only_mastered:
                kept = [x for x in items if not x.get("mastered")]
            else:
                kept = []
            removed = len(items) - len(kept)
            self._save(kept)
            return removed

    # ------------------------------------------------------------------ #
    # 导出
    # ------------------------------------------------------------------ #
    def export_markdown(self, only_unmastered: bool = False) -> str:
        data = self.list(include_body=True, only_unmastered=only_unmastered)
        items = data["items"]
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        lines: list[str] = [
            "# 我的编程错题本",
            "",
            f"> 导出时间：{now}　共 {data['total']} 道，已掌握 {data['mastered']} 道",
            "",
            "复习方法：先看「当时的代码」和「报错」，自己回想病因，再看答案。",
            "",
            "---",
            "",
        ]

        for idx, it in enumerate(items, 1):
            fence = "c" if it.get("language") in ("c", "cpp") else "python"
            status = "✅ 已掌握" if it.get("mastered") else "📌 待复习"
            lines.append(f"## 第 {idx} 题 · {kind_label(it.get('error_kind'))}　{status}")
            lines.append("")
            meta = [
                f"语言：{it.get('language_label') or it.get('language')}",
                f"错误行：第 {it.get('error_line')} 行" if it.get("error_line") else "错误行：—",
                f"收藏于：{it.get('created_at', '')[:19].replace('T', ' ')}",
                f"复习次数：{it.get('review_count', 0)}",
            ]
            lines.append("　".join(meta))
            lines.append("")

            lines.append("### 当时的代码")
            lines.append(f"```{fence}")
            lines.append(it.get("code") or "")
            lines.append("```")
            lines.append("")

            if it.get("stderr"):
                lines.append("### 真实报错")
                lines.append("```")
                lines.append(it["stderr"].strip())
                lines.append("```")
                lines.append("")

            if it.get("knowledge_title"):
                lines.append(f"### 病因：{it['knowledge_title']}")
                if it.get("knowledge_meaning"):
                    lines.append(it["knowledge_meaning"])
                lines.append("")
                if it.get("knowledge_causes"):
                    lines.append("**常见原因**")
                    lines.extend(f"- {c}" for c in it["knowledge_causes"])
                    lines.append("")
                if it.get("knowledge_fixes"):
                    lines.append("**怎么修**")
                    lines.extend(f"{i}. {f}" for i, f in enumerate(it["knowledge_fixes"], 1))
                    lines.append("")

            if it.get("fixed_code"):
                if it.get("fixed_ok"):
                    head = "### 正确写法（本题修复，已跑通验证）"
                elif it.get("fixed_from") == "knowledge":
                    head = "### 参考写法（同类错误的通用示例，不是本题原文改写）"
                else:
                    head = "### 正确写法"
                lines.append(head)
                lines.append(f"```{fence}")
                lines.append(it["fixed_code"])
                lines.append("```")
                lines.append("")

            if it.get("note"):
                lines.append("### 我的笔记")
                lines.append(it["note"])
                lines.append("")

            if it.get("tags"):
                lines.append("标签：" + " ".join(f"`{t}`" for t in it["tags"]))
                lines.append("")

            lines.append("---")
            lines.append("")

        if not items:
            lines.append("（错题集还是空的）")

        return "\n".join(lines)


# 全局单例
book = MistakeBook()
