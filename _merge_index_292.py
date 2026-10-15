# -*- coding: utf-8 -*-
"""index.html v2.9.2 合并脚本（九处编辑 + 变更记录重排 + 陈旧编号清零）。
纪律：每个替换带出现次数断言；变更记录区只做块级重排，不做字样替换。
"""
import io, re, sys

P = r'D:\Users\WorkBuddy\Claw\myme\ui\index.html'
src = io.open(P, encoding='utf-8').read()
ORIG = src

def rep(old, new, cnt=1, where=''):
    global src
    n = src.count(old)
    if n != cnt:
        raise SystemExit('ANCHOR COUNT %d != %d %s: %r' % (n, cnt, where, old[:70]))
    src = src.replace(old, new)
    print('OK  [%s] %s' % (where, old[:52].replace('\n', '⏎')))

# ---------- 分区：变更记录函数整体切出，历史块只重排不改字 ----------
chg_start = src.find('function dlgChangelog(){')
if chg_start < 0: raise SystemExit('no dlgChangelog')
chg_end = src.find('`);}', chg_start)
if chg_end < 0: raise SystemExit('no changelog end')
chg_end += 4
R1, RM, R2 = src[:chg_start], src[chg_start:chg_end], src[chg_end:]

def rep1(old, new, cnt=1):  # 变更记录之前
    global R1
    n = R1.count(old)
    if n != cnt: raise SystemExit('R1 COUNT %d != %d: %r' % (n, cnt, old[:70]))
    R1 = R1.replace(old, new); print('OK  [R1] %s' % old[:52].replace('\n', '⏎'))

def rep2(old, new, cnt=1):  # 变更记录之后
    global R2
    n = R2.count(old)
    if n != cnt: raise SystemExit('R2 COUNT %d != %d: %r' % (n, cnt, old[:70]))
    R2 = R2.replace(old, new); print('OK  [R2] %s' % old[:52].replace('\n', '⏎'))

# ============ 1) MYME_VERSION 常量 + 关于版本行 + 软件清单动态版本 ============
rep1("function closeModal(){$('mask').classList.remove('on');}\n$('mClose').onclick=closeModal;",
     "function closeModal(){$('mask').classList.remove('on');}\n"
     "$('mClose').onclick=closeModal;\n"
     "$('mask').onclick=e=>{if(e.target===$('mask'))closeModal();};\n\n"
     "/* 当前应用版本（与 config.VERSION / 打包版本号一致；帮助「关于 / 软件配置清单」由此取） */\n"
     'const MYME_VERSION="2.9.2";')
rep1("  <p><b>__APP__</b> —— 本地运行的 PPT 制作与分身演示助手。</p>\n  <div class=\"sw\"><span>声音</span>",
     "  <p><b>__APP__</b> —— 本地运行的 PPT 制作与分身演示助手。</p>\n"
     "  <div class=\"sw\"><span>版本</span><span>v${MYME_VERSION}</span></div>\n"
     "  <div class=\"sw\"><span>声音</span>")
rep1('<p class="muted">当前版本 <b>v2.9.1</b>。本机运行数字分身所依赖的软件与模型清单',
     '<p class="muted">当前版本 <b>v${MYME_VERSION}</b>。本机运行数字分身所依赖的软件与模型清单')
for a, b in [('myme-2.9.1-win-x64-full.msi', 'myme-2.9.2-win-x64-full.msi'),
             ('myme-setup.exe + myme-2.9.1-runtime.7z', 'myme-setup.exe + myme-2.9.2-runtime.7z'),
             ('myme-2.9.1-win-x64-play.zip', 'myme-2.9.2-win-x64-play.zip'),
             ('myme-2.9.1-android-play.apk', 'myme-2.9.2-android-play.apk'),
             ('myme-player_2.6.1_all.deb', 'myme-player_2.9.2_all.deb')]:
    rep1(a, b)

