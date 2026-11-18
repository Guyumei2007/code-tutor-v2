"""
诊断大脑：把「真正运行」+「静态预检」+「报错解析」+「知识库」+「大模型」串成一条流水线。

核心原则（这是本项目和普通聊天机器人的本质区别）：
  报错必须以【真实运行结果】为准，绝不凭空猜测；
  大模型给出的修复代码必须【重新跑一次】验证，跑通了才交还给用户。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

from . import boundary, expected, intent, knowledge, parser, static_check
from .config import LLMConfig, load_config
from .llm import LLMClient
from .runner import CodeRunner, RunResult, detect_language, detect_toolchain, LANG_LABEL


@dataclass
class FixAttempt:
    """一次「AI 修复 -> 重新运行」的尝试记录。"""
    round: int
    code: str
    run: dict
    ok: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Diagnosis:
    language: str = ""
    language_label: str = ""
    code: str = ""
    stdin: str = ""
    static_issues: list = field(default_factory=list)
    first_run: dict = field(default_factory=dict)
    parsed: dict = field(default_factory=dict)
    knowledge_hit: dict | None = None
    explanation: str = ""
    llm_used: bool = False
    llm_error: str = ""
    fixed_code: str = ""
    fixed_run: dict | None = None
    fixed_ok: bool = False
    attempts: list = field(default_factory=list)
    env_hint: str = ""
    verdict: str = ""       # 一句话结论
    # 边界测试：主流程跑通后，自动造极端数据再跑一遍
    boundary: dict = field(default_factory=dict)
    boundary_failed: int = 0
    # 正确输出核对：用独立参考实现算出标准答案
    expected: dict = field(default_factory=dict)
    # 用户填写的「代码作用」解析结果（描述优先于代码推断）
    intent: dict = field(default_factory=dict)
    # 按用户描述给出的结论：correct / wrong / unknown
    intent_check: dict = field(default_factory=dict)
    # 描述核对没通过（缺必要操作 / 结果不符 / 格式不符）
    intent_mismatch: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class Diagnoser:
    def __init__(self, cfg: LLMConfig | None = None, timeout: int = 10, max_fix_rounds: int = 2):
        self.cfg = cfg or load_config()
        self.llm = LLMClient(self.cfg)
        self.runner = CodeRunner(timeout=timeout)
        self.max_fix_rounds = max_fix_rounds

    # ------------------------------------------------------------------ #
    def diagnose(
        self,
        code: str,
        language: str = "",
        stdin: str = "",
        filename: str = "",
        use_llm: bool = True,
        auto_fix: bool = True,
        auto_test: bool = True,
        description: str = "",
    ) -> Diagnosis:
        lang = (language or detect_language(code, filename)).lower()
        if lang in ("cpp", "c++"):
            lang = "cpp"
        if lang not in ("c", "cpp", "python"):
            lang = detect_language(code, filename)

        d = Diagnosis(
            language=lang,
            language_label=LANG_LABEL.get(lang, lang),
            code=code,
            stdin=stdin,
        )

        # 0) 先听用户自己说这段代码要做什么（没填就跳过）
        #    用户描述优先于「看代码关键词猜题型」，因为代码本身可能就写错了。
        desc_intent = intent.analyze(description, code, lang)
        d.intent = desc_intent.to_dict()
        d.intent_mismatch = desc_intent.provided and desc_intent.fits is False
        task, task_label = intent.merge_task(desc_intent, "", "")

        # 1) 静态预检（不运行也能发现的低级错误）
        d.static_issues = [x.to_dict() for x in static_check.check(code, lang)]

        # 2) 真跑一遍，拿真实报错
        run1 = self.runner.run(code, lang, stdin=stdin, filename=filename)
        d.first_run = run1.to_dict()
        d.env_hint = run1.compiler_hint or ""

        if run1.stage == "env_error":
            d.verdict = "运行环境不完整，先把编译器/解释器装好。"
            d.explanation = self._env_error_markdown(run1, lang)
            d.intent_check = self._judge_intent(desc_intent, code, lang, run_ok=False)
            return d

        # 3) 解析报错
        parsed = parser.parse(
            lang, run1.stderr, run1.stdout,
            stage="compile" if run1.stage == "compile_error" else "runtime",
        )
        d.parsed = parsed.to_dict()

        # Python 的语法类错误虽然表现为「进程退出码非 0」，
        # 但实际上程序一行都没执行。单独标出来，避免误导新手。
        if (
            run1.stage == "runtime_error"
            and parsed.issues
            and parsed.issues[0].stage == "syntax"
        ):
            d.first_run["stage"] = "syntax_error"

        # 4) 跑通了 -> 直接给结论（但警告也要讲清楚，警告常是 bug 前兆）
        if run1.ok:
            warn_text = run1.stderr or ""
            has_warn = "warning:" in warn_text.lower()
            filled_warn = None
            if has_warn:
                h = knowledge.lookup(lang, warn_text)
                if h:
                    filled_warn = knowledge.fill(h, warn_text)
                    d.knowledge_hit = filled_warn.to_dict()

            d.verdict = (
                "程序跑通了，但编译器报了警告，建议一并修掉。"
                if has_warn else "代码运行成功，没有报错。"
            )
            # 4.1) 正确输出核对：用独立参考实现算出标准答案
            #      题型以用户描述为准（没写描述才回退到看代码猜）
            profile = None
            try:
                profile = boundary.analyze(code, lang, task=task, task_label=task_label)
                d.expected = expected.compute(stdin, profile, run1.stdout, code).to_dict()
            except Exception:                                # noqa: BLE001
                d.expected = {}

            d.explanation = self._success_markdown(
                run1, d.static_issues, filled_warn, d.expected
            )

            # 4.2) 边界测试：能跑通 ≠ 正确。自动造极端数据再跑一遍。
            if auto_test:
                d.boundary = self._run_boundary(code, lang, task, task_label)
                stats = d.boundary.get("summary") or {}
                d.boundary_failed = int(stats.get("fail") or 0)
                d.explanation += self._boundary_markdown(d.boundary)

                # 4.2.1) 边界暴露了问题 -> 必须真的修一次，并用触发问题的输入复跑
                if auto_fix and d.boundary_failed:
                    self._auto_fix(
                        d, lang, stdin, filename, d.boundary.get("cases") or [],
                    )

            # 4.3) 代码跑通了，还要按用户的描述判断「它到底是不是做了这件事」
            d.intent_check = self._judge_intent(
                desc_intent, code, lang, run_ok=True,
                expected_data=d.expected, stdout=run1.stdout or "",
            )
            d.intent_mismatch = d.intent_check.get("verdict") == "wrong"

            # 结论优先级：和描述对不上 > 边界崩溃 > 符合描述 > 结果算错 > 编译警告 > 一切正常
            if d.intent_mismatch:
                d.verdict = d.intent_check.get("detail") or (
                    f"代码能跑通，但看不出它实现了你说的「{desc_intent.task_label}」。"
                )
            elif d.boundary_failed:
                if d.fixed_ok:
                    d.verdict = (
                        f"边界测试发现 {d.boundary_failed} 个问题，"
                        "已自动修复并用触发问题的输入复跑通过。"
                    )
                else:
                    d.verdict = (
                        f"正常输入下能跑，但有 {d.boundary_failed} 个边界场景会崩溃。"
                    )
            elif d.intent_check.get("verdict") == "correct":
                d.verdict = d.intent_check.get("detail") or "代码输出符合你描述的期望。"
            elif d.expected.get("matches") is False:
                d.verdict = (
                    f"代码跑通了，但结果算错了 —— 正确答案应该是 {d.expected.get('display')}。"
                )
            elif has_warn:
                d.verdict = "程序跑通了，但编译器报了警告，建议一并修掉。"
            elif d.expected.get("matches") is True and desc_intent.fits:
                d.verdict = (
                    f"代码正确，输出和标准答案一致，"
                    f"确实做到了你说的「{desc_intent.task_label}」。"
                )
            elif d.expected.get("matches") is True:
                d.verdict = "代码正确，输出和标准答案一致。"
            else:
                d.verdict = "代码运行成功，没有报错。"

            return d

        # 5) 知识库匹配
        # 先用"第一条错误"精确匹配（避免被后面的连锁报错带偏），
        # 匹配不到再用完整输出兜底。
        raw_err = (run1.stderr or "") + "\n" + (run1.stdout or "")
        hit = None
        match_text = raw_err
        for it in parsed.issues:
            if it.severity != "error":
                continue
            probe = f"{it.kind}: {it.message}"
            hit = knowledge.lookup(lang, probe)
            if hit:
                match_text = probe
                break
        if hit is None:
            hit = knowledge.lookup(lang, raw_err)
            match_text = raw_err

        filled = None
        if hit:
            filled = knowledge.fill(hit, match_text)
            d.knowledge_hit = filled.to_dict()

        d.verdict = self._short_verdict(parsed, run1)

        # 6) 生成讲解（先知识库，再叠加 LLM 的针对性分析）
        base_md = self._rule_based_markdown(d, parsed, filled, run1)

        if use_llm and self.llm.ready:
            llm_md = self._llm_explain(d, parsed, filled, run1)
            if llm_md:
                d.llm_used = True
                d.explanation = base_md + "\n\n---\n\n" + llm_md
            else:
                d.explanation = base_md
        else:
            if use_llm and not self.llm.ready:
                d.llm_error = "未配置大模型 API Key，当前为内置知识库解析模式。"
            d.explanation = base_md

        # 7) 自动修复 + 验证
        if auto_fix:
            self._auto_fix(d, lang, stdin, filename)

        # 8) 代码没跑通：描述核对只能给「暂时无法判断」，但要把原因说清楚
        d.intent_check = self._judge_intent(desc_intent, code, lang, run_ok=False)

        return d

    def _judge_intent(
        self,
        desc_intent: intent.Intent,
        code: str,
        lang: str,
        run_ok: bool,
        expected_data: dict | None = None,
        stdout: str = "",
    ) -> dict:
        """按用户描述判定代码是否正确（没填描述时返回未核对）。"""
        auto_task, auto_label, _ = boundary.detect_task(code, lang)
        return intent.judge(
            desc_intent,
            run_ok=run_ok,
            expected=expected_data,
            code_task=auto_task,
            code_label=auto_label,
            actual_output=stdout,
        ).to_dict()

    # ------------------------------------------------------------------ #
    # 自动修复与验证
    # ------------------------------------------------------------------ #
    def _auto_fix(
        self,
        d: Diagnosis,
        lang: str,
        stdin: str,
        filename: str,
        boundary_cases: list[dict] | None = None,
    ) -> None:
        """让 AI 出修复代码，再用真实运行（含触发问题的边界输入）验证。"""
        if not self.llm.ready:
            return

        cases = boundary_cases or []
        stress = self._stress_cases(cases)
        overflow = any(
            c.get("kind") == "IntegerOverflow"
            or "溢出" in self._case_text(c)
            or "int 上限" in self._case_text(c)
            for c in cases
        )

        current_code = d.code
        current_run = RunResult(**d.first_run)
        history_md: list[str] = []

        for rnd in range(1, self.max_fix_rounds + 1):
            prompt = self._fix_prompt(
                current_code, lang, stdin, current_run, d.parsed, rnd,
                stress=stress, overflow=overflow,
            )
            reply = self.llm.chat(prompt, self._fix_system_prompt())
            if not reply.ok:
                d.llm_error = reply.error
                break

            fixed = self._extract_code(reply.text, lang)
            if not fixed:
                history_md.append(f"第 {rnd} 次修复：模型没有返回代码")
                break
            if self._same_code(fixed, current_code):
                history_md.append(
                    f"第 {rnd} 次修复：模型原样返回了代码，已拒绝（不算修复成功）"
                )
                continue
            if overflow and lang in ("c", "cpp") and not self._has_overflow_fix(fixed):
                history_md.append(
                    f"第 {rnd} 次修复：仍是 int 运算，既没改 long long 也没做溢出拦截，已拒绝"
                )
                continue

            verify = self.runner.run(fixed, lang, stdin=stdin, filename=filename)
            stress_ok, stress_detail, last_stress_run = self._verify_stress(
                fixed, lang, filename, stress,
            )
            passed = verify.ok and stress_ok

            attempt = FixAttempt(
                round=rnd, code=fixed,
                run=(last_stress_run or verify).to_dict(), ok=passed,
            )
            d.attempts.append(attempt.to_dict())

            if passed:
                d.fixed_code = fixed
                d.fixed_run = (last_stress_run or verify).to_dict()
                d.fixed_ok = True
                history_md.append(
                    f"第 {rnd} 次修复后重新运行：✅ 通过"
                    + (f"（已用边界输入 {stress_detail} 复跑）" if stress_detail else "")
                )
                break

            history_md.append(
                f"第 {rnd} 次修复后重新运行：❌ 未通过"
                + (f"（{stress_detail}）" if stress_detail else "")
            )
            current_code = fixed
            current_run = verify
            d.fixed_code = fixed
            d.fixed_run = (last_stress_run or verify).to_dict()
            new_parsed = parser.parse(
                lang, verify.stderr, verify.stdout,
                stage="compile" if verify.stage == "compile_error" else "runtime",
            )
            d.parsed = new_parsed.to_dict()

        if history_md:
            d.explanation += (
                "\n\n---\n\n### 🔁 修复验证过程\n"
                "（这一步是本工具的关键：AI 给出的代码必须真的跑通才算数）\n\n"
                + "\n".join(f"- {x}" for x in history_md)
            )
        if d.fixed_code and not d.fixed_ok:
            d.explanation += (
                "\n\n> ⚠️ 自动修复未能完全跑通，上面的代码仍需人工确认。"
                "请把最新的报错再发一次，我们继续排查。"
            )

    @staticmethod
    def _case_text(case: dict) -> str:
        return " ".join(str(case.get(k) or "") for k in ("why", "reason", "advice", "expect"))

    @staticmethod
    def _same_code(a: str, b: str) -> bool:
        return re.sub(r"\s+", "", a or "") == re.sub(r"\s+", "", b or "")

    @staticmethod
    def _has_overflow_fix(code: str) -> bool:
        low = code.lower()
        if "long long" in low or "llong_max" in low:
            return True
        # 也接受「计算前拦截过大输入」的写法：if (n > 12) { ... }
        return bool(re.search(r"if\s*\([^)]*\b(?:n|num|x|a|b)\s*>\s*\d+", low))

    @staticmethod
    def _stress_cases(cases: list[dict]) -> list[dict]:
        """挑出「触发问题」的输入，修复后必须用它们复跑。"""
        out: list[dict] = []
        seen: set[str] = set()
        for c in cases:
            if c.get("status") not in ("fail", "warn"):
                continue
            text = " ".join(str(c.get(k) or "") for k in ("why", "reason", "advice", "expect"))
            overflow = (
                c.get("kind") == "IntegerOverflow"
                or "溢出" in text
                or "int 上限" in text
            )
            if not (c.get("expect_value") or overflow):
                continue
            stdin = c.get("stdin") or ""
            if not stdin or stdin in seen:
                continue
            seen.add(stdin)
            out.append({
                "stdin": stdin,
                "expect_value": c.get("expect_value") or "",
                "overflow": overflow,
                "name": c.get("name") or "",
            })
        return out

    def _verify_stress(
        self, code: str, lang: str, filename: str, stress: list[dict],
    ) -> tuple[bool, str, RunResult | None]:
        """用边界输入复跑修复代码：必须不崩溃、且结果不是溢出的错误值。"""
        if not stress:
            return True, "", None

        last: RunResult | None = None
        used: list[str] = []
        for item in stress:
            run = self.runner.run(code, lang, stdin=item["stdin"], filename=filename)
            last = run
            label = item["stdin"].strip().replace("\n", ",")
            used.append(label)
            if not run.ok:
                return False, f"输入 {label} 仍然报错", run

            out = (run.stdout or "").replace(" ", "").replace(",", "")
            expect = item.get("expect_value") or ""
            if expect and expect not in out:
                actual = (run.stdout or "").strip()[:60] or "空"
                return False, f"输入 {label} 期望 {expect}，实际输出 {actual}", run

            if item.get("overflow"):
                nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", run.stdout or "")]
                if any(n < 0 for n in nums):
                    return False, f"输入 {label} 仍然溢出成负数", run

        return True, "、".join(used), last

    # ------------------------------------------------------------------ #
    # 提示词
    # ------------------------------------------------------------------ #
    def _fix_system_prompt(self) -> str:
        return (
            "你是严谨的编程助教。你的任务是修复学生代码里的错误，并且只输出代码。\n"
            "要求：\n"
            "1. 只输出一个 Markdown 代码块，不要任何解释文字。\n"
            "2. 代码必须是完整可运行的（包含 main 函数 / 所有 import），不是片段。\n"
            "3. 不要改变原代码的意图和整体结构，只做最小的必要修复。\n"
            "4. 不要添加原代码没有的功能，不要为了跑通而删除核心逻辑。\n"
            "5. 如果是缺少头文件、缺少分号、缩进等小问题，直接修正即可。\n"
            "6. 如果问题是 32 位整型溢出（结果变成负数/明显错误），必须把相关变量改为 "
            "long long（C/C++）或增加溢出拦截；只改注释、输出格式或变量名不算修复。\n"
            "7. 绝对不要原样返回原始代码；没有改动就不要输出代码块。\n"
            "8. 分析与说明必须基于当前代码的真实语义，禁止提及代码里不存在的操作"
            "（例如阶乘代码不要说「除以 0」）。"
        )

    def _fix_prompt(
        self, code, lang, stdin, run: RunResult, parsed: dict, rnd: int,
        stress: list[dict] | None = None, overflow: bool = False,
    ) -> str:
        issues = parsed.get("issues") or []
        lines_txt = []
        for it in issues:
            lines_txt.append(
                f"- [{it.get('severity')}] {it.get('kind')} 第{it.get('line')}行: {it.get('message')}"
            )
        if not lines_txt:
            lines_txt.append("- （未能结构化解析，请直接看下面的原始输出）")

        stress_txt = ""
        if stress:
            items = [
                f"- 输入 `{s['stdin'].strip()}`：期望 {s['expect_value'] or '不崩溃、不出现负数溢出'}"
                for s in stress
            ]
            stress_txt = (
                "\n\n【必须通过的边界场景】\n" + "\n".join(items)
                + "\n修复后会用上面这些输入重新运行；只通过普通输入不算修复成功。"
            )

        overflow_txt = ""
        if overflow and lang in ("c", "cpp"):
            overflow_txt = (
                "\n\n【硬性要求】本次问题包含 32 位整数溢出：必须把相关变量从 int 改为 "
                "long long，输出格式改成 %lld；或者在计算前拦截过大的输入。"
                "只改注释、改变量名或原样返回都不算修复。"
            )

        return f"""请修复下面这段 {LANG_LABEL.get(lang, lang)} 代码。

