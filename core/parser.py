"""
报错解析引擎：把 Python / C 的原始英文报错，拆成结构化的"病历"。

新手看到的是一大坨英文；我们把它拆成：
  - 错误类型（是谁报的错）
  - 出错位置（第几行、第几列）
  - 原始消息（英文原文，保留）
  - 涉及的对象（变量名 / 函数名 / 文件名）

这样界面才能做到"指到具体某个字"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict


@dataclass
class Issue:
    """一条被解析出来的错误/警告。"""
    language: str = ""
    severity: str = "error"          # error / warning / note
    kind: str = ""                   # 错误类型，如 NameError / SyntaxError / compile
    message: str = ""                # 原始英文消息
    line: int | None = None
    column: int | None = None
    source_line: str = ""            # 出错那一行的源码
    caret: str = ""                  # C 语言的 ^ 指示符
    file: str = ""
    stage: str = "runtime"           # compile / runtime
    frames: list[dict] = field(default_factory=list)   # Python 调用栈
    raw: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ParsedReport:
    language: str = ""
    issues: list[Issue] = field(default_factory=list)
    summary: str = ""
    has_output: bool = False

    def to_dict(self) -> dict:
        return {
            "language": self.language,
            "issues": [i.to_dict() for i in self.issues],
            "summary": self.summary,
            "has_output": self.has_output,
        }


# --------------------------------------------------------------------------- #
# Python
# --------------------------------------------------------------------------- #
PY_FRAME_RE = re.compile(
    r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+)(?:, in (?P<func>.+))?$'
)
PY_FINAL_RE = re.compile(
    r"^(?P<kind>[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Interrupt|Exit|Warning|Iteration|"
    r"KeyboardInterrupt|SystemExit))(?::\s*(?P<msg>.*))?$"
)
# 兜底：形如 "NameError: xxx" 但类型不在惯例列表里
PY_ANY_FINAL_RE = re.compile(r"^(?P<kind>[A-Z]\w*(?:Error|Warning|Exception))(?::\s*(?P<msg>.*))?$")
PY_CARET_LINE_RE = re.compile(r"^\s*(\^+)\s*$")


def parse_python(stderr: str, stdout: str = "") -> ParsedReport:
    report = ParsedReport(language="python", has_output=bool((stdout or "").strip()))
    if not stderr or not stderr.strip():
        return report

    lines = stderr.replace("\r\n", "\n").split("\n")
    frames: list[dict] = []
    issues: list[Issue] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = PY_FRAME_RE.match(line)
        if m:
            raw_file = m.group("file") or ""
            # 只显示文件名，不暴露沙箱绝对路径，讲解时更干净
            short_file = re.split(r"[\\/]", raw_file)[-1] or raw_file
            frame = {
                "file": short_file,
                "line": int(m.group("line")),
                "func": (m.group("func") or "").strip(),
                "code": "",
            }
            # 下一行通常就是源码
            if i + 1 < len(lines) and lines[i + 1].startswith("    ") and not PY_FRAME_RE.match(lines[i + 1]):
                frame["code"] = lines[i + 1].strip()
                i += 1
            frames.append(frame)
            i += 1
            continue

        m = PY_FINAL_RE.match(line.strip()) or PY_ANY_FINAL_RE.match(line.strip())
        if m:
            kind = m.group("kind")
            msg = (m.group("msg") or "").strip()
            last = frames[-1] if frames else {}
            issue = Issue(
                language="python",
                severity="error",
                kind=kind,
                message=msg,
                line=last.get("line"),
                source_line=last.get("code", ""),
                file=last.get("file", ""),
                stage="runtime",
                frames=list(frames),
                raw=line.strip(),
            )
            issues.append(issue)
            frames = []   # 多段 traceback（异常链）时重置
            i += 1
            continue

        # Python 3.11+ 的 "^^^^" 精确指示行，例如 SyntaxError
        m = PY_CARET_LINE_RE.match(line)
        if m and issues:
            issues[-1].caret = line
        i += 1

    # SyntaxError 特殊处理：报错里自带 File/line 与源码，但格式与运行期不同
    if "SyntaxError" in stderr or "IndentationError" in stderr or "TabError" in stderr:
        for issue in issues:
            if issue.kind in ("SyntaxError", "IndentationError", "TabError"):
                issue.stage = "syntax"
                src_m = re.search(r"^\s{4}\S.*$", stderr, re.M)
                if src_m and not issue.source_line:
                    issue.source_line = src_m.group(0).strip()
                caret_m = re.search(r"^\s*(\^+)\s*$", stderr, re.M)
                if caret_m:
                    issue.caret = caret_m.group(0)

    report.issues = issues
    if issues:
        first = issues[0]
        report.summary = f"{first.kind}: {first.message}" if first.message else first.kind
    return report


# --------------------------------------------------------------------------- #
# C / C++
# --------------------------------------------------------------------------- #
GCC_DIAG_RE = re.compile(
    r"^(?P<file>[^:\n]+):(?P<line>\d+):(?:(?P<col>\d+):)?\s*"
    r"(?P<sev>fatal error|error|warning|note|required from here):\s*(?P<msg>.*)$"
)
GCC_FUNC_CTX_RE = re.compile(r"^(?P<file>[^:\n]+):\s*In function\s+['`](?P<func>[^']+)':")


def parse_c(stderr: str, stdout: str = "", stage: str = "compile") -> ParsedReport:
    report = ParsedReport(language="c", has_output=bool((stdout or "").strip()))
    text = (stderr or "") + "\n" + (stdout or "")
    if not text.strip():
        return report

    lines = text.replace("\r\n", "\n").split("\n")
    issues: list[Issue] = []
    current_func = ""

    for idx, line in enumerate(lines):
        m = GCC_FUNC_CTX_RE.match(line)
        if m:
            current_func = m.group("func")
            continue

        m = GCC_DIAG_RE.match(line)
        if not m:
            continue

        sev_raw = m.group("sev")
        severity = "warning" if sev_raw == "warning" else ("note" if sev_raw in ("note", "required from here") else "error")

        source_line = ""
        caret = ""
        # 后面 3 行内找 "  5 | code" 和 "    | ^~~"
        for j in range(idx + 1, min(idx + 4, len(lines))):
            probe = lines[j]
            if not source_line:
                sm = re.match(r"^\s*\d+\s*\|\s?(.*)$", probe)
                if sm:
                    source_line = sm.group(1).rstrip()
                    continue
            if not caret:
                cm = re.search(r"(\^[~^]*)\s*$", probe)
                if cm:
                    caret = probe

        msg = m.group("msg").strip()
        issues.append(Issue(
            language="c",
            severity=severity,
            kind=("compile_" + severity),
            message=msg,
            line=int(m.group("line")),
            column=int(m.group("col")) if m.group("col") else None,
            source_line=source_line,
            caret=caret,
            file=m.group("file"),
            stage=stage,
            frames=[{"func": current_func}] if current_func else [],
            raw=line.strip(),
        ))

    report.issues = issues
    errs = [x for x in issues if x.severity == "error"]
    if errs:
        report.summary = errs[0].message
    elif issues:
        report.summary = issues[0].message
    return report


# --------------------------------------------------------------------------- #
# 统一入口
# --------------------------------------------------------------------------- #
def parse(language: str, stderr: str, stdout: str = "", stage: str = "") -> ParsedReport:
    lang = (language or "").lower()
    if lang in ("c", "cpp"):
        return parse_c(stderr, stdout, stage="compile" if stage == "compile_error" else "runtime")
    return parse_python(stderr, stdout)


def format_for_prompt(report: ParsedReport) -> str:
    """把解析结果整理成给大模型看的紧凑文本。"""
    if not report.issues:
        return "（未解析出结构化错误）"
    out = []
    for n, it in enumerate(report.issues, 1):
        head = f"[{n}] {it.severity} {it.kind}"
        if it.line:
            head += f" | 第 {it.line} 行"
            if it.column:
                head += f" 第 {it.column} 列"
        out.append(head)
        if it.message:
            out.append(f"    消息: {it.message}")
        if it.source_line:
            out.append(f"    源码: {it.source_line}")
        if it.caret:
            out.append(f"    指示: {it.caret.strip()}")
        if it.frames:
            ctx = [f"{f.get('func','')}" for f in it.frames if f.get("func")]
            if ctx:
                out.append(f"    上下文: {' -> '.join(ctx)}")
    return "\n".join(out)
