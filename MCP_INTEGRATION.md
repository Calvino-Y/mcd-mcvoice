# MCP 集成说明（麦声 McVoice）

本文回答三个问题：数据从哪来、怎么进页面、以及为什么这样设计。
工具参数与实测踩坑的速查版在 `references/mcp-tools.md`，这里讲链路与契约。

## 1. 架构总览：两种模式，一套理解核

```
┌─ 演示模式（GitHub Pages）────────────────────────────┐
│  docs/index.html                                      │
│  数据 = 构建时注入的快照（data/menu.json + price_raw）│
│  算价 = 按菜单现价相加，标注 estimated，不建单        │
└───────────────────────────────────────────────────────┘
┌─ 本地模式（python scripts/serve.py）──────────────────┐
│  docs/index.html + serve.py 注入的 window.__LOCAL__   │
│  数据 = 你自己账号的实时返回（stores/menu/price）     │
│  算价 = calculate-price 真实报价，可 create-order     │
│  Token 只在 serve.py 进程内存里                       │
└───────────────────────────────────────────────────────┘
```

两种模式共用同一个语音理解核与状态机（页面里 `VOICE` 标记之间的纯函数），
数据结构完全一致——这是刻意的：`scripts/build_menu.py::assemble()` 是**唯一**的整理逻辑，
`build_menu.py`（构建快照）和 `serve.py`（整理实时返回）都调用它。
两处若各写一份，演示模式与本地模式的语音匹配行为就会悄悄分叉。

## 2. 连接与鉴权

- 接入地址：`https://mcp.mcd.cn`（MCP Streamable HTTP）
- 鉴权：`Authorization: Bearer <MCP Token>`，Token 在 <https://open.mcd.cn/mcp> 免费激活
- 客户端实现：`scripts/mcp_client.py`，**纯 Python 标准库，约 200 行，零依赖**。
  不用官方 SDK 的原因：部署目标是「clone 下来就能跑」，且 MCP 的 Streamable HTTP
  传输足够简单，代码完全可审计。

Streamable HTTP 的三条硬规则（踩过的坑）：

1. POST 的 `Accept` 必须同时声明 `application/json` 与 `text/event-stream`
2. `initialize` 后服务端可能下发 `Mcp-Session-Id`，后续请求必须原样回传
3. 响应可能是纯 JSON，也可能是 SSE（`data: {...}` 逐行），两种都要能解

**返回体的形态**：麦当劳 MCP 的工具返回是「字段说明 + JSON」混排的 Markdown：
先若干行 `- **data.storeCode**: 门店编码 (Type: string)`，末尾跟一段真正的 JSON。
`extract_json()` 的做法是逐个 `{` 位置尝试 `raw_decode`，第一个「能解成 dict 且含
`required` 字段」的就是答案——只按字段名判定，不做位置假设。

## 3. 页面用到的工具链路

### 3.1 `query-nearby-stores` → 门店列表

```
入参：{ beType: 1, searchType: 2, city: "成都", keyword: "天府三街" }
返回：data[]（门店数组）+ datetime
```

页面消费：`storeCode` `storeName` `address` `distance` `businessStatus`
`businessStartTime` `businessEndTime`。语音选门店用 `storeName`（LCS 打分）或序号。

注意：这个工具返回的 `data` 是**裸数组**，不含 `success` 字段，
`extract_json` 对它放宽 `required=("data",)`。

### 3.2 `query-meals` → 菜单

```
入参：{ storeCode, orderType: 1, beType: 1 }
返回：data.categories[] + data.meals{code→商品} + data.frequent
```

页面消费的每个商品字段：`name` `currentPrice`（元，字符串）`originalPrice`
`discountType` `canWithOrder`。`image` 是带签名的临时链接，快照已剔除。

### 3.3 `calculate-price` → 真实报价

```
入参：{ storeCode, orderType: 1, beType: 1, items: [{ productCode, quantity }] }
返回：data.price（分）+ data.productList + data.takeWayList
```

页面消费：`price`（**除以 100 展示**）、`productList[].productName / subtotal`、
`takeWayList[].code / title`。报价步骤的「堂食 / 外带」就来自 `takeWayList`，
**不写死取值**——取餐方式随门店变化。

### 3.4 `create-order` → 创建订单（唯一不可逆动作）

```
入参：{ storeCode, orderType: 1, beType: 1, takeWayCode, items }
返回：订单信息（支付链接字段名官方未固定）
```

页面消费：`deep_find_url()` 深度遍历找第一个像支付链接的字符串；找不到就把原始
返回带回去展示。**绝不假设某个字段名一定存在。**

双闸门：页面开关 `allowOrder`（默认关）+ 服务端要求 `confirm: true`。

### 3.5 `query-my-coupons` → 卡包券（只播报）

返回是 Markdown 文本，无 `couponId` / `couponCode`，而套券必须传这两个字段——
**纯 MCP 链路拿不到可回填的券 ID**。所以页面只播报券名、价格、时段，
`serve.py::parse_my_coupons()` 尽力解析、失败返回空列表（页面如实少显示，不编造）。

