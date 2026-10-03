# myme · PPT制作及分身演示

> **当前版本 v2.6.3**（2026-10-03）。基于交接包 `handover_myme_2026-10-03`（v2.6.2）同步开发：
> 🔗 源码：<https://github.com/g101400/myme4060> ｜ 发行版：<https://github.com/g101400/myme4060/releases/tag/v2.6.3>（Win / 安卓 APK / 统信 deb 三平台 8 件）
> ① 以交接源为唯一功能基底同步到本目录（含流程重构 ①~⑩、Sonic 双引擎、知识库三层、三端便携）；
> ② 修复 **阻断级环境错配**：源码原写死 D:/ComfyUI_Mie_2026_V8.0，本机 ComfyUI 实际在 **H 盘** → TTS / Sonic / 数字人服务全部拉不起来，已改为「环境变量 → 设置登记 → 全盘扫描」三级探测（`config.py` / `tts_worker.py`），命中须含 `custom_nodes` 才算 ComfyUI 真身；现已实跑验证命中 H 盘。
> ③ 修复出包路径： `_build_sfx.py` 原写死旧工作区，改为相对脚本目录；
> ④ 源码态已实跑验证（`/api/meta` = 2.6.3、`/api/projects`、`/api/scope` 全绿）；三平台（Win / 安卓 APK / 统信 deb）已重出。
> 公开源码仓：<https://github.com/g101400/myme4060>

# myme · 本地数字分身 —— 用「炎冰」的声音讲 PPT 并自动答疑

> 一个**完全本地运行**的数字分身 / 轻量 APP：上传 PPT → 用你本人的声音逐页讲解 → 讲完别人问 PPT 内容，自动用你的声音回答 → 内置个人知识库支撑问答 → 中文 / 英文自由切换。
>
> 文件夹 `myme/`，零云服务依赖（模型与声纹全部在本机）。

---

## 一、需求细化与可行性分析（在你给的方案上优化）

你的原始需求有 4 条 + 2 条路线。结合**本机真实资产**重新评估，结论：路线 B（本地自建）在本机几乎是"零额外成本"落地，因为关键能力都已就绪。

| # | 需求 | 可行性 | 本机落地方案（优化点） |
|---|------|--------|----------------------|
| 1 | 用**我的声音**讲解 PPT | **高** | 复用已验证管线：本地 **Qwen3-TTS-1.7B 语音克隆** + 你的声纹 `yanbing-sample-01.wav`（约 10 分钟录音）。**同一引擎原生支持中/英等 10 种语言**，无需两套 TTS。 |
| 2 | 讲完自动回答 PPT 问题 | **高** | **RAG（混合检索）**：纯标准库 JSON + Ollama `bge-m3` 向量检索 **+ 纯 Python BM25** 加权融合，再按向量阈值过滤；`qwen3.5-9b` 大模型作答。检索上下文注入提示词，超范围明确拒答。 |
| 3 | 建立个人知识库支撑问答 | **高** | PPT 文本/备注 → 切片(chunk=500,overlap=100) → 向量化入库；**并支持追加笔记/Word（.txt/.md/.docx，纯标准库 zip+xml 解析），按文件 sha256 指纹增量去重**。PPT 与笔记共享同一知识库。 |
| 4 | 中英文语音切换 | **中→高** | TTS 语言标记 `Chinese/English` 一键切换；ASR 用 whisper 中英混合识别；LLM 按提问语种自动回灌同语种答案。 |

### 对你原方案的优化点

1. **路线选择**：原路线 A（GKK）适合"1–2 天验证"；但本机已有 Qwen3-TTS 声纹克隆 + RTX 4060 8GB + Ollama，**路线 B 的"硬骨头"（声音克隆、本地大模型）已经啃完了**，直接落地反而更可控、数据不出机、永久免费。故本方案选 **路线 B 并实例化**。
2. **TTS 不再"需录制 10–30 分钟"**：你**早已录好**声纹（`yanbing-sample-01.wav`），开箱即用。
3. **数字人不再强依赖 GPU 唇形**：核心交付是"声纹驱动的数字分身"（声音+知识+双语）；实时唇形（LivePortrait/Wav2Lip）在本机 RTX 4060 8GB 可跑，但列为**可选增强**，避免把 MVP 拖进 2–4 周调试。
4. **中英文不必"分语言引擎"**：Qwen3-TTS 单模型多语，GPT-SoVITS 那套"中英各一套"的麻烦被消除。

