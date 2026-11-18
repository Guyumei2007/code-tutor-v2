"""
报错英文术语词典：把报错信息里的每个英文单词翻成"人话"。

新手最大的障碍不是不会修，而是根本读不懂那串英文。
这里收录了 C / Python 报错中 95% 以上会出现的词汇。
"""

from __future__ import annotations

TERMS: dict[str, str] = {
    # ---------------- 错误大类 ----------------
    "Error": "错误（程序无法继续，必须修）",
    "error": "错误（程序无法继续，必须修）",
    "Exception": "异常（运行中出了意外状况）",
    "Warning": "警告（程序还能跑，但有隐患）",
    "warning": "警告（程序还能跑，但有隐患）",
    "fatal error": "致命错误（编译器直接放弃，一个文件都没生成）",
    "note": "提示（编译器的补充说明，不是错误本身）",
    "Traceback": "调用轨迹（Python 把「程序是怎么一路执行到这里」列出来）",
    "most recent call last": "最近的一次调用在最后（所以真正出错的原因在最下面几行）",

    # ---------------- Python 异常类型 ----------------
    "NameError": "名字错误：用了一个没定义过的变量名",
    "TypeError": "类型错误：把不匹配的类型放在一起用了（比如文字加数字）",
    "ValueError": "值错误：类型对了，但值的内容不合法",
    "SyntaxError": "语法错误：句子结构本身就不成立，程序根本没开始跑",
    "IndentationError": "缩进错误：行首空格数量不对",
    "TabError": "制表符错误：Tab 和空格混用了",
    "IndexError": "索引错误：下标超出范围（俗称越界）",
    "KeyError": "键错误：字典里没有这个键",
    "AttributeError": "属性错误：这个对象身上没有这个属性/方法",
    "ModuleNotFoundError": "模块未找到：要 import 的库没装或名字写错",
    "ImportError": "导入错误：导入失败",
    "ZeroDivisionError": "除以零错误：分母是 0",
    "RecursionError": "递归错误：函数自己调自己太多次，栈爆了",
    "FileNotFoundError": "文件未找到：路径上没有这个文件",
    "UnboundLocalError": "局部变量未绑定：函数里的变量还没赋值就被用了",
    "PermissionError": "权限错误：没有权限读写这个文件",
    "OverflowError": "溢出错误：数值大到装不下",
    "StopIteration": "迭代结束（正常现象，通常被 for 循环内部处理）",
    "KeyboardInterrupt": "键盘中断：你按了 Ctrl+C",
    "SystemExit": "程序退出",
    "AssertionError": "断言失败：assert 后面的条件不成立",
    "RuntimeError": "运行时错误",
    "MemoryError": "内存不足",

    # ---------------- 常见消息词 ----------------
    "name": "名字/变量名",
    "defined": "被定义过（赋值过）",
    "is not defined": "没有被定义 —— 这个变量前面从来没出现过",
    "cannot": "不能",
    "can only": "只能",
    "concatenate": "拼接（把两个字符串接起来）",
    "unsupported": "不支持的",
    "operand": "操作数（参与运算的那个东西）",
    "operand type": "操作数的类型",
    "for": "对于 / 用于",
    "and": "和",
    "type": "类型（整数 int / 小数 float / 文字 str / 真假 bool）",
    "object": "对象（一个具体的数据/东西）",
    "has no attribute": "没有这个属性",
    "attribute": "属性（对象身上的数据或方法）",
    "subscriptable": "可以用方括号取值的",
    "not subscriptable": "不能用方括号取值（它不是列表/字符串/字典）",
    "callable": "可以被调用的（后面能加括号执行）",
    "not callable": "不能被调用（它不是函数）",
    "iterable": "可迭代的（能用 for 挨个取）",
    "index": "索引 / 下标（从 0 开始数的位置编号）",
    "out of range": "超出范围",
    "list index out of range": "列表下标超出范围 —— 你要的那一格不存在",
    "key": "键（字典里用来查值的名字）",
    "value": "值",
    "invalid": "无效的 / 不合法的",
    "literal": "字面量（代码里直接写出来的值）",
    "invalid literal for int()": "这串文字无法转成整数",
    "with base 10": "按十进制解析（就是我们平常的数字写法）",
    "division": "除法",
    "by zero": "被零除",
    "maximum": "最大",
    "recursion": "递归",
    "depth": "深度",
    "exceeded": "超过了",
    "module": "模块（一个 .py 库文件）",
    "named": "名为",
    "missing": "缺少",
    "required": "必需的",
    "positional argument": "位置参数（按顺序传的参数）",
    "takes": "需要（接受）",
    "given": "实际给了",
    "unexpected": "意外的 / 没想到的",
    "indent": "缩进",
    "indented block": "缩进块（冒号下面必须缩进的那部分）",
    "unindent": "减少缩进",
    "does not match": "不匹配",
    "inconsistent use of tabs": "Tab 和空格的使用不一致",
    "eof": "文件结尾（file end of file）",
    "unterminated": "没有结束的（比如引号没闭合）",
    "string": "字符串（一串文字，用引号包起来）",
    "literal": "字面量",
    "character": "字符",
    "expected": "期望 / 这里本应该出现",
    "found": "发现了",
    "found but": "却发现",
    "empty": "空的",
    "null": "空值 None",
    "nonetype": "None 的类型（说明这个变量是空的）",
    "not supported between instances of": "这两种类型之间不支持这个比较",
    "between": "之间",
    "instances": "实例（具体的东西）",
    "argument": "参数（传给函数的东西）",
    "arguments": "参数们",
    "takes no arguments": "不需要传参数，但传了",
    "too many": "太多",
    "too few": "太少",
    "must be": "必须是",
    "cannot be": "不能是",
    "integer": "整数（1、2、3 这种不带小数点的）",
    "float": "浮点数（带小数点的数）",
    "int": "整数类型",
    "str": "字符串类型",
    "bool": "布尔类型（True / False）",
    "sequence": "序列（列表/字符串这种有序的）",
    "item": "元素",
    "assignment": "赋值（把右边的值放进左边的变量）",
    "before": "在……之前",
    "after": "在……之后",
    "during": "在……期间",
    "while": "在……的时候",
    "processing": "处理",
    "encoding": "编码（文字在电脑里的存储格式，中文常用 utf-8）",
    "utf-8": "UTF-8 编码（能正确存中文）",
    "gbk": "GBK 编码（Windows 中文默认编码，容易和 utf-8 冲突）",
    "codec": "编解码器",
    "can't decode": "无法解码（读文件时的编码不对）",
    "can't encode": "无法编码",
    "byte": "字节",
    "position": "位置",
    "path": "路径",
    "directory": "文件夹（目录）",
    "no such file or directory": "没有这个文件或文件夹",
    "permission denied": "没有权限（文件被占用，或需要管理员权限）",

    # ---------------- C / C++ ----------------
    "declaration": "声明（告诉编译器「有这么个东西」）",
    "statement": "语句（一条完整的指令）",
    "end of input": "输入结束（也就是文件结尾）",
    "at end of input": "在文件末尾 —— 说明有个括号一直没闭合",
    "undeclared": "未声明的（编译器不认识这个名字）",
    "was not declared in this scope": "在这个范围内没有声明过",
    "first use": "第一次使用",
    "implicit declaration of function": "函数的隐式声明 —— 你没声明就直接用了，通常是缺头文件",
    "implicit": "隐式的（编译器自己猜的）",
    "conflicting types for": "类型冲突",
    "previously declared here": "之前在这里声明过",
    "format": "格式（printf 里的 %d %f 这些占位符）",
    "expects": "要求 / 期望",
    "but argument": "但参数",
    "lvalue": "左值（能被赋值的东西，通常是一个变量）",
    "left operand": "左侧的操作数",
    "right operand": "右侧的操作数",
    "stray": "游离的 / 不该出现的",
    "in program": "在程序里",
    "incompatible": "不兼容的",
    "conversion": "转换",
    "integer to pointer": "整数转指针（类型不匹配）",
    "pointer": "指针（存地址的变量）",
    "undefined reference": "未定义的引用 —— 链接时找不到这个函数的实现",
    "undefined": "未定义的",
    "reference": "引用",
    "segmentation fault": "段错误 —— 程序访问了不属于它的内存，被系统强制终止",
    "segmentation": "分段",
    "fault": "错误/故障",
    "control reaches end of non-void function": "函数声明要返回值，但有分支没返回",
    "non-void": "非空（表示要返回值）",
    "void": "空（表示不返回值）",
    "unused variable": "声明了但没用的变量",
    "set but not used": "赋了值但没被读取",
    "used": "使用过",
    "macro": "宏（#define 定义的替换文本）",
    "header": "头文件（.h 文件，里面放着函数声明）",
    "include": "包含/引入",
    "linker": "链接器（把所有编译好的部分拼成程序）",
    "compilation": "编译",
    "compiler": "编译器",
    "warning: ": "警告：",
    "deprecated": "已废弃（不推荐再用）",
    "overflow": "溢出",
    "zero-length": "长度为零",
    "array": "数组",
    "subscript": "下标",
    "function": "函数",
    "return": "返回",
    "parameter": "形参（函数定义里写的）",
    "prototype": "函数原型（提前声明函数长什么样）",
    "cast": "类型转换",
    "sizeof": "占用的字节数",
    "NULL": "空指针（不指向任何地方）",
}


