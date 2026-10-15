/* ============================================================
   player.html 播放参数逻辑桩测试（v2.8.0）
   ------------------------------------------------------------
   针对「导入自动识别语言/分身 + 互斥禁用」这一条，用最小 DOM 桩把
   player.html 的内联脚本真跑一遍，断言下拉项的可选性：
     · 只有中文 → 英文置灰；只有英文 → 中文置灰
     · 本包只有金子 → 分身里不出现炎冰
     · 本包是第三人 → 炎冰、金子都不出现
     · 老包（无 params / 无逐分身视频）→ 全列出，不崩
   用法： node _harness_player.js
   ============================================================ */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const HTML = fs.readFileSync(path.join(__dirname, 'ui', 'player.html'), 'utf8');
const m = HTML.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error('FATAL: 找不到内联脚本'); process.exit(2); }
const SRC = m[1];

/* ---------------- 最小 DOM 桩 ---------------- */
function mkClassList(el) {
  return {
    add: c => { el._cls.add(c); },
    remove: c => { el._cls.delete(c); },
    toggle: (c, f) => {
      if (f === undefined) { el._cls.has(c) ? el._cls.delete(c) : el._cls.add(c); }
      else if (f) { el._cls.add(c); } else { el._cls.delete(c); }
    },
    contains: c => el._cls.has(c)
  };
}

function El(tag) {
  const el = {
    tagName: String(tag || 'div').toUpperCase(),
    children: [], parentNode: null, _cls: new Set(), _attrs: {},
    _text: '', _html: '', style: {}, value: '', disabled: false,
    selectedIndex: 0, files: null, src: '', currentTime: 0, playbackRate: 1,
    muted: false, duration: 0, paused: true, title: '', accept: '',
    onclick: null, onchange: null, onerror: null, onload: null,
    appendChild(c) { c.parentNode = el; el.children.push(c); return c; },
    removeChild(c) { const i = el.children.indexOf(c); if (i >= 0) el.children.splice(i, 1); return c; },
    setAttribute(k, v) { el._attrs[k] = v; if (k === 'src') el.src = v; },
    getAttribute(k) { return el._attrs[k]; },
    removeAttribute(k) { delete el._attrs[k]; },
    addEventListener() { }, removeEventListener() { },
    dispatchEvent() { }, click() { if (el.onclick) el.onclick.call(el, {}); },
    pause() { el.paused = true; }, play() { el.paused = false; return Promise.resolve(); },
    closest() { return null; }, focus() { }, remove() { },
    requestFullscreen() { }, querySelector() { return null; }, querySelectorAll() { return []; }
  };
  el.classList = mkClassList(el);
  Object.defineProperty(el, 'options', {
    get() { return el.children.filter(c => c.tagName === 'OPTION'); }
  });
  Object.defineProperty(el, 'firstElementChild', {
    get() { return el.children[0] || null; }
  });
  Object.defineProperty(el, 'textContent', {
    get() { return el._text; },
    set(v) { el._text = String(v); el.children.length = 0; }
  });
  Object.defineProperty(el, 'innerHTML', {
    get() { return el._html; },
    set(v) { el._html = String(v); el.children.length = 0; }
  });
  return el;
}

const REG = {};
const document = {
  readyState: 'complete',
  documentElement: El('html'),
  head: El('head'),
  body: El('body'),
  getElementById(id) {
    if (!REG[id]) { REG[id] = El('div'); REG[id]._id = id; }
    return REG[id];
  },
  createElement(t) { return El(t); },
  querySelector() { return null; },
  querySelectorAll() { return []; },
  addEventListener() { }, removeEventListener() { }
};