---

## 二、本地落地技术架构

```
                ┌─────────────────────────────────────────────┐
  上传 PPT/PDF   │                 myme (本地)                  │
  ───────────►  │                                               │
                │  ① pptx_parse.py 解析 → 结构化讲稿原料         │
                │        │                                      │
                │        ├─► ② knowledge_base.py                 │
                │        │      JSON + Ollama bge-m3 向量化(余弦)  │
                │        │                                      │
                │        └─► ③ qa_brain.py (Ollama qwen3.5-9b)   │
                │               ├ 生成口语讲稿(供讲解)           │
                │               └ RAG 问答(供答疑)              │
                │                        │                      │
                │  ④ narrate.py ──► tts_service.py(常驻,热)       │
                │       (系统python)     (嵌式python + Qwen3-TTS) │
                │          └─回退─► tts_worker.py(冷,按需)         │
                │                        + 炎冰声纹 ──► wav      │
                │                                               │
                │  ⑤ asr_listen.py (可选: whisper 语音提问)       │
                │  ⑥ app.py (stdlib http.server UI) ⑦ avatar.py  │
                └─────────────────────────────────────────────┘
```

### 关键选型（均已在你机器上验证可用）

| 模块 | 选型 | 说明 |
|------|------|------|
| PPT 解析 | **纯标准库** `zipfile`+`xml.etree`（见 `pptx_parse.py`） | 抽标题/正文/备注，无需 python-pptx |
| 知识库 | **纯标准库**：JSON 持久化 + Ollama `bge-m3` 嵌入 + 余弦检索 **+ 纯 Python BM25 混合检索** | 零依赖，无需 ChromaDB；支持 .txt/.md/.docx 增量入库与文件指纹去重 |
| 嵌入模型 | Ollama `bge-m3` | 中文友好、多语言，1.2GB |
| 大模型（问答/讲稿） | Ollama `qwen3.5:9b`（可换 27b/35b） | 中文强、本地、免费；**已关 thinking 直出文本** |
| **声音克隆 TTS** | **Qwen3-TTS-1.7B**（ComfyUI 嵌式 python） | **复用你的声纹**，支持 10 语言；已通过隔离的 `transformers==4.57.3`（`ts_deps`）解决本机 5.5 的 KV-cache 不兼容，输出 24kHz 单声道 wav |
| 语音识别（可选） | `whisper`（嵌式 python） | 中英混合识别，需额外装 |
| UI | **纯标准库** `http.server` + 内置 HTML/JS | 本地 Web，零部署，无需 Gradio |
| 数字人（可选） | ComfyUI 文生图 + ffmpeg 合成 | RTX 4060 8GB 可上 LivePortrait |

> ⚠️ **双 python 架构（重要）**：TTS/whisper 需要 torch，跑在 **ComfyUI 内置 python**（H:/ComfyUI_Mie_2026_V8.0/python_embeded/python.exe，已带 torch 2.10+cu130）；主逻辑跑在系统 `python3`。

---

## 三、智能体任务分配方案（4 个智能体协同）

| 智能体 | 输入 | 任务 | 输出 |
|--------|------|------|------|
| **1 PPT解析与讲稿生成器** (`pptx_parse`+`qa_brain.generate_narration`) | PPT/PDF + 语言 | 逐页抽文本/备注/图；LLM 生成口语讲稿(≈200字/页) | 结构化讲稿 JSON |
| **2 知识库管理员** (`knowledge_base`+`doc_ingest`) | 讲稿 + 补充文档(.txt/.md/.docx) | 清洗(去噪/脱敏)→切片(500/100)→bge-m3 向量化；**混合检索**(向量+BM25, 阈值过滤)；文件指纹增量去重 | 检索片段 + 来源 |
| **3 问答大脑** (`qa_brain.answer_question`) | 问题 + 上下文 | 判相关性；基于上下文作答；中英双语；超范围拒答 | 文本回答 |
| **4 语音与形象驱动** (`tts_worker`+`narrate`+`asr_listen`+`avatar`) | 文本 + 语言 | 语言判定；Qwen3-TTS 克隆声合成；可选唇形 | 音频 / 音视频 |