【原始代码】
```{ 'c' if lang in ('c','cpp') else 'python' }
{code}
```

【真实运行结果】
- 阶段：{run.stage}
- 退出码：{run.exit_code}
- 标准输出：
{run.stdout[:1500] or '(空)'}
- 错误输出：
{run.stderr[:2500] or '(空)'}

【解析出的问题】
{chr(10).join(lines_txt)}

【标准输入】
{stdin or '(无)'}{stress_txt}{overflow_txt}

这是第 {rnd} 次修复尝试。请输出修复后的完整代码（仅代码块）。"""

    def _llm_explain(self, d, parsed, filled, run: RunResult) -> str:
        code_fence = "c" if d.language in ("c", "cpp") else "python"
        parsed_txt = parser.format_for_prompt(parsed)
        kb_txt = ""
        if filled:
            kb_txt = (
                f"\n【内置知识库已匹配到】{filled.title}\n"
                f"病因：{'；'.join(filled.causes[:3])}\n"
                f"修法：{'；'.join(filled.fixes[:3])}\n"
            )

        prompt = f"""一位编程新手提交了下面这段 {d.language_label} 代码，它运行报错了。
我已经**真实运行过**这段代码，下面是真实的运行结果，请基于它进行讲解。

【用户代码】
```{code_fence}
{d.code}
```

