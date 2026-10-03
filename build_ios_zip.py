# -*- coding: utf-8 -*-
"""
打包「iOS / 任意主机 本地部署 PWA」zip。
========================================
把 myme 数字分身 Web 应用（纯标准库后端 + 前端）整理成可部署压缩包：
- 在任意一台机器（Windows / macOS / Linux）解压后 `python myme_launcher.py` 即可起服务；
- iPhone 用 Safari 打开该机器的局域网地址 → 分享 →「添加到主屏幕」，即得到一个本地 PWA App；
- 数据全程本地，不出机。

说明：语音（炎冰声音）与数字人视频依赖本机另行的 Ollama + 常驻 TTS 服务（ComfyUI 嵌式 python）。
仅跑本 zip 也能用 Web / 知识库检索 / 文字答疑；要"开口说话"需按 README 部署 AI 栈。
"""
import os
import shutil
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "dist_pkg")
STAGE = os.path.join(OUT_DIR, "myme-ios-pwa")
ZIP_PATH = os.path.join(OUT_DIR, "myme-ios-pwa.zip")

PY_MODULES = [
    "app.py", "config.py", "pptx_parse.py", "knowledge_base.py",
    "qa_brain.py", "narrate.py", "avatar.py", "doc_parse.py",
    "persons.py", "myme_launcher.py",
]

MANIFEST = """{
  "name": "炎冰数字分身",
  "short_name": "炎冰分身",
  "description": "本地数字分身：用炎冰的声音讲 PPT 并自动答疑",
  "start_url": "/",
  "scope": "/",
  "display": "standalone",
  "background_color": "#0f1420",
  "theme_color": "#0f1420",
  "icons": [
    {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
    {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"}
  ]
}
"""

SW_JS = """const CACHE='myme-v5';
const SHELL=['/','/manifest.json','/icon-192.png','/icon-512.png'];
self.addEventListener('install',e=>{e.waitUntil(caches.open(CACHE).then(c=>c.addAll(SHELL)));self.skipWaiting();});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==CACHE).map(k=>caches.delete(k)))));self.clients.claim();});
self.addEventListener('fetch',e=>{
  if(e.request.method!=='GET')return;
  e.respondWith(fetch(e.request).then(r=>{const cp=r.clone();caches.open(CACHE).then(c=>c.put(e.request,cp));return r;}).catch(()=>caches.match(e.request).then(m=>m||caches.match('/'))));
});
"""

README = """# 炎冰数字分身 · 本地部署 PWA（iOS / 任意主机）

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
"""


def _cp(rel_src, rel_dst):
    s = os.path.join(HERE, rel_src)
    d = os.path.join(STAGE, rel_dst)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    shutil.copy2(s, d)


def main():
    if os.path.exists(STAGE):
        shutil.rmtree(STAGE)
    os.makedirs(STAGE, exist_ok=True)

    for m in PY_MODULES:
        _cp(m, m)
    _cp("ui/index.html", "ui/index.html")
    _cp("voice/yanbing-sample-01.wav", "voice/yanbing-sample-01.wav")
    _cp("icon-192.png", "icon-192.png")
    _cp("icon-512.png", "icon-512.png")
    _cp("prompts/__init__.py", "prompts/__init__.py")
    _cp("prompts/system_prompt.py", "prompts/system_prompt.py")

    # 静态 PWA 资源参考
    os.makedirs(os.path.join(STAGE, "pwa"), exist_ok=True)
    with open(os.path.join(STAGE, "pwa", "manifest.webmanifest"), "w", encoding="utf-8") as f:
        f.write(MANIFEST)
    with open(os.path.join(STAGE, "pwa", "sw.js"), "w", encoding="utf-8") as f:
        f.write(SW_JS)
    _cp("icon-192.png", "pwa/icon-192.png")
    _cp("icon-512.png", "pwa/icon-512.png")

    with open(os.path.join(STAGE, "README.md"), "w", encoding="utf-8") as f:
        f.write(README)

    # 打包成 zip（含顶层目录 myme-ios-pwa/）
    if os.path.exists(ZIP_PATH):
        os.remove(ZIP_PATH)
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(STAGE):
            for fn in files:
                fp = os.path.join(root, fn)
                arc = os.path.relpath(fp, OUT_DIR)  # myme-ios-pwa/...
                z.write(fp, arc)

    print("IOS_ZIP_OK", ZIP_PATH, os.path.getsize(ZIP_PATH), "bytes")


if __name__ == "__main__":
    main()
