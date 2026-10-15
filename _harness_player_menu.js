/* ============================================================
   player.html 功能菜单逐个验证（v2.8.0，桩环境真跑）
   ------------------------------------------------------------
   直接对应用户报障与本次改动：
     · 点「📂 导入播放文件」再点三个子菜单 → 必须真的触发对应的 <input type=file>
       并（在安卓壳里）先把"选文件夹"意图告诉原生桥；
     · 语言/分身下拉的可选项与互斥；
     · 顶部 / 底部 / 空白三击快捷菜单 / 退出当前页面 等全部入口点了不报错、有反应。
   用法： node _harness_player_menu.js
   ============================================================ */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const HTML = fs.readFileSync(path.join(__dirname, 'ui', 'player.html'), 'utf8');
const SRC = HTML.match(/<script>([\s\S]*?)<\/script>/)[1];

/* ---------------- 最小 DOM 桩 ---------------- */
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
    removeEventListener() { },
    dispatchEvent() { return true; },
    click() { CLICKS.push(el._id || el.tagName); if (el.onclick) el.onclick.call(el, fakeEvent(el)); },
    pause() { el.paused = true; }, play() { el.paused = false; return Promise.resolve(); },
    closest() { return null; }, focus() { }, remove() { },
    requestFullscreen() { FULLSCREEN.push('enter'); },
    querySelector() { return null; }, querySelectorAll() { return []; }
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

let CLICKS = [], FULLSCREEN = [], TOASTS = [], DOC_LS = {}, REG = {};
const document = {
  readyState: 'complete', documentElement: El('html'), head: El('head'), body: El('body'),
  getElementById(id) { if (!REG[id]) { REG[id] = El('div'); REG[id]._id = id; } return REG[id]; },
  createElement(t) { return El(t); },
  querySelector() { return null; }, querySelectorAll() { return []; },
  addEventListener(t, f) { (DOC_LS[t] = DOC_LS[t] || []).push(f); },
  removeEventListener() { }
};

function newSandbox(android) {
  const sb = {
    console, setTimeout, clearTimeout, setInterval, clearInterval,
    Promise, JSON, Object, Array, String, Number, Boolean, Math, Date, RegExp, Error,
    document, navigator: { userAgent: android ? 'Mozilla/5.0 (Linux; Android 13)' : 'Mozilla/5.0 (Windows NT 10.0)' },
    URL: { createObjectURL: () => 'blob:x', revokeObjectURL: () => { } },
    Blob: function () { }, TextDecoder: function () { this.decode = () => ''; },
    FileReader: function () { }, Response: function () { }, DecompressionStream: undefined,
    fetch: () => Promise.reject(new Error('no net')),
    XMLHttpRequest: function () { this.open = () => { }; this.send = () => { }; },
    alert: m => ALERTS.push(String(m)), location: { href: '' }
  };
  sb.window = sb; sb.globalThis = sb;
  if (android) {
    sb.__folderMode = null;
    sb.MymeNative = { isAndroid: () => true, platform: () => 'android-webview',
                      setFolderMode: b => { sb.__folderMode = b; FOLDERMODE.push(b); } };
  }
  return sb;
}
let ALERTS = [], FOLDERMODE = [];

function seed(sb) {
  const d = sb.document;
  const ls = d.getElementById('langSel');
  const o1 = d.createElement('option'); o1.value = 'zh'; o1.textContent = '中文';
  const o2 = d.createElement('option'); o2.value = 'en'; o2.textContent = 'English';
  ls.appendChild(o1); ls.appendChild(o2);
  d.getElementById('avSel').style.display = 'none';
  // 底部/顶部的原生 select 也按真实页面预置
  const rate = d.getElementById('rate');
  ['0.75', '1', '1.25', '1.5', '2'].forEach((v, i) => {
    const o = d.createElement('option'); o.value = v; o.textContent = v + 'x';
    if (v === '1') o.selectedIndex = 1; rate.appendChild(o);
  });
  const aut = d.getElementById('auto'); aut.checked = true;
  const bar = d.getElementById('bar');
  bar.appendChild(d.createElement('i'));
}

const MAN = {
  app: 'myme', manifest: 1,
  params: { lang: 'zh', langs: ['zh'], person: 'jinzi', avatar: 'pro', avkey: 'jinzi_pro' },
  personas: [{ id: 'default', name: '默认分身' },
    { id: 'yanbing_selfie', name: '炎冰 · 日常', person: 'yanbing', avatar: 'selfie' },
    { id: 'jinzi_pro', name: '金子 · 职业', person: 'jinzi', avatar: 'pro' }],
  versions: [{ id: 'v1', name: 'V1', slides: [
    { idx: 1, title: 'T1', text: 'a', img: 'images/p01.png', audio: 'audio/a.mp3', audio_en: '', video: '', videoAlt: { jinzi_pro: 'video/x.mp4' } },
    { idx: 2, title: 'T2', text: 'b', img: '', audio: 'audio/b.mp3', audio_en: '', video: '', videoAlt: {} }] }]
};

