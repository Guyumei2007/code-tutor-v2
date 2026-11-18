"""
代码意图解析：把用户用大白话写的"这段代码要做什么"，转成可判断的规格。

为什么值得单独做一个模块：
自动识别（看代码关键词猜题型）一定会错，尤其是新手代码本身就写错的时候
—— 用错代码去猜"他想干什么"，等于缘木求鱼。
而用户自己写的一句描述，是最可靠的意图来源。

所以优先级是：用户描述 > 代码特征推断。

它做三件事：
1. 从描述里认出题型（求平均/求和/排序/阶乘/素数/最大最小/计数）。
2. 检查代码里有没有实现这个题型必需的操作（比如"求平均"至少得有除法），
   缺了就提示"代码里没看到 XX，可能没真正做到"。
3. 提取描述里的输出格式要求（保留几位小数、每行一个等）作为提醒。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any


# --------------------------------------------------------------------------- #
# 题型识别
# --------------------------------------------------------------------------- #
# 顺序很重要：越具体的越靠前，避免"输入 n 个数"被误判成"统计个数"
TASK_PATTERNS: list[tuple[str, str, list[str]]] = [
    ("factorial", "求阶乘", [r"阶乘", r"factorial"]),
    ("prime", "判断素数", [r"素数", r"质数", r"prime"]),
    ("average", "求平均值", [r"平均", r"均值", r"\bavg\b", r"\baverage\b", r"\bmean\b"]),
    ("sort", "排序", [r"排序", r"从小到大排", r"从大到小排", r"升序", r"降序", r"排好序", r"按顺序输出"]),
    ("max", "求最大值", [r"最大值", r"最高的", r"最多的是", r"求出最大", r"最大的是", r"\bmax\b"]),
    ("min", "求最小值", [r"最小值", r"最低的", r"最少的是", r"求出最小", r"最小的是", r"\bmin\b"]),
    ("sum", "求和", [r"总和", r"求和", r"之和", r"加起来", r"累加", r"加起来是多少"]),
    ("count", "统计个数", [r"统计.{0,6}个数", r"有多少个", r"计数", r"个数是", r"个数为", r"统计出现次数"]),
]

# 每个题型必须出现的代码特征（每组至少命中一个）
# 用途：描述说"求平均"，代码里却没有任何除法 → 大概率没做到
REQUIRED_FEATURES: dict[str, list[tuple[str, str]]] = {
    "average": [
        (r"[/÷]", "除法运算"),
        (r"\bsum\s*\(|total|\+=", "累加求和"),
    ],
    "sum": [
        (r"\+[^=]|\+=|\bsum\s*\(", "加法累加"),
    ],
    "sort": [
        (r"\.sort\s*\(|\bsorted\s*\(|\bqsort\b|Arrays\.sort|for\s*\([^)]*\)\s*\{?[^}]{0,300}for\s*\(",
         "排序逻辑（排序函数或双重循环）"),
    ],
    "max": [
        (r"\bmax\b|>|>=", "比较大小"),
    ],
    "min": [
        (r"\bmin\b|<|<=", "比较大小"),
    ],
    "factorial": [
        (r"\*=|factorial|multiply|\*", "乘法累乘"),
    ],
    "prime": [
        (r"%|mod|isqrt|sqrt", "取余或开方判断"),
    ],
    "count": [
        (r"count|\+=|len\s*\(", "计数累加"),
    ],
}

# 意图里的题型名 -> 边界测试/标准答案模块使用的题型名
# （max/min 在那边合并成一个 maxmin）
PIPELINE_TASK: dict[str, str] = {
    "max": "maxmin",
    "min": "maxmin",
}

# 描述里的输出格式要求
FORMAT_RULES: list[tuple[str, str, str]] = [
    (r"保留\s*(\d+)\s*位小数|(\d+)\s*位小数", "decimals", "保留小数位"),
    (r"输出整数|不要小数|取整", "integer", "输出整数"),
    (r"空格(?:分隔|隔开)|用空格", "space", "空格分隔"),
    (r"每行(?:一个|输出)|一行一个", "per_line", "每行一个"),
]

# 描述里直接写出的「期望输出」。
# 之所以要单独抓：程序可能不读输入（数据写死），或者用户没填运行输入，
# 这时靠算法推算标准答案会失败，但用户自己在描述里已经给了答案。
EXPECT_PATTERNS: list[str] = [
    r"(?:期望|预期|正确)(?:的)?(?:输出|结果|答案)\s*(?:应该|应当)?\s*(?:是|为|[:：])?\s*(?P<v>[^。！？\n，,；;]{1,40})",
    r"(?:输出|打印|返回|结果|答案)(?:应该|应当|必须|需要|要|应)\s*(?:是|为|[:：])\s*(?P<v>[^。！？\n，,；;]{1,40})",
    r"(?:输出|打印)(?:结果)?\s*[:：]\s*(?P<v>[^。！？\n，,；;]{1,40})",
]

# 抓到这些词说明是「格式要求」而不是「具体答案」
EXPECT_BAD_WORDS = (
    "整数", "小数", "保留", "格式", "空格", "每行", "一行", "个数",
    "位数", "换行", "分隔", "平均值", "总和",
)

QUOTE_CHARS = "\"'“”‘’「」"


@dataclass
class Intent:
    provided: bool = False
    description: str = ""
    task: str = ""                  # 识别出的题型；空串表示没识别出来
    task_label: str = ""
    confidence: str = "low"
    missing_features: list[str] = field(default_factory=list)
    found_features: list[str] = field(default_factory=list)
    fits: bool | None = None        # 代码是否具备实现该题型的必要操作
    formats: list[str] = field(default_factory=list)   # 描述里的格式要求
    # 描述里写明的期望输出（例如「期望输出是 2.00」「输出：1 2 3」）
    expected_output: str = ""
    expected_numbers: list[float] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize(text: str) -> str:
    return (text or "").strip()


def _extract_expected(desc: str) -> tuple[str, list[float]]:
    """从描述里抽出用户写明的期望输出。抽不准就返回空，宁可不判断。"""
    for pattern in EXPECT_PATTERNS:
        m = re.search(pattern, desc)
        if not m:
            continue

        raw = (m.group("v") or "").strip()
        quoted = bool(raw) and raw[0] in QUOTE_CHARS
        raw = raw.strip(QUOTE_CHARS).strip()
        raw = re.sub(r"^(?:结果|答案)\s*(?:是|为|[:：])?\s*", "", raw)
        raw = raw.strip(" ，,、；;:：")
        if not raw:
            continue
        if any(bad in raw for bad in EXPECT_BAD_WORDS):
            continue

        has_num = bool(re.search(r"\d", raw))
        ascii_text = all(ord(c) < 128 for c in raw)
        # 只接受三种能比对的东西：数字、被引号括起来的文本、纯英文短语
        if not (has_num or quoted or (ascii_text and len(raw) <= 20)):
            continue

        nums = [float(x) for x in _numbers(raw)]
        return raw, nums

    return "", []


def _match_expected(want: str, want_nums: list[float], actual: str) -> bool | None:
    """把程序实际输出和描述里的期望输出比对。None 表示没法比。"""
    got_text = normalize(actual)
    if not got_text:
        return False

    if want_nums:
        got = [float(x) for x in _numbers(got_text)]
        if not got:
            return False
        n = len(want_nums)
        for start in range(len(got) - n + 1):
            if all(
                abs(got[start + i] - want_nums[i]) <= 1e-9 * max(1.0, abs(want_nums[i]))
                for i in range(n)
            ):
                return True
        return False

    w = normalize(want).lower()
    if not w:
        return None
    return w in got_text.lower()


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def analyze(description: str, code: str, language: str) -> Intent:
    """解析用户描述，并对照代码检查。"""
    desc = normalize(description)
    it = Intent(provided=bool(desc), description=desc)

    if not desc:
        return it

    lowered = desc.lower()

    # 1) 认题型
    for task, label, pats in TASK_PATTERNS:
        hits = sum(1 for p in pats if re.search(p, lowered))
        if hits >= 1:
            it.task = task
            it.task_label = label
            it.confidence = "high" if hits >= 2 else "medium"
            break

    # 2) 提取格式要求
    for pattern, kind, label in FORMAT_RULES:
        m = re.search(pattern, desc)
        if not m:
            continue
        if kind == "decimals":
            digits = next((g for g in m.groups() if g), "2")
            it.formats.append(f"保留 {digits} 位小数")
        else:
            it.formats.append(label)

    # 3) 描述里写明的期望输出（没有输入数据时靠它核对）
    it.expected_output, it.expected_numbers = _extract_expected(desc)

    if not it.task:
        if it.expected_output:
            it.note = (
                f"描述里没识别出具体题型，但写明了期望输出「{it.expected_output}」，"
                "可以据此核对。"
            )
        else:
            it.note = "这句描述里没能识别出具体题型（求平均/求和/排序等），将以代码本身为准。"
        return it

    # 4) 对照代码检查是否具备必要操作
    required = REQUIRED_FEATURES.get(it.task, [])
    if required:
        missing: list[str] = []
        found: list[str] = []
        for pattern, label in required:
            if re.search(pattern, code or ""):
                found.append(label)
            else:
                missing.append(label)
        it.found_features = found
        it.missing_features = missing
        it.fits = not missing

        if missing:
            it.note = (
                f"你说这段代码是「{it.task_label}」，但代码里没有看到"
                f"「{'、'.join(missing)}」。请确认它是否真的做到了这件事。"
            )
        else:
            it.note = f"代码里能看到实现「{it.task_label}」所需的操作。"

    if it.formats:
        extra = "、".join(it.formats)
        if it.note:
            it.note = f"{it.note}另外，描述里还要求{extra}。"
        else:
            it.note = f"描述里还要求{extra}。"

    return it


def merge_task(intent: Intent, auto_task: str, auto_label: str) -> tuple[str, str]:
    """决定最终采用哪个题型：用户描述优先，其次代码推断。

    返回的 task 已转换成下游（boundary / expected）认识的名字。
    """
    if intent.task:
        return PIPELINE_TASK.get(intent.task, intent.task), intent.task_label
    return auto_task, auto_label


# --------------------------------------------------------------------------- #
# 按描述判定「这段代码对不对」
# --------------------------------------------------------------------------- #
@dataclass
class IntentCheck:
    """针对用户描述给出的结论，而不是泛泛的「代码有没有报错」。"""
    checked: bool = False
    verdict: str = "unknown"        # correct / wrong / unknown
    label: str = "未做描述核对"
    short: str = ""                 # 给结论栏用的一句话
    detail: str = ""                # 给 verdict 用的一句话
    reasons: list[str] = field(default_factory=list)
    format_issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


NUM_RE = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")


def _numbers(text: str) -> list[str]:
    return NUM_RE.findall(text or "")


def _check_formats(formats: list[str], stdout: str) -> list[str]:
    """检查输出格式是否满足描述里的要求。只报能确定的，猜不出的不报。"""
    issues: list[str] = []
    if not formats:
        return issues

    nums = _numbers(stdout)
    if not nums:
        return issues
    last = nums[-1]

    for fmt in formats:
        m = re.search(r"保留\s*(\d+)\s*位小数", fmt)
        if m:
            want = int(m.group(1))
            frac = last.split(".")[1] if "." in last else ""
            if len(frac) != want:
                issues.append(f"要求保留 {want} 位小数，但最后输出的是「{last}」")
            continue
        if fmt == "输出整数":
            if "." in last or "e" in last.lower():
                issues.append(f"要求输出整数，但最后输出的是「{last}」")
            continue
        # 「空格分隔」「每行一个」在输出可能夹杂提示文字时无法可靠判断，不报。
    return issues


def judge(
    intent: Intent,
    *,
    run_ok: bool,
    expected: dict | None = None,
    code_task: str = "",
    code_label: str = "",
    actual_output: str = "",
) -> IntentCheck:
    """综合「描述 + 静态特征 + 真实输出」判断代码是否符合用户描述。

    这是"用户说这段代码是求平均，那它到底是不是"的最终回答：
    - wrong：缺关键操作 / 结果与标准答案不符 / 格式不满足描述；
    - correct：关键操作齐全且输出与标准答案一致；
    - unknown：没填描述、代码没跑通、或缺少可推算的条件。
    """
    chk = IntentCheck()

    if not intent.provided:
        chk.label = "未填代码作用，未做描述核对"
        return chk

    chk.checked = True

    if not run_ok:
        chk.verdict = "unknown"
        chk.label = "❔ 代码还没跑通，暂不判断是否符合描述"
        chk.short = "代码未跑通，无法按描述判断"
        chk.reasons.append("先把运行报错解决，才能判断它是否真的做到了你描述的事。")
        return chk

    # 没写题型、也没写期望输出 → 没有可判断的依据
    if not intent.task and not intent.expected_output:
        chk.verdict = "unknown"
        chk.label = "❔ 描述里没能识别出具体题型"
        chk.short = "描述里没识别出题型，无法按描述判断"
        chk.reasons.append("换一种说法试试，比如「求平均值」「求和」「排序」；"
                           "或者直接写明期望输出，例如「期望输出是 2.00」。")
        return chk

    exp = expected or {}
    reasons: list[str] = []
    fmt_issues = _check_formats(intent.formats, actual_output)
    wrong = False
    subject = intent.task_label or f"输出「{intent.expected_output}」"

    # 1) 代码里有没有实现该题型的必要操作（没写题型就不查）
    if intent.task and intent.fits is True:
        found = "、".join(intent.found_features) or "关键操作"
        reasons.append(f"代码里能看到实现「{intent.task_label}」所需的操作（{found}）。")
    elif intent.task and intent.fits is False:
        wrong = True
        missing = "、".join(intent.missing_features) or "关键步骤"
        reasons.append(f"代码里没有看到「{missing}」，而实现「{intent.task_label}」通常离不开它。")

    # 2) 用户说的题型 vs 从代码写法推断的题型（不一致时只提示，不直接判错）
    if intent.task:
        mapped = PIPELINE_TASK.get(intent.task, intent.task)
        if code_task and code_task != "generic" and code_task != mapped:
            reasons.append(
                f"从写法上看，代码更像是在「{code_label}」，和你说的「{intent.task_label}」不一致。"
            )

    # 3) 输出核对：描述里写明了期望输出就用它，否则用算法推算的标准答案
    actual_all = (actual_output or exp.get("actual") or "").strip()
    actual_show = actual_all[:60] or "空"

    desc_match: bool | None = None
    if intent.expected_output:
        desc_match = _match_expected(
            intent.expected_output, intent.expected_numbers, actual_all,
        )
        if desc_match is True:
            reasons.append(
                f"描述里写的期望输出是「{intent.expected_output}」，"
                f"程序实际输出「{actual_show}」，一致。"
            )
        elif desc_match is False:
            wrong = True
            reasons.append(
                f"描述里写的期望输出是「{intent.expected_output}」，"
                f"但程序实际输出「{actual_show}」。"
            )

    numeric_match: bool | None = None
    if exp.get("available"):
        numeric_match = exp.get("matches")
        if numeric_match is True:
            reasons.append(f"输出与按「{intent.task_label}」算出的标准答案一致（{exp.get('display')}）。")
        elif numeric_match is False and desc_match is None:
            wrong = True
            reasons.append(
                f"你的程序输出「{actual_show}」，"
                f"而按「{intent.task_label}」应该是 {exp.get('display')}。"
            )

    # 4) 输出格式是否满足描述（数值本身就不对时不再重复报格式，避免干扰主因）
    if numeric_match is False or desc_match is False:
        fmt_issues = []
    if fmt_issues:
        wrong = True
        reasons.extend(fmt_issues)

    chk.format_issues = fmt_issues
    chk.reasons = reasons

    if wrong:
        chk.verdict = "wrong"
        chk.label = f"❌ 不符合你的描述：{subject}"
        chk.short = f"不符合你的描述：{subject}"
        if desc_match is False:
            chk.detail = (
                f"代码能跑通，但输出和你描述的期望不一致 —— "
                f"你期望 {intent.expected_output}，实际输出 {actual_show}。"
            )
        elif numeric_match is False:
            chk.detail = (
                f"代码能跑通，但按「{intent.task_label}」核对结果不对 —— "
                f"应为 {exp.get('display')}，实际输出 {actual_show}。"
            )
        elif fmt_issues:
            chk.detail = f"代码能跑通，数值也一致，但输出格式没满足描述：{fmt_issues[0]}。"
        elif desc_match is True:
            chk.detail = (
                f"输出虽然和你描述的期望一致，但代码里没看到实现"
                f"「{intent.task_label}」的必要操作，可能是把答案写死了。"
            )
        else:
            chk.detail = f"代码能跑通，但看不出它实现了你说的「{intent.task_label}」。"
    elif desc_match is True or (
        desc_match is None and numeric_match is True
        and intent.task and intent.fits is not False
    ):
        chk.verdict = "correct"
        chk.label = f"✅ 符合你的描述：{subject}"
        chk.short = f"符合你的描述：{subject}"
        chk.detail = (
            f"按你说的核对：程序输出与期望一致（{actual_show}）。"
            if desc_match is True
            else f"按你说的「{intent.task_label}」核对：输出与标准答案一致。"
        )
    else:
        chk.verdict = "unknown"
        chk.label = "❔ 已按描述核对，但无法自动判断"
        chk.short = "无法自动判断是否符合描述"
        if intent.expected_output and desc_match is None:
            reasons.append("描述里的期望输出没能和程序输出对上号。")
        elif not exp.get("available"):
            reasons.append(
                "缺少输入数据或无法推算标准答案。可以在描述里写明期望输出，"
                "例如「期望输出是 2.00」，就能直接核对。"
            )
        else:
            reasons.append("输出里没有能和期望比对的内容，需要你自己确认结果。")
        chk.reasons = reasons

    return chk
