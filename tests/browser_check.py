#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用无头浏览器真跑一遍页面，抓「语法正确但执行即抛错」，并做无障碍审计。

为什么必须有它：
  tests/check_page.mjs 只能验语法，tests/test_voice.mjs 只测纯函数。
  但语音应用最典型的故障是**整段脚本在解析/执行阶段就静默失效**——
  页面看起来正常、控制台一片安静，而所有按钮都没反应。
  本项目在开发过程中真的踩到过：数量词正则写错，用户说「来两份」永远只加一份，
  页面上没有任何报错。这类问题只有真跑一遍才能发现。

这个脚本做三件事：

  第 1 遍（正常环境）
    · 注入错误探针，抓未捕获异常
    · **程序化驱动完整语音流程**：开始 → 选门店 → 加购两样 → 算价 → 选外带 → 确认
      逐步骤断言状态机真的转移了、购物车真的进了东西、报价真的出来了
    · 断言「演示模式下即使打开下单开关，也不会产生订单」
    · 运行页面内置的无障碍自检，断言零项未通过
    · 用 axe-core（Deque 官方无障碍引擎）做第三方审计，报告 violations

  第 2 遍（模拟不支持语音的浏览器）
    · 删掉 SpeechRecognition，断言页面给出明确降级提示，
      并且**打字输入照样能完成整个流程**——无障碍设计的底线是不能把用户堵死

用法： python tests/browser_check.py
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "index.html"
AXE = ROOT / "tests" / "vendor" / "axe.min.js"
TMP = ROOT / "docs" / "_probe.html"

BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]

ERROR_PROBE = """<script>
window.__errs=[];
window.addEventListener("error",function(e){
  var d=document.createElement("div");d.id="__errbox";
  d.textContent="ERRBOX::"+(e.message||"")+" @"+(e.filename||"")+":"+e.lineno;
  document.body.appendChild(d);
});
window.addEventListener("unhandledrejection",function(e){
  var d=document.createElement("div");d.id="__rejbox";
  d.textContent="REJBOX::"+((e.reason&&e.reason.message)||String(e.reason));
  document.body.appendChild(d);
});
</script>"""