> 编排层 = `app.py`（纯标准库 `http.server` Web UI）或你的主控智能体；上述 4 个智能体也可直接拆给不同 Agent 并行开发——接口即各 `.py` 的导出函数。

---

## 四、完整提示词（可直接复制分发）

### 4.1 问答大脑 · 系统提示词
见 `prompts/system_prompt.py` 的 `QA_SYSTEM_PROMPT`（已用你的人设 `PERSONA` 注入）。核心要点：
- 知识边界：只答知识库内容，不足时明确"超出范围"
- 语言适配：中问中答、英问英答
- 回答模板：【核心答案】→【详细说明】→【相关参考】
- 拒绝闲聊 + 多轮记忆 + 忠于原文

### 4.2 讲稿生成 · 系统提示词
见 `NARRATE_SYSTEM_PROMPT`：把单页内容改成"炎冰本人会说的口语"，不用"如图所示"等屏依赖词，纯文本输出。

### 4.3 给其它智能体的分工说明
见 `AGENT_SPEC`（含 4 智能体职责/输入输出）。

---

## 五、运行方式

```bash
cd D:/Users/WorkBuddy/Claw/myme
# 1) 零依赖：核心管线纯标准库，系统 python 无需 pip 安装（ASR 可选）
# 2) 确保 Ollama 已启动且拉好模型
ollama list               # 确认 bge-m3 与 qwen3.5:9b 在
# 3) 启动数字分身（纯标准库 Web，无需 Gradio）
python app.py             # 打开 http://127.0.0.1:7860
                        # 启动时自动后台拉起常驻 TTS 服务（首次加载模型约 1-2 分钟），之后合成秒级
```

### 常驻 TTS 服务（消除冷加载）
`app.py` 启动时会自动检测并后台拉起 TTS 服务；也可单独管理：
```bash
# 单独启动常驻 TTS 服务（嵌式 python，模型常驻内存）
"H:/ComfyUI_Mie_2026_V8.0/python_embeded/python.exe" tts_service.py --eager
# 端点：GET /ready（健康检查）、POST /tts {text,lang,out,seed}
curl http://127.0.0.1:8777/ready          # {"ready":true,...}
# 关闭：结束该进程即可；app.py 下次启动会重新拉起
```
> 若不想用常驻服务，把 `config.USE_TTS_SERVICE=False`，则恢复为每次冷启动子进程（首次需数分钟）。
界面六个标签页 + 流程向导：顶部菜单栏新增 **「🎬 演示流程」**（五阶段向导，一键跳转到对应面板）。六个标签页：① 准备工作（上传 PPT·选语言·生成炎冰语音讲解·分段试听调整）② 智能答疑（文字/语音→炎冰声音回答，可勾选沉淀记录）③ 数字分身视频（炎冰声纹+LivePortrait 唇形）④ 知识库（上传笔记/Word/PPT 增量入库、查看来源）⑤ 正式演示（点播放→分身讲+PPT同步翻页，领导打断→答疑→**从此页续播并记住位置**）⑥ 答疑总结（生成《答疑汇总》文档、导出、征询入库）。

命令行直跑：
```bash
python narrate.py 你的.pptx 中文      # 解析+入库+逐页炎冰语音+完整讲解音频
python validate_e2e.py               # 端到端小样本验证（生成示例PPT→问答→中英TTS）
python doc_ingest.py 笔记.docx        # 抽文本/清洗/脱敏预览（不入库）
python -c "import knowledge_base as k; print(k.ingest_document('笔记.docx'))"  # 单文件增量入库
python asr_listen.py --audio q.wav   # 语音问题转写（需在嵌式python: -m pip install openai-whisper）
python avatar.py --image avatar_out/portrait.png --audio narrations/answer_tmp.wav --out avatar_out/clip.mp4   # 数字人唇形驱动
python avatar.py --prepare-only --audio in.wav --out out_16k.wav   # 仅做音频预处理(16k/-1dB/WAV)
```

