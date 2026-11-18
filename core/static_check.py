"""
静态预检：在真正运行前，先扫一遍高置信度的低级错误。

原则：宁可少报，不可误报。误报会让新手更迷糊。
每条结果都带行号和修复建议。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field


@dataclass
class StaticIssue:
    line: int
    column: int
    code: str = ""          # 错误代码片段
    severity: str = "error" # error / warning
    title: str = ""
    detail: str = ""
    fix: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# 中文全角标点 -> 对应英文
FULLWIDTH_MAP = {
    "（": "(", "）": ")", "，": ",", "。": ".", "；": ";", "：": ":",
    "“": '"', "”": '"', "‘": "'", "’": "'", "！": "!", "？": "?",
    "【": "[", "】": "]", "《": "<", "》": ">", "＝": "=", "＋": "+",
    "－": "-", "＊": "*", "／": "/", "＜": "<", "＞": ">", "％": "%",
    "　": " ",
}
FULLWIDTH_RE = re.compile("[" + "".join(map(re.escape, FULLWIDTH_MAP.keys())) + "]")


def _strip_strings_and_comments_py(line: str) -> str:
    """把字符串内容和注释替换成空格，避免对中文标点误报。"""
    out = []
    i = 0
    n = len(line)
    in_s = None
    while i < n:
        ch = line[i]
        if in_s:
            if ch == "\\":
                out.append("  ")
                i += 2
                continue
            if ch == in_s:
                in_s = None
            out.append(" ")
            i += 1
            continue
        if ch in ("'", '"'):
            in_s = ch
            out.append(" ")
            i += 1
            continue
        if ch == "#":
            out.append(" " * (n - i))
            break
        out.append(ch)
        i += 1
    return "".join(out)


def _strip_strings_and_comments_c(line: str) -> str:
    out = []
    i = 0
    n = len(line)
    in_s = None
    while i < n:
        ch = line[i]
        if in_s:
            if ch == "\\":
                out.append("  ")
                i += 2
                continue
            if ch == in_s:
                in_s = None
            out.append(" ")
            i += 1
            continue
        if ch in ("'", '"'):
            in_s = ch
            out.append(" ")
            i += 1
            continue
        if line.startswith("//", i) or line.startswith("/*", i):
            out.append(" " * (n - i))
            break
        out.append(ch)
        i += 1
    return "".join(out)


def _check_brackets(code_lines: list[str], strip_fn, lang: str) -> list[StaticIssue]:
    """括号配对检查。返回配对失败的位置。"""
    issues: list[StaticIssue] = []
    stack: list[tuple[str, int, int]] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    line_no = 0
    for idx, raw in enumerate(code_lines, 1):
        clean = strip_fn(raw)
        line_no = idx
        for col, ch in enumerate(clean, 1):
            if ch in "([{":
                stack.append((ch, idx, col))
            elif ch in ")]}":
                if not stack:
                    issues.append(StaticIssue(
                        line=idx, column=col, code=raw.strip(),
                        severity="error",
                        title="多了一个右括号",
                        detail=f"这里出现了 `{ch}`，但前面没有与之配对的 `{pairs[ch]}`。",
                        fix=f"删掉这个多余的 `{ch}`，或者检查前面是否漏写了对应的左括号。",
                    ))
                    continue
                top, l, c = stack.pop()
                if top != pairs[ch]:
                    want = {"(": ")", "[": "]", "{": "}"}[top]
                    issues.append(StaticIssue(
                        line=idx, column=col, code=raw.strip(),
                        severity="error",
                        title="括号类型不匹配",
                        detail=(
                            f"第 {idx} 行的 `{ch}` 想和第 {l} 行的 `{top}` 配对，"
                            f"但 `{top}` 只能和 `{want}` 配对。"
                        ),
                        fix=f"把第 {l} 行的 `{top}` 改成 `{pairs[ch]}`，或把这里的 `{ch}` 改成 `{want}` 的反向配对。",
                    ))
    for ch, l, c in stack:
        want = {"(": ")", "[": "]", "{": "}"}[ch]
        issues.append(StaticIssue(
            line=l, column=c, code=code_lines[l - 1].strip() if l - 1 < len(code_lines) else "",
            severity="error",
            title="左括号没有闭合",
            detail=f"第 {l} 行第 {c} 列写的 `{ch}`，一直到文件结束都没找到对应的 `{want}`。",
            fix=f"在第 {l} 行这一句的末尾补上 `{want}`，或检查是否有多余的删除。",
        ))
    return issues


def check_python(code: str) -> list[StaticIssue]:
    issues: list[StaticIssue] = []
    lines = code.split("\n")

    # 1. 中文全角标点（代码区，排除字符串/注释）
    for i, raw in enumerate(lines, 1):
        clean = _strip_strings_and_comments_py(raw)
        for m in FULLWIDTH_RE.finditer(clean):
            ch = m.group(0)
            if ch == "　":   # 全角空格单独提示
                issues.append(StaticIssue(
                    line=i, column=m.start() + 1, code=raw.strip(),
                    severity="warning",
                    title="这里有全角空格",
                    detail="全角空格和普通空格长得像，但 Python 不认，会导致缩进报错。",
                    fix="删掉这个全角空格，用普通空格重新缩进。",
                ))
                continue
            issues.append(StaticIssue(
                line=i, column=m.start() + 1, code=raw.strip(),
                severity="error",
                title=f"用了中文标点 `{ch}`",
                detail=f"这里的 `{ch}` 是中文全角符号，Python 只认英文半角符号 `{FULLWIDTH_MAP[ch]}`。",
                fix=f"把它改成英文的 `{FULLWIDTH_MAP[ch]}`（切换输入法到英文再输入）。",
            ))

    # 2. 括号配对
    issues.extend(_check_brackets(lines, _strip_strings_and_comments_py, "python"))

    # 3. if/while 条件里把 == 写成 =（高置信度：条件里只有一个 = 且不是 ==/>=/<=/!= 等）
    for i, raw in enumerate(lines, 1):
        clean = _strip_strings_and_comments_py(raw)
        m = re.match(r"^\s*(if|elif|while)\s+(.+?)\s*:\s*$", clean)
        if not m:
            continue
        cond = m.group(2)
        if re.search(r"(?<![=!<>+\-*/%])=(?!=)", cond):
            issues.append(StaticIssue(
                line=i, column=raw.index("=") + 1 if "=" in raw else 1, code=raw.strip(),
                severity="error",
                title="判断相等要用 `==`，你写成了 `=`",
                detail=f"`{m.group(1)}` 后面的条件里出现了单个 `=`。`=` 是「赋值」，`==` 才是「判断相等」。",
                fix="把条件里的 `=` 改成 `==`。",
            ))

    # 4. print / input 忘写括号（Python 3 中会报 SyntaxError）
    for i, raw in enumerate(lines, 1):
        clean = _strip_strings_and_comments_py(raw)
        m = re.match(r"^\s*(print|input|len|range|str|int|float)\s+[^(=+\-*/]", clean)
        if m:
            issues.append(StaticIssue(
                line=i, column=1, code=raw.strip(),
                severity="error",
                title=f"`{m.group(1)}` 后面漏了括号",
                detail=f"在 Python 里，`{m.group(1)}` 是函数，必须写成 `{m.group(1)}(...)` 的形式。",
                fix=f"给 `{m.group(1)}` 的参数加上英文圆括号，例如 `{m.group(1)}(内容)`。",
            ))

    # 5. Tab 与空格混用
    has_tab_line = [i for i, l in enumerate(lines, 1) if l.startswith("\t")]
    has_space_indent = any(re.match(r"^ +\S", l) for l in lines)
    if has_tab_line and has_space_indent:
        issues.append(StaticIssue(
            line=has_tab_line[0], column=1,
            code=lines[has_tab_line[0] - 1][:40],
            severity="error",
            title="Tab 和空格混着用来缩进",
            detail="同一个文件里有的行用 Tab 缩进，有的行用空格。Python 会因此报 TabError 或缩进错误。",
            fix="全选代码，统一替换成 4 个空格（在编辑器里把 Tab 设置为插入空格）。",
        ))

    return issues


def check_c(code: str) -> list[StaticIssue]:
    issues: list[StaticIssue] = []
    lines = code.split("\n")
    joined = "\n".join(_strip_strings_and_comments_c(l) for l in lines)

    # 1. 中文全角标点
    for i, raw in enumerate(lines, 1):
        clean = _strip_strings_and_comments_c(raw)
        for m in FULLWIDTH_RE.finditer(clean):
            ch = m.group(0)
            issues.append(StaticIssue(
                line=i, column=m.start() + 1, code=raw.strip(),
                severity="error",
                title=f"用了中文标点 `{ch}`",
                detail=f"`{ch}` 是中文全角符号，C 编译器会报 `stray '\\xxx' in program`。",
                fix=f"把它改成英文半角 `{FULLWIDTH_MAP[ch]}`。",
            ))

    # 2. 括号配对
    issues.extend(_check_brackets(lines, _strip_strings_and_comments_c, "c"))

    # 3. 明显漏分号：语句行结尾不是 ; { } : 或预处理指令
    skip_kw = (
        "if", "else", "for", "while", "do", "switch", "case", "default",
        "struct", "union", "enum", "typedef", "return",
    )
    for i, raw in enumerate(lines, 1):
        clean = _strip_strings_and_comments_c(raw).rstrip()
        s = clean.strip()
        if not s or s.startswith("#"):
            continue
        if s.endswith((";", "{", "}", ":", ",", "\\", "(", "&", "|")):
            continue
        # 纯注释 / 宏续行
        if s.startswith("//") or s.startswith("/*") or s.startswith("*"):
            continue
        # 函数定义行 / 控制语句行（本来就不带分号）
        if s.startswith(skip_kw) and s.endswith(")"):
            continue
        if re.match(r"^[A-Za-z_]\w*[\s\*]+[A-Za-z_]\w*\s*\([^;]*\)\s*$", s):
            continue   # 形如 int main()
        if s.endswith(")"):
            # 可能是函数调用漏分号，高置信度才报
            if re.match(r"^[A-Za-z_][\w\.\->\[\]]*\s*\(", s) and not s.startswith(("if", "while", "for", "switch")):
                issues.append(StaticIssue(
                    line=i, column=len(raw.rstrip()), code=s,
                    severity="error",
                    title="这一行结尾可能漏了分号 `;`",
                    detail="这是一条函数调用语句，但行尾没有 `;`。C 语言每条语句都必须以分号结束。",
                    fix="在这一行末尾补上英文分号 `;`。",
                ))
            continue
        if re.match(r"^(int|float|double|char|long|short|unsigned|signed|void|const|static|size_t)\b", s):
            issues.append(StaticIssue(
                line=i, column=len(raw.rstrip()), code=s,
                severity="error",
                title="变量声明结尾漏了分号 `;`",
                detail=f"`{s[:30]}` 看起来是一条声明语句，但结尾没有分号。",
                fix="在这一行末尾补上英文分号 `;`。",
            ))
        elif re.match(r"^[A-Za-z_]\w*\s*=[^=]", s):
            issues.append(StaticIssue(
                line=i, column=len(raw.rstrip()), code=s,
                severity="error",
                title="赋值语句结尾漏了分号 `;`",
                detail="这是一条赋值语句，但结尾没有分号。",
                fix="在这一行末尾补上英文分号 `;`。",
            ))

    # 4. 用了库函数却没包含头文件
    headers = {
        "stdio.h": [r"\bprintf\s*\(", r"\bscanf\s*\(", r"\bputs\s*\(", r"\bgets\s*\(", r"\bfopen\s*\(", r"\bgetchar\s*\("],
        "string.h": [r"\bstrlen\s*\(", r"\bstrcpy\s*\(", r"\bstrcmp\s*\(", r"\bstrcat\s*\(", r"\bmemset\s*\(", r"\bstrstr\s*\("],
        "stdlib.h": [r"\bmalloc\s*\(", r"\bfree\s*\(", r"\bcalloc\s*\(", r"\bexit\s*\(", r"\batoi\s*\(", r"\brand\s*\(", r"\bqsort\s*\("],
        "math.h":   [r"\bsqrt\s*\(", r"\bpow\s*\(", r"\bsin\s*\(", r"\bcos\s*\(", r"\bfabs\s*\(", r"\bceil\s*\("],
    }
    for header, pats in headers.items():
        if re.search(r"#\s*include\s*<\s*" + re.escape(header) + r"\s*>", joined):
            continue
        for i, raw in enumerate(lines, 1):
            clean = _strip_strings_and_comments_c(raw)
            for p in pats:
                m = re.search(p, clean)
                if m:
                    fn = p.replace(r"\b", "").replace(r"\s*\(", "")
                    issues.append(StaticIssue(
                        line=i, column=m.start() + 1, code=raw.strip(),
                        severity="error",
                        title=f"用了 `{fn}` 但没包含 `<{header}>`",
                        detail=f"`{fn}` 是在 `{header}` 里声明的，没 `#include` 就使用，编译器会报 implicit declaration。",
                        fix=f"在文件最上方加一行：`#include <{header}>`",
                    ))
                    break
            else:
                continue
            break

    # 5. scanf 忘 &
    for i, raw in enumerate(lines, 1):
        clean = _strip_strings_and_comments_c(raw)
        m = re.search(r"scanf\s*\(\s*\"([^\"]*)\"\s*,(.*)\)", clean)
        if not m:
            continue
        fmt, args = m.group(1), m.group(2)
        specs = re.findall(r"%[\d\.]*([a-zA-Z])", fmt)
        arg_list = [a.strip() for a in args.split(",") if a.strip()]
        for spec, arg in zip(specs, arg_list):
            if spec not in ("d", "f", "lf", "c", "u", "ld", "i", "lu", "x"):
                continue   # %s 用数组名，不需要 &
            if not arg.startswith("&"):
                issues.append(StaticIssue(
                    line=i, column=clean.index(arg) + 1 if arg in clean else 1, code=raw.strip(),
                    severity="error",
                    title=f"`scanf` 的 `{arg}` 前面漏了 `&`",
                    detail=(
                        "scanf 需要知道「把读到的值存到哪个地址」，所以要传地址（加 `&`）。"
                        "漏掉 `&` 是 C 语言最经典的错误，往往导致程序直接崩溃（段错误）。"
                    ),
                    fix=f"改成 `scanf(..., &{arg})`。注意：如果是字符数组（字符串），数组名本身已是地址，不用加 `&`。",
                ))
    return issues


def check(code: str, language: str) -> list[StaticIssue]:
    lang = (language or "").lower()
    if lang in ("c", "cpp", "c++"):
        return check_c(code)
    return check_python(code)