# ============ 2) dlgFeatures → 13 标签新版（整函数替换） ============
NEW_FEATURES = '''function dlgFeatures(){openModal('功能介绍',`
  <p class="muted">整套流程一句话：<b>选分身 → 沉淀知识库 → 素材与文稿智能(出稿) → 制作 PPT/PDF → 生成分身语音讲解 → 制作视频 → 正式演示 → 答疑 → 总结 → 导出交付</b>，
     最后把新资料沉淀回知识库，形成闭环。第三层<b>十三个</b>标签就按这个顺序排，从左到右走完即是一整场。</p>
  <p class="muted"><b>分身在哪选？</b>顶栏第二层作用域栏：<b>项目 + 分身 + 形象</b> 三个下拉。
     分身＝声音与人设，形象＝画面上的样子；切换后全系统生效（讲解、答疑、视频用的是同一个声纹）。</p>

  <h4 style="margin:12px 0 4px">准备</h4>
  <p class="muted"><b>① 知识库 · 沉淀</b>：PDF / Word / Excel / PPT / TXT / Markdown 等 18 种格式增量入库，
     也可整目录批量。分<b>公有 / 项目 / 分身私有</b>三层，检索时一起查、私有加权；私有内容可一键"提升"到公有沉淀复用。
     支持导出备份 .mymekb、导入还原、重建去重、重建向量与检索自检。</p>
  <p class="muted"><b>② 素材与文稿智能</b>：根据知识库生成书面汇报材料（总结 / 半年 / 工作 / 项目 / 季度），可下载 .docx；
     PDF 在「③ 制作 PPT」同步导出，打磨与出稿都在此阶段完成。</p>

  <h4 style="margin:12px 0 4px">出稿</h4>
  <p class="muted"><b>③ 制作 PPT · PDF</b>：给个主题就出一套 PPT；可<b>选模板</b>（导入自家 .pptx 继承字体/主色/版式），
     可<b>同步导出 PDF</b>，还能再出一份<b>书面文档（doc / pdf）</b>——PPT 是要点式给"讲"用的，
     书面文档是段落式给"看"用的，内容会自动扩写详实。
     同一份大纲还可一键出 <b>Markdown 思维导图</b>，可导入 Xmind / 幕布 / 语雀 继续手绘。</p>
  <p class="muted"><b>④ 生成分身语音讲解</b>：把 PPT 解析成讲解页，用当前分身的声音逐页合成<b>中英双语</b>讲解音频，
     可分段试听、重生成；缺英文配音时会明确提示缺几页，可一键补录，不会静默播中文。</p>
  <p class="muted"><b>⑤ 制作视频</b>：输入台词 → 当前分身音色 + 口型驱动的形象 → 输出 MP4。
     口型驱动为<b>双引擎</b>：优先 <b>Sonic 扩散式</b>（整帧生成，几乎不变形，需本机 ComfyUI + NVIDIA 显卡），
     不可用或推理失败时自动回退 <b>LivePortrait</b>（轻量 2D 驱动；播放包固定用它）。
     「分身管理（人 × 形象）」是本页唯一入口：一个人可以有多张形象，换图即换分身，零训练。
     炎冰 / 金子的 Sonic 真机演示片段已<b>内置</b>在帮助菜单「🎭 内置分身演示（验证环境）」中，可随时播放对照。</p>

  <h4 style="margin:12px 0 4px">加工</h4>
  <p class="muted"><b>⑥ 多源搬运 · 多项产出</b>：一行链接或关键词 → 自动认来源（微信 / 网页 / B站 / 播客 / 微博 /
     今日头条 / 百家号 / 文档 / 音频 / 压缩包 / 目录）→ 按来源选策略清洗 → 入库；
     <b>七项产物</b>（报告 / 幻灯片 / 播客讲稿+音频 / 思维导图 / 测验题 / 记忆闪卡 / 数据表）据知识库真实片段生成，
     全部<b>本地离线版、不依赖任何境外服务</b>，单条失败不中断整批。</p>
  <p class="muted"><b>⑦ 领域包 · 水利古建与感知</b>：内置水利一张图三端四平台 / 水利古建遗产 / 感知应用三档，
     骨架 + 术语 + 口径都可改（落 <span class="fk">domains/*.json</span>，改完不用重启）；
     按骨架一键出 PPT，或直接让 Agent 按本行业口径答疑——口径 → 演示 → 答疑 → 沉淀，闭环。</p>
  <p class="muted"><b>⑧ 深度加工 · 图谱/Agent/分块</b>：知识图谱增强检索（问答候选 6→24 条并标注出处）、
     ReAct Agent 答疑（自规划 + 工具调用 + 可开人工审批，联网动作等你确认）、
     分块可编辑（改 → 重算向量 → diff → 回滚，旧版自动进快照）、
     链路追踪（本地版 Langfuse，记录检索路径 / 耗时 / token）——检索不再是个黑盒子。</p>

  <h4 style="margin:12px 0 4px">演示</h4>
  <p class="muted"><b>⑨ 正式演示</b>：分身 + PPT 原稿逐页同步，自动翻页播讲解；被提问点「⏸ 领导提问」暂停答疑，
     答完「▶ 继续演示（从此页）」精确续播。</p>
  <p class="muted"><b>⑩ 智能答疑</b>：跨三层知识库检索作答，中英文提问都能识别；命中某页自动展示该页；可勾「记入答疑记录」。</p>
  <p class="muted"><b>⑪ 答疑总结</b>：自动生成《答疑汇总》，导出 Markdown，也可把高频问答沉淀回知识库。</p>

  <h4 style="margin:12px 0 4px">交付</h4>
  <p class="muted"><b>⑫ 导出交付</b>：集中导出本项目全部产出（源文件 / 形象 / 方案 / 音频 / 视频 / 分身）。</p>
  <p class="muted"><b>⑬ 播放文件</b>：导出离线演示包。勾上「📱 打包为离线便携播放包」后，
     解压双击 <code>player.html</code> 就能播，同一个包在 <b>Win / 安卓 / 统信</b> 三端通用；
     播放端导入时按包内 <code>manifest.params</code> 自动识别语言与分身，本包没有的选项会自动置灰。</p>

  <h4 style="margin:12px 0 4px">三层导航怎么理解</h4>
  <p class="muted"><b>第一层菜单</b>＝全局命令：<b>项目 / 演示向导 / 📥 多源导入 / 📤 多项导出 / 📚 知识库 / 播放 / 视图 / 语言 / 设置 / 帮助</b>，全部下拉式；
     <b>第二层作用域栏</b>＝当前作用于哪个【项目 + 分身 + 形象】，只做选择不执行命令；
     <b>第三层标签</b>＝按演示生命周期排列的<b>十三个</b>工作台。
     <b>语言一处切换、全程生效</b>：菜单「🌐 语言」选中文或英文后，讲解、答疑、书面稿、PPT 与分身视频会同步到同一语言。</p>
  <p class="muted" style="margin-top:10px">💡 想按部就班走一遍：菜单「🎬 演示流程」八阶段向导，每步都能直接跳到对应模块。</p>`);}'''
