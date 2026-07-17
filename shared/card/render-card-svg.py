#!/usr/bin/env python3
"""render-card-svg.py — 业务层:生成小程序卡片封面 SVG(填好内容,交给系统层渲染发送)。

职责(业务逻辑,归 weight-loss-skill):
  - 读 USER.md 解析用户名字(用修好的正则,不误抓下一行标签)
  - 选封面模板(减脂档案 / 周报信封 中英)
  - 往模板里填标题文字「{Name}的减脂档案」+ 样式(字体/字号/颜色/坐标)
  - 输出一份填好内容的完整 SVG 到指定路径

不做(系统功能,归 backend send-miniprogram-card.mjs):
  - SVG→PNG 渲染、S3 上传、讯企发送

用法:
  python3 render-card-svg.py --workspace <workspace目录> --kind <profile|weekly> \
      --out <输出SVG路径> [--name <显式名字,覆盖USER.md>]

  --kind profile : 减脂档案卡(cover-base.svg + 「{Name}的减脂档案」标题)
  --kind weekly  : 周报信封卡(cover-weekly[-en].svg,按 USER.md Language 选中/英,静态无标题)

输出(stdout): 解析到的名字(供调用方拿去拼文案); 无名字则输出空行。
"""
import argparse
import os
import re
import sys

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def parse_name(user_md_path):
    """从 USER.md 解析 - **Name:** 后的名字。修好的正则:值不含换行、首字符非 `*`,
    Name 为空时返回 None(不误抓下一行的 `- **What to call them:**` 标签)。"""
    try:
        with open(user_md_path, encoding="utf-8") as f:
            md = f.read()
    except OSError:
        return None
    # 值捕获 [^\n*][^\n]*? : 首字符不能是换行或 *(排除空值/纯标签),后续不跨行
    for pat in (
        r"^[ \t]*[-*]?[ \t]*\*\*(?:Name|姓名|名字)[：:]\*\*[ \t]*([^\n*][^\n]*?)[ \t]*$",
        r"^[ \t]*[-*]?[ \t]*(?:Name|姓名|名字)[：:][ \t]*([^\n*][^\n]*?)[ \t]*$",
    ):
        m = re.search(pat, md, re.IGNORECASE | re.MULTILINE)
        if m:
            v = m.group(1).strip()
            # 占位符(破折号/未填)视为无名字
            if v and not re.fullmatch(r"[-–—‒―\s]+", v) and v.lower() not in ("未填写", "未知", "无", "n/a", "na", "none", "tbd"):
                return v
    return None


def parse_lang(user_md_path):
    """从 USER.md 解析 Language,返回 'en' 或 'zh'(默认 zh)。"""
    try:
        with open(user_md_path, encoding="utf-8") as f:
            md = f.read()
    except OSError:
        return "zh"
    m = re.search(r"Language[：:]\s*\**\s*([A-Za-z-]+)", md, re.IGNORECASE)
    if m:
        return "en" if not m.group(1).lower().startswith("zh") else "zh"
    return "zh"


def build_profile_svg(name):
    """减脂档案卡:读 cover-base.svg,插入标题「{name}的减脂档案」。"""
    with open(os.path.join(TEMPLATES_DIR, "cover-base.svg"), encoding="utf-8") as f:
        svg = f.read()
    # XML 转义,防止名字里的特殊字符破坏 SVG
    safe = (name or "我").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    title = (
        f'<text x="250" y="42" text-anchor="middle" '
        f'font-family="PingFang SC, Microsoft YaHei, sans-serif" '
        f'font-size="22" font-weight="700" fill="#1a1a1a">{safe}的减脂档案</text>'
    )
    return svg.replace("</svg>", title + "</svg>")


def build_weekly_svg(lang):
    """周报信封卡:按语言选中/英信封模板,静态无需填字。"""
    fn = "cover-weekly-en.svg" if lang == "en" else "cover-weekly.svg"
    path = os.path.join(TEMPLATES_DIR, fn)
    if not os.path.exists(path):  # 英文缺失兜底中文
        path = os.path.join(TEMPLATES_DIR, "cover-weekly.svg")
    with open(path, encoding="utf-8") as f:
        return f.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True, help="用户 workspace 目录(含 USER.md)")
    ap.add_argument("--kind", required=True, choices=["profile", "weekly"])
    ap.add_argument("--out", required=True, help="输出 SVG 文件路径")
    ap.add_argument("--name", default=None, help="显式名字(覆盖 USER.md 解析)")
    args = ap.parse_args()

    user_md = os.path.join(args.workspace, "USER.md")
    name = args.name or parse_name(user_md)

    if args.kind == "profile":
        svg = build_profile_svg(name)
    else:
        svg = build_weekly_svg(parse_lang(user_md))

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(svg)

    # stdout 回传 "名字<TAB>语言",供调用方拼文案/选标题(名字无则空)。
    lang = parse_lang(user_md)
    print(f"{name or ''}\t{lang}")


if __name__ == "__main__":
    main()
