#!/usr/bin/env node
/**
 * 语音理解核心的单元测试。
 *
 * 为什么必须有这个文件：
 *   「语音点餐」最典型的故障不是崩溃，而是**用户说了一句话却什么也没发生**——
 *   页面不报错、控制台一片安静，只有真正拿它点餐的人才发现。
 *   所以这里把内联脚本里 VOICE 标记之间的纯函数原样抽出来，
 *   拿真实菜单数据喂一批「人真的会这么说」的句子，逐条断言解析结果。
 *
 * 与 tests/check_page.mjs 的分工：
 *   - 这里测**语义**（这句话理解成什么意图、指向哪个商品）
 *   - 那里测**结构**（页面有没有占位符残留、数据是否完整注入）
 *
 * 用法： node tests/test_voice.mjs
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const html = fs.readFileSync(path.join(ROOT, 'docs', 'index.html'), 'utf8');
const menu = JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'menu.json'), 'utf8'));

/* ---- 从页面里原样抽出语音理解核心 ---- */
const m = html.match(/\/\* ---VOICE-BEGIN--- \*\/([\s\S]*?)\/\* ---VOICE-END--- \*\//);
if (!m) {
  console.error('FAIL  页面里找不到 VOICE 标记，无法抽取语音理解核心');
  process.exit(2);
}
new Function(m[1])();
const V = globalThis.__MCVOICE__;
if (!V || typeof V.parseUtterance !== 'function') {
  console.error('FAIL  VOICE 段没有导出 __MCVOICE__');
  process.exit(2);
}

/* 数据里的拼音表是「音节 → 汉字串」的紧凑存法，页面启动时展开成「字 → 音节」。
   这里用页面自己导出的同一个展开函数，保证测的和跑的是同一套。 */
const PY = V.expandPinyin(menu.pinyin);

const ctx = {
  items: menu.items,
  aliases: menu.aliases,
  homophones: menu.homophones,
  // 拼音层默认打开：这才是线上跑的那条路径。
  // 如果某条断言在「开拼音」时反而变差，那说明拼音层制造了假歧义，必须修。
  pinyin: PY,
  storeCount: menu.stores.length,
  inStoreStep: false
};
const inStore = (text) => V.parseUtterance(text, { ...ctx, inStoreStep: true });
const say = (text) => V.parseUtterance(text, ctx);
const nameOf = (code) => (menu.items.find((i) => i.code === code) || {}).name || code;

let pass = 0;
const failures = [];

function check(label, ok, detail) {
  if (ok) { pass++; console.log(`  PASS  ${label}`); }
  else { failures.push(label); console.log(`  FAIL  ${label}  ${detail || ''}`); }
}

/** 断言：这句话解析出的意图是 X，且（如果给了编码）第一名指向该编码 */
function expect(label, text, intent, code) {
  const r = say(text);
  const hit = r.intent === intent && (code == null || r.code === code || (r.ties || []).includes(code));
  check(`${label}：「${text}」→ ${r.intent}${r.code ? ' / ' + nameOf(r.code) : ''}`,
        hit,
        `期望 intent=${intent}${code ? ' code=' + nameOf(code) : ''}，实际 ${JSON.stringify(r)}`);
}

/** 断言数量解析正确。
    单独立一个函数是因为「数量静默失效」太隐蔽了：意图和商品都对，
    只有份数是错的，页面上不会报任何错，用户只会觉得「怎么加了两份」。 */
function expectQty(label, text, qty) {
  const r = say(text);
  check(`${label}：「${text}」→ ${qty} 份`, r.qty === qty,
        `期望 qty=${qty}，实际 ${JSON.stringify({ intent: r.intent, qty: r.qty, query: r.query })}`);
}

console.log(`从页面抽出语音理解核心（${m[1].length} 字符）\n读取真实菜单：${menu.items.length} 项餐品 / ${Object.keys(menu.aliases).length} 条口语别名\n`);

/* ==================== 1. 加购：用户真会这么说 ==================== */
console.log('1. 加购');
expect('最标准的一句', '来一份巨无霸', 'add', '1100');
expect('不带量词', '巨无霸', 'add', '1100');
expect('带数量', '来两份薯条', 'add', '4810');
expect('中文数量', '要两个麦辣鸡翅', 'add', '1700');
expect('阿拉伯数量', '3个麦乐鸡', 'add', '1401');
expect('口语量词「杯」', '一杯可乐', 'add', '9900008751');
expect('口语量词「块」', '来一块脆汁鸡', 'add', '9900005432');
expect('礼貌说法', '麻烦帮我拿一个圆筒', 'add', '515837');
expect('口语别名', '来个双吉', 'add', '1120');
expect('简写别名', '要一份板烧', 'add', '1406');
expect('那么大鸡排', '来一份那么大鸡排', 'add', '521156');

console.log('\n1b. 数量解析（这里踩过静默失效的坑，单独成组盯住）');
expectQty('中文【一】', '来一份巨无霸', 1);
expectQty('中文【两】', '来两份薯条', 2);
expectQty('中文【三】', '要三个麦辣鸡翅', 3);
expectQty('中文【十】', '来十份麦乐鸡', 10);
expectQty('中文【十二】', '来十二份麦乐鸡', 12);
expectQty('阿拉伯数字', '3个麦乐鸡', 3);
expectQty('无数量词时默认一份', '巨无霸', 1);
expectQty('量词【杯】', '一杯可乐', 1);
expectQty('量词【块】', '来四块脆汁鸡', 4);
expectQty('数量词必须从商品名里剥干净', '来两份薯条', 2);
{
  const r = say('来两份薯条');
  check('剥掉数量词后商品名只剩「薯条」', r.query === '薯条', `实际 query=${r.query}`);
}

/* ==================== 2. 一句话点多样东西（应只认第一样，不能崩） ==================== */
console.log('\n2. 一句话里有多样东西');
{
  const r = say('来一份巨无霸和一杯可乐');
  check('「来一份巨无霸和一杯可乐」至少能认出巨无霸',
        r.intent === 'add' && ((r.ties || []).includes('1100') || r.code === '1100'),
        JSON.stringify(r));
  check('不把「和」当成商品名的一部分而彻底失效', r.intent !== 'unknown', JSON.stringify(r));
}

/* ==================== 3. 数量词不能被误当成门店序号（回归测试） ==================== */
console.log('\n3. 门店序号解析（这里是修过的坑）');
{
  const bad = inStore('来一份巨无霸');
  check('门店步骤说「来一份巨无霸」不会被当成选第 1 家店',
        bad.intent !== 'pick-store', JSON.stringify(bad));
}
check('「第一家」→ 第 1 家', inStore('第一家').intent === 'pick-store' && inStore('第一家').index === 1);
check('「第二个」→ 第 2 家', inStore('第二个').index === 2);
check('「三家」→ 第 3 家', inStore('三家').index === 3);
check('「三」→ 第 3 家', inStore('三').index === 3);
check('「第六家」超出门店总数时不会被认成门店', inStore('第六家').intent !== 'pick-store');

/* ==================== 3b. 门店名匹配 ==================== */
console.log('\n3b. 门店名匹配（用最长公共子串，不能用字覆盖率）');
{
  const names = menu.stores.map((s) => s.storeName);
  const target = '麦当劳成都天府三街餐厅';
  const scores = names.map((n) => [n, V.storeScore('麦当劳天府三街餐厅', n)]);
  const best = scores.slice().sort((a, b) => b[1] - a[1])[0];
  check(`用户说「麦当劳天府三街餐厅」能唯一指向「${target}」`,
        best[0] === target, JSON.stringify(scores));
  const sorted = scores.slice().sort((a, b) => b[1] - a[1]);
  check('与第二名的分差足够大，不会被判成歧义（≥5 分）',
        sorted[0][1] - sorted[1][1] >= 5, `${sorted[0][1]} vs ${sorted[1][1]}`);

  check('lcsLength 能区分「连续 6 字」和「分散的字」',
        V.lcsLength('天府三街餐厅', '麦当劳成都天府三街餐厅') === 6
        && V.lcsLength('天府三街餐厅', '麦当劳成都高新大源东天府三街新希望国际B座餐厅') === 4,
        `连续=${V.lcsLength('天府三街餐厅', '麦当劳成都天府三街餐厅')} `
        + `分散=${V.lcsLength('天府三街餐厅', '麦当劳成都高新大源东天府三街新希望国际B座餐厅')}`);

  check('完全无关的说法的分数接近 0',
        V.storeScore('今天天气不错', '麦当劳成都天府三街餐厅') < 20,
        String(V.storeScore('今天天气不错', '麦当劳成都天府三街餐厅')));

  check('演示快照覆盖的门店确实在门店列表里（否则演示模式无从点餐）',
        names.includes(target), names.join(' / '));
}

/* ==================== 4. 流程指令 ==================== */
console.log('\n4. 流程指令');
expect('算价', '算价', 'price');
expect('问价', '一共多少钱', 'price');
expect('结账', '结账', 'price');
expect('堂食', '堂食', 'takeway_eatin');
expect('外带', '外带', 'takeway_takeout');
expect('带走', '带走', 'takeway_takeout');
expect('下单', '确认下单', 'order');
expect('退回', '退回上一步', 'back');
expect('取消', '算了', 'cancel');
expect('重来', '重来', 'cancel');
expect('再说一遍', '没听清，再说一遍', 'repeat');
expect('帮助', '我能说什么', 'help');
expect('菜单', '菜单', 'menu');
expect('继续', '继续', 'continue');
expect('常点', '我的常点', 'frequent');
expect('看购物车', '我点了什么', 'mycart');
expect('换门店', '附近有什么店', 'stores');

/* ==================== 5. 删除 ==================== */
console.log('\n5. 删除与修改');
expect('删除', '不要薯条了', 'remove', '4810');
expect('删除别名', '把可乐去掉', 'remove', '9900008751');
expect('「不要了」是取消整单而不是删某个商品', '不要了', 'cancel');

/* ==================== 6. 同音误听纠正 ==================== */
console.log('\n6. 同音误听纠正（语音识别常犯的错）');
{
  const pairs = [
    ['巨无八', '巨无霸'], ['聚无霸', '巨无霸'], ['数条', '薯条'], ['暑条', '薯条'],
    ['雪比', '雪碧'], ['圆桶', '圆筒'], ['麦乐机', '麦乐鸡'], ['安格丝', '安格斯']
  ];
  for (const [wrong, right] of pairs) {
    const fixed = V.applyHomophones(V.normalizeUtterance('来一份' + wrong), menu.homophones);
    check(`「${wrong}」被纠正成「${right}」`, fixed.includes(right), `实际得到 ${fixed}`);
  }
}

/* ==================== 7. 歧义识别：宁可不猜，也不能猜错 ==================== */
console.log('\n7. 歧义识别：说清楚了就别追问，说不清楚才问');
{
  // 回归测试：早期版本的别名表收了泛化的「三件套」「四件套」，
  // 导致说「巨无霸三件套」时把「大堡口福三件套」也拉进候选，反过来问用户是哪一个。
  const specific = V.matchItems('巨无霸三件套', menu.items, menu.aliases);
  check('「巨无霸三件套」不会再被「大堡口福三件套」拖成歧义',
        (specific.ties || []).length === 1 && specific.best === '9900005466',
        JSON.stringify(specific));

  // 「巨无霸」是菜单里的精确商品名 → 直接采纳，只顺口提一句还有大份的
  const exact = say('来一份巨无霸');
  check('「巨无霸」精确命中商品名，直接采纳（best=巨无霸，不进歧义追问）',
        exact.intent === 'add' && exact.code === '1100' && (exact.ties || []).length === 1,
        JSON.stringify(exact));
  check('同时带回「还有巨无霸三件套 / 四件套」的提示',
        (exact.alsoConsider || []).length >= 1,
        JSON.stringify(exact.alsoConsider));

  // 真正说不清楚的时候，必须能发现歧义并交给页面追问
  const vague = V.matchItems('鸡腿堡', menu.items, menu.aliases);
  check('「鸡腿堡」这种确实指向多个不同商品的说法，仍然会被标成歧义',
        (vague.ties || []).length >= 2, JSON.stringify(vague));

  // 泛化规格词必须已经从别名表里清除
  const generic = ['三件套', '四件套', '单人餐', '双人餐'].filter(a => a in menu.aliases);
  check('别名表里没有「三件套 / 四件套 / 单人餐 / 双人餐」这类泛化规格词',
        generic.length === 0, generic.join('、'));
}

/* ==================== 8. 乱说要有兜底，不能崩 ==================== */
console.log('\n8. 无效输入的兜底');
for (const junk of ['', '   ', '啊啊啊啊', '今天天气不错', '%%%', '12345678901234']) {
  let r, threw = null;
  try { r = say(junk); } catch (e) { threw = e.message; }
  check(`「${junk || '(空)'}」被安全兜底`, !threw && r && typeof r.intent === 'string',
        threw || JSON.stringify(r));
}

/* ==================== 9. 大量真实菜单名的自我一致性 ==================== */
console.log('\n9. 全菜单自我一致性：每项的名字都该能匹配到它自己');
{
  let bad = [];
  for (const item of menu.items) {
    const r = V.matchItems(item.name, menu.items, menu.aliases, PY);
    if (r.best !== item.code) bad.push(`${item.name} → ${nameOf(r.best)}`);
  }
  check(`${menu.items.length} 项菜单的全部名称都能精确命中自己`, bad.length === 0,
        bad.slice(0, 6).join('；'));
}

/* ==================== 10. 别名表指向的编码都真实存在 ==================== */
console.log('\n10. 别名表完整性');
{
  const codes = new Set(menu.items.map((i) => i.code));
  const dangling = [];
  for (const [alias, targets] of Object.entries(menu.aliases)) {
    for (const c of targets) if (!codes.has(c)) dangling.push(`${alias}→${c}`);
  }
  check(`${Object.keys(menu.aliases).length} 条别名指向的编码全部存在于菜单中`,
        dangling.length === 0, dangling.join('、'));
}

/* ==================== 11. 同音容错（拼音层）====================
   为什么单独立一组：
     「语音识别不准」最常见的形态就是**听成同音字**。手写的误听表只能覆盖
     想得到的几十种，拼音层要覆盖剩下的所有情况——而它一旦写错，
     表现同样是「说了没反应」，静默无声。所以这里专盯它。

   注意这批用例的错字**刻意都不在 homophones 表里**，
   否则测的是那张表而不是拼音层。 */
console.log('\n11. 同音容错（拼音层能救回手写表覆盖不到的同音错字）');
{
  // 断言方式刻意用「名字里包含」而不是硬编码编码：
  // 这个测试要表达的是「听错一个字之后还能找回那道菜」，不是「编码是多少」。
  const resolvesTo = (r, expected) =>
    r.intent === 'add' && (r.ties.length ? r.ties : [r.best]).map(nameOf).join(' / ').includes(expected);

  // 这些错法 homophones 表里都没有，只能靠拼音层救
  const homophoneOnly = [
    ['渴乐', '可乐'],
    ['书条', '薯条'],
    ['板勺', '板烧鸡腿堡'],
    ['雪必', '雪碧'],
    ['麦旋封', '麦旋风'],
    ['脆汁机', '脆汁鸡'],
    ['巨无八', '巨无霸']      // 这一条表里有，留作对照
  ];
  for (const [said, what] of homophoneOnly) {
    const r = say(said);
    check(`「${said}」能找回「${what}」`, resolvesTo(r, what),
          `实际 ${JSON.stringify({ intent: r.intent, best: nameOf(r.best), ties: r.ties })}`);
  }

  // 平翘舌不分：识别器本身的老毛病（南方口音尤其常见），模糊音层要能吃下
  const fuzzy = [
    ['苏条', '薯条', 'sh→s'],
    ['麦辣鸡刺', '麦辣鸡翅', 'ch→c'],
    ['咋鸡', '蘸酱炸鸡', 'zh→z'],
    ['锡兰红插', '锡兰红茶', 'ch→c'],
    ['麦旋分', '麦旋风', 'eng→en']
  ];
  for (const [said, what, why] of fuzzy) {
    const r = say(said);
    check(`模糊音「${said}」能找回「${what}」（${why}）`, resolvesTo(r, what),
          `实际 ${JSON.stringify({ intent: r.intent, best: nameOf(r.best), ties: r.ties })}`);
  }

  check('模糊音归并本身正确：sh→s、ch→c、zh→z、eng→en',
        V.fuzzyPinyinSeq(['shu', 'tiao']).join(',') === 'su,tiao'
        && V.fuzzyPinyinSeq(['chi']).join(',') === 'ci'
        && V.fuzzyPinyinSeq(['zha']).join(',') === 'za'
        && V.fuzzyPinyinSeq(['feng']).join(',') === 'fen',
        JSON.stringify(V.fuzzyPinyinSeq(['shu', 'chi', 'zha', 'feng'])));

  check('精确同音识别为 exact=1，模糊音识别为 exact<1 但 fuzzy=1',
        V.pinyinMatch('书条', '薯条', PY).exact === 1
        && V.pinyinMatch('苏条', '薯条', PY).exact < 1
        && V.pinyinMatch('苏条', '薯条', PY).fuzzy === 1,
        JSON.stringify(V.pinyinMatch('苏条', '薯条', PY)));

  // 拼音层是补救层，绝不能被它抢走字面已经认出来的东西
  check('字面完全命中时，拼音层不会把第一名换掉',
        say('巨无霸').code === '1100' && say('可乐').code === '9900008751');

  // 单字查询太宽泛，拼音层必须对它闭嘴
  check('单字查询不启用拼音层（否则「牛」会匹配到牛奶、牛堡一大串）',
        V.pinyinMatch('牛', '牛奶', PY).exact === 0,
        JSON.stringify(V.pinyinMatch('牛', '牛奶', PY)));

  /* ---- 长名字降权：这里锁的是一个真出现过的 bug ----
     拼音层第一版用「最长公共子序列 ÷ 用户说的音节数」算相似度，
     于是只要名字**开头**读音对得上就满分——说「薯条」时，
     「薯条」和「薯条脆卜卜毛绒周边」同分并列，页面凭空多问一句
     「你要的是哪一个」，而用户刚刚说得很清楚。
     现在两条一起堵：对称 Dice 系数（名字越长越吃亏）+ embed 只在
     「嵌在非开头位置」时才给分（开头命中说明用户只说出了修饰语）。 */
  const fries = say('薯条');
  check('「薯条」不会被「薯条脆卜卜毛绒周边」拖成假歧义（长名字要降权）',
        fries.intent === 'add' && (fries.ties || []).length === 1
        && nameOf(fries.ties[0]) === '薯条',
        JSON.stringify((fries.ties || []).map(nameOf)));

  check('读音只嵌在候选名开头时不给 embed 分（那说明用户只说了修饰语，核心词还没出口）',
        V.pinyinMatch('薯条', '薯条脆卜卜毛绒周边', PY).embed === 0,
        JSON.stringify(V.pinyinMatch('薯条', '薯条脆卜卜毛绒周边', PY)));

  check('读音嵌在候选名中后段时给 embed 分（核心词命中，是很硬的证据）',
        V.pinyinMatch('咋鸡', '蘸酱炸鸡', PY).embed > 0
        && V.pinyinMatch('红茶', '锡兰红茶', PY).embed > 0,
        JSON.stringify(V.pinyinMatch('咋鸡', '蘸酱炸鸡', PY)));

  check('embed 只在整段读音连续吻合时才有值（零散撞音、只说修饰语都不算）',
        V.contiguousSeqRun(['ji', 'za'], ['za', 'ji']).hit === false
        && V.contiguousSeqRun(['za', 'ji'], ['zan', 'jian']).hit === false
        && V.contiguousSeqRun(['za', 'ji'], ['zan', 'jian', 'za', 'ji']).hit === true,
        JSON.stringify(V.contiguousSeqRun(['za', 'ji'], ['zan', 'jian'])));

  // 负例：完全不同音的说法不该被拼音层硬拉过来
  const wrong = ['汽车', '太阳', '明天', '电脑'];
  const dragged = wrong.filter((w) => {
    const r = say(w);
    return r.intent === 'add';
  });
  check('完全无关的说法不会被拼音层误匹配', dragged.length === 0, dragged.join('、'));

  check('无关说法同样拿不到 embed 分（否则等于给菜单里每个名字白送分）',
        wrong.every((w) => V.pinyinMatch(w, '蘸酱炸鸡', PY).embed === 0),
        JSON.stringify(wrong.map((w) => V.pinyinMatch(w, '蘸酱炸鸡', PY).embed)));

  check('拼音表覆盖了全部菜单汉字（缺字会让某道菜永远容错不了）', (() => {
    const missing = new Set();
    for (const item of menu.items) {
      for (const ch of item.name) {
        if (/[\u4e00-\u9fa5]/.test(ch) && !PY[ch]) missing.add(ch);
      }
    }
    return missing.size === 0;
  })());
}

/* ==================== 12. 多候选择优 ====================
   一次识别给好几个转写，选哪一个直接决定用户说的话会不会被听错。
   这段逻辑原本只活在浏览器里，现在抽成纯函数，就该被单测盯死。 */
console.log('\n12. 多候选择优：哪一句才是被真正理解的');
{
  const addHi  = { text: '巨无霸', parsed: { intent: 'add', matchScore: 100 }, conf: 0.3 };
  const addLo  = { text: '巨无把', parsed: { intent: 'add', matchScore: 55 },  conf: 0.9 };
  const cancel = { text: '算了',   parsed: { intent: 'cancel' },               conf: 0.8 };
  const junk   = { text: '阿巴阿巴', parsed: { intent: 'unknown' },            conf: 0.99 };

  const withScore = (c) => ({ ...c, score: V.parseScore(c.parsed) });

  check('认出了商品 ＞ 只是控制指令', V.parseScore(addHi.parsed) > V.parseScore(cancel.parsed));
  check('控制指令 ＞ 完全没听懂', V.parseScore(cancel.parsed) > V.parseScore(junk.parsed));
  check('没听懂就是 0 分（不能因为别的候选也烂就凑合采纳）', V.parseScore(junk.parsed) === 0);
  check('匹配质量越高分越高', V.parseScore(addHi.parsed) > V.parseScore(addLo.parsed));

  const r1 = V.rankCandidates([junk, addHi, cancel].map(withScore));
  check('三个候选里选出真正认出了商品的那句', r1[0].text === '巨无霸', r1.map((x) => x.text).join(' > '));

  const r2 = V.rankCandidates([addLo, cancel].map(withScore));
  check('弱匹配的商品也优先于控制指令（语境是点餐，认出了菜就该加）',
        r2[0].text === '巨无把', r2.map((x) => x.text).join(' > '));

  const r3 = V.rankCandidates([{ text: 'a', parsed: { intent: 'unknown' }, score: 0, conf: 0.2 },
                               { text: 'b', parsed: { intent: 'unknown' }, score: 0, conf: 0.9 }]);
  check('全都听不懂时退回到识别器自己的置信度', r3[0].text === 'b');

  check('候选择优不会改动原数组（避免污染调用方）', (() => {
    const input = [junk, addHi].map(withScore);
    const copy = input.slice();
    V.rankCandidates(input);
    return input.every((v, i) => v === copy[i]);
  })());
}

/* ==================== 结果 ==================== */
console.log('');
if (failures.length) {
  console.log(`${failures.length} 项未通过，${pass} 项通过`);
  failures.forEach((f) => console.log('  · ' + f));
  process.exit(1);
}
console.log(`全部通过：${pass} 项断言覆盖加购 / 流程指令 / 删除 / 误听纠正 / 歧义识别 / 同音容错 / 多候选择优 / 边界兜底`);
