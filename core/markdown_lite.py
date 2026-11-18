"""极简 Markdown -> HTML 渲染器（零依赖），够用于本项目输出。"""

from __future__ import annotations

import html
import re


def _inline(text: str) -> str:
    # 先转义，再处理标记
    s = html.escape(text, quote=False)
    s = re.sub(r"`([^`]+)`", r'<code class="inline">\1</code>', s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" target="_blank">\1</a>', s)
    return s


def render(md: str) -> str:
    if not md:
        return ""

    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    in_list = None      # 'ul' / 'ol'
    in_table = False

    def close_list():
        nonlocal in_list
        if in_list:
            out.append(f"</{in_list}>")
            in_list = None

    def close_table():
        nonlocal in_table
        if in_table:
            out.append("</tbody></table>")
            in_table = False

    while i < n:
        line = lines[i]

        # 代码块
        if line.strip().startswith("```"):
            close_list(); close_table()
            lang = line.strip()[3:].strip()
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            cls = f' class="lang-{html.escape(lang)}"' if lang else ""
            out.append(
                f'<pre class="code"><code{cls}>'
                + html.escape("\n".join(buf))
                + "</code></pre>"
            )
            continue

        # 表格
        if line.strip().startswith("|") and line.strip().endswith("|"):
            close_list()
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            nxt = lines[i + 1] if i + 1 < n else ""
            if not in_table and re.match(r"^\s*\|[\s:\-\|]+\|\s*$", nxt):
                out.append('<table class="md-table"><thead><tr>')
                for c in cells:
                    out.append(f"<th>{_inline(c)}</th>")
                out.append("</tr></thead><tbody>")
                in_table = True
                i += 2
                continue
            if in_table:
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
                i += 1
                continue
            close_table()

        if in_table and not line.strip().startswith("|"):
            close_table()

        # 分割线
        if re.match(r"^\s*(-{3,}|\*{3,})\s*$", line):
            close_list(); close_table()
            out.append("<hr>")
            i += 1
            continue

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            close_list(); close_table()
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{_inline(m.group(2).strip())}</h{lvl}>")
            i += 1
            continue

        # 引用
        if line.strip().startswith(">"):
            close_list(); close_table()
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip()[1:].strip())
                i += 1
            out.append(f'<blockquote>{_inline(" ".join(buf))}</blockquote>')
            continue

        # 无序列表
        m = re.match(r"^\s*[-*+]\s+(.*)$", line)
        if m:
            close_table()
            if in_list != "ul":
                close_list()
                out.append("<ul>")
                in_list = "ul"
            out.append(f"<li>{_inline(m.group(1))}</li>")
            i += 1
            continue

        # 有序列表
        m = re.match(r"^\s*(\d+)[.)]\s+(.*)$", line)
        if m:
            close_table()
            if in_list != "ol":
                close_list()
                out.append("<ol>")
                in_list = "ol"
            out.append(f"<li>{_inline(m.group(2))}</li>")
            i += 1
            continue

        # 空行
        if not line.strip():
            close_list()
            i += 1
            continue

        # 普通段落
        close_list()
        buf = [line]
        i += 1
        while i < n and lines[i].strip() and not re.match(
            r"^\s*(#{1,6}\s|[-*+]\s|\d+[.)]\s|>|\||```|---)", lines[i]
        ):
            buf.append(lines[i])
            i += 1
        out.append(f"<p>{_inline(' '.join(x.strip() for x in buf))}</p>")

    close_list()
    close_table()
    return "\n".join(out)
