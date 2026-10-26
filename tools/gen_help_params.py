# -*- coding: utf-8 -*-
"""把 llama_params.PARAM_DEFINITIONS 渲染为 help/index.html 的「全参数参考」段落。

开发期脚本，不参与运行时。用法:
    python tools/gen_help_params.py

在 help/index.html 的 <!-- PARAMS:BEGIN --> 与 <!-- PARAMS:END --> 标记之间
生成静态参数卡片（按分组，含 flag/别名/类型/默认值/可选值/中文含义，以及
llama_params.PARAM_PLAIN 里的一句话「白话解说」），可重复运行，幂等替换。
参数表或白话解说更新后重跑一次即可刷新站点内容；缺白话解的参数会逐条告警。
"""

import html
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent          # llama-shell/tools
PROJ_DIR = SCRIPT_DIR.parent                          # llama-shell
sys.path.insert(0, str(PROJ_DIR))

from llama_params import PARAM_DEFINITIONS, PARAM_GROUP_LABELS, PARAM_PLAIN  # noqa: E402

HELP_PATH = PROJ_DIR / "help" / "index.html"
BEGIN = "<!-- PARAMS:BEGIN -->"
END = "<!-- PARAMS:END -->"

TYPE_LABELS = {
    "int": "整数",
    "float": "小数",
    "str": "字符串",
    "choice": "枚举",
    "bool": "开关",
    "bool_pair": "开关(三态)",
    "path": "路径",
}


def fmt_default(p) -> str:
    if p.default is None:
        return "未设置"
    if p.default is True:
        return "true"
    if p.default is False:
        return "false"
    if p.default == "":
        return "(空)"
    return str(p.default)


def render_param(p) -> str:
    """单个参数 → 一张卡片"""
    flags = [p.cli_flag]
    if p.cli_short:
        flags.append(p.cli_short)
    alias_parts = []
    if p.negation_flag:
        alias_parts.append(p.negation_flag)
    if p.alias:
        alias_parts.append(p.alias)
    alias_parts.extend(f for f in p.extra_flags if f not in alias_parts)

    # 多个 flag 之间用显式分隔符, 避免相邻连读(如 "--cache-list -cl" 被看成 "--cache-list-cl")
    flag_html = ' <span class="fsep">·</span> '.join(
        f'<span class="flag">{html.escape(f)}</span>' for f in flags)
    alias_html = ""
    if alias_parts:
        alias_html = f'<span class="palias">等价写法: {" · ".join(html.escape(a) for a in alias_parts)}</span>'
    label_html = f'<span class="plabel">{html.escape(p.label)}</span>' if p.label else ""

    meta = [f'<span class="badge type">{TYPE_LABELS.get(p.type, p.type)}</span>',
            f'<span class="badge default">默认: {html.escape(fmt_default(p))}</span>']
    if p.type == "choice" and p.choices:
        vals = " / ".join(c if c else "(空)" for c in p.choices)
        meta.append(f'<span class="badge choice">可选值: {html.escape(vals)}</span>')

    desc = f'<div class="pdesc">{html.escape(p.help_text)}</div>' if p.help_text else ""
    plain = PARAM_PLAIN.get(p.key, "").strip()
    plain_html = (f'<div class="pplain"><b>白话解说</b>{html.escape(plain)}</div>'
                  if plain else "")
    return (
        '<div class="pcard">\n'
        f'  <div class="pflag">{flag_html}{alias_html}{label_html}</div>\n'
        f'  <div class="pmeta">{"".join(meta)}</div>\n'
        f'  {desc}\n'
        f'  {plain_html}\n'
        '</div>'
    )


def render_section() -> str:
    """全部分组 → 组导航 + 分组卡片"""
    groups: dict[str, list] = {}
    for p in PARAM_DEFINITIONS:
        groups.setdefault(p.group, []).append(p)

    # 分组顺序: 先按 PARAM_GROUP_LABELS, 再补其余分组(如 Auto)
    order = [g for g in PARAM_GROUP_LABELS if g in groups]
    order += [g for g in groups if g not in PARAM_GROUP_LABELS]

    chips = "".join(
        f'<a href="#pgrp-{html.escape(g.lower())}">{html.escape(PARAM_GROUP_LABELS.get(g, g))}</a>'
        for g in order
    )
    body = [f'<div class="group-nav">{chips}</div>']
    for g in order:
        label = PARAM_GROUP_LABELS.get(g, g)
        cards = "\n".join(render_param(p) for p in groups[g])
        body.append(
            f'<div class="param-group" id="pgrp-{html.escape(g.lower())}">\n'
            f'  <h3>{html.escape(label)}（{g} · {len(groups[g])} 项）</h3>\n'
            f'{cards}\n'
            '</div>'
        )
    total = len(PARAM_DEFINITIONS)
    body.append(f'<p class="gen-note">共收录 {total} 个参数。数值范围未逐一注明的，以 <code>llama-server --help</code> 输出为准。</p>')
    return "\n".join(body)


def main() -> None:
    text = HELP_PATH.read_text(encoding="utf-8")
    i, j = text.find(BEGIN), text.find(END)
    if i < 0 or j < 0 or j < i:
        raise SystemExit(f"错误: {HELP_PATH} 中找不到 {BEGIN}...{END} 标记")
    missing = [p.cli_flag for p in PARAM_DEFINITIONS if not PARAM_PLAIN.get(p.key, "").strip()]
    if missing:
        print(f"警告: {len(missing)} 个参数缺「白话解说」(PARAM_PLAIN)，卡片将不含该段：\n  "
              + ", ".join(missing), file=sys.stderr)
    new_text = text[: i + len(BEGIN)] + "\n" + render_section() + "\n" + text[j:]
    HELP_PATH.write_text(new_text, encoding="utf-8")
    print(f"OK: 已写入 {HELP_PATH}（共 {len(PARAM_DEFINITIONS)} 个参数）")


if __name__ == "__main__":
    main()
