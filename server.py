"""
编程报错检查助手 —— 本地 Web 服务（零第三方依赖，Python 标准库实现）。

启动：python server.py
然后浏览器访问 http://127.0.0.1:7860
"""

from __future__ import annotations

import json
import mimetypes
import os
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from core import knowledge, glossary, mistakes, judge          # noqa: E402
from core.analyzer import Diagnoser                            # noqa: E402
from core.config import load_config, save_config               # noqa: E402
from core.markdown_lite import render                          # noqa: E402
from core.runner import detect_toolchain, detect_language      # noqa: E402
from core.static_check import check as static_check            # noqa: E402

book = mistakes.book

APP_VERSION = "2026.11.17-judge.7"
JUDGE_ROUTES = ("/api/evaluate", "/api/judge", "/api/check_logic")

# 默认只听本机；设置 HOST=0.0.0.0 可让局域网/隧道访问
HOST = os.environ.get("HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = int(os.environ.get("PORT", "7860"))

TEMPLATES = BASE_DIR / "templates"
STATIC = BASE_DIR / "static"


# --------------------------------------------------------------------------- #
# 示例代码（同时也是比赛演示素材）
# --------------------------------------------------------------------------- #
SAMPLES = [
    {
        "id": "py_avg",
        "title": "Python · 求平均数（普通输入正常，边界会崩）",
        "language": "python",
        "code": (
            "n = int(input('有几个数？'))\n"
            "total = 0.0\n"
            "for i in range(n):\n"
            "    total += float(input(f'第 {i+1} 个数：'))\n"
            "print(f'平均数是 {total / n}')\n"
        ),
        "stdin": "3\n1\n2\n3\n",
        "note": "普通输入算得对，但输入 0 个数据时会除以 0 崩溃 —— 检查后看「边界测试」怎么抓它。",
    },
    {
        "id": "c_avg",
        "title": "C 语言 · 求平均数（边界会崩）",
        "language": "c",
        "code": (
            "#include <stdio.h>\n\nint main() {\n"
            "    int n, x;\n"
            "    int total = 0;\n"
            "    scanf(\"%d\", &n);\n"
            "    for (int i = 0; i < n; i++) {\n"
            "        scanf(\"%d\", &x);\n"
            "        total += x;\n"
            "    }\n"
            "    printf(\"%d\\n\", total / n);\n"
            "    return 0;\n}\n"
        ),
        "stdin": "3\n1\n2\n3\n",
        "note": "C 语言里整数除以 0 是未定义行为，n=0 时程序会卡住或崩溃。",
    },
    {
        "id": "py_name",
        "title": "Python · 变量名拼错 / 先用后定义",
        "language": "python",
        "code": "total = 0\nfor i in range(1, 6):\n    totl = total + i\n\nprint(total)",
        "stdin": "",
        "note": "循环里把 total 拼成了 totl，结果累加全丢了，最后打印恒为 0。",
    },
    {
        "id": "py_indent",
        "title": "Python · 缩进错误",
        "language": "python",
        "code": "score = 85\nif score >= 60:\nprint(\"及格了\")\nelse:\n    print(\"不及格\")",
        "stdin": "",
        "note": "if 下面那一行没有缩进。",
    },
    {
        "id": "py_type",
        "title": "Python · 字符串和数字相加",
        "language": "python",
        "code": "name = input(\"请输入姓名：\")\nage = input(\"请输入年龄：\")\nprint(\"你好，\" + name + \"，你今年 \" + age + \" 岁\")\nprint(\"明年你就 \" + age + 1 + \" 岁了\")",
        "stdin": "小明\n18\n",
        "note": "input() 拿到的是字符串，直接 +1 会报错。",
    },
    {
        "id": "py_index",
        "title": "Python · 列表越界",
        "language": "python",
        "code": "nums = [3, 1, 4, 1, 5]\ntotal = 0\nfor i in range(1, len(nums) + 1):\n    total += nums[i]\nprint(\"总和：\", total)",
        "stdin": "",
        "note": "下标从 0 开始，range 写到 len+1 就越界了。",
    },
    {
        "id": "py_cn",
        "title": "Python · 用了中文标点",
        "language": "python",
        "code": "for i in range(3)：\n    print（\"第\", i, \"次\"）",
        "stdin": "",
        "note": "冒号和括号都是中文全角的。",
    },
    {
        "id": "c_semicolon",
        "title": "C 语言 · 漏分号",
        "language": "c",
        "code": "#include <stdio.h>\n\nint main() {\n    int a = 10\n    int b = 20;\n    printf(\"%d\\n\", a + b);\n    return 0;\n}",
        "stdin": "",
        "note": "第 4 行结尾漏了分号。",
    },
    {
        "id": "c_header",
        "title": "C 语言 · 缺头文件",
        "language": "c",
        "code": "int main() {\n    printf(\"Hello World!\\n\");\n    return 0;\n}",
        "stdin": "",
        "note": "用了 printf 却没 #include <stdio.h>。",
    },
    {
        "id": "c_segv",
        "title": "C 语言 · 数组越界导致段错误",
        "language": "c",
        "code": "#include <stdio.h>\n\nint main() {\n    int a[3] = {1, 2, 3};\n    for (int i = 0; i <= 3; i++) {\n        a[i] = i * 2;\n    }\n    for (int i = 0; i < 3; i++) {\n        printf(\"%d \", a[i]);\n    }\n    return 0;\n}",
        "stdin": "",
        "note": "i <= 3 会写到 a[3]，越界了。",
    },
    {
        "id": "c_scanf",
        "title": "C 语言 · scanf 忘了 &",
        "language": "c",
        "code": "#include <stdio.h>\n\nint main() {\n    int age;\n    printf(\"请输入年龄：\");\n    scanf(\"%d\", age);\n    printf(\"明年你 %d 岁\\n\", age + 1);\n    return 0;\n}",
        "stdin": "18\n",
        "note": "scanf 的变量前必须加 &。",
    },
    {
        "id": "c_format",
        "title": "C 语言 · 格式符与类型不匹配",
        "language": "c",
        "code": "#include <stdio.h>\n\nint main() {\n    double pi = 3.14159;\n    printf(\"pi = %d\\n\", pi);\n    return 0;\n}",
        "stdin": "",
        "note": "double 用 %d 打印会得到垃圾值。",
    },
]


# --------------------------------------------------------------------------- #
# 业务
# --------------------------------------------------------------------------- #
_diagnoser_cache: dict = {}
_lock = threading.Lock()


def get_diagnoser(timeout: int = 10, max_rounds: int = 2) -> Diagnoser:
    key = (timeout, max_rounds)
    with _lock:
        d = _diagnoser_cache.get(key)
        if d is None:
            d = Diagnoser(cfg=load_config(), timeout=timeout, max_fix_rounds=max_rounds)
            _diagnoser_cache[key] = d
        return d


def reset_diagnoser() -> None:
    with _lock:
        _diagnoser_cache.clear()


class Handler(BaseHTTPRequestHandler):
    server_version = "CodeFixBuddy/1.0"

    # ---------------- 基础工具 ---------------- #
    def log_message(self, fmt, *args):
        # 只打印错误，避免刷屏
        if len(args) > 1 and str(args[1]).startswith(("4", "5")):
            sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _send(self, status: int, body: bytes, ctype: str = "text/plain; charset=utf-8",
              extra_headers: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, status: int = 200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(status, data, "application/json; charset=utf-8")

    def _file(self, path: Path):
        if not path.exists() or not path.is_file():
            self._json({"ok": False, "error": "文件不存在"}, 404)
            return
        ctype, _ = mimetypes.guess_type(str(path))
        if path.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"
        elif path.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif path.suffix == ".html":
            ctype = "text/html; charset=utf-8"
        self._send(200, path.read_bytes(), ctype or "application/octet-stream")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    # ---------------- 路由 ---------------- #
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self._file(TEMPLATES / "index.html")
            return
        if path.startswith("/static/"):
            rel = path[len("/static/"):].lstrip("/")
            target = (STATIC / rel).resolve()
            if STATIC.resolve() in target.parents or target.parent == STATIC.resolve():
                self._file(target)
            else:
                self._json({"ok": False, "error": "非法路径"}, 403)
            return

        if path == "/api/env":
            tc = detect_toolchain()
            self._json({
                "ok": True,
                "python": sys.version.split()[0],
                "python_exe": sys.executable,
                "c_compiler": tc.cc or "",
                "cxx_compiler": tc.cxx or "",
                "compiler_origin": tc.origin,
                "compiler_notes": tc.notes,
                "has_c": tc.has_c,
            })
            return

        if path == "/api/health":
            cfg = load_config()
            self._json({
                "ok": True,
                "app": "code-tutor",
                "version": APP_VERSION,
                "judge_routes": list(JUDGE_ROUTES),
                "has_key": bool(cfg.api_key),
            })
            return

        if path == "/api/settings":
            cfg = load_config()
            self._json({
                "ok": True,
                "base_url": cfg.base_url,
                "model": cfg.model,
                "has_key": bool(cfg.api_key),
                "key_masked": (cfg.api_key[:4] + "****" + cfg.api_key[-4:]) if len(cfg.api_key) > 8 else "",
            })
            return

        if path == "/api/samples":
            self._json({"ok": True, "samples": SAMPLES})
            return

        if path == "/api/knowledge":
            lang = (qs.get("lang") or [""])[0]
            entries = knowledge.all_entries(lang)
            self._json({
                "ok": True,
                "count": len(entries),
                "entries": [
                    {"id": e.id, "lang": e.lang, "title": e.title,
                     "meaning": e.meaning, "causes": e.causes,
                     "fixes": e.fixes, "tip": e.tip}
                    for e in entries
                ],
            })
            return

        if path == "/api/glossary":
            self._json({"ok": True, "terms": glossary.all_terms()})
            return

        # ---------------- 错题集 ---------------- #
        if path == "/api/mistakes":
            only = (qs.get("only_unmastered") or ["0"])[0] in ("1", "true")
            data = book.list(include_body=False, only_unmastered=only)
            self._json({"ok": True, **data})
            return

        if path == "/api/mistakes/export":
            only = (qs.get("only_unmastered") or ["0"])[0] in ("1", "true")
            md = book.export_markdown(only_unmastered=only)
            body = md.encode("utf-8")
            self._send(200, body, "text/markdown; charset=utf-8", {
                "Content-Disposition": 'attachment; filename="mistakes.md"',
            })
            return

        if path.startswith("/api/mistakes/"):
            item_id = path[len("/api/mistakes/"):].strip("/")
            item = book.get(item_id)
            if not item:
                self._json({"ok": False, "error": "错题不存在"}, 404)
                return
            detail = dict(item)
            detail["explanation_html"] = render(item.get("explanation") or "")
            self._json({"ok": True, "item": detail})
            return

        self._json({"ok": False, "error": "接口不存在"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._body()

        try:
            if path == "/api/diagnose":
                self._handle_diagnose(body)
                return
            if path == "/api/settings":
                self._handle_settings(body)
                return
            if path == "/api/translate":
                self._handle_translate(body)
                return
            if path == "/api/precheck":
                self._handle_precheck(body)
                return
            if path in JUDGE_ROUTES:
                self._handle_evaluate(body)
                return
            if path == "/api/mistakes":
                self._handle_mistake_add(body)
                return
            if path == "/api/mistakes/clear":
                removed = book.clear(only_mastered=bool(body.get("only_mastered", True)))
                self._json({"ok": True, "removed": removed})
                return
        except Exception:
            self._json({"ok": False, "error": "服务端异常：\n" + traceback.format_exc()[-1500:]}, 500)
            return

        self._json({"ok": False, "error": "接口不存在"}, 404)

    def do_PATCH(self):
        path = urlparse(self.path).path
        body = self._body()
        if path.startswith("/api/mistakes/"):
            item_id = path[len("/api/mistakes/"):].strip("/")
            item = book.update(item_id, body)
            if not item:
                self._json({"ok": False, "error": "错题不存在"}, 404)
                return
            self._json({"ok": True, "item": book._brief(item)})
            return
        self._json({"ok": False, "error": "接口不存在"}, 404)

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith("/api/mistakes/"):
            item_id = path[len("/api/mistakes/"):].strip("/")
            ok = book.delete(item_id)
            self._json({"ok": ok, "error": "" if ok else "错题不存在"})
            return
        self._json({"ok": False, "error": "接口不存在"}, 404)

    # ---------------- 具体接口 ---------------- #
    def _handle_diagnose(self, body: dict):
        code = (body.get("code") or "").strip("\n")
        if not code.strip():
            self._json({"ok": False, "error": "请先把代码粘贴进来。"}, 400)
            return

        language = body.get("language") or ""
        if language == "auto":
            language = ""
        stdin = body.get("stdin") or ""
        description = (body.get("description") or "").strip()[:300]
        # 描述里写了「输入 6」时自动当作运行输入，否则 input() 会直接 EOFError
        if not stdin and description:
            stdin = judge.infer_stdin(description)
        timeout = int(body.get("timeout") or 10)
        timeout = max(1, min(timeout, 60))
        max_rounds = int(body.get("max_rounds") or 2)
        max_rounds = max(0, min(max_rounds, 3))
        use_llm = bool(body.get("use_llm", True))
        auto_fix = bool(body.get("auto_fix", True))
        auto_test = bool(body.get("auto_test", True))
        # 前端会单独调用 /api/evaluate 做裁判，这里可跳过，避免调两次大模型
        skip_judge = bool(body.get("skip_judge", False))

        d = get_diagnoser(timeout, max_rounds).diagnose(
            code=code, language=language, stdin=stdin,
            use_llm=use_llm, auto_fix=auto_fix, auto_test=auto_test,
            description=description,
        )

        result = d.to_dict()
        result["ok"] = True
        result["explanation_html"] = render(d.explanation)
        result["detected_language"] = detect_language(code)

        # ---------------- 语义逻辑裁判（有描述且代码跑通时必须走一次） ---------------- #
        result["judge"] = {"judged": False}
        if description and (d.first_run or {}).get("ok") and not skip_judge:
            try:
                jr = judge.evaluate(
                    code, intent=description, language=language,
                    stdin=stdin, timeout=timeout,
                )
            except judge.JudgeUnavailable as exc:
                entry = {"judged": False, "error": str(exc)}
                if exc.result is not None:
                    entry.update({
                        "llm_error": exc.result.llm_error,
                        "judge_source": exc.result.judge_source,
                        "actual_output": exc.result.actual_output,
                        "is_syntax_correct": exc.result.is_syntax_correct,
                    })
                result["judge"] = entry
            else:
                result["judge"] = jr.to_dict()
                result["is_syntax_correct"] = jr.is_syntax_correct
                result["is_logic_correct"] = jr.is_logic_correct
                result["actual_output"] = jr.actual_output
                result["feedback"] = jr.feedback

                # 裁判只覆盖「逻辑对错」结论；边界测试 / 修复结果继续保留给前端
                result["intent"] = {}
                result["intent_check"] = {}
                result["intent_mismatch"] = False
                result["verdict"] = (
                    f"逻辑正确：{jr.feedback}" if jr.is_logic_correct
                    else f"逻辑错误：{jr.feedback}"
                )

        self._json(result)

    def _handle_settings(self, body: dict):
        cfg = load_config()
        if "base_url" in body and body["base_url"]:
            cfg.base_url = body["base_url"].strip()
        if "model" in body and body["model"]:
            cfg.model = body["model"].strip()
        if body.get("api_key"):
            cfg.api_key = body["api_key"].strip()
        if body.get("clear_key"):
            cfg.api_key = ""
        if body.get("system_prompt"):
            cfg.system_prompt = body["system_prompt"]
        save_config(cfg)
        reset_diagnoser()
        self._json({"ok": True, "has_key": bool(cfg.api_key), "model": cfg.model, "base_url": cfg.base_url})

    def _handle_translate(self, body: dict):
        """单独翻译一段报错文本（新手可以只贴报错，不贴代码）。"""
        text = body.get("text") or ""
        pairs = glossary.translate_line(text)
        self._json({"ok": True, "pairs": [{"en": a, "zh": b} for a, b in pairs]})

    def _handle_precheck(self, body: dict):
        """只做静态预检，不运行（适合快速看低级错误）。"""
        code = body.get("code") or ""
        language = body.get("language") or ""
        if not language or language == "auto":
            language = detect_language(code)
        issues = [x.to_dict() for x in static_check(code, language)]
        self._json({"ok": True, "language": language, "issues": issues})

    def _handle_evaluate(self, body: dict):
        """语义逻辑校验：沙盒执行 -> 分类 -> 大模型裁判。"""
        code = (body.get("code") or "").strip("\n")
        if not code.strip():
            self._json({"ok": False, "error": "请先提供代码。"}, 400)
            return

        intent_text = (body.get("intent") or body.get("description") or "").strip()[:500]
        language = body.get("language") or ""
        if language == "auto":
            language = ""
        stdin = body.get("stdin") or ""
        timeout = max(1, min(int(body.get("timeout") or 10), 60))
        use_llm = bool(body.get("use_llm", True))

        try:
            result = judge.evaluate(
                code,
                intent=intent_text,
                language=language,
                stdin=stdin,
                use_llm=use_llm,
                timeout=timeout,
            )
        except judge.JudgeUnavailable as exc:
            # 裁判没真正执行：如实报错，不伪造 is_logic_correct
            data = exc.result.to_dict() if exc.result is not None else {
                "is_syntax_correct": False,
                "is_logic_correct": False,
                "actual_output": "",
                "feedback": "",
            }
            data.update({"ok": False, "error": str(exc), "judged": False})
            self._json(data, 502)
            return

        data = result.to_dict()
        data["ok"] = True
        self._json(data)

    def _handle_mistake_add(self, body: dict):
        """把一次诊断结果收进错题集。"""
        diag = body.get("diagnosis") or {}
        if not diag.get("code"):
            self._json({"ok": False, "error": "没有可收藏的代码，请先检查一次代码。"}, 400)
            return

        # 前端会把 explanation_html 一起传回来，存库时不需要，剔掉
        diag.pop("explanation_html", None)

        note = (body.get("note") or "").strip()
        tags = body.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.replace("，", ",").split(",") if t.strip()]
        tags = [str(t)[:20] for t in tags[:8]]

        item = book.add(diag, note=note, tags=tags)
        duplicated = bool(item.get("duplicated"))
        item.pop("duplicated", None)

        data = book.list(include_body=False)
        self._json({
            "ok": True,
            "duplicated": duplicated,
            "item": book._brief(item),
            "total": data["total"],
            "mastered": data["mastered"],
        })


def main():
    print("=" * 62)
    print("  编程报错检查助手  CodeFix Buddy")
    print("=" * 62)

    tc = detect_toolchain()
    print(f"  Python : {sys.version.split()[0]}  ({sys.executable})")
    if tc.has_c:
        print(f"  C 编译器: {tc.cc}  (来自 {tc.origin or '未知'})")
    else:
        print("  C 编译器: 未检测到 —— C 语言代码暂时无法运行")
        for note in tc.notes:
            print(f"            {note}")

    cfg = load_config()
    if cfg.enabled:
        print(f"  大模型   : {cfg.model} @ {cfg.base_url}")
    else:
        print("  大模型   : 未配置（将使用内置知识库讲解，功能基本可用）")

    print("-" * 62)
    print(f"  访问地址 : http://{HOST}:{PORT}")
    print("  按 Ctrl+C 停止")
    print("=" * 62)

    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    if os.environ.get("NO_BROWSER") != "1":
        threading.Timer(1.0, lambda: webbrowser.open(f"http://{HOST}:{PORT}")).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