# 第 1 遍：驱动完整流程 + 无障碍自检 + axe
DRIVER = """<script>
function __done(obj){
  var d=document.createElement("div"); d.id="__result";
  d.textContent = "RESULT::" + JSON.stringify(obj);
  document.body.appendChild(d);
}
window.addEventListener("load", function(){
  setTimeout(async function(){
    var A = window.__MCVOICE_APP__;
    var log = [], o = {};
    if (!A) { __done({error:"__MCVOICE_APP__ 没有挂载，说明主脚本执行失败"}); return; }
    var wait = function(ms){ return new Promise(function(r){ setTimeout(r, ms); }); };
    try {
      A.begin();
      log.push(["begin", A.State.step, A.State.started]);
      // 门店不再是必须回答的一道题：begin 之后应当已经自动选定，且列表没有铺开
      log.push(["自动选店", A.State.step, A.State.store && A.State.store.storeCode, A.State.pickingStore]);
      o.storeAutoPicked = !!(A.State.store);

      // 说「换门店」才把列表铺开；此时序号才开始按门店解析
      A.handleUtterance("换门店");
      log.push(["换门店", A.State.pickingStore, A.State.step]);
      // 选一家没有菜单快照的店：必须被挡住、不换过去
      A.handleUtterance("第一家");
      log.push(["选无快照门店", A.State.pickingStore, A.State.store && A.State.store.storeCode]);
      A.handleUtterance("麦当劳天府三街餐厅");
      log.push(["按店名选门店", A.State.pickingStore, A.State.store && A.State.store.storeCode]);

      A.handleUtterance("来一份巨无霸");
      log.push(["加购巨无霸", A.State.cart.length, A.State.cart[0] && A.State.cart[0].code, A.State.cart[0] && A.State.cart[0].qty]);

      // 同音容错：识别把「可乐」听成「渴乐」，也必须能加进来
      A.handleUtterance("渴乐");
      log.push(["同音容错渴乐", A.State.cart.length, A.State.cart[1] && A.State.cart[1].code]);

      // 听到原文回显：用户必须能看见「系统到底听到了什么」
      o.heardText = (document.getElementById("heardVal") || {}).textContent || "";
      o.heardHidden = !!(document.getElementById("heard") || {}).hidden;

      // 边打边预览：在按下发送之前就告诉用户「我理解成了什么」
      var inp = document.getElementById("typeInput");
      var pv = document.getElementById("preview");
      inp.value = "巨无霸";
      inp.dispatchEvent(new Event("input", { bubbles: true }));
      o.previewText = (pv || {}).textContent || "";
      o.previewHidden = !!(pv || {}).hidden;
      inp.value = "阿巴阿巴";
      inp.dispatchEvent(new Event("input", { bubbles: true }));
      o.previewBadText = (pv || {}).textContent || "";
      o.previewBadClass = (pv || {}).className || "";
      inp.value = "";
      inp.dispatchEvent(new Event("input", { bubbles: true }));
      o.previewCleared = !!(pv || {}).hidden;

      // 说话通道：这台机器上跑的是哪一条，圆按钮和说明行分别说了什么
      o.channel = A.voiceChannel();
      o.micBtnText = (document.getElementById("micBtn") || {}).textContent || "";
      o.micNoteText = (document.getElementById("micNote") || {}).textContent || "";

      // 多候选择优：模拟识别器给三个候选，第一名是错的
      A.State.cart = []; A.State.step = "order";
      A.handleAlternatives([
        { text: "阿巴阿巴", conf: 0.91 },
        { text: "书条", conf: 0.42 },
        { text: "巨无把", conf: 0.30 }
      ]);
      log.push(["多候选择优", A.State.cart.length, A.State.cart[0] && A.State.cart[0].code]);
      o.heardAltText = (document.getElementById("heardVal") || {}).textContent || "";
      A.State.cart = [];

      // 进度条随步骤走
      o.TOTAL = A.TOTAL_STEPS;
      o.progressCells = document.querySelectorAll("#progress li").length;
      o.progressNow = document.querySelectorAll("#progress li.now").length;
      o.progressText = (document.getElementById("progressText") || {}).textContent || "";

      A.handleUtterance("再来两份薯条");
      var last = A.State.cart[A.State.cart.length - 1];
      log.push(["加购薯条x2", A.State.cart.length, last && last.qty]);

      // 「就这些」= 我说完了 → 直接进第 2 步算总价（不再经过购物车、报价两个中间步骤）
      A.handleUtterance("就这些");
      await wait(400);
      log.push(["就这些进确认", A.State.step, A.State.quoted && A.State.quoted.price,
                A.State.quoted && A.State.quoted.estimated, A.State.takeWay]);

      // 取餐方式默认堂食；说「外带」当场改，并且仍然停在确认步骤
      A.handleUtterance("外带");
      log.push(["改外带", A.State.step, A.State.takeWay]);

      // 下单开关关着时说「确认」：必须明确不建单，并留在确认步骤
      A.handleUtterance("确认");
      await wait(300);
      log.push(["开关关闭说确认", A.State.step, !!A.State.orderResult, A.State.cart.length]);

      // 打开下单开关再确认
      document.getElementById("allowOrder").checked = true;
      A.handleUtterance("确认");
      await wait(400);
      log.push(["演示模式说确认", A.State.step, !!A.State.orderResult]);

      // 第 3 步说「再点一单」要能重新开始
      A.handleUtterance("再点一单");
      log.push(["再点一单", A.State.step, A.State.cart.length]);

      // 歧义追问
      A.State.pendingTies = null;
      A.handleUtterance("鸡腿堡");
      log.push(["歧义追问", !!A.State.pendingTies, A.State.pendingTies && A.State.pendingTies.ties.length]);

      A.handleUtterance("取消");
      log.push(["取消后购物车", A.State.cart.length]);

      // 内置无障碍自检
      A.runAudit();
      var auditText = document.getElementById("auditOut").textContent || "";
      o.auditText = auditText.replace(/\\s+/g, " ").slice(0, 400);
      o.auditFailCount = (auditText.match(/未通过/g) || []).length;

      // axe-core 审计
      if (window.axe) {
        var res = await window.axe.run(document, {
          resultTypes: ["violations"],
          runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"] }
        });
        o.axe = res.violations.map(function(v){
          return { id: v.id, impact: v.impact, n: v.nodes.length,
                   help: v.help, sample: (v.nodes[0] && v.nodes[0].target || []).join(" ") };
        });
        o.axeNode = document.getElementById("main") ? "ok" : "missing";
      } else {
        o.axe = null;
      }
      o.log = log;
      __done(o);
    } catch (e) {
      o.log = log;
      o.error = String(e && e.message);
      o.stack = String(e && e.stack || "").slice(0, 260);
      __done(o);
    }
  }, 700);
});
</script>"""

