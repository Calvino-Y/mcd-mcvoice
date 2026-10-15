---
name: mcd-mcvoice
description: >
  麦声 McVoice。当用户想「用语音点麦当劳」「帮视障用户完成麦当劳点餐」
  「不看屏幕操作麦当劳」「语音选门店、语音加购、语音算价、语音下单」
  「测试无障碍语音交互」「口语说法怎么对应到麦当劳菜单」时使用。
  基于麦当劳 MCP 的真实门店、菜单与价格，把整条点餐流程翻转成一场对话：
  播报每一步、理解口语说法（86 条别名 + 27 条误听纠正）、听不懂就问，
  默认只算价不建单，显式确认后才创建订单。
  This skill should be used when a user wants voice-first McDonald's China ordering
  designed for visually impaired users, or needs the spoken-phrase-to-menu mapping,
  the accessibility audit results, or the local MCP proxy with safety gates.
agent_created: true
license: MIT
---

# 麦声 · McVoice

用说的，点麦当劳。为视障用户设计的语音点餐全流程：**选门店 → 点餐 → 确认 → 算价 → 下单**，
每一步都念出来，每一步都能用嘴（或键盘）完成。

## 什么时候用这个 Skill

- 「帮我用语音点一份麦当劳」
- 「我是视障用户 / 我不方便看屏幕，帮我完成点餐」
- 「这个页面语音交互是怎么设计的？」
- 「用户说『来个巨无霸』，页面是怎么匹配到菜单的？」
- 「跑一遍无障碍审计」（内置自检 + axe-core）
- 「用我自己的账号真实下单」（本地工具模式）
- 「麦当劳 MCP 的门店/菜单/算价/下单接口怎么调？」

## 快速上手

```bash
# 演示模式：直接打开 docs/index.html（或 GitHub Pages），零依赖
# 本地模式（真实数据 + 真实下单）：零依赖，只用 Python 标准库
python scripts/serve.py          # 自动打开 http://127.0.0.1:8765
```

需要 MCP Token（https://open.mcd.cn/mcp 免费激活）。**Token 由用户在页面里自己填，
Agent 不要经手，更不要让用户把 Token 发进对话。**

## 语音流程（八步）

`welcome → store → browse → cart → price → confirm → ordered → done`

每一步 `STEPS[step].chips` 里列着该步骤能说的话，页面会把它们显示出来并念出来。
任何时刻的控制指令（「再说一遍」「退回上一步」「取消」「我能说什么」…）优先放行。

## 语音理解核（页面里 `VOICE` 标记之间的纯函数）

理解管道：`normalizeUtterance → applyHomophones → parseUtterance`。

- **口语别名表**（`data/menu.json` 的 `aliases`，86 条）：口语词 → 可能的多个商品编码。
  「巨无霸」可能指单点 / 三件套 / 四件套，全部保留，交给歧义逻辑裁决。
- **同音纠正表**（`homophones`，27 条）：「巨无八」→ 巨无霸、「麦乐机」→ 麦乐鸡。
- **数量解析**：`([零一两二三四五六七八九十\d]{1,3})\s*(?:份|个|杯|块|…)`。
- **歧义三层优先级**：完整商品名精确命中 → 采纳；并列候选里「不带后缀的单品」唯一 →
  认定单品并顺口提示套装；其余 → 念出候选让用户选。
- **选门店**：先认序号（「第一家」），再按店名匹配（最长公共子串打分，
  **不能用字覆盖率**——门店名共享长前缀会让任意两家都接近满分）。

**改动理解核的规矩**：它被 `/* ---VOICE-BEGIN--- */` / `/* ---VOICE-END--- */` 标记包裹，
`tests/test_voice.mjs` 原样抽出执行——不要动这两个标记，改完必须重跑该测试。

## 必须遵守的约束

1. **诚实优先于功能。** 三条写进代码的边界不许越过：
   - 演示快照只覆盖采样门店；没有菜单快照的门店**不能点**（页面会挡住并说明）；
   - 演示模式报价必须标注 `estimated`，不冒充 `calculate-price` 的正式报价；
   - 券只播报不假装套用（MCP 券工具不返回 `couponId`，套券必须传，见
     `menu.json` 的 `couponLimitation` 字段）。
2. **创建订单是唯一不可逆动作**：页面开关（默认关）+ 服务端 `confirm: true` 双闸门。
   任何自动化场景下都不要替用户打开开关。
3. **不代付。** 订单创建后由用户在麦当劳官方渠道扫码支付。
4. **无障碍不是装饰。** 改动页面后必须跑 `python tests/browser_check.py`
   （含 axe-core 审计）；任何「视觉有变化但播报没跟上」的改动都算回归。
5. **语音识别不可用是常态不是异常。** 打字输入走同一条理解管道，必须始终可用。

## 常见坑

- **金额单位不统一**：`query-meals` 的价格是「元」（字符串），`calculate-price` 返回的是
  「分」。换算错就是差 100 倍。
- **同一商品两处名字不同**：菜单里叫「薯条」，报价明细里叫「中薯条」。名称匹配要用编码。
- **菜单价格是门店级的**：跨门店套用价格是最隐蔽的错误（本项目为此专门加了
  「无快照门店不能点」的规则与测试）。
- **取餐方式不能写死**：`takeWayList` 随门店变化，必须读 `calculate-price` 的返回。
- **浏览器不能直连 MCP**：`https://mcp.mcd.cn` 不带 CORS 头（实测 403），必须走
  `scripts/serve.py` 本地代理；安全闸门见 `tests/test_serve.py`，改了必须重跑。
- **别名表是手工维护的**：构建时逐条校验指向的编码必须存在，写错当场构建失败。
  不要往里收「三件套」「单人餐」这类泛化规格词——会让说得清楚的用户反被追问。
- **播报与拾音互抢**：页面朗读前要 `cancel()`，朗读期间要暂停拾音，否则自问自答。
- **降级提示会被开场白覆盖**：`speak()` 每次先 `cancel()`，所以关键提示必须并进
  下一条播报，不能指望前一条播完。
- **Windows 下 `write_text` 会把 `\n` 写成 `\r\n`**：生成 HTML 时务必传 `newline="\n"`。

## 验证链（改完任何东西都要跑）

```bash
node tests/check_page.mjs          # 页面结构自检（66 项）
node tests/test_voice.mjs          # 语音理解核心（76 项）
python tests/test_serve.py         # 安全闸门（18 项）
python tests/browser_check.py      # 无头浏览器全流程 + axe 审计 + 降级路径
```

## 参考

- MCP 工具参数与实测踩坑：`references/mcp-tools.md`
- 本地工具模式与安全设计：`MCP_INTEGRATION.md`
- 数据字段说明：`MCP_INTEGRATION.md` 与 `data/menu.json` 自带的 `source` / `couponLimitation`
- 项目背景与开发笔记：`workbuddy.md`
