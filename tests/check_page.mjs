#!/usr/bin/env node
/**
 * 页面结构自检（构建产物完整性）。
 *
 * 为什么必须有这个文件：
 *   这个页面是把「模板 + 注入数据」拼出来的。拼接类构建有一个典型故障：
 *   拼接**看起来成功了**——文件生成了、浏览器也打得开、界面还画得出来，
 *   但某个占位符没被替换（于是菜单是空的）、或者某个字段在数据整理阶段丢了
 *   （于是某一类目点开后什么都没有）。这类问题不会报错，只会让视障用户
 *   在某一句话之后突然「什么都听不到，也没人告诉他为什么」。
 *   所以在构建产物上做一次结构 + 数据自一致性检查，是最后一道拦网。
 *
 * 与 tests/test_voice.mjs 的分工：
 *   - 那里测**语义**（这句话理解成什么意图、指向哪个商品）
 *   - 这里测**结构**（占位符是否清干净、数据是否完整且自洽、无障碍骨架是否齐）
 *
 * 与 tests/browser_check.py 的分工：
 *   - 这里只读文件，不启浏览器，秒级给出结论（适合每次改完就跑）
 *   - 那里在真浏览器里跑流程 + axe 审计（适合提交前跑）
 *
 * 用法： node tests/check_page.mjs
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const HTML_PATH = path.join(ROOT, 'docs', 'index.html');
const MENU_PATH = path.join(ROOT, 'data', 'menu.json');
const PRICE_PATH = path.join(ROOT, 'data', 'price_raw.json');

const html = fs.readFileSync(HTML_PATH, 'utf8');
const menu = JSON.parse(fs.readFileSync(MENU_PATH, 'utf8'));
const priceRaw = JSON.parse(fs.readFileSync(PRICE_PATH, 'utf8'));

let pass = 0;
const failures = [];

function check(label, ok, detail) {
  if (ok) { pass++; console.log(`  PASS  ${label}`); }
  else { failures.push(label); console.log(`  FAIL  ${label}${detail ? '  ' + detail : ''}`); }
}

const KNOWN_HOMOPHONE_NOISE = /^(的|了|吧|吗|呢|一下|一个)$/;

console.log(`页面产物：docs/index.html（${(html.length / 1024).toFixed(1)} KB）`);
console.log(`数据集：${menu.items.length} 项餐品 / ${menu.categories.length} 个类目 / ${Object.keys(menu.aliases).length} 条口语别名\n`);

/* ==================== 1. 占位符必须被清干净 ==================== */
console.log('1. 构建占位符');

for (const token of ['__DATA__', '__PRICE__', '__BUILD__']) {
  check(`模板占位符 ${token} 没有残留在产物里`, !html.includes(token));
}
check('数据层已接管为 LIVE 优先写法', /let\s+DATA\s*=\s*\(LIVE\s*&&\s*LIVE\.menu\)\s*\|\|/.test(html),
      '期望 let DATA = (LIVE && LIVE.menu) || …');
check('报价快照已接管为 LIVE 优先写法', /const\s+PRICE_SNAPSHOT\s*=\s*\(LIVE\s*&&\s*LIVE\.price\)\s*\|\|/.test(html));
check('构建信息被真实对象替换（不再是 null）', /const\s+BUILD_INFO\s*=\s*\{/.test(html),
      '期望 const BUILD_INFO = {…}');

/* ==================== 2. 内联脚本可编译 + 离线自包含 ==================== */
console.log('\n2. 内联脚本与离线自包含');

const scriptTags = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)];
check('页面只有一个内联脚本块（无外链 JS）', scriptTags.length === 1, `实际 ${scriptTags.length} 个`);
check('内联脚本没有 src 属性（不依赖网络）',
      scriptTags.every((s) => !/\bsrc\s*=/.test(s[1])));

const inlineJs = scriptTags[0] ? scriptTags[0][2] : '';
let syntaxError = null;
try {
  new Function(inlineJs);
} catch (e) {
  syntaxError = e;
}
check('内联脚本语法可以通过 JS 引擎编译', syntaxError === null,
      syntaxError ? String(syntaxError.message) : '');
check('内联脚本里没有未转义的 </script>', !inlineJs.includes('</script>'));

