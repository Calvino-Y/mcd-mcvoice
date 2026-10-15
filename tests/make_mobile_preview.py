# -*- coding: utf-8 -*-
"""手机宽度（390px）的截图验收。

为什么需要专门的脚本：headless Chrome 有 **500px 的最小窗宽**，
--window-size=390 会被钳制到 500，媒体查询永远不会命中手机断点——
量出来的「手机版」其实是假的。

做法：把主页面装进一个 390px 宽的 <iframe> 再截外层。
iframe 的视口就是 390px，媒体查询、flex 换行都按真实手机计算。

顺带伪装成微信内置浏览器（删掉原生语音识别 + 改 UA），
验收的是中国用户手机上真实的那条键盘通道。
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "index.html"
OUT = ROOT / "docs" / "preview-mobile.png"

BROWSERS = [
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
]

FAKE_WECHAT = """<script>
try { delete window.SpeechRecognition; delete window.webkitSpeechRecognition; } catch (e) {}
try {
  Object.defineProperty(navigator, "userAgent", { configurable: true,
    value: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
         + "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.49 NetType/WIFI" });
} catch (e) {}
</script>"""

DRIVER = """<script>
window.addEventListener("load", function(){
  setTimeout(function(){
    var A = window.__MCVOICE_APP__;
    if (!A) return;
    A.begin();
    A.handleUtterance("来一份巨无霸");
    A.handleUtterance("渴乐");
    A.handleUtterance("再来两份薯条");
  }, 400);
});
</script>"""


def find_browser():
    for p in BROWSERS:
        if pathlib.Path(p).exists():
            return p
    return None


def main() -> int:
    br = find_browser()
    if not br:
        print("找不到 Chrome / Edge")
        return 1
    work = ROOT / "docs"
    html = SRC.read_text(encoding="utf-8")
    # 伪装脚本必须插在主脚本之前（否则 UA / 能力检测来不及生效）
    (work / "_mobile.html").write_text(
        html.replace("<script>", FAKE_WECHAT + "<script>", 1).replace("</body>", DRIVER + "</body>", 1),
        encoding="utf-8", newline="\n")
    shell = ('<!DOCTYPE html><html><body style="margin:0;background:#888">'
             '<iframe src="./_mobile.html" style="width:390px;height:1500px;border:0;display:block">'
             "</iframe></body></html>")
    (work / "_shell.html").write_text(shell, encoding="utf-8", newline="\n")

    frame = work / "_m_frame.png"
    subprocess.run(
        [br, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--force-device-scale-factor=1", "--hide-scrollbars",
         "--window-size=500,1500", "--virtual-time-budget=9000",
         f"--screenshot={frame}", (work / "_shell.html").as_uri()],
        capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=240,
        check=True)

    from PIL import Image
    im = Image.open(frame)
    im.crop((0, 0, 390, min(1500, im.size[1]))).save(OUT)
    frame.unlink(missing_ok=True)
    (work / "_mobile.html").unlink(missing_ok=True)
    (work / "_shell.html").unlink(missing_ok=True)
    print(f"docs/preview-mobile.png 已生成（390px 视口，微信键盘通道）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