f_start = R1.find('function dlgFeatures(){')
if f_start < 0: raise SystemExit('no dlgFeatures')
f_end = R1.find('`);}', f_start)
if f_end < 0: raise SystemExit('no dlgFeatures end')
f_end += 4
R1 = R1[:f_start] + NEW_FEATURES + R1[f_end:]
print('OK  [R1] dlgFeatures whole-function replaced (%d chars)' % (f_end - f_start))

# ============ 3) 其余陈旧编号（变更记录之前） ============
rep1('/* ============ ⑤ 正式演示：全屏 + 底部状态栏字幕 ============ */',
     '/* ============ ⑨ 正式演示：全屏 + 底部状态栏字幕 ============ */')
rep1('  <!-- ======== ⑤ 正式演示 ======== -->', '  <!-- ======== ⑨ 正式演示 ======== -->')
rep1('在「完整版」的 <b>⑤ 正式演示</b> 中点', '在「完整版」的 <b>⑨ 正式演示</b> 中点')
rep1('（无分身，请在「④ 制作视频」页的「分身管理」卡片添加）',
     '（无分身，请在「⑤ 制作视频」页的「分身管理」卡片添加）')
rep1('// ⚠ 原实现无条件把当前形象重置为该分身的第一张图，导致用户在 ④ 数字分身 里',
     '// ⚠ 原实现无条件把当前形象重置为该分身的第一张图，导致用户在「⑤ 制作视频」的分身管理里')
rep1('则「④ 制作视频」也会走同样的引擎。', '则「⑤ 制作视频」也会走同样的引擎。')
rep1('：到「④ 制作视频」用任一分身生成 8 秒视频即可', '：到「⑤ 制作视频」用任一分身生成 8 秒视频即可')
rep1("const TAB_LABEL = {1:'① 素材准备',9:'② 制作 PPT',4:'③ 知识库',3:'④ 数字分身',\n                   5:'⑤ 正式演示',2:'⑥ 智能答疑',6:'⑦ 答疑总结',7:'⑧ 资源管理',8:'⑨ 播放文件'};",
     "const TAB_LABEL = {4:'① 知识库 · 沉淀',1:'② 素材与文稿智能',9:'③ 制作 PPT · PDF',10:'④ 生成分身语音讲解',\n"
     "                   3:'⑤ 制作视频',11:'⑥ 多源搬运 · 多项产出',12:'⑦ 领域包 · 水利古建与感知',13:'⑧ 深度加工 · 图谱/Agent/分块',\n"
     "                   5:'⑨ 正式演示',2:'⑩ 智能答疑',6:'⑪ 答疑总结',7:'⑫ 导出交付',8:'⑬ 播放文件'};")

