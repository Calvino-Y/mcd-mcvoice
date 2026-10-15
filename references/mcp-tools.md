# 麦当劳 MCP 工具速查（麦声 McVoice 用到的部分）

> 官方仓库：<https://github.com/M-China/mcd-mcp-server> · Token 申请：<https://open.mcd.cn/mcp>
> 传输方式：MCP Streamable HTTP，接入地址 `https://mcp.mcd.cn`

## 本项目实际跑通的链路

```
query-nearby-stores  →  query-meals  →  calculate-price  →  create-order
    （门店）             （菜单）         （真实报价）        （创建订单 → 支付链接）
                              └── query-my-coupons（卡包，只用于播报）
```

---

## 1. `query-nearby-stores`

```
入参：
  beType      1=到店自提  5=得来速
  searchType  1=我的收藏门店  2=按位置搜索
  city        城市名，如「成都」（searchType=2 时必传）
  keyword     门店关键词，如「天府三街」
返回：data[] 门店数组 + datetime（本次查询时间）
```

单个门店对象的真实字段（实测返回）：

```json
{
  "storeCode": "3450121",
  "storeName": "麦当劳成都天府三街餐厅",
  "address": "高新区天府三街218号峰汇中心二号楼一层",
  "distance": 671,
  "businessStatus": true,
  "businessStartTime": "07:00",
  "businessEndTime": "23:00",
  "reservation": true
}
```

要点：

- `distance` 单位是米。
- 同一次搜索返回的门店**共享很长的名字前缀**（都是「麦当劳成都…」）。做「按店名选门店」
  时不能用字覆盖率打分——任意两家店的覆盖率都会双双接近满分——必须用最长公共子串。
  实现见 `assets/app-template.html` 里的 `lcsLength` / `storeScore`。
- **`query-nearby-stores` 返回的 `data` 是裸数组，不含 `success` 字段**，因此
  `extract_json` 对这个工具要放宽 `required`，只认 `data`。

## 2. `query-meals`

```
入参：
  storeCode       必填
  orderType       1=到店（含自提+得来速）  2=外送（含麦乐送+团餐）
  beType          1=到店自提  2=麦乐送  5=得来速  6=企业团餐
  beCode          得来速/外送/团餐必传，与 storeCode 配对
  reservationDate 预约场景必传，格式 yyyy-MM-dd HH:mm
返回：data.categories[]（类目与餐品编码）+ data.meals（code → 商品）+ data.frequent（常点）
```

beType 与 orderType 的对应关系（官方约定）：

| beType | 场景 | orderType | beCode |
|---|---|---|---|
| 1 | 到店自提 | 1 | 不传 |
| 5 | 得来速 | 1 | 必传 |
| 2 | 麦乐送 | 2 | 必传 |
| 6 | 企业团餐 | 2 | 必传 |

实测要点：

- `meals` 是 **`{编码: 商品对象}` 的字典，不是数组**。商品对象的字段是
  `name` / `currentPrice` / `originalPrice` / `discountType` / `canWithOrder` / `image`。
- `currentPrice` 与 `originalPrice` 的单位是**元**（字符串形式），而
  `calculate-price` 返回的金额单位是**分**。两者不统一，换算错就会差 100 倍。
- 一个商品编码**可以同时出现在多个类目**里（营销位「人气热卖」与品类位「鸡肉汉堡/卷」
  共存）。实测 122 项餐品里有 11 项跨类目重叠，属正常；单测断言的是「类目内不重复」
  与「每个餐品至少被一个类目收录」。
- `image` 是带签名参数的临时链接，会过期，仓库快照已剔除该字段。
- 菜单与价格是**门店级实时数据**，跨门店不可复用。

## 3. `calculate-price`

```
入参：
  storeCode     必填
  orderType     1
  beType        1
  items         [{ "productCode": "1100", "quantity": 1 }, ...]   ← 注意字段名
返回：data + datetime
```

`data` 的真实结构（实测：巨无霸 + 中薯条 + 可乐）：

```json
{
  "productPrice": 5000,
  "originalPrice": 5000,
  "discount": 0,
  "price": 5000,
  "productList": [
    { "productCode": "1100", "productName": "巨无霸", "quantity": 1, "subtotal": 2600 },
    { "productCode": "4810", "productName": "中薯条", "quantity": 1, "subtotal": 1450 },
    { "productCode": "9900008751", "productName": "可乐", "quantity": 1, "subtotal": 950 }
  ],
  "takeWayList": [
    { "code": "eat-in", "title": "堂食", "subtitle": "店内用餐" },
    { "code": "take-in-store", "title": "外带", "subtitle": "店内自提" }
  ]
}
```

