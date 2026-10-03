# -*- coding: utf-8 -*-
"""
myme — 本地数字分身 / 用我的声音讲 PPT 并自动答疑
集中配置：所有路径、模型、服务地址都在这里改，模块从本文件读取。
"""
import os
import re
import json
import uuid
import shutil

# ============ 项目根目录 ============
WORKSPACE = os.path.dirname(os.path.abspath(__file__))

# ============ 打包运行（PyInstaller onefile）路径重定向 ============
# 冻结后 __file__ 指向每次随机解压的临时目录（不可持久、Program Files 只读）。
# 只读打包资源（ui/voice/LivePortrait/图标）继续按 WORKSPACE(=MEIPASS) 读取；
# 运行时可变数据（知识库/讲解/数字人/验证/分身象库）重定向到用户可写目录，保证持久化。
import sys as _sys
if getattr(_sys, "frozen", False):
    WORKSPACE = _sys._MEIPASS
    DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "myme")
    os.makedirs(DATA_DIR, exist_ok=True)
else:
    DATA_DIR = WORKSPACE

# ============ 语音克隆 TTS（复用已验证管线）============
# ComfyUI 内置 python（自带 torch 2.10+cu130 / transformers 5.5 / soundfile）
# 【2026-10-03 修复】原先写死 D:\ComfyUI_Mie_2026_V8.0，而本机实际实例在 H 盘，
# 导致 TTS 常驻服务 / Sonic 引擎 / avatar_service 全部拉不起来（装好也白跑）。
# ComfyUI 树根不随盘符固定，故改为「环境变量 > 设置登记 > 全盘扫描」三级探测。


def _probe_comfy_root():
    """扫描本机盘符找 ComfyUI 树根（H/E/F 等可移动盘优先于 C/D 系统盘）。

    探测不到时返回空串，交给下游走「优雅降级 + 设置里手填」，不要崩。
    """
    try:
        import string
        roots = []
        for d in string.ascii_uppercase:
            drv = d + ":"
            try:
                if not os.path.isdir(drv + "\\"):
                    continue
            except Exception:
                continue
            roots.append(drv)
        # 可移动/数据盘优先，C/D 排后（系统盘常被多个软件实例混用）
        def _rank(drv):
            return (0 if drv[0] not in ("C", "D") else 1, drv)

        for drv in sorted(roots, key=_rank):
            # 候选顺序即优先级：① 外层目录套 ComfyUI 子目录（本机结构）
            # ② 裸 ComfyUI ③ 外层目录兜底。
            # 判定「真身」的硬条件：有 custom_nodes 子目录（否则路径语义会错位，
            # 导致 python_embeded / qwen3-tts-comfyui 都推导错）。
            for buf in (os.path.join(drv, "ComfyUI_Mie_2026_V8.0", "ComfyUI"),
                        os.path.join(drv, "ComfyUI_Mie_2026_V8.0"),
                        os.path.join(drv, "ComfyUI")):
                try:
                    if os.path.isdir(buf) and os.path.isdir(os.path.join(buf, "custom_nodes")):
                        return buf
                except Exception:
                    continue
            for buf in (os.path.join(drv, "ComfyUI_Mie_2026_V8.0", "ComfyUI"),
                        os.path.join(drv, "ComfyUI_Mie_2026_V8.0"),
                        os.path.join(drv, "ComfyUI")):
                try:
                    if os.path.isdir(buf):
                        return buf
                except Exception:
                    continue
    except Exception:
        pass
    return ""


_COMFY_ROOT_RAW = (
    (os.environ.get("MYME_COMFYUI_ROOT") or "").strip()
    or _probe_comfy_root()
)
COMFYUI_ROOT = _COMFY_ROOT_RAW
# 树根为空时不要把路径拼成相对串（会被误判为「已配置」），显式给空
QWEN_TTS_NODES = os.path.join(COMFYUI_ROOT, "custom_nodes", "qwen3-tts-comfyui") if COMFYUI_ROOT else ""
QWEN_TTS_MODEL = os.path.join(COMFYUI_ROOT, "models", "qwen-tts", "Qwen3-TTS-12Hz-1.7B-Base") if COMFYUI_ROOT else ""
EMBEDDED_PY = (os.environ.get("MYME_EMBEDDED_PY") or "").strip() or (
    os.path.abspath(os.path.join(COMFYUI_ROOT, "..", "python_embeded", "python.exe"))
    if (COMFYUI_ROOT and os.path.isfile(os.path.join(COMFYUI_ROOT, "..", "python_embeded", "python.exe")))
    else ""
)
# Qwen3-TTS 节点目录（qwen_tts 包所在）
# 树根为空时显式保持空串：否则会拼成相对串（如 "custom_nodes/qwen3-tts-comfyui"），
# 被 os.path.exists 按当前工作目录判定，产生「已配置但路径其实是相对串」的假象。
if COMFYUI_ROOT:
    QWEN_TTS_NODES = os.path.join(COMFYUI_ROOT, "custom_nodes", "qwen3-tts-comfyui")
    # 语音克隆基座模型（1.7B，支持中/英等 10 种语言）—— 注意：本体是【目录】不是单文件
    QWEN_TTS_MODEL = os.path.join(COMFYUI_ROOT, "models", "qwen-tts", "Qwen3-TTS-12Hz-1.7B-Base")
# 用户声纹样本（炎冰本人 ~10 分钟 24kHz 单声道）
VOICE_SAMPLE = os.path.join(WORKSPACE, "voice", "yanbing-sample-01.wav")
# 各分身声纹映射（person id -> wav）。新增分身：把干净参考 wav 放 voice/ 并在此登记。
VOICE_BY_PERSONA = {
    "yanbing": VOICE_SAMPLE,
    "jinzi": os.path.join(WORKSPACE, "voice", "jinzi-sample-01.wav"),
}


