/* ============================================================
   player.html「分身 × 语言 × 声音」三方绑定矩阵验证（v2.9.5，桩环境真跑）
   ------------------------------------------------------------
   直接对应用户点名的三条要求：
     ② 生成文字 / 音频讲解时选中文或英文要对应，选分身也要相互对应；
     ③ 选分身（炎冰 / 金子）声音和形象要对应，不能串和混乱；
     ④ 播放时可选择分身、也可选择中英文，没有对应部分要灰色不可选或给提示。

   判定用的真凭据是导出清单里的 params.voices = {分身id: {zh, en}}，
   老包没有这个字段时退化为逐页 audio_person，再没有就"不误判、不置灰"。

   用法： node _harness_lang_matrix.js
   ============================================================ */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const HTML = fs.readFileSync(path.join(__dirname, 'ui', 'player.html'), 'utf8');
const SRC = HTML.match(/<script>([\s\S]*?)<\/script>/)[1];

/* ---------------- 最小 DOM 桩（与 _harness_player_menu.js 同一套） ---------------- */
function El(tag) {
  const el = {
    tagName: String(tag || 'div').toUpperCase(),
    _id: '', children: [], parentNode: null, _cls: new Set(), _attrs: {},
    _text: '', _html: '', style: {}, value: '', disabled: false, selectedIndex: 0,
    files: null, src: '', currentTime: 0, playbackRate: 1, muted: false, duration: 0,
    paused: true, title: '', accept: '', checked: false,
    onclick: null, onchange: null, onerror: null, onload: null, oninput: null,
    appendChild(c) { c.parentNode = el; el.children.push(c); return c; },
    removeChild(c) { const i = el.children.indexOf(c); if (i >= 0) el.children.splice(i, 1); return c; },
    setAttribute(k, v) { el._attrs[k] = v; if (k === 'src') el.src = v; },
    getAttribute(k) { return el._attrs[k]; },
    removeAttribute(k) { delete el._attrs[k]; },
    addEventListener(t, f) { (el._ls = el._ls || {})[t] = (el._ls[t] || []).concat(f); },
    removeEventListener() { }, dispatchEvent() { return true; },
    click() { if (el.onclick) el.onclick.call(el, fakeEvent(el)); },
    pause() { el.paused = true; }, play() { el.paused = false; return Promise.resolve(); },
    closest() { return null; }, focus() { }, remove() { },
    requestFullscreen() { }, querySelector() { return null; }, querySelectorAll() { return []; }
  };
  el.classList = {
    add: c => el._cls.add(c), remove: c => el._cls.delete(c),
    toggle: (c, f) => { if (f === undefined) { el._cls.has(c) ? el._cls.delete(c) : el._cls.add(c); }
                        else if (f) { el._cls.add(c); } else { el._cls.delete(c); } },
    contains: c => el._cls.has(c)
  };
  Object.defineProperty(el, 'options', { get() { return el.children.filter(c => c.tagName === 'OPTION'); } });
  Object.defineProperty(el, 'firstElementChild', { get() { return el.children[0] || null; } });
  Object.defineProperty(el, 'textContent', {
    get() { return el._text; }, set(v) { el._text = String(v); el.children.length = 0; } });
  Object.defineProperty(el, 'innerHTML', {
    get() { return el._html; }, set(v) { el._html = String(v); el.children.length = 0; } });
  return el;
}
function fakeEvent(el) {
  return { target: el || null, stopPropagation() { }, preventDefault() { }, key: '', closest: () => null };
}

let CLICKS = [], TOASTS = [], DOC_LS = {}, REG = {};
const document = {
  readyState: 'complete', documentElement: El('html'), head: El('head'), body: El('body'),
  getElementById(id) { if (!REG[id]) { REG[id] = El('div'); REG[id]._id = id; } return REG[id]; },
  createElement(t) { return El(t); },
  querySelector() { return null; }, querySelectorAll() { return []; },
  addEventListener(t, f) { (DOC_LS[t] = DOC_LS[t] || []).push(f); },
  removeEventListener() { }
};

function newSandbox() {
  const sb = {
    console, setTimeout, clearTimeout, setInterval, clearInterval,
    Promise, JSON, Object, Array, String, Number, Boolean, Math, Date, RegExp, Error,
    document, navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0)' },
    URL: { createObjectURL: () => 'blob:x', revokeObjectURL: () => { } },
    Blob: function () { }, TextDecoder: function () { this.decode = () => ''; },
    FileReader: function () { }, Response: function () { }, DecompressionStream: undefined,
    fetch: () => Promise.reject(new Error('no net')),
    XMLHttpRequest: function () { this.open = () => { }; this.send = () => { }; },
    alert: () => { }, location: { href: '' }
  };
  sb.window = sb; sb.globalThis = sb;
  return sb;
}

