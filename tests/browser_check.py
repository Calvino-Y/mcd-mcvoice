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
      log.push(["门店总数", A.State.stores.length, A.State.stores[0] && A.State.stores[0].storeCode,
                A.State.stores[A.State.stores.length - 1] && A.State.stores[A.State.stores.length - 1].storeCode]);

      // 先试着选一家**没有菜单快照**的门店：必须被挡住并说明原因，
      // 绝不能拿别家店的菜单价格替这一家点餐
      A.handleUtterance("第一家");
      log.push(["选无快照门店", A.State.step, A.State.store && A.State.store.storeCode]);

      // 再用店名选快照覆盖的那家（演示库里是第 4 家，距离 671 米的麦当劳天府三街餐厅）
      A.handleUtterance("麦当劳天府三街餐厅");
      log.push(["按店名选门店", A.State.step, A.State.store && A.State.store.storeCode]);

      A.handleUtterance("来一份巨无霸");
      log.push(["加购巨无霸", A.State.cart.length, A.State.cart[0] && A.State.cart[0].code, A.State.cart[0] && A.State.cart[0].qty]);

      A.handleUtterance("再来两份薯条");
      log.push(["加购薯条x2", A.State.cart.length, A.State.cart[1] && A.State.cart[1].qty]);

      A.handleUtterance("算价");
      await wait(400);
      log.push(["算价", A.State.step, A.State.quoted && A.State.quoted.price, A.State.quoted && A.State.quoted.estimated]);

      A.handleUtterance("堂食");
      log.push(["取餐方式-开关关闭", A.State.step]);

      // 打开下单开关，走一遍到「等待确认」
      document.getElementById("allowOrder").checked = true;
      A.handleUtterance("外带");
      log.push(["取餐方式-开关打开", A.State.step, A.State.takeWay]);

      A.handleUtterance("确认");
      await wait(400);
      log.push(["演示模式说确认", A.State.step, !!A.State.orderResult]);

      // 歧义追问
      A.State.step = "browse"; A.State.pendingTies = null;
      A.handleUtterance("鸡腿堡");
      log.push(["歧义追问", !!A.State.pendingTies, A.State.pendingTies && A.State.pendingTies.ties.length]);

      // 退回上一步 / 取消
      A.handleUtterance("退回上一步");
      log.push(["退回", A.State.step]);
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

# 第 2 遍：抹掉语音识别能力，验证降级路径
NODEG_SPEECH = """<script>
try {
  delete window.SpeechRecognition;
  delete window.webkitSpeechRecognition;
} catch (e) {
  window.SpeechRecognition = undefined;
  window.webkitSpeechRecognition = undefined;
}
window.__noSpeech = true;
window.__speechProbe = [];
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
      A.begin();
      log.push(["开始", A.State.step, A.State.started]);
      await wait(120);
      // 语音识别不可用时，开始流程必须给出明确提示而不是静默失败
      var alertText = document.getElementById("alert").textContent || "";
      var saidText = document.getElementById("said").textContent || "";
      o.alertText = alertText.slice(0, 200);
      o.saidText = saidText.replace(/\\s+/g," ").slice(0, 200);

      // 打字兜底：整条流程照样要能走完
      A.handleUtterance("麦当劳天府三街餐厅");
      A.handleUtterance("来一份巨无霸");
      A.handleUtterance("一杯可乐");
      A.handleUtterance("算价");
      await wait(400);
      log.push(["打字走完到算价", A.State.step, A.State.cart.length, A.State.quoted && A.State.quoted.price]);
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
    check("开始后进入门店步骤", rows_ok(steps, "begin", 1, "store"))
    check("选了一家没有菜单快照的门店时被挡住（仍停在门店步骤、没有选中）",
          rows_ok(steps, "选无快照门店", 1, "store")
          and (steps.get("选无快照门店") or [None, None, None])[2] is None,
          str(steps.get("选无快照门店")))
    check("按店名「麦当劳天府三街餐厅」能选中快照覆盖的门店 3450121",
          rows_ok(steps, "按店名选门店", 1, "browse")
          and (steps.get("按店名选门店") or [None, None, None])[2] == "3450121",
          str(steps.get("按店名选门店")))
    check("「来一份巨无霸」加购 1 件巨无霸（1100）",
          (steps.get("加购巨无霸") or [])[1] == 1 and (steps.get("加购巨无霸") or [None, None, None])[2] == "1100",
          str(steps.get("加购巨无霸")))
    check("「再来两份薯条」的数量解析正确（2 份）",
          (steps.get("加购薯条x2") or [None, None])[2] == 2, str(steps.get("加购薯条x2")))
    check("「算价」进入报价步骤且给出金额",
          rows_ok(steps, "算价", 1, "price") and (steps.get("算价") or [None, None, None])[2] > 0,
          str(steps.get("算价")))
    check("演示模式的报价被标记为 estimated（不会冒充正式报价）",
          (steps.get("算价") or [None, None, None, None])[3] is True, str(steps.get("算价")))
    check("下单开关关闭时说「堂食」流程直接结束、不产生订单",
          rows_ok(steps, "取餐方式-开关关闭", 1, "done"), str(steps.get("取餐方式-开关关闭")))
    check("开启下单开关后说「外带」进入下单确认步骤",
          rows_ok(steps, "取餐方式-开关打开", 1, "confirm") and (steps.get("取餐方式-开关打开") or [None, None])[2] == "take-in-store",
          str(steps.get("取餐方式-开关打开")))
    check("演示模式下说「确认」流程收尾且绝不产生订单",
          rows_ok(steps, "演示模式说确认", 1, "done")
          and (steps.get("演示模式说确认") or [None, None, None])[2] is False,
          str(steps.get("演示模式说确认")))
    check("说不清楚的「鸡腿堡」会触发歧义追问",
          (steps.get("歧义追问") or [None, False])[1] is True, str(steps.get("歧义追问")))
    check("「退回上一步」从点餐退回门店步骤",
          rows_ok(steps, "退回", 1, "store"), str(steps.get("退回")))
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
        check("给出明确的降级提示（提示用户改用打字）",
              ("打字" in (res2.get("saidText") or "")) or ("输入框" in (res2.get("alertText") or ""))
              or ("打字" in (res2.get("alertText") or "")),
              f"said={res2.get('saidText','')[:80]} | alert={res2.get('alertText','')[:80]}")
        st = {row[0]: row for row in (res2.get("log") or [])}
        check("打字输入照样能走完流程到报价",
              rows_ok(st, "打字走完到算价", 1, "price") and (st.get("打字走完到算价") or [None, None, None, None])[3] > 0,
              str(st.get("打字走完到算价")))

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