def _settings_person():
    """从 settings.json 里读用户上次选的分身 id（前端第二层作用域栏写入）。

    注意读 DATA_DIR 而不是 WORKSPACE：打包成 onefile 后 WORKSPACE 是每次随机解压的
    临时目录（MEIPASS），settings.json 实际落在 %LOCALAPPDATA%\\myme\\。
    """
    try:
        fp = os.path.join(DATA_DIR, "settings.json")
        with open(fp, "r", encoding="utf-8") as f:
            return (json.load(f) or {}).get("person") or ""
    except Exception:
        return ""


def _discover_voice(person):
    """声纹自动发现：voice/<person>-sample-01.wav、voice/<person>.wav、voice/<person>*.wav。

    新增分身时把干净参考音频丢进 voice/ 就能用，不必再改代码登记表。
    """
    if not person:
        return ""
    import glob as _glob
    vdir = os.path.join(WORKSPACE, "voice")
    for pat in ("%s-sample-*.wav" % person, "%s.wav" % person, "%s*.wav" % person):
        hits = sorted(_glob.glob(os.path.join(vdir, pat)))
        if hits:
            return hits[0]
    return ""


def resolve_voice(person=None):
    """把分身 id 解析为声纹 wav 绝对路径。

    优先级：显式指定且已登记 > persons.json 的当前激活分身 > settings.json 的 person
            > 全局 VOICE_SAMPLE(炎冰)；person 未在表中但 voice/ 下有同名样本则自动采用。

    ⚠ 历史坑：前端「选择分身」只写 settings.json，而这里只读 persons.json.default，
      两边不同步 → 界面选金子、语音仍是炎冰。现在 persons.set_active() 会同时更新
      persons.json，这里再把 settings.json 作为兜底，双保险。
    """
    if not person:
        try:
            with open(os.path.join(PORTRAITS_DIR, "persons.json"), "r", encoding="utf-8") as f:
                person = (json.load(f) or {}).get("default")
        except Exception:
            person = None
    if not person:
        person = _settings_person()
    p = VOICE_BY_PERSONA.get(person) or _discover_voice(person)
    return p if (p and os.path.exists(p)) else VOICE_SAMPLE

# ============ 音频工具 ============
FFMPEG = r"C:\ProgramData\chocolatey\bin\ffmpeg.exe"

# ============ 本地大模型 / 嵌入（Ollama）============
OLLAMA_BASE = "http://localhost:11434"
# 问答大脑默认模型（中文强、速度快）；质量优先可改 qwen3.8:27b 或 ornith-1.5:35b
LLM_MODEL = os.environ.get("MYME_LLM", "qwen3.5:9b")
# 向量嵌入模型（中文友好、多语言）
EMBED_MODEL = os.environ.get("MYME_EMBED", "bge-m3")

# ============ 目录 ============
DIR_KB = os.path.join(WORKSPACE, "kb_data")          # 知识库持久化（JSON + Ollama 嵌入）
# ============ 知识库三层范围（v2.6）============
#   public       —— 公有库（通用常识：IT / 水利 / 公文写作…），全机共享，跨项目可用
#   project      —— 项目私有库（config.DIR_KB），跟随当前项目
#   person:<id>  —— 分身私有库（保证每个分身有自己的用语习惯、人设与文档模板）
# 原则：私有的可以「提升」到公有（沉淀复用），公有不必下沉到私有（避免重复占空间）。
DIR_KB_PUBLIC = os.path.join(DATA_DIR, "kb_public")
DIR_KB_PERSON = os.path.join(DATA_DIR, "kb_person")
DIR_NARR = os.path.join(WORKSPACE, "narrations")      # 讲解音频产出
DIR_AVATAR = os.path.join(WORKSPACE, "avatar_out")    # 数字人产出
DIR_TEST = os.path.join(WORKSPACE, "test_out")        # 验证产出
# 备用素材目录（离线兜底）：本机 U 盘/网盘同步目录，放演示文稿、讲解文本与参考文档。
# 上传通道不可用时，可直接从这里挑 PPT 讲解、并入知识库。可用环境变量覆盖。
DIR_BACKUP = os.environ.get("MYME_BACKUP_DIR", r"D:/Users/ppt")
BACKUP_DECK_EXT = (".pptx", ".ppt")
BACKUP_DOC_EXT = (".md", ".txt", ".markdown", ".docx", ".pdf", ".doc", ".xls", ".xlsx", ".csv")

# ============ TTS 默认参数（克隆声，稳定可复现）============
TTS_SPEED_ZH = 1.0        # 讲解用原速（成人专业语气）
TTS_TOP_P = 0.8
TTS_TOP_K = 20
TTS_TEMP = 0.9
TTS_REP_PEN = 1.05
TTS_SEED_BASE = 42

# ============ 常驻 TTS 服务（消除冷加载）============
# 把 Qwen3-TTS 模型常驻一个嵌式 python 进程，app/narrate 走 HTTP 热合成；
# 服务不可用时自动回退到「每次冷启动子进程」(原行为)。两者都需 EMBEDDED_PY。
USE_TTS_SERVICE = True
TTS_SERVICE_URL = os.environ.get("MYME_TTS_URL", "http://127.0.0.1:8777")
TTS_SERVICE_PORT = int(os.environ.get("MYME_TTS_PORT", "8777"))

# ============ 知识库分块参数 ============
CHUNK_SIZE = 500
CHUNK_OVERLAP = 100
RETRIEVE_TOP_K = 5
RETRIEVE_ALPHA = 0.6      # 混合检索：向量权重 0.6 / BM25 权重 0.4（开放问答可调高；技术文档可调高 BM25）
RETRIEVE_THRESHOLD = 0.0  # 向量相似度阈值，低于此值视为不相关直接丢弃（抑制幻觉）