---

## 六、已验证 / 已知限制 / 增强路线

**已具备（本机资产，直接复用）**
- 炎冰声纹 `voice/yanbing-sample-01.wav`、Qwen3-TTS-1.7B、ComfyUI 嵌式 python、Ollama 多模型（bge-m3 / qwen3.5:9b / qwen3.8:27b）、ffmpeg、RTX 4060 8GB。
- 核心管线已改为**纯标准库零依赖**（解析=zipfile+xml；知识库=JSON+余弦；UI=http.server），系统 python 无需装包即可跑。

**已知限制 / 环境说明**
- **TTS 已打通（关键突破）**：本机嵌式 python 自带 transformers **5.5.0**，与 Qwen3-TTS 的 `qwen_tts` 包不兼容——自回归生成时 KV-cache 的 key/value 长度不一致（报错 `size a (21) must match b (11)`）且因果掩码形状错位。已用**非破坏式**方案解决：在 `myme/ts_deps/` 隔离安装 `qwen_tts` 官方要求的 `transformers==4.57.3`，由 `tts_worker.py` 启动时前置到 `sys.path`，**完全不动 ComfyUI 自带的 5.5.0**（ComfyUI 其它节点如 nunchaku 也兼容 4.57.3）。另在 `tts_worker.py` 内对 `qwen_tts` 的 `eager_attention_forward` 做了运行时掩码对齐补丁作兜底。验证：`tts_zh_fix.wav` / `tts_en_fix.wav` 均已成功出声。
- **常驻 TTS 服务消除冷加载** ✅：每次冷启动子进程加载 1.7B 模型要数分钟；现已新增 `tts_service.py`——用嵌式 python 把 Qwen3-TTS 模型**常驻一个进程**，`narrate.py`/`app.py` 通过 HTTP 热合成（首条解码后约 5–11s，后续持续秒级）。`app.py` 启动时会自动后台拉起该服务（已运行则跳过），模型只加载一次。服务不可用时 `narrate.synth_one` 自动回退到冷启动子进程（`tts_worker.py`），保持兼容。端口 `config.TTS_SERVICE_PORT`（默认 8777），可用 `MYME_TTS_URL` 覆盖。
- TTS 单次合成长文本按 max_new_tokens 截断。
- ASR（whisper）需在嵌式 python 额外 `pip install openai-whisper`，默认以文字提问为主。
- 数字人实时唇形（LivePortrait）未默认开启，当前为"声纹驱动的数字分身"。

**本次已完成的增强（2026-09-24）**
1. ✅ **知识库扩充：笔记/Word 入库** —— 新增 `doc_ingest.py`：纯标准库解析 `.txt/.md/.docx`（docx 用 zip+xml，同 PPT 思路），清洗（去 HTML/页眉页脚/水印噪音）+ 脱敏（身份证/手机号/邮箱正则替换）；`knowledge_base.ingest_document` 按文件 **sha256 指纹增量去重**（内容未变跳过向量化）。PPT 与笔记共享同一知识库（`ingest_pptx` 也用指纹去重）。
2. ✅ **混合检索（向量 + BM25）** —— `knowledge_base.hybrid_query`：纯 Python BM25 关键词检索（专业术语/编号精准命中）与 bge-m3 向量检索加权融合（`alpha=0.6` 向量 / `0.4` BM25，可在 `config.RETRIEVE_ALPHA` 调），再按向量余弦阈值（`config.RETRIEVE_THRESHOLD`）过滤抑制幻觉。问答链路已切换为混合检索。
3. ✅ **PWA 外壳 + 知识库页** —— `app.py` 新增④知识库管理页（上传 .txt/.md/.docx/.pptx 增量入库、查看来源列表），并启用 PWA：`manifest.json`（`display:standalone` 沉浸式全屏）+ `sw.js` 离线壳 + 纯标准库生成的图标。`/api/ingest`、`/api/sources` 端点已加。浏览器"添加到主屏"即可当原生 App 用。
4. ✅ **数字人音频驱动（LivePortrait 接入点）** —— `avatar.py` 实现音频驱动唇形：先 `prepare_audio` 用 ffmpeg 把 TTS 音频**重采样 16kHz + 峰值 -1dB 标准化 + WAV 无损**（严禁 MP3 二次压缩），再以 688×368 调 LivePortrait 推理；未安装时优雅降级并给出安装/调用指引。
5. ✅ **常驻 TTS 服务（消除冷加载）** —— 新增 `tts_service.py`：嵌式 python 常驻进程，模型仅加载一次（`ThreadingHTTPServer` + 锁保证串行生成），暴露 `GET /ready`、`POST /tts`。`narrate.synth_one` 优先走热服务、失败回退冷子进程；`app.py` 启动时自动预热该服务。详见下方「常驻 TTS 服务」。

