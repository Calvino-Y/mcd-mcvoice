#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 README 用的预览图（docs/preview-full.png 长图 + docs/preview-top.png 头图）。

为什么不能手工截：
  这个页面的开头是「准备开始」，内容区写着「还没有内容」——直接截下来是一张
  空页面，恰好把最重要的东西（报价、购物车、无障碍自检结果）全漏掉了。
  所以这里用页面自己导出的 __MCVOICE_APP__ 把流程驱到「算价」那一步再截。

  另外页面高度跟着数据走，手工截容易截到一半或留一大片白。这里先量真实高度，
  按量到的高度截，最后把底部纯白部分裁掉。

依赖：headless Chrome/Edge + Pillow（只在生成预览图时需要，跑本项目不需要）。
用法： python tests/make_preview.py
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "index.html"
FULL = ROOT / "docs" / "preview-full.png"
TOP = ROOT / "docs" / "preview-top.png"

WIDTH = 1440
TOP_HEIGHT = 2050           # 头图高度，和上一个项目保持一致
BG = (255, 253, 247)        # 页面 --bg（暖白纸面）
BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]

# 把页面驱到一个「有东西可看」的状态：选好门店 → 加三样 → 算价 → 跑一遍自检。
# 尺寸探针写在最后，保证量到的是驱动之后的高度。
PREVIEW_DRIVER = """<script>
window.addEventListener("load", function(){
  setTimeout(async function(){
    var A = window.__MCVOICE_APP__;
    var wait = function(ms){ return new Promise(function(r){ setTimeout(r, ms); }); };
    if (A) {
      try {
        // 截图环境不需要页面自己朗读，关掉以免和截图时机打架
        var s = document.getElementById("speakOn");
        if (s) { s.checked = false; s.dispatchEvent(new Event("change", {bubbles:true})); }

        A.begin();
        A.handleUtterance("麦当劳天府三街餐厅");
        A.handleUtterance("来一份巨无霸");
        A.handleUtterance("一杯可乐");
        A.handleUtterance("再来一份中薯条");
        A.handleUtterance("算价");
        await wait(600);
        A.runAudit();
        await wait(300);
      } catch (e) {
        var err = document.createElement("div");
        err.id = "__driver_error";
        err.textContent = "DRIVER_ERROR::" + (e && e.message);
        document.body.appendChild(err);
      }
    }
    var d = document.createElement("div");
    d.id = "__metrics";
    d.style.display = "none";   // 只用来回传高度，不能漏到截图里
    d.textContent = "METRICS::" + Math.ceil(document.documentElement.scrollHeight);
    document.body.appendChild(d);
  }, 700);
});
</script>"""


def find_browser() -> str | None:
    for p in BROWSERS:
        if pathlib.Path(p).exists():
            return p
    return shutil.which("chrome") or shutil.which("chromium") or shutil.which("msedge")


def build_probe() -> pathlib.Path:
    probe = ROOT / "docs" / "_preview.html"
    html = SRC.read_text(encoding="utf-8")
    probe.write_text(html.replace("</body>", PREVIEW_DRIVER + "</body>", 1),
                     encoding="utf-8", newline="\n")
    return probe


def measure(browser: str, probe: pathlib.Path) -> int:
    """返回驱动之后页面的真实高度（CSS px）。"""
    proc = subprocess.run(
        [browser, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--hide-scrollbars", f"--window-size={WIDTH},1000",
         "--virtual-time-budget=12000", "--dump-dom", probe.as_uri()],
        capture_output=True, text=True, encoding="utf-8", errors="ignore",
        timeout=240)
    dom = proc.stdout or ""
    # 注意：驱动脚本自身的源码也在 DOM 里，所以不能直接搜 "DRIVER_ERROR::"，
    # 那会永远命中脚本正文。要搜真正被插入的元素。
    err = re.search(r'<div id="__driver_error">(.*?)</div>', dom, re.S)
    if err:
        raise SystemExit("预览驱动脚本报错：" + err.group(1)[:300])
    m = re.search(r"METRICS::(\d+)", dom)
    if not m:
        raise SystemExit("量不到页面高度：metrics 探针没有回值")
    return int(m.group(1))


def shoot(browser: str, probe: pathlib.Path, height: int, out: pathlib.Path) -> None:
    # 多给 240px 余量，避免把最后一行字切掉；裁白阶段会再收回来
    subprocess.run(
        [browser, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--hide-scrollbars", f"--window-size={WIDTH},{height + 240}",
         "--virtual-time-budget=12000",
         f"--screenshot={out}", probe.as_uri()],
        capture_output=True, text=True, encoding="utf-8", errors="ignore",
        timeout=300, check=True)


def crop_bottom(path: pathlib.Path) -> tuple[int, int]:
    """裁掉底部连续纯白行，返回最终尺寸。"""
    from PIL import Image

    im = Image.open(path).convert("RGB")
    px = im.load()
    w, h = im.size
    last = h - 1
    while last > 0:
        row = [px[x, last] for x in range(0, w, 7)]
        if any(abs(c[0] - BG[0]) + abs(c[1] - BG[1]) + abs(c[2] - BG[2]) > 12 for c in row):
            break
        last -= 1
    if last < h - 1:
        im = im.crop((0, 0, w, last + 24))
    im.save(path)
    return im.size


def make_top(path: pathlib.Path, height: int) -> tuple[int, int]:
    from PIL import Image

    im = Image.open(path).convert("RGB")
    w, h = im.size
    top = im.crop((0, 0, w, min(height, h)))
    top.save(TOP)
    return top.size


def main() -> int:
    browser = find_browser()
    if not browser:
        print("找不到 Chrome/Edge，无法生成预览图")
        return 0

    probe = build_probe()
    try:
        h = measure(browser, probe)
        print(f"驱动到「算价」后的页面高度 {h} px")
        shoot(browser, probe, h, FULL)
        print(f"已截图 {FULL.name}")
    finally:
        probe.unlink(missing_ok=True)

    size = crop_bottom(FULL)
    print(f"裁白后 {FULL.name}: {size[0]}x{size[1]}")

    tsize = make_top(FULL, TOP_HEIGHT)
    print(f"{TOP.name}: {tsize[0]}x{tsize[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