check('没有外部样式表引用', !/<link\b[^>]*rel=["']?stylesheet/i.test(html));
check('没有内联事件属性（onclick / onchange / onkeydown）',
      !/\son(click|change|keydown|keyup|input|submit|focus|blur)\s*=/i.test(html));

/* ==================== 3. 注入的数据必须完整且自洽 ==================== */
console.log('\n3. 注入数据的完整性与自一致性');

const dataM = html.match(/let\s+DATA\s*=\s*\(LIVE\s*&&\s*LIVE\.menu\)\s*\|\|\s*([\s\S]*?);\s*\n/);
let injected = null;
if (dataM) {
  try { injected = JSON.parse(dataM[1]); } catch (e) { injected = null; }
}
check('页面内联的数据对象可以解析成 JSON', injected !== null);

if (injected) {
  check(`餐品数量为 ${menu.items.length} 项`, injected.items.length === menu.items.length,
        `实际 ${injected.items.length}`);
  check(`类目数量为 ${menu.categories.length} 个`, injected.categories.length === menu.categories.length);
  check(`口语别名 ${Object.keys(menu.aliases).length} 条`, Object.keys(injected.aliases).length === Object.keys(menu.aliases).length);
  check(`同音纠正 ${Object.keys(menu.homophones).length} 条`, Object.keys(injected.homophones).length === Object.keys(menu.homophones).length);
  check(`门店 ${menu.stores.length} 家`, injected.stores.length === menu.stores.length);
  check(`优惠券 ${menu.coupons.length} 张`, injected.coupons.length === menu.coupons.length);
  check('页面数据集与 data/menu.json 逐字节一致', JSON.stringify(injected) === JSON.stringify(menu));
}

const codes = new Set(menu.items.map((i) => i.code));
const catNames = new Set(menu.categories.map((c) => c.name));

check('餐品编码全局唯一', codes.size === menu.items.length,
      `${menu.items.length} 项里只有 ${codes.size} 个不同编码`);
check('每个餐品都能对上一个存在的类目',
      menu.items.every((i) => catNames.has(i.category)),
      menu.items.filter((i) => !catNames.has(i.category)).map((i) => i.name).join('、'));
check('每个类目引用的餐品编码都真实存在',
      menu.categories.every((c) => c.meals.every((m) => codes.has(m))),
      menu.categories.flatMap((c) => c.meals.filter((m) => !codes.has(m))).join('、'));
// 跨类目重叠是零售菜单的正常形态（「人气热卖」这类营销位会和品类位共存），
// 所以这里不断言「不重叠」，只把它作为信息打印出来留痕。
// 真正会伤到用户的是下面两条：类目内重复、以及有餐品任何类目都进不去。
{
  const perCat = [];
  for (const c of menu.categories) {
    const seen = new Set();
    const dup = c.meals.filter((m) => (seen.has(m) ? true : (seen.add(m), false)));
    if (dup.length) perCat.push(`${c.name}: ${dup.join('、')}`);
  }
  check('同一个类目内部没有重复收录同一餐品', perCat.length === 0, perCat.join(' | '));

  const covered = new Set(menu.categories.flatMap((c) => c.meals));
  const orphan = menu.items.filter((i) => !covered.has(i.code));
  check('每个餐品都至少被一个类目收录（否则按类目浏览永远找不到它）',
        orphan.length === 0, orphan.map((i) => i.name).join('、'));

  const crossed = [...covered].length - new Set(covered).size;
  console.log(`        INFO  跨类目重叠 ${crossed} 项（营销位与品类位共存，属正常形态）`);
  console.log(`        INFO  类目覆盖 ${covered.size} / ${menu.items.length} 项餐品`);
}

// 别名值是「一条口语说法 → 可能对应的若干餐品编码」的数组，
// 因为「安格斯」这种说法确实同时指向好几个不同商品，需要交给后面的歧义逻辑裁决。
{
  const badType = Object.entries(menu.aliases)
    .filter(([, v]) => !Array.isArray(v) || v.length === 0);
  check('每条别名都指向至少一个餐品编码', badType.length === 0,
        badType.map(([k]) => k).join('、'));

  const dangling = [];
  for (const [alias, targets] of Object.entries(menu.aliases)) {
    for (const c of targets) if (!codes.has(c)) dangling.push(`${alias}→${c}`);
  }
  check('别名指向的编码全部存在（没有指向已下架商品的死链）',
        dangling.length === 0, dangling.join('、'));

  check('别名表里没有空串或纯空白键',
        Object.keys(menu.aliases).every((k) => k.trim().length > 0));
}
check('同音纠正的目标都是真实存在的说法', (() => {
  const vocab = new Set([...Object.keys(menu.aliases), ...menu.items.map((i) => i.name)]);
  const bad = Object.entries(menu.homophones)
    .filter(([, to]) => !vocab.has(to) && ![...vocab].some((v) => v.includes(to)));
  return bad.length === 0;
})(), Object.entries(menu.homophones).filter(([, to]) =>
  ![...menu.aliases ? Object.keys(menu.aliases) : [], ...menu.items.map((i) => i.name)]
    .some((v) => v.includes(to))).map(([k, v]) => `${k}→${v}`).join('、'));

check('所有餐品都有正数价格', menu.items.every((i) => typeof i.price === 'number' && i.price > 0),
      menu.items.filter((i) => !(i.price > 0)).map((i) => i.name).join('、'));
check('名称字段都没有首尾空白', menu.items.every((i) => i.name === i.name.trim()));
check('没有名为空的餐品', menu.items.every((i) => i.name.length > 0));

check('演示快照门店确实在门店列表里（否则演示模式无从点餐）',
      menu.stores.some((s) => s.storeCode === menu.source.storeCode),
      `快照门店 ${menu.source.storeCode}`);
check('门店列表每家都有编码与名称',
      menu.stores.every((s) => s.storeCode && s.storeName));
check('优惠券限制被如实记录（不假装能自动套券）',
      typeof menu.couponLimitation === 'string' && menu.couponLimitation.includes('couponId'));

/* ==================== 4. 构建元信息与实际一致 ==================== */
console.log('\n4. 构建元信息');

const buildM = html.match(/const\s+BUILD_INFO\s*=\s*(\{[\s\S]*?\});/);
let build = null;
if (buildM) { try { build = JSON.parse(buildM[1]); } catch (e) { build = null; } }
check('构建元信息能解析', build !== null);
if (build) {
  check('元信息里的餐品数与数据一致', build.itemCount === menu.items.length,
        `元信息 ${build.itemCount} / 数据 ${menu.items.length}`);
  check('元信息里的类目数与数据一致', build.categoryCount === menu.categories.length);
  check('元信息里的别名数与数据一致', build.aliasCount === Object.keys(menu.aliases).length);
  check('元信息记录了采样日期与采样门店',
        Boolean(build.capturedAt) && Boolean(build.storeCode),
        JSON.stringify(build));
}

/* ==================== 4b. 拼音表（同音容错的底座）====================
   这一层的失败方式很隐蔽：表一旦缩水，拼音匹配就悄悄失效，
   表现只是「说了没反应」——所以覆盖范围必须被断言盯住。 */
console.log('\n4b. 拼音表');

check('数据集里有拼音表', !!menu.pinyin && typeof menu.pinyin === 'object');
check('拼音表按音节分组存放（不是逐字存，否则体积翻四倍）',
      Object.values(menu.pinyin || {}).every((v) => typeof v === 'string'));
{
  const flat = {};
  for (const syl of Object.keys(menu.pinyin || {})) {
    for (const ch of menu.pinyin[syl]) flat[ch] = syl;
  }
  const n = Object.keys(flat).length;
  check(`拼音表覆盖 ${n} 个汉字（够不够救「菜单外的错字」就看这个数）`, n >= 3000, `实际 ${n}`);
  check('每个音节都是纯 ASCII 拼音', Object.keys(menu.pinyin).every((s) => /^[a-z]+$/.test(s)));

  // 关键：错字本身必须能查到读音。第一版只收菜单里的字，
  // 「可乐」被听成「渴乐」时「渴」查不到，整层失效——这条断言守的就是那个坑。
  const beyond = ['渴', '书', '勺', '必', '封', '刺', '插', '咋'];
  const missing = beyond.filter((c) => !flat[c]);
  check('拼音表包含菜单之外、但会被听错的常用字', missing.length === 0, missing.join('、'));

  // 菜单里每个汉字都必须能查到，否则那道菜永远容错不了
  const notCovered = new Set();
  for (const it of menu.items) {
    for (const ch of it.name) {
      if (/[\u4e00-\u9fa5]/.test(ch) && !flat[ch]) notCovered.add(ch);
    }
  }
  check('菜单里的每个汉字都能查到读音', notCovered.size === 0, [...notCovered].join('、'));

  // 拼音层的三条通路必须都在，少一条就会有一整类「说了没反应」悄悄回来：
  //   exact  —— 整段同音（「渴乐」→「可乐」）
  //   fuzzy  —— 平翘舌/前后鼻音归并后同音（「苏条」→「薯条」）
  //   embed  —— 整段读音嵌在名字中后段（「咋鸡」→「蘸酱炸鸡」，只说核心词的场景）
  // 这里只查源码里有没有这三条分支，语义由 tests/test_voice.mjs 盯。
  const pinyinFn = html.match(/function\s+pinyinMatch[\s\S]*?\n\}/);
  const matchFn = html.match(/function\s+matchItems[\s\S]*?\n\}/);
  check('pinyinMatch 同时给出 exact / fuzzy / embed 三种相似度',
        !!pinyinFn && /exact\s*:/.test(pinyinFn[0]) && /fuzzy\s*:/.test(pinyinFn[0])
        && /embed\s*:/.test(pinyinFn[0]));
  check('matchItems 里三种相似度都接了评分通路（有通路才救得回，缺一条就有一类说法听不回来）',
        !!matchFn && /pm\.exact/.test(matchFn[0]) && /pm\.fuzzy/.test(matchFn[0])
        && /pm\.embed/.test(matchFn[0]));
  check('embed 只在读音嵌在**非开头**位置时才给分（嵌在开头说明用户只说出了修饰语）',
        !!html.match(/run\.hit\s*&&\s*run\.at\s*>\s*0/));
}

