# -*- coding: utf-8 -*-
"""
myme.app_settings —— 统一应用设置中枢（v2.6）

承载四类配置项，全部持久化到 <DATA_DIR>/app_settings.json：
  1. models   —— 大模型「配置记录库」：可保存多份，每份是一套完整接入参数
  2. assign   —— 能力槽位：文本 / 编程 / 图片 / 向量，各槽位可选「主模型 + 备用模型」
                 主备均从已保存的配置记录里挑，不用每次重填
  3. comfyui  —— ComfyUI 根目录、启动文件、API 地址、是否用于配图
  4. folders  —— 系统默认文件夹（PPT / 图片 / 文档 / 导入 / 导出）

另外提供：
  - deps_status()  统一依赖自检（返回人话指引，供前端做"缺什么/去哪装/点这里设置"）
  - test_model()   连通性测试（真发一次最小请求，返回延迟与错误原因）

设计原则：
  · 纯标准库，不引第三方，保证打包后零依赖丢失
  · 所有读取都是「缺省兜底」，旧版本没有 app_settings.json 也照常工作
  · 不 import config 以外的高层模块，避免循环导入
"""
import os
import json
import time
import uuid
import shutil
import urllib.request
import urllib.error

import config

SETTINGS_FILE = os.path.join(config.DATA_DIR, "app_settings.json")

# ---------- 能力槽位：模型用途 ----------
# 默认处理文本；编程、图片等按需单独委派，避免"一个模型扛所有"导致质量/成本失控
CAPABILITIES = [
    {"key": "text", "name": "文本", "desc": "讲稿 / 答疑 / 大纲 / 书面文档的正文生成"},
    {"key": "code", "name": "编程", "desc": "脚本、公式、数据处理片段"},
    {"key": "image", "name": "图片", "desc": "配图提示词与图片类模型接入"},
    {"key": "embed", "name": "向量", "desc": "知识库索引与检索用的嵌入模型"},
]

DEFAULT_MODEL = {
    "id": "",
    "name": "",
    "provider": "ollama",           # ollama | openai
    "base_url": "",
    "api_key": "",
    "chat_model": "",
    "embed_model": "",
    "caps": ["text"],               # 该模型可用于哪些能力
    "temperature": 0.7,
    "max_tokens": 1200,
    "timeout": 60,
}

DEFAULT_SETTINGS = {
    "version": 1,
    "models": [],
    "assign": {
        "text": {"primary": "", "backup": ""},
        "code": {"primary": "", "backup": ""},
        "image": {"primary": "", "backup": ""},
        "embed": {"primary": "", "backup": ""},
    },
    "comfyui": {
        "enabled": False,
        "dir": "",
        "launch_file": "",          # 如 run_nvidia_gpu.bat
        "api_url": config.COMFYUI_API_URL if hasattr(config, "COMFYUI_API_URL") else "http://127.0.0.1:8188",
        "ckpt": "",
        "lora": "",
        "auto_launch": False,       # 未运行时是否自动拉起
    },
    "folders": {
        "ppt": "",
        "image": "",
        "doc": "",
        "import": "",
        "export": "",
    },
    # 绿色伴随目录：重依赖（LivePortrait 682MB / ts_deps 141MB / ComfyUI）不随 exe 分发，
    # 单独压缩后解压到任意位置，在这里登记目录即可生效（不用重装、不用改代码）。
    "runtime": {
        "comfyui_root": "",      # ComfyUI 根目录（含 main.py + models）
        "embedded_py": "",       # ComfyUI 内置 python.exe（跑 TTS / 数字人服务）
        "liveportrait": "",      # LivePortrait 仓库根（含 src/config/inference_config.py）
        "ts_deps": "",           # TTS 依赖隔离包（含 transformers 目录）
        "ffmpeg": "",            # ffmpeg.exe
    },
}