**可选增强（剩余 / 按需）**
1. 实时唇形真正跑通：安装 LivePortrait（`git clone https://github.com/KwaiVGI/LivePortrait.git`）后，`avatar.py` 即可一键生成口型同步视频；或在 ComfyUI 用 kijai 的 LivePortrait 节点联动。
2. 微信小程序外壳：PWA 已满足"添加到桌面/离线"需求；若要进微信生态，可把静态资源+基础模型放小程序本地包，无需服务器域名。
3. 批量课件：遍历 `sample_ppt/` 目录一键生成全套讲解音视频。
4. 检索进阶：按需接入 BGE-Reranker 重排、父子分块（章节概要+段落）、Qdrant 持久向量库（超个人量级时）。

**本次已完成的增强（2026-09-25 · v2.1「🎬 演示流程」）**
1. ✅ **五阶段流程向导 + 正式演示面板（⑤）** —— 顶部新增「🎬 演示流程」顶级菜单，含五阶段子菜单（基础配置 / 准备工作 / 正式演示 / 答疑模式 / 梳理总结）与流程总览，弹窗列出本阶段步骤并可一键跳转到对应面板。新增 **⑤ 正式演示** 面板：`demoStart` 开始演示（按页播放逐页炎冰语音讲解 + PPT 同步翻页，播完自动下一页）、`demoPause`（⏸ 领导提问，进入答疑）、`demoResume`（**从当前页继续演示**）、`demoPrev/demoNext`（手动翻页）、`demoStop`（结束并**记忆断点页码**到 `settings.json` 的 `presPos`）；下次 `demoStart` 从断点页续播。分身视频与 PPT 舞台同屏，领导打断答疑后无缝回到原位。
2. ✅ **答疑记录持久化 + 梳理总结面板（⑥）** —— 新增 `qa_log.json` 沉淀演示/答疑期间的问答（含 `mode` 区分"答疑/演示中断"）。**⑥ 答疑总结** 面板：`genSummary` 调本地大模型生成《答疑汇总》Markdown、`exportSummary` 导出到磁盘、`storeSummary`（征询确认后）存入知识库（`category="答疑汇总"`）、`clearLog` 清空。② 智能答疑面板新增 `logQA` 复选框（默认勾选）自动沉淀记录。
3. ✅ **逐页讲解音频持久化** —— 异步讲解 job 完成后，每页 audio/narration 挂回全局 `SLIDES` 并写入 `slides.json`，刷新/重启不丢；正式演示按页播放即从该持久数据重建，无需重新合成。
4. ✅ **后端接口新增/增强** —— `GET/POST /api/qa_log`（add/list/clear/summary/export/store）、`DEFAULT_SETTINGS` 增 `presPos`、PWA Service Worker 缓存升 `myme-v5`（改版必升，否则浏览器加载旧页）。
5. ✅ **LibreOffice 渲染环境修复** —— `slide_render.py` 重写 `_clean_env()`：剥离 WorkBuddy 注入的 `PYTHONPATH` 等变量对 soffice 自带 Python 的污染，并把 `PYTHONHOME/PYTHONPATH` 指回 soffice 的 `python-core-*/lib`，修复 soffice bootstrap 失败。
6. ✅ **端到端验证脚本** —— `test_out/e2e_test.py` 驱动**真实 Web 接口**跑通全流程（导入PPT+生成讲解→轮询进度→校验逐页音频→自测答疑→记录沉淀→汇总→导出→入库），输出"全流程跑通成功"，非仅单元测试。