实测踩到的两个坑：

1. **金额单位是「分」**。`5000` 是 50 元，不是 5000 元。展示时必须除以 100，
   页面里对应的字段是 `estimated` 与 `cents`。
2. **`takeWayList` 是 `create-order` 必传的 `takeWayCode` 来源，而且随门店变化**。
   本次门店只返回「堂食 / 外带」两项，不能写死取值，必须读返回。
3. 同一个商品在 `query-meals` 里叫「薯条」，在 `calculate-price` 的 `productList`
   里叫「中薯条」——**两处命名不完全一致**，做名称匹配时不能只靠名字，要用编码。

## 4. `create-order`

```
入参：
  storeCode     必填
  orderType     1
  beType        1
  takeWayCode   必填，取自 calculate-price 的 takeWayList
  items         [{ "productCode": "...", "quantity": N }]
返回：订单信息；支付链接的字段名官方文档未固定
```

要点：

- 这是本项目里**唯一不可逆**的动作。因此设了两道闸门：页面上需要显式打开
  「允许创建订单」开关（默认关闭），服务端 `/api/order` 还额外要求 `confirm: true`。
- 支付链接的字段名没有对外写死，所以 `scripts/serve.py` 用 `deep_find_url()`
  在任意嵌套结构里深度遍历找第一个像支付链接的字符串，而不是假设某个字段名存在。
  找不到链接时，接口会把原始返回一并带回去，页面至少能展示点东西，而不是
  「下单成功但什么都看不到」。
- 本项目**不代付**。创建订单后由用户本人在麦当劳官方渠道扫码完成支付。

## 5. `query-my-coupons` / `available-coupons`

```
入参：query-my-coupons 无参（当前账号卡包资产）
      available-coupons  传订单上下文，返回该订单可用的券
返回：**Markdown 文本**，不是结构化 JSON
```

**关键限制（必须如实说明）**：

> 这两个工具的返回里都**没有 `couponId` / `couponCode`**，而 `calculate-price` 与
> `create-order` 套券时**必须**传这两个字段。也就是说，纯 MCP 链路目前拿不到可回填的
> 券 ID。

所以本项目**只做到「语音播报有哪些券、什么时段能用」，不假装能自动套券**。
`scripts/serve.py::parse_my_coupons()` 对这个 Markdown 做的是**尽力而为**的解析：
解析不出来就返回空列表，页面如实少显示一点，而不是编一个看起来完整的券列表出来。

页面与数据集里的 `couponLimitation` 字段就是这段说明，可以直接读出来给用户听。

---

## 项目未用到、但同一 Server 提供的

`delivery-query-addresses` `delivery-create-address` `delivery-query-stores`
`query-meal-assistance` `query-meal-detail` `query-store-coupons`
`cancel-order` `query-order` `campaign-calendar` `auto-bind-coupons` `query-my-account`
`mall-points-products` `mall-product-detail` `mall-create-order`
`mall-order-list` `mall-order-detail` `now-time-info` `query-lottery-info`
`draw-lottery` `query-my-prizes` `query-party-city` `query-party-store`
`query-partystore-date` `query-partystore-session` `party-order-create`

## 通用注意事项

- **传输三条硬规则**（`scripts/mcp_client.py` 已处理，自己实现时别踩）：
  1. POST 的 `Accept` 必须同时声明 `application/json` 与 `text/event-stream`，只声明前者拿不到响应。
  2. `initialize` 后服务端可能通过响应头下发 `Mcp-Session-Id`，后续请求必须原样回传。
  3. 响应可能是纯 JSON，也可能是 SSE（`data: {...}` 逐行），两种都要能解。
- **返回是「字段说明 + JSON」混排的 Markdown**，真正的结构化数据要逐个 `{` 位置尝试
  `raw_decode` 并校验关键字段才能抽出来（`mcp_client.extract_json`）。
- **浏览器无法直连**：`https://mcp.mcd.cn` 的响应不带 `Access-Control-Allow-Origin`
  （OPTIONS / POST 均返回 403 且无 CORS 头）。这恰好是正确的安全设计——MCP Token
  本来就不该交给浏览器保管——所以本项目用 `scripts/serve.py` 在本机进程里代调。
- 限流 600 次/分钟，超限返回 429；401 = Token 无效/过期/未提供；403 = 无权限。
