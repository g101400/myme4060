# 炎冰数字分身 · 本地部署 PWA（iOS / 任意主机）

一个**完全本地运行**的数字分身：用「炎冰」本人的声音讲 PPT，讲完自动答疑，数据不出机。
本压缩包即「可部署的 PWA 网页」——把它跑在一台机器上，手机/平板用浏览器访问并"添加到主屏幕"，即可像原生 App 一样使用。

## 一、在主机上启动（一次性）
解压本包到任意目录，确保主机已装 **Python 3.10+**，然后：

```bash
cd myme-ios-pwa
python myme_launcher.py        # 启动本地服务并自动打开浏览器
```

默认地址：`http://127.0.0.1:7860`
服务常驻，Ctrl+C 退出。

## 二、iPhone / iPad 上使用（PWA 安装）
1. 让手机与主机在**同一局域网**（连同一个 Wi-Fi）。
2. 在 iPhone 的 **Safari** 打开主机的局域网地址，例如：
   `http://192.168.1.XX:7860` （把 `192.168.1.XX` 换成主机的局域网 IP； Windows 可用 `ipconfig`、macOS/Linux 用 `ifconfig` 查看）。
3. 点 Safari 底部**分享按钮** → **"添加到主屏幕"** → 取名"炎冰分身" → 完成。
4. 以后从桌面图标打开，即为全屏、离线壳的本地 PWA App（manifest + service worker 已内置）。

> 安卓 / 桌面 Chrome / Edge 同样支持"安装"为 PWA。

## 三、让分身"开口说话" + 生成数字人视频（可选 AI 栈）
本包只含 Web / 知识库检索 / 文字答疑。要合成**炎冰声音**与**口型视频**，需在本机另行部署：
- **Ollama**：安装后拉取模型 `ollama pull bge-m3` 与 `ollama pull qwen3.5:9b`（嵌入 + 问答大模型）。
- **常驻 TTS 服务**（炎冰声纹，Qwen3-TTS，运行在 ComfyUI 嵌式 python）：
  在 ComfyUI 环境执行 `python tts_service.py --eager`（端口 8777）。
- 数字人视频还需 **LivePortrait**（torch 环境）。未部署时，Web 内相关按钮会优雅降级（仅出文字/音频）。

> 本机若已按原 myme 工程配置好 Ollama + TTS 服务，直接运行 `python myme_launcher.py` 即全自动接管。

## 四、目录说明
- `app.py` / `config.py` / `pptx_parse.py` / `knowledge_base.py` / `qa_brain.py` / `narrate.py` / `avatar.py` / `doc_parse.py` / `persons.py`：纯标准库后端（无需 pip 安装）。
- `ui/index.html`：前端（含「🎬 演示流程」五阶段向导、正式演示、答疑总结）。
- `voice/yanbing-sample-01.wav`：炎冰声纹样本（供 TTS 服务使用）。
- `prompts/`：问答 / 讲稿提示词。
- `pwa/`：静态 PWA 资源参考（manifest / service worker / 图标），由后端自动服务，一般无需手动处理。
- `myme_launcher.py`：启动器（开发 / 部署通用，自动开浏览器）。

知识库、讲解音频、设置等运行时数据默认写在解压目录内（开发模式），便于携带。
