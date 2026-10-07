---
name: qiaomu-local
description: 多源内容搬运工（国内离线版）——把微信公众号、微博、今日头条、百家号、微信读书、任意网页、B站、播客、水利/古建/感知类政务与厂商站点、PDF/Word/Excel/PPT/EPUB/图片/音频/压缩包/整个目录/关键词，自动识别来源、按来源挑提取策略、清洗后存进本地向量知识库；检索侧再叠知识图谱增强与 ReAct Agent 答疑、分块可编辑与版本回滚、全链路追踪（本地版 Langfuse），并据知识库产出报告、PPT、播客音频、思维导图、测验题、闪卡、数据表。全程本机直连，不依赖 NotebookLM、不翻墙、不出网。
user-invocable: true
homepage: https://github.com/joeseesun/qiaomu-anything-to-notebooklm
---

# 多源内容 → 本地向量知识库 → 多项产出

原项目 `qiaomu-anything-to-notebooklm` 是「多源内容 → NotebookLM」。国内环境里
NotebookLM CLI、微信 read MCP、Get 笔记 API、r.jina.ai 代理全不通，
这里把同一套骨架换成本机自己的链路：抓取走直连、转写走本地 ASR、出口走本地知识库与本地模型。

## 它做了三件事

1. **搬运**：一句话丢个链接/文件/目录/关键词，自动识别来源类型。
2. **清洗**：按来源用不同提取策略，去掉导航、页脚、脚本、评论区，留下正文。
3. **加工**：洗干净的正文进本地向量库；之后任何问答、PPT、播客、脑图都从这块料里长出来。

## 支持的内容源

| 输入 | 识别为 | 提取策略 |
|------|--------|---------|
| `mp.weixin.qq.com/s/...` | 微信文章 | 直连抓取，`#js_content` 取正文，去导航页脚 |
| 任意 `http(s)://` 网页 | 网页 | 先直连；抓不到自动升级「Googlebot UA → Bingbot UA → AMP 副本」 |
| `bilibili.com/video/...` | B站视频 | 先取官方字幕接口，取不到才下音频走本地 ASR |
| `xiaoyuzhoufm.com` / `ximalaya.com` | 播客 | 挖音频直链 + 本地 ASR 转写 |
| `weibo.com` | 微博 | 走 `m.weibo.cn/statuses/show` 详情接口（整页是登录墙） |
| `toutiao.com` / `baijiahao.baidu.com` | 资讯 | 直连 + 正文选择器，去侧栏推荐 |
| `weread.qq.com` | 微信读书 | 直连 + 章节正文选择器 |
| `*.gov.cn`（水利部 / 生态环境部 / 各级水利厅） | 政务站 | 自动套用 `#content / .view / TRS_Editor` 口径 |
| `ihchina.cn` / `hikvision.com` / `dahuatech.com` / `huaweicloud.com` | 古建与感知源 | 直连，取文档正文 |
| ~~`youtube.com` / `x.com`~~ | **已删除** | 国内不可达：提示改投 B站（视频）/ 微博（短内容）/ 复制粘贴（客户端内内容） |
| `.pdf/.doc/.docx/.xls/.xlsx/.csv/.ppt/.pptx/.md/.txt/.json/.html/.epub` | 文档 | 复用既有 18 种格式抽取器 |
| `.jpg/.png/.webp` | 图片 | OCR（装了 tesseract 才跑） |
| `.mp3/.wav/.m4a/.mp4` | 音频 | 本地 ASR 转写 |
| `.zip` | 压缩包 | 解压后批量抽取 |
| 目录 | 目录 | 递归扫描全部可解析文件 |
| 中文短词 | 关键词 | 不联网，直接查本机知识库 |

## v2.9 补上的三件事（都是知识库侧的，搬运工本身没变）

参考 Tencent/WeKnora 的三层能力做本地化落地：

1. **知识图谱增强检索**（`kb_graph`）
   从知识库抽实体（形态正则 + 领域包术语种子），按「同处一块内容」共现连边；
   问答命中一块时顺着边外扩几条邻居，解决向量只命中概念链条一半就答偏的问题。
   `kb_graph.rebuild()` 秒级重建，`neighbors(term)` 查某个概念的邻居，可导出 Mermaid。
2. **ReAct Agent 答疑**（`kb_agent`）
   思考 → 行动 → 观察循环：先 `kb_search` 本机库，查不到才 `web_search`（必应 → 百度）、
   `fetch` 直连抓页，最后 `finish` 收尾。每步留轨迹；开 `approve=True` 时联网类动作会
   **停下来等人确认**（`confirm(aid)` 放行 / 拒绝后改回本地库答）。
   领域问答走 `domain_water.qa("water", "清淤工期多久")` —— 三端四平台口径自动进提示词。
3. **分块可运营 + 链路追踪**（`kb_editor` / `kb_trace`）
   自动切块的噪声（术语被切成两半、正文混进页脚）人一眼能看出来：改完自动重算向量、
   旧版本自动进快照、随时 `diff` / `rollback`；每次问答的检索路径、耗时、token 估算记在
   `trace/trace.jsonl`，出问题能分清是检索不行还是模型不行。