# 运行时目录配置项的元信息（前端据此渲染表单 + 校验 + 给"去哪弄"人话）
RUNTIME_KEYS = [
    {"key": "comfyui_root", "name": "ComfyUI 根目录", "kind": "dir",
     "must": "main.py 与 models 目录",
     "how": "解压 ComfyUI 绿色包后，选择含 main.py 的那一层目录",
     "why": "语音合成（TTS）与文生图配图都跑在它的内置 Python 上"},
    {"key": "embedded_py", "name": "内置 Python（python.exe）", "kind": "file",
     "must": "python.exe",
     "how": "通常是 ComfyUI 上级目录的 python_embeded\\python.exe；登记 ComfyUI 根目录后会自动带出",
     "why": "TTS 与数字人服务由它拉起；配错会提示“语音合成失败”"},
    {"key": "liveportrait", "name": "LivePortrait 目录（682MB）", "kind": "dir",
     "must": "src\\config\\inference_config.py",
     "how": "把 LivePortrait 绿色包解压到任意盘（建议 D:\\myme-runtime\\LivePortrait），在这里登记",
     "why": "数字人视频的口型驱动；缺失时演示只剩静态图 + 配音"},
    {"key": "ts_deps", "name": "TTS 依赖隔离包 ts_deps（141MB）", "kind": "dir",
     "must": "transformers 目录",
     "how": "解压后与 LivePortrait 放同一层，选择 ts_deps 目录",
     "why": "锁住 transformers 4.57.3；缺失会报张量形状不匹配导致合成失败"},
    {"key": "ffmpeg", "name": "ffmpeg 可执行文件", "kind": "file",
     "must": "ffmpeg.exe",
     "how": "choco install ffmpeg，或下载绿色版后在此指定 ffmpeg.exe",
     "why": "音频标准化与视频合成；缺失则无法出视频"},
]