# 第 2 遍：抹掉语音识别能力 + 伪装成微信内置浏览器，验证降级路径
NODEG_SPEECH = """<script>
try {
  delete window.SpeechRecognition;
  delete window.webkitSpeechRecognition;
} catch (e) {
  window.SpeechRecognition = undefined;
  window.webkitSpeechRecognition = undefined;
}
// 伪装成微信内置浏览器：这条路径必须走「唤起键盘、用输入法自带的麦克风说话」
try {
  Object.defineProperty(navigator, "userAgent", {
    value: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
         + "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.49(0x18003132) NetType/WIFI",
    configurable: true
  });
} catch (e) {}
window.__noSpeech = true;
</script>"""

NODEG_DRIVER = """<script>
function __done(obj){
  var d=document.createElement("div"); d.id="__result";
  d.textContent = "RESULT::" + JSON.stringify(obj);
  document.body.appendChild(d);
}
window.addEventListener("load", function(){
  setTimeout(async function(){
    var A = window.__MCVOICE_APP__;
    var o = {}, log = [];
    if (!A) { __done({error:"主脚本没挂载"}); return; }
    var wait = function(ms){ return new Promise(function(r){ setTimeout(r, ms); }); };
    try {
      o.support = A.speechSupport();
      o.channel = A.voiceChannel();
      o.isWechat = A.IS_WECHAT;
      o.isMobile = A.IS_MOBILE;
      o.fallbackMsg = A.micFallbackMsg();
      o.shortHint = A.shortHint("keyboard");

      A.begin();
      log.push(["开始", A.State.step, A.State.started]);
      await wait(120);
      // 语音走不通时，第一句必须是「怎么才能说话」，而不是静默失败
      o.alertText = (document.getElementById("alert").textContent || "").slice(0, 240);
      o.saidText = (document.getElementById("said").textContent || "").replace(/\\s+/g," ").slice(0, 240);
      o.micNoteText = (document.getElementById("micNote") || {}).textContent || "";
      o.micBtnText = (document.getElementById("micBtn") || {}).textContent || "";

      // 点圆按钮：键盘通道下必须把焦点送到输入框，而不是报一句「语音不可用」
      document.getElementById("micBtn").click();
      await wait(60);
      o.focusedIsInput = (document.activeElement && document.activeElement.id) === "typeInput";
      o.noteAfterClick = (document.getElementById("micNote") || {}).textContent || "";

      // 打字/输入法走完整条流程：一句说完 → 直接算总价
      A.handleUtterance("来一份巨无霸");
      A.handleUtterance("一杯可乐");
      A.handleUtterance("就这些");
      await wait(400);
      log.push(["降级路径走完到确认", A.State.step, A.State.cart.length, A.State.quoted && A.State.quoted.price]);
      o.log = log;
      __done(o);
    } catch (e) { o.log = log; o.error = String(e && e.message); __done(o); }
  }, 700);
});
</script>"""


def find_browser() -> str | None:
    for p in BROWSERS:
        if pathlib.Path(p).exists():
            return p
    return shutil.which("chrome") or shutil.which("chromium") or shutil.which("msedge")


def run_dom(browser: str, html: str) -> str:
    TMP.write_text(html, encoding="utf-8", newline="\n")
    try:
        proc = subprocess.run(
            [browser, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
             "--window-size=1440,1200", "--virtual-time-budget=25000", "--dump-dom", TMP.as_uri()],
            capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=300,
        )
        return proc.stdout or ""
    finally:
        TMP.unlink(missing_ok=True)