const priceM = html.match(/const\s+PRICE_SNAPSHOT\s*=\s*\(LIVE\s*&&\s*LIVE\.price\)\s*\|\|\s*([\s\S]*?);\s*\n/);
let priceSnap = null;
if (priceM) { try { priceSnap = JSON.parse(priceM[1]); } catch (e) { priceSnap = null; } }
check('真实报价快照已注入', priceSnap !== null);
if (priceSnap) {
  const ways = priceSnap.takeWayList || priceSnap.data?.takeWayList || [];
  check('报价快照带取餐方式列表（下单必需）', Array.isArray(ways) && ways.length > 0,
        JSON.stringify(Object.keys(priceSnap)));
}
check('原始报价快照文件与注入内容对应', JSON.stringify(priceSnap) === JSON.stringify(priceRaw.data || priceRaw));

/* ==================== 5. 语音与本地模式的挂载点 ==================== */
console.log('\n5. 语音与本地模式的挂载点');

check('VOICE 标记成对存在',
      html.includes('/* ---VOICE-BEGIN--- */') && html.includes('/* ---VOICE-END--- */'));
check('VOICE 段导出了 __MCVOICE__（供单测抽取）', /__MCVOICE__\s*=/.test(inlineJs));
check('应用层导出了 __MCVOICE_APP__（供浏览器检查驱动）', /__MCVOICE_APP__\s*=/.test(inlineJs));
check('读取 serve.py 注入的 window.__LOCAL__', /window\.__LOCAL__/.test(inlineJs));
check('使用 sessionStorage 键 mcvoice.live 做整页接管', html.includes("'mcvoice.live'")
      || html.includes('"mcvoice.live"'));
