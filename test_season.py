"""test_season.py —— 语义逻辑校验（沙盒执行 + 大模型裁判）测试。

覆盖两种情况：
1. 逻辑正确：输入 6 月 -> 输出「夏季」；
2. 逻辑错误：输入 6 月 -> 输出「春季」（6 月应是夏季）。

测试用 FakeJudgeClient 模拟大模型裁判，不依赖真实 API Key，
因此在离线环境也能直接跑：

    python tests/test_season.py

（如果装了 pytest，也可以 `pytest tests/test_season.py -q`。）
"""

from __future__ import annotations

import json
import random
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.judge import JudgeUnavailable, evaluate    # noqa: E402
from core.llm import LLMReply                        # noqa: E402


INTENT = "根据月份判断季节，输入6"

CORRECT_CODE = '''month = int(input("请输入月份："))
if month in (3, 4, 5):
    print("春季")
elif month in (6, 7, 8):
    print("夏季")
elif month in (9, 10, 11):
    print("秋季")
else:
    print("冬季")
'''

WRONG_CODE = '''month = int(input("请输入月份："))
if month in (6, 7, 8):
    print("春季")
else:
    print("其他")
'''

# 常识表：6 月属于夏季
SEASON = {
    1: "冬季", 2: "冬季", 3: "春季", 4: "春季", 5: "春季", 6: "夏季",
    7: "夏季", 8: "夏季", 9: "秋季", 10: "秋季", 11: "秋季", 12: "冬季",
}


class FakeJudgeClient:
    """模拟一个有常识的大模型裁判，只用于测试流程，不访问网络。"""

    ready = True

    def __init__(self) -> None:
        self.calls = 0
        self.last_prompt = ""

    def chat(self, user_prompt: str, system_prompt: str = "", temperature: float | None = None) -> LLMReply:
        self.calls += 1
        self.last_prompt = user_prompt

        # 固定模板：作用：【{intent}】 ... 输出是：【{actual_output}】
        m_intent = re.search(r"作用是：【(.*?)】", user_prompt, re.S)
        intent = m_intent.group(1).strip() if m_intent else ""
        m_out = re.search(r"输出是：【(.*?)】", user_prompt, re.S)
        output = m_out.group(1).strip() if m_out else ""

        m_month = re.search(r"输入\s*(\d+)", intent)
        month = int(m_month.group(1)) if m_month else 6
        expect = SEASON.get(month, "未知")
        logic_ok = expect in output

        payload = {
            "verdict": "correct" if logic_ok else "wrong",
            "reason": (
                f"{month} 月属于{expect}，程序输出「{output}」，逻辑正确。"
                if logic_ok else
                f"{month} 月属于{expect}，但程序输出「{output}」，"
                f"请检查月份到季节的判断条件。"
            ),
        }
        return LLMReply(ok=True, text=json.dumps(payload, ensure_ascii=False))


class NoKeyClient:
    """模拟「没有配置 API Key」的客户端。"""

    ready = False

    def chat(self, *args, **kwargs):
        raise AssertionError("没有 Key 时不应该调用真实客户端")


class FailingClient:
    """模拟「有 Key 但调用失败」（401 / 超时等）。"""

    ready = True

    def chat(self, *args, **kwargs):
        return LLMReply(ok=False, error="401 Unauthorized: invalid api key")


class ContradictoryClient:
    """模拟大模型「理由说有错、verdict 却写 correct」的自相矛盾输出。"""

    ready = True
    reason = (
        "输入3对应3月，属于春季，但代码将3、4、5月归为夏季，输出夏季，"
        "与用户意图不符，存在逻辑错误。"
    )

    def chat(self, *args, **kwargs):
        return LLMReply(
            ok=True,
            text=json.dumps({"verdict": "correct", "reason": self.reason}, ensure_ascii=False),
        )


class NegatedClient:
    """理由里出现「没有错误」时，不应该被误判成 wrong。"""

    ready = True

    def chat(self, *args, **kwargs):
        return LLMReply(
            ok=True,
            text=json.dumps(
                {"verdict": "correct", "reason": "没有逻辑错误，输出与描述一致。"},
                ensure_ascii=False,
            ),
        )


class FlakyClient:
    """模拟「时好时坏」的模型：每次调用随机换一种自相矛盾的写法。

    所有写法都有同一个特征：reason 明确说了「不符 / 错误 / 应为」，
    但 verdict 却（部分样本）写成 correct 甚至 true。代码层必须兜住。
    """

    ready = True

    VARIANTS = (
        {"verdict": "correct", "reason": "3 月属于春季，但程序输出夏季，与意图不符，存在逻辑错误。"},
        {"verdict": "Correct", "reason": "输入 3 应为春季，程序输出夏季，判断不符。"},
        {"verdict": "correct。", "reason": "程序输出夏季，与 3 月应有的春季不一致。"},
        {"verdict": True, "reason": "季节判断有误：3 月应为春季，实际输出夏季。"},
        {"is_logic_correct": True, "reason": "输出夏季，与「3 月为春季」不符。"},
        {"verdict": "wrong", "reason": "3 月属于春季，程序却输出夏季，判断条件写错了。"},
    )

    def __init__(self, seed: int = 0) -> None:
        self.rand = random.Random(seed)
        self.calls = 0
        self.temperatures: list[float | None] = []
        self.last_system_prompt = ""

    def chat(self, user_prompt: str, system_prompt: str = "", temperature: float | None = None) -> LLMReply:
        self.calls += 1
        self.temperatures.append(temperature)
        self.last_system_prompt = system_prompt
        payload = self.rand.choice(self.VARIANTS)
        return LLMReply(ok=True, text=json.dumps(payload, ensure_ascii=False))