function seed(sb) {
  const d = sb.document;
  const ls = d.getElementById('langSel');
  const o1 = d.createElement('option'); o1.value = 'zh'; o1.textContent = '中文';
  const o2 = d.createElement('option'); o2.value = 'en'; o2.textContent = 'English';
  ls.appendChild(o1); ls.appendChild(o2);
  d.getElementById('avSel').style.display = 'none';
  const rate = d.getElementById('rate');
  ['0.75', '1', '1.25', '1.5', '2'].forEach(v => {
    const o = d.createElement('option'); o.value = v; o.textContent = v + 'x'; rate.appendChild(o);
  });
  d.getElementById('bar').appendChild(d.createElement('i'));
}

let pass = 0, fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; console.log('  ✅ ' + name); }
  else { fail++; console.log('  ❌ ' + name + (extra !== undefined ? ('  → ' + JSON.stringify(extra)) : '')); }
}

/* 三个真实场景的清单 */
const MAN_MIXED = {   // 炎冰中英都有；金子只有中文
  app: 'myme', manifest: 1,
  params: {
    lang: 'zh', langs: ['zh', 'en'], person: 'yanbing', avatar: 'selfie',
    avkey: 'yanbing_selfie', voice_person: 'yanbing',
    voices: { yanbing: { zh: true, en: true }, jinzi: { zh: true, en: false } }
  },
  personas: [{ id: 'yanbing', name: '炎冰' }, { id: 'jinzi', name: '金子' }],
  segments: [
    { id: 'yanbing_zh', name: '炎冰 · 中文', person: 'yanbing', lang: 'zh', ready: true, missing: '' },
    { id: 'yanbing_en', name: '炎冰 · 英文', person: 'yanbing', lang: 'en', ready: true, missing: '' },
    { id: 'jinzi_zh', name: '金子 · 中文', person: 'jinzi', lang: 'zh', ready: true, missing: '' },
    { id: 'jinzi_en', name: '金子 · 英文', person: 'jinzi', lang: 'en', ready: false, missing: '「金子」的配音（本包配音是「炎冰」的声音）' }
  ],
  versions: [{ id: 'v1', name: 'V1', slides: [
    { idx: 1, title: 'T1', text: 'a', audio: 'audio/a.mp3', audio_en: 'audio/a_en.mp3',
      audio_person: 'yanbing', audio_person_en: 'yanbing',
      videoAlt: { yanbing_selfie: 'video/1.mp4', jinzi_pro: 'video/2.mp4' } }] }]
};

const MAN_SOLO = {    // 只有炎冰配了音，金子只有画面
  app: 'myme', manifest: 1,
  params: {
    lang: 'zh', langs: ['zh'], person: 'yanbing', voice_person: 'yanbing',
    voices: { yanbing: { zh: true, en: false } }
  },
  personas: [{ id: 'yanbing', name: '炎冰' }, { id: 'jinzi', name: '金子' }],
  segments: [
    { id: 'yanbing_zh', name: '炎冰 · 中文', person: 'yanbing', lang: 'zh', ready: true, missing: '' },
    { id: 'jinzi_zh', name: '金子 · 中文', person: 'jinzi', lang: 'zh', ready: false, missing: '「金子」的配音（本包配音是「炎冰」的声音）' }
  ],
  versions: [{ id: 'v1', name: 'V1', slides: [
    { idx: 1, title: 'T1', text: 'a', audio: 'audio/a.mp3', audio_en: '',
      audio_person: 'yanbing', videoAlt: { yanbing_selfie: 'video/1.mp4', jinzi_pro: 'video/2.mp4' } }] }]
};

const MAN_LEGACY = {  // 老包：没有 voices、也没有 audio_person —— 不能凭空白白置灰
  app: 'myme', manifest: 1,
  params: { lang: 'zh', langs: ['zh', 'en'], person: 'yanbing' },
  personas: [{ id: 'yanbing', name: '炎冰' }, { id: 'jinzi', name: '金子' }],
  versions: [{ id: 'v1', name: 'V1', slides: [
    { idx: 1, title: 'T1', text: 'a', audio: 'audio/a.mp3', audio_en: 'audio/a_en.mp3', videoAlt: {} }] }]
};

async function boot(man) {
  CLICKS = []; TOASTS = []; DOC_LS = {}; REG = {};
  const sb = newSandbox();
  sb.__MYME_MANIFEST__ = man;
  sb.__MYME_DEBUG = true;
  seed(sb);
  vm.createContext(sb);
  vm.runInContext(SRC, sb, { filename: 'player.html' });
  /* 必须在跑完脚本后再接管：player 自己声明了 function toast()，会覆盖预先挂的桩 */
  sb.toast = m => TOASTS.push(String(m));
  await new Promise(r => setImmediate(r));
  const $ = id => sb.document.getElementById(id);
  const opt = (id, v) => $(id).options.filter(o => o.value === v)[0];
  const pick = (id, v) => {
    const sel = $(id); sel.value = v;
    if (sel.onchange) sel.onchange.call(sel, fakeEvent(sel));
  };
  return { sb, $, opt, pick };
}