check('有 ?audit=1 自动自检入口（便于自动化）', /audit=1/.test(inlineJs));

/* 语音识别的配置直接决定「识别得准不准」，所以把它当结构断言盯住。
   这三项每一项的退化都会让识别变差，而页面上一点报错都不会有。 */
console.log('\n5b. 语音识别配置');
check('向浏览器要多个转写候选（maxAlternatives > 1）',
      /maxAlternatives\s*=\s*([2-9]|\d\d)/.test(inlineJs),
      '只取第一名会浪费掉最有价值的信息：备选里往往就有对的那句');
check('打开了中间结果（interimResults = true），说话过程中就能回显',
      /interimResults\s*=\s*true/.test(inlineJs));
check('识别结束后会自动拉起继续听（continuous + onend 重启）',
      /continuous\s*=\s*true/.test(inlineJs) && /onend/.test(inlineJs));
check('重启带退避，异常时不会变成每秒几十次的空转', /restarts/.test(inlineJs));
check('采纳非第一名候选时会说明「按哪一句理解」', /按「/.test(inlineJs));

/* ==================== 6. 无障碍骨架（静态可查的部分） ==================== */
console.log('\n6. 无障碍骨架');

check('声明了页面语言 zh-CN', /<html[^>]*\blang="zh-CN"/.test(html));
check('viewport 不禁止缩放（视障用户常需放大）',
      !/(maximum-scale|user-scalable\s*=\s*no)/i.test(html));
check('有且只有一个一级标题', (html.match(/<h1[\s>]/g) || []).length === 1);
check('有唯一的 main 地标', (html.match(/<main[\s>]/g) || []).length === 1
      && /<main[^>]*id="main"/.test(html));