# ============ 4) 导出对话框：参数契约（交接线实现移植） ============
rep2('let _exStop=false, _lastExSig=null, _lastExFile=null, _lastExName=null;',
     'let _exStop=false, _lastExSig=null, _lastExFile=null, _lastExName=null, _exParams=null;')
rep2("""  let vids=[];
  try{ const r=await getJSON('/api/playback_files'); vids=r.videos||[]; }catch(e){}
  const vers=(S.versions||[]);""",
     """  let vids=[];
  try{ const r=await getJSON('/api/playback_files'); vids=r.videos||[]; }catch(e){}
  // 播放参数契约（lang / person / avatar / avkey）：与后端导出、播放端导入同一组参数名。
  // 导出前就把"本包是中文还是英文、分身是谁"显示出来，导出的 manifest.params 与这里一致。
  try{ _exParams=await postJSON('/api/resources',{act:'playback_params'}); }catch(e){ _exParams=null; }
  const P=_exParams||{};
  const LangN={zh:'中文',en:'English'};
  const pLangs=(P.langs||[]).map(x=>LangN[x]||x).join('、')||'未检测';
  const pMain=LangN[P.lang]||P.lang||'未标注';
  const pWho=P.avatar_name||([P.person,P.avatar].filter(Boolean).join(' · '))||'未标注';
  const paramsBox=`
    <div class="exSec" style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px">
      <b>🏷 本包标注（导入播放文件时自动识别，本包没有的选项会自动置灰）</b>
      <ul class="flist" style="margin:6px 0 0">
        <li><span class="fk">lang（配音语言）</span><span class="fv">${esc(pMain)}　·　本包实际包含：${esc(pLangs)}</span></li>
        <li><span class="fk">person / avatar（分身）</span><span class="fv">${esc(pWho)}</span></li>
        <li><span class="fk">avkey（分身键）</span><span class="fv">${esc(P.avkey||'未标注')}　<code>person_avatar</code>，与逐页视频名中的那段一致</span></li>
      </ul>
      <p class="muted" style="margin:6px 0 0">生成讲解、导出播放文件、导入播放文件三处使用这同一组参数（也写进包内
        <code>manifest.json</code> 的 <code>params</code> 与 <code>README.txt</code>），所以播放端导入后能自动认出：
        只有中文配音时英文置灰；本包只有金子时分身里不出现炎冰。</p>
    </div>`;
  const vers=(S.versions||[]);""")
rep2("""      || '<span class="muted">暂无整场分身视频（可在「⑤ 正式演示」点「生成整场分身视频」后导出）</span>';""",
     """      || '<span class="muted">暂无整场分身视频（可在「⑨ 正式演示」点「生成整场分身视频」后导出）</span>';""")
rep2("""    <p class="muted">选择要导出的内容，生成 zip 后可在「便携播放版（⑧）」导入离线演示。带 <b>*</b> 为必选项。</p>
    <div class="exSec">""",
     """    <p class="muted">选择要导出的内容，生成 zip 后可在「便携播放版（⑬）」导入离线演示。带 <b>*</b> 为必选项。</p>
    ${paramsBox}
    <div class="exSec">""")

# ============ 5) doExportPlayback：四键透传 + 本包标注回显 ============
rep2("""  try{
    const r=await postJSON('/api/resources',{act:'export',cats,
      versions: needVersions?selVer:[], videos: needVideo?selVid:[], quality,
      include_player: includePlayer});""",
     """  try{
    // 参数与「本包标注」框显示的一致（后端未指定时也会用同一套兜底规则取值）
    const P=_exParams||{};
    const r=await postJSON('/api/resources',{act:'export',cats,
      versions: needVersions?selVer:[], videos: needVideo?selVid:[], quality,
      include_player: includePlayer,
      lang:P.lang||'', person:P.person||'', avatar:P.avatar||'', avkey:P.avkey||''});""")
