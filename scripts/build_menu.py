#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 MCP 原始快照整理成页面直接可用的数据集（data/menu.json）。

这个脚本做三件事：

1. **整理菜单**：把 query-meals 的 ``categories`` + ``meals`` 拍平成一个有序的
   ``items`` 数组，并补上所属分类（语音播报「这是汉堡类」比播报编码有用得多）。

2. **建口语别名表**（本项目最关键的一步）：视障用户不会照着屏幕念
   「龙焰芝士棒鸡腿堡三件套」，他们会说「巨无霸」「可乐」「薯条」。
   所以这里手工维护一份 ``SPOKEN_ALIASES``，把口语词映射到菜单编码。
   **构建时会校验每个别名指向的编码都真实存在于菜单里，写错就直接构建失败**——
   宁可当场报错，也不要让页面上出现一个永远匹配不到东西的别名。

3. **生成歧义提示**：同一个口语词可能对应多个条目（「巨无霸」→ 单点 / 三件套 / 四件套）。
   别名表保留全部候选，由页面的匹配器负责「听不懂就问一句」，脚本不擅自替用户选。

数据来源全部是 data/*_raw.json（MCP 真实返回），没有任何编造字段。

用法： python scripts/build_menu.py
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# ---------------------------------------------------------------------------
# 口语别名表：左边是用户可能说出口的词，右边是菜单里真实的商品编码。
#
# 维护原则：
#   * 只收「真实日常会说」的说法，不收我造出来的同义词
#   * 一个口语词可以对应多个编码（有歧义就让页面去问），但每个编码都必须存在
#   * 别名不做大小写/全半角以外的变形，匹配由页面的 matcher 负责
# ---------------------------------------------------------------------------
SPOKEN_ALIASES: dict[str, list[str]] = {
    # —— 汉堡 ——
    "巨无霸": ["1100", "9900005466", "9900000888"],
    "big mac": ["1100", "9900005466", "9900000888"],
    "双吉": ["1120", "9900005464"],
    "双层吉士": ["1120", "9900005464"],
    "吉士汉堡": ["1120", "9900005464", "9900005449"],
    "芝士汉堡": ["1120", "9900005464"],
    "板烧": ["1406", "9900005462", "9900000890"],
    "板烧鸡腿": ["1406", "9900005462", "9900000890"],
    "麦辣鸡腿堡": ["1440", "9900000891"],
    "麦辣鸡腿": ["1440", "9900000891"],
    "麦香鸡": ["1450", "9900005453"],
    "麦香鱼": ["1600", "9900005460"],
    "鳕鱼堡": ["9900003586", "9900005413", "9900000893"],
    "深海鳕鱼堡": ["9900003586", "9900005413", "9900000893"],
    "不素之霸": ["4648", "9900000886", "9900005411"],
    "安格斯": ["511781", "511782", "9900000881", "9900000882", "9900005428", "9900005430"],
    "厚牛堡": ["511781", "511782", "9900000881", "9900000882", "9900006957"],
    "培根蔬萃": ["1106", "9900005468", "9900000885"],
    "高达吉士": ["521316"],
    "龙焰鸡腿堡": ["521952", "9900016073", "9900016309"],
    "龙焰芝士棒鸡腿堡": ["521953", "9900016075", "9900016310"],
    "咸蛋黄鸡腿蛋月堡": ["521900", "9900015850"],
    "鸡腿堡": ["521952", "1406", "9900016073", "9900016075"],

    # —— 炸鸡 · 小食 ——
    "麦辣鸡翅": ["1700", "9900005451"],
    "鸡翅": ["1700", "9900005451"],
    "麦乐鸡": ["1401", "9900004835"],
    "鸡块": ["1401", "9900004835"],
    "那么大鸡排": ["521156"],
    "鸡排": ["521156"],
    "薄皮焦香v翅": ["520445"],
    "v翅": ["520445", "9900006332"],
    "脆汁鸡": ["9900005432", "9900016078"],
    "薯条": ["4810"],
    "中薯条": ["4810"],
    "大薯条": ["4810"],
    "玉米杯": ["4437"],
    "玉米": ["4437"],
    "苹果片": ["6102"],
    "拉丝芝士条": ["521986"],
    "芝士条": ["521986"],
    "鸡薯双全盒": ["9900016076"],
    "扭扭薯": ["521901"],
    "薯饼": ["9900016082"],

    # —— 饮品 ——
    "可乐": ["9900008751"],
    "雪碧": ["3010"],
    "芬达": ["3010"],
    "柠檬红茶": ["515280"],
    "红茶": ["515280", "3505"],
    "锡兰红茶": ["3505"],
    "纯悦": ["3757"],
    "矿泉水": ["3757"],
    "水": ["3757"],
    "牛奶": ["3755", "507394"],
    "纯牛奶": ["3755"],
    "燕麦奶": ["510488"],
    "苹果汁": ["6362"],
    "果汁": ["6362"],
    "咖啡": ["520689", "507411", "507387", "514851"],
    "美式": ["507411"],
    "卡布奇诺": ["507387"],
    "鲜萃咖啡": ["520689"],
    "奶铁": ["507409", "510479"],
    "黑巧": ["515946", "515955"],
    "柚桂茶": ["9900015852"],

    # —— 甜品 · 冰淇淋 ——
    "圆筒": ["515837"],
    "甜筒": ["515837"],
    "冰淇淋": ["515837", "9900008745", "9900008754"],
    "新地": ["9900008745"],
    "麦旋风": ["9900008754", "521950", "521917"],
    "派": ["9900008752", "521906", "521922"],
    "糍粑派": ["521906"],
    "大米派": ["521922"],

    # —— 蘸酱 ——
    "蘸酱": ["521966", "521797", "521799"],
    "牧场酱": ["521797"],
    "山葵酱": ["521799"],
    "芝士酱": ["521966"],

    # —— 套餐 · 随心配 ——
    # 注意：这里刻意不收「三件套」「四件套」「单人餐」这类**泛化规格词**。
    # 试过收录，结果是说「巨无霸三件套」时把「大堡口福三件套」也拉进了候选，
    # 页面于是反过来问用户「你要的是哪一个」，而用户刚刚说得很清楚。
    # 别名只收「指向明确商品」的口语说法。
    "随心配": ["9900013304", "9900015568"],
    "1+1": ["9900013304", "9900015568"],
    "大堡口福": ["9900015602"],
    "开心乐园餐": ["9900016057", "9900016056", "9900016058"],
    "儿童餐": ["9900016057", "9900016056", "9900016058"],
    "亲子双人餐": ["9900016055"],
    "四宫格": ["9900015730", "9900015729", "9900005321"],
    "小食拼盘": ["9900015730", "9900015729", "9900006332"],
    "双拼十翅": ["9900006332"],
    "四件套随心选": ["9900004236", "9900010110", "9900006957"],
}

# 语音识别常见的同音/近音误听，提前纠正，省得用户重复说三遍。
# 只收「确定是误听」的，不收「可能是别的意思」的。
HOMOPHONE_FIXES: dict[str, str] = {
    "巨无八": "巨无霸",
    "巨无吧": "巨无霸",
    "巨无爸": "巨无霸",
    "聚无霸": "巨无霸",
    "麦辣鸡翅": "麦辣鸡翅",
    "麦拉鸡翅": "麦辣鸡翅",
    "麦乐机": "麦乐鸡",
    "麦勒鸡": "麦乐鸡",
    "薯条二": "薯条",
    "数条": "薯条",
    "暑条": "薯条",
    "可乐二": "可乐",
    "卡了": "可乐",
    "可了": "可乐",
    "双极": "双吉",
    "板骚": "板烧",
    "麦香于": "麦香鱼",
    "麦香雨": "麦香鱼",
    "雪壁": "雪碧",
    "雪比": "雪碧",
    "圆桶": "圆筒",
    "甜桶": "圆筒",
    "安格丝": "安格斯",
    "安格司": "安格斯",
    "玉米杯二": "玉米杯",
    "鸡块二": "鸡块",
    "咖啡二": "咖啡",
}


COUPON_LIMITATION = (
    "关键限制：query-my-coupons 与 available-coupons 的返回里都没有 couponId / couponCode，"
    "而 calculate-price 与 create-order 套券必须传这两个字段。也就是说，纯 MCP 链路目前"
    "拿不到可回填的券 ID。本项目因此只做到「语音播报有哪些券、什么时段能用」，"
    "不假装能自动套券。"
)


def load_raw(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def load_pinyin_table() -> dict[str, str]:
    """读取「音节 → 汉字串」的拼音表（data/pinyin_table.json）。

    表是可选的：没有它页面照样能跑，只是少一层同音容错。
    所以这里读取失败不报错、返回空表，由页面自行降级。
    """
    path = DATA / "pinyin_table.json"
    if not path.exists():
        print("提示：没有 data/pinyin_table.json，同音容错会降级。"
              "可执行 python scripts/build_pinyin.py 生成（需要 pypinyin）。")
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("syllables") or {}


def expand_pinyin(grouped: dict[str, str]) -> dict[str, str]:
    """把「音节 → 汉字串」展开成「字 → 音节」，供构建期校验使用。"""
    flat: dict[str, str] = {}
    for syllable, chars in grouped.items():
        for ch in chars:
            flat[ch] = syllable
    return flat


def to_pinyin(name: str, flat: dict[str, str]) -> str:
    """把商品名转成拼音串（表外字符原样保留，便于两侧同字仍能对上）。"""
    return "".join((flat.get(ch) or ch) for ch in name)


def check_homophone_collisions(items: list[dict], flat: dict[str, str]) -> list[str]:
    """找出「拼音完全相同但商品不同」的项。

    这不是错误——菜单里本来就有同音不同字的商品（用户说出来也确实会混），
    但值得在构建时打印出来，因为这类商品必须靠字面层或追问来区分，
    不能指望拼音层。漏掉这行日志，就会出现「怎么老是问我是哪一个」而找不到原因。
    """
    if not flat:
        return []
    groups: dict[str, list[str]] = {}
    for it in items:
        py = to_pinyin(it["name"], flat)
        if py:
            groups.setdefault(py, []).append(it["name"])
    return [f"{py} → {names}" for py, names in groups.items() if len(names) > 1]


def build_aliases(codes: set[str]) -> dict[str, list[str]]:
    """校验并返回口语别名表。任何指向不存在编码的别名都视为构建错误。

    这条校验是刻意的：别名表是手工维护的，写错一个编码就会让某句话永远匹配不到东西，
    而页面上的表现只是「说了没反应」。宁可在构建时就报错失败。
    """
    problems: list[str] = []
    clean: dict[str, list[str]] = {}
    for alias, targets in SPOKEN_ALIASES.items():
        valid = []
        for code in targets:
            if code in codes:
                valid.append(code)
            else:
                problems.append(f"别名「{alias}」指向了菜单里不存在的编码 {code}")
        if valid:
            clean[alias] = valid
    if problems:
        print("口语别名表校验失败：")
        for p in problems:
            print("  ·", p)
        raise SystemExit(2)
    return clean


def assemble(menu_payload: dict, stores: list[dict], coupons: list[dict],
             captured_at: str = "2026-10-15", menus_raw: dict | None = None) -> dict:
    """把 MCP 的原始返回整理成页面直接可用的数据集。

    这个函数被两处调用，**必须是唯一的整理逻辑**：
      * build_menu.py 的 main()——从仓库里的快照生成 data/menu.json
      * serve.py ——把用户自己账号的实时返回整理成同样的结构
    两处若各写一份，本地模式和演示模式的语音匹配行为就会悄悄分叉。
    """
    meals: dict[str, dict] = menu_payload["meals"]
    codes = set(meals)

    # code -> 分类名（一个编码可能出现在多个分类里，取第一个，贴近菜单展示顺序）
    category_of: dict[str, str] = {}
    for cat in menu_payload["categories"]:
        for code in cat["meals"]:
            category_of.setdefault(code, cat["name"])

    items = []
    for code, meal in meals.items():
        price = meal.get("currentPrice")
        original = meal.get("originalPrice")
        items.append({
            "code": code,
            "name": meal["name"],
            "category": category_of.get(code, "其他"),
            "price": float(price) if price is not None else None,
            "originalPrice": float(original) if original is not None else None,
            "discountType": meal.get("discountType"),
            "canWithOrder": bool(meal.get("canWithOrder")),
        })

    # 展示顺序按菜单类目走，方便「翻菜单」这类语音指令按顺序播报
    order_index: dict[str, int] = {}
    for i, cat in enumerate(menu_payload["categories"]):
        for code in cat["meals"]:
            order_index.setdefault(code, i * 1000 + len(order_index))
    items.sort(key=lambda it: order_index.get(it["code"], 10 ** 6))

    aliases = build_aliases(codes)
    store = next(s for s in stores if s["storeCode"] == menu_payload["arguments"]["storeCode"])
    pinyin_table = load_pinyin_table()

    return {
        "source": {
            "storeCode": store["storeCode"],
            "storeName": store["storeName"],
            "address": store["address"],
            "distance": store["distance"],
            "orderType": menu_payload["arguments"]["orderType"],
            "beType": menu_payload["arguments"]["beType"],
            "tools": ["query-nearby-stores", "query-meals"],
            "capturedAt": captured_at,
            "note": "演示快照：菜单与价格来自上面的门店在采样时刻的真实返回。"
                    "演示模式下小计按菜单现价相加，未调 calculate-price，因此不含优惠与打包费。",
        },
        "stores": stores,
        "categories": [{"name": c["name"], "meals": c["meals"]} for c in menu_payload["categories"]],
        "items": items,
        "aliases": aliases,
        "homophones": HOMOPHONE_FIXES,
        # 音节 → 汉字串（「ke」→「可渴克刻客课…」）。页面把它展开成
        # 「字 → 音节」后，就能把「用户说的」和「菜单里的」都转成读音再比一次，
        # 这样语音识别把字听错成同音字时还能找回来。
        # 按音节存而不是按字存，是为了把这张 3700 多字的表压到 16 KB。
        "pinyin": pinyin_table,
        "frequent": menu_payload.get("frequent"),
        "coupons": coupons,
        "couponLimitation": COUPON_LIMITATION,
    }


def main() -> int:
    menu_raw = load_raw("menu_raw.json")
    stores_raw = load_raw("stores_raw.json")
    coupons_raw = load_raw("coupons_raw.json")

    payload = assemble(menu_raw, stores_raw["stores"], coupons_raw["coupons"])

    out = DATA / "menu.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8", newline="\n")

    items = payload["items"]
    store = payload["source"]
    with_price = sum(1 for it in items if it["price"] is not None)
    flat = expand_pinyin(payload["pinyin"])
    print(f"menu.json 已生成：{len(items)} 项餐品（{with_price} 项有价格）、"
          f"{len(payload['categories'])} 个类目、{len(payload['aliases'])} 条口语别名、"
          f"{len(payload['homophones'])} 条误听纠正、{len(payload['coupons'])} 张券、"
          f"{len(flat)} 个拼音字")
    print(f"采样门店：{store['storeName']}（{store['storeCode']}）")

    collisions = check_homophone_collisions(items, flat)
    if collisions:
        print(f"同音商品 {len(collisions)} 组（拼音层无法区分，靠字面层或追问）：")
        for line in collisions:
            print("  ·", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
