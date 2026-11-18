"""
语义逻辑校验（Judge）：沙盒执行 -> 分类处理 -> 大模型裁判。

工作流（严格三步）：

  第一步  沙盒执行：无论有没有 intent，先把代码真实跑一遍，拿到 stdout / stderr。
  第二步  分类处理：
          · 有报错       -> 直接返回报错，辅导用户修语法/运行错误；
          · 无报错且无描述 -> 直接判定成功（不调用大模型）；
          · 无报错且有描述 -> 进入第三步。
  第三步  大模型裁判（LLM as a Judge）：
          把「代码 + 真实输出 + 用户描述」交给大模型做语义判断，
          例如描述"根据月份判断季节，输入6"，代码输出"春季" -> 6 月是夏季，判逻辑错误。

对外统一输出 JSON：
  {
    "is_syntax_correct": bool,
    "is_logic_correct": bool,
    "actual_output": str,       # 始终是沙盒里的真实输出，不采信大模型的转述
    "feedback": str
  }
另外附带少量排查字段（judge_source / llm_error / stdin_used 等），不影响上面的契约。

设计原则：
1. 语法/运行是否正确，只由沙盒结果决定，大模型无权推翻；
2. 「逻辑是否正确」只有一个来源：这一次独立的 LLM 调用（temperature=0）；
   代码层不猜业务对错，只用关键词做一致性兜底 —— reason 说「错」时 verdict
   不能是 correct，reason 说「一致」时 verdict 也不能是 wrong；
3. 没有配置 API Key 或调用失败时：
   · 默认用 MockJudgeClient 返回一份【明确标注的模拟结果】，证明裁判通路已打通；
   · 设置环境变量 JUDGE_MOCK=0 可关闭模拟，此时抛 JudgeUnavailable 如实报错。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import load_config
from .llm import LLMClient, LLMReply
from .runner import LANG_LABEL, CodeRunner, RunResult, detect_language


class JudgeUnavailable(RuntimeError):
    """大模型裁判没有真正执行（未配置 / 调用失败 / 返回无法解析）。"""

    def __init__(self, message: str, result: "JudgeResult | None" = None):
        super().__init__(message)
        self.result = result


# --------------------------------------------------------------------------- #
# 裁判 Prompt
# --------------------------------------------------------------------------- #
# 裁判固定用 temperature=0：同一输入 + 同一 Prompt 必须得到同一判定。
# 采样随机性是「同一段代码一会儿对一会儿错」的头号来源，这里不给它留口子。
JUDGE_TEMPERATURE = 0.0

JUDGE_SYSTEM_PROMPT = (
    "你是一个严格的代码逻辑审查员。你只输出一个 JSON 对象，不要 Markdown 代码块，"
    "不要任何解释性文字（不要在 JSON 前后写「好的」「以下是结论」之类的话）。\n"
    "\n"
    "【判定步骤，必须按顺序做】\n"
    "第 1 步：读【用户描述（意图）】：他想让这段代码做什么？输入是什么？\n"
    "第 2 步：在心里算出「正确输出应该是什么」，写在心里，不要输出。\n"
    "第 3 步：把【代码的真实运行输出】和「正确输出」逐项比对：数值、文字含义、顺序、"
    "格式，任何一项不同都算不符合。\n"
    "第 4 步：先写 reason（说清哪里对、哪里不对），再写 verdict。\n"
    "\n"
    "【不可违背的铁律】\n"
    "铁律 1：只要真实输出与意图不符（哪怕只错一个月份、一个季度、一个小数），"
    "verdict 必须是 wrong。\n"
    "铁律 2：verdict 只能取 correct 或 wrong 两个值，不能是空、不能是 true/false、"
    "不能是 unknown。\n"
    "铁律 3：reason 里出现「错误 / 不符 / 不一致 / 应为 / 有误 / 矛盾 / 不对」"
    "任一表述时，verdict 必须是 wrong —— 理由说了错、结论却写 correct 是绝对禁止的。\n"
    "铁律 4：只有你能明确说出「输出与意图一致」时才允许写 correct；"
    "拿不准时按 wrong 处理，不要因为「语法没报错」「代码写得像那么回事」就给 correct。\n"
    "\n"
    "【示例 1：差一个月也必须判 wrong】\n"
    "意图：根据月份判断季节，输入3；真实输出：夏季。\n"
    "推理：3 月属于春季，代码却输出夏季，与意图不符。\n"
    '正确输出：{"verdict": "wrong", "reason": "3 月属于春季，但程序输出夏季，季节判断有错误。"}\n'
    "\n"
    "【示例 2：完全一致才判 correct】\n"
    "意图：根据月份判断季节，输入6；真实输出：夏季。\n"
    "推理：6 月属于夏季，输出与意图一致。\n"
    '正确输出：{"verdict": "correct", "reason": "6 月属于夏季，输出与描述的预期一致。"}\n'
    "\n"
    "【示例 3：数值算错就是 wrong】\n"
    "意图：求三个数的平均值，输入 3 个数 1 2 3；真实输出：1.5。\n"
    "推理：正确平均值是 2.0，输出 1.5 与意图不符。\n"
    '正确输出：{"verdict": "wrong", "reason": "1、2、3 的平均值应为 2.0，程序输出 1.5，计算有误。"}\n'
    "\n"
    "【示例 4：最容易犯的错，务必看懂】\n"
    "假设你心里已经写下理由：「3 月属于春季，但程序输出夏季，与意图不符，存在逻辑错误。」\n"
    "这时 reason 里已经有「不符」「错误」，所以 verdict 只能写 wrong。\n"
    '正确输出：{"verdict": "wrong", "reason": "3 月属于春季，但程序输出夏季，与意图不符，存在逻辑错误。"}\n'
    '错误示范（绝对禁止）：{"verdict": "correct", "reason": "3 月属于春季，但程序输出夏季，存在逻辑错误。"}\n'
    "\n"
    "【输出格式】只输出形如 "
    '{"verdict": "correct 或 wrong", "reason": "一句话说明依据"}'
    " 的 JSON，不要输出其他键，不要输出 JSON 以外的任何字符。"
)

# 固定模板：只把 intent 与真实输出交给大模型，不附加任何本地规则或猜测
JUDGE_PROMPT_TEMPLATE = (
    "你是一个严格的代码逻辑审查员，按系统提示里的 4 条铁律判定。\n"
    "用户说这段代码的作用是：【{intent}】。"
    "代码的真实运行结果输出是：【{actual_output}】。"
    "请先自己算出正确输出，再与真实输出逐项比对，最后只输出一个 JSON 对象。\n"
    '如果符合意图（例如意图是求平均，输出等于正确平均值）：'
    '{"verdict": "correct", "reason": "说明为什么符合"}；\n'
    "如果不符合意图（例如根据月份 6 应该输出夏季，实际输出了春季；或数值、顺序算错）："
    '{"verdict": "wrong", "reason": "指出是哪一步不符合"}。\n'
    "只要 reason 里出现「错误 / 不符 / 不一致 / 应为 / 有误」等字眼，verdict 就必须是 wrong。"
)


def _call_judge(client: Any, prompt: str, system_prompt: str) -> LLMReply:
    """统一以 temperature=0 调裁判；老客户端不接受该参数时退回两参数调用。"""
    try:
        return client.chat(prompt, system_prompt, temperature=JUDGE_TEMPERATURE)
    except TypeError:
        return client.chat(prompt, system_prompt)


# --------------------------------------------------------------------------- #
# 结果结构
# --------------------------------------------------------------------------- #
@dataclass
class JudgeResult:
    # 统一契约的四个字段（顺序固定，便于前端直接读取）
    is_syntax_correct: bool = False
    is_logic_correct: bool = False
    actual_output: str = ""
    feedback: str = ""

    # 辅助排查字段
    intent: str = ""                # 本次裁判收到的用户描述
    verdict: str = ""               # correct / wrong / 空（未裁判）
    verdict_corrected: bool = False  # verdict 与 reason 矛盾，被代码强制纠正
    reason: str = ""                # 大模型给出的原始理由
    judge_temperature: float = JUDGE_TEMPERATURE  # 裁判调用使用的温度（固定 0）
    stage: str = ""
    language: str = ""
    language_label: str = ""
    stderr: str = ""
    intent_provided: bool = False
    judged: bool = False            # 裁判是否真的执行过（真实或 Mock）
    judge_source: str = ""          # sandbox / skipped / llm / mock
    llm_error: str = ""
    stdin_used: str = ""
    stdout_raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# 从描述里推断运行输入
# --------------------------------------------------------------------------- #
STDIN_HINT_RE = re.compile(r"输入\s*(?:是|为|[:：])?\s*([0-9][0-9\s.,、-]*)")
# 「输入 5 个数」说的是数量，不是数据
STDIN_COUNT_TAIL = ("个", "位", "次", "行")


def infer_stdin(intent: str) -> str:
    """用户常在描述里写「输入 6」，这里把它变成真正的 stdin。"""
    if not intent:
        return ""
    m = STDIN_HINT_RE.search(intent)
    if not m:
        return ""
    if intent[m.end():m.end() + 1] in STDIN_COUNT_TAIL:
        return ""
    parts = [p for p in re.split(r"[\s,，、;；]+", m.group(1)) if p]
    return "".join(f"{p}\n" for p in parts[:20])


# --------------------------------------------------------------------------- #
# JSON 解析与容错
# --------------------------------------------------------------------------- #
_BRACE_OBJECT_RE = re.compile(r"\{[^{}]*\}")


def parse_judge_json(text: str) -> dict[str, Any] | None:
    """从大模型回复里取出 JSON（兼容 ```json 包裹、前后废话、多个 JSON）。"""
    if not text:
        return None
    raw = text.strip()

    candidates = [m.group(1).strip() for m in re.finditer(r"```(?:json)?\s*(.*?)```", raw, re.S)]
    candidates.append(raw)
    if "{" in raw:
        candidates.append(raw[raw.find("{"): raw.rfind("}") + 1])
    candidates.extend(m.group(0) for m in _BRACE_OBJECT_RE.finditer(raw))

    for candidate in candidates:
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    return None


# --------------------------------------------------------------------------- #
# verdict 归一化：模型写 true / "Correct " / "correct。" / "正确" 都要认
# --------------------------------------------------------------------------- #
_VERDICT_PUNCT = " \t\r\n。.，,；;：:！!？?、\"'“”‘’（）()[]【】"

_TRUE_VERDICT = (
    "correct", "true", "yes", "ok", "pass", "passed", "right", "valid",
    "正确", "对的", "符合", "一致", "通过",
)
_FALSE_VERDICT = (
    "wrong", "false", "no", "fail", "failed", "incorrect", "invalid", "error",
    "错误", "不对", "不符", "不一致", "有误", "不通过", "错", "否",
)
# 包含关系判断时，否定词优先（否则 "incorrect" 会被当成 "correct"）
_FALSE_HINTS = (
    "incorrect", "invalid", "wrong", "fail", "false", "notcorrect",
    "错误", "不符", "不一致", "有误", "不对", "不通过", "错",
)
_TRUE_HINTS = ("correct", "true", "pass", "valid", "正确", "一致", "符合", "通过")

VERDICT_KEYS = ("verdict", "is_logic_correct", "logic_correct", "correct", "passed", "result")
REASON_KEYS = ("reason", "explanation", "detail", "why", "message", "analysis")


def normalize_bool(value: Any) -> bool | None:
    """把模型可能写出的各种「对 / 错」写法统一成 bool；认不出返回 None。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0

    text = str(value or "").strip(_VERDICT_PUNCT).lower().replace(" ", "")
    if not text:
        return None
    # 先整词匹配，避免 "incorrect" 被当成 "correct"
    if text in _TRUE_VERDICT:
        return True
    if text in _FALSE_VERDICT:
        return False
    # 整词匹配失败时按包含关系判断
    if any(word in text for word in _FALSE_HINTS):
        return False
    if any(word in text for word in _TRUE_HINTS):
        return True
    return None


def extract_verdict(payload: dict[str, Any]) -> bool | None:
    """取出「逻辑是否正确」，兼容 verdict / is_logic_correct / true / "correct。" 等写法。"""
    for key in VERDICT_KEYS:
        if key not in payload:
            continue
        got = normalize_bool(payload.get(key))
        if got is not None:
            return got
    return None


def extract_reason(payload: dict[str, Any]) -> str:
    for key in REASON_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


# --------------------------------------------------------------------------- #
# 理由一致性校验：reason 说「错」就不能是 correct，说「一致」也不能是 wrong
# --------------------------------------------------------------------------- #
# 理由里出现这些词，说明模型自己已经在理由里判了「错」
WRONG_WORDS = (
    "错误", "不符", "不符合", "不一致", "不正确", "应为", "应该是", "有误",
    "矛盾", "不对", "不匹配", "算错", "写错", "偏离", "报错", "崩溃", "越界",
)
# 理由里出现这些词，说明模型在理由里判了「对」
CORRECT_WORDS = (
    "正确", "一致", "符合", "无误", "没问题", "通过", "匹配", "相符",
)
# 只描述「逻辑」的错误词：被否定时（没有错误）反过来就是「判对」
LOGIC_ERROR_WORDS = ("错误", "不符", "不一致", "有误", "矛盾", "不对")
# 关键词前面出现这些否定词，就是「没有错误」这类否定句，不能算作判错
NEGATORS = (
    "没有", "没", "未", "不存在", "并无", "并非", "不是", "不属于",
    "不构成", "算不上", "谈不上", "不算", "无", "非",
)
# 逗号也切句：否则「程序未崩溃，输出正确」里的「未」会跨句把「正确」也否定掉
CLAUSE_SPLIT_RE = re.compile(r"[。；;！!？?，,\n]+")
NEGATION_WINDOW = 6


def _clauses(text: str) -> list[str]:
    return [c.strip() for c in CLAUSE_SPLIT_RE.split(text or "") if c.strip()]


def _has_marker(clause: str, markers: tuple[str, ...]) -> bool:
    """分句里是否出现了「没有被否定」的关键词。"""
    for marker in markers:
        start = 0
        while True:
            idx = clause.find(marker, start)
            if idx < 0:
                break
            before = clause[max(0, idx - NEGATION_WINDOW):idx]
            if not any(neg in before for neg in NEGATORS):
                return True
            start = idx + len(marker)
    return False


def _has_negated_marker(clause: str, markers: tuple[str, ...]) -> bool:
    """分句里是否出现了「被否定」的关键词（例如「没有错误」）。"""
    for marker in markers:
        start = 0
        while True:
            idx = clause.find(marker, start)
            if idx < 0:
                break
            before = clause[max(0, idx - NEGATION_WINDOW):idx]
            if any(neg in before for neg in NEGATORS):
                return True
            start = idx + len(marker)
    return False


def _reason_says_wrong(reason: str) -> bool:
    """理由是否明确说了「有错」（「没有逻辑错误」这类否定句不算）。"""
    return any(_has_marker(c, WRONG_WORDS) for c in _clauses(reason))


def _reason_says_correct(reason: str) -> bool:
    """理由是否明确说了「一致 / 正确」。

    只要有任何一句在说有错，就不认为整体在判对；
    「没有错误 / 不存在问题」这类否定句反过来算作判对。
    """
    clauses = _clauses(reason)
    if any(_has_marker(c, WRONG_WORDS) for c in clauses):
        return False
    if any(_has_marker(c, CORRECT_WORDS) for c in clauses):
        return True
    return any(_has_negated_marker(c, LOGIC_ERROR_WORDS) for c in clauses)


REASON_MAX_LEN = 400


def clip_reason(reason: str) -> str:
    """理由过长时截断，保留开头与结尾结论，避免前端刷屏。"""
    text = (reason or "").strip()
    if len(text) <= REASON_MAX_LEN:
        return text
    return f"{text[:260].rstrip()}…（理由已截断）…{text[-120:].lstrip()}"


SEASON_BY_MONTH = {
    1: "冬季", 2: "冬季", 3: "春季", 4: "春季", 5: "春季", 6: "夏季",
    7: "夏季", 8: "夏季", 9: "秋季", 10: "秋季", 11: "秋季", 12: "冬季",
}


class MockJudgeClient:
    """没有 API Key 时的替身裁判。

    只用来证明「沙盒执行 -> 拼 Prompt -> 解析 verdict -> 返回 JSON」这条链路是通的，
    返回内容一律带「Mock」前缀，绝不冒充真实大模型判断。
    """

    ready = True

    def chat(
        self, user_prompt: str, system_prompt: str = "", temperature: float | None = None,
    ) -> LLMReply:
        m_intent = re.search(r"作用是：【(.*?)】", user_prompt, re.S)
        m_out = re.search(r"输出是：【(.*?)】", user_prompt, re.S)
        intent = (m_intent.group(1) if m_intent else "").strip()
        output = (m_out.group(1) if m_out else "").strip()

        note = "（Mock 模拟裁判：当前环境未调用真实大模型 API）"
        verdict, reason = "correct", f"{note} 未发现明显矛盾；真实环境会由大模型逐条核对。"

        if "季节" in intent:
            for month, season in SEASON_BY_MONTH.items():
                if str(month) in intent:
                    if season in output:
                        reason = f"{note} {month} 月属于{season}，输出与描述一致。"
                    else:
                        verdict = "wrong"
                        reason = f"{note} {month} 月属于{season}，但程序输出「{output}」，判断条件有误。"
                    break

        return LLMReply(
            ok=True,
            text=json.dumps({"verdict": verdict, "reason": reason}, ensure_ascii=False),
        )


# --------------------------------------------------------------------------- #
# 报错反馈
# --------------------------------------------------------------------------- #
STAGE_HINT = {
    "compile_error": "代码在编译阶段就失败了，还没有真正跑起来。",
    "runtime_error": "代码运行到一半崩溃了。",
    "timeout": "程序运行超时（可能是死循环，或者在等输入）。",
    "env_error": "运行环境不完整，缺少编译器或解释器。",
}


def error_feedback(run: RunResult) -> str:
    stderr = run.stderr or ""
    hint = STAGE_HINT.get(run.stage, "代码没有成功运行。")
    # Python 的语法错误也表现为「退出码非 0」，但程序其实一行都没执行
    if re.search(r"\b(SyntaxError|IndentationError|TabError)\b", stderr):
        hint = "代码有语法错误，程序一行都没有执行。"
    detail = (stderr or run.stdout or "").strip()
    if len(detail) > 1200:
        detail = detail[:1200] + "\n...（报错已截断）"
    return (
        f"{hint}\n\n真实报错：\n{detail or '（没有拿到报错信息）'}\n\n"
        "请先按这条报错修好语法或运行问题；修好之后再检查逻辑是否符合你的描述。"
    )


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def evaluate(
    code: str,
    intent: str = "",
    language: str = "",
    stdin: str = "",
    *,
    client: Any | None = None,
    use_llm: bool = True,
    timeout: int = 10,
    filename: str = "",
) -> JudgeResult:
    """按三步工作流校验一段代码，返回 JudgeResult（to_dict() 即统一 JSON）。"""
    desc = (intent or "").strip()
    res = JudgeResult(intent_provided=bool(desc))

    if not (code or "").strip():
        res.is_syntax_correct = False
        res.is_logic_correct = False
        res.feedback = "没有收到代码，请先把代码粘贴进来。"
        res.judge_source = "sandbox"
        return res

    # 语言识别（auto 交给特征判断）
    lang = (language or "").lower()
    if lang in ("", "auto"):
        lang = detect_language(code, filename)
    if lang in ("c++", "cpp"):
        lang = "cpp"
    if lang not in ("c", "cpp", "python"):
        lang = detect_language(code, filename)

    res.intent = desc
    res.language = lang
    res.language_label = LANG_LABEL.get(lang, lang)

    # ---------------- 第一步：沙盒执行 ---------------- #
    # 描述里写了「输入 6」就把它当作运行输入，避免程序卡在 input() 上
    stdin_used = stdin or infer_stdin(desc)
    res.stdin_used = stdin_used

    try:
        runner = CodeRunner(timeout=max(1, min(int(timeout or 10), 60)))
        run = runner.run(code, lang, stdin=stdin_used, filename=filename)
    except Exception as exc:                                  # noqa: BLE001
        run = RunResult(
            language=lang, ok=False, stage="env_error",
            stderr=f"沙盒执行失败：{type(exc).__name__}: {exc}",
        )

    res.stage = run.stage
    res.stderr = run.stderr or ""
    res.actual_output = run.stdout or ""
    res.stdout_raw = run.to_dict()

    # ---------------- 第二步：分类处理 ---------------- #
    if not run.ok:
        res.is_syntax_correct = False
        res.is_logic_correct = False
        res.feedback = error_feedback(run)
        res.judge_source = "sandbox"
        return res

    res.is_syntax_correct = True

    if not desc:
        res.is_logic_correct = True
        res.feedback = (
            "程序运行成功，没有报错。你没有填写代码作用，"
            "本次只检查了语法与运行结果，未做语义逻辑判断。"
        )
        res.judge_source = "skipped"
        return res

    # ---------------- 第三步：大模型裁判（唯一判定来源） ---------------- #
    # 只要走到这里，就必须发起一次独立的大模型调用：
    # 固定模板只喂 [用户描述] + [真实输出]，没有任何本地规则参与判断。
    if not use_llm:
        raise JudgeUnavailable("已关闭大模型裁判，无法判断逻辑是否正确。", res)

    # 用 replace 而不是 format：模板里的 JSON 示例含有大括号。
    # 固定模板原样保留；前面补上代码与本次运行输入，方便模型结合场景判断
    # （否则它只看得到「判断季节」+「夏季」，不知道输入是 3 月）。
    prompt = (
        "【学生代码】\n```\n" + code.strip() + "\n```\n\n"
        "【本次运行输入】\n" + (stdin_used.strip() or "（无）") + "\n\n"
        + JUDGE_PROMPT_TEMPLATE.replace("{intent}", desc).replace(
            "{actual_output}", (run.stdout or "").strip() or "（无输出）",
        )
    )

    judge_client = client if client is not None else LLMClient(load_config())

    # 情况 A：确实没有 Key -> 用明确标注的 Mock 数据（可用 JUDGE_MOCK=0 关闭）
    if not getattr(judge_client, "ready", False):
        msg = "未配置大模型 API Key（可在网页「设置」或 config.json / 环境变量里配置）。"
        if os.environ.get("JUDGE_MOCK", "1") == "0":
            res.llm_error = msg
            raise JudgeUnavailable(msg, res)
        return _mock_result(res, prompt, msg)

    # 情况 B：有 Key 就必须真实调用。任何失败都把原始报错抛给接口层，
    #         不再用 Mock 兜底掩盖 401 / 连接失败 / 超时 等问题。
    try:
        reply = _call_judge(judge_client, prompt, JUDGE_SYSTEM_PROMPT)
    except Exception as exc:                                  # noqa: BLE001
        res.llm_error = f"{type(exc).__name__}: {exc}"
        raise JudgeUnavailable(f"调用大模型 API 失败：{res.llm_error}", res) from exc

    if not getattr(reply, "ok", False):
        res.llm_error = str(getattr(reply, "error", "未知错误"))
        raise JudgeUnavailable(f"大模型 API 返回错误：{res.llm_error}", res)

    raw_text = getattr(reply, "text", "") or ""
    payload = parse_judge_json(raw_text)
    if payload is not None:
        logic = extract_verdict(payload)
        reason = clip_reason(extract_reason(payload))
    else:
        # 模型没按格式给 JSON：先别判「未执行」，从纯文本里抽结论
        logic = None
        reason = clip_reason(raw_text)

    # verdict 字段缺失或不认识时，退回「按理由本身推断」，别让整次裁判失效
    if logic is None:
        says_wrong = _reason_says_wrong(reason)
        says_correct = _reason_says_correct(reason)
        if says_wrong and not says_correct:
            logic = False
        elif says_correct and not says_wrong:
            logic = True

    if logic is None:
        res.llm_error = f"大模型返回的内容里没有可识别的 verdict：{raw_text[:300]}"
        raise JudgeUnavailable("大模型返回的内容缺少可识别的 verdict 字段。", res)

    if not reason:
        reason = "逻辑正确。" if logic else "逻辑与你的描述不一致，请检查判断条件。"

    # 代码层兜底拦截：verdict 必须与 reason 的结论一致，不一致时以 reason 为准。
    # 这是最后一道闸门 —— 即使模型自相矛盾，界面也不会给出反的结论。
    says_wrong = _reason_says_wrong(reason)
    says_correct = _reason_says_correct(reason)
    if logic is True and says_wrong:
        logic = False
        res.verdict_corrected = True
        reason = f"{reason}（系统检测到理由与 verdict 不一致，已自动纠正为 wrong）"
    elif logic is False and says_correct and not says_wrong:
        logic = True
        res.verdict_corrected = True
        reason = f"{reason}（系统检测到理由与 verdict 不一致，已自动纠正为 correct）"

    res.verdict = "correct" if logic else "wrong"
    res.is_logic_correct = logic
    res.reason = reason
    res.feedback = reason
    res.judge_temperature = JUDGE_TEMPERATURE
    res.judged = True
    res.judge_source = "llm"
    return res


def _mock_result(res: JudgeResult, prompt: str, note: str) -> JudgeResult:
    """无 Key 时的 Mock 裁判：结果一律带 Mock 标注，绝不冒充真实判定。"""
    mock_reply = MockJudgeClient().chat(prompt, JUDGE_SYSTEM_PROMPT, temperature=JUDGE_TEMPERATURE)
    payload = parse_judge_json(mock_reply.text) or {}
    logic = extract_verdict(payload)
    if logic is None:
        logic = True
    reason = clip_reason(extract_reason(payload) or "（Mock）未发现明显逻辑问题。")

    # 与真实裁判同一条兜底规则：理由说了错，结论就不能是正确
    if logic is True and _reason_says_wrong(reason):
        logic = False
        reason = f"{reason}（系统检测到理由与 verdict 不一致，已自动纠正为 wrong）"

    res.verdict = "correct" if logic else "wrong"
    res.is_logic_correct = logic
    res.reason = reason
    res.feedback = reason
    res.judge_temperature = JUDGE_TEMPERATURE
    res.judged = True
    res.judge_source = "mock"
    res.llm_error = note
    return res