rep2("""      const ff=r.ffmpeg?'':'⚠ 未检测到 ffmpeg，已按<b>无损原样</b>拷贝。';
      _showEx('✅ 已生成 <b>'+r.count+'</b> 项 · 精度 <b>'+esc(r.quality||quality)+'</b> · 体积 <b>'+mb+'</b>。'+ff
        +'<br>文件：<b>'+esc(r.name)+'</b><br>'
        +'服务器已保存于：<code>'+esc(r.path||'')+'</code><br>'
        +'已自动开始下载；若未弹出，请点下方「下载文件」。也可在「便携播放版（⑧）」导入此 zip。'""",
     """      const ff=r.ffmpeg?'':'⚠ 未检测到 ffmpeg，已按<b>无损原样</b>拷贝。';
      const RP=r.params||{};
      const LANGN={zh:'中文',en:'English'};
      const mark=RP.avkey||RP.person
        ? ('<br>🏷 本包标注：<b>'+(LANGN[RP.lang]||RP.lang||'未标注')+'</b>'
           +' · 分身 <b>'+esc(RP.avkey||RP.person)+'</b>（已写入 manifest.params 与 README.txt，导入时自动识别）')
        : '';
      _showEx('✅ 已生成 <b>'+r.count+'</b> 项 · 精度 <b>'+esc(r.quality||quality)+'</b> · 体积 <b>'+mb+'</b>。'+ff
        +'<br>文件：<b>'+esc(r.name)+'</b><br>'
        +'服务器已保存于：<code>'+esc(r.path||'')+'</code><br>'
        +'已自动开始下载；若未弹出，请点下方「下载文件」。也可在「便携播放版（⑬）」导入此 zip。'
        +mark""")

# ============ 6) 导入侧：_lastImportParams + 回显 ============
rep2("""    _impHide();
    return last.count||0;""",
     """    _impHide();
    window._lastImportParams = last.params || null;   // 包内 manifest.params，供导入后回显
    return last.count||0;""")
rep2("""    const n=await uploadZipFile(f);
    if(n!==null){ $('playImportHint').textContent='已导入 '+n+' 项，前往「⑤ 正式演示」即可播放';
      await loadSlides(); await loadVersions(); await loadPersons(); renderPlayList(); switchTab('5'); }""",
     """    const n=await uploadZipFile(f);
    if(n!==null){
      // 回显包内 manifest.params：导入后就知道"这包是中文还是英文、分身是谁"
      const P=window._lastImportParams||{}, LANGN={zh:'中文',en:'English'};
      const who=P.avatar_name||[P.person,P.avatar].filter(Boolean).join(' · ');
      const mark=(P.avkey||P.person||P.lang)
        ? ('。🏷 本包标注：'+(LANGN[P.lang]||P.lang||'未标注')+(who?(' · 分身 '+who):''))
        : '';
      $('playImportHint').textContent='已导入 '+n+' 项，前往「⑨ 正式演示」即可播放'+mark;
      await loadSlides(); await loadVersions(); await loadPersons(); renderPlayList(); switchTab('5'); }""")
rep2("""  list.innerHTML='<div class="muted">已载入 '+S.slides.length+' 页讲解，可前往「⑤ 正式演示」播放。</div>';""",
     """  list.innerHTML='<div class="muted">已载入 '+S.slides.length+' 页讲解，可前往「⑨ 正式演示」播放。</div>';""")

# ============ 7) 播放版横幅 / 启动引导 / 注释 ============
rep2('// ⚠ 播放版原先只有底部标签「⑩ 播放文件」一个导入口，顶栏菜单里也没有明显入口，',
     '// ⚠ 播放版原先只有底部标签「⑬ 播放文件」一个导入口，顶栏菜单里也没有明显入口，')
rep2("""      + '<button class="btn ghost" onclick="switchTab(\\'8\\')" style="padding:4px 10px">⑩ 播放文件</button>'""",
     """      + '<button class="btn ghost" onclick="switchTab(\\'8\\')" style="padding:4px 10px">⑬ 播放文件</button>'""")
rep2("""        <div class="gact"><button class="btn" onclick="closeModal();switchTab('8')">前往「⑨ 播放文件」→</button></div>""",
     """        <div class="gact"><button class="btn" onclick="closeModal();switchTab('8')">前往「⑬ 播放文件」→</button></div>""")
rep2('        <b>开始播放</b>到「⑤ 正式演示」点「▶ 开始演示」，数字分身按页播放讲解并自动同步翻页；',
     '        <b>开始播放</b>到「⑨ 正式演示」点「▶ 开始演示」，数字分身按页播放讲解并自动同步翻页；')
