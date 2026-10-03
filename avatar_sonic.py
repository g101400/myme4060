"""avatar_sonic.py —— 扩散式 talking-head 驱动（Sonic，经 ComfyUI HTTP API）。

替代 LivePortrait 的「单图 2D warp」机制：Sonic 用扩散模型把【肖像图 + 音频】「生成」
每一帧，身份保持更好、嘴动更自然，几乎不存在 LivePortrait 那种嘴炸开 / 下半脸拉伸 /
呲牙的扭曲型变形。这是根治分身脸部变形的中期方案（见 kb_src/KB08 第八节）。

前置条件：
  1. 本机 ComfyUI 已在运行，且加载了 ComfyUI_Sonic 节点（本机嵌式 ComfyUI 已内置）。
  2. CUDA GPU 可用（扩散远重于 LivePortrait，CPU 基本不可用）。
  3. 权重齐备：models/sonic/{unet.pth,audio2token.pth,audio2bucket.pth,yoloface_v5m.pt,
     whisper-tiny/,RIFE/flownet.pkl} 与 models/checkpoints/SVD/svd_xt_1_1.safetensors
     （本机均已就位）。

回退策略：调用方（app.py）在 Sonic 不可用或推理失败时，应回退到 avatar.drive_avatar
（LivePortrait），保证分身功能不中断。

接口与 avatar.drive_avatar 对齐：drive_avatar_sonic(image, audio, out_mp4, ...) -> {ok, out_mp4, note}
"""
import os
import io as _io
import json
import time
import uuid
import urllib.request
import urllib.error
import urllib.parse
import traceback

import config