## 触发方式

- `把这篇微信文章搬进知识库 <URL>`
- `这个网页生成一份报告 <URL>`
- `这期播客转成文字 <小宇宙链接>`
- `把 D:/资料 里所有 PDF 扫进知识库`
- `清淤工期与预算` （关键词 → 查本地库）
- `深度解读这期播客 <URL>` → 三轮递进（4+5+3 = 12 问）
- `用知识库生成 PPT：渠道清淤攻坚`
- `把领域口径（三端四平台）并入知识库`

## 工作流

### Step 1 识别来源

```python
import qiaomu_local
typ, site = qiaomu_local.detect_input("https://mp.weixin.qq.com/s/xxx")
# ('weixin', 'mp.weixin.qq.com')
```

### Step 2 抓 + 洗 + 入库

```python
r = qiaomu_local.run("https://mp.weixin.qq.com/s/xxx", kb="myme_kb", category="公众号")
# {ok, type, title, chars, ingest: {added, total}, secs}
```

批量（一行一条，行尾 `#分类` 指定分类，单条失败不中断）：

```python
qiaomu_local.run(items)  # 或走 /api/qm/batch
```

### Step 3 三轮递进深度解读（可选）

```python
r = qiaomu_local.deep_analyze("https://...", kb="myme_kb")
# 12 问 → {questions, answers, total_questions, answered}
```
设计原则（沿用原项目）：
- 每问都带「只依据资料内容回答，不要联网」→ 防幻觉
- 轮次递进：概览建框架 → 挖细节与矛盾 → 综合反刍出行动
- 词按需分叉：书/文档讲论证逻辑，视频讲叙事结构，文章讲观点与启发

### Step 4 产出（七种）

```python
import kb_export
kb_export.export("report",    topic="渠道清淤工程")     # Markdown + docx
kb_export.export("pptx",      topic="渠道清淤攻坚")     # 走既有 make_ppt/pptx_write
kb_export.export("podcast",   topic="渠道清淤工程")     # 讲稿 + TTS 音频
kb_export.export("mindmap",   topic="渠道清淤工程")     # Mermaid + 文本大纲
kb_export.export("quiz",      topic="渠道清淤工程")     # 8 道选择题
kb_export.export("flashcards",topic="渠道清淤工程")     # 15 张记忆卡
kb_export.export("table",     topic="渠道清淤工程")     # Markdown 表 + CSV
```
产物一律落 `%LOCALAPPDATA%\myme\exports`。

### Step 5 领域口径（水利古建 · 感知应用 · 三端四平台）

```python
import domain_water
domain_water.list_profiles()          # water / heritage / sensor
domain_water.outline("water", "渠道清淤攻坚", 8)   # 纯骨架，秒出，不调模型
domain_water.kb_entry("water")        # 领域说明文本 → 可直接入库
domain_water.update("water", {...})   # 口径可改，落盘 domains/water.json
```

## 调用链（Python 侧）

```
qiaomu_local.detect_input  ──→  fetch_weixin / fetch_url / fetch_bilibili
                                 / fetch_podcast / fetch_epub / fetch_zip
                                 / fetch_audio_file / fetch_search
        ↓ 正文
qiaomu_local.ingest_text   ──→  kb_chunking 分块（面包屑算进 embedding 不算进正文）
                                 → ollama_embed(bge-m3) → myme_kb.json
        ↓
kb_export.export / kb_qa.answer / make_ppt.gen_outline / kb_wiki.generate_wiki
```

## 硬约束

- **不出网**：不做任何境外请求；YouTube、X/Twitter 直接告知不可达，不用代理假装成功。
- **不依赖 NotebookLM / 飞书**：出口一律是本地文件或本机模型。
- **依据可回溯**：进库时正文带头（来源 + 时间），出稿时逐条标出处。
- **搜不到就说搜不到**：知识库没料时，导出返回「先去搬运台把资料搬进来」，不编内容。

## 已知限制（别把它说成万能）

- 小宇宙/喜马拉雅有防盗链，挖不到音频时会明确提示「手动把 mp3 放进来」。
- 长音频本地转写很慢（几十分钟音频按小时计），先确认要不要转。
- 播客/网页抓取偶发站点改版，解析失败会返回可执行的下一步，不是静默失败。
- 深度解读 12 问约十几分钟且吃单模型，跑的时候别同时问问题。

## 与原项目的差异

| 原项目 | 本实现 |
|--------|--------|
| `notebooklm create/source/add` | `knowledge_base.ingest_text` → 本地向量库 |
| 微信 read MCP | `fetch_weixin` 直连 + Referer 处理 |
| Get 笔记 API（需海外 Key） | 本地 ASR（`asr_listen`）+ 平台字幕接口 |
| r.jina.ai → defuddle → agent-fetch | 国内直连 + Googlebot/Bingbot UA + AMP |
| `notebooklm generate audio/slide-deck/…` | `kb_export.export(...)` 走本机管道 |
| 飞书 `lark-cli docs +create` | 本地 Markdown / docx / pptx 落盘 |
