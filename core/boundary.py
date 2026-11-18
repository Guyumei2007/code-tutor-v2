"""
边界测试引擎：自动识别"这段程序要读什么输入、大概在算什么"，
然后针对性地造极端数据，真的跑一遍。

为什么需要它：
新手写的代码通常能通过"老师给的样例"，但一遇到空数据、单个数据、0、负数、
超大值就崩。这些恰恰是程序健壮性的分水岭，而新手往往想不到要自测。

设计原则：
1. 只报"能确定"的失败（崩溃、超时、nan/inf、退出码非 0），不瞎猜结果对错。
2. 能自动验证的不变量一定验证（例如求 1 个数的平均，结果必然等于那个数）。
3. 无法判定的，如实说"跑通了，但结果需要你自己核对"，不假装通过。
4. 每个用例都说明"为什么造它"，让新手学会怎么想测试。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

from .runner import CodeRunner, RunResult

MAX_CASES = 7
CASE_TIMEOUT = 3


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass
class InputSpec:
    """程序期望从键盘读什么。"""
    mode: str = "unknown"       # n_then_values / line_values / single / repeat / none / unknown
    value_type: str = "int"     # int / float / str
    count: int = 0              # line_values 时一行几个；0 表示数量不定
    desc: str = ""              # 给用户看的说明
    confidence: str = "low"     # high / medium / low


@dataclass
class Profile:
    """对这段程序的整体判断。"""
    task: str = "generic"
    task_label: str = "通用程序"
    confidence: str = "low"
    spec: InputSpec = field(default_factory=InputSpec)
    notes: list[str] = field(default_factory=list)
    has_division: bool = False     # 代码里是否真的出现 / 或 %，用于避免提「除以 0」

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TestCase:
    name: str
    stdin: str
    why: str                       # 为什么要测这个
    expect: str = ""               # 期望（不变量或说明）
    checker: str = ""              # 内部判定标记
    expect_value: str = ""         # 期望输出里应包含的关键值（精确核对用）


@dataclass
class CaseResult:
    name: str = ""
    stdin: str = ""
    why: str = ""
    expect: str = ""
    status: str = "pass"           # pass / fail / warn / unknown
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration: float = 0.0
    reason: str = ""               # 失败原因（人话）
    advice: str = ""               # 修改建议
    kind: str = ""                 # 崩溃类型，用于知识库匹配
    expect_value: str = ""         # 期望输出里的关键值

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# 1. 识别输入形态
# --------------------------------------------------------------------------- #
PY_INPUT_RE = re.compile(r"input\s*\(")
PY_INT_INPUT_RE = re.compile(r"int\s*\(\s*input\s*\(")
PY_FLOAT_INPUT_RE = re.compile(r"float\s*\(\s*input\s*\(")
# 注意：input() 里常常带提示文字（input('请输入：')），正则必须允许括号内有内容，
# 否则「一行读多个数」会被误判成「只读一个值」，进而把数据算错。
PY_SPLIT_INPUT_RE = re.compile(
    r"([A-Za-z_]\w*)\s*=\s*(?:list\s*\(\s*)?map\s*\(\s*(int|float)\s*,\s*"
    r"input\s*\([^)]*\)\s*\.split\s*\("
)
PY_UNPACK_RE = re.compile(
    r"([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)+)\s*=\s*map\s*\(\s*(int|float)\s*,\s*"
    r"input\s*\([^)]*\)\s*\.split"
)
PY_N_INPUT_RE = re.compile(r"([A-Za-z_]\w*)\s*=\s*int\s*\(\s*input\s*\(")

C_SCANF_RE = re.compile(r'scanf\s*\(\s*"([^"]*)"')
C_SPEC_RE = re.compile(r"%\s*(?:[\d.*+-]*)?([dioufFeEgGxXcsl]+)")


def _spec_type(specs: list[str]) -> str:
    if not specs:
        return "int"
    joined = "".join(specs)
    if any(c in joined for c in "fFeEgG"):
        return "float"
    if any(c in joined for c in "sc"):
        return "str"
    return "int"


def detect_input_spec(code: str, language: str) -> InputSpec:
    lang = (language or "").lower()

    if lang in ("c", "cpp"):
        scanfs = C_SCANF_RE.findall(code)
        if not scanfs:
            return InputSpec("none", "int", 0, "程序不读取任何输入", "high")

        specs = []
        for fmt in scanfs:
            specs.extend(C_SPEC_RE.findall(fmt))
        vtype = _spec_type(specs)

        # 判断是否有 "先读个数 n，再循环读 n 个" 的结构
        has_loop_read = bool(re.search(
            r"for\s*\([^)]*<[^)]*\)\s*\{?[^}]{0,200}?scanf", code, re.S
        ))
        first_fmt = scanfs[0] if scanfs else ""
        first_cnt = len(C_SPEC_RE.findall(first_fmt)) or 1

        if has_loop_read and first_cnt == 1:
            return InputSpec(
                "n_then_values", vtype, 0,
                f"第 1 行输入一个数字（数量 n），之后输入 n 个{'小数' if vtype == 'float' else '整数'}",
                "high",
            )
        if len(scanfs) == 1 and first_cnt >= 2:
            return InputSpec(
                "line_values", vtype, first_cnt,
                f"一行输入 {first_cnt} 个{'小数' if vtype == 'float' else '整数'}",
                "high",
            )
        if len(scanfs) == 1:
            return InputSpec("single", vtype, 1, "输入 1 个值", "high")
        return InputSpec(
            "repeat", vtype, 0,
            f"依次输入 {len(scanfs)} 个值（共 {len(specs)} 个格式符）", "medium",
        )

    # ---- Python ----
    if not PY_INPUT_RE.search(code):
        return InputSpec("none", "int", 0, "程序不读取任何输入", "high")

    split_m = PY_SPLIT_INPUT_RE.search(code)
    unpack_m = PY_UNPACK_RE.search(code)
    n_input_m = PY_N_INPUT_RE.search(code)

    # "n 然后 n 个数"：先 int(input()) 拿数量，循环里再 input()
    if n_input_m:
        n_name = n_input_m.group(1)
        # 允许 range(n)、range(n-1)、range(n+1) 等写法（range(n-1) 本身常是 bug，
        # 但仍属"先读数量再读数据"的形态，必须识别出来才能正确解析输入）
        loop_read = re.search(
            r"for\s+\w+\s+in\s+range\s*\(\s*" + re.escape(n_name) + r"\s*(?:[-+]\s*\d+\s*)?\)"
            r".*?input\s*\(", code, re.S,
        )
        if loop_read:
            vtype = "float" if PY_FLOAT_INPUT_RE.search(code) else "int"
            return InputSpec(
                "n_then_values", vtype, 0,
                f"第 1 行输入数量 n（变量 {n_name}），之后输入 n 个值",
                "high",
            )

    if unpack_m:
        cnt = len(unpack_m.group(1).split(","))
        return InputSpec("line_values", unpack_m.group(2), cnt,
                         f"一行输入 {cnt} 个数值（用空格隔开）", "high")

    if split_m:
        vtype = split_m.group(2)
        return InputSpec("line_values", vtype, 0,
                         "一行输入若干个数值（用空格隔开，个数不定）", "medium")

    total = len(PY_INPUT_RE.findall(code))
    vtype = "float" if PY_FLOAT_INPUT_RE.search(code) else ("int" if PY_INT_INPUT_RE.search(code) else "str")
    if total == 1:
        return InputSpec("single", vtype, 1, f"输入 1 个{'数字' if vtype != 'str' else '字符串'}", "high")
    return InputSpec("repeat", vtype, 0, f"依次输入 {total} 个值", "medium")


# --------------------------------------------------------------------------- #
# 2. 识别程序在算什么
# --------------------------------------------------------------------------- #
TASK_RULES: list[tuple[str, str, list[str]]] = [
    # (task, 展示名, 正则列表：命中任一即算)
    ("average", "求平均值", [
        r"\baverage\b", r"\bavg\b", r"\bmean\b", r"平均",
        r"/\s*(?:len\s*\(|count\b|n\b|num\b)", r"sum\s*/\s*\w+",
    ]),
    ("factorial", "求阶乘", [r"\bfactorial\b", r"\bfact\b", r"阶乘", r"\*=\s*i\b"]),
    ("prime", "判断素数", [r"\bprime\b", r"素数", r"%\s*i\s*==\s*0"]),
    ("sort", "排序", [r"\.sort\s*\(", r"\bsorted\s*\(", r"\bqsort\b", r"排序",
                       r"for\s*\([^)]*\)\s*\{\s*for\s*\(", r"bubble"]),
    ("maxmin", "求最大/最小值", [r"\bmax\s*\(", r"\bmin\s*\(", r"\blargest\b", r"\bsmallest\b",
                                 r"\bmaximum\b", r"\bminimum\b", r"最大", r"最小"]),
    ("sum", "求和/累加", [r"\btotal\b", r"\bsum\b", r"\+= ", r"\+=", r"累加", r"总和"]),
    ("count", "统计个数", [r"\bcount\b", r"个数", r"计数"]),
]


def detect_task(code: str, language: str) -> tuple[str, str, str]:
    """返回 (task, 展示名, 置信度)。

    TASK_RULES 已按「特异性从高到低」排好序：越靠前的规则越具体。
    因此一旦命中就直接采用，不再让后面的宽泛规则（如 sum）抢走判断
    —— 否则「求平均值」里出现 total += 时会被误判成「求和」。
    """
    text = code
    for task, label, pats in TASK_RULES:
        hits = sum(1 for p in pats if re.search(p, text, re.I))
        if hits >= 1:
            return task, label, "high" if hits >= 2 else "medium"
    return "generic", "通用程序", "low"


# --------------------------------------------------------------------------- #
# 3. 生成用例
# --------------------------------------------------------------------------- #
def generate_cases(profile: Profile) -> list[TestCase]:
    spec = profile.spec
    task = profile.task
    cases: list[TestCase] = []
    # 语义闸门：代码里没有除法/取模、也不是求平均，就绝不提「除以 0」
    div_relevant = profile.has_division or task == "average"

    if spec.mode == "none":
        # 不读输入：边界靠"没有输入"和"环境"来测，这里只做一次确认
        cases.append(TestCase(
            name="无输入运行", stdin="",
            why="这段程序不需要输入，确认它在无输入下能正常结束。",
            expect="正常退出，退出码为 0", checker="plain",
        ))
        return cases[:MAX_CASES]

    # 阶乘：绝对不能用「除以 0」那套模板，必须按阶乘语义生成用例
    if task == "factorial" and spec.mode in ("single", "line_values", "unknown"):
        cases.extend([
            TestCase(
                name="输入 0（0! = 1）", stdin="0\n",
                why="阶乘的数学定义是 0! = 1，很多从 1 开始循环的写法会漏掉这个边界。",
                expect="输出 1", checker="factorial", expect_value="1",
            ),
            TestCase(
                name="输入 1（1! = 1）", stdin="1\n",
                why="最小正数，用来检查循环起点和乘积初值是否写对。",
                expect="输出 1", checker="factorial", expect_value="1",
            ),
            TestCase(
                name="输入 5（5! = 120）", stdin="5\n",
                why="正常规模，确认基本的连乘逻辑没有问题。",
                expect="输出 120", checker="factorial", expect_value="120",
            ),
            TestCase(
                name="输入 20（20! 超出 32 位 int）", stdin="20\n",
                why="20! = 2432902008176640000，远超 32 位 int 的上限（约 21.47 亿）。"
                    "C/C++ 里若用 int 存乘积，会溢出成错误值。",
                expect="输出 2432902008176640000（C/C++ 需用 long long）",
                checker="factorial_big", expect_value="2432902008176640000",
            ),
        ])
        return cases[:MAX_CASES]

    if spec.mode == "n_then_values":
        vtype = spec.value_type

        # 先写数量 n，之后每个值单独一行。
        # 这样 Python 的 input()（每次读一行）和 C 的 scanf（自动跳过空白）都能正确读取。
        def nv(values: list[str]) -> str:
            return f"{len(values)}\n" + "".join(f"{v}\n" for v in values)

        # ① 空数据：新手最常翻车的点
        cases.append(TestCase(
            name="空数据（n=0）", stdin=nv([]),
            why=(
                "很多程序默认「至少有一个数」，n=0 时求平均会除以 0。"
                if div_relevant else
                "很多程序默认「至少有一个数」，n=0 时可能访问空列表或循环一次都不执行。"
            ),
            expect="不崩溃；若求平均，应提示「没有数据」而不是崩溃",
            checker="no_crash",
        ))
        # ② 只有一个数
        cases.append(TestCase(
            name="单个数据（n=1）", stdin=nv(["5"]),
            why="验证边界上结果是否正确：只有一个数时，平均值/总和/最大值都应该等于它自己。",
            expect="结果等于 5",
            checker="single_value_sum",
        ))
        # ③ 全是 0
        cases.append(TestCase(
            name="全部为 0（n=3）", stdin=nv(["0", "0", "0"]),
            why="全 0 常用于检查是否把「0」误当成「没有数据」。",
            expect="不崩溃，结果为 0",
            checker="no_crash",
        ))
        # ④ 含负数
        cases.append(TestCase(
            name="含负数（n=3）", stdin=nv(["-5", "0", "5"]),
            why="负数是最容易漏测的情况，很多累加/比较逻辑在负数下才暴露问题。",
            expect="不崩溃，平均值应为 0",
            checker="no_crash",
        ))
        # ⑤ 重复值
        cases.append(TestCase(
            name="全部相同（n=4）", stdin=nv(["7", "7", "7", "7"]),
            why="检查程序是否把「值相同」和「只有一个元素」混淆。",
            expect="不崩溃，结果应为 7",
            checker="no_crash",
        ))

        # ⑥ 极大值：C 语言整型溢出
        if vtype == "int":
            big = "2147483647"
            if task in ("average", "sum"):
                cases.append(TestCase(
                    name="极大值（n=2，接近 int 上限）", stdin=nv([big, big]),
                    why="两个 21 亿相加会超出 32 位 int 的范围（约 21.47 亿），C 语言里会溢出成负数。",
                    expect="结果仍应为 2147483647",
                    checker="big_value",
                ))
        else:
            cases.append(TestCase(
                name="极大值（n=2，1e308）", stdin=nv(["1e308", "1e308"]),
                why="浮点数相加可能变成 inf（无穷大），再相除就得到 nan。",
                expect="不出现 inf / nan",
                checker="no_nan",
            ))

        # ⑦ 小数精度（仅浮点）
        if vtype == "float":
            cases.append(TestCase(
                name="小数精度（n=3）", stdin=nv(["0.1", "0.2", "0.3"]),
                why="0.1+0.2 在计算机里不等于 0.3，检查程序是否做了合理处理。",
                expect="不崩溃，结果约等于 0.2",
                checker="no_crash",
            ))

        return cases[:MAX_CASES]

    if spec.mode == "line_values":
        cnt = spec.count
        vtype = spec.value_type
        nums = lambda xs: " ".join(xs) + "\n"  # noqa: E731

        if cnt >= 2:
            cases.append(TestCase(
                name=f"全部为 0（{cnt} 个）", stdin=nums(["0"] * cnt),
                why=(
                    "全 0 检查程序是否把 0 当成非法输入，或触发除以 0。"
                    if div_relevant else
                    "全 0 检查程序是否把 0 当成非法输入。"
                ),
                expect="不崩溃", checker="no_crash",
            ))
            cases.append(TestCase(
                name=f"含负数（{cnt} 个）", stdin=nums(["-5", "0", "5"][:cnt]),
                why="负数边界，容易暴露比较逻辑和符号处理的问题。",
                expect="不崩溃", checker="no_crash",
            ))
            cases.append(TestCase(
                name=f"相同值（{cnt} 个）", stdin=nums(["3"] * cnt),
                why="相同值可以暴露「交换/排序/去重」逻辑里的隐患。",
                expect="不崩溃", checker="no_crash",
            ))
            if vtype == "int":
                cases.append(TestCase(
                    name=f"极大值（{cnt} 个）", stdin=nums(["2147483647"] * cnt),
                    why="接近 int 上限，C 语言里相加会溢出。",
                    expect="不崩溃，结果不应变成负数", checker="big_value",
                ))
        else:
            # 一行若干个，个数不定
            cases.append(TestCase(
                name="空输入（直接回车）", stdin="\n",
                why="用户什么都没输入就按回车，程序不应崩溃。",
                expect="给出提示或正常结束，不能抛异常", checker="no_crash",
            ))
            cases.append(TestCase(
                name="只有一个数", stdin="5\n",
                why="最小规模输入，检查循环和边界条件。",
                expect="不崩溃，结果应为 5", checker="no_crash",
            ))
            cases.append(TestCase(
                name="全为 0", stdin="0 0 0\n",
                why="全 0 常见于除零场景。",
                expect="不崩溃", checker="no_crash",
            ))
            cases.append(TestCase(
                name="含负数", stdin="-5 0 5\n",
                why="负数边界。",
                expect="不崩溃", checker="no_crash",
            ))
            cases.append(TestCase(
                name="极大值", stdin="2147483647 2147483647\n",
                why="整数溢出边界。",
                expect="不崩溃", checker="big_value",
            ))
        return cases[:MAX_CASES]

    if spec.mode == "single":
        vtype = spec.value_type
        if vtype == "str":
            cases.append(TestCase("空字符串", "\n", "直接回车，检查空串处理。", "不能崩溃", "no_crash"))
            cases.append(TestCase("单字符", "a\n", "最小规模输入。", "不能崩溃", "no_crash"))
            cases.append(TestCase("超长字符串", "a" * 500 + "\n", "很长的输入可能撑爆固定长度数组。", "不能崩溃", "no_crash"))
            cases.append(TestCase("含中文", "你好\n", "编码问题在 Windows 上很常见。", "不能崩溃", "no_crash"))
        else:
            zero_why = (
                "0 是求平均/除法最容易出问题的边界（可能触发除以 0）。"
                if div_relevant else
                "0 是最经典的边界值，检查程序对 0 的处理是否合理。"
            )
            cases.append(TestCase("输入 0", "0\n", zero_why, "不能崩溃", "no_crash"))
            cases.append(TestCase("输入负数", "-1\n", "负数边界，循环次数或数组长度会出问题。", "不能崩溃", "no_crash"))
            cases.append(TestCase("输入极大值", "2147483647\n", "接近 int 上限，检查是否溢出。", "不能崩溃", "no_crash"))
            if vtype == "float":
                cases.append(TestCase("输入 0.0", "0.0\n", "浮点 0，检查除零和精度。", "不能崩溃", "no_crash"))
        return cases[:MAX_CASES]

    # repeat / unknown：保守造几个
    cases.append(TestCase("输入 0", "0\n0\n", "0 是关键边界值。", "不能崩溃", "no_crash"))
    cases.append(TestCase("输入负数", "-1\n-1\n", "负数边界。", "不能崩溃", "no_crash"))
    cases.append(TestCase("正常小值", "1\n1\n", "最小合法输入，确认基本功能正常。", "不能崩溃", "plain"))
    return cases[:MAX_CASES]


# --------------------------------------------------------------------------- #
# 4. 判定
# --------------------------------------------------------------------------- #
NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")
BAD_WORDS_RE = re.compile(r"\b(nan|inf|infinity|-inf)\b", re.I)


def _extract_numbers(text: str) -> list[float]:
    out = []
    for m in NUMBER_RE.finditer(text or ""):
        try:
            out.append(float(m.group(0)))
        except ValueError:
            pass
    return out


def judge(case: TestCase, run: RunResult, language: str = "", code: str = "") -> CaseResult:
    r = CaseResult(
        name=case.name, stdin=case.stdin, why=case.why, expect=case.expect,
        expect_value=case.expect_value,
        stdout=(run.stdout or "")[:3000],
        stderr=(run.stderr or "")[:4000],
        exit_code=run.exit_code, duration=run.duration,
    )

    # --- 超时 ---
    if run.stage == "timeout":
        # 「无输入运行」这类探测性用例超时，更可能是程序在等我们没识别出来的输入，
        # 不能算作程序的错，只标记为待确认。
        if case.checker == "plain":
            r.status = "unknown"
            r.reason = "程序在等待输入（输入形式未能识别），因此这次超时不能算作错误。"
            return r

        r.status = "fail"
        lang_c = (language or "").lower() in ("c", "cpp")
        has_div = bool(re.search(r"[/%]\s*[A-Za-z_(0-9]", code or ""))

        if lang_c and has_div:
            # Windows 上 C 程序整除 0 常常不是干净退出，而是卡在系统错误框上
            r.reason = (
                "程序没有正常结束。C 语言里「整数除以 0」属于未定义行为，"
                "在 Windows 上往往会让进程卡住（弹出系统错误框）而不是正常退出。"
            )
            r.advice = (
                "先确认这个输入下分母是不是 0（例如数据个数 n 为 0 时做 total / n）。"
                "在除法之前加判断：`if (n == 0) { printf(\"没有数据\\n\"); return 0; }`"
            )
            r.kind = "DivideByZeroSuspect"
            return r

        r.reason = "程序在这个输入下超过了时间限制，一直没有结束。"
        r.advice = (
            "常见原因：循环的终止条件依赖输入值，而该值没有让循环停下来；"
            "或者程序在等待比预期更多的输入。请检查循环条件与 input()/scanf() 的次数。"
        )
        r.kind = "Timeout"
        return r

    # --- 崩溃 / 编译失败 ---
    crashed = (
        run.stage in ("runtime_error", "syntax_error", "compile_error")
        or (run.exit_code not in (0, None) and "Traceback" in (run.stderr or ""))
    )
    if crashed:
        r.status = "fail"
        r.kind = _guess_kind(run.stderr or "")
        r.reason = _explain_crash(r.kind, run.stderr or "")
        r.advice = _advise_crash(r.kind, run.stderr or "")
        return r

    if run.stage == "env_error":
        r.status = "unknown"
        r.reason = "环境问题，未能运行。"
        return r

    out = run.stdout or ""
    combined = out + "\n" + (run.stderr or "")

    # --- 输出里出现 nan / inf ---
    if BAD_WORDS_RE.search(out):
        r.status = "fail"
        word = BAD_WORDS_RE.search(out).group(1).lower()
        r.reason = f"输出里出现了 `{word}`，说明计算过程中出现了非法数值。"
        if word == "nan":
            r.advice = (
                "nan 通常来自 0/0 或者 inf-inf。检查是否在数据为空时做了除法"
                "（比如平均值 = 总和 / 个数，个数为 0 就会这样）。应该先判断个数并给出提示。"
            )
        else:
            r.advice = "inf 表示数值已经大到装不下（溢出）。换用更大的类型，或先检查数值范围。"
        r.kind = "InvalidNumber"
        return r

    # --- 精确值核对（阶乘边界等） ---
    if case.expect_value:
        compact = out.replace(" ", "").replace(",", "")
        if case.expect_value in compact:
            r.status = "pass"
            return r
        r.status = "fail"
        r.reason = f"期望结果是 {case.expect_value}，但程序输出「{out.strip()[:80] or '空'}」。"
        if case.checker == "factorial_big":
            r.advice = (
                "20! 已超过 32 位 int 的上限，乘积变量必须用 long long（C/C++），"
                "输出用 %lld；或者改用 Python（整数不会溢出）。"
            )
            r.kind = "IntegerOverflow"
        else:
            r.advice = "检查循环起点、乘积初值（应为 1）以及循环结束条件。"
        return r

    # --- 不变量检查 ---
    if case.checker == "single_value_sum":
        expected = 5.0
        nums = _extract_numbers(out)
        if not nums:
            r.status = "unknown"
            r.reason = "程序正常运行，但没有在输出中找到数字，无法自动核对结果。"
            r.advice = "确认程序是否真的输出了计算结果。"
            return r
        if any(abs(x - expected) < 1e-6 for x in nums):
            r.status = "pass"
            return r
        r.status = "fail"
        r.reason = f"只有一个数据（5）时，结果应当就是 5，但程序输出里没有 5。"
        r.advice = (
            "检查求和的初始值、循环次数和除法。典型 bug：循环写了 range(n+1) 多算一次、"
            "或者累加变量没清零、或者除法用错了分母。"
        )
        return r

    if case.checker == "big_value":
        nums = _extract_numbers(out)
        neg = any(x < 0 for x in nums)
        if neg:
            r.status = "fail"
            r.reason = "输出中出现了负数结果，说明结果已经超出 32 位 int 的范围，发生了整数溢出。"
            r.advice = (
                "把参与计算的变量改成 long long（C/C++），输出用 %lld；"
                "或者改用 Python（整数不会溢出）。"
            )
            r.kind = "IntegerOverflow"
            return r

    # --- 输出为空 ---
    if not out.strip() and case.checker != "plain":
        r.status = "warn"
        r.reason = "程序正常结束，但没有任何输出。"
        r.advice = "检查是否漏了 print / printf，或者输出语句被放在了不会执行的 if 分支里。"
        return r

    r.status = "pass"
    return r


def _guess_kind(stderr: str) -> str:
    m = re.search(r"(\w+(?:Error|Exception))", stderr or "")
    if m:
        return m.group(1)
    if "Segmentation fault" in (stderr or "") or "段错误" in (stderr or ""):
        return "SegmentationFault"
    if "expected" in (stderr or "") or "error:" in (stderr or ""):
        return "CompileError"
    return "UnknownError"


def _explain_crash(kind: str, stderr: str) -> str:
    table = {
        "ZeroDivisionError": "程序在这里除以了 0。",
        "IndexError": "程序访问了不存在的下标（数组/列表越界）。",
        "ValueError": "程序拿到了无法转换的值（比如把空字符串转成数字）。",
        "TypeError": "程序把不匹配的类型放在一起运算了。",
        "NameError": "程序用了没有定义过的变量。",
        "UnboundLocalError": "函数里的变量还没赋值就被使用了。",
        "SegmentationFault": "程序访问了非法内存（段错误），通常是数组越界或空指针。",
        "CompileError": "这段代码没能通过编译。",
        "RecursionError": "递归太深，没有正确的终止条件。",
        "KeyboardInterrupt": "程序被中断。",
    }
    base = table.get(kind, "程序在这个输入下崩溃了。")
    # 补充原始报错的最后一行，让用户知道真实原因
    last = ""
    for line in reversed((stderr or "").strip().split("\n")):
        if line.strip():
            last = line.strip()
            break
    if last and last not in base:
        base += f"\n\n原始报错最后一行：`{last}`"
    return base


def _advise_crash(kind: str, stderr: str = "") -> str:
    table = {
        "ZeroDivisionError": (
            "这是这次边界测试最想抓的 bug：数据为空（或个数为 0）时仍然做了除法。"
            "先判断个数：`if n == 0: print(\"没有数据\"); return`，再做平均/除法。"
        ),
        "IndexError": "循环的边界多走了一步。检查 `range(...)` 或数组下标，最后一个有效下标是 长度-1。",
        "ValueError": "输入转换失败。用 try/except 包住 int()/float()，或先判断输入是否为空。",
        "SegmentationFault": (
            "C 语言段错误。常见原因：数组越界（`<=` 写成了数组长度）、scanf 漏了 `&`、"
            "指针未初始化。建议用 `-fsanitize=address -g` 重新编译定位。"
        ),
        "RecursionError": "补上递归的终止条件，确保每次递归都更接近终止。",
        "CompileError": "先解决编译错误，边界测试才有意义。",
    }
    return table.get(kind, "针对这个输入加一个判断分支，让它不至于崩溃。")


# --------------------------------------------------------------------------- #
# 5. 对外入口
# --------------------------------------------------------------------------- #
def _code_has_division(code: str) -> bool:
    """代码里是否真的存在除法/取模（忽略注释和字符串里的 / 与 %）。"""
    text = re.sub(r"//[^\n]*|/\*.*?\*/", "", code, flags=re.S)
    text = re.sub(r'""".*?"""|\'\'\'.*?\'\'\'', "", text, flags=re.S)
    text = re.sub(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'', "", text)
    return bool(re.search(r"/[^/*]|%", text))


def analyze(
    code: str,
    language: str,
    task: str = "",
    task_label: str = "",
) -> Profile:
    """识别这段程序。task/task_label 非空时表示「用户自己说了它在做什么」，
    此时以用户描述为准，不再用代码关键词去猜。"""
    spec = detect_input_spec(code, language)

    if task:
        label = task_label or task
        conf = "high"
        notes = [f"按你写的作用「{label}」来设计边界用例。"]
    else:
        task, label, conf = detect_task(code, language)
        notes = []

    if spec.confidence == "low":
        notes.append("输入格式是推测出来的，用例可能不准确。")

    # 平均值 + 读了 n 个数据 → 除零风险极高，单独提示
    if task == "average" and spec.mode == "n_then_values":
        notes.append("检测到「求平均值」且先读数量再读数据，n=0 时极易出现除以 0。")

    # 只有代码里真的出现除法/取模，才允许在用例里提「除以 0」。
    # 注意先去掉注释和字符串，避免把 printf("%d\n") 里的 \n 误判成除法。
    has_division = _code_has_division(code or "")
    return Profile(
        task=task, task_label=label, confidence=conf,
        spec=spec, notes=notes, has_division=has_division,
    )


def run_suite(
    code: str,
    language: str,
    runner: CodeRunner | None = None,
    max_cases: int = MAX_CASES,
    task: str = "",
    task_label: str = "",
) -> dict[str, Any]:
    """生成并执行边界用例。返回可直接序列化给前端的结果。"""
    profile = analyze(code, language, task=task, task_label=task_label)

    cases = generate_cases(profile)[:max_cases]

    if profile.spec.mode == "none" and not cases:
        return {
            "profile": profile.to_dict(),
            "cases": [],
            "summary": {"total": 0, "pass": 0, "fail": 0, "warn": 0, "unknown": 0},
            "conclusion": "没识别出输入形式，跳过边界测试。",
        }

    rn = runner or CodeRunner(timeout=CASE_TIMEOUT)
    results: list[CaseResult] = []

    for case in cases:
        run = rn.run(code, language, stdin=case.stdin, timeout=CASE_TIMEOUT)
        results.append(judge(case, run, language, code))

    stats = {"total": len(results), "pass": 0, "fail": 0, "warn": 0, "unknown": 0}
    for r in results:
        stats[r.status] = stats.get(r.status, 0) + 1

    if stats["fail"]:
        conclusion = (
            f"发现 {stats['fail']} 个边界场景会出问题。"
            "你的代码通过了普通样例，但在极端输入下会崩溃 —— 这正是需要修的地方。"
        )
    elif stats["warn"]:
        conclusion = "所有边界场景都能跑完，但有需要留意的地方（见下方黄色标记）。"
    elif stats["pass"] == stats["total"]:
        conclusion = "所有边界用例都通过了，这段程序的健壮性不错。"
    else:
        conclusion = "边界用例已运行，部分结果需要你人工核对。"

    return {
        "profile": profile.to_dict(),
        "cases": [r.to_dict() for r in results],
        "summary": stats,
        "conclusion": conclusion,
    }
