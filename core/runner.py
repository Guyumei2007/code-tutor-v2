"""
代码执行引擎：把用户给的代码真正跑起来，拿到【真实】的报错。

设计要点（针对新手场景）：
1. 沙箱化：每次运行在独立临时目录，隔离用户环境。
2. 超时保护：防止死循环 / input() 卡死把程序挂住。
3. 分阶段记录：编译期错误 与 运行期错误 分开，因为新手最容易混淆这两件事。
4. 输入注入：新手代码常写 input() / scanf()，允许用户预先提供测试输入。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class RunResult:
    """一次运行的完整结果。"""
    language: str = ""
    ok: bool = False                # 是否成功运行完毕（无报错）
    stage: str = ""                 # ok / compile_error / runtime_error / success / timeout / env_error
    command: str = ""               # 实际执行的命令，展示给用户看
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration: float = 0.0
    compiler_hint: str = ""         # C 语言编译环境相关提示
    compile_ok: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# 语言识别
# --------------------------------------------------------------------------- #
C_EXT = {".c", ".h"}
CPP_EXT = {".cpp", ".cc", ".cxx", ".hpp"}
PY_EXT = {".py"}

LANG_LABEL = {
    "c": "C 语言",
    "cpp": "C++",
    "python": "Python",
}


def detect_language(code: str, filename: str = "") -> str:
    """根据文件名后缀优先判断，其次靠代码特征猜测。"""
    suffix = Path(filename).suffix.lower() if filename else ""

    if suffix in PY_EXT:
        return "python"
    if suffix in CPP_EXT:
        return "cpp"
    if suffix in C_EXT:
        return "c"

    # 无后缀时靠特征猜
    c_markers = [
        r"#include\s*<", r"\bint\s+main\s*\(", r"\bprintf\s*\(",
        r"\bscanf\s*\(", r"#define\s+\w+", r"\bvoid\s+\w+\s*\(",
    ]
    py_markers = [
        r"^\s*def\s+\w+\s*\(", r"^\s*import\s+\w+", r"^\s*from\s+\w+\s+import",
        r"\bprint\s*\(", r"^\s*class\s+\w+.*:", r"\bself\b",
    ]
    c_score = sum(len(re.findall(p, code, re.M)) for p in c_markers)
    py_score = sum(len(re.findall(p, code, re.M)) for p in py_markers)

    if c_score == 0 and py_score == 0:
        return "python"
    return "c" if c_score >= py_score else "python"


# --------------------------------------------------------------------------- #
# 编译器探测（C / C++）
# --------------------------------------------------------------------------- #
@dataclass
class Toolchain:
    cc: str | None = None
    cxx: str | None = None
    origin: str = ""            # 从哪里找到的
    notes: list[str] = field(default_factory=list)

    @property
    def has_c(self) -> bool:
        return bool(self.cc)


def _probe(exe: str) -> bool:
    try:
        p = subprocess.run(
            [exe, "--version"],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
        )
        return p.returncode == 0
    except Exception:
        return False


_toolchain_cache: Toolchain | None = None


def detect_toolchain(force: bool = False) -> Toolchain:
    """在 PATH 与常见安装位置里找一个能用的 C/C++ 编译器。"""
    global _toolchain_cache
    if _toolchain_cache is not None and not force:
        return _toolchain_cache

    tc = Toolchain()

    candidates: list[tuple[str, str]] = []
    # 1) PATH 里的通用名字
    for name in ("gcc", "clang", "cc"):
        exe = shutil.which(name)
        if exe:
            candidates.append((exe, "PATH"))
    for name in ("g++", "clang++", "c++"):
        exe = shutil.which(name)
        if exe:
            candidates.append((exe, "PATH"))

    # 2) Windows 常见安装目录
    win_dirs = [
        r"C:\msys64\mingw64\bin",
        r"C:\msys64\ucrt64\bin",
        r"C:\msys64\clang64\bin",
        r"C:\MinGW\bin",
        r"C:\mingw64\bin",
        r"C:\TDM-GCC-64\bin",
        r"C:\Program Files\LLVM\bin",
        r"C:\Program Files (x86)\Dev-Cpp\MinGW64\bin",
        r"C:\Program Files\CodeBlocks\MinGW\bin",
        r"C:\ProgramData\mingw64\mingw64\bin",
        r"C:\ProgramData\chocolatey\bin",
    ]
    for d in win_dirs:
        for exe_name in ("gcc.exe", "g++.exe", "clang.exe", "clang++.exe"):
            p = Path(d) / exe_name
            if p.exists():
                candidates.append((str(p), f"安装目录 {d}"))

    # 3) winget 安装的 WinLibs / LLVM（路径带哈希，需通配搜索）
    local_app = os.environ.get("LOCALAPPDATA", "")
    winget_roots = []
    if local_app:
        winget_roots.append(Path(local_app) / "Microsoft" / "WinGet" / "Packages")
        winget_roots.append(Path(local_app) / "Microsoft" / "WindowsApps")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    winget_roots.append(Path(program_files) / "WinLibs")

    for root in winget_roots:
        if not root.is_dir():
            continue
        try:
            for pattern in ("*/mingw64/bin/gcc.exe", "*/mingw32/bin/gcc.exe", "*/bin/gcc.exe",
                            "*/bin/clang.exe", "WinLibs*/**/bin/gcc.exe",
                            "*/mingw64/bin/g++.exe", "*/bin/g++.exe", "*/bin/clang++.exe"):
                for p in root.glob(pattern):
                    if p.is_file():
                        candidates.append((str(p), f"winget 安装 {root}"))
        except (OSError, ValueError):
            pass

    # 去重后逐个验证
    seen: set[str] = set()
    for exe, origin in candidates:
        key = exe.lower()
        if key in seen:
            continue
        seen.add(key)
        is_cxx = any(k in Path(exe).name.lower() for k in ("g++", "clang++", "c++"))
        if is_cxx:
            if tc.cxx is None and _probe(exe):
                tc.cxx = exe
                tc.origin = tc.origin or origin
        else:
            if tc.cc is None and _probe(exe):
                tc.cc = exe
                tc.origin = tc.origin or origin

    # 有 g++ 但没 gcc 时，g++ 也能编译 C（会多带 C++ 运行时，可接受）
    if tc.cc is None and tc.cxx:
        tc.cc = tc.cxx
        tc.notes.append("未找到 gcc，已用 g++ 编译 C 代码，部分 C 写法可能报 C++ 的错。")

    if tc.cc is None:
        tc.notes.append(
            "未检测到 C 编译器。请安装 MinGW-w64（推荐 https://github.com/niXman/mingw-builds-binaries ）"
            "并把 bin 目录加入 PATH 后重启程序；Windows 用户也可在 PowerShell 执行 "
            "`winget install --id BrechtSanders.WinLibs.POSIX.UCRT`。"
        )

    _toolchain_cache = tc
    return tc


# --------------------------------------------------------------------------- #
# 执行器
# --------------------------------------------------------------------------- #
class CodeRunner:
    def __init__(self, timeout: int = 10, work_root: Path | None = None):
        self.timeout = timeout
        self.work_root = work_root or (Path(tempfile.gettempdir()) / "codefix_sandbox")
        self.work_root.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        code: str,
        language: str = "",
        stdin: str = "",
        filename: str = "",
        timeout: int | None = None,
        extra_args: list[str] | None = None,
    ) -> RunResult:
        language = (language or detect_language(code, filename)).lower()
        if language in ("c++", "cpp"):
            language = "cpp"

        run_dir = self.work_root / f"run_{uuid.uuid4().hex[:10]}"
        run_dir.mkdir(parents=True, exist_ok=True)
        limit = timeout or self.timeout

        try:
            if language == "python":
                result = self._run_python(code, run_dir, stdin, limit, extra_args)
            elif language in ("c", "cpp"):
                result = self._run_c(code, language, run_dir, stdin, limit, extra_args)
            else:
                result = RunResult(
                    language=language, ok=False, stage="env_error",
                    stderr=f"暂不支持的语言：{language}（目前支持 C / C++ / Python）",
                )
            return self._sanitize(result, run_dir)
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    @staticmethod
    def _sanitize(result: RunResult, run_dir: Path) -> RunResult:
        """把沙箱绝对路径换成 main.py，避免干扰新手阅读报错。"""
        raw = str(run_dir)
        variants = {
            raw + os.sep + "main.py": "main.py",
            raw + "/main.py": "main.py",
            raw + os.sep + "main.c": "main.c",
            raw + "/main.c": "main.c",
            raw + os.sep + "main.cpp": "main.cpp",
            raw + "/main.cpp": "main.cpp",
            raw + os.sep + "main.exe": "main.exe",
            raw + "/main.exe": "main.exe",
            raw: ".",
        }
        py_exe = sys.executable

        def clean(text: str) -> str:
            if not text:
                return text
            for k, v in variants.items():
                text = text.replace(k, v)
            if py_exe:
                text = text.replace(py_exe, "python")
            return text

        result.stdout = clean(result.stdout)
        result.stderr = clean(result.stderr)
        result.command = clean(result.command)
        return result

    # ----------------------------- Python ----------------------------- #
    def _run_python(self, code, run_dir, stdin, limit, extra_args) -> RunResult:
        src = run_dir / "main.py"
        src.write_text(code, encoding="utf-8")

        cmd = [sys.executable, "-u", str(src)] + (extra_args or [])
        env = os.environ.copy()
        env.update({
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            # 让 traceback 是标准英文报错，便于解析与教学
            "PYTHONNOUSERSITE": "1",
        })

        return self._exec(
            cmd, cwd=run_dir, stdin_text=stdin, limit=limit, language="python",
            env=env,
        )

    # ------------------------------ C/C++ ------------------------------ #
    def _run_c(self, code, language, run_dir, stdin, limit, extra_args) -> RunResult:
        tc = detect_toolchain()
        if not tc.has_c:
            return RunResult(
                language=language, ok=False, stage="env_error",
                stderr="\n".join(tc.notes),
                compiler_hint="\n".join(tc.notes),
            )

        ext = ".c" if language == "c" else ".cpp"
        src = run_dir / f"main{ext}"
        src.write_text(code, encoding="utf-8")
        binary = run_dir / ("main.exe" if os.name == "nt" else "main")

        if language == "c":
            compiler = tc.cc
            std_flag = "-std=c11"
        else:
            compiler = tc.cxx or tc.cc
            std_flag = "-std=c++17"

        if not compiler:
            return RunResult(
                language=language, ok=False, stage="env_error",
                stderr="未找到可用的 C/C++ 编译器。",
                compiler_hint="\n".join(tc.notes),
            )

        # -Wall -Wextra 让新手看到"能编译但不规范"的问题；-fdiagnostics-color=never 便于解析
        compile_cmd = [
            compiler, std_flag, "-O0", "-g",
            "-Wall", "-Wextra",
            "-fno-diagnostics-color",
            str(src), "-o", str(binary), "-lm",
        ]
        # 展示给用户看的版本：编译器只留名字，路径用文件名，避免一屏都是 C:\Users\...
        # compile_cmd 结构: [gcc, 选项..., src, -o, binary, -lm]
        pretty_cmd = " ".join(
            [Path(compiler).stem] + compile_cmd[1:-4] + ["main" + ext, "-o", "main", "-lm"]
        )
        if os.name == "nt":
            # Windows 中文系统下 gcc 输出是 GBK，强制英文更利于解析教学
            env = os.environ.copy()
            env["LC_ALL"] = "C"
            env["LANG"] = "C"
        else:
            env = os.environ.copy()

        comp = subprocess.run(
            compile_cmd, cwd=run_dir, capture_output=True, text=True,
            timeout=60, encoding="utf-8", errors="replace", env=env,
        )

        if comp.returncode != 0 or not binary.exists():
            return RunResult(
                language=language,
                ok=False,
                stage="compile_error",
                compile_ok=False,
                command=pretty_cmd,
                stdout=comp.stdout or "",
                stderr=comp.stderr or comp.stdout or "",
                exit_code=comp.returncode,
                compiler_hint=f"编译器：{compiler}（来自 {tc.origin or '未知'}）",
            )

        result = self._exec(
            [str(binary)] + (extra_args or []),
            cwd=run_dir, stdin_text=stdin, limit=limit, language=language, env=env,
        )
        result.command = pretty_cmd + "\n" + result.command
        result.compile_ok = True
        result.compiler_hint = f"编译器：{compiler}（来自 {tc.origin or '未知'}）"
        # 编译有警告时也要告诉用户（警告虽不阻止运行，但常藏着 bug）
        warnings = (comp.stdout or "") + (comp.stderr or "")
        if warnings.strip():
            result.stderr = warnings + "\n" + result.stderr
        return result

    # ---------------------------- 通用执行 ---------------------------- #
    def _exec(self, cmd, cwd, stdin_text, limit, language, env) -> RunResult:
        import time
        started = time.time()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(cwd),
                input=(stdin_text or ""),
                capture_output=True,
                text=True,
                timeout=limit,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            elapsed = time.time() - started
            stage = "success" if proc.returncode == 0 else "runtime_error"
            return RunResult(
                language=language,
                ok=proc.returncode == 0,
                stage=stage,
                command=" ".join(cmd),
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                exit_code=proc.returncode,
                duration=round(elapsed, 3),
            )
        except subprocess.TimeoutExpired as e:
            elapsed = time.time() - started
            out = e.stdout or ""
            err = e.stderr or ""
            if isinstance(out, bytes):
                out = out.decode("utf-8", "replace")
            if isinstance(err, bytes):
                err = err.decode("utf-8", "replace")
            return RunResult(
                language=language,
                ok=False,
                stage="timeout",
                command=" ".join(cmd),
                stdout=out,
                stderr=(err + "\n" if err else "") +
                       f"[程序运行超过 {limit} 秒被强制结束] 常见原因：死循环（while 条件永远为真）、"
                       f"或者程序在等你输入但你没有提供输入。",
                exit_code=None,
                duration=round(elapsed, 3),
            )
        except FileNotFoundError:
            return RunResult(
                language=language, ok=False, stage="env_error",
                command=" ".join(cmd),
                stderr="找不到解释器/编译器，请检查环境安装。",
            )
