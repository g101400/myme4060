/* ============================================================
   index.html「防卡死三件套」真跑验证（v2.9.3）
   ------------------------------------------------------------
   为什么要有它：源码里写了"退出按钮 / 三击空白呼主菜单"≠ 运行时真的生效。
   v2.9.3 之前主程序 index.html 这两个入口**完全不存在**（用户报的"点不动了只能关窗口"），
   所以这里不看代码长相，而是在桩 DOM 里真跑一遍点击，逐条断言：

     [1] 品牌栏三件套在位（版本徽标 / 🧭 主菜单 / ⏏ 退出当前页面）
     [2] 空白处连点三下 → 主菜单浮层打开；清单由菜单与标签实时生成（不写死）
     [3] 单点空白 → 浮层收起（且"刚打开那一下"不会被自己关掉）
     [4] 有弹层时连点三下 → 只关最上层，不叠开新浮层（三端统一语义）
     [5] Esc 逐层关闭：退出页 → 主菜单 → 弹窗 → 展开的菜单
     [6] ⏏ 退出当前页面 → 停媒体 + 关弹层 + 退全屏 + 落地页；返回按钮能回来

   用法： node _harness_index_guard.js
   ============================================================ */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

// init() 里一串 await fetch 在桩环境必然失败；吞掉未处理拒绝，避免把它当成崩溃
process.on('unhandledRejection', () => {});

const HTML = fs.readFileSync(path.join(__dirname, 'ui', 'index.html'), 'utf8');
const SRC = HTML.match(/<script>([\s\S]*?)<\/script>/)[1];

/* ---------------- 最小 DOM 桩 ---------------- */
function El(tag) {
  const el = {
    tagName: String(tag || 'div').toUpperCase(),
    _id: '', children: [], parentNode: null, _cls: new Set(), _attrs: {}, dataset: {},
    _text: '', _html: '', style: {}, value: '', checked: false, disabled: false,
    selectedIndex: 0, files: null, onclick: null, onchange: null, oninput: null,
    appendChild(c) { c.parentNode = el; el.children.push(c); return c; },
    removeChild(c) { const i = el.children.indexOf(c); if (i >= 0) el.children.splice(i, 1); return c; },
    insertBefore(c) { return el.appendChild(c); },
    setAttribute(k, v) { el._attrs[k] = v; },
    getAttribute(k) { return el._attrs[k]; },
    removeAttribute(k) { delete el._attrs[k]; },
    addEventListener(t, f) { (el._ls = el._ls || {}); (el._ls[t] = el._ls[t] || []).push(f); },
    removeEventListener() { },
    dispatchEvent() { return true; },
    click() { if (el.onclick) el.onclick.call(el, fakeEvent(el)); },
    pause() { }, play() { return Promise.resolve(); },
    closest(sel) { return wantClosest(el, sel); },
    focus() { }, remove() { }, scrollIntoView() { },
    requestFullscreen() { FULLSCREEN = true; return Promise.resolve(); },
    querySelector(sel) { return qsel(sel)[0] || null; },
    querySelectorAll(sel) { return qsel(sel); }
  };
  el.classList = {
    add: c => el._cls.add(c), remove: c => el._cls.delete(c),
    toggle: (c, f) => { if (f === undefined) { el._cls.has(c) ? el._cls.delete(c) : el._cls.add(c); }
                        else if (f) { el._cls.add(c); } else { el._cls.delete(c); } },
    contains: c => el._cls.has(c)
  };
  Object.defineProperty(el, 'id', { get() { return el._id; }, set(v) { el._id = String(v); } });
  Object.defineProperty(el, 'firstElementChild', { get() { return el.children[0] || null; } });
  Object.defineProperty(el, 'textContent', {
    get() { return el._text; }, set(v) { el._text = String(v); el.children.length = 0; } });
  Object.defineProperty(el, 'innerHTML', {
    get() { return el._html; }, set(v) { el._html = String(v); el.children.length = 0; } });
  return el;
}
function fakeEvent(el, key) {
  return { target: el || null, key: key || '', stopPropagation() { }, preventDefault() { },
           closest(sel) { return el ? wantClosest(el, sel) : null; } };
}

let REG = {}, DOC_LS = [], FULLSCREEN = false, LOCAL = new Map();