# ============ 知识库「调用层上下文压缩」============
# 遵循三层压缩原则：存储层保留原文块、检索层向量+BM25混合、调用层检索后再压缩。
# 压缩在「检索之后、送大模型之前」做，只把与问题最相关的句子/要点送给模型，减少 token。
# 默认走规则提取（离线、零依赖、保证来源可溯源）；可开启 LLM 精炼（需本地 Ollama，速度较慢）。
KB_COMPRESS = True               # 总开关：检索结果送模型前先压缩
KB_COMPRESS_TOKENS = 1200         # 压缩后目标 token 上限（中文约按字符计，1200≈600~1200 字）
KB_COMPRESS_USE_LLM = False       # 是否启用 LLM 精炼压缩（默认关，离线规则兜底；开启需本地模型在线）
# 检索相似度过滤默认阈值：技术文档可调高到 0.6~0.75，开放问答可降到 0.3~0.45
KB_QUERY_THRESHOLD = 0.45

# ============ 知识库「重建」（去重 / 优化 / 压缩）============
# 重建只动「索引与冗余」，不动「原文」：保留每一块原始文本与向量，只删掉重复/低质块、合并来源元数据。
KB_NEAR_DUP_THR = 0.97            # 近重复余弦阈值：> 此值视为重复，保留更长更完整的一块
KB_MIN_CHARS = 8                  # 低于此字符数的零散/空块（纯标点、空白、页码）视为低质，重建时丢弃
KB_BACKUP_EXT = ".mymekb"         # 知识库备份文件扩展名（zip + manifest，导出可再导入，格式兼容）

# ============ 数字人（LivePortrait 音频驱动）============
# 形象素材：正面平视、无强阴影的高清图，人脸占比足够大
AVATAR_IMAGE = os.path.join(WORKSPACE, "avatar_out", "portrait.png")
# LivePortrait 官方仓库目录（含 src 包与 assets 示例）
# ⚠ 打包态陷阱：682MB 的模型仓库不会打进 exe，冻结后 WORKSPACE 只是临时解压目录，
#   直接拼出的路径必然不存在，最终表现为数字人视频 ModuleNotFoundError: No module named 'src'。
#   这里按优先级挑一个"真实存在"的仓库根：环境变量 > exe 同级/上级 ../LivePortrait > 包内 > 源码树。
def _resolve_lp_repo():
    _cands = [os.environ.get("MYME_LP", "")]
    if getattr(_sys, "frozen", False):
        # 从 exe 所在目录逐级向上找配套的 LivePortrait（开发布局 dist/myme.exe → ../LivePortrait）
        _d = os.path.dirname(_sys.executable)
        for _ in range(4):
            _cands.append(os.path.join(_d, "LivePortrait"))
            _up = os.path.dirname(_d)
            if _up == _d:
                break
            _d = _up
    _cands.append(os.path.join(WORKSPACE, "LivePortrait"))
    _cands.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "LivePortrait"))
    for _c in _cands:
        if _c and os.path.isfile(os.path.join(_c, "src", "config", "inference_config.py")):
            return _c
    return os.path.join(WORKSPACE, "LivePortrait")   # 都没命中则保持原行为

LIVEPORTRAIT_REPO = _resolve_lp_repo()

# ⚠ 子进程看不到 exe 路径：TTS / 数字人服务由【外部 ComfyUI 嵌入式 python】拉起，
#   该进程 sys.frozen=False，上面的 exe 逐级探测逻辑不会执行，config 的 WORKSPACE
#   会退回临时解压目录 → 再次 ModuleNotFoundError: No module named 'src'。
#   解决：冻结主进程解出真实仓库后写入环境变量，子进程天然继承即可命中同一份仓库。
if LIVEPORTRAIT_REPO and os.path.isfile(
        os.path.join(LIVEPORTRAIT_REPO, "src", "config", "inference_config.py")):
    os.environ.setdefault("MYME_LP", LIVEPORTRAIT_REPO)


def svc_script(name):
    """返回供【外部嵌入式 python】执行的服务脚本路径（冻结态会自动拷出 _MEIPASS）。

    ⚠ 为什么必须拷出去：Python 解释器启动时一定会把"脚本所在目录"塞进 sys.path[0]。
      打包根 _MEIPASS 里塞满了打包解释器自带的扩展（_cffi_backend / _ssl / _socket…），
      而 TTS/数字人服务实际跑在 ComfyUI 的嵌入式 python 上，它自己 site-packages 里的
      cffi 版本（2.0.0）与包里的 _cffi_backend（2.1.1）不一致，一旦合成就会崩：
        "Version mismatch: this is the 'cffi' package ... The two versions should be equal"
      把脚本连同 config.py 拷到独立目录再运行，sys.path[0] 就是干净目录，彻底规避冲突。
    """
    _here = os.path.dirname(os.path.abspath(__file__))
    if not getattr(_sys, "frozen", False):
        return os.path.join(_here, name)
    _root = getattr(_sys, "_MEIPASS", _here)
    _src = os.path.join(_root, name)
    if not os.path.exists(_src):
        return _src
    _dep = ("tts_service.py", "tts_worker.py", "avatar_service.py", "avatar.py", "config.py")
    try:
        import shutil as _sh
        _d = os.path.join(DATA_DIR, "svc")
        os.makedirs(_d, exist_ok=True)
        for f in _dep:
            s = os.path.join(_root, f)
            if not os.path.exists(s):
                continue
            t = os.path.join(_d, f)
            if (not os.path.exists(t)
                    or os.path.getsize(s) != os.path.getsize(t)
                    or os.path.getmtime(s) > os.path.getmtime(t)):
                _sh.copy2(s, t)
    except Exception:
        return _src
    return os.path.join(os.path.join(DATA_DIR, "svc"), name)