const sandbox = {
  console, setTimeout, clearTimeout, setInterval, clearInterval,
  Promise, JSON, Object, Array, String, Number, Boolean, Math, Date, RegExp, Error,
  document, navigator: { userAgent: 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36' },
  URL: { createObjectURL: () => 'blob:x', revokeObjectURL: () => { } },
  Blob: function () { }, TextDecoder: function () { this.decode = () => ''; },
  FileReader: function () { }, Response: function () { }, DecompressionStream: undefined,
  fetch: () => Promise.reject(new Error('no net')),
  XMLHttpRequest: function () { this.open = () => { }; this.send = () => { }; },
  alert: () => { }, location: { href: '' }
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

/* ---------------- 工具 ---------------- */
function personasAll() {
  return [{ id: 'default', name: '默认分身' },
    { id: 'yanbing_selfie', name: '炎冰 · 日常', person: 'yanbing', avatar: 'selfie' },
    { id: 'yanbing_formal', name: '炎冰 · 正式', person: 'yanbing', avatar: 'formal' },
    { id: 'jinzi_pro', name: '金子 · 职业', person: 'jinzi', avatar: 'pro' },
    { id: 'jinzi_meet', name: '金子 · 会议', person: 'jinzi', avatar: 'meet' },
    { id: 'diyi_work', name: '第一 · 工作', person: 'diyi', avatar: 'work' }];
}
function page(o) {
  return Object.assign({ idx: 1, title: 'T', text: 'x', notes: '', img: '',
    audio: '', audio_en: '', video: '', videoAlt: {} }, o || {});
}
function seedDom() {
  // 按 player.html 的真实首屏结构预置两个下拉（langSel 自带 zh/en 两项；avSel 空）
  const ls = document.getElementById('langSel');
  const o1 = document.createElement('option'); o1.value = 'zh'; o1.textContent = '中文';
  const o2 = document.createElement('option'); o2.value = 'en'; o2.textContent = 'English';
  ls.appendChild(o1); ls.appendChild(o2);
  const as = document.getElementById('avSel'); as.style.display = 'none';
}

async function run(manifest) {
  for (const k in REG) delete REG[k];
  seedDom();
  sandbox.__MYME_MANIFEST__ = manifest;
  sandbox.MymeNative = undefined;
  const ctx = vm.createContext(sandbox);
  vm.runInContext(SRC, ctx, { filename: 'player.html' });
  await new Promise(res => setImmediate(res));   // 冲掉微任务：load().then(renderVers) 才会跑
  const st = c => { const e = sandbox.document.getElementById(c); return e ? { disabled: !!e.disabled, text: e.textContent } : null; };
  const errEl = sandbox.document.getElementById('phMsg');
  if (errEl && errEl.innerHTML && /失败/.test(errEl.innerHTML)) console.log('  [启动报错] ' + errEl.innerHTML.replace(/<[^>]*>/g, '').slice(0, 200));
  const av = () => {
    const as = sandbox.document.getElementById('avSel');
    return { hidden: as.style.display === 'none', ids: as.options.map(o => o.value), selected: as.value };
  };
  const ls = () => {
    const s = sandbox.document.getElementById('langSel');
    return { zh: st('langSel') && s.options.filter(o => o.value === 'zh').map(o => ({ disabled: !!o.disabled }))[0],
             en: s.options.filter(o => o.value === 'en').map(o => ({ disabled: !!o.disabled }))[0],
             value: s.value };
  };
  return {
    langs: ls(), avatars: av(),
    LANG: sandbox.LANG, AV: sandbox.AV_SEL,
    pTag: (sandbox.document.getElementById('pTag') || {}).textContent
  };
}

let pass = 0, fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; console.log('  ✅ ' + name); }
  else { fail++; console.log('  ❌ ' + name + (extra ? ('  → ' + JSON.stringify(extra)) : '')); }
}