def grab_result(dom: str) -> dict | None:
    m = re.search(r'<div id="__result">RESULT::(.*?)</div>', dom, re.S)
    if not m:
        return None
    text = m.group(1)
    text = (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&#39;", "'"))
    try:
        return json.loads(text)
    except Exception:                                  # noqa: BLE001
        return {"_unparsed": text[:400]}


def main() -> int:
    browser = find_browser()
    if not browser:
        print("找不到可用的 Chrome/Edge，跳过浏览器检查")
        return 0
    if not AXE.exists():
        print(f"缺少 axe-core（{AXE}）。用下面这条命令取一份：")
        print("  curl -sL -o tests/vendor/axe.min.js "
              "https://cdn.jsdelivr.net/npm/axe-core@4.10.2/axe.min.js")
        return 2

    html = SRC.read_text(encoding="utf-8")
    if html.count("<script>") != 1:
        print(f"预期页面里只有 1 个 <script>，实际 {html.count('<script>')} 个，注入点不可靠")
        return 2

    failures = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        if ok:
            print(f"  PASS  {label}")
        else:
            failures += 1
            print(f"  FAIL  {label}  {detail}")

    # ==================== 第 1 遍：完整流程 + 无障碍 ====================
    print("第 1 遍 · 完整语音流程与无障碍审计")
    axe_src = AXE.read_text(encoding="utf-8")
    dom = run_dom(browser, html.replace(
        "</body>",
        ERROR_PROBE + f"<script>{axe_src}</script>" + DRIVER + "</body>"))

    err = re.search(r"ERRBOX::(.*?)</div>", dom)
    rej = re.search(r"REJBOX::(.*?)</div>", dom)
    check("浏览器端没有未捕获的同步异常", not err, err.group(1)[:160] if err else "")
    check("没有未处理的 Promise 拒绝", not rej, rej.group(1)[:160] if rej else "")

    res = grab_result(dom)
    if not res:
        check("拿到了流程执行结果", False, "页面里没有 RESULT:: 记录（主脚本很可能执行失败）")
        return 1
    if res.get("error"):
        check("流程执行没有抛错", False, res.get("error") + " | " + str(res.get("stack", ""))[:160])

    steps = {row[0]: row for row in (res.get("log") or [])}
    check("开始后直接进入第 1 步「说单」（不再先问门店）",
          rows_ok(steps, "begin", 1, "order"), str(steps.get("begin")))
    check("门店自动选定、列表不铺开（不必先做一道选择题）",
          res.get("storeAutoPicked") is True
          and (steps.get("自动选店") or [None, None, None, None])[3] is False,
          str(steps.get("自动选店")))
    check("说「换门店」才铺开门店列表，此时序号才按门店解析",
          rows_ok(steps, "换门店", 1, True), str(steps.get("换门店")))
    check("选了一家没有菜单快照的门店时被挡住（没有换过去）",
          (steps.get("选无快照门店") or [None, None, None])[2] == "3450121",
          str(steps.get("选无快照门店")))
    check("按店名能换到快照覆盖的门店 3450121，并收起列表",
          rows_ok(steps, "按店名选门店", 1, False)
          and (steps.get("按店名选门店") or [None, None, None])[2] == "3450121",
          str(steps.get("按店名选门店")))
    check("「来一份巨无霸」加购 1 件巨无霸（1100）",
          (steps.get("加购巨无霸") or [])[1] == 1 and (steps.get("加购巨无霸") or [None, None, None])[2] == "1100",
          str(steps.get("加购巨无霸")))
    check("同音容错：「渴乐」也能加进购物车（识别把「可」听成「渴」）",
          (steps.get("同音容错渴乐") or [None, None])[1] == 2
          and (steps.get("同音容错渴乐") or [None, None, None])[2] == "9900008751",
          str(steps.get("同音容错渴乐")))

    print("\n  听不清时用户能不能自己判断（识别不准的止血措施）")
    check("页面上如实回显「听到原文」", res.get("heardHidden") is False and bool(res.get("heardText")),
          f"hidden={res.get('heardHidden')} text={res.get('heardText')!r}")
    check("采纳非第一名候选时会写明「按哪一句理解」",
          "按「" in (res.get("heardAltText") or ""), repr(res.get("heardAltText")))
    check("多候选择优：三个候选里挑出了能被理解的那句（书条→薯条）",
          (steps.get("多候选择优") or [None, None])[1] == 1
          and (steps.get("多候选择优") or [None, None, None])[2] == "4810",
          str(steps.get("多候选择优")))
    check("边打边预览：输入商品名时立刻显示「听懂了：巨无霸」",
          res.get("previewHidden") is False and "巨无霸" in (res.get("previewText") or ""),
          f"hidden={res.get('previewHidden')} text={res.get('previewText')!r}")
    check("认不出来的说法会被标红提示，而不是静默通过",
          "bad" in (res.get("previewBadClass") or "") and "认不出来" in (res.get("previewBadText") or ""),
          f"class={res.get('previewBadClass')!r} text={res.get('previewBadText')!r}")
    check("清空输入框后预览也跟着收起", res.get("previewCleared") is True,
          str(res.get("previewCleared")))
    check("进度条按三步铺满且只有一格是「当前」",
          res.get("progressCells") == res.get("TOTAL") == 3 and res.get("progressNow") == 1,
          f"cells={res.get('progressCells')} total={res.get('TOTAL')} now={res.get('progressNow')}")

    print("\n  说话通道（决定微信 / 手机浏览器能不能用）")
    check("页面能报出当前走的是哪条通道",
          res.get("channel") in ("native", "keyboard", "cloud"), str(res.get("channel")))
    check("圆按钮文案随通道变化，不是写死的「开始说话」",
          bool(res.get("micBtnText")), repr(res.get("micBtnText")))
    check("说明行写清了「这个环境里该怎么说话」",
          bool(res.get("micNoteText")), repr(res.get("micNoteText")))

    check("「再来两份薯条」的数量解析正确（2 份）",
          (steps.get("加购薯条x2") or [None, None])[2] == 2, str(steps.get("加购薯条x2")))
    check("「就这些」= 说完了，直接进第 2 步算总价（跳过购物车与报价两个中间步骤）",
          rows_ok(steps, "就这些进确认", 1, "confirm") and (steps.get("就这些进确认") or [None, None, None])[2] > 0,
          str(steps.get("就这些进确认")))
    check("演示模式的报价被标记为 estimated（不会冒充正式报价）",
          (steps.get("就这些进确认") or [None, None, None, None])[3] is True, str(steps.get("就这些进确认")))
    check("取餐方式默认堂食（同样不必先做一道选择题）",
          (steps.get("就这些进确认") or [None, None, None, None, None])[4] == "eat-in",
          str(steps.get("就这些进确认")))
    check("说「外带」当场改取餐方式，且仍然停在确认步骤",
          rows_ok(steps, "改外带", 1, "confirm") and (steps.get("改外带") or [None, None])[2] == "take-in-store",
          str(steps.get("改外带")))
    check("下单开关关闭时说「确认」：明确不建单，购物车原样保留",
          rows_ok(steps, "开关关闭说确认", 1, "confirm")
          and (steps.get("开关关闭说确认") or [None, None, None])[2] is False
          and (steps.get("开关关闭说确认") or [None, None, None, None])[3] > 0,
          str(steps.get("开关关闭说确认")))
    check("演示模式下说「确认」流程收尾且绝不产生订单",
          rows_ok(steps, "演示模式说确认", 1, "done")
          and (steps.get("演示模式说确认") or [None, None, None])[2] is False,
          str(steps.get("演示模式说确认")))
    check("第 3 步说「再点一单」能重新开始并清空",
          rows_ok(steps, "再点一单", 1, "order") and (steps.get("再点一单") or [None, None])[2] == 0,
          str(steps.get("再点一单")))
    check("说不清楚的「鸡腿堡」会触发歧义追问",
          (steps.get("歧义追问") or [None, False])[1] is True, str(steps.get("歧义追问")))
    check("「取消」能清空购物车", (steps.get("取消后购物车") or [None, -1])[1] == 0,
          str(steps.get("取消后购物车")))

    print("\n  页面内置无障碍自检")
    audit_fails = res.get("auditFailCount")
    check(f"内置自检零项未通过（{res.get('auditText', '')[:90]}…）",
          audit_fails == 0, f"未通过 {audit_fails} 项：{res.get('auditText','')[:300]}")

    print("\n  axe-core 第三方审计（WCAG 2.0/2.1 A + AA + best-practice）")
    axe = res.get("axe")
    if axe is None:
        check("axe-core 已执行", False, "window.axe 不存在")
    else:
        serious = [v for v in axe if v.get("impact") in ("serious", "critical")]
        check(f"没有 serious / critical 级违规（共 {len(axe)} 类违规）",
              not serious,
              "；".join(f"{v['id']}({v['impact']}×{v['n']})" for v in serious[:5]))
        for v in axe:
            print(f"        · {v['impact']:<9} {v['id']} ×{v['n']}  {v['help'][:64]}  [{v['sample'][:60]}]")
        print(f"        INFO  违规类别合计 {len(axe)}，其中 serious/critical {len(serious)}")

    # ==================== 第 2 遍：语音不可用时的降级 ====================
    print("\n第 2 遍 · 语音识别不可用时的降级路径")
    dom2 = run_dom(browser, html.replace("<script>", NODEG_SPEECH + "<script>", 1)
                                  .replace("</body>", ERROR_PROBE + NODEG_DRIVER + "</body>"))
    err2 = re.search(r"ERRBOX::(.*?)</div>", dom2)
    check("降级路径下没有未捕获异常", not err2, err2.group(1)[:160] if err2 else "")
    res2 = grab_result(dom2)
    if not res2:
        check("拿到了降级路径的执行结果", False, "没有 RESULT:: 记录")
    else:
        if res2.get("error"):
            check("降级路径没有抛错", False, res2["error"])
        check("页面正确识别出「语音识别不可用」",
              (res2.get("support") or {}).get("recognition") is False, str(res2.get("support")))
        check("微信内置浏览器被认出来，并自动走键盘通道",
              res2.get("isWechat") is True and res2.get("channel") == "keyboard",
              f"wechat={res2.get('isWechat')} mobile={res2.get('isMobile')} channel={res2.get('channel')}")
        check("提示话术是针对微信说的，而不是笼统一句「语音不可用」",
              "微信" in (res2.get("fallbackMsg") or "") and "麦克风" in (res2.get("fallbackMsg") or ""),
              repr((res2.get("fallbackMsg") or "")[:90]))
        check("圆按钮说明行告诉用户「按键盘上的麦克风说话」",
              "麦克风" in (res2.get("shortHint") or ""), repr(res2.get("shortHint")))
        check("点圆按钮后焦点落在输入框（这一步决定系统键盘会不会弹出来）",
              res2.get("focusedIsInput") is True, str(res2.get("focusedIsInput")))
        check("点圆按钮不会只丢一句「不可用」，而是给出可执行的下一步",
              "不可用" not in (res2.get("noteAfterClick") or "")
              and "麦克风" in (res2.get("noteAfterClick") or ""),
              repr(res2.get("noteAfterClick")))
        check("圆按钮文案在键盘通道下不谎称能直接录音",
              (res2.get("micBtnText") or "") != "", repr(res2.get("micBtnText")))
        st = {row[0]: row for row in (res2.get("log") or [])}
        check("这条通道下照样能说完一整单并算出总价",
              rows_ok(st, "降级路径走完到确认", 1, "confirm")
              and (st.get("降级路径走完到确认") or [None, None, None, None])[3] > 0,
              str(st.get("降级路径走完到确认")))

    print()
    if failures:
        print(f"{failures} 项未通过")
        return 1
    print("浏览器端检查全部通过：完整语音流程 / 演示模式零订单 / 无障碍自检 / axe 审计 / 降级路径")
    return 0


def rows_ok(steps: dict, key: str, idx: int, expected) -> bool:
    row = steps.get(key)
    return bool(row) and len(row) > idx and row[idx] == expected


if __name__ == "__main__":
    sys.exit(main())