## 4. 本地工具模式的接口契约（serve.py）

页面通过 `window.__LOCAL__ = { origin, sessionKey, port }` 获知本地服务；
所有 `/api/*` 请求必须带 `X-McVoice-Key: <sessionKey>` 头。

| 端点 | 方法 | 作用 | 失败语义 |
|---|---|---|---|
| `/api/status` | GET | 连接状态、调用计数、采样时间 | 总是 200，**不回显 Token** |
| `/api/connect` | POST | 校验 Token 并搜索附近门店 | Token 无效 → 400；无门店 → 400 |
| `/api/stores` | POST | 返回已搜索的门店列表 | 未连接 → 空列表 |
| `/api/menu` | POST | 拉取指定门店实时菜单并整理 | 未连接 → **409** + 提示；菜单空 → 502 |
| `/api/price` | POST | `calculate-price` 真实报价 | 未连接 → 409 |
| `/api/order` | POST | 创建订单，返回支付链接 | 缺 `confirm:true` → **400**；未连接 → 409 |
| `/` | GET | 页面（注入 `__LOCAL__`） | Host 伪造 → 403 |

安全闸门（`tests/test_serve.py` 端到端逐条验证，18 项）：

| 防线 | 行为 |
|---|---|
| 只绑 `127.0.0.1` | 局域网内其他设备连不上 |
| `Host` 头校验 | 防 DNS rebinding；伪造时连页面也 403 |
| `Origin` 校验 | 跨站请求 403 |
| `OPTIONS` 预检 | 一律 403（不提供跨域能力） |
| 会话密钥 | 每次启动 `secrets.token_urlsafe(24)`，缺失/错误 → 401 |
| Token 内存化 | 不落盘、不回显、注入页面前做泄漏形态扫描 |

## 5. 数据字段说明（data/menu.json）

由 `build_menu.py::assemble()` 生成，本地模式实时整理出的结构与此完全一致：

| 字段 | 类型 | 说明 |
|---|---|---|
| `source` | object | 采样元信息：`storeCode` `storeName` `address` `distance` `orderType` `beType` `tools` `capturedAt` `note` |
| `stores` | array | 门店列表（来自 `query-nearby-stores`），含 `storeCode` `storeName` `address` `distance` `businessStatus` `businessStartTime` `businessEndTime` `reservation` |
| `categories` | array | `{ name, meals[] }`，`meals` 是商品编码数组；**一个编码可出现在多个类目**（营销位与品类位共存，实测 11 项重叠） |
| `items` | array | 展示顺序按类目走：`code` `name` `category` `price`（元）`originalPrice` `discountType` `canWithOrder` |
| `aliases` | object | 口语说法 → **编码数组**（多候选，交歧义逻辑裁决），86 条；构建时校验全部编码存在 |
| `homophones` | object | 同音误听 → 正确写法，27 条 |
| `pinyin` | object | 拼音容错表，**按音节分组**存（`{"ke": "可渴克刻客课…"}`），396 个音节 / 3766 个汉字（GB2312 一级），16 KB。由 `scripts/build_pinyin.py` 构建期用 pypinyin 生成到 `data/pinyin_table.json`，页面启动时展开成「字 → 音节」。**表必须比菜单宽**：识别错的字绝大多数是菜单外的字（「可乐」→「渴乐」，「渴」不在菜单里），只收菜单用字的表在最该救的时刻必然失效 |
| `frequent` | object | 「我的常点」标记（来自 `data.frequent`） |
| `coupons` | array | 券的播报用快照：`name` `price` `validFrom` `validTo` `weekdays` `timeFrom` `timeTo` `tags` |
| `couponLimitation` | string | 券 ID 缺失的原文说明，页面可直接念给用户听 |

## 6. 已知限制（写进代码，不藏着）

1. **券无法自动套用**：MCP 券工具不返回券 ID。只播报，不假装。
2. **门店级数据**：菜单与价格跨门店不可复用；演示快照只覆盖采样门店，
   其余门店在页面上明确标注「无菜单快照」并拒绝点餐。
3. **浏览器不能直连 MCP**：`https://mcp.mcd.cn` 不带 CORS 头（实测 403）。
   这是正确的安全设计，本项目以本地代理应对，而不是想办法绕过它。
4. **支付链接字段名未固定**：`create-order` 的返回结构官方没有写死，
   页面用深度遍历兜底，找不到链接时展示原始返回。
5. **演示模式不建单**：GitHub Pages 上即使打开下单开关也不会创建订单，
   页面与测试都明确验证这一点。

## 7. 变更后必须验证

```bash
python scripts/build_menu.py        # 改了别名表/整理逻辑后重建 data/menu.json
python scripts/render_app.py        # 重新渲染 docs/index.html
node tests/check_page.mjs           # 结构自检（66 项）
node tests/test_voice.mjs           # 语音理解核心（76 项）
python tests/test_serve.py          # 安全闸门（18 项）
python tests/browser_check.py       # 无头浏览器全流程 + axe 审计 + 降级路径
python tests/make_preview.py        # 若页面视觉变了，重生成 README 预览图
```