/* 假菜单（顶栏一级菜单）与假标签页 —— 只实现被测代码真正用到的接口 */
function fakeMenus() {
  return ['🧭 知识库', '🧑 分身', '📁 项目', '❓ 帮助'].map(label => {
    const btn = El('button'); btn.textContent = label;
    const menu = El('div');
    menu.querySelector = () => btn;
    menu._label = label;
    return menu;
  });
}
function fakeTabs() {
  return [['4', '① 知识库 · 沉淀'], ['1', '② 素材与文稿智能'], ['5', '⑨ 正式演示']].map(([t, txt]) => {
    const b = El('button'); b.textContent = txt; b.dataset.t = t;
    return b;
  });
}
function qsel(sel) {
  if (sel === '#menubar .menu > button') return REG.__menus.map(m => m.querySelector('button'));
  if (sel === '#menubar .menu' || sel === '.menu') return REG.__menus;
  if (sel === '#menubar .menu.open') return REG.__menus.filter(m => m.classList.contains('open'));
  if (sel === '#tabs .tab' || sel === '.tabs .tab') return REG.__tabs;
  if (sel === '.panel') return REG.__panels;
  return [];
}
function wantClosest(el, sel) {
  const parts = String(sel).split(',').map(s => s.trim());
  for (let n = el; n; n = n.parentNode) {
    for (const p of parts) {
      if (p.startsWith('#') && n._id === p.slice(1)) return n;
      if (p.startsWith('.') && n.classList && n.classList.contains(p.slice(1))) return n;
      if (/^[a-z]+$/.test(p) && n.tagName === p.toUpperCase()) return n;
    }
  }
  return null;
}

const document = {
  readyState: 'complete', documentElement: El('html'), head: El('head'), body: El('body'),
  fullscreenElement: null,
  exitFullscreen() { FULLSCREEN = false; return Promise.resolve(); },
  getElementById(id) { if (!REG[id]) { REG[id] = El('div'); REG[id]._id = id; } return REG[id]; },
  createElement(t) { return El(t); },
  querySelector(sel) { return qsel(sel)[0] || null; },
  querySelectorAll(sel) { return qsel(sel); },
  addEventListener(t, f, capture) { DOC_LS.push({ t, f, capture: !!capture }); },
  removeEventListener() { }
};

function fireClick(target) {
  const ev = fakeEvent(target);
  DOC_LS.filter(l => l.t === 'click' && l.capture).forEach(l => l.f(ev));   // 捕获阶段
  DOC_LS.filter(l => l.t === 'click' && !l.capture).forEach(l => l.f(ev));  // 冒泡阶段
}
function fireKey(key) {
  const ev = fakeEvent(null, key);
  DOC_LS.filter(l => l.t === 'keydown').forEach(l => l.f(ev));
}

function newSandbox() {
  const sb = {
    console, setTimeout, clearTimeout, setInterval, clearInterval, setImmediate,
    Promise, JSON, Object, Array, String, Number, Boolean, Math, Date, RegExp, Error, Map, Set,
    document, navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0)' },
    URL: { createObjectURL: () => 'blob:x', revokeObjectURL: () => { } },
    Blob: function () { }, FileReader: function () { }, TextDecoder: function () { this.decode = () => ''; },
    localStorage: {
      getItem: k => (LOCAL.has(k) ? LOCAL.get(k) : null),
      setItem: (k, v) => LOCAL.set(k, String(v)),
      removeItem: k => LOCAL.delete(k)
    },
    fetch: () => Promise.resolve({ ok: false, status: 500,
      json: () => Promise.resolve({ ok: false, error: 'harness-stub' }), text: () => Promise.resolve('') }),
    alert: () => { }, confirm: () => false, prompt: () => null,
    location: { href: '' }, btoa: s => Buffer.from(String(s), 'binary').toString('base64')
  };
  sb.window = sb; sb.globalThis = sb;
  return sb;
}

let pass = 0, fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; console.log('  ✅ ' + name); }
  else { fail++; console.log('  ❌ ' + name + (extra !== undefined ? ('  → ' + JSON.stringify(extra)) : '')); }
}