def _resolve_ts_deps():
    """定位 TTS 专用的隔离依赖目录（transformers ==4.57.3，qwen_tts 官方要求）。

    ⚠ 141MB 不随包发布；冻结态下服务脚本被拷到独立目录后 HERE 已不是源码根，
      tts_worker 自身拼出的 ts_deps 必然落空，会退回 ComfyUI 的 transformers 5.5，
      触发 Q/K 掩码形状不匹配（"Expected ... [16, 21] but got: [16, 11]"）导致合成失败。
      故与 LivePortrait 同策：主进程解析真实路径后写入环境变量，子进程继承。
    """
    _cands = [os.environ.get("MYME_TS_DEPS", "")]
    if getattr(_sys, "frozen", False):
        _d = os.path.dirname(_sys.executable)
        for _ in range(4):
            _cands.append(os.path.join(_d, "ts_deps"))
            _up = os.path.dirname(_d)
            if _up == _d:
                break
            _d = _up
    _cands.append(os.path.join(WORKSPACE, "ts_deps"))
    _cands.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ts_deps"))
    for _c in _cands:
        if _c and os.path.isdir(_c) and os.path.isdir(os.path.join(_c, "transformers")):
            return _c
    return ""


TS_DEPS_DIR = _resolve_ts_deps()
if TS_DEPS_DIR:
    os.environ.setdefault("MYME_TS_DEPS", TS_DEPS_DIR)
# 默认演示肖像（仓库自带示例，仅在 AVATAR_IMAGE 缺失时用于联调/演示；正式用请替换为炎冰本人正面照）
AVATAR_IMAGE_DEFAULT = os.path.join(LIVEPORTRAIT_REPO, "assets", "examples", "source", "s0.jpg")
# 人脸检测（绕开 insightface：其依赖的 protobuf 与本地 TTS 音频栈冲突）。
# 用 OpenCV 内置 haarcascade 做检测 + 官方 landmark.onnx 精修 203 关键点。
LANDMARK_ONNX = os.path.join(LIVEPORTRAIT_REPO, "pretrained_weights", "liveportrait", "landmark.onnx")
# LivePortrait 官方仓库目录（含 inference.py 与 liveportrait 包）；为空则自动探测常见位置
LIVEPORTRAIT_DIR = os.environ.get("MYME_LP", "")
# 推荐分辨率 688x368（显存占用与口型稳定性的最佳平衡点）
AVATAR_RES = "688,368"
# 音频驱动唇形关键参数：重采样 16kHz + 峰值 -1dB 标准化 + WAV 无损（严禁 MP3 二次压缩）
AVATAR_AUDIO_SR = 16000
AVATAR_AUDIO_PEAK = -1.0

# ============ 数字分身驱动强度（防「脸肿 / 双下颌 / 多嘴巴」）============
# LivePortrait 官方管线在三处「收敛」，我们早期实现三处都缺，于是脸被拉变形：
#   ① driving_multiplier —— 官方最后一步 x_d = x_s + (x_d - x_s) * mult，
#      把所有偏离源的量按比例压回去；官方默认 1.0，口型驱动场景 0.7~0.85 更稳。
#   ② lip normalize —— 官方先把「静音时的唇开合比」归零（lip_delta_before_animation），
#      否则静止帧也带一个恒定唇形偏移，整张脸会像被下巴顶起来（双下颌、肿胀感）。
#   ③ 源姿态必须保留：R_d 要乘上源的 R_s，不能拿小幅度摆动矩阵直接把源姿态覆盖掉。
AVATAR_DRIVE_MULT = float(os.environ.get("MYME_LP_MULT", "0.72"))   # 0.6~1.0，越小越保守（脸变形多次修复后下调到 0.72）
# v2.6.1 第三批（口型极简化）：实测用户真实肖像（中老年男性、薄唇）在 0.32/0.42 下
# 说话时嘴部被拉成"呲牙假笑"、嘴角涂抹变形—— retarget_lip 的 MLP 稍一超出训练分布，
# 下半张脸就跟着歪。把张嘴幅度砍半、上限砍半，口型动得少但脸保得住（用户明确要求"口型调到最小"）。
AVATAR_LIP_AMP = float(os.environ.get("MYME_LP_LIP_AMP", "0.15"))   # 音频能量 -> 张嘴幅度（0.32→0.15）
AVATAR_LIP_MAX = float(os.environ.get("MYME_LP_LIP_MAX", "0.22"))   # 唇开合比上限（0.42→0.22）
# v2.6.1 第三批：说话时的"微笑微表情"是呲牙假笑的直接推手——它把嘴角/脸颊关键点往外拉，
# 与口型叠加后嘴被拉宽、露出僵硬牙条。默认压到 0.25 倍；设 0 完全关闭（MYME_LP_SMILE）。
AVATAR_SMILE_MULT = float(os.environ.get("MYME_LP_SMILE", "0.25"))
AVATAR_MOTION_DEFAULT = float(os.environ.get("MYME_LP_MOTION", "0.6"))  # 微动总强度

# ============ 制作 PPT 配图（可选：本地 ComfyUI 文生图 + LoRA）============
# 默认关闭：需在「设置 / 大模型接入」或环境变量开启，并配置本机 ComfyUI API 与模型。
# 不配置也能正常"制作 PPT"（仅文字大纲），配图为增强项。
COMFYUI_API_URL = os.environ.get("MYME_COMFYUI_API", "http://127.0.0.1:8188")
COMFYUI_TXT2IMG_CKPT = os.environ.get("MYME_COMFYUI_CKPT", "")
COMFYUI_TXT2IMG_LORA = os.environ.get("MYME_COMFYUI_LORA", "")
COMFYUI_IMG_ENABLED = os.environ.get("MYME_COMFYUI_IMG", "0") == "1"