> ⚠️ **本环境已知限制（非致命）**：当前 WorkBuddy 调试会话中，本机 LibreOffice 的 **PPTX→PDF 渲染失败**（仅 TXT→PDF 正常，重置 LO profile 无效），疑为 PPTX 导入过滤器异常。故 PPT 舞台像素级原稿页图降级为 **HTML 版式**（`fallbackSlideHtml`），功能不受影响；换干净环境或修复 LibreOffice 后应恢复像素页图。

---

## 七、增强模块使用说明（本次新增）

### 7.1 把笔记/Word 喂进知识库
```bash
# 方式A：Web（④知识库页）拖文件即可，按指纹增量去重
# 方式B：命令行
python -c "import knowledge_base as k; print(k.ingest_document('你的笔记.docx', category='水利规范'))"
# 批量
python -c "import knowledge_base as k; print(k.ingest_paths(['a.md','b.docx']))"
```
入库后，②提问答疑会自动从"PPT + 笔记"混合检索作答。已收录来源用 `knowledge_base.list_sources()` 查。

### 7.2 混合检索调参
`config.py` 中：
- `RETRIEVE_ALPHA`（默认 0.6）：向量权重；技术文档可降到 0.4 提高 BM25 关键词权重。
- `RETRIEVE_THRESHOLD`（默认 0.0）：向量相似度下限，低于此值直接丢弃，避免"胡编"。

### 7.3 PWA 部署注意
- 本地 `http://127.0.0.1:7860` 可直接"添加到主屏"（Chrome/Edge 菜单）。
- **公网部署必须 HTTPS**（PWA 安全上下文要求，localhost 例外）；可经反向代理（Nginx/Caddy）挂证书。

### 7.4 数字人（LivePortrait）前置要求
- 形象素材：正面平视、无强阴影高清图，人脸占比足够大（放入 `avatar_out/portrait.png` 或 `--image` 指定）。
- 音频：由 TTS 产出 wav，`avatar.py` 自动预处理为 16kHz/-1dB/WAV；**切勿用 MP3**，否则口型驱动精度下降。
- 分辨率：推荐 688×368（显存/口型稳定平衡点）。

---

## 八、打包分发（iOS PWA / Windows EXE / MSI）

把 myme 数字分身做成可分发的安装包，构建脚本均已落在 `myme/`：

- **iOS / 任意主机 本地部署 PWA zip**：`python build_ios_zip.py` → `dist_pkg/myme-ios-pwa.zip`。解压后 `python myme_launcher.py` 起服务；iPhone 用 Safari 打开主机局域网地址 → 分享 →「添加到主屏幕」，即得到本地 PWA App（数据不出机）。
- **Windows 便携 EXE**：`python -m PyInstaller --onefile --windowed --name myme --add-data "ui/index.html;ui" --add-data "voice/yanbing-sample-01.wav;voice" --add-data "icon-192.png;." --add-data "icon-512.png;." --hidden-import pypdf --hidden-import doc_ingest --hidden-import prompts.system_prompt myme_launcher.py` → `dist/myme.exe`。双击即启动本地服务并打开浏览器，免安装。
- **Windows 原生 MSI**：`wix build myme.wxs -o dist/myme-win.msi`（需先 `dotnet tool install --global wix`）→ `dist/myme-win.msi`。安装到 `Program Files\myme`，并创建开始菜单 + 桌面快捷方式。

> **打包范围与已知约束**：EXE/MSI 只打包"Web 服务 + 编排层"（纯标准库，无 pip 依赖，故体积小）。**炎冰声音（TTS）与数字人视频依赖本机另行的 Ollama + 常驻 TTS 服务（ComfyUI 嵌式 python）**；未部署时 Web / 知识库 / 文字答疑仍可用，相关按钮优雅降级。打包运行后，运行时数据（知识库 / 讲解 / 设置 / 答疑记录）重定向到 `%LOCALAPPDATA%/myme`，保证持久化且兼容 Program Files 只读（`config.py` 中 `frozen` 分支）。`myme_launcher.py` 同时是开发启动器与打包入口。