【真实运行结果】
- 阶段：{run.stage}（compile_error=编译不通过；runtime_error=运行时崩溃）
- 退出码：{run.exit_code}
- 标准输出：
{run.stdout[:1500] or '(空)'}
- 标准错误：
{run.stderr[:2500] or '(空)'}

【结构化解析出的错误】
{parsed_txt}
{kb_txt}
【静态预检发现的可疑点】
{chr(10).join(f"- 第{x['line']}行 {x['title']}" for x in d.static_issues) or '(无)'}

请按以下结构回答（Markdown，中文）：

## 一句话说明
用一句大白话说清楚「程序为什么崩了」。

## 报错原文逐词翻译
把报错信息中出现的英文关键词逐个拆开解释（例如 `NameError` = 名字错误，
`is not defined` = 没有被定义），让新手下次自己能看懂。

## 出错位置
明确指出是第几行、哪一段代码出的问题，并解释「为什么这一行会走到这里」。

## 修改方案
说明要改什么、为什么这样改。如果有多处错误，按「先修第一个」的顺序讲，
并说明为什么后面的错误可能只是连锁反应。

## 正确代码
给出完整可运行的代码（用 Markdown 代码块）。

## 记住这一点
用一句话总结这次要记住的知识点。

注意：不要编造我没提供的报错信息；解释要具体到变量名和符号，不要泛泛而谈。
分析必须基于当前代码的真实语义，禁止提及代码里不存在的操作
（例如阶乘代码不要分析成「除以 0」）。"""

        reply = self.llm.chat(prompt)
        if not reply.ok:
            d.llm_error = reply.error
            return ""
        return reply.text

    # ------------------------------------------------------------------ #
    # 规则化 Markdown（无 LLM 也能用）
    # ------------------------------------------------------------------ #
    def _short_verdict(self, parsed, run: RunResult) -> str:
        if run.stage == "compile_error":
            n = len([i for i in parsed.issues if i.severity == "error"])
            return f"编译失败，共 {n or 1} 处错误，程序一行都没跑起来。"
        if run.stage == "timeout":
            return "程序运行超时，多半是死循环或在等输入。"

        # 只看真正的 error，别把编译警告当成本次崩溃的原因
        errors = [i for i in parsed.issues if i.severity == "error"]
        if errors:
            first = errors[0]
            if first.stage == "syntax":
                loc = f"第 {first.line} 行有" if first.line else "存在"
                return f"{loc}语法错误，程序一个字都没执行。"
            loc = f"第 {first.line} 行" if first.line else "运行过程中"
            return f"程序在{loc}崩溃：{first.kind}。"

        if run.stage == "runtime_error":
            # 编译期只有警告（甚至没有诊断信息），运行期却崩了 —— C 语言里最典型
            return (
                "程序运行时崩溃了，但没有给出具体的出错行。"
                "C 语言里这通常是数组越界、空指针、或 scanf 漏写了 &。"
            )
        return "程序运行时出错，但没有捕获到可解析的报错。"

    def _rule_based_markdown(self, d: Diagnosis, parsed, filled, run: RunResult) -> str:
        parts: list[str] = []

        # 结论
        parts.append(f"## 结论\n{d.verdict}\n")

        # 运行现场
        parts.append("## 真实运行现场\n")
        if run.command:
            parts.append(f"**执行的命令**\n```\n{run.command}\n```\n")
        if run.stdout.strip():
            parts.append(f"**程序输出（stdout）**\n```\n{run.stdout.strip()[:2000]}\n```\n")
        if run.stderr.strip():
            parts.append(f"**报错信息（stderr）**\n```\n{run.stderr.strip()[:3000]}\n```\n")
        if run.duration:
            parts.append(f"耗时 {run.duration} 秒，退出码 `{run.exit_code}`。\n")

        # 定位
        issues = parsed.issues
        if issues:
            parts.append("## 出错位置定位\n")
            for it in issues[:5]:
                if it.severity != "error":
                    continue
                head = f"**{it.kind}**"
                if it.line:
                    head += f" — 第 **{it.line}** 行"
                    if it.column:
                        head += f"，第 {it.column} 列"
                parts.append(head)
                if it.message:
                    parts.append(f"> {it.message}")
                if it.source_line:
                    parts.append(f"问题代码：`{it.source_line}`")
                if it.caret and "|" in it.caret:
                    parts.append(f"编译器指向：\n```\n{it.caret.strip()}\n```")
                parts.append("")
            if len([x for x in issues if x.severity == "error"]) > 5:
                parts.append(f"（共 {len(issues)} 条，先解决上面这些。）\n")

        # 逐词翻译
        if issues:
            trans = self._translate_report(d.language, issues[0])
            if trans:
                parts.append("## 报错信息逐词翻译\n" + trans + "\n")

        # 知识库讲解
        if filled:
            parts.append(f"## 这是什么错误：{filled.title}\n")
            parts.append(filled.meaning + "\n")
            if filled.causes:
                parts.append("**常见原因**\n")
                for c in filled.causes:
                    parts.append(f"- {c}")
                parts.append("")
            if filled.fixes:
                parts.append("**怎么修**\n")
                for i, f in enumerate(filled.fixes, 1):
                    parts.append(f"{i}. {f}")
                parts.append("")
            if filled.example_bad:
                fence = "c" if d.language in ("c", "cpp") else "python"
                parts.append(f"**错误写法**\n```{fence}\n{filled.example_bad}\n```")
                parts.append(f"**正确写法**\n```{fence}\n{filled.example_good}\n```\n")
            if filled.tip:
                parts.append(f"> 💡 {filled.tip}\n")
        else:
            parts.append(
                "## 说明\n"
                "这条报错不在内置知识库中。下面是基于真实运行结果的定位，"
                "配置大模型后可以获得更细致的逐词讲解。\n"
            )

        # 静态预检
        if d.static_issues:
            parts.append("## 静态预检发现的可疑点\n")
            parts.append("（这些是没运行前就能看出来的问题，建议一并改掉）\n")
            for s in d.static_issues[:8]:
                parts.append(f"- **第 {s['line']} 行**：{s['title']}\n  - {s['detail']}\n  - 修法：{s['fix']}")
            parts.append("")

        return "\n".join(parts)

    def _translate_report(self, language: str, issue) -> str:
        """把报错里的英文关键词逐个翻成中文。"""
        from . import glossary
        kind = issue.kind or ""
        msg = issue.message or ""

        rows: list[str] = []
        # 错误类型
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", kind):
            zh = glossary.term(token)
            if zh:
                rows.append(f"| `{token}` | {zh} |")
        # 消息中的关键词
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", msg):
            zh = glossary.term(token)
            if zh and f"`{token}`" not in " ".join(rows):
                rows.append(f"| `{token}` | {zh} |")

        if not rows:
            return ""
        header = "| 英文原文 | 中文含义 |\n|---|---|"
        return header + "\n" + "\n".join(rows[:14])

    # ------------------------------------------------------------------ #
    # 边界测试
    # ------------------------------------------------------------------ #
    def _run_boundary(self, code: str, lang: str, task: str = "", task_label: str = "") -> dict:
        """跑边界测试。任何异常都不能影响主诊断结果。"""
        try:
            return boundary.run_suite(
                code, lang, runner=self.runner, task=task, task_label=task_label,
            )
        except Exception as e:                      # noqa: BLE001
            return {
                "profile": {},
                "cases": [],
                "summary": {"total": 0, "pass": 0, "fail": 0, "warn": 0, "unknown": 0},
                "conclusion": f"边界测试未能执行：{type(e).__name__}: {e}",
            }

    DIV_MENTION_RE = re.compile(r"(?:除以\s*0|除以零|除零|除法)")

    def _clean_boundary_text(self, text: str, div_relevant: bool) -> str:
        """代码里没有除法时，禁止边界分析里出现「除以 0 / 除零」等无关字样。"""
        if div_relevant or not text:
            return text
        return self.DIV_MENTION_RE.sub("边界输入的处理", text)

    def _boundary_markdown(self, data: dict) -> str:
        profile = data.get("profile") or {}
        cases = data.get("cases") or []
        stats = data.get("summary") or {}
        if not cases:
            return ""

        div_relevant = bool(profile.get("has_division")) or profile.get("task") == "average"

        parts = ["## 边界测试（自动生成的极端数据）\n"]
        spec = profile.get("spec") or {}
        if profile:
            parts.append(
                f"识别为 **{profile.get('task_label', '通用程序')}**"
                f"（置信度 {profile.get('confidence', 'low')}）；"
                f"输入形态：{spec.get('desc', '未知')}。\n"
            )

        parts.append(
            f"共 {stats.get('total', 0)} 个用例："
            f"通过 {stats.get('pass', 0)} · 失败 {stats.get('fail', 0)} · "
            f"需留意 {stats.get('warn', 0)} · 待确认 {stats.get('unknown', 0)}。\n"
        )
        parts.append(f"**{data.get('conclusion', '')}**\n")

        bad = [c for c in cases if c.get("status") == "fail"]
        if bad:
            parts.append("### 会崩溃的边界场景\n")
            for c in bad:
                parts.append(f"#### ❌ {c.get('name')}")
                parts.append(f"- **输入**：`{c.get('stdin', '').replace(chr(10), ' ⏎ ').strip()}`")
                parts.append(
                    f"- **为什么测它**：{self._clean_boundary_text(c.get('why', ''), div_relevant)}"
                )
                if c.get("expect"):
                    parts.append(f"- **期望**：{c.get('expect')}")
                parts.append(
                    f"- **实际**：{self._clean_boundary_text(str(c.get('reason', '')).splitlines()[0], div_relevant)}"
                )
                if c.get("advice"):
                    parts.append(
                        f"- **怎么改**：{self._clean_boundary_text(c.get('advice'), div_relevant)}"
                    )
                parts.append("")

        warns = [c for c in cases if c.get("status") == "warn"]
        if warns:
            parts.append("### 需要留意\n")
            for c in warns:
                parts.append(
                    f"- **{c.get('name')}**：{self._clean_boundary_text(c.get('reason', ''), div_relevant)}"
                )
                if c.get("advice"):
                    parts.append(f"  - {self._clean_boundary_text(c.get('advice'), div_relevant)}")
            parts.append("")

        for note in (profile.get("notes") or []):
            parts.append(f"> 💡 {note}\n")

        return "\n".join(parts)

    def _success_markdown(self, run: RunResult, static_issues: list,
                          warn_entry=None, exp: dict | None = None) -> str:
        parts: list[str] = []
        warn = (run.stderr or "")
        if "warning:" in warn.lower():
            parts.append("## 结论\n✅ 程序能跑通，但编译器报了**警告**。\n")
            parts.append(f"```\n{warn.strip()[:1800]}\n```\n")
            parts.append(
                "> 警告不阻止程序运行，但它往往是 bug 的前兆。"
                "比如「变量声明了没用」常意味着你把它名字写错了；"
                "「格式符类型不匹配」会让 printf 打印出乱码。\n"
            )
            if warn_entry:
                parts.append(f"## 这条警告是什么意思：{warn_entry.title}\n")
                parts.append(warn_entry.meaning + "\n")
                if warn_entry.causes:
                    parts.append("**常见原因**\n")
                    parts.extend(f"- {c}" for c in warn_entry.causes)
                    parts.append("")
                if warn_entry.fixes:
                    parts.append("**怎么修**\n")
                    parts.extend(f"{i}. {f}" for i, f in enumerate(warn_entry.fixes, 1))
                    parts.append("")
                if warn_entry.tip:
                    parts.append(f"> 💡 {warn_entry.tip}\n")
        else:
            parts.append("## 结论\n✅ 代码运行成功，没有报错。\n")
        if run.stdout.strip():
            parts.append(f"## 程序输出\n```\n{run.stdout.strip()[:3000]}\n```\n")
        else:
            parts.append("## 程序输出\n（程序没有任何输出。如果你期望有输出，检查一下是不是漏了 print / printf。）\n")

        # 正确输出核对
        if exp:
            if exp.get("available"):
                parts.append("## 正确输出\n")
                if exp.get("formula"):
                    parts.append(f"按标准算法：**{exp['formula']}**\n")
                parts.append(f"### 正确答案：`{exp.get('display')}`\n")
                if exp.get("actual", "").strip():
                    parts.append(f"你的程序实际输出：\n```\n{exp['actual'].strip()[:800]}\n```")
                state = exp.get("matches")
                if state is True:
                    parts.append("✅ **和正确答案一致，这道题算对了。**\n")
                elif state is False:
                    parts.append(
                        f"❌ **和正确答案 `{exp.get('display')}` 不一致。**"
                        "代码能跑通，但结果算错了，这才是真正要修的 bug。\n"
                    )
                else:
                    parts.append(
                        f"❔ 从输出里没能确认结果，请对照上面的「正确答案」自行核对。\n"
                    )
                if exp.get("note"):
                    parts.append(f"> {exp['note']}\n")
            elif exp.get("note"):
                parts.append(f"## 正确输出\n{exp['note']}\n")

        if static_issues:
            parts.append("## 但有一些隐患\n")
            for s in static_issues[:8]:
                parts.append(f"- 第 {s['line']} 行：**{s['title']}** — {s['detail']}")
            parts.append("")
        parts.append(
            "## 下一步\n"
            "代码能跑不等于结果正确。建议用下面几组数据自测：\n"
            "- 正常数据（普通输入）\n"
            "- 边界数据（0、1、最大值）\n"
            "- 异常数据（空输入、负数、超长输入）\n"
        )
        return "\n".join(parts)

    def _env_error_markdown(self, run: RunResult, lang: str) -> str:
        tc = detect_toolchain()
        lines = ["## 环境问题\n", "当前电脑上缺少运行/编译这类代码所需的工具。\n"]
        if run.stderr:
            lines.append(f"```\n{run.stderr.strip()}\n```\n")
        notes = tc.notes or []
        if notes:
            lines.append("### 怎么解决\n")
            for n in notes:
                lines.append(f"- {n}")
            lines.append("")
        lines.append(
            "### 装好之后\n"
            "重新点一次「检查代码」，工具会自动找到新装的编译器，不需要重启电脑。\n"
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_code(text: str, lang: str) -> str:
        """从模型回复里抽出第一个代码块。"""
        blocks = re.findall(r"```[a-zA-Z0-9+#]*\s*\n(.*?)```", text, re.S)
        if not blocks:
            return ""
        # 选最长的那个（通常就是完整代码）
        code = max(blocks, key=len).strip()
        return code
