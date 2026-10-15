#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「汉字 → 拼音」映射表（data/pinyin_table.json）。

为什么需要它：
    语音识别错字时，错的多半是**同音字**：「巨无霸」听成「巨无八」、
    「薯条」听成「数条」、「板烧」听成「板骚」。靠一张手写的误听纠正表
    只能覆盖我们想得到的那几十种；拼音层能覆盖**所有**同音错法——
    只要读音一样，就还能找回来。

覆盖范围为什么是 GB2312 一级汉字（3755 字）：
    第一版只收了「菜单里出现过的字」，结果最该救的那些情况反而救不了
    ——「可乐」被听成「渴乐」时，「渴」压根不在菜单里，于是查不到读音、
    整层失效。错字天生是菜单外的字，所以表必须比菜单宽。
    GB2312 一级汉字覆盖日常用字的 99.7%，用 Python 自带的 gb2312 编解码器
    就能枚举出来，不需要额外的字表文件。

为什么体积不是问题：
    按**音节分组**存（`{"ke": "可渴克刻客课…"}`），400 多个音节、
    3755 个汉字，整张表约 15 KB —— 比逐字存节省四倍多。
    页面启动时展开成「字→音节」再查，展开是一次性的。

为什么运行时不用第三方库：
    页面要「clone 下来就能跑」，不能因为一个辅助功能就塞进一个拼音库。
    所以拼音在**构建时**算好随仓库提交，页面只查表。

依赖：pypinyin（只在跑这个脚本时需要，运行本项目不需要）
用法： python scripts/build_pinyin.py
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "pinyin_table.json"

sys.path.insert(0, str(ROOT / "scripts"))

# GB2312 一级汉字：区 16–55（首字节 0xB0–0xD7），位 1–94（次字节 0xA1–0xFE）
GB2312_L1_HI = range(0xB0, 0xD8)
GB2312_L1_LO = range(0xA1, 0xFF)


def gb2312_level1() -> list[str]:
    """用标准库的 gb2312 编解码器枚举一级汉字。"""
    chars: list[str] = []
    for hi in GB2312_L1_HI:
        for lo in GB2312_L1_LO:
            try:
                ch = bytes([hi, lo]).decode("gb2312")
            except UnicodeDecodeError:
                continue
            if "\u4e00" <= ch <= "\u9fff":
                chars.append(ch)
    return chars


def menu_chars() -> list[str]:
    """菜单/别名/误听表/门店名里出现的汉字，作为一级汉字之外的补充。"""
    from build_menu import HOMOPHONE_FIXES, SPOKEN_ALIASES      # noqa: E402

    raw = json.loads((DATA / "menu_raw.json").read_text(encoding="utf-8"))
    stores = json.loads((DATA / "stores_raw.json").read_text(encoding="utf-8"))
    strings: list[str] = []
    strings.extend(m.get("name", "") for m in raw["meals"].values())
    strings.extend(SPOKEN_ALIASES.keys())
    strings.extend(HOMOPHONE_FIXES.keys())
    strings.extend(HOMOPHONE_FIXES.values())
    strings.extend(s.get("storeName", "") for s in stores["stores"])
    strings.extend(["麦当劳", "餐厅", "门店", "点餐", "可乐", "薯条", "汉堡", "套餐",
                    "堂食", "外带", "打包", "现金", "找零", "柜台", "取餐", "排号"])
    out: list[str] = []
    for s in strings:
        out.extend(ch for ch in s if "\u4e00" <= ch <= "\u9fff")
    return out


def build() -> tuple[dict[str, str], list[str]]:
    try:
        from pypinyin import Style, pinyin
    except ImportError:
        print("缺少 pypinyin。请先安装（只在生成拼音表时需要）：")
        print("  pip install pypinyin")
        return {}, []

    chars = sorted(set(gb2312_level1()) | set(menu_chars()))
    table: dict[str, str] = {}
    missing: list[str] = []
    for ch in chars:
        if ch in table:
            continue
        got = pinyin(ch, style=Style.NORMAL)
        py = got[0][0] if got and got[0] else ""
        if not py or not py.isascii():
            missing.append(ch)
            continue
        table[ch] = py

    # 缺拼音会静默削弱容错能力，所以直接失败，不要生成一张有洞的表
    if missing:
        print("这些汉字没能取到拼音：" + "".join(missing[:40])
              + ("…" if len(missing) > 40 else ""))
        return {}, []
    return table, chars


def group_by_syllable(table: dict[str, str]) -> dict[str, str]:
    """按音节把汉字归组，体积比逐字存小四倍多。"""
    grouped: dict[str, list[str]] = {}
    for ch, py in table.items():
        grouped.setdefault(py, []).append(ch)
    return {py: "".join(sorted(chars)) for py, chars in sorted(grouped.items())}


def main() -> int:
    table, _chars = build()
    if not table:
        return 2

    grouped = group_by_syllable(table)
    OUT.write_text(json.dumps(
        {
            "note": "汉字→拼音，按音节分组存放（音节 -> 该读音的汉字串）。"
                    "覆盖 GB2312 一级汉字 + 菜单/别名/门店名用到的字。"
                    "由 scripts/build_pinyin.py 用 pypinyin 生成；"
                    "页面启动时展开成「字->音节」再查，运行时零依赖。",
            "charCount": len(table),
            "syllableCount": len(grouped),
            "syllables": grouped,
        },
        ensure_ascii=False, indent=1, sort_keys=False) + "\n",
        encoding="utf-8", newline="\n")

    size_kb = OUT.stat().st_size / 1024
    print(f"pinyin_table.json 已生成：{len(table)} 个汉字 / {len(grouped)} 个音节 / {size_kb:.1f} KB")

    # 抽样自检：既查菜单里的字，也查典型的「错字」——那些字才是这层存在的理由
    probes = {"巨": "ju", "薯": "shu", "板": "ban", "可": "ke",
              "渴": "ke", "书": "shu", "勺": "shao", "必": "bi", "封": "feng"}
    bad = [f"{c}应为{p}实为{table.get(c)}" for c, p in probes.items() if table.get(c) != p]
    if bad:
        print("抽样校验失败：" + "；".join(bad))
        return 2
    print("抽样校验通过（含错字本身：渴=ke、书=shu、勺=shao、必=bi、封=feng）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