# 复合短语优先匹配（长的在前）
_COMPOUND = sorted(
    [(k, v) for k, v in TERMS.items() if " " in k],
    key=lambda x: -len(x[0]),
)


def term(word: str) -> str:
    """查一个词或短语的中文含义，查不到返回空字符串。"""
    if not word:
        return ""
    w = word.strip()

    # 1. 原样（大小写不敏感优先精确）
    if w in TERMS:
        return TERMS[w]
    low = w.lower()
    for k, v in TERMS.items():
        if k.lower() == low:
            return v

    # 2. 驼峰拆分，如 NameError -> Name + Error
    import re
    parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", w)
    if len(parts) > 1:
        subs = [TERMS.get(p) or TERMS.get(p.lower()) for p in parts]
        if all(subs):
            return " + ".join(subs)
        if subs and subs[-1]:
            return f"{w}（{' + '.join(p for p in parts)}）：{subs[-1]}"

    return ""


def translate_line(text: str) -> list[tuple[str, str]]:
    """把一整行报错里能认出来的词都翻出来，返回 [(英文, 中文), ...]。"""
    import re
    out: list[tuple[str, str]] = []
    used: set[str] = set()

    remaining = text
    # 先匹配复合短语
    for phrase, zh in _COMPOUND:
        m = re.search(re.escape(phrase), remaining, re.I)
        if m:
            out.append((m.group(0), zh))
            used.add(phrase.lower())

    # 再匹配单词
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", remaining):
        if token.lower() in used:
            continue
        zh = term(token)
        if zh:
            out.append((token, zh))
            used.add(token.lower())

    return out


def all_terms() -> dict[str, str]:
    return dict(TERMS)