rep2("""        <div class="gact"><button class="btn" onclick="closeModal();switchTab('5')">前往「⑤ 正式演示」→</button></div>""",
     """        <div class="gact"><button class="btn" onclick="closeModal();switchTab('5')">前往「⑨ 正式演示」→</button></div>""")
rep2('/* 播放版只剩「⑤ 正式演示 / ⑧ 导入播放文件」，分组标题与分隔线会变成孤儿，直接隐藏 */',
     '/* 播放版只剩「⑨ 正式演示 / ⑬ 播放文件」，分组标题与分隔线会变成孤儿，直接隐藏 */')
rep2('/* ================= ⑨⑩ 加工车间：多源搬运 / 多项产出 / 领域包 ================= */',
     '/* ================= ⑥⑦⑧ 加工车间：多源搬运 / 多项产出 / 领域包 ================= */')
rep2("""    else{ add('video/demo_av_full_<版本>.mp4','整场分身视频（当前暂无可选视频，可先在「⑤ 正式演示」生成）'); }""",
     """    else{ add('video/demo_av_full_<版本>.mp4','整场分身视频（当前暂无可选视频，可先在「⑨ 正式演示」生成）'); }""")

# ============ 7b) 演示向导菜单错位修复（菜单区在变更记录之前，走 R1） ============
rep1("""        mi('① 备料 · 上传 PPT / 讲稿',()=>dlgStage(1)),
        mi('② 出稿 · 生成并打磨 PPT',()=>dlgStage(2)),
        mi('③ 蓄力 · 沉淀知识库',()=>dlgStage(3)),
        mi('④ 造像 · 生成数字分身',()=>dlgStage(4)),
        mi('⑤ 上台 · 正式演示 + 答疑',()=>dlgStage(5)),
        mi('⑥ 收尾 · 总结 / 归档 / 交付',()=>dlgStage(6)),""",
     """        mi('① 备料 · 知识库（公有 / 私有）与沉淀',()=>dlgStage(1)),
        mi('② 出稿 · 素材与文稿智能（生成 / 打磨书面材料）',()=>dlgStage(2)),
        mi('③ 出稿 · 制作 PPT 与 PDF',()=>dlgStage(3)),
        mi('④ 配音 · 生成分身语音讲解（必须在 PPT 之后）',()=>dlgStage(4)),
        mi('⑤ 造像 · 制作数字分身视频',()=>dlgStage(5)),
        mi('⑨ 上台 · 正式演示',()=>dlgStage(6)),
        mi('⑩ 答疑 · 现场问答',()=>dlgStage(7)),
        mi('⑪ 总结 · 沉淀问答',()=>dlgStage(8)),
        mi('⑫ 交付 · 导出相关文档',()=>dlgStage(9)),""")
rep2("""/* 演示向导：编号与第三层标签一致（v2.6.1 按用户流程重排；2026-10-03 再把"生成分身语音讲解"迁移到"制作 PPT"之后）。
   选择分身（第二层作用域栏）→ ① 知识库·沉淀 → ② 素材与文稿智能 → ③ 制作PPT·PDF
   → ④ 生成分身语音讲解 → ⑤ 制作视频 → ⑥ 正式演示 → ⑦ 答疑 → ⑧ 总结 → ⑨ 导出交付 →（沉淀回①，闭环）*/""",
     """/* 演示向导：阶段编号与第三层标签一致（⑥~⑧ 加工车间按需使用，不在主线向导里）；
   2026-10-03 把"生成分身语音讲解"迁移到"制作 PPT"之后，2026-10-15 菜单补全为 9 阶段。
   选择分身（第二层作用域栏）→ ① 知识库·沉淀 → ② 素材与文稿智能 → ③ 制作PPT·PDF
   → ④ 生成分身语音讲解 → ⑤ 制作视频 → ⑨ 正式演示 → ⑩ 答疑 → ⑪ 总结 → ⑫ 导出交付 →（沉淀回①，闭环）*/""")
rep2("""  {t:'⑥ 上台 · 正式演示',tab:5,go:'正式演示',steps:[""",
     """  {t:'⑨ 上台 · 正式演示',tab:5,go:'正式演示',steps:[""")
rep2("""    '在「⑥ 正式演示」点「▶ 开始演示」：分身肖像 + PPT 原稿逐页同步，自动翻页并播放讲解。',""",
     """    '在「⑨ 正式演示」点「▶ 开始演示」：分身肖像 + PPT 原稿逐页同步，自动翻页并播放讲解。',""")