# ==================== 基础读写 ====================
def _deep_merge(base, patch):
    """字典深合并，patch 覆盖 base，返回新字典。"""
    out = dict(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load():
    s = json.loads(json.dumps(DEFAULT_SETTINGS))  # 深拷贝，避免调用方改到默认值
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                s = _deep_merge(s, json.load(f) or {})
    except Exception:
        pass
    return s


def save(patch):
    cur = load()
    if isinstance(patch, dict):
        cur = _deep_merge(cur, patch)
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return cur


# ==================== 大模型配置记录库 ====================
def _norm_model(rec):
    m = dict(DEFAULT_MODEL)
    if isinstance(rec, dict):
        m.update({k: v for k, v in rec.items() if v is not None})
    if not m.get("id"):
        m["id"] = uuid.uuid4().hex[:10]
    if not m.get("name"):
        m["name"] = m.get("chat_model") or m.get("embed_model") or ("模型" + m["id"][:4])
    # 按 provider 给缺省地址，避免用户每次都要填 localhost
    if not m.get("base_url"):
        m["base_url"] = "http://localhost:11434" if m.get("provider") == "ollama" else ""
    return m


def list_models():
    return load().get("models") or []


def get_model(mid):
    if not mid:
        return None
    for m in list_models():
        if m.get("id") == mid:
            return m
    return None


def upsert_model(rec):
    """新增或更新一条模型配置记录。返回保存后的记录。"""
    s = load()
    models = s.get("models") or []
    m = _norm_model(rec)
    for i, x in enumerate(models):
        if x.get("id") == m["id"]:
            models[i] = m
            break
    else:
        models.append(m)
    save({"models": models})
    return m


def delete_model(mid):
    s = load()
    models = [m for m in (s.get("models") or []) if m.get("id") != mid]
    # 同时清理被删除模型占用的槽位，避免出现"主模型指向不存在的记录"
    assign = s.get("assign") or {}
    for cap, slot in assign.items():
        if slot.get("primary") == mid:
            slot["primary"] = ""
        if slot.get("backup") == mid:
            slot["backup"] = ""
    save({"models": models, "assign": assign})
    return True


def get_assign(cap="text"):
    a = load().get("assign") or {}
    return (a.get(cap) or {"primary": "", "backup": ""})


def set_assign(cap, primary="", backup=""):
    s = load()
    assign = s.get("assign") or {}
    assign[cap] = {"primary": primary or "", "backup": backup or ""}
    save({"assign": assign})
    return assign[cap]


def _fallback_cfg():
    """没有配置任何模型记录时，回落到 llm_client 的历史配置（零破坏旧行为）。"""
    try:
        import llm_client
        c = llm_client.load_llm_config()
    except Exception:
        c = {}
    return {
        "provider": c.get("provider", "ollama"),
        "base_url": c.get("base_url", "http://localhost:11434"),
        "api_key": c.get("api_key", ""),
        "chat_model": c.get("chat_model", getattr(config, "LLM_MODEL", "qwen3.5:9b")),
        "embed_model": c.get("embed_model", getattr(config, "EMBED_MODEL", "bge-m3")),
        "temperature": c.get("temperature", 0.7),
        "max_tokens": c.get("max_tokens", 1200),
        "timeout": c.get("timeout", 300),
    }


def resolve_cap(cap="text"):
    """解析某能力的主/备模型配置。返回 (primary_cfg, backup_cfg)。
    没配就回落到 llm_client 历史配置，保证知识库/答疑等旧链路照常可用。"""
    a = get_assign(cap)
    pm = get_model(a.get("primary"))
    bm = get_model(a.get("backup"))
    return (pm or _fallback_cfg()), (bm if bm else None)


def chat(messages, cap="text", temperature=None, max_tokens=None):
    """按能力槽调用：先打主模型，失败自动切备用模型。返回 (text, used_name)。

    调用方无需关心主备切换，容错对用户透明；两次都失败才抛最后一次异常。
    """
    import llm_client
    primary, backup = resolve_cap(cap)
    last_err = None
    for cfg in [primary, backup]:
        if not cfg:
            continue
        try:
            txt = llm_client.chat(messages, cfg=cfg, temperature=temperature, max_tokens=max_tokens)
            return txt, (cfg.get("name") or cfg.get("chat_model") or "模型")
        except Exception as e:
            last_err = e
            continue
    raise last_err if last_err else RuntimeError("没有可用的大模型配置")


def embed(text, cap="embed"):
    """按能力槽做向量化，失败自动切备用。"""
    import llm_client
    primary, backup = resolve_cap(cap)
    last_err = None
    for cfg in [primary, backup]:
        if not cfg:
            continue
        try:
            return llm_client.embed(text, cfg=cfg)
        except Exception as e:
            last_err = e
            continue
    raise last_err if last_err else RuntimeError("没有可用的嵌入模型配置")


# ==================== 连通性测试 ====================
def _http_ok(url, timeout=6):
    """最小连通探测：GET 一个地址，只看能不能连上。"""
    req = urllib.request.Request(url, headers={"User-Agent": "myme/2.6"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read(2048)
    return True, int((time.time() - t0) * 1000), body[:512]


def test_model(rec):
    """测试一条模型配置的连通性。返回 {ok, latency_ms, detail, error, hint}。

    策略：先连 base_url 探活，再针对 provider 列模型 / 发一次极小生成请求，
    既能证明"网络通"，也能证明"模型名真的存在"。
    """
    m = _norm_model(rec)
    out = {"ok": False, "latency_ms": 0, "detail": "", "error": "", "hint": "",
           "models_found": []}
    base = (m.get("base_url") or "").rstrip("/")
    if not base:
        out["error"] = "接口地址为空"
        out["hint"] = "请先填写接口地址（如本地 Ollama 为 http://localhost:11434）"
        return out

    t0 = time.time()
    try:
        if m.get("provider") == "ollama":
            ok, _, body = _http_ok(base + "/api/tags", timeout=6)
            try:
                tags = json.loads(body.decode("utf-8", "ignore")) if isinstance(body, bytes) else {}
            except Exception:
                tags = {}
            names = [x.get("name", "") for x in (tags.get("models") or [])]
            out["models_found"] = names[:30]
            want = (m.get("chat_model") or "").split(":")[0]
            if want and names and not any(n.split(":")[0] == want for n in names):
                out["detail"] = "服务已连上，但未找到模型 " + m.get("chat_model", "")
                out["hint"] = ("本机可用模型：" + "、".join(names[:8])) if names else \
                              ("该地址没有返回模型列表，用 `ollama pull %s` 拉取模型" % want)
                out["latency_ms"] = int((time.time() - t0) * 1000)
                return out
            # 真的发一次极小生成，确认链路完全可用
            import llm_client
            llm_client.chat([{"role": "user", "content": "ping"}], cfg={
                **m, "max_tokens": 8, "timeout": 20})
            out["ok"] = True
            out["detail"] = "连接正常，模型 %s 可调用" % (m.get("chat_model") or "-")
        else:
            ok, _, body = _http_ok(base + "/v1/models", timeout=8)
            names = []
            try:
                d = json.loads(body.decode("utf-8", "ignore")) if isinstance(body, bytes) else {}
                names = [x.get("id", "") for x in (d.get("data") or [])]
            except Exception:
                pass
            out["models_found"] = names[:30]
            import llm_client
            llm_client.chat([{"role": "user", "content": "ping"}], cfg={
                **m, "max_tokens": 16, "timeout": 25})
            out["ok"] = True
            out["detail"] = "连接正常，模型 %s 可调用" % (m.get("chat_model") or "-")
    except urllib.error.HTTPError as e:
        code = getattr(e, "code", 0)
        out["error"] = "HTTP %s" % code
        if code in (401, 403):
            out["hint"] = "密钥无效或没有该模型的访问权限，请检查 API Key"
        elif code == 404:
            out["hint"] = "接口路径不存在：地址是否少了 /v1？还是服务没起来？"
        else:
            out["hint"] = "服务返回了错误，确认地址与模型名是否匹配"
    except urllib.error.URLError as e:
        out["error"] = "无法连接（%s）" % (e.reason if hasattr(e, "reason") else e)
        out["hint"] = ("本机 Ollama 未启动？命令行执行 `ollama serve` 后再试"
                       if m.get("provider") == "ollama" else "网络不通或服务地址填错")
    except Exception as e:
        out["error"] = str(e)[:180]
        out["hint"] = "模型名可能不存在，或该服务不接受当前调用格式"
    out["latency_ms"] = int((time.time() - t0) * 1000)
    return out


# ==================== ComfyUI ====================
def comfyui_cfg():
    s = load()
    return s.get("comfyui") or DEFAULT_SETTINGS["comfyui"]


def save_comfyui(patch):
    cur = _deep_merge(comfyui_cfg(), patch or {})
    save({"comfyui": cur})
    return cur


def find_comfyui_root(d=None):
    """在候选位置里找一个"真的像 ComfyUI 根目录"的路径。

    判定依据：同时存在 main.py 与 models 目录（ComfyUI 的必备结构）。
    用户配了就用用户的；没配则从常见安装位和 exe 同级向上探测。
    """
    cands = []
    if d:
        cands.append(d)
    cands.append((comfyui_cfg() or {}).get("dir", ""))
    cands.append(getattr(config, "COMFYUI_ROOT", ""))
    if getattr(__import__("sys"), "frozen", False):
        import sys
        p = os.path.dirname(sys.executable)
        for _ in range(4):
            cands.append(p)
            cands.append(os.path.join(p, "ComfyUI"))
            up = os.path.dirname(p)
            if up == p:
                break
            p = up
    for drv in ("D", "C", "E"):
        cands.append(os.path.join(drv + ":", "ComfyUI_Mie_2026_V8.0", "ComfyUI"))
        cands.append(os.path.join(drv + ":", "ComfyUI"))
    for c in cands:
        if c and os.path.isfile(os.path.join(c, "main.py")) and os.path.isdir(os.path.join(c, "models")):
            return c
    return ""


def list_comfyui_launchers(root=None):
    """列出 ComfyUI 根目录下所有可用启动脚本（.bat/.py/.sh），供下拉选择。"""
    root = root or find_comfyui_root()
    if not root or not os.path.isdir(root):
        return []
    out = []
    for nm in sorted(os.listdir(root)):
        full = os.path.join(root, nm)
        if os.path.isfile(full) and nm.lower().endswith((".bat", ".cmd", ".py", ".sh")):
            low = nm.lower()
            if any(k in low for k in ("run", "start", "launch", "main", "启动")):
                out.append(nm)
    return out


def test_comfyui(url=None):
    """探测 ComfyUI API 是否可用。返回 {ok, error, hint, latency_ms}。"""
    cfg = comfyui_cfg()
    url = (url or cfg.get("api_url") or "http://127.0.0.1:8188").rstrip("/")
    out = {"ok": False, "latency_ms": 0, "error": "", "hint": "", "url": url}
    t0 = time.time()
    try:
        _http_ok(url + "/system_stats", timeout=8)
        out["ok"] = True
        out["hint"] = "ComfyUI 服务运行中"
    except urllib.error.HTTPError as e:
        out["ok"] = True  # 返回 4xx 说明进程在，只是路径权限/版本差异
        out["hint"] = "ComfyUI 有响应（HTTP %s）" % getattr(e, "code", 0)
    except Exception as e:
        out["error"] = "无法连接：%s" % (e.reason if hasattr(e, "reason") else e)
        out["hint"] = "先在 ComfyUI 目录运行启动脚本（或勾选自动拉起），服务就绪后再点测试"
    out["latency_ms"] = int((time.time() - t0) * 1000)
    return out


# ==================== 默认文件夹 ====================
def folders():
    f = load().get("folders") or {}
    base = os.path.expanduser("~")
    return {
        "ppt": f.get("ppt") or os.path.join(base, "Documents", "myme", "PPT"),
        "image": f.get("image") or os.path.join(base, "Pictures", "myme"),
        "doc": f.get("doc") or os.path.join(base, "Documents", "myme", "文档"),
        "import": f.get("import") or os.path.join(base, "Downloads"),
        "export": f.get("export") or os.path.join(base, "Documents", "myme", "导出"),
    }


def ensure_folders():
    """确保默认文件夹都存在（首次使用时自动建，避免写入失败）。"""
    made = []
    for k, v in folders().items():
        try:
            if v and not os.path.isdir(v):
                os.makedirs(v, exist_ok=True)
                made.append(v)
        except Exception:
            pass
    return made


def save_folders(patch):
    cur = _deep_merge(load().get("folders") or {}, patch or {})
    save({"folders": cur})
    return folders()


# ==================== 运行时目录（绿色伴随目录） ====================
def runtime_cfg():
    s = load()
    rt = dict(DEFAULT_SETTINGS["runtime"])
    rt.update({k: v for k, v in (s.get("runtime") or {}).items() if v is not None})
    # 从未配置过 → 用 config 当前已解析出来的路径回填，界面上直接显示"现在用的在哪"
    try:
        import config
        cur = {
            "comfyui_root": getattr(config, "COMFYUI_ROOT", ""),
            "embedded_py": getattr(config, "EMBEDDED_PY", ""),
            "liveportrait": getattr(config, "LIVEPORTRAIT_REPO", ""),
            "ts_deps": getattr(config, "TS_DEPS_DIR", ""),
            "ffmpeg": getattr(config, "FFMPEG", ""),
        }
        for k, v in cur.items():
            if not rt.get(k) and v:
                rt[k] = v
    except Exception:
        pass
    return rt


def _check_runtime(key, path):
    """校验某一项运行时目录是否真的可用（不只看存在，还看标志性文件）。"""
    if not path:
        return False, "未配置"
    if not os.path.exists(path):
        return False, "路径不存在"
    if key == "comfyui_root":
        ok = os.path.isfile(os.path.join(path, "main.py")) and os.path.isdir(os.path.join(path, "models"))
        return ok, ("" if ok else "目录下没找到 main.py 或 models，不是 ComfyUI 根目录")
    if key == "embedded_py":
        return path.lower().endswith(".exe"), ("" if path.lower().endswith(".exe") else "请选择 python.exe")
    if key == "liveportrait":
        ok = os.path.isfile(os.path.join(path, "src", "config", "inference_config.py"))
        return ok, ("" if ok else "没找到 src\\config\\inference_config.py，不是 LivePortrait 仓库根")
    if key == "ts_deps":
        ok = os.path.isdir(os.path.join(path, "transformers"))
        return ok, ("" if ok else "目录下没找到 transformers，不是 ts_deps")
    if key == "ffmpeg":
        return os.path.isfile(path), ("" if os.path.isfile(path) else "不是可执行文件")
    return True, ""


def runtime_status():
    """返回每项运行时目录的 {key,name,ok,path,error,must,how,why}。"""
    rt = runtime_cfg()
    out = []
    for m in RUNTIME_KEYS:
        p = rt.get(m["key"], "")
        ok, err = _check_runtime(m["key"], p)
        out.append({"key": m["key"], "name": m["name"], "kind": m["kind"], "ok": ok,
                    "path": p or "", "error": err, "must": m["must"], "how": m["how"], "why": m["why"]})
    return {"items": out, "all_ok": all(i["ok"] for i in out)}


def _apply_to_config(rt):
    """把运行时目录即时套用到 config（免重启），并同步子进程需要的环境变量。"""
    try:
        import config
    except Exception:
        return
    c = rt.get("comfyui_root", "")
    if c and os.path.isdir(c):
        config.COMFYUI_ROOT = c
        config.QWEN_TTS_NODES = os.path.join(c, "custom_nodes", "qwen3-tts-comfyui")
        config.QWEN_TTS_MODEL = os.path.join(c, "models", "qwen-tts", "Qwen3-TTS-12Hz-1.7B-Base")
    p = rt.get("embedded_py", "")
    if p and os.path.isfile(p):
        config.EMBEDDED_PY = p
    f = rt.get("ffmpeg", "")
    if f and os.path.isfile(f):
        config.FFMPEG = f
    l = rt.get("liveportrait", "")
    if l and os.path.isfile(os.path.join(l, "src", "config", "inference_config.py")):
        config.LIVEPORTRAIT_REPO = l
        config.AVATAR_IMAGE_DEFAULT = os.path.join(l, "assets", "examples", "source", "s0.jpg")
        config.LANDMARK_ONNX = os.path.join(l, "pretrained_weights", "liveportrait", "landmark.onnx")
        os.environ["MYME_LP"] = l
    t = rt.get("ts_deps", "")
    if t and os.path.isdir(os.path.join(t, "transformers")):
        config.TS_DEPS_DIR = t
        os.environ["MYME_TS_DEPS"] = t


def save_runtime(patch):
    """保存运行时目录配置并即时生效（同时写入 app_settings.json，下次启动自动沿用）。"""
    cur = runtime_cfg()
    cur.update({k: (v or "").strip() for k, v in (patch or {}).items() if v is not None})
    # 只登记 ComfyUI 根目录时，顺带把同级嵌入 python 补出来
    if cur.get("comfyui_root") and not cur.get("embedded_py"):
        guess = os.path.abspath(os.path.join(cur["comfyui_root"], "..", "python_embeded", "python.exe"))
        if os.path.isfile(guess):
            cur["embedded_py"] = guess
    save({"runtime": cur})
    _apply_to_config(cur)
    return runtime_status()


def scan_runtime():
    """自动探测本机常见位置的运行时目录，返回候选列表（供"自动探测"按钮一键填充）。"""
    import sys
    found = {}
    cands = {k: [] for k in ("comfyui_root", "embedded_py", "liveportrait", "ts_deps", "ffmpeg")}
    roots = []
    if getattr(sys, "frozen", False):
        p = os.path.dirname(sys.executable)
        for _ in range(4):
            roots.append(p)
            up = os.path.dirname(p)
            if up == p:
                break
            p = up
    roots.append(os.path.dirname(os.path.abspath(__file__)))          # 源码树（开发态）
    roots.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 上级（常见部署布局）
    for drv in ("C", "D", "E", "F"):
        roots.append(drv + ":\\")
        roots.append(os.path.join(drv + ":\\", "myme-runtime"))
        roots.append(os.path.join(drv + ":\\", "ComfyUI_Mie_2026_V8.0"))
        roots.append(os.path.join(drv + ":\\", "ComfyUI_Mie_2026_V8.0", "ComfyUI"))

    def uniq(seq):
        seen, out = set(), []
        for x in seq:
            if x and x not in seen:
                seen.add(x)
                out.append(x)
        return out

    # LivePortrait / ts_deps：额外扫一层 runtime 目录
    for r in uniq(roots):
        for nm in ("LivePortrait", "myme-runtime\\LivePortrait", "runtime\\LivePortrait"):
            cands["liveportrait"].append(os.path.join(r, nm))
        for nm in ("ts_deps", "myme-runtime\\ts_deps", "runtime\\ts_deps"):
            cands["ts_deps"].append(os.path.join(r, nm))
        cands["comfyui_root"].append(r)
        cands["comfyui_root"].append(os.path.join(r, "ComfyUI"))
        cands["embedded_py"].append(os.path.join(r, "python_embeded", "python.exe"))
        cands["embedded_py"].append(os.path.join(r, "ComfyUI", "..", "python_embeded", "python.exe"))
    for ff in (r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
               r"C:\ffmpeg\bin\ffmpeg.exe", r"D:\ffmpeg\bin\ffmpeg.exe"):
        cands["ffmpeg"].append(ff)
    try:
        which = shutil.which("ffmpeg")
        if which:
            cands["ffmpeg"].insert(0, which)
    except Exception:
        pass

    for k, paths in cands.items():
        for p in uniq(paths):
            ok, _ = _check_runtime(k, p)
            if ok:
                found[k] = p
                break
    return found


# ==================== 依赖自检（人话指引） ====================
def _exists(p):
    return bool(p) and os.path.exists(p)


def deps_status():
    """检查所有外部依赖，每项带「缺什么 → 去哪弄 → 点哪设置」。

    前端据此渲染：未就绪项显示黄色告警 + 「去设置」按钮直达对应配置界面。
    """
    items = []

    def add(key, name, ok, path, need, how, setting):
        items.append({"key": key, "name": name, "ok": bool(ok), "path": path or "",
                      "need": need, "how": how, "setting": setting})

    # 1) TTS 环境（ComfyUI 嵌入式 python）
    py = getattr(config, "EMBEDDED_PY", "")
    add("tts_python", "语音合成环境（ComfyUI 内置 Python）", _exists(py), py,
        "缺失则无法生成分身语音，视频也没有配音",
        "到「设置 → 🗂 运行时目录配置」登记 ComfyUI 根目录（或直接在那里指定 python.exe）",
        "runtime")

    # 2) Qwen3-TTS 模型
    tm = getattr(config, "QWEN_TTS_MODEL", "")
    add("tts_model", "Qwen3-TTS 声纹克隆模型", _exists(tm), tm,
        "缺失则无法克隆音色",
        "把 Qwen3-TTS-12Hz-1.7B-Base 放到 ComfyUI\\models\\qwen-tts\\ 下（ComfyUI 根目录登记正确后会自动定位）",
        "runtime")

    # 3) ffmpeg
    ff = getattr(config, "FFMPEG", "")
    add("ffmpeg", "ffmpeg（音视频转码）", _exists(ff), ff,
        "缺失则无法合成视频、无法做音频标准化",
        "choco install ffmpeg，或下载绿色版后到「设置 → 🗂 运行时目录配置」指定 ffmpeg.exe",
        "runtime")

    # 4) LivePortrait（口型驱动，682MB）
    lp = getattr(config, "LIVEPORTRAIT_REPO", "")
    lp_ok = _exists(os.path.join(lp, "src", "config", "inference_config.py"))
    add("liveportrait", "LivePortrait（口型驱动模型，682MB）", lp_ok, lp,
        "缺失则画面为静态图 + 配音，没有口型动作",
        "把 LivePortrait 绿色包解压到任意盘，到「设置 → 🗂 运行时目录配置」登记该目录",
        "runtime")

    # 5) ts_deps（TTS 专用 transformers 隔离包）
    ts = getattr(config, "TS_DEPS_DIR", "")
    add("ts_deps", "TTS 依赖隔离包（ts_deps，141MB）", _exists(ts), ts,
        "缺失会导致模型推理报张量形状错误，合成失败",
        "把 ts_deps 绿色包解压后与 LivePortrait 放同一层，再到「设置 → 🗂 运行时目录配置」登记",
        "runtime")

    return {"items": items, "all_ok": all(i["ok"] for i in items)}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "deps":
        import pprint
        pprint.pprint(deps_status())
    else:
        import pprint
        pprint.pprint(load())
