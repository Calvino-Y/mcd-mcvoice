#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地工具模式 scripts/serve.py 的端到端测试。

这个进程将来会持有用户的 MCP Token、并且能创建真实订单，所以这里不测「功能跑通」就完事，
重点测**安全闸门**和**不可逆动作的防护**：

  1. 正常路径：会话密钥从注入的页面里取（真实浏览器就是这么拿的），带上它才能调接口
  2. 没有密钥 / 错误密钥        -> 401
  3. 跨站 Origin               -> 403
  4. Host 头伪造               -> 403（防 DNS rebinding）
  5. 预检 OPTIONS              -> 403（本服务不对外提供跨域）
  6. 响应里绝不能出现 Token 明文
  7. 未连接账号时调业务接口     -> 409，而不是 500 崩掉
  8. 创建订单必须显式确认       -> 否则 400（这是唯一不可逆的动作）

用法： python tests/test_serve.py
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
PORT = 8891
BASE = f"http://127.0.0.1:{PORT}"

failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures
    if ok:
        print(f"  PASS  {label}")
    else:
        failures += 1
        print(f"  FAIL  {label}  {detail}")


def request(path: str, key: str | None = None, origin: str | None = None,
            host: str | None = None, method: str = "GET",
            body: dict | None = None) -> tuple[int, str]:
    req = urllib.request.Request(BASE + path, method=method)
    if key:
        req.add_header("X-McVoice-Key", key)
    if origin:
        req.add_header("Origin", origin)
    if host:
        # urllib 不允许随便改 Host，用底层接口覆盖
        req.add_unredirected_header("Host", host)
        req.host = host
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=25) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:                       # noqa: BLE001
        return 0, str(exc)


def main() -> int:
    proc = subprocess.Popen(
        [PY, "scripts/serve.py", "--no-browser", "--port", str(PORT)],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace")
    try:
        ready = False
        for _ in range(60):
            code, _body = request("/")
            if code == 200:
                ready = True
                break
            time.sleep(0.25)
        if not ready:
            print("服务未能在 15 秒内启动")
            return 2

        print("安全闸门")
        code, page = request("/")
        m = re.search(r"window\.__LOCAL__=(\{.*?\});</script>", page, re.S)
        check("GET / 返回页面并注入 window.__LOCAL__", code == 200 and bool(m), f"code={code}")
        key = json.loads(m.group(1))["sessionKey"] if m else ""

        code, _ = request("/api/status")
        check("无密钥访问 /api/status -> 401", code == 401, f"code={code}")

        code, _ = request("/api/status", key="wrong-key")
        check("错误密钥 -> 401", code == 401, f"code={code}")

        code, body = request("/api/status", key=key)
        check("正确密钥 -> 200 且 mode=local",
              code == 200 and '"mode":"local"' in body.replace('"mode": "local"', '"mode":"local"'),
              f"code={code} body={body[:140]}")

        code, _ = request("/api/status", key=key, origin="https://evil.example.com")
        check("跨站 Origin -> 403", code == 403, f"code={code}")

        code, _ = request("/api/status", key=key, host="attacker.example.com")
        check("Host 头伪造 -> 403（防 DNS rebinding）", code == 403, f"code={code}")

        code, _ = request("/", host="attacker.example.com")
        check("Host 头伪造时连页面也拿不到 -> 403", code == 403, f"code={code}")

        code, _ = request("/api/status", key=key, method="OPTIONS")
        check("预检 OPTIONS -> 403", code == 403, f"code={code}")

        code, _ = request("/api/status", key=key, origin=f"http://127.0.0.1:{PORT}")
        check("同源 Origin -> 放行", code == 200, f"code={code}")

        print("\n不可逆动作的防护（创建订单）")
        code, body = request("/api/order", key=key, method="POST",
                             body={"storeCode": "3450121", "takeWayCode": "eat-in",
                                   "items": [{"productCode": "1100", "quantity": 1}]})
        check("/api/order 缺 confirm -> 400（不会被误触发）",
              code == 400 and "confirm" in body, f"code={code} body={body[:120]}")

        code, body = request("/api/order", key=key, method="POST",
                             body={"confirm": True, "storeCode": "3450121"})
        check("/api/order 缺必要字段 -> 400", code == 400, f"code={code} body={body[:120]}")

        print("\n未连接账号时的行为")
        for name, payload in (("menu", {"storeCode": "3450121"}),
                              ("price", {"storeCode": "3450121", "items": [{"productCode": "1100", "quantity": 1}]})):
            code, body = request(f"/api/{name}", key=key, method="POST", body=payload)
            check(f"/api/{name} -> 409 并提示填 Token",
                  code == 409 and "Token" in body, f"code={code} body={body[:120]}")

        code, body = request("/api/order", key=key, method="POST",
                             body={"confirm": True, "storeCode": "3450121",
                                   "takeWayCode": "eat-in",
                                   "items": [{"productCode": "1100", "quantity": 1}]})
        check("/api/order 未连接账号 -> 409", code == 409, f"code={code} body={body[:120]}")

        print("\nToken 泄漏检查")
        leak = re.search(r"\b(?:ghp_|github_pat_|mcd_)[A-Za-z0-9_-]{10,}", page)
        check("页面内没有 Token 形态的字符串", leak is None, leak.group(0)[:14] if leak else "")
        code, body = request("/api/status", key=key)
        leak2 = re.search(r"\b(?:ghp_|github_pat_|mcd_)[A-Za-z0-9_-]{10,}", body)
        check("/api/status 不回显 Token", leak2 is None, leak2.group(0)[:14] if leak2 else body[:120])
        check("/api/status 声明 Token 只存内存", '"tokenStored":"memory-only"' in body.replace('": "', '":"'),
              body[:200])

        print("\n静态页面完整性")
        check("注入后的页面仍是完整可渲染的页面",
              'id="micBtn"' in page and 'id="live"' in page and 'id="typeInput"' in page
              and "/* ---VOICE-BEGIN--- */" in page and page.rstrip().endswith("</html>"))

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            proc.kill()

    print()
    if failures:
        print(f"{failures} 项失败")
        return 1
    print("全部通过：本地工具模式的安全闸门与不可逆动作防护符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