class PlainTextClient:
    """模型完全不按 JSON 格式回答（只说了一句中文结论）。"""

    ready = True

    def chat(self, *args, **kwargs):
        return LLMReply(
            ok=True,
            text="推理：3 月属于春季，程序却输出夏季，与意图不符，因此判定为错误。",
        )


class ChineseVerdictClient:
    """verdict 用中文、且带标点。"""

    ready = True

    def chat(self, *args, **kwargs):
        return LLMReply(
            ok=True,
            text=json.dumps({"verdict": "正确。", "reason": "6 月属于夏季，输出与描述一致。"},
                            ensure_ascii=False),
        )


class ReverseContradictionClient:
    """反方向自相矛盾：理由说「完全一致」，verdict 却写 wrong。"""

    ready = True

    def chat(self, *args, **kwargs):
        return LLMReply(
            ok=True,
            text=json.dumps(
                {"verdict": "wrong", "reason": "6 月属于夏季，程序输出夏季，与描述完全一致，逻辑正确。"},
                ensure_ascii=False,
            ),
        )


class SeasonJudgeTest(unittest.TestCase):
    """三步工作流的端到端测试（沙盒真实运行 + 模拟裁判）。"""

    def test_logic_correct(self) -> None:
        client = FakeJudgeClient()
        result = evaluate(CORRECT_CODE, intent=INTENT, language="python", client=client)

        self.assertTrue(result.is_syntax_correct)
        self.assertTrue(result.is_logic_correct)
        self.assertIn("夏季", result.actual_output)
        self.assertTrue(result.judged)
        self.assertEqual(result.verdict, "correct")
        self.assertEqual(result.judge_source, "llm")
        self.assertEqual(client.calls, 1)
        # 描述里的「输入6」应被自动用作运行输入
        self.assertEqual(result.stdin_used, "6\n")

    def test_logic_wrong(self) -> None:
        client = FakeJudgeClient()
        result = evaluate(WRONG_CODE, intent=INTENT, language="python", client=client)

        self.assertTrue(result.is_syntax_correct)
        self.assertFalse(result.is_logic_correct)
        self.assertIn("春季", result.actual_output)
        self.assertIn("夏季", result.feedback)
        self.assertEqual(result.verdict, "wrong")
        self.assertEqual(result.judge_source, "llm")

        # 固定 Prompt：把代码、描述与真实输出原样交给大模型
        self.assertIn("【学生代码】", client.last_prompt)
        self.assertIn("你是一个严格的代码逻辑审查员", client.last_prompt)
        self.assertIn("【根据月份判断季节，输入6】", client.last_prompt)
        self.assertIn("【请输入月份：春季】", client.last_prompt)
        self.assertIn('"verdict"', client.last_prompt)

    def test_no_intent_skips_llm(self) -> None:
        client = FakeJudgeClient()
        result = evaluate("print(1)\n", language="python", client=client)

        self.assertTrue(result.is_syntax_correct)
        self.assertTrue(result.is_logic_correct)
        self.assertEqual(result.judge_source, "skipped")
        self.assertEqual(client.calls, 0)

    def test_syntax_error_skips_llm(self) -> None:
        client = FakeJudgeClient()
        result = evaluate("print(1\n", intent=INTENT, language="python", client=client)

        self.assertFalse(result.is_syntax_correct)
        self.assertFalse(result.is_logic_correct)
        self.assertTrue(result.feedback)
        self.assertEqual(client.calls, 0)

    def test_mock_fallback_without_key(self) -> None:
        """没有 API Key 时用 Mock 裁判跑通链路，并明确标注来源。"""
        result = evaluate(WRONG_CODE, intent=INTENT, language="python", client=NoKeyClient())

        self.assertTrue(result.judged)
        self.assertEqual(result.judge_source, "mock")
        self.assertEqual(result.verdict, "wrong")
        self.assertFalse(result.is_logic_correct)
        self.assertIn("Mock", result.feedback)
        self.assertTrue(result.llm_error)

    def test_real_api_error_is_not_masked(self) -> None:
        """有 Key 但调用失败时，必须把真实错误抛出来，不能偷偷走 Mock。"""
        with self.assertRaises(JudgeUnavailable) as ctx:
            evaluate(CORRECT_CODE, intent=INTENT, language="python", client=FailingClient())
        self.assertIn("401", str(ctx.exception))
        self.assertIn("401", ctx.exception.result.llm_error)

    def test_llm_is_mandatory(self) -> None:
        """有描述且无报错时，必须真正调用大模型；不调用就如实报错。"""
        with self.assertRaises(JudgeUnavailable) as ctx:
            evaluate(CORRECT_CODE, intent=INTENT, language="python", use_llm=False)
        self.assertIn("大模型", str(ctx.exception))
        # 异常里仍带上沙盒的真实输出，方便前端展示
        self.assertIn("夏季", ctx.exception.result.actual_output)

    def test_contradictory_verdict_is_corrected(self) -> None:
        """理由说「存在逻辑错误」但 verdict=correct 时，必须强制纠成 wrong。"""
        result = evaluate(
            CORRECT_CODE, intent=INTENT, language="python", client=ContradictoryClient(),
        )
        self.assertFalse(result.is_logic_correct)
        self.assertEqual(result.verdict, "wrong")
        self.assertTrue(result.verdict_corrected)
        self.assertIn("存在逻辑错误", result.feedback)
        self.assertIn("已自动纠正", result.feedback)

    def test_negated_wrong_words_not_flipped(self) -> None:
        """「没有逻辑错误」不能被误伤成 wrong。"""
        result = evaluate(
            CORRECT_CODE, intent=INTENT, language="python", client=NegatedClient(),
        )
        self.assertTrue(result.is_logic_correct)
        self.assertEqual(result.verdict, "correct")
        self.assertFalse(result.verdict_corrected)

    def test_repeated_runs_stable_over_flaky_llm(self) -> None:
        """同一段错误代码 + 10 次抖动的模型输出，结论必须每次都稳定是 wrong。"""
        for i in range(10):
            client = FlakyClient(seed=i)
            result = evaluate(WRONG_CODE, intent=INTENT, language="python", client=client)

            self.assertEqual(client.calls, 1)
            self.assertEqual(result.judge_temperature, 0, f"第 {i + 1} 次裁判温度不是 0")
            self.assertEqual(client.temperatures, [0], f"第 {i + 1} 次调用没有传 temperature=0")
            self.assertFalse(
                result.is_logic_correct,
                f"第 {i + 1} 次把错误代码判成了正确：{result.reason}",
            )
            self.assertEqual(result.verdict, "wrong", f"第 {i + 1} 次 verdict 不稳定")

    def test_prompt_has_strict_few_shot(self) -> None:
        """系统 Prompt 必须带 4 条铁律与 4 个示例，并且裁判温度固定为 0。"""
        client = FlakyClient(seed=0)
        result = evaluate(CORRECT_CODE, intent=INTENT, language="python", client=client)

        self.assertEqual(result.judge_temperature, 0)
        self.assertEqual(client.temperatures, [0])
        for needle in ("铁律 1", "铁律 4", "示例 1", "示例 4", "错误示范"):
            self.assertIn(needle, client.last_system_prompt)

    def test_plain_text_reply_still_judged(self) -> None:
        """模型没给 JSON 时，从纯文本理由里抽结论，不能直接判「裁判未执行」。"""
        result = evaluate(WRONG_CODE, intent=INTENT, language="python", client=PlainTextClient())

        self.assertTrue(result.judged)
        self.assertFalse(result.is_logic_correct)
        self.assertEqual(result.verdict, "wrong")
        self.assertIn("不符", result.reason)

    def test_chinese_verdict_value_is_normalized(self) -> None:
        """verdict 写成「正确。」也要能识别成 correct。"""
        result = evaluate(CORRECT_CODE, intent=INTENT, language="python", client=ChineseVerdictClient())

        self.assertTrue(result.is_logic_correct)
        self.assertEqual(result.verdict, "correct")
        self.assertFalse(result.verdict_corrected)

    def test_reverse_contradiction_is_corrected(self) -> None:
        """理由说「完全一致」但 verdict=wrong 时，也要纠正成 correct。"""
        result = evaluate(
            CORRECT_CODE, intent=INTENT, language="python", client=ReverseContradictionClient(),
        )

        self.assertTrue(result.is_logic_correct)
        self.assertEqual(result.verdict, "correct")
        self.assertTrue(result.verdict_corrected)
        self.assertIn("已自动纠正", result.feedback)

    def test_json_contract(self) -> None:
        result = evaluate(CORRECT_CODE, intent=INTENT, language="python", client=FakeJudgeClient())
        data = result.to_dict()

        for key in ("is_syntax_correct", "is_logic_correct", "actual_output", "feedback"):
            self.assertIn(key, data)
        self.assertIsInstance(data["is_syntax_correct"], bool)
        self.assertIsInstance(data["is_logic_correct"], bool)
        self.assertIsInstance(data["actual_output"], str)
        self.assertIsInstance(data["feedback"], str)
        # 必须能直接序列化成 JSON 返回给前端
        json.dumps(data, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