# --------------------------------------------------------------------------- #
# 可用性探测
# --------------------------------------------------------------------------- #
def sonic_available(url=None, timeout=5):
    """ComfyUI 在线且已加载 Sonic 节点（SONICSampler）则返回 True。"""
    url = url or config.SONIC_URL
    try:
        with urllib.request.urlopen(url + "/object_info/SONICSampler", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return isinstance(data, dict) and "SONICSampler" in data
    except Exception:
        return False


def comfyui_up(url=None, timeout=5):
    """仅判断 ComfyUI 服务是否在线。"""
    url = url or config.SONIC_URL
    try:
        with urllib.request.urlopen(url + "/", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# 底层 HTTP 工具
# --------------------------------------------------------------------------- #
def _post_json(url, path, obj, timeout=60):
    body = json.dumps(obj).encode("utf-8")
    req = urllib.request.Request(url + path, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _upload(url, filepath, timeout=180):
    """上传文件到 ComfyUI 的 input 目录（图片 / 音频都走 /upload/image，服务端按字节落盘）。
    返回服务端文件名（含可能的去重后缀），LoadImage / LoadAudio 按此名引用。"""
    boundary = uuid.uuid4().hex
    with open(filepath, "rb") as f:
        data = f.read()
    base = os.path.basename(filepath)
    ext = os.path.splitext(base)[1].lower()
    # 服务端按文件名落到 input 目录；不同分身/并发若用同名文件（如都叫 selfie.jpg，
    # 或应用层固定的 avatar_src.png）会被去重、互相串图。统一加唯一前缀彻底隔离。
    name = "myme_%s_%s" % (uuid.uuid4().hex[:10], base)
    if ext in (".png",):
        ctype = b"image/png"
    elif ext in (".jpg", ".jpeg"):
        ctype = b"image/jpeg"
    else:
        # wav / mp3 / 其它：Content-Type 不影响落盘（服务端只写字节），LoadAudio 按扩展名读
        ctype = b"application/octet-stream"
    crlf = b"\r\n"
    body = b""
    body += ("--" + boundary).encode() + crlf
    body += b'Content-Disposition: form-data; name="image"; filename="' + name.encode() + b'"' + crlf
    body += b"Content-Type: " + ctype + crlf + crlf
    body += data + crlf
    body += ("--" + boundary + "--").encode() + crlf
    req = urllib.request.Request(
        url + "/upload/image", data=body,
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode("utf-8"))
    return resp.get("name") or name


def _view(url, filename, subfolder, ftype, timeout=180):
    """从 ComfyUI 取回生成的媒体文件字节（SaveVideo 落盘后经 /view 回传）。"""
    q = urllib.parse.urlencode({
        "filename": filename or "",
        "subfolder": subfolder or "",
        "type": ftype or "output",
    })
    try:
        with urllib.request.urlopen(url + "/view?" + q, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# 工作流构造（ComfyUI API 格式）
# 节点图完全对应 ComfyUI_Sonic/example_workflows/example.json，仅把 LoadImage/
# LoadAudio 指向上传文件、SaveVideo 前缀设为唯一串、duration 设为音频真实时长。
# --------------------------------------------------------------------------- #
def build_sonic_prompt(img_name, aud_name, prefix, opts):
    # ComfyUI 在 Windows 下列 checkpoint/unet 用反斜杠，归一化避免 value_not_in_list
    ckpt = opts.get("ckpt", config.SONIC_CHECKPOINT).replace("/", os.sep)
    unet = opts.get("unet", config.SONIC_UNET).replace("/", os.sep)
    dtype = opts.get("dtype", config.SONIC_DTYPE)           # fp16 / fp32 / bf16
    min_res = int(opts.get("min_res", config.SONIC_MIN_RES))
    duration = float(opts.get("duration", 10.0))
    expand = float(opts.get("expand_ratio", 0.5))
    steps = int(opts.get("steps", config.SONIC_STEPS))
    dscale = float(opts.get("dynamic_scale", 1.0))
    fps = float(opts.get("fps", config.SONIC_FPS))
    seed = int(opts.get("seed", 0))
    ip_scale = float(opts.get("ip_audio_scale", 1.0))
    use_interframe = bool(opts.get("use_interframe", True))

    return {
        # SVD checkpoint -> MODEL / CLIP_VISION / VAE
        "16": {"class_type": "ImageOnlyCheckpointLoader",
               "inputs": {"ckpt_name": ckpt}},
        # 包成 Sonic 模型 + 权重 dtype
        "14": {"class_type": "SONICTLoader",
               "inputs": {"model": ["16", 0], "sonic_unet": unet,
                          "ip_audio_scale": ip_scale,
                          "use_interframe": use_interframe, "dtype": dtype}},
        # 参考肖像
        "18": {"class_type": "LoadImage", "inputs": {"image": img_name}},
        # 驱动音频
        "19": {"class_type": "LoadAudio", "inputs": {"audio": aud_name}},
        # 预数据处理：音频特征 + 人脸检测 + 潜变量
        "17": {"class_type": "SONIC_PreData",
               "inputs": {"clip_vision": ["16", 1], "vae": ["16", 2],
                          "audio": ["19", 0], "image": ["18", 0],
                          "weight_dtype": ["14", 1],
                          "min_resolution": min_res, "duration": duration,
                          "expand_ratio": expand}},
        # 扩散采样：image + fps
        "15": {"class_type": "SONICSampler",
               "inputs": {"model": ["14", 0], "data_dict": ["17", 0],
                          "seed": seed, "inference_steps": steps,
                          "dynamic_scale": dscale, "fps": fps}},
        # 合成视频（逐帧 + 音频）
        "21": {"class_type": "CreateVideo",
               "inputs": {"images": ["15", 0], "audio": ["19", 0],
                          "fps": ["15", 1]}},
        # 落盘（唯一前缀 -> 便于回传定位）
        # 注：SaveVideo 的 format="mp4" 属 COMFY_DYNAMICCOMBO_V3，会激活依赖输入 codec，
        # 必须显式给出（与官方 example.json 的 h264 一致），否则 /prompt 校验失败。
        "20": {"class_type": "SaveVideo",
               "inputs": {"video": ["21", 0], "filename_prefix": prefix,
                          "format": "mp4", "codec": "h264"}},
    }


def validate_prompt(prompt):
    """离线校验 prompt 图：所有 link 的源节点 / 槽位存在，且终端 SaveVideo 有唯一前缀。"""
    ids = set(prompt.keys())
    for nid, node in prompt.items():
        for k, v in node.get("inputs", {}).items():
            if isinstance(v, list) and len(v) >= 2 and isinstance(v[0], str):
                assert v[0] in ids, "link 源节点缺失: %s -> %s" % (nid, v[0])
    assert prompt["20"]["class_type"] == "SaveVideo"
    assert prompt["20"]["inputs"]["filename_prefix"].startswith("myme_sonic_")
    return True


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def drive_avatar_sonic(image_path, audio_path, out_mp4, url=None, timeout=1800, **opts):
    """给定肖像图 + 音频 -> 扩散式口型同步 mp4（Sonic / ComfyUI）。
    返回 {ok, out_mp4, note}；失败 ok=False 并给出原因（调用方据此回退 LivePortrait）。"""
    url = url or config.SONIC_URL
    res = {"ok": False, "out_mp4": out_mp4, "note": ""}
    tmp_wav = None
    try:
        if not os.path.exists(image_path):
            res["note"] = "肖像图缺失: %s" % image_path
            return res
        os.makedirs(os.path.dirname(os.path.abspath(out_mp4)), exist_ok=True)

        # 1) 音频规整为 wav（复用 LivePortrait 的 prepare_audio，ffmpeg 转码）
        try:
            import avatar as _av
            tmp_wav = out_mp4 + ".sonic.wav"
            wav = _av.prepare_audio(audio_path, tmp_wav)
        except Exception:
            wav = audio_path

        # 2) 真实音频时长 -> duration（Sonic 据此切片）
        duration = float(opts.get("duration", 10.0))
        try:
            import soundfile as sf
            info = sf.info(wav)
            duration = min(float(info.frames) / float(info.samplerate),
                           float(opts.get("max_duration", 60.0)))
        except Exception:
            pass

        # 3) 上传 image + audio 到 ComfyUI input 目录
        img_name = _upload(url, image_path)
        aud_name = _upload(url, wav)

        # 4) 构造并提交 prompt
        prefix = "myme_sonic_%s" % uuid.uuid4().hex[:12]
        prompt = build_sonic_prompt(img_name, aud_name, prefix,
                                    dict(opts, duration=duration))
        client_id = uuid.uuid4().hex
        pr = _post_json(url, "/prompt",
                        {"prompt": prompt, "client_id": client_id}, timeout=60)
        prompt_id = pr.get("prompt_id")
        if not prompt_id:
            res["note"] = "ComfyUI 未返回 prompt_id: %s" % pr
            return res

        # 5) 轮询 history 并取回输出视频
        video_bytes = _wait_and_fetch(url, prompt_id, save_node="20", timeout=timeout)
        if not video_bytes:
            res["note"] = "Sonic 推理完成但未取到输出视频（history 无 SaveVideo 结果，请检查 ComfyUI 日志）"
            return res

        # 6) 落盘
        with open(out_mp4, "wb") as f:
            f.write(video_bytes)
        if os.path.getsize(out_mp4) > 2000:
            res["ok"] = True
            res["note"] = "Sonic 扩散式驱动（ComfyUI %s，duration=%.1fs）" % (url, duration)
        else:
            res["note"] = "Sonic 输出文件异常（过小，可能非视频）"
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "ignore")[:600]
        except Exception:
            body = ""
        res["note"] = "ComfyUI HTTP 错误 %s: %s" % (e.code, body)
    except Exception as e:
        res["note"] = "Sonic 推理异常：%s" % e
        traceback.print_exc()
    finally:
        if tmp_wav and os.path.exists(tmp_wav):
            try:
                os.remove(tmp_wav)
            except Exception:
                pass
    return res


def _wait_and_fetch(url, prompt_id, save_node="20", timeout=1800):
    """轮询 /history/<prompt_id>，取到 SaveVideo 输出后经 /view 回传视频字节。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/history/" + prompt_id, timeout=30) as r:
                h = json.loads(r.read().decode("utf-8"))
        except Exception:
            time.sleep(3)
            continue
        item = h.get(prompt_id)
        if item and item.get("outputs"):
            outs = item["outputs"].get(save_node)
            files = []
            if isinstance(outs, dict):
                for v in outs.values():
                    if isinstance(v, list):
                        files.extend(v)
            for f in files:
                data = _view(url, f.get("filename"), f.get("subfolder", ""),
                             f.get("type", "output"))
                if data:
                    return data
            # 若 SaveVideo 未记录（极少数版本），尝试从整个 outputs 里捞 VIDEO 类型
            for node_id, node_out in item["outputs"].items():
                if isinstance(node_out, dict):
                    for v in node_out.values():
                        if isinstance(v, list):
                            for f in v:
                                if isinstance(f, dict) and f.get("type") in ("output", "input"):
                                    d = _view(url, f.get("filename"),
                                              f.get("subfolder", ""), f.get("type", "output"))
                                    if d:
                                        return d
        time.sleep(3)
    return None


# --------------------------------------------------------------------------- #
# 可选：拉起本机嵌式 ComfyUI（含 Sonic）。默认不自动调用，避免意外后台进程；
# 需用时由调用方显式触发（如首次检测到 ComfyUI 未在线）。
# --------------------------------------------------------------------------- #
def start_comfyui(timeout=120):
    """尝试启动本机嵌式 ComfyUI（run_nvidia_gpu.bat），并等待 API 就绪。返回是否成功。"""
    if comfyui_up(timeout=3):
        return True
    root = getattr(config, "COMFYUI_ROOT", "")
    if not root or not os.path.isdir(root):
        return False
    launcher = None
    for cand in ("run_nvidia_gpu.bat", "run_nvidia_gpu_fast_fp16_accumulation.bat",
                "run_cpu.bat"):
        p = os.path.join(root, cand)
        if os.path.exists(p):
            launcher = p
            break
    if not launcher:
        return False
    try:
        import subprocess
        subprocess.Popen(["cmd", "/c", launcher], cwd=root,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True)
    except Exception:
        return False
    # 等待 API 就绪
    deadline = time.time() + timeout
    while time.time() < deadline:
        if comfyui_up(timeout=3):
            return True
        time.sleep(3)
    return False