# ============ 运行时目录覆盖（绿色伴随目录 · v2.6）============
# 分发给别人时，682MB 的 LivePortrait 与 141MB 的 ts_deps 不塞进 62MB 的 exe，
# 而是单独压缩成「绿色伴随目录」随包分发：解压到任意盘后，在
# 「设置 → 🗂 运行时目录配置」里登记目录即可，不用重装、不用改代码。
#
# 优先级：环境变量 > app_settings.json(runtime 段) > 自动探测 > 代码默认值。
# ⚠ 必须放在上面所有路径常量之后：本段会重算由它们派生出来的二级路径。
def _runtime_override():
    """读取 <DATA_DIR>/app_settings.json 的 runtime 段（不 import 任何高层模块，避免循环导入）。"""
    try:
        with open(os.path.join(DATA_DIR, "app_settings.json"), "r", encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("runtime") or {}
    except Exception:
        return {}


_RT = _runtime_override()
_RT_ENV = {
    "embedded_py":     os.environ.get("MYME_EMBEDDED_PY", ""),
    "comfyui_root":    os.environ.get("MYME_COMFYUI_ROOT", ""),
    "ffmpeg":          os.environ.get("MYME_FFMPEG", ""),
    "liveportrait":    os.environ.get("MYME_LP", ""),
    "ts_deps":         os.environ.get("MYME_TS_DEPS", ""),
    "comfyui_api_url": os.environ.get("MYME_COMFYUI_API", ""),
}


def _rt_pick(key):
    """环境变量 > 配置记录 > 空。"""
    return (_RT_ENV.get(key) or (_RT or {}).get(key) or "").strip()


# --- ComfyUI 根目录（决定嵌入式 python / TTS 模型 / 节点目录）---
_c = _rt_pick("comfyui_root")
if _c and os.path.isdir(_c):
    COMFYUI_ROOT = _c
    QWEN_TTS_NODES = os.path.join(COMFYUI_ROOT, "custom_nodes", "qwen3-tts-comfyui")
    QWEN_TTS_MODEL = os.path.join(COMFYUI_ROOT, "models", "qwen-tts", "Qwen3-TTS-12Hz-1.7B-Base")
# --- 嵌入式 python（TTS / 数字人服务的实际解释器）---
_p = _rt_pick("embedded_py")
if _p and os.path.isfile(_p):
    EMBEDDED_PY = _p
elif COMFYUI_ROOT and os.path.isfile(os.path.join(COMFYUI_ROOT, "..", "python_embeded", "python.exe")):
    # 只登记了 ComfyUI 根目录时，顺带把同级嵌入 python 也带上
    EMBEDDED_PY = os.path.abspath(os.path.join(COMFYUI_ROOT, "..", "python_embeded", "python.exe"))
# --- ffmpeg ---
_f = _rt_pick("ffmpeg")
if _f and os.path.isfile(_f):
    FFMPEG = _f
# --- LivePortrait（682MB 绿色伴随目录）---
_l = _rt_pick("liveportrait")
if _l and os.path.isfile(os.path.join(_l, "src", "config", "inference_config.py")):
    LIVEPORTRAIT_REPO = _l
    AVATAR_IMAGE_DEFAULT = os.path.join(LIVEPORTRAIT_REPO, "assets", "examples", "source", "s0.jpg")
    LANDMARK_ONNX = os.path.join(LIVEPORTRAIT_REPO, "pretrained_weights", "liveportrait", "landmark.onnx")
    os.environ["MYME_LP"] = LIVEPORTRAIT_REPO   # 子进程（外部 python）靠它命中同一份仓库
# --- ts_deps（141MB TTS 依赖隔离包）---
_t = _rt_pick("ts_deps")
if _t and os.path.isdir(_t) and os.path.isdir(os.path.join(_t, "transformers")):
    TS_DEPS_DIR = _t
    os.environ["MYME_TS_DEPS"] = TS_DEPS_DIR
# --- ComfyUI API ---
_u = _rt_pick("comfyui_api_url")
if _u:
    COMFYUI_API_URL = _u

# ============ 多分身象库（不同场合用不同肖像，零训练，换图即换分身）============
# 象库目录：人像原件仍留在 avatar_out/ 根（用户原始上传），此处放"精选命名分身" + portraits.json 清单。
# portraits.json 结构：{ "default": "<id>", "personas": { "<id>": {"file","name","scene","desc"} } }
# 新增/替换分身：把图丢进 portraits/ 目录，改 portraits.json 即可，无需任何训练。
PORTRAITS_DIR = os.path.join(WORKSPACE, "avatar_out", "portraits")
# 两套象库并存（同目录、不同文件，均随项目动态重算）：
#   PORTRAITS_JSON = portraits.json —— 旧「扁平象库」清单（load_portraits / list_portraits / 资源管理用）
#   PERSONS_JSON   = persons.json   —— 新「多人×多形象」分身库（persons.py 用）
PORTRAITS_JSON = os.path.join(PORTRAITS_DIR, "portraits.json")
PERSONS_JSON = os.path.join(PORTRAITS_DIR, "persons.json")

# ============ 项目系统基础常量 ============
# 所有运行时可变资源（知识库/讲解/数字人/分身象库/版本/问答日志/资源历史）均归属某个项目；
# 当前所有资源默认落入 default 项目。详细 CRUD 见文末 init_projects / _recompute 等。
PROJECTS_DIR = os.path.join(DATA_DIR, "projects")
DEFAULT_PROJECT_ID = "default"
ACTIVE_PROJECT_FILE = os.path.join(PROJECTS_DIR, "active.json")
_active_project = DEFAULT_PROJECT_ID

# 以下三项为「项目作用域内」的文件（随当前项目动态重算，见文末 _recompute / init_projects）；
# 此处给出 default 项目占位初值，init_projects() 启动时会重算为真实激活项目路径。
SLIDES_FILE = os.path.join(PROJECTS_DIR, DEFAULT_PROJECT_ID, "slides.json")
QA_LOG_FILE = os.path.join(PROJECTS_DIR, DEFAULT_PROJECT_ID, "qa_log.json")
RESOURCE_HISTORY = os.path.join(PROJECTS_DIR, DEFAULT_PROJECT_ID, "resource_history.json")

# 冻结运行时：运行时可变数据目录由「项目系统」统一重算（见文末 init_projects / _recompute），
# 落盘到 %LOCALAPPDATA%/myme/projects/<id>/，不再直接挂在 DATA_DIR 根（见上方 WORKSPACE 重定向说明）。


def load_portraits():
    """读取象库清单，返回 {default, personas:{id:{"file_abs",...原始字段}}}。
    文件不存在或解析失败返回空象库（仅 default=None）。不参与任何网络/模型加载。"""
    empty = {"default": None, "personas": {}}
    try:
        if not os.path.exists(PORTRAITS_JSON):
            return empty
        with open(PORTRAITS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        personas = {}
        for pid, meta in (data.get("personas") or {}).items():
            fpath = meta.get("file")
            if fpath:
                meta = dict(meta)
                # file 支持相对 portraits/ 目录的相对路径或绝对路径
                meta["file_abs"] = fpath if os.path.isabs(fpath) else os.path.join(PORTRAITS_DIR, fpath)
            personas[pid] = meta
        return {"default": data.get("default"), "personas": personas}
    except Exception:
        return empty


def resolve_avatar(persona_id=None):
    """把分身 id 解析为可用的肖像图片绝对路径。
    优先级：指定且存在的分身 > 默认分身 > 旧 AVATAR_IMAGE(portrait.png) > 仓库示例 s0.jpg。
    persona_id 为空时回退到默认分身（而非缺肖像的 portrait.png），保证默认就能出视频。"""
    lib = load_portraits()
    candidates = []
    if persona_id and persona_id in lib["personas"]:
        candidates.append(lib["personas"][persona_id].get("file_abs"))
    if lib["default"] and lib["default"] in lib["personas"]:
        candidates.append(lib["personas"][lib["default"]].get("file_abs"))
    candidates.append(AVATAR_IMAGE if os.path.exists(AVATAR_IMAGE) else None)
    candidates.append(AVATAR_IMAGE_DEFAULT)
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return AVATAR_IMAGE_DEFAULT


def list_portraits():
    """给前端下拉框用：只暴露 id/name/scene/desc（不泄露绝对路径）。"""
    lib = load_portraits()
    items = []
    for pid, meta in lib["personas"].items():
        items.append({
            "id": pid,
            "name": meta.get("name", pid),
            "scene": meta.get("scene", ""),
            "desc": meta.get("desc", ""),
        })
    default = lib["default"] if lib["default"] in lib["personas"] else (items[0]["id"] if items else None)
    return {"default": default, "personas": items}


# 与 tts_service 同构：把 LivePortrait 推理模型常驻一个嵌式 python 进程，
# 经 HTTP 接收（音频路径 + 肖像路径）-> 返回口型同步 mp4。模型只加载一次。
# 服务不可用时，app 会自动回退到「每次冷启动子进程」跑 LivePortrait（原行为）。
USE_AVATAR_SERVICE = True
AVATAR_SERVICE_URL = os.environ.get("MYME_AVATAR_URL", "http://127.0.0.1:8778")
AVATAR_SERVICE_PORT = int(os.environ.get("MYME_AVATAR_PORT", "8778"))

# ============ 分身驱动引擎开关（#382 中期方案：扩散式 talking-head 迁移）============
# AVATAR_ENGINE: "sonic"（默认，扩散式，经本机 ComfyUI 的 ComfyUI_Sonic 节点，变形大幅减少）
#              / "liveportrait"（2D warp，已用 #379 参数压回分布内，作为 Sonic 的回退）
# Sonic 需要：① 本机 ComfyUI 运行且加载 Sonic 节点；② CUDA GPU；③ SVD + Sonic 权重齐备。
# 任一不满足时，app 会自动回退到 liveportrait，分身功能不中断。
AVATAR_ENGINE = os.environ.get("MYME_AVATAR_ENGINE", "sonic")
# Sonic 经由 ComfyUI HTTP API（与图文生成共用的 MYME_COMFYUI_API）；可单独用 MYME_SONIC_URL 覆盖。
SONIC_URL = os.environ.get("MYME_SONIC_URL", COMFYUI_API_URL)
# 注：ComfyUI 在 Windows 下 checkpoint 列表用反斜杠（SVD\svd_xt_1_1.safetensors），
# 必须与此完全一致，否则 /prompt 校验报 value_not_in_list。
SONIC_CHECKPOINT = os.environ.get("MYME_SONIC_CKPT", "SVD\\svd_xt_1_1.safetensors")
SONIC_UNET = os.environ.get("MYME_SONIC_UNET", "unet.pth")
SONIC_DTYPE = os.environ.get("MYME_SONIC_DTYPE", "fp16")
SONIC_MIN_RES = int(os.environ.get("MYME_SONIC_MINRES", "256"))   # 越小越省显存（256 / 512）
SONIC_STEPS = int(os.environ.get("MYME_SONIC_STEPS", "25"))
SONIC_FPS = float(os.environ.get("MYME_SONIC_FPS", "25"))
SONIC_TIMEOUT = int(os.environ.get("MYME_SONIC_TIMEOUT", "1800"))  # 单条推理轮询上限（秒）

# ============ 播放版标记（便携播放版专有）============
# 便携播放版由 myme_portable.spec 注入空文件 playback.mode 到打包资源目录（冻结后 = _MEIPASS），
# 据此关闭需要重型模型的常驻服务（TTS / LivePortrait）——播放版只串流已渲染好的音频与视频，无需模型。
# 也可用环境变量 MYME_PLAYBACK=1 在源码态临时切换，便于联调。
PLAYBACK_MODE = (
    os.environ.get("MYME_PLAYBACK", "0") == "1"
    or os.path.exists(os.path.join(WORKSPACE, "playback.mode"))
)
if PLAYBACK_MODE:
    USE_TTS_SERVICE = False
    USE_AVATAR_SERVICE = False

# ============ 版本号（同时用于关于/安装包标识）============
VERSION = "2.6.3"

# ============ 应用名称（多分身通用，不绑定具体人或行业）============
# 产品名：PPT制作及分身演示。首页标题/关于/安装包均用此名，
# 不再写死"炎冰的数字分身 / 水利工程"，因为支持多个分身。
APP_NAME = "PPT制作及分身演示"

# ============ 完整版 / 播放版 端口（二者不同，可同时运行）============
PORT_FULL = 7860        # 完整版（创作 + 播放）
PORT_PLAYBACK = 7861     # 便携播放版（仅播放，关闭重型模型服务）

# ============ 数字分身人设（默认值，可经分身菜单修改）============
# 首页标题会动态取「当前激活分身」的名字/角色；此处仅作兜底默认值。
PERSONA = {
    "name": "默认分身",
    "title": "PPT制作及分身演示",
    "wechat": "小七爱旺仔",
    "style": "专业但不晦涩，简洁直接，适当用比喻帮助理解",
}


def get_current_persona():
    """返回当前激活分身的可展示信息 {name, title}。

    多分身场景下，首页标题/关于据此动态显示「当前分身」，而非写死炎冰。
    优先取 persons.json 的 default 人；取不到回落到 PERSONA 默认。
    懒加载 persons，避免与 persons.py 形成顶层 import 环。
    """
    try:
        import persons
        d = persons.load()
        ps = d.get("persons") or []
        pid = d.get("default") or (ps[0].get("id") if ps else None)
        p = next((x for x in ps if x["id"] == pid), None) or (ps[0] if ps else None)
        if p:
            return {"name": p.get("name") or PERSONA["name"],
                    "title": p.get("note") or PERSONA["title"]}
    except Exception:
        pass
    return {"name": PERSONA["name"], "title": PERSONA["title"]}


def ensure_dirs():
    for d in (DIR_KB, DIR_NARR, DIR_AVATAR, DIR_TEST, PORTRAITS_DIR):
        os.makedirs(d, exist_ok=True)


def sanity_check():
    """启动前自检，返回 (ok, msgs)。"""
    msgs = []
    ok = True
    if not os.path.exists(EMBEDDED_PY):
        ok = False; msgs.append(f"[x] 嵌式 python 缺失: {EMBEDDED_PY}")
    if not os.path.exists(QWEN_TTS_MODEL):
        ok = False; msgs.append(f"[x] Qwen3-TTS 模型缺失: {QWEN_TTS_MODEL}")
    if not os.path.exists(VOICE_SAMPLE):
        ok = False; msgs.append(f"[x] 声纹样本缺失: {VOICE_SAMPLE}")
    if not os.path.exists(FFMPEG):
        ok = False; msgs.append(f"[x] ffmpeg 缺失: {FFMPEG}")
    return ok, msgs


# ============ 项目系统（顶层引入 project 概念，所有资源隶属于项目）============
# 资源布局：DATA_DIR/projects/<project_id>/
#   { kb_data/, narrations/, avatar_out/(portraits/), slides.json, qa_log.json, resource_history.json }
# 当前所有资源默认归属 default 项目；支持切换 / 新建 / 重命名 / 删除项目。
# 基础常量 PROJECTS_DIR / DEFAULT_PROJECT_ID / ACTIVE_PROJECT_FILE / _active_project 见目录段定义。

def _project_base(pid):
    return os.path.join(PROJECTS_DIR, pid)


def _slug(name):
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "-", (name or "").strip()).strip("-")
    return s or ("p" + uuid.uuid4().hex[:6])


def _recompute(pid):
    """把模块级 DIR_* / SLIDES_FILE / QA_LOG_FILE / RESOURCE_HISTORY 重算到指定项目。
    各数据模块（knowledge_base / persons / avatar / app）均经 config.X 动态读取，切换项目即生效。"""
    global DIR_KB, DIR_NARR, DIR_AVATAR, DIR_TEST, PORTRAITS_DIR, PORTRAITS_JSON, PERSONS_JSON
    global SLIDES_FILE, QA_LOG_FILE, RESOURCE_HISTORY
    base = _project_base(pid)
    DIR_KB = os.path.join(base, "kb_data")
    DIR_NARR = os.path.join(base, "narrations")
    DIR_AVATAR = os.path.join(base, "avatar_out")
    DIR_TEST = os.path.join(base, "test_out")
    PORTRAITS_DIR = os.path.join(base, "avatar_out", "portraits")
    # 旧扁平象库清单 + 新分身库，二者同目录、文件名不同（详见目录段注释）
    PORTRAITS_JSON = os.path.join(PORTRAITS_DIR, "portraits.json")
    PERSONS_JSON = os.path.join(PORTRAITS_DIR, "persons.json")
    SLIDES_FILE = os.path.join(base, "slides.json")
    QA_LOG_FILE = os.path.join(base, "qa_log.json")
    RESOURCE_HISTORY = os.path.join(base, "resource_history.json")


def get_active_project():
    try:
        if os.path.exists(ACTIVE_PROJECT_FILE):
            with open(ACTIVE_PROJECT_FILE, "r", encoding="utf-8") as f:
                return (json.load(f) or {}).get("active", DEFAULT_PROJECT_ID)
    except Exception:
        pass
    return DEFAULT_PROJECT_ID


def set_active_project(pid, persist=True):
    """切换当前激活项目，并即时重算所有数据目录。persist=False 仅内存切换（如回退到 default）。"""
    global _active_project
    _active_project = pid
    _recompute(pid)
    if persist:
        try:
            os.makedirs(PROJECTS_DIR, exist_ok=True)
            with open(ACTIVE_PROJECT_FILE, "w", encoding="utf-8") as f:
                json.dump({"active": pid}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    ensure_dirs()
    return pid


def list_projects():
    os.makedirs(PROJECTS_DIR, exist_ok=True)
    active = get_active_project()
    items = []
    for name in sorted(os.listdir(PROJECTS_DIR)):
        full = os.path.join(PROJECTS_DIR, name)
        if name == "active.json" or not os.path.isdir(full):
            continue
        proj_name = name
        meta = os.path.join(full, "project.json")
        if os.path.exists(meta):
            try:
                proj_name = (json.load(open(meta, encoding="utf-8")) or {}).get("name", name)
            except Exception:
                pass
        items.append({"id": name, "name": proj_name, "active": name == active})
    if not items:
        items.append({"id": DEFAULT_PROJECT_ID, "name": DEFAULT_PROJECT_ID, "active": True})
    return items


def create_project(name):
    os.makedirs(PROJECTS_DIR, exist_ok=True)
    pid = _slug(name) or "project"
    orig, i = pid, 2
    while os.path.isdir(_project_base(pid)):
        pid = "%s%d" % (orig, i)
        i += 1
    base = _project_base(pid)
    os.makedirs(base, exist_ok=True)
    try:
        with open(os.path.join(base, "project.json"), "w", encoding="utf-8") as f:
            json.dump({"name": name or pid}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return pid


def rename_project(pid, name):
    base = _project_base(pid)
    if not os.path.isdir(base):
        return False
    try:
        with open(os.path.join(base, "project.json"), "w", encoding="utf-8") as f:
            json.dump({"name": name or pid}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return True


def delete_project(pid):
    """删除项目（默认项目不可删）。删除当前激活项目则自动回退到 default。"""
    if pid == DEFAULT_PROJECT_ID:
        return False
    base = _project_base(pid)
    if not os.path.isdir(base):
        return False
    if get_active_project() == pid:
        set_active_project(DEFAULT_PROJECT_ID)
    try:
        shutil.rmtree(base)
    except Exception:
        return False
    return True


def _safe_move(src, dst):
    """移动文件/目录到 dst；dst 为目录时合并子项。用于首次迁移顶层遗留数据。"""
    if not os.path.exists(src):
        return
    if os.path.isdir(src):
        os.makedirs(dst, exist_ok=True)
        for nm in os.listdir(src):
            _safe_move(os.path.join(src, nm), os.path.join(dst, nm))
        try:
            os.rmdir(src)
        except Exception:
            pass
    else:
        parent = os.path.dirname(dst)
        os.makedirs(parent, exist_ok=True)
        if os.path.isdir(dst):
            dst = os.path.join(dst, os.path.basename(src))
        elif os.path.exists(dst):
            try:
                os.remove(dst)
            except Exception:
                pass
        try:
            shutil.move(src, dst)
        except Exception:
            pass


def migrate_default_project():
    """首次启动：把 DATA_DIR 顶层遗留数据搬入 projects/default/，保证旧数据零破坏。"""
    default_base = _project_base(DEFAULT_PROJECT_ID)
    os.makedirs(default_base, exist_ok=True)
    moves = [
        (os.path.join(DATA_DIR, "kb_data"), os.path.join(default_base, "kb_data")),
        (os.path.join(DATA_DIR, "narrations"), os.path.join(default_base, "narrations")),
        (os.path.join(DATA_DIR, "avatar_out"), os.path.join(default_base, "avatar_out")),
        (os.path.join(DATA_DIR, "portraits"), os.path.join(default_base, "avatar_out", "portraits")),
        (os.path.join(DATA_DIR, "slides.json"), os.path.join(default_base, "slides.json")),
        (os.path.join(DATA_DIR, "qa_log.json"), os.path.join(default_base, "qa_log.json")),
        (os.path.join(DATA_DIR, "resource_history.json"), os.path.join(default_base, "resource_history.json")),
    ]
    for src, dst in moves:
        _safe_move(src, dst)


def init_projects():
    """启动时初始化项目系统：确保 projects 目录存在、迁移默认项目、加载激活项目并建好目录。

    每次启动都尝试迁移：migrate_default_project 是幂等的（源不存在即跳过），
    因此即使上次迁移被中断（如进程被杀 / 断电导致数据一半在顶层一半在项目里），
    下次启动也会把剩余部分继续搬完，不会出现"数据被劈成两半再也合不回来"的情况。
    """
    os.makedirs(PROJECTS_DIR, exist_ok=True)
    try:
        migrate_default_project()
    except Exception:
        pass
    pid = get_active_project()
    if not pid or not os.path.isdir(_project_base(pid)):
        pid = DEFAULT_PROJECT_ID
        set_active_project(pid, persist=False)
    _recompute(pid)
    ensure_dirs()
    return pid


if __name__ == "__main__":
    ensure_dirs()
    ok, msgs = sanity_check()
    for m in msgs:
        print(m)
    print("SANITY", "OK" if ok else "FAIL")
