"""
报错知识库：常见 C / Python 报错的"人话翻译 + 病因 + 药方"。

设计目的：即使没有配置大模型，助手也能给出准确、可操作的讲解。
大模型的作用是在此基础上做更贴合具体代码的补充，而不是替代它。

每条规则包含：
  id        唯一标识
  lang      python / c / any
  match     用于匹配错误文本的正则
  title     一句话概括（大白话）
  meaning   这条报错"字面在说什么"
  causes    常见原因列表
  fixes     修复办法列表
  example   典型错误 -> 正确写法
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict


@dataclass
class Entry:
    id: str
    lang: str
    pattern: str
    title: str
    meaning: str
    causes: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    example_bad: str = ""
    example_good: str = ""
    tip: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


ENTRIES: list[Entry] = [
    # =============================== Python =============================== #
    Entry(
        id="py.name_error", lang="python",
        pattern=r"NameError:\s*name '(?P<name>[^']+)' is not defined",
        title="用了一个还没定义过的名字",
        meaning=(
            "NameError 直译是『名字错误』。Python 说：你在代码里写了一个叫 "
            "`{name}` 的词，但我在前面从来没见你给它赋过值，所以我不认识它。"
        ),
        causes=[
            "变量名拼错了（大小写不同也算不同的名字，如 `Name` 和 `name`）",
            "变量确实还没赋值就直接用了（顺序反了）",
            "变量定义在 if / for / 函数内部，出了那个范围就消失了（作用域问题）",
            "想写字符串却忘了加引号，比如写了 `print(hello)` 而不是 `print(\"hello\")`",
        ],
        fixes=[
            "检查拼写：报错里的这个名字，和你定义时的名字是否一模一样",
            "把赋值语句移到使用它的那一行之前",
            "如果要跨函数用，把它通过参数传进去，或定义在函数外面",
            "文字内容记得加引号：`\"hello\"` 或 `'hello'`",
        ],
        example_bad="print(total)\ntotal = 10",
        example_good="total = 10\nprint(total)",
        tip="Python 从上往下逐行执行，所以『先用后定义』一定会失败。",
    ),
    Entry(
        id="py.syntax", lang="python",
        pattern=r"SyntaxError:\s*(invalid syntax|invalid character|unexpected EOF|unterminated string literal|"
                r"EOL while scanning|expected .+)",
        title="语法写错，Python 根本没读懂这句话",
        meaning=(
            "SyntaxError 是『语法错误』。就像中文少了一个句号或者括号没闭合，"
            "整句话语法不成立，所以 Python 连运行都没法开始，一行都不会执行。"
        ),
        causes=[
            "括号 `()` `[]` `{}` 没成对，少了右边那个",
            "if / for / while / def / class 那行结尾漏了英文冒号 `:`",
            "用了中文标点（中文全角括号（）、引号“”、冒号：、逗号，）",
            "字符串引号没闭合",
            "把 `=` （赋值）写成了需要比较的地方，或反过来",
        ],
        fixes=[
            "看报错指向的行，重点检查这一行**和它的上一行**（漏冒号常报在下一行）",
            "把中文标点全部换成英文标点",
            "用编辑器的括号匹配功能，确认括号成对",
        ],
        example_bad="for i in range(5)\n    print(i)",
        example_good="for i in range(5):\n    print(i)",
        tip="SyntaxError 常常是「上一行」的问题，因为 Python 读到下一行才发现前面没写完。",
    ),
    Entry(
        id="py.indent", lang="python",
        pattern=r"(IndentationError|TabError):\s*(unexpected indent|expected an indented block|"
                r"unindent does not match|inconsistent use of tabs)",
        title="缩进（前面空格）不对",
        meaning=(
            "IndentationError 是『缩进错误』。Python 靠行首空格来判断「哪几行属于同一个块」，"
            "所以空格多一个少一个都可能报错——这是它和 C 语言最大的不同。"
        ),
        causes=[
            "该缩进的地方没缩进（比如 if 下面、for 下面）",
            "不该缩进的地方多缩进了",
            "同一层级混用了 Tab 和空格",
            "复制粘贴代码带进来了不规则空格",
        ],
        fixes=[
            "统一用 4 个空格，全文件保持一致（不要混用 Tab）",
            "冒号 `:` 下面那一行必须比它多缩进一层",
            "在编辑器里开启「显示空白字符」，肉眼就能看出问题",
        ],
        example_bad="if x > 0:\nprint('正数')",
        example_good="if x > 0:\n    print('正数')",
        tip="记住口诀：谁的下面，就比谁多缩进 4 个空格。",
    ),
    Entry(
        id="py.type_concat", lang="python",
        pattern=r"TypeError:\s*can only concatenate str \(not \"(?P<other>[\w\[\]']+)\"\) to str",
        title="字符串和数字直接相加了",
        meaning=(
            "TypeError 是『类型错误』。`+` 号用在两个字符串上是「拼接」，"
            "用在两个数字上是「相加」，但你不能把文字和数字混在一起加，Python 不知道该按哪种算。"
        ),
        causes=[
            "`\"分数：\" + 95` 想把文字和数字拼起来",
            "input() 拿到的是字符串，却直接当数字参与计算",
        ],
        fixes=[
            "用 f-string：`f\"分数：{score}\"`（最推荐）",
            "用 `str(score)` 先转成字符串再拼",
            "如果是拿 input() 的数做运算，先用 `int()` 或 `float()` 转换",
        ],
        example_bad='age = input("年龄:")\nprint("你今年" + age + "岁")',
        example_good='age = int(input("年龄:"))\nprint(f"你今年{age}岁")',
        tip="input() 拿到的永远是字符串，要算数就先 int() 或 float()。",
    ),
    Entry(
        id="py.type_operand", lang="python",
        pattern=r"TypeError:\s*unsupported operand type\(s\) for [^:]+:\s*'(?P<a>[^']+)' and '(?P<b>[^']+)'",
        title="两个类型不支持这个运算",
        meaning=(
            "TypeError『不支持的运算』。比如 `'abc' - 1`，减法对字符串没有意义，"
            "就像让「苹果」减去「3」一样，Python 不知道怎么做。"
        ),
        causes=[
            "把字符串当成数字来算（忘记 int/float 转换）",
            "None 参与了运算（函数没有 return 却拿来用）",
            "list 和数字相加（应改用 append 或 + [x]）",
        ],
        fixes=[
            "先确认参与运算的两个东西各是什么类型：`print(type(x))`",
            "把字符串转成数字：`int(x)` / `float(x)`",
            "检查函数是不是漏了 `return`",
        ],
        example_bad='num = "10"\nprint(num * 2 + 5)',
        example_good='num = int("10")\nprint(num * 2 + 5)',
        tip="拿不准类型时，`print(type(变量))` 是最快的排查手段。",
    ),
    Entry(
        id="py.index", lang="python",
        pattern=r"IndexError:\s*list index out of range",
        title="下标超出范围了",
        meaning=(
            "IndexError『索引错误』。列表像一排编号从 0 开始的抽屉，"
            "你伸手去拉第 5 个抽屉，但那排只有 3 个，所以扑空了。"
        ),
        causes=[
            "以为下标从 1 开始，实际 Python 从 0 开始",
            "循环写到 `range(len(lst) + 1)`，多跑了一圈",
            "列表本身是空的，却访问 `lst[0]`",
            "列表长度在循环中被删减了",
        ],
        fixes=[
            "记住最后一个元素的下标是 `len(lst) - 1`",
            "循环直接用 `for x in lst:`，不要手动管下标",
            "访问前先判断：`if len(lst) > i:`",
        ],
        example_bad="a = [1, 2, 3]\nprint(a[3])",
        example_good="a = [1, 2, 3]\nprint(a[2])   # 或者 print(a[-1]) 取最后一个",
        tip="Python 下标从 0 开始，`-1` 表示最后一个，`-2` 表示倒数第二个。",
    ),
    Entry(
        id="py.key", lang="python",
        pattern=r"KeyError:\s*'?(?P<key>[^'\n]+)'?",
        title="字典里没有这个键",
        meaning=(
            "KeyError『键错误』。字典是「名字 -> 内容」的对照表，"
            "你喊了一个表里没有登记的名字，所以拿不到东西。"
        ),
        causes=[
            "键名拼写错误或大小写不一致",
            "这个键还没被写进字典",
            "读取了从 JSON / 文件里来的数据，但字段名不对",
        ],
        fixes=[
            "用 `d.get(key)` 代替 `d[key]`，取不到时返回 None 而不是报错",
            "先判断：`if key in d:`",
            "用 `print(d.keys())` 看看实际有哪些键",
        ],
        example_bad='d = {"name": "小明"}\nprint(d["age"])',
        example_good='d = {"name": "小明"}\nprint(d.get("age", "未知"))',
        tip="不确定键存不存在时，一律用 `.get()`，更安全。",
    ),
    Entry(
        id="py.value_int", lang="python",
        pattern=r"ValueError:\s*invalid literal for int\(\) with base 10:\s*'(?P<val>[^']*)'",
        title="这段文字转不成整数",
        meaning=(
            "ValueError『值错误』。类型是对的（确实是字符串），但内容没法变成数字——"
            "`int()` 只认 `\"123\"` 这种纯数字，遇到 `\"abc\"`、`\"12.5\"`、`\"\"` 就没办法。"
        ),
        causes=[
            "用户输入了字母或空回车",
            "输入里带了空格、单位（如 `12岁`）",
            "想转小数却用了 `int()`",
        ],
        fixes=[
            "想要小数用 `float()`，它接受 `\"12.5\"`",
            "先清洗：`s.strip()` 去掉首尾空格",
            "用 try/except 兜住错误输入并提示用户重输",
        ],
        example_bad='age = int(input("年龄："))',
        example_good=(
            'while True:\n'
            '    try:\n'
            '        age = int(input("年龄："))\n'
            '        break\n'
            '    except ValueError:\n'
            '        print("请输入数字！")'
        ),
        tip="凡是要把输入转数字，都应该有 try/except 保护。",
    ),
    Entry(
        id="py.zero_div", lang="python",
        pattern=r"ZeroDivisionError:\s*(division by zero|float division by zero|integer division or modulo by zero)",
        title="除数（分母）是 0",
        meaning=(
            "ZeroDivisionError『除以零』。数学上除以 0 没有意义，"
            "程序也一样会直接崩溃。"
        ),
        causes=[
            "分母来自变量，这个变量恰好算出来是 0",
            "循环里累加/累乘，初始值是 0 导致后面除它",
            "求平均值时列表是空的",
        ],
        fixes=[
            "除法前判断：`if b != 0:`",
            "求平均时先判空：`if len(nums) > 0:`",
            "用 try/except ZeroDivisionError 给出友好提示",
        ],
        example_bad="a, b = 10, 0\nprint(a / b)",
        example_good="a, b = 10, 0\nprint(a / b if b != 0 else '不能除以0')",
        tip="任何除法，都该问一句：分母会不会是 0？",
    ),
    Entry(
        id="py.attribute", lang="python",
        pattern=r"AttributeError:\s*'(?P<obj>[^']+)' object has no attribute '(?P<attr>[^']+)'",
        title="这个对象没有你要的方法/属性",
        meaning=(
            "AttributeError『属性错误』。`{obj}` 这个东西身上并没有叫 `{attr}` 的技能，"
            "就像让汽车去调用 `.fly()` 一样。"
        ),
        causes=[
            "名字拼错，或大小写写错",
            "把列表当字符串用（`list.upper()` 不存在）",
            "变量实际是 None（函数没 return），所以什么方法都没有",
        ],
        fixes=[
            "先 `print(type(x))` 确认对象的真实类型",
            "查询该类型支持的方法（如列表用 append/extend，字符串用 upper/strip）",
            "确认函数有没有漏 `return`",
        ],
        example_bad="nums = [1, 2]\nprint(nums.upper())",
        example_good="nums = [1, 2]\nprint(len(nums))",
        tip="报错里 `'NoneType' object has no attribute` 十有八九是函数忘了 return。",
    ),
    Entry(
        id="py.arg_count", lang="python",
        pattern=r"TypeError:\s*\w+\(\)\s*(missing \d+ required positional argument|takes \d+ positional argument)",
        title="函数调用时参数个数不对",
        meaning=(
            "TypeError『参数不对』。你定义的函数要求填 2 个东西，"
            "但你只给了 1 个（或者给多了）。"
        ),
        causes=[
            "漏传参数",
            "参数顺序搞混",
            "方法和函数搞混，忘了 self / 忘了括号",
        ],
        fixes=[
            "对照函数定义，数一数需要几个参数",
            "给参数设默认值：`def f(a, b=0):`",
        ],
        example_bad="def add(a, b):\n    return a + b\nprint(add(1))",
        example_good="def add(a, b):\n    return a + b\nprint(add(1, 2))",
        tip="",
    ),
    Entry(
        id="py.unbound", lang="python",
        pattern=r"UnboundLocalError:\s*(cannot access local variable|local variable '(?P<name>[^']+)' referenced before assignment)",
        title="函数内部的变量「先用后赋值」",
        meaning=(
            "UnboundLocalError。函数里出现了给 `{name}` 赋值的语句，"
            "Python 就认为它是局部变量；但你在这条赋值之前就使用了它，所以它还不存在。"
        ),
        causes=[
            "函数里既用了外部同名变量，又给它赋值",
            "`count += 1` 而 count 是全局变量",
        ],
        fixes=[
            "把赋值挪到使用之前",
            "确实要改全局变量就声明 `global count`（但更推荐用参数/返回值）",
        ],
        example_bad="count = 0\ndef f():\n    count += 1\nf()",
        example_good="def f(c):\n    return c + 1\ncount = f(0)",
        tip="",
    ),
    Entry(
        id="py.module", lang="python",
        pattern=r"(ModuleNotFoundError|ImportError):\s*(No module named '(?P<mod>[^']+)'|.*)",
        title="找不到这个模块（库没装或名字错）",
        meaning=(
            "ModuleNotFoundError『模块没找到』。你 `import` 的库，"
            "当前 Python 环境里没有安装。"
        ),
        causes=[
            "库确实没装",
            "装到了另一个 Python 版本/虚拟环境里",
            "库名写错（如 `import sk-learn`，正确是 `sklearn`）",
            "自己写的文件名与库同名，把库挡住了（如文件叫 `random.py`）",
        ],
        fixes=[
            "在终端执行 `pip install 库名` 安装",
            "确认用的是同一个 Python：`python -c \"import sys; print(sys.executable)\"`",
            "检查自己的文件名是否和库重名，重名就改名",
        ],
        example_bad="import requests",
        example_good="# 终端执行：pip install requests\nimport requests",
        tip="装完还是找不到？99% 是装到了别的 Python 环境里。",
    ),
    Entry(
        id="py.recursion", lang="python",
        pattern=r"RecursionError:\s*maximum recursion depth exceeded",
        title="递归太深（忘了写停止条件）",
        meaning=(
            "RecursionError『递归过深』。函数自己调用自己，但没有「什么时候停」的条件，"
            "于是一直套下去，撑爆了。"
        ),
        causes=[
            "递归函数缺少终止条件（base case）",
            "终止条件永远不成立（比如参数没往终止方向变化）",
        ],
        fixes=[
            "补上终止条件：`if n <= 0: return`",
            "每次递归调用都让参数更接近终止条件，如 `f(n-1)`",
        ],
        example_bad="def f(n):\n    return f(n - 1)",
        example_good="def f(n):\n    if n <= 0:\n        return 0\n    return f(n - 1)",
        tip="",
    ),
    Entry(
        id="py.not_callable", lang="python",
        pattern=r"TypeError:\s*'(?P<obj>[^']+)' object is not callable",
        title="把「东西」当「函数」加了括号",
        meaning=(
            "「not callable」意思是「不能调用」。你在某个变量后面写了 `()`，"
            "但它不是函数，不能被执行。"
        ),
        causes=[
            "变量名和函数名重名了（变量把函数覆盖了）",
            "给属性赋值时多写了括号",
        ],
        fixes=[
            "检查这个名字是不是被同名变量覆盖了",
            "确认你调用的确实是函数",
        ],
        example_bad="len = 5\nprint(len([1, 2, 3]))",
        example_good="print(len([1, 2, 3]))",
        tip="永远别用内置函数名（len、list、dict、sum）当变量名。",
    ),
    Entry(
        id="py.not_subscriptable", lang="python",
        pattern=r"TypeError:\s*'(?P<obj>[^']+)' object is not subscriptable",
        title="这个对象不能用 [ ] 取值",
        meaning=(
            "「not subscriptable」= 不能加方括号。方括号取值只适用于字符串、列表、字典等，"
            "`{obj}` 不支持。"
        ),
        causes=[
            "函数返回 None（忘了 return），又对结果做 `[0]`",
            "把数字当列表取下标",
        ],
        fixes=[
            "确认这个变量是不是 None：先 `print(x)`",
            "补上 return",
        ],
        example_bad="def f():\n    print([1, 2])\nx = f()\nprint(x[0])",
        example_good="def f():\n    return [1, 2]\nx = f()\nprint(x[0])",
        tip="",
    ),
    Entry(
        id="py.file", lang="python",
        pattern=r"FileNotFoundError:\s*\[Errno 2\].*'(?P<path>[^']+)'",
        title="文件路径不存在",
        meaning=(
            "FileNotFoundError『文件没找到』。程序按你给的路径去找文件，那个位置没有这个文件。"
        ),
        causes=[
            "相对路径的「当前目录」和你以为的不一样",
            "文件名/后缀写错",
            "文件确实没创建",
        ],
        fixes=[
            "打印当前目录：`import os; print(os.getcwd())`",
            "用绝对路径，或 `os.path.join` 拼接",
            "先判断：`os.path.exists(path)`",
        ],
        example_bad='open("data.txt")',
        example_good='open(r"C:\\Users\\me\\data.txt", encoding="utf-8")',
        tip="",
    ),

    # ============================== C / C++ ============================== #
    Entry(
        id="c.semicolon", lang="c",
        # gcc 可能报 "expected ';' before"，也可能报 "expected ',' or ';' before"
        pattern=r"expected [^\n]{0,20}';'[^\n]{0,20}(before|at end of)",
        title="漏了分号 `;`",
        meaning=(
            "编译器说『我期待一个分号』。C 语言里每条语句结束都必须用英文分号 `;` 收尾，"
            "少一个，编译器就读不懂句子在哪结束。"
        ),
        causes=[
            "这一行（或上一行）结尾没写 `;`",
            "写成了中文分号 `；`",
            "括号里的语句漏了分号（常见于 `for` 循环里的 `i++` 后）",
        ],
        fixes=[
            "找到报错行和它的上一行，在语句结尾补 `;`",
            "把中文分号换成英文分号",
            "注意 `#define` 宏定义行末尾不要加分号",
        ],
        example_bad="int a = 10\nprintf(\"%d\", a);",
        example_good="int a = 10;\nprintf(\"%d\", a);",
        tip="报错说 before 'return'，真正的错往往在 return 的上一行末尾。",
    ),
    Entry(
        id="c.brace", lang="c",
        pattern=r"expected declaration or statement at end of input|expected '\}' at end of input",
        title="大括号 `{ }` 没配对",
        meaning=(
            "编译器一直读到了文件末尾，还在等一个 `}`。说明你少写了右大括号，"
            "或者多写了一个左大括号。"
        ),
        causes=[
            "函数 / if / for 的 `{` 忘了配对的 `}`",
            "从别处复制代码时括号没复制全",
            "注释没闭合 `/*` 少 `*/`，把后面的代码全吃掉了",
        ],
        fixes=[
            "从最后一个函数开始，逐层往回数括号",
            "编辑器里用括号高亮/折叠功能快速定位",
            "检查有没有 `/*` 没配对的 `*/`",
        ],
        example_bad="int main() {\n    if (1) {\n        printf(\"hi\");\n    return 0;\n}",
        example_good="int main() {\n    if (1) {\n        printf(\"hi\");\n    }\n    return 0;\n}",
        tip="写代码时先把 `{}` 一次性敲完，再往中间填内容。",
    ),
    Entry(
        id="c.undeclared", lang="c",
        pattern=r"'(?P<name>[A-Za-z_]\w*)' (undeclared|was not declared in this scope)",
        title="用了没声明的变量",
        meaning=(
            "编译器说『我不认识 `{name}` 这个东西』。在 C 里，所有变量必须先声明类型才能用。"
        ),
        causes=[
            "变量名拼错",
            "变量声明在另一个函数里（C 的变量不跨函数可见）",
            "忘了写类型，如直接写 `a = 10;`",
            "用了中文变量名或全角字母",
        ],
        fixes=[
            "在使用前声明类型：`int a = 10;`",
            "检查拼写和大小写",
            "需要跨函数共用的变量，定义在函数外或用参数传",
        ],
        example_bad="int main() {\n    a = 10;\n    return 0;\n}",
        example_good="int main() {\n    int a = 10;\n    return 0;\n}",
        tip="C 和 Python 最大的区别之一：C 必须先声明类型。",
    ),
    Entry(
        id="c.implicit_decl", lang="c",
        pattern=r"implicit declaration of function '(?P<name>[^']+)'",
        title="函数没声明就用了（缺少头文件）",
        meaning=(
            "编译器说『`{name}` 这个函数我没见过声明，但我先猜它是 int 返回值』。"
            "这在 C99 之后是错误，通常是你少 `#include` 了对应头文件。"
        ),
        causes=[
            "用了 printf/scanf 但没 `#include <stdio.h>`",
            "用了 strlen/strcpy 但没 `#include <string.h>`",
            "用了 sqrt/pow 但没 `#include <math.h>`",
            "自己写的函数定义在使用之后，且没有前置声明",
        ],
        fixes=[
            "按功能补头文件：输入输出 -> stdio.h；字符串 -> string.h；数学 -> math.h；malloc -> stdlib.h",
            "自定义函数：在使用前先写一行声明（原型），或把定义挪到前面",
        ],
        example_bad="int main() {\n    printf(\"hi\");\n    return 0;\n}",
        example_good="#include <stdio.h>\nint main() {\n    printf(\"hi\");\n    return 0;\n}",
        tip="`#include` 相当于告诉编译器「这个函数长什么样」。",
    ),
    Entry(
        id="c.format", lang="c",
        pattern=r"format '(?P<fmt>%[\w\.\-]*[a-zA-Z])' expects argument of type '(?P<want>[^']+)', "
                r"but argument \d+ has type '(?P<got>[^']+)'",
        title="printf/scanf 的格式符和变量类型不匹配",
        meaning=(
            "格式符 `{fmt}` 要求的类型是 `{want}`，但你给的变量是 `{got}`。"
            "printf 里的 `%d` 就是「请用整数格式打印」，给个浮点数就会打印出乱码。"
        ),
        causes=[
            "int 用了 `%f`，或 float/double 用了 `%d`",
            "long 用了 `%d`（应该是 `%ld`）",
            "字符串用了 `%c`（单字符）而不是 `%s`",
        ],
        fixes=[
            "int -> `%d`；float/double -> `%f` 或 `%.2f`；char -> `%c`；字符串 -> `%s`；long -> `%ld`",
            "scanf 里 double 必须用 `%lf`",
        ],
        example_bad='float x = 3.5;\nprintf("%d", x);',
        example_good='float x = 3.5;\nprintf("%f", x);',
        tip="scanf 用错格式符，程序可能不报错但读进来的是垃圾值，非常难查。",
    ),
    Entry(
        id="c.stray", lang="c",
        pattern=r"stray '\\\d+' in program",
        title="代码里混进了中文全角符号",
        meaning=(
            "编译器的意思其实是「这里出现了非 ASCII 字符」。"
            "最常见的原因是：括号、逗号、分号、引号写成了中文全角的。"
        ),
        causes=[
            "输入法没切成英文就敲了 `（ ） ， ； \" \"`",
            "从网页/文档里复制代码，带了不可见字符",
        ],
        fixes=[
            "把报错位置附近的标点全部删掉重新用英文打一遍",
            "搜索文件里的中文标点：`（ ） ， ； ：`",
        ],
        example_bad='printf（"hi"）；',
        example_good='printf("hi");',
        tip="写代码前先按 Shift 把输入法切到英文，能省掉一半的报错。",
    ),
    Entry(
        id="c.expected_expression", lang="c",
        pattern=r"expected expression before|expected '\)' before|expected ';' before '\}'",
        title="表达式或括号写残了",
        meaning=(
            "编译器读到某个位置，发现该有个完整的表达式/右括号，但没找到。"
            "通常是这一行或上一行的括号、逗号没写对。"
        ),
        causes=[
            "多写或漏写逗号、括号",
            "`if (a = 1)` 之类把 `==` 写成了 `=`（不报错但逻辑错）",
            "函数调用参数之间漏逗号",
        ],
        fixes=[
            "盯住报错行与上一行，逐个检查括号与逗号",
            "比较运算用 `==`，赋值用 `=`",
        ],
        example_bad="if (a > 1 {\n    printf(\"big\");\n}",
        example_good="if (a > 1) {\n    printf(\"big\");\n}",
        tip="",
    ),
    Entry(
        id="c.lvalue", lang="c",
        pattern=r"lvalue required as left operand of assignment",
        title="等号左边不是一个「能装东西的变量」",
        meaning=(
            "lvalue 指「可以被赋值的左值」。你写了类似 `5 = a` 或 `a + 1 = 3` 这种，"
            "左边不是变量而是算式，没法往里存值。"
        ),
        causes=[
            "把 `==` 写成 `=`，或反过来写反了顺序",
            "对表达式赋值：`(a + b) = 3`",
            "想比较却用了赋值",
        ],
        fixes=[
            "赋值方向是「变量 = 值」",
            "判断相等用 `==`",
        ],
        example_bad="if (a + b = 10)",
        example_good="if (a + b == 10)",
        tip="",
    ),
    Entry(
        id="c.segv", lang="c",
        pattern=r"(Segmentation fault|segmentation fault|SIGSEGV|access violation|0xC0000005)",
        title="程序访问了不该访问的内存（段错误）",
        meaning=(
            "Segmentation fault『段错误』。程序想去读写一块不属于它的内存，"
            "操作系统直接把它掐掉了。C 语言里这是最常见的崩溃，但报错信息最少，需要自己定位。"
        ),
        causes=[
            "数组下标越界（`int a[5]; a[5] = 1;`）",
            "用了没初始化的指针，或对 NULL 指针解引用",
            "scanf 忘了加 `&`：`scanf(\"%d\", n)`",
            "字符串没留 `\\0` 的位置：`char s[3] = \"abc\";`",
            "`malloc` 之后没检查是否返回 NULL",
            "函数返回了局部数组的地址",
        ],
        fixes=[
            "数组大小留余量，循环条件用 `< n` 而不是 `<= n`",
            "scanf 的变量前加 `&`（字符串数组名除外）",
            "指针使用前判空：`if (p != NULL)`",
            "字符串数组长度 = 字符数 + 1",
            "用 `-fsanitize=address -g`（GCC/Clang）重新编译，能精确指出越界的那一行",
        ],
        example_bad="int a[3];\nfor (int i = 0; i <= 3; i++) a[i] = i;",
        example_good="int a[3];\nfor (int i = 0; i < 3; i++) a[i] = i;",
        tip="段错误不告诉你行号，用 printf 分段打印是最土但最快的定位法。",
    ),
    Entry(
        id="c.undefined_ref", lang="c",
        pattern=r"undefined reference to ['`](?P<name>[^'`]+)['`]",
        title="链接阶段找不到函数的实现",
        meaning=(
            "编译通过了，但『链接』时找不到 `{name}` 的实现代码。"
            "说明你声明了它，却没有任何地方真正定义它。"
        ),
        causes=[
            "用了 math.h 的 sqrt 但编译时没加 `-lm`",
            "函数只写了声明，忘了写函数体",
            "多文件项目里，另一个 .c 文件没参与编译",
        ],
        fixes=[
            "数学库：编译加 `-lm`",
            "补上函数实现",
            "多文件：`gcc main.c util.c -o main` 把所有源文件都列上",
        ],
        example_bad="gcc main.c -o main   # 用了 sqrt 却没加 -lm",
        example_good="gcc main.c -o main -lm",
        tip="error 是「看不懂」，undefined reference 是「找不到东西」，两者阶段不同。",
    ),
    Entry(
        id="c.return_warning", lang="c",
        pattern=r"control reaches end of non-void function",
        title="函数承诺返回一个值，但可能什么都没返回",
        meaning=(
            "函数声明了返回类型（比如 int），但存在某条执行路径到最后都没有 `return`，"
            "调用者会拿到一个随机值。"
        ),
        causes=[
            "if/else 只在一个分支里写了 return",
            "循环结束后忘了 return",
        ],
        fixes=[
            "在每个分支末尾都 return",
            "函数末尾补一个兜底 `return 0;`",
        ],
        example_bad="int f(int x) {\n    if (x > 0) return 1;\n}",
        example_good="int f(int x) {\n    if (x > 0) return 1;\n    return 0;\n}",
        tip="",
    ),
    Entry(
        id="c.unused", lang="c",
        pattern=r"unused variable '(?P<name>[^']+)'|set but not used",
        title="声明了变量却没用（警告，不是错误）",
        meaning=(
            "warning『警告』，程序能正常运行，只是编译器提醒你：变量 `{name}` 定义了但一直没用。"
            "这常常意味着你「想用但写错了名字」。"
        ),
        causes=[
            "变量名拼错，导致真正想用的地方用了别的名字",
            "调试留下的无用变量",
        ],
        fixes=[
            "确认是不是想用这个变量但拼错了",
            "确实不需要就删掉",
        ],
        example_bad="int main() {\n    int count = 0;\n    return 0;\n}",
        example_good="int main() {\n    int count = 0;\n    printf(\"%d\", count);\n    return 0;\n}",
        tip="警告不阻止运行，但往往预告着真正的 bug，不要忽略。",
    ),
    Entry(
        id="c.too_few_args", lang="c",
        pattern=r"too few arguments to function|too many arguments to function",
        title="函数调用时参数个数不对",
        meaning="调用函数时给的参数个数，和函数定义要求的个数不一致。",
        causes=["漏传参数", "多传了参数"],
        fixes=["对照函数定义数一数参数", "检查是不是把逗号写错导致参数合并了"],
        example_bad="int add(int a, int b);\nadd(1);",
        example_good="int add(int a, int b);\nadd(1, 2);",
        tip="",
    ),
    Entry(
        id="c.incompatible", lang="c",
        pattern=r"incompatible (types|integer to pointer conversion)|makes (integer from pointer|pointer from integer)",
        title="类型不兼容（常见于指针和整数混用）",
        meaning=(
            "编译器说这两种类型不能直接互转。C 语言里最常见的是："
            "把整数当指针用，或者指针类型不匹配。"
        ),
        causes=[
            "给指针赋了整数，或反过来",
            "函数声明返回指针，但返回了整数",
            "scanf 里 `%d` 配了指针变量却没加 `&`",
        ],
        fixes=[
            "检查变量的声明类型",
            "指针就用 `*` 取内容、`&` 取地址",
            "确实需要强转时显式写 `(int *)p`",
        ],
        example_bad="int *p = 5;",
        example_good="int x = 5;\nint *p = &x;",
        tip="",
    ),
]


def _compile_index() -> list[tuple[re.Pattern, Entry]]:
    out = []
    for e in ENTRIES:
        try:
            out.append((re.compile(e.pattern, re.I), e))
        except re.error:
            continue
    return out


_INDEX = _compile_index()


def lookup(language: str, text: str) -> Entry | None:
    """在知识库里找最匹配的一条。"""
    lang = (language or "").lower()
    if lang in ("cpp", "c++"):
        lang = "c"
    for pattern, entry in _INDEX:
        if entry.lang != "any" and entry.lang != lang:
            continue
        if pattern.search(text or ""):
            return entry
    return None


def fill(entry: Entry, text: str) -> Entry:
    """把匹配到的名字（变量名/类型名）填进解释模板。"""
    if not entry or not text:
        return entry
    pattern = re.compile(entry.pattern, re.I)
    m = pattern.search(text)
    if not m:
        return entry
    groups = {k: (v or "") for k, v in m.groupdict().items()}

    def sub(s: str) -> str:
        for k, v in groups.items():
            s = s.replace("{" + k + "}", v)
        return s

    return Entry(
        id=entry.id, lang=entry.lang, pattern=entry.pattern,
        title=sub(entry.title), meaning=sub(entry.meaning),
        causes=[sub(c) for c in entry.causes],
        fixes=[sub(f) for f in entry.fixes],
        example_bad=entry.example_bad, example_good=entry.example_good,
        tip=entry.tip,
    )


def all_entries(lang: str = "") -> list[Entry]:
    if not lang:
        return list(ENTRIES)
    lang = "c" if lang in ("cpp", "c++") else lang
    return [e for e in ENTRIES if e.lang in (lang, "any")]