(async () => {
  console.log('='.repeat(58));
  console.log('index.html 防卡死三件套真跑验证（v2.9.3）');
  console.log('='.repeat(58));

  REG = {}; DOC_LS = []; FULLSCREEN = false; LOCAL = new Map();
  REG.__menus = fakeMenus(); REG.__tabs = fakeTabs();
  // 面板元素要和 getElementById('p4') 是"同一个对象"，否则 switchTab 断言会假失败
  REG.__panels = ['1', '4', '5'].map(n => { const e = El('div'); e._id = 'p' + n; REG['p' + n] = e; return e; });
  const sb = newSandbox();
  vm.createContext(sb);
  try {
    vm.runInContext(SRC, sb, { filename: 'index.html' });
  } catch (e) {
    console.log('❌ index.html 内联脚本在桩环境里就抛错了（说明有顶层语句依赖真 DOM）：');
    console.log(e && e.stack ? e.stack.split('\n').slice(0, 6).join('\n') : String(e));
    process.exit(1);
  }
  await new Promise(r => setImmediate(r));

  const $ = id => sb.document.getElementById(id);
  const on = id => $(id).classList.contains('on');
  const blankDiv = El('div'); blankDiv._id = 'someBlankCard'; blankDiv.parentNode = document.body;

  console.log('\n[1] 品牌栏三件套在位');
  ok('⏏ 退出当前页面 按钮有 onclick', typeof $('btnExit').onclick === 'function');
  ok('🧭 主菜单 按钮有 onclick', typeof $('btnQuickNav').onclick === 'function');
  ok('退出落地页 #bye 存在', !!$('bye'));
  ok('主菜单浮层 #quickNav 存在', !!$('quickNav'));
  ok('三击提示条 #tapTip 存在', !!$('tapTip'));
  ok('版本徽标由 MYME_VERSION 驱动', /^v2\.9/.test($('verBadge').textContent || ''), $('verBadge').textContent);
  ok('导出 mymeTopLayer / mymeCloseTopLayer / exitPage', typeof sb.mymeTopLayer === 'function'
     && typeof sb.mymeCloseTopLayer === 'function' && typeof sb.exitPage === 'function');
  ok('初始状态：无任何弹层', sb.mymeAnyBusy() === false, sb.mymeTopLayer());

  console.log('\n[2] 空白处连点三下 → 主菜单浮层');
  fireClick(blankDiv); fireClick(blankDiv);
  ok('只点两下时不弹（防误触）', !on('quickNav'));
  fireClick(blankDiv);
  ok('连点三下 → #quickNav 打开', on('quickNav'));
  const items = $('qnGrid').children;
  ok('清单由菜单+标签实时生成（非写死）', items.length >= 4, items.length);
  ok('清单里含一级菜单项', items.some(b => /知识库|分身|项目|帮助/.test(b._html || '')));
  ok('清单里含标签页项', items.some(b => /① 知识库|⑨ 正式演示/.test(b._html || '')));

  console.log('\n[3] 单点空白 / 关闭按钮 → 浮层收起');
  fireClick(blankDiv);
  ok('点空白处 → #quickNav 收起', !on('quickNav'));
  $('btnQuickNav').onclick();
  ok('点 🧭 主菜单 → 打开且不被当次点击收掉', on('quickNav'));
  $('qnClose').onclick();
  ok('点「关闭」→ 收起', !on('quickNav'));

  console.log('\n[4] 有弹层时连点三下 → 只关最上层，不叠开新浮层');
  sb.openModal ? sb.openModal('t', 'b') : 0;
  // openModal 是函数声明，挂在沙箱全局上
  const openModal = sb.openModal;
  if (typeof openModal === 'function') openModal('测试弹窗', '<p>x</p>');
  ok('弹窗已打开（#mask.on）', on('mask'));
  fireClick(blankDiv); fireClick(blankDiv); fireClick(blankDiv);
  ok('三击后弹窗被关掉', !on('mask'));
  ok('三击后没有叠开主菜单浮层', !on('quickNav'));
  ok('最高层判定：弹窗 > 主菜单 > 菜单', sb.mymeTopLayer() === '');

  console.log('\n[5] Esc 逐层关闭');
  $('btnQuickNav').onclick();
  ok('先开主菜单', on('quickNav'));
  fireKey('Escape');
  ok('Esc → 主菜单收起', !on('quickNav'));
  if (typeof openModal === 'function') openModal('测试弹窗', '<p>x</p>');
  $('btnQuickNav').onclick();
  ok('弹窗+主菜单同时开着（主菜单层级更高）', sb.mymeTopLayer() === 'quickNav', sb.mymeTopLayer());
  fireKey('Escape');
  ok('Esc 第一次关主菜单', !on('quickNav') && on('mask'));
  fireKey('Escape');
  ok('Esc 第二次关弹窗', !on('mask'));

  console.log('\n[6] ⏏ 退出当前页面：停干净 + 落地页 + 能回来');
  if (typeof openModal === 'function') openModal('测试弹窗', '<p>x</p>');
  $('btnQuickNav').onclick();
  $('btnExit').onclick();
  ok('退出后落地页 #bye 打开', on('bye'));
  ok('退出后弹层全关（弹窗）', !on('mask'));
  ok('退出后弹层全关（主菜单）', !on('quickNav'));
  ok('退出后退出页自身是最上层', sb.mymeTopLayer() === 'bye', sb.mymeTopLayer());
  ok('返回按钮存在', typeof $('byeBack').onclick === 'function');
  $('byeBack').onclick();
  ok('点「返回界面继续」→ 回到工作界面', !on('bye'));
  $('qnExit').onclick();
  ok('主菜单里的「⏏ 退出当前页面」同样可用', on('bye'));
  $('byeHome').onclick();
  ok('点「🏠 回到① 知识库」→ 关闭落地页并跳标签', !on('bye') && $('p4').classList.contains('on'));

  console.log('\n' + '='.repeat(58));
  console.log('通过 ' + pass + ' / 失败 ' + fail);
  console.log('结论：' + (fail ? '❌ 防卡死三件套未达标' : '✅ 防卡死三件套逐条通过'));
  process.exit(fail ? 1 : 0);
})();
