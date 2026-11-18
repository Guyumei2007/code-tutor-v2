"""
正确输出计算器：用一份独立的"参考实现"算出标准答案，再和用户程序的输出比对。

为什么值得做：
程序不崩 ≠ 结果对。新手最常见的困惑是「我的代码跑通了，可答案不对」。
这个模块用完全独立于用户代码的方式（Python 重算一遍）得出正确结果，
把「跑通了」和「算对了」这两件事分开呈现。

安全边界（很重要）：
1. 只有识别出明确的题目类型（求平均/求和/最值/排序/阶乘/素数/计数）才计算，
   识别不出来就如实说「无法自动判断」，绝不瞎猜。
2. 比对采用「输出里是否包含期望值」的宽松策略，宁可少判「不一致」，
   也不要把格式不同但结果正确的代码冤枉成错的。
3. 参考实现基于「常见教材写法」的假设，结果里会注明这一点。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, asdict
from typing import Any

from .boundary import InputSpec, Profile

NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass
class ExpectedResult:
    available: bool = False          # 是否算出了正确答案
    task: str = ""
    task_label: str = ""
    value: Any = None                # 期望值
    display: str = ""                # 人类可读的正确输出
    formula: str = ""                # 怎么算出来的（教学用）
    actual: str = ""                 # 用户程序的实际输出（原样）
    actual_numbers: list = field(default_factory=list)   # 输出里所有数字
    result_numbers: list = field(default_factory=list)   # 只取「结果部分」的数字
    matches: bool | None = None      # True / False / None(无法判断)
    verdict: str = ""                # 一致 / 不一致 / 无法判断
    note: str = ""                   # 补充说明与免责

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _fmt(v: Any) -> str:
    """把数值格式化成好看的样子：2.0 -> 2，0.30000000000000004 -> 0.3"""
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return str(v)
        if abs(v - round(v)) < 1e-9:
            return str(int(round(v)))
        return f"{v:.10g}"
    if isinstance(v, list):
        return " ".join(_fmt(x) for x in v)
    return str(v)


def _extract_numbers(text: str) -> list[float]:
    out = []
    for m in NUM_RE.finditer(text or ""):
        try:
            out.append(float(m.group(0)))
        except ValueError:
            pass
    return out


# 提示语和结果常常挤在同一行（input() 的提示不换行），必须切开只取结果部分
SEGMENT_RE = re.compile(r"[?？:：\n\r]")


def split_result(text: str) -> tuple[list[float], list[float]]:
    """把程序输出切成「提示语」和「结果」两部分。

    返回 (结果数字, 全部数字)。

    为什么需要：新手常写
        n = int(input('请输入个数：'))
        s += int(input(f'请输入第{i+1}个数：'))
        print('平均数是', s / n)
    输出长这样（挤在一行）：
        请输入个数：请输入第1个数：请输入第2个数：平均数是 2.5
    如果直接从整段文字里抓数字，会抓到提示里的 1、2，
    万一正确答案恰好是 2，就会把算错的程序判成「正确」。

    解决办法：结果永远出现在最后一个标点之后，所以从后往前找
    第一段含数字的内容，只信它。
    """
    all_nums = _extract_numbers(text)
    if not (text or "").strip():
        return [], all_nums

    segments = [s for s in SEGMENT_RE.split(text) if s.strip()]
    for seg in reversed(segments):
        nums = _extract_numbers(seg)
        if nums:
            return nums, all_nums
    return all_nums, all_nums


def parse_inputs(stdin: str, spec: InputSpec) -> tuple[list[float], list[float]]:
    """把标准输入解析成数值列表。

    返回 (真正参与计算的数据, 全部数字)。
    区别在于 n_then_values 形态：第一行是数量 n，不是数据本身。
    """
    raw = (stdin or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.split("\n")

    if spec.mode == "n_then_values":
        n: int | None = None
        if lines and lines[0].strip():
            m = re.search(r"-?\d+", lines[0])
            if m:
                n = int(m.group(0))
        rest = _extract_numbers("\n".join(lines[1:]))
        data = rest[:n] if isinstance(n, int) and n >= 0 else rest
        return data, _extract_numbers(raw)

    if spec.mode == "line_values":
        # 这种形态程序只读第一行，多给的行根本不会被读到，不能算进数据
        first = lines[0] if lines else ""
        return _extract_numbers(first), _extract_numbers(raw)

    if spec.mode == "single":
        nums = _extract_numbers(raw)
        return nums[:1], nums

    nums = _extract_numbers(raw)
    return nums, nums


def _is_prime(n: int) -> bool:
    if n < 2:
        return False
    if n % 2 == 0:
        return n == 2
    limit = math.isqrt(n)
    for i in range(3, limit + 1, 2):
        if n % i == 0:
            return False
    return True


def _close(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= max(tol, abs(b) * 1e-9)


# --------------------------------------------------------------------------- #
# 比对
# --------------------------------------------------------------------------- #
def _compare(task: str, value: Any, result_nums: list[float],
             all_nums: list[float], actual_text: str) -> tuple[bool | None, str]:
    # 结果只信「最后一段」里的数字；整段数字仅作为兜底
    nums = result_nums or all_nums
    if not nums:
        return None, "无法判断（程序没有输出任何数字）"

    def contains(v: float) -> bool:
        return any(_close(x, v) for x in nums)

    if task == "maxmin":
        # 最大值和最小值都可能被当作"答案"，命中任意一个就算对
        raw = value if isinstance(value, list) else [value]
        candidates = []
        for x in raw:
            try:
                candidates.append(float(x))
            except (TypeError, ValueError):
                pass
        if any(contains(v) for v in candidates):
            return True, "一致"
        return False, "不一致"

    if task == "sort":
        seq = [float(x) for x in value]
        n = len(seq)
        if n == 0:
            return None, "无法判断"
        for order, label in ((seq, "一致"), (list(reversed(seq)), "一致（降序排列）")):
            for i in range(len(nums) - n + 1):
                window = nums[i:i + n]
                if all(_close(a, b) for a, b in zip(window, order)):
                    return True, label
        return False, "不一致"

    if task == "prime":
        txt = (actual_text or "").lower()
        yes = any(w in txt for w in ("是素数", "素数", "质数", "prime", "true", "yes"))
        no = any(w in txt for w in ("不是素数", "非素数", "合数", "not prime", "false", "no"))
        if bool(value) and yes and not no:
            return True, "一致"
        if (not bool(value)) and no:
            return True, "一致"
        if yes or no:
            return False, "不一致"
        return None, "无法判断（输出里没有明确的「是/否」）"

    # 单值（平均/求和/计数/阶乘）
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None, "无法判断"

    if contains(v):
        return True, "一致"
    return False, "不一致"


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
NOTE_BASE = (
    "按常见教材的算法推算；比较数值时忽略小数末尾的 0（2 与 2.0 视为相同）。"
    "如果题目对输出格式有特殊要求，请以题目为准。"
)


def compute(stdin: str, profile: Profile, actual_stdout: str, code: str = "") -> ExpectedResult:
    """计算正确输出并与实际输出比对。"""
    result_nums, all_nums_out = split_result(actual_stdout or "")
    r = ExpectedResult(
        task=profile.task,
        task_label=profile.task_label,
        actual=(actual_stdout or "")[:3000],
        actual_numbers=all_nums_out,
        result_numbers=result_nums,
    )

    spec = profile.spec
    task = profile.task
    data, all_nums = parse_inputs(stdin, spec)

    if spec.mode == "none":
        r.available = False
        r.verdict = "无法判断"
        r.note = "这段程序不读取输入，无法用通用方法推算它的正确输出。"
        return r

    if task == "generic":
        r.available = False
        r.verdict = "无法判断"
        r.note = "本题没有可自动推算的标准答案，请对照题目要求核对输出。"
        return r

    value: Any = None
    formula = ""
    display_override = ""      # 少数任务需要自定义展示（如最值）

    if task == "average":
        if not data:
            r.available = False
            r.verdict = "无法判断"
            r.note = "没有解析到参与计算的数据。"
            return r
        total = sum(data)
        value = total / len(data)
        formula = f"({'+'.join(_fmt(x) for x in data)}) ÷ {len(data)} = {_fmt(value)}"

    elif task == "sum":
        if not data:
            r.available = False
            r.verdict = "无法判断"
            r.note = "没有解析到参与计算的数据。"
            return r
        value = sum(data)
        formula = f"{'+'.join(_fmt(x) for x in data)} = {_fmt(value)}"

    elif task == "maxmin":
        if not data:
            r.available = False
            r.verdict = "无法判断"
            r.note = "没有解析到参与计算的数据。"
            return r
        mx, mn = max(data), min(data)
        pool = r.result_numbers or r.actual_numbers
        has_mx = any(_close(x, mx) for x in pool)
        has_mn = any(_close(x, mn) for x in pool)
        data_desc = f"（数据：{' '.join(_fmt(x) for x in data)}）"

        # 先看代码本身想求什么（是 max 还是 min），这比猜输出可靠
        code_l = (code or "").lower()
        wants_max = bool(re.search(r"\bmax\b", code_l))
        wants_min = bool(re.search(r"\bmin\b", code_l))

        if mx == mn:
            value = mx
            formula = f"所有数据都等于 {_fmt(mx)}"
        elif wants_max and not wants_min:
            value = mx
            formula = f"最大值 = {_fmt(mx)}{data_desc}"
        elif wants_min and not wants_max:
            value = mn
            formula = f"最小值 = {_fmt(mn)}{data_desc}"
        elif has_mx and not has_mn:
            # 代码里分不清，但输出只有一个值，按它来
            value = mx
            formula = f"最大值 = {_fmt(mx)}{data_desc}"
        elif has_mn and not has_mx:
            value = mn
            formula = f"最小值 = {_fmt(mn)}{data_desc}"
        else:
            # 实在无法区分求最大还是最小，两个都给
            value = [mx, mn]
            display_override = f"最大值 {_fmt(mx)} / 最小值 {_fmt(mn)}"
            formula = f"最大值 = {_fmt(mx)}，最小值 = {_fmt(mn)}{data_desc}"

    elif task == "sort":
        if not data:
            r.available = False
            r.verdict = "无法判断"
            r.note = "没有解析到参与排序的数据。"
            return r
        value = sorted(data)
        formula = f"把 {' '.join(_fmt(x) for x in data)} 从小到大排列 = {_fmt(value)}"

    elif task == "factorial":
        if not data:
            r.available = False
            r.verdict = "无法判断"
            r.note = "没有解析到要求阶乘的那个数。"
            return r
        n = int(data[0])
        if n < 0:
            r.available = False
            r.verdict = "无法判断"
            r.note = "负数没有阶乘，请确认输入。"
            return r
        if n > 20:
            r.available = False
            r.verdict = "无法判断"
            r.note = f"{n} 的阶乘超出了 64 位整数范围，普通类型装不下。"
            return r
        value = math.factorial(n)
        formula = f"{n}! = " + "×".join(str(i) for i in range(1, n + 1)) + f" = {value}"

    elif task == "prime":
        if not data:
            r.available = False
            r.verdict = "无法判断"
            r.note = "没有解析到要判断的数字。"
            return r
        n = int(data[0])
        value = _is_prime(n)
        formula = f"{n} {'是' if value else '不是'}素数"

    elif task == "count":
        if not data:
            r.available = False
            r.verdict = "无法判断"
            r.note = "没有解析到参与统计的数据。"
            return r
        value = len(data)
        formula = f"一共 {len(data)} 个数据"

    else:
        r.available = False
        r.verdict = "无法判断"
        r.note = "暂不支持自动推算这类题目的答案。"
        return r

    if value is None:
        r.available = False
        r.verdict = "无法判断"
        r.note = "没能算出标准答案。"
        return r

    r.available = True
    r.value = value
    r.display = display_override or _fmt(value)
    r.formula = formula

    matches, verdict = _compare(task, value, r.result_numbers, r.actual_numbers, r.actual)
    r.matches = matches
    r.verdict = verdict

    if matches is False:
        r.note = (
            "输出和标准答案对不上。可能的原因："
            "① 累加/计数的初始值写错；② 循环次数多算或少算了一次；"
            "③ 除法用错分母（比如用了数据个数以外的东西）；"
            "④ 输出格式问题（比如本该输出小数却用了整数除法取整）。"
        )
    else:
        r.note = NOTE_BASE

    return r
