#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地工具模式：让这份页面跑在**你自己的**麦当劳账号上。

为什么必须有这一层，而不是让网页直接调 MCP：
    实测 https://mcp.mcd.cn 的响应不带 Access-Control-Allow-Origin（OPTIONS/POST 均返回
    403 且无 CORS 头），浏览器直连会被同源策略拦下。这恰好是**正确的安全设计**——
    MCP Token 本来就不该交给浏览器保管。所以由本机进程代浏览器调 MCP，
    网页只跟 127.0.0.1 说话，Token 只存在于本进程的内存里。

安全边界（tests/test_serve.py 逐条端到端验证）：
    * 只绑定 127.0.0.1，局域网内其它设备连不上
    * 校验 Host 头，防 DNS rebinding（伪造域名指向本机服务）
    * 拒绝跨站 Origin，别的网页无法借你的浏览器调用本服务
    * 每次启动生成随机会话密钥，/api/* 必须带 X-McVoice-Key，否则 401
    * Token 只在内存：不落盘、不回显、注入页面之前先做泄漏形态扫描
    * /api/order 额外要求显式 confirm=true，防止误触发真实下单

用法::

    python scripts/serve.py                 # 自动打开浏览器
    python scripts/serve.py --no-browser    # 不打开浏览器（测试用）
    python scripts/serve.py --port 8765
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import secrets
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from build_menu import COUPON_LIMITATION, HOMOPHONE_FIXES, assemble   # noqa: E402
from mcp_client import McpError, McdMcpClient, extract_json          # noqa: E402

PAGE = ROOT / "docs" / "index.html"
KEY_HEADER = "X-McVoice-Key"

# 进程内的会话状态。Token 只在这里活着，进程退出就没了。
SESSION: dict = {
    "token": None,
    "client": None,
    "stores": [],
    "captured_at": None,
    "calls": 0,
    "last_error": None,
}


def parse_my_coupons(markdown: str) -> list[dict]:
    """把 query-my-coupons 的 Markdown 卡片解析成结构化券列表。

    这个工具返回的是给人看的 Markdown（没有 JSON，也没有 couponId），
    所以这里的解析是**尽力而为**：解析失败就返回空列表，页面会如实少显示一点，
    而不是编一个看起来完整的券列表出来。
    """
    coupons: list[dict] = []
    # 券名是二级标题；券价在 "**优惠**: ¥13.5 (用券价格)" 这样的行里
    blocks = re.split(r"\n##\s+", markdown)
    for block in blocks:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        name = lines[0].lstrip("#").strip()
        if not name or name.startswith("您") or name.startswith("#"):
            continue
        price = None
        validity = None
        for line in lines[1:]:
            m = re.search(r"\*\*优惠\*\*\s*[:：]\s*¥?\s*([\d.]+)", line)
            if m and price is None:
                price = float(m.group(1))
            m = re.search(r"\*\*有效期\*\*\s*[:：]\s*([0-9]{4}-[0-9]{2}-[0-9]{2}[^\n]*)", line)
            if m and validity is None:
                validity = m.group(1).strip()
        if name and (price is not None or validity):
            coupons.append({"name": name, "price": price, "validityText": validity})
    return coupons


class Handler(BaseHTTPRequestHandler):
    server_version = "McVoice/1.0"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------- 基础设施

    def log_message(self, fmt, *args):        # noqa: A003
        sys.stderr.write("  %s\n" % (fmt % args))

    def _json(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:                      # noqa: BLE001
            return {}

    def _guard(self) -> bool:
        """三道闸门。任何一道不过就直接拒绝，并说明原因。"""
        port = self.server.server_address[1]                       # type: ignore[attr-defined]

        host = (self.headers.get("Host") or "").split(":")[0].strip().lower()
        if host not in ("127.0.0.1", "localhost", "::1", "[::1]"):
            self._json(403, {"error": "拒绝：Host 头是 %r，本服务只接受本机访问" % host})
            return False

        origin = self.headers.get("Origin")
        allowed = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
        if origin and origin not in allowed:
            self._json(403, {"error": "拒绝跨站请求：Origin 是 %r" % origin})
            return False

        if self.path.startswith("/api/") and self.headers.get(KEY_HEADER) != self.server.session_key:  # type: ignore[attr-defined]
            self._json(401, {"error": "缺少或错误的会话密钥。请从本服务打开的页面里操作。"})
            return False

        return True

    # ------------------------------------------------------------- 路由

    def do_OPTIONS(self):      # noqa: N802
        # 本服务不对外提供跨域能力，因此不给预检任何正面响应
        self._json(403, {"error": "本服务不提供跨域访问"})

    def do_GET(self):          # noqa: N802
        # 闸门对所有请求都生效：连页面本身也要求 Host 头正确，
        # 这样 DNS rebinding 攻击连页面都拿不到。
        if not self._guard():
            return
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._serve_page()
        if path == "/api/status":
            return self._api_status()
        self._json(404, {"error": "没有这个路径"})

    def do_POST(self):         # noqa: N802
        if not self._guard():
            return
        path = self.path.split("?")[0]
        routes = {
            "/api/connect": self._api_connect,
            "/api/stores": self._api_stores,
            "/api/menu": self._api_menu,
            "/api/price": self._api_price,
            "/api/order": self._api_order,
        }
        fn = routes.get(path)
        if not fn:
            return self._json(404, {"error": "没有这个接口"})
        try:
            fn(self._body())
        except McpError as exc:
            SESSION["last_error"] = str(exc)
            self._json(502, {"error": f"MCP 调用失败：{exc}"})
        except Exception as exc:                                    # noqa: BLE001
            SESSION["last_error"] = repr(exc)
            self._json(500, {"error": f"服务内部错误：{exc}"})

    # ------------------------------------------------------------- 页面

    def _serve_page(self):
        if not PAGE.exists():
            return self._json(500, {"error": "docs/index.html 不存在，请先跑 python scripts/render_app.py"})

        html = PAGE.read_text(encoding="utf-8")
        port = self.server.server_address[1]                        # type: ignore[attr-defined]
        local = {"origin": f"http://127.0.0.1:{port}", "sessionKey": self.server.session_key,  # type: ignore[attr-defined]
                 "port": port}
        inject = f"<script>window.__LOCAL__={json.dumps(local, ensure_ascii=False)};</script>\n"
        html = html.replace("<script>", inject + "<script>", 1)

        # 注入前扫一遍：页面里绝不该出现 Token 形态的字符串
        if SESSION["token"]:
            if SESSION["token"] in html:
                return self._json(500, {"error": "安全检查未通过：页面内容里出现了 Token，已中止"})

        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _client(self) -> McdMcpClient | None:
        return SESSION["client"]

    def _call(self, tool: str, args: dict | None = None) -> dict:
        client = self._client()
        if client is None:
            raise McpError("__NOT_CONNECTED__")
        SESSION["calls"] += 1
        payload = client.call_tool_json(tool, args or {})
        return payload

    # ------------------------------------------------------------- 接口

    def _api_status(self):
        self._json(200, {
            "mode": "local",
            "connected": SESSION["client"] is not None,
            "store": (SESSION["stores"][0]["storeName"] if SESSION["stores"] else None),
            "storeCount": len(SESSION["stores"]),
            "apiCalls": SESSION["calls"],
            "capturedAt": SESSION["captured_at"],
            "lastError": SESSION["last_error"],
            "envToken": bool(SESSION["token"]),
            "tokenStored": "memory-only",
        })

    def _api_connect(self, body: dict):
        token = (body.get("token") or "").strip()
        keyword = (body.get("keyword") or "成都 天府三街").strip()
        if not token:
            return self._json(400, {"error": "请先填入 MCP Token"})

        try:
            client = McdMcpClient(token=token)
            client.initialize()
        except McpError as exc:
            return self._json(400, {"error": f"Token 校验失败：{exc}"})

        city, _, kw = keyword.partition(" ")
        payload = client.call_tool_json("query-nearby-stores", {
            "beType": 1, "searchType": 2, "city": city or "成都", "keyword": kw or city or "天府三街",
        })
        stores = payload.get("data") or []
        if not stores:
            return self._json(400, {"error": "没有搜到门店，换个关键词试试"})

        SESSION["token"] = token
        SESSION["client"] = client
        SESSION["stores"] = stores
        SESSION["captured_at"] = payload.get("datetime")
        SESSION["last_error"] = None
        self._json(200, {"data": {
            "stores": stores,
            "source": {
                "storeCode": stores[0]["storeCode"],
                "storeName": stores[0]["storeName"],
                "capturedAt": payload.get("datetime"),
                "tools": ["query-nearby-stores"],
                "note": "门店列表来自你自己的账号搜索。选定门店后才会拉取该门店的实时菜单。",
            },
        }})

    def _api_stores(self, body: dict):
        self._json(200, {"data": SESSION["stores"]})

    def _api_menu(self, body: dict):
        store_code = str(body.get("storeCode") or "").strip()
        if not store_code:
            return self._json(400, {"error": "缺少 storeCode"})
        if not SESSION["client"]:
            return self._json(409, {"error": "还没有连接账号。请先在页面里填入 MCP Token。"})

        payload = self._call("query-meals", {"storeCode": store_code, "orderType": 1, "beType": 1})
        data = payload.get("data") or {}
        if not data.get("meals"):
            return self._json(502, {"error": "门店菜单返回为空"})

        menu_payload = {
            "arguments": {"storeCode": store_code, "orderType": 1, "beType": 1},
            "categories": [
                {"name": c.get("name", "其他"),
                 "meals": [m["code"] for m in c.get("meals", []) if m.get("code")]}
                for c in data.get("categories", [])
            ],
            "meals": data.get("meals", {}),
            "frequent": data.get("frequent"),
        }

        # 券：尽力解析，解析不到就为空（页面会少显示，不会编）
        coupons: list[dict] = []
        try:
            text = SESSION["client"].call_tool("query-my-coupons")
            SESSION["calls"] += 1
            coupons = parse_my_coupons(text)
        except McpError:
            coupons = []

        menu = assemble(menu_payload, SESSION["stores"], coupons,
                        captured_at=payload.get("datetime") or "")
        self._json(200, {"data": {"menu": menu, "source": menu["source"]}})

    def _api_price(self, body: dict):
        store_code = str(body.get("storeCode") or "").strip()
        items = body.get("items") or []
        if not store_code or not items:
            return self._json(400, {"error": "缺少 storeCode 或 items"})
        if not SESSION["client"]:
            return self._json(409, {"error": "还没有连接账号。请先在页面里填入 MCP Token。"})

        payload = self._call("calculate-price", {
            "storeCode": store_code, "orderType": 1, "beType": 1, "items": items,
        })
        data = payload.get("data") or {}
        self._json(200, {"data": data})

    def _api_order(self, body: dict):
        # 真实下单是这里唯一不可逆的动作，所以除了页面上的开关，
        # 服务端还要一道显式确认，避免任何形式的误触发。
        if body.get("confirm") is not True:
            return self._json(400, {"error": "创建订单需要显式确认（confirm=true）"})

        store_code = str(body.get("storeCode") or "").strip()
        take_way = str(body.get("takeWayCode") or "").strip()
        items = body.get("items") or []
        if not store_code or not take_way or not items:
            return self._json(400, {"error": "缺少 storeCode / takeWayCode / items"})
        if not SESSION["client"]:
            return self._json(409, {"error": "还没有连接账号。请先在页面里填入 MCP Token。"})

        payload = self._call("create-order", {
            "storeCode": store_code, "orderType": 1, "beType": 1,
            "takeWayCode": take_way, "items": items,
        })
        data = payload.get("data") or {}

        # 支付链接的字段名官方文档没有给死，所以这里不写死字段名：
        # 深度遍历找出第一个像支付链接的字符串，同时把原始返回带回去，
        # 页面至少能展示点东西，而不是"下单成功但什么都看不到"。
        url = deep_find_url(data) or deep_find_url(payload)
        order_id = None
        for key in ("orderId", "orderNo", "orderCode", "id"):
            if isinstance(data.get(key), (str, int)):
                order_id = str(data[key])
                break

        self._json(200, {"data": {"url": url, "orderId": order_id,
                                  "raw": data if not url else None},
                          "note": "订单已创建。支付由你自己完成，本工具不代付。"})


def deep_find_url(node, depth: int = 0) -> str | None:
    """在任意嵌套结构里找第一个像支付链接的字符串。"""
    if depth > 6:
        return None
    if isinstance(node, str):
        if node.startswith("http") and ("pay" in node.lower() or "order" in node.lower()
                                        or "mcd" in node.lower()):
            return node
        return None
    if isinstance(node, dict):
        for value in node.values():
            found = deep_find_url(value, depth + 1)
            if found:
                return found
    if isinstance(node, list):
        for value in node:
            found = deep_find_url(value, depth + 1)
            if found:
                return found
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="麦声 McVoice 本地工具模式")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args(argv)

    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    httpd.session_key = secrets.token_urlsafe(24)      # type: ignore[attr-defined]
    httpd.daemon_threads = True                        # type: ignore[attr-defined]

    url = f"http://127.0.0.1:{args.port}/"
    print("麦声 McVoice · 本地工具模式")
    print(f"  地址：{url}（只绑定 127.0.0.1，局域网内其它设备连不上）")
    print(f"  会话密钥：{httpd.session_key[:8]}…（每次启动都不同）")   # type: ignore[attr-defined]
    print("  Token：只存在本进程内存里，不落盘、不回显、不上传第三方")
    print("  创建订单：需要在页面上打开「允许创建订单」，且服务端要求显式确认")
    print("  按 Ctrl+C 退出\n")

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
