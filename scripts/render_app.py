#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把数据集注入页面模板，生成 docs/index.html。

只做一件事：把 assets/app-template.html 里那三个占位符换成真实数据。
不碰模板的其它任何字符——因为页面模板里同时住着「语音理解核心」和「无障碍样式」，
任何多余的改写都可能悄悄破坏它们。

三个占位符（都写成 `/*__X__*/ null` 的形式，这样即使忘记替换，
页面也只是数据为空并抛出明确错误，而不是语法错误）：

  /*__DATA__*/   菜单数据集（data/menu.json）
  /*__PRICE__*/  calculate-price 的真实返回快照（data/price_raw.json）
  /*__BUILD__*/  构建元信息（时间、数据来源）

用法： python scripts/render_app.py
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "assets" / "app-template.html"
OUT = ROOT / "docs" / "index.html"
DATA = ROOT / "data"

PLACEHOLDERS = ("/*__DATA__*/ null", "/*__PRICE__*/ null", "/*__BUILD__*/ null")


def inject(html: str, needle: str, payload: object) -> str:
    """替换占位符。找不到占位符就直接失败——静默跳过会让页面拿不到数据却还能打开。"""
    if needle not in html:
        raise SystemExit(f"模板里找不到占位符 {needle!r}，无法注入数据")
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # 防止数据里的 </script> 或 <!-- 提前结束脚本块
    text = text.replace("</", "<\\/").replace("<!--", "<\\!--")
    return html.replace(needle, text, 1)


def main() -> int:
    menu = json.loads((DATA / "menu.json").read_text(encoding="utf-8"))
    price = json.loads((DATA / "price_raw.json").read_text(encoding="utf-8"))

    src = menu["source"]
    build = {
        "generatedBy": "scripts/render_app.py",
        "generatedAt": "2026-10-15",
        "storeCode": src["storeCode"],
        "storeName": src["storeName"],
        "capturedAt": src["capturedAt"],
        "itemCount": len(menu["items"]),
        "categoryCount": len(menu["categories"]),
        "aliasCount": len(menu["aliases"]),
    }

    html = TEMPLATE.read_text(encoding="utf-8")
    html = inject(html, PLACEHOLDERS[0], menu)
    html = inject(html, PLACEHOLDERS[1], price.get("data"))
    html = inject(html, PLACEHOLDERS[2], build)

    leftovers = [p for p in PLACEHOLDERS if p in html]
    if leftovers:
        print("仍有未替换的占位符：", leftovers)
        return 2
    if "__DATA__" in html or "__PRICE__" in html or "__BUILD__" in html:
        print("页面里还残留占位符名字，请检查注入逻辑")
        return 2

    OUT.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" 是必须的：默认会把 \n 写成 \r\n，导致同一份模板在不同平台
    # 产出字节不一致，下游按 \n 写的自检正则就会失配。
    OUT.write_text(html, encoding="utf-8", newline="\n")

    print(f"docs/index.html 已生成（{len(html) / 1024:.1f} KB）")
    print(f"  门店：{build['storeName']}（{build['storeCode']}）")
    print(f"  数据：{build['itemCount']} 项餐品 / {build['categoryCount']} 个类目 / {build['aliasCount']} 条口语别名")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