async function main() {
/* ---------------- 场景 1：只有中文 + 标注分身金子 ---------------- */
console.log('\n[场景1] 只有中文配音 · 清单标注分身=金子(jinzi_pro)');
{
  const r = await run({ app: 'myme', manifest: 1,
    params: { lang: 'zh', langs: ['zh'], person: 'jinzi', avatar: 'pro', avkey: 'jinzi_pro', avkeys: [] },
    personas: personasAll(),
    versions: [{ id: 'v1', name: 'V1', slides: [page({ audio: 'audio/a.mp3' }), page({ idx: 2, audio: 'audio/b.mp3' })] }] });
  ok('中文可选', r.langs.zh && !r.langs.zh.disabled, r.langs);
  ok('英文被置灰', r.langs.en && r.langs.en.disabled, r.langs);
  ok('LANG 落在中文', r.LANG === 'zh', r.LANG);
  ok('分身不含炎冰（互斥）', r.avatars.ids.indexOf('yanbing_selfie') < 0 && r.avatars.ids.indexOf('yanbing_formal') < 0, r.avatars);
  ok('分身含金子', r.avatars.ids.indexOf('jinzi_pro') >= 0, r.avatars);
  ok('自动选中金子', r.AV === 'jinzi_pro', r.AV);
  ok('顶栏摘要含"分身"', /分身/.test(r.pTag || ''), r.pTag);
}

/* ---------------- 场景 2：中英都有 + 只有炎冰有画面 ---------------- */
console.log('\n[场景2] 中英配音都有 · 逐页视频只有炎冰(selfie)');
{
  const r = await run({ app: 'myme', manifest: 1, personas: personasAll(),
    versions: [{ id: 'v1', name: 'V1', slides: [
      page({ audio: 'audio/a.mp3', audio_en: 'audio/a_en.mp3', videoAlt: { yanbing_selfie: 'video/x.mp4' } }),
      page({ idx: 2, audio: 'audio/b.mp3', audio_en: 'audio/b_en.mp3', videoAlt: { yanbing_selfie: 'video/y.mp4' } })] }] });
  ok('中文可选', r.langs.zh && !r.langs.zh.disabled, r.langs);
  ok('英文可选', r.langs.en && !r.langs.en.disabled, r.langs);
  ok('分身不含金子', r.avatars.ids.indexOf('jinzi_pro') < 0 && r.avatars.ids.indexOf('jinzi_meet') < 0, r.avatars);
  ok('分身含炎冰(selfie)', r.avatars.ids.indexOf('yanbing_selfie') >= 0, r.avatars);
  ok('自动选中炎冰', r.AV === 'yanbing_selfie', r.AV);
}

/* ---------------- 场景 3：只有英文 + 第三人 ---------------- */
console.log('\n[场景3] 只有英文配音 · 第三分身(diyi_work)');
{
  const r = await run({ app: 'myme', manifest: 1,
    params: { lang: 'en', langs: ['en'], person: 'diyi', avatar: 'work', avkey: 'diyi_work' },
    personas: personasAll(),
    versions: [{ id: 'v1', name: 'V1', slides: [
      page({ audio_en: 'audio/a_en.mp3', videoAlt: { diyi_work: 'video/x.mp4' } })] }] });
  ok('中文被置灰', r.langs.zh && r.langs.zh.disabled, r.langs);
  ok('英文可选', r.langs.en && !r.langs.en.disabled, r.langs);
  ok('LANG 落在英文', r.LANG === 'en', r.LANG);
  ok('炎冰、金子都不列出', ['yanbing_selfie', 'yanbing_formal', 'jinzi_pro', 'jinzi_meet']
    .every(id => r.avatars.ids.indexOf(id) < 0), r.avatars);
  ok('第三人可选', r.avatars.ids.indexOf('diyi_work') >= 0, r.avatars);
  ok('自动选中第三人', r.AV === 'diyi_work', r.AV);
}

/* ---------------- 场景 4：老包（无 params / 无逐分身视频） ---------------- */
console.log('\n[场景4] 老包（无 params、无逐分身视频、仅中文）');
{
  const r = await run({ app: 'myme', manifest: 1, personas: personasAll(),
    versions: [{ id: 'default', name: '默认讲解', slides: [page({ audio: 'audio/narr-01.mp3', video: 'video/demo_av_default_1.mp4' })] }] });
  ok('中文可选', r.langs.zh && !r.langs.zh.disabled, r.langs);
  ok('英文置灰（本包无）', r.langs.en && r.langs.en.disabled, r.langs);
  ok('分身下拉不空（兜底全列）', r.avatars.ids.length >= 2, r.avatars);
  ok('不崩且摘要有内容', typeof r.pTag === 'string' && r.pTag.indexOf('本包：') === 0, r.pTag);
}

/* ---------------- 场景 5：纯图片包（无任何配音） ---------------- */
console.log('\n[场景5] 无配音（纯图片包）→ 语言两个都留');
{
  const r = await run({ app: 'myme', manifest: 1, personas: personasAll(),
    versions: [{ id: 'v1', name: 'V1', slides: [page({ img: 'images/a.png' })] }] });
  ok('中文可选', r.langs.zh && !r.langs.zh.disabled, r.langs);
  ok('英文可选', r.langs.en && !r.langs.en.disabled, r.langs);
}

/* ---------------- 场景 6：真实内置示例播放包 ---------------- */
console.log('\n[场景6] 真实内置示例包（C:/tmp/myme_pkg/portable/manifest.json）');
{
  const MP = 'C:/tmp/myme_pkg/portable/manifest.json';
  if (!fs.existsSync(MP)) { console.log('  ⏭ 未找到内置包，跳过'); }
  else {
    const man = JSON.parse(fs.readFileSync(MP, 'utf8'));
    const r = await run(man);
    const pr = man.params || {};
    ok('中文可选', r.langs.zh && !r.langs.zh.disabled, r.langs);
    ok('英文置灰（本包仅中文）', r.langs.en && r.langs.en.disabled, r.langs);
    ok('分身不含炎冰', r.avatars.ids.every(id => id.indexOf('yanbing') < 0), r.avatars);
    ok('自动选中清单标注的分身', r.AV === (pr.avkey || 'default'), { got: r.AV, want: pr.avkey });
    ok('顶栏摘要可读', typeof r.pTag === 'string' && r.pTag.indexOf('本包：') === 0, r.pTag);
  }
}

console.log('\n================ 结果 ================');
console.log('  通过 ' + pass + ' / 失败 ' + fail);
}
main().then(() => process.exit(fail ? 1 : 0), e => { console.error(e); process.exit(2); });