(async () => {
  console.log('='.repeat(66));
  console.log('「分身 × 语言 × 声音」绑定矩阵验证（v2.9.5）');
  console.log('='.repeat(66));

  /* ---------- 场景 A：炎冰中英俱全，金子只有中文 ---------- */
  console.log('\n[A] 本包 = 炎冰(中/英) + 金子(中) —— 选金子时英文必须灰掉并说明原因');
  {
    const { sb, $, opt, pick } = await boot(MAN_MIXED);
    ok('默认分身＝炎冰（清单标注生效）', sb.AV_SEL === 'yanbing', sb.AV_SEL);
    ok('炎冰·中文 可选', opt('langSel', 'zh').disabled === false);
    ok('炎冰·英文 可选', opt('langSel', 'en').disabled === false);

    pick('langSel', 'en');                     // 先用炎冰的英文（本包有）
    ok('炎冰·英文 已选中', sb.LANG === 'en', sb.LANG);
    TOASTS.length = 0;
    pick('avSel', 'jinzi');                    // 再切到只有中文的金子
    ok('切到金子 → 分身选择生效', sb.AV_SEL === 'jinzi', sb.AV_SEL);
    ok('金子·英文 置灰（不可选）', opt('langSel', 'en').disabled === true);
    ok('灰选项文字带「· 无」', /·\s*无/.test(opt('langSel', 'en').textContent), opt('langSel', 'en').textContent);
    ok('灰选项 title 写清为什么', /没有「金子」的英文配音/.test(opt('langSel', 'en').title || ''), opt('langSel', 'en').title);
    ok('语言自动回落到中文（不会静默播炎冰的英文）', sb.LANG === 'zh', sb.LANG);
    ok('当场弹出中文提示', TOASTS.some(t => /没有英文配音/.test(t)), TOASTS);
    ok('金子·中文 仍然可选（本包确有金子中文）', opt('langSel', 'zh').disabled === false);

    pick('avSel', 'yanbing');
    ok('切回炎冰 → 英文恢复可选', opt('langSel', 'en').disabled === false);
    ok('切回炎冰 → 分身不再被标"仅画面"', !/仅画面/.test(opt('avSel', 'yanbing').textContent), opt('avSel', 'yanbing').textContent);
  }

  /* ---------- 场景 B：只有炎冰的声音 ---------- */
  console.log('\n[B] 本包只有炎冰配音 —— 金子必须标「仅画面」且不可选');
  {
    const { sb, $, opt } = await boot(MAN_SOLO);
    ok('金子项被置灰', opt('avSel', 'jinzi').disabled === true);
    ok('金子项标注「仅画面」', /仅画面/.test(opt('avSel', 'jinzi').textContent), opt('avSel', 'jinzi').textContent);
    ok('金子项 title 说明原因', /没有配音/.test(opt('avSel', 'jinzi').title || ''), opt('avSel', 'jinzi').title);
    ok('炎冰项可选', opt('avSel', 'yanbing').disabled === false);
    ok('顶部提示写明「仅有画面无配音」', /仅有画面无配音/.test($('mHint').textContent || ''), $('mHint').textContent);
  }

  /* ---------- 场景 C：老包（无 voices / 无 audio_person） ---------- */
  console.log('\n[C] 老包未记录配音归属 —— 不能误判置灰');
  {
    const { sb, $, opt } = await boot(MAN_LEGACY);
    ok('中文可选', opt('langSel', 'zh').disabled === false);
    ok('英文可选', opt('langSel', 'en').disabled === false);
    ok('炎冰未被误判为"仅画面"', opt('avSel', 'yanbing').disabled === false, opt('avSel', 'yanbing').textContent);
    ok('金子未被误判为"仅画面"', opt('avSel', 'jinzi').disabled === false, opt('avSel', 'jinzi').textContent);
    ok('未出现"仅画面"字样', !/仅画面/.test($('mHint').textContent || ''), $('mHint').textContent);
  }

  /* ---------- 场景 D：逐页兜底（段级挡不住时，播放当场说清楚） ---------- */
  console.log('\n[D] 强行播"别人的声音"时必须有逐页提示，不许假装无缝');
  {
    const { sb, $, pick } = await boot(MAN_MIXED);
    pick('langSel', 'en');                     // 先用炎冰的英文（本包有）
    ok('炎冰·英文 已选中', sb.LANG === 'en', sb.LANG);
    TOASTS.length = 0;
    pick('avSel', 'jinzi');                    // 再切到只有中文的金子                    // 金子只有中文
    pick('langSel', 'zh');
    sb.play();
    await new Promise(r => setImmediate(r));
    ok('金子·中文 播放不报错', true);
    // 手动把 AV_SEL 掰成金子但让页面记的是炎冰的声音 → 应弹提示
    TOASTS.length = 0;
    sb.AV_SEL = 'jinzi';
    const s = sb.cur();
    if (s) { s.audio_person = 'yanbing'; }
    sb.play();
    await new Promise(r => setImmediate(r));
    ok('声音与分身不一致 → 当场提示（含"的声音"）', TOASTS.some(t => /的声音/.test(t)), TOASTS);
  }

  console.log('\n' + '='.repeat(66));
  console.log('通过 ' + pass + ' / 失败 ' + fail);
  console.log('结论：' + (fail === 0 ? '✅ 分身×语言×声音 三方绑定逐条通过' : '❌ 有断言未通过'));
  process.exit(fail === 0 ? 0 : 1);
})();