rep2("""  {t:'⑦ 答疑 · 现场问答',tab:2,go:'智能答疑',steps:[""",
     """  {t:'⑩ 答疑 · 现场问答',tab:2,go:'智能答疑',steps:[""")
rep2("""    '在「⑦ 智能答疑」提问，系统按 公有 / 项目 / 分身 三库检索作答，命中某页会自动定位展示。',
    '可勾选「记入答疑记录」，供第 ⑧ 步自动汇总。',""",
     """    '在「⑩ 智能答疑」提问，系统按 公有 / 项目 / 分身 三库检索作答，命中某页会自动定位展示。',
    '可勾选「记入答疑记录」，供第 ⑪ 步自动汇总。',""")
rep2("""  {t:'⑧ 总结 · 沉淀问答',tab:6,go:'答疑总结',steps:[""",
     """  {t:'⑪ 总结 · 沉淀问答',tab:6,go:'答疑总结',steps:[""")
rep2("""    '「⑧ 答疑总结」自动生成《答疑汇总》，可导出 Markdown，也可把高频问答沉淀回知识库。',""",
     """    '「⑪ 答疑总结」自动生成《答疑汇总》，可导出 Markdown，也可把高频问答沉淀回知识库。',""")
rep2("""  {t:'⑨ 交付 · 导出相关文档',tab:7,go:'导出交付',steps:[""",
     """  {t:'⑫ 交付 · 导出相关文档',tab:7,go:'导出交付',steps:[""")
rep2("""    '「⑨ 导出交付」集中导出本项目全部产出：PPT、PDF、书面文档、音频、视频、分身、知识库备份。',""",
     """    '「⑫ 导出交付」集中导出本项目全部产出：PPT、PDF、书面文档、音频、视频、分身、知识库备份。',""")
# FLOW 入口列标签引用校正（步骤序号 ①~⑨ 是主链路自己的编号，不动）
rep2("""   '① 素材与文稿 → 📝 生成文字材料'],""",
     """   '② 素材与文稿智能 → 📝 生成文字材料'],""")
rep2("""   '⑥ 正式演示 → ▶ 开始演示'],""",
     """   '⑨ 正式演示 → ▶ 开始演示'],""")
rep2("""   '⑦ 智能答疑'],""",
     """   '⑩ 智能答疑'],""")
rep2("""   '⑧ 答疑总结'],""",
     """   '⑪ 答疑总结'],""")
rep2("""   '⑨ 导出交付 → 📀 打包为离线便携播放包'],""",
     """   '⑫ 导出交付 → 📀 打包为离线便携播放包'],""")