check('有跳到主要内容的链接且指向 #main',
      /<a[^>]*class="[^"]*skip[^"]*"[^>]*href="#main"/.test(html));
check('有 polite 播报区（role=status + aria-live）',
      /role="status"[^>]*aria-live="polite"|aria-live="polite"[^>]*role="status"/.test(html));
check('有 assertive 播报区（role=alert + aria-live）',
      /role="alert"[^>]*aria-live="assertive"|aria-live="assertive"[^>]*role="alert"/.test(html));
check('每个表单控件都有与之关联的 label', (() => {
  const ids = [...html.matchAll(/<(?:input|select|textarea)\b[^>]*\bid="([^"]+)"/g)].map((m) => m[1]);
  const forAttrs = new Set([...html.matchAll(/<label\b[^>]*\bfor="([^"]+)"/g)].map((m) => m[1]));
  return ids.every((id) => forAttrs.has(id));
})());
check('所有 button 都显式声明 type（否则表单里会意外提交）',
      [...html.matchAll(/<button\b[^>]*>/g)].every((m) => /\btype\s*=/.test(m[0])));
check('没有缺少 alt 的图片', (html.match(/<img\b/g) || []).length === 0
      || [...html.matchAll(/<img\b[^>]*>/g)].every((m) => /\balt\s*=/.test(m[0])));
check('存在 sr-only 文本类（供屏幕阅读器补充说明）', /\.sr-only\b/.test(html));
check('声明了 prefers-contrast 高对比度适配', /prefers-contrast/.test(html));
/* 内置对比度审计必须在采样前关掉过渡和动画，量「稳定状态」。
   不这样做的话：主按钮从品牌黄过渡到深红的 0.12 秒里，
   文字已经是白的、背景还停在黄的，量出 1.6:1 的假警报——
   无头浏览器（--virtual-time-budget）里过渡时钟不走，假警报会永远挂着。 */
check('对比度审计在采样前关掉过渡与动画（量稳态，不量半路的颜色）',
      /node\.style\.transition\s*=\s*'none'/.test(html)
      && /node\.style\.animation\s*=\s*'none'/.test(html));
check('声明了 prefers-reduced-motion 动效减弱适配', /prefers-reduced-motion/.test(html));

/* ==================== 7. 关键交互节点齐备 ==================== */
console.log('\n7. 关键交互节点');

const REQUIRED_IDS = [
  'main', 'live', 'alert', 'micBtn', 'repeatBtn', 'backBtn', 'helpBtn',
  'typeInput', 'sendBtn', 'body', 'chips', 'stepNo', 'stepName', 'said',
  'auditBtn', 'auditOut', 'speakOn', 'allowOrder', 'fontScale', 'fontScaleVal', 'fontReset',
  // 视觉改版新增：进度条 + 听到原文回显（少了它们，识别不准时用户没有任何抓手）
  'progress', 'progressText', 'heard', 'heardLbl', 'heardVal', 'micNote'
];
const presentIds = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]));
const missing = REQUIRED_IDS.filter((id) => !presentIds.has(id));
check(`${REQUIRED_IDS.length} 个关键节点 id 齐备`, missing.length === 0,
      `缺失：${missing.join('、')}`);

check('进度条对屏幕阅读器隐藏（步骤信息已由 stepNo/stepName 播报，不重复念）',
      /class="progress-wrap"[^>]*aria-hidden="true"/.test(html));
check('进度条初始为空、由脚本按总步数填充', /<ol class="progress" id="progress"><\/ol>/.test(html));
check('听到原文回显初始隐藏（没听过话时不占地方）',
      /id="heard"[^>]*\shidden/.test(html));

check('设置区有「允许创建订单」开关（不可逆动作的显式闸门）',
      /id="allowOrder"[^>]*type="checkbox"|type="checkbox"[^>]*id="allowOrder"/.test(html));
check('开关默认未勾选（默认只算价，不建单）',
      !/id="allowOrder"[^>]*checked/.test(html));
check('字号调节范围上限到 160%', /id="fontScale"[^>]*max="160"/.test(html));

/* ==================== 汇总 ==================== */
console.log('');
if (failures.length === 0) {
  console.log(`全部通过：${pass} 项结构自检覆盖占位符 / 脚本可编译 / 数据自洽 / 无障碍骨架 / 交互节点`);
  process.exit(0);
} else {
  console.log(`${failures.length} 项未通过：`);
  for (const f of failures) console.log(`  - ${f}`);
  process.exit(1);
}
