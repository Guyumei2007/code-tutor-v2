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
2. 「逻辑是否正确」只有一个来源：这一次独立的 LLM 调用。
   底层不写任何 if-else / 关键词规则去猜对错；
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
JUDGE_SYSTEM_PROMPT = (
    "你是一个严格的代码逻辑审查员，只输出 JSON，不要 Markdown、不要多余解释。\n"
    "铁律：如果代码的实际输出与用户的描述（意图）不符，verdict 字段必须且只能输出 wrong！"
    "绝不能输出 correct。请仔细检查你的推理和 JSON 字段是否一致。\n"
    "示例 1（Few-shot，必须照此判定）：用户描述是「根据月份判断季节，输入6」，"
    "代码真实输出是「春季」。6 月属于夏季，输出与意图不符，因此必须返回："
    '{"verdict": "wrong", "reason": "6 月属于夏季，但程序输出春季，存在逻辑错误。"}\n'
    "示例 2：用户描述是「求平均值」，代码输出恰好等于正确平均值，与意图一致，返回："
    '{"verdict": "correct", "reason": "输出与描述的预期一致。"}\n'
    "再次强调：verdict 必须与你 reason 中的结论一致；reason 里说存在错误/不符，"
    "verdict 就必须是 wrong。"
)

# 固定模板：只把 intent 与真实输出交给大模型，不附加任何本地规则或猜测
JUDGE_PROMPT_TEMPLATE = (
    "你是一个严格的代码逻辑审查员。用户说这段代码的作用是：【{intent}】。"
    "代码的真实运行结果输出是：【{actual_output}】。"
    "请判断这个输出结果是否符合用户的预期意图？"
    '如果符合（比如意图是求平均，输出是正确数值），返回 '
    '{"verdict": "correct", "reason": "解释原因"}；'
    "如果不符合（比如根据月份6应该输出夏季，但实际输出了春季），返回 "
    '{"verdict": "wrong", "reason": "指出逻辑错误"}。只输出 JSON。'
)


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
    verdict_corrected: bool = False  # verdict 与 reason 矛盾，被代码强制纠正为 wrong
    reason: str = ""                # 大模型给出的原始理由
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
def parse_judge_json(text: str) -> dict[str, Any] | None:
    """从大模型回复里取出 JSON（兼容 ```json 包裹和前后废话）。"""
    if not text:
        return None
    raw = text.strip()

    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if fence:
        raw = fence.group(1).strip()

    for candidate in (raw, raw[raw.find("{"): raw.rfind("}") + 1] if "{" in raw else ""):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    return None


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
        reply = judge_client.chat(prompt, JUDGE_SYSTEM_PROMPT, temperature=0)
    except Exception as exc:                                  # noqa: BLE001
        res.llm_error = f"{type(exc).__name__}: {exc}"
        raise JudgeUnavailable(f"调用大模型 API 失败：{res.llm_error}", res) from exc

    if not getattr(reply, "ok", False):
        res.llm_error = str(getattr(reply, "error", "未知错误"))
        raise JudgeUnavailable(f"大模型 API 返回错误：{res.llm_error}", res)

    payload = parse_judge_json(getattr(reply, "text", ""))
    if payload is None:
        res.llm_error = f"大模型返回的不是合法 JSON：{(getattr(reply, 'text', '') or '')[:300]}"
        raise JudgeUnavailable("大模型返回的不是合法 JSON。", res)

    verdict = str(payload.get("verdict") or "").strip().lower()
    logic = {"correct": True, "wrong": False}.get(verdict)
    if logic is None:
        res.llm_error = f"大模型返回 JSON 缺少合法 verdict：{str(payload)[:300]}"
        raise JudgeUnavailable("大模型返回的 JSON 缺少合法的 verdict 字段。", res)

    reason = str(payload.get("reason") or "").strip()
    if not reason:
        reason = "逻辑正确。" if logic else "逻辑与你的描述不一致，请检查判断条件。"

    # 防御性纠正：reason 说「错误 / 不符 / 应为 …」但 verdict 却是 correct，
    # 说明模型自相矛盾，强制按 reason 纠正为 wrong，避免界面显示错误结论。
    if logic is True and _reason_says_wrong(reason):
        verdict = "wrong"
        logic = False
        res.verdict_corrected = True
        reason = f"{reason}（系统检测到理由与 verdict 不一致，已自动纠正为 wrong）"

    res.verdict = verdict
    res.is_logic_correct = logic
    res.reason = reason
    res.feedback = reason
    res.judged = True
    res.judge_source = "llm"
    return res


# reason 里出现这些词，说明模型自己在理由里已经判了「错」
WRONG_WORDS = ("错误", "不符", "不一致", "应为", "有误", "矛盾", "不对")
# 但「没有逻辑错误 / 未发现错误」这种否定句不能算
NEGATED_WRONG_RE = re.compile(
    r"(?:没有|不存在|未发现|并无|无)(?:任何|明显|重大|逻辑|语法)?的?(?:错误|问题)"
)


def _reason_says_wrong(reason: str) -> bool:
    """大模型的 reason 是否明确说了「有错」。"""
    if not reason:
        return False
    text = NEGATED_WRONG_RE.sub("", reason)
    return any(word in text for word in WRONG_WORDS)


def _mock_result(res: JudgeResult, prompt: str, note: str) -> JudgeResult:
    """无 Key 时的 Mock 裁判：结果一律带 Mock 标注，绝不冒充真实判定。"""
    mock_reply = MockJudgeClient().chat(prompt, JUDGE_SYSTEM_PROMPT, temperature=0)
    payload = parse_judge_json(mock_reply.text) or {}
    verdict = "correct" if payload.get("verdict") == "correct" else "wrong"
    reason = str(payload.get("reason") or "（Mock）未发现明显逻辑问题。")

    res.verdict = verdict
    res.is_logic_correct = verdict == "correct"
    res.reason = reason
    res.feedback = reason
    res.judged = True
    res.judge_source = "mock"
    res.llm_error = note
    return res