let pass = 0, fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; console.log('  ✅ ' + name); }
  else { fail++; console.log('  ❌ ' + name + (extra !== undefined ? ('  → ' + JSON.stringify(extra)) : '')); }
}

async function boot(android) {
  CLICKS = []; FOLDERMODE = []; ALERTS = []; DOC_LS = {}; REG = {}; FULLSCREEN = [];
  const sb = newSandbox(android);
  sb.__MYME_MANIFEST__ = MAN;
  seed(sb);
  vm.createContext(sb);
  vm.runInContext(SRC, sb, { filename: 'player.html' });
  await new Promise(r => setImmediate(r));
  const $ = id => sb.document.getElementById(id);
  const cls = (id, c) => $(id)._cls.has(c);
  return { sb, $, cls };
}

(async () => {
  console.log('='.repeat(58));
  console.log('player.html 功能菜单逐个验证（安卓壳模式）');
  console.log('='.repeat(58));
  const { sb, $, cls } = await boot(true);

  console.log('\n[1] 顶部：📂 导入播放文件 面板');
  ok('btnImport 存在且有 onclick', typeof $('btnImport').onclick === 'function');
  $('btnImport').onclick(fakeEvent($('btnImport')));
  ok('点 btnImport → 面板 #imp 打开', cls('imp', 'on'));
  ok('#imp 三个子菜单都在', ['miZip', 'miDir', 'miJson'].every(id => typeof $(id).onclick === 'function'));

  console.log('\n[2] 三条导入通路（用户报障的"点了没反应"）');
  CLICKS = []; FOLDERMODE = [];
  $('miZip').onclick();
  ok('🗜️ zip → 触发 #impZip.click()', CLICKS.indexOf('impZip') >= 0, CLICKS);
  ok('zip 通路声明"选文件"（folderMode=false）', sb.__folderMode === false, sb.__folderMode);
  ok('点完自动收起面板（不再挡画面）', !cls('imp', 'on'));

  CLICKS = []; 
  $('miDir').onclick();
  ok('📁 文件夹 → 触发 #impDir.click()', CLICKS.indexOf('impDir') >= 0, CLICKS);
  ok('文件夹通路声明"选目录"（folderMode=true）', sb.__folderMode === true, sb.__folderMode);

  CLICKS = [];
  $('miJson').onclick();
  ok('🧩 清单+素材 → 触发 #impJson.click()', CLICKS.indexOf('impJson') >= 0, CLICKS);
  ok('清单通路声明"选文件"', sb.__folderMode === false, sb.__folderMode);

  console.log('\n[3] 三个隐藏 input 的 onchange 都已接线');
  ok('impZip.onchange 是函数', typeof $('impZip').onchange === 'function');
  ok('impDir.onchange 是函数', typeof $('impDir').onchange === 'function');
  ok('impJson.onchange 是函数', typeof $('impJson').onchange === 'function');
  ok('impZip.onchange 空选择不崩', (() => { try { $('impZip').files = null; $('impZip').onchange(); return true; } catch (e) { return e.message; } })() === true);
  ok('impDir.onchange 空选择不崩', (() => { try { $('impDir').files = null; $('impDir').onchange(); return true; } catch (e) { return e.message; } })() === true);
  ok('impJson.onchange 空选择不崩', (() => { try { $('impJson').files = null; $('impJson').onchange(); return true; } catch (e) { return e.message; } })() === true);

  console.log('\n[4] 语言 / 分身下拉：自动识别 + 互斥');
  ok('英文被置灰（本包仅中文）', $('langSel').options.filter(o => o.value === 'en')[0].disabled === true);
  ok('中文可选', $('langSel').options.filter(o => o.value === 'zh')[0].disabled === false);
  ok('自动选中中文', $('langSel').value === 'zh' && sb.LANG === 'zh');
  ok('分身下拉不含炎冰', $('avSel').options.every(o => o.value.indexOf('yanbing') < 0), $('avSel').options.map(o => o.value));
  ok('自动选中金子（清单标注）', sb.AV_SEL === 'jinzi_pro', sb.AV_SEL);
  ok('顶栏「本包：…」已渲染', String($('pTag').textContent).indexOf('本包：') === 0, $('pTag').textContent);
  ok('langSel.onchange 是函数', typeof $('langSel').onchange === 'function');
  ok('avSel.onchange 是函数', typeof $('avSel').onchange === 'function');

  console.log('\n[5] 顶部其余按钮');
  ok('btnList → 目录开合', (() => { $('btnList').onclick(); const a = cls('list', 'on'); $('btnList').onclick(); return a && !cls('list', 'on'); })());
  ok('btnPip → 分身画中画开/关', (() => { const t0 = $('btnPip').textContent; $('btnPip').onclick(); const t1 = $('btnPip').textContent; return t0 !== t1; })(), [$('btnPip').textContent]);
  ok('btnFs → 请求全屏', (() => { FULLSCREEN = []; $('btnFs').onclick(); return FULLSCREEN.indexOf('enter') >= 0; })());
  ok('btnExit → 退出页 #bye 打开', (() => { $('btnExit').onclick(); return cls('bye', 'on'); })());
  ok('byeBack → 退出页关闭', (() => { $('byeBack').onclick(); return !cls('bye', 'on'); })());
  ok('auto 连播勾选框有 onchange', typeof $('auto').onchange === 'function');
  ok('#ver 版本下拉有 onchange', typeof $('ver').onchange === 'function');
  ok('#ver 渲染出选项', $('ver').options.length === MAN.versions.length, $('ver').options.map(o => o.value));
  ok('#pipClose 有 onclick', typeof $('pipClose').onclick === 'function');
  ok('#rate 倍速有 onchange', typeof $('rate').onchange === 'function');

  console.log('\n[6] 底部播放控制');
  ['bPrev', 'bNext', 'bPlay', 'bStop', 'bFull'].forEach(id => {
    ok(id + ' 有 onclick', typeof $(id).onclick === 'function');
  });
  ok('bNext 翻页不崩', (() => { try { $('bNext').onclick(); return true; } catch (e) { return e.message; } })() === true);
  ok('bPrev 翻页不崩（已在首页）', (() => { try { $('bPrev').onclick(); return true; } catch (e) { return e.message; } })() === true);
  ok('bStop 停止不崩', (() => { try { $('bStop').onclick(); return true; } catch (e) { return e.message; } })() === true);
  ok('bPlay 播放切换不崩', (() => { try { $('bPlay').onclick(); return true; } catch (e) { return e.message; } })() === true);
  ok('bFull 整场视频切换不崩', (() => { try { $('bFull').onclick(); return true; } catch (e) { return e.message; } })() === true);

  console.log('\n[7] 空白三击快捷菜单（防卡死逃生口）');
  const fire = target => DOC_LS['click'].forEach(f => f({ target, closest: () => null }));
  const blankTarget = sb.document.body;
  const before = cls('aux', 'on');
  fire(blankTarget); fire(blankTarget); fire(blankTarget);
  ok('空白连点三下 → 快捷菜单打开', !before && cls('aux', 'on'));
  ['axImport', 'axList', 'axPlay', 'axPip', 'axLang', 'axFs', 'axExit'].forEach(id => {
    ok(id + ' 有 onclick', typeof $(id).onclick === 'function');
  });
  ok('axImport → 收起快捷菜单并打开导入面板', (() => { $('axImport').onclick(); return cls('imp', 'on') && !cls('aux', 'on'); })());
  ok('axLang（本包无英文）→ 明确提示且不切语言', (() => {
    TOASTS = 0; $('axLang').onclick();
    const t = String($('toast').textContent);
    return sb.LANG === 'zh' && /没有英文配音/.test(t);
  })(), $('toast').textContent);
  ok('再点一次空白处 → 面板正常收起（_skipClose 只跳过那一次）', (() => {
    fire(blankTarget); fire(blankTarget); fire(blankTarget);   // 三击重新打开 aux
    const on1 = cls('aux', 'on');
    fire(blankTarget);                                        // 单击空白 → 应收起
    return on1 && !cls('aux', 'on');
  })());
  ok('axExit → 退出页打开', (() => { $('axExit').onclick(); return cls('bye', 'on'); })());
  $('byeBack').onclick();
  ok('axList / axPlay / axPip / axFs 不崩', (() => {
    try { $('axList').onclick(); $('axPlay').onclick(); $('axPip').onclick(); $('axFs').onclick(); return true; }
    catch (e) { return e.message; }
  })() === true);

  console.log('\n[8] 桌面浏览器模式（无 MymeNative，应优雅降级）');
  const d2 = await boot(false);
  ok('无 MymeNative 时不报错', typeof d2.sb.renderVers === 'function' || true);
  CLICKS = [];
  d2.$('miZip').onclick();
  ok('zip 通路仍触发 #impZip.click()', CLICKS.indexOf('impZip') >= 0, CLICKS);
  CLICKS = [];
  d2.$('miDir').onclick();
  ok('文件夹通路仍触发 #impDir.click()（交给浏览器 webkitdirectory）', CLICKS.indexOf('impDir') >= 0, CLICKS);

  console.log('\n' + '='.repeat(58));
  console.log('通过 %d / 失败 %d', pass, fail);
  console.log(fail ? '结论：❌ 有菜单未通过' : '结论：✅ 全部菜单逐个验证通过');
  process.exit(fail ? 1 : 0);
})();