# ============ 8) 变更记录：v2.9.2 块置顶 + 版本降序重排 ============
BLOCK292 = """  <h4 style="margin:0 0 6px">v2.9.2（10-15 交接线合并：v2.8.x 修复并入 · 播放参数契约 · 统信 deb 三根因修复 · 帮助与版本全面对齐）</h4>
  <ul style="margin:0;padding-left:20px" class="muted">
    <li><b>合并 v2.8.1 交接线全部修复</b>：以 <span class="fk">handover_myme_2026-10-08b</span> 为基底，
        并入安卓 WebView 文件选择器修复（<span class="fk">onShowFileChooser</span> + SAF 目录授权 + MymeNative JS 桥）、
        播放器导入三通路（zip / 目录 / JSON）与播放端四级兜底加载、老壳体检提示；
        当前线的四片段播放、深度加工四项（kb_graph / kb_agent / kb_editor / kb_trace）、
        乔牧七项产出、13 标签编号全部保留，两线合一为 2.9.2。</li>
    <li><b>播放参数契约（lang / person / avatar / avkey）</b>：生成讲解、导出播放文件、导入播放文件三处共用同一组参数名，
        写进包内 <span class="fk">manifest.json</span> 的 <span class="fk">params</span> 与 <span class="fk">README.txt</span>「本播放包标注」抬头；
        播放端导入后自动识别——只有中文配音时英文置灰，本包只有金子时分身里不出现炎冰。
        导出对话框新增「🏷 本包标注」预览框，导入后回显本包标注。</li>
    <li><b>统信 deb 装不上的三个根因都修了</b>：postinst <span class="fk">set -e</span> 遇 chmod 失败即中断
        （改守卫式 fix_mode + 完整性自检，四件套任缺即报「安装不完整」）；
        启动器包内权限 0644 且 CRLF 换行（<span class="fk">#!/bin/sh\\r</span> 直接 execve ENOENT，
        改强制 LF + 包内可执行成员 0755）；.desktop 用通用图标名导致菜单无图标
        （改绝对路径 Exec/Icon + <span class="fk">/usr/bin</span> 转发入口）。
        出包脚本新增 37 项 UOS 专项校验，四件套无 CRLF、postinst 无 set -e 才会放行。</li>
    <li><b>版本标识五处对齐 2.9.2</b>：config.VERSION、安卓 versionCode 9 / versionName 2.9.2、
        MSI 2.9.2、exe 版本资源 2.9.2.0、帮助「当前版本」改为动态取 <span class="fk">MYME_VERSION</span> 常量；
        出包版本号统一由 <span class="fk">_pkg_ver()</span> 从 config 动态读取，杜绝「源码与产物版本不一致」。</li>
    <li><b>帮助「功能介绍」重写为 13 标签</b>：按 准备 / 出稿 / 加工 / 演示 / 交付 五组讲清 ①~⑬ 每个工作台，
        与第三层标签栏一字不差；「版本变更」按版本号降序重排（2.9.1 → 2.9.0 → 2.8.0 → …），新增本版块置顶。</li>
    <li><b>全流程陈旧编号引用清零</b>：演示面板、导出对话框、导入回显、播放版横幅与启动引导中
        残留的旧标签号（⑤ 正式演示 / ④ 制作视频 / ⑧ 便携播放版 / ⑩ 播放文件等）全部校正到 ⑨ / ⑤ / ⑬ 等现行编号。</li>
    <li><b>验证链</b>：播放器 29 项参数断言 + 60 项菜单断言（三通路 + 快捷菜单）全绿；
        deb 37 项解包校验通过；门禁脚本三层（源码 / EXE CArchive / APK-dex+zip+deb）随版本号同步升级。</li>
  </ul>"""

body = RM[RM.find('`') + 1: RM.rfind('`')]
blocks = re.findall(r'[ \t]*<h4 style="margin:[^"]*">(.*?)</h4>\s*<ul style="margin:0;padding-left:20px" class="muted">(.*?)</ul>', body, re.S)
if len(blocks) != 23:
    raise SystemExit('changelog blocks = %d (expect 23)' % len(blocks))

def vkey(title):
    m = re.match(r'v(\d+(?:\.\d+)*)(?:\s*(增补二|增补|本次))?', title)
    nums = [int(x) for x in m.group(1).split('.')]
    while len(nums) < 3: nums.append(0)
    supp = {'增补二': 3, '增补': 2, '本次': 1}.get(m.group(2) or '', 0)
    return tuple(nums + [supp])

blocks.sort(key=lambda b: vkey(b[0]), reverse=True)
print('CHANGELOG ORDER:', ' → '.join(re.sub(r'（.*', '', t) for t, _ in blocks))
out = [BLOCK292]
for i, (title, inner) in enumerate(blocks):
    m = '0 0 6px' if i == 0 else '14px 0 6px'
    out.append('  <h4 style="margin:%s">%s</h4>\n  <ul style="margin:0;padding-left:20px" class="muted">%s</ul>' % (m, title, inner))
RM = 'function dlgChangelog(){openModal(\'版本变更\',`\n' + '\n'.join(out) + '\n`);}'

# ============ 9) 组装 + 残留扫描 ============
src = R1 + RM + R2
stale = ['⑤ 正式演示', '⑥ 智能答疑', '⑦ 答疑总结', '⑧ 导出交付', '⑨ 播放文件', '⑩ 播放文件',
         '④ 制作视频', '⑧ 导入播放文件', '（⑧）', '① 素材准备', '② 制作 PPT', '③ 知识库',
         '④ 数字分身', '⑧ 资源管理', '九个工作台', 'v2.9.1</b>', '⑨ 导出交付', '⑥ 正式演示']
left = [(p, src[:src.find('function dlgChangelog(){')].count(p) + src[src.find('`);}', src.find('function dlgChangelog{')):].count(p))
        for p in stale]
bad = [(p, c) for p, c in left if c]
if bad:
    for p, c in bad: print('STALE LEFT [%s] × %d' % (p, c))
    raise SystemExit('stale refs remain outside changelog')
print('OK  stale-ref scan clean (outside changelog)')

io.open(P, 'w', encoding='utf-8', newline='\n').write(src)
print('WROTE %s  (%d → %d chars)' % (P, len(ORIG), len(src)))
