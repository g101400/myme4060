# -*- coding: utf-8 -*-
"""
myme.avatar — 数字人（LivePortrait 音频驱动唇形，真正跑通）
------------------------------------------------------------------
把快手开源 LivePortrait 接入"热服务链路"：用炎冰 TTS 声音驱动肖像口型同步，
产出"声音复刻 + 视频数字人"的 mp4。

实现要点（与官方 main 分支的差异，已实测约束）：
  • 官方 LivePortrait(main) 仅支持"视频驱动"，无音频驱动代码/权重；
  • 因此这里用 LivePortrait 自带的"唇形重定向(lip retargeting)"能力，把 TTS 音频的
    音量包络映射成"张嘴比例"，逐帧驱动 retarget_lip → stitching → warp_decode → paste_back，
    实现真正的"音频驱动口型"（嘴型随炎冰声音开合，而非跟随某段驱动视频）。
  • 人脸检测绕开 insightface（其依赖的 protobuf 与本地 TTS 音频栈 descript-audiotools
    冲突，强行升级会破坏声纹复刻）。改用 OpenCV 内置 haarcascade 检测 + 官方 landmark.onnx
    精修 203 关键点，全程只依赖 onnxruntime(CPU) + torch(GPU)，不碰 insightface。

依赖：
  - LivePortrait 源码包（LivePortrait/src，已 git clone 并加了 src/__init__.py）
  - 权重：convert_lp_weights.py 已把 ComfyUI 的 .safetensors 转成 LivePortrait 期望的 .pth
  - 嵌式 python：torch(+cu130) / onnxruntime / cv2 / imageio / soundfile

运行示例：
  python avatar.py --drive --audio narrations/answer.wav --image avatar_out/portrait.png --out avatar_out/clip.mp4
  python avatar.py --prepare-only --audio in.wav --out out_16k.wav   # 仅做音频预处理
"""
import os
import sys
import json
import time
import argparse
import subprocess
import threading

# 嵌式 python 的 _pth 不会自动把脚本所在目录加入 sys.path，须手动加入才能 import 同目录模块。
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import config
from config import (FFMPEG, AVATAR_IMAGE, AVATAR_IMAGE_DEFAULT,
                    LIVEPORTRAIT_REPO, LANDMARK_ONNX, AVATAR_RES, AVATAR_AUDIO_SR,
                    AVATAR_AUDIO_PEAK, EMBEDDED_PY,
                    AVATAR_DRIVE_MULT, AVATAR_LIP_AMP, AVATAR_LIP_MAX,
                    AVATAR_MOTION_DEFAULT, AVATAR_SMILE_MULT)

# 全局单例（常驻服务只加载一次模型）
DRIVER = None
LOCK = threading.Lock()


def ensure_avatar_dir():
    os.makedirs(config.DIR_AVATAR, exist_ok=True)


def prepare_audio(in_wav, out_wav, sr=AVATAR_AUDIO_SR, peak=AVATAR_AUDIO_PEAK):
    """音频预处理：重采样到 sr，单声道，峰值归一化到 peak dB，导出 WAV 无损。
    这是唇形自然的关键一步——必须使用 WAV，禁止 MP3 二次压缩。"""
    if not os.path.exists(FFMPEG):
        raise RuntimeError(f"未找到 ffmpeg: {FFMPEG}")
    cmd = [FFMPEG, "-y", "-i", in_wav, "-ar", str(sr), "-ac", "1",
           "-af", f"loudnorm=I=-16:TP={peak}:LRA=11", "-f", "wav", out_wav]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0 or not os.path.exists(out_wav):
        raise RuntimeError(f"音频预处理失败:\n{r.stderr[-400:]}")
    return out_wav


class LivePortraitDriver:
    """加载 LivePortrait 推理模型（F/M/W/G/S）一次并常驻；用音频驱动肖像口型。"""

    def __init__(self, repo=LIVEPORTRAIT_REPO, fps=25, use_half=True, force_cpu=False):
        # —— 延迟导入重依赖，保证本模块在系统 python(app.py) 中也能安全 import ——
        import numpy as np
        import cv2; cv2.setNumThreads(0); cv2.ocl.setUseOpenCL(False)
        self.np = np
        self.cv2 = cv2
        self.repo = repo
        # 导入 src 包需要把"src 的父目录(LivePortrait)"加入 sys.path，而非 src 自身
        if repo not in sys.path:
            sys.path.insert(0, repo)

        from src.config.inference_config import InferenceConfig
        from src.config.crop_config import CropConfig
        from src.live_portrait_wrapper import LivePortraitWrapper
        from src.utils.human_landmark_runner import LandmarkRunner
        from src.utils.camera import get_rotation_matrix
        from src.utils.retargeting_utils import calc_lip_close_ratio, calc_eye_close_ratio

        self.get_rotation_matrix = get_rotation_matrix
        self.calc_lip_close_ratio = calc_lip_close_ratio
        self.calc_eye_close_ratio = calc_eye_close_ratio

        inf_cfg = InferenceConfig()
        if force_cpu:
            inf_cfg.flag_force_cpu = True
        inf_cfg.flag_use_half_precision = use_half and (not force_cpu)
        crop_cfg = CropConfig()
        # insightface 不可用，这里只用 landmark.onnx（onnxruntime CPU）精修关键点，
        # 因此无需 insightface_root / landmark 默认路径（已指向转换后的 landmark.onnx）。
        self.wrapper = LivePortraitWrapper(inference_cfg=inf_cfg)

        # —— 轻量人脸检测 + 203 关键点精修（替代 insightface）——
        self.cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        if self.cascade.empty():
            # 个别 cv2 构建不含该级联，则退化为"用整图中心裁剪"（对正脸仍可用）
            self.cascade = None
        self.lm_runner = LandmarkRunner(ckpt_path=LANDMARK_ONNX, onnx_provider="cpu")
        self.crop_cfg = crop_cfg
        self.inf_cfg = inf_cfg
        self.fps = fps

    # ---------- 人脸裁剪（绕开 insightface） ----------
    @staticmethod
    def _five_from_landmark(lmk):
        """从 landmark.onnx 精修后的 203 关键点取标准 5 点（顺序对齐 crop_image）：
        [左眼中心, 右眼中心, 鼻尖, 左嘴角, 右嘴角]。
        lmk 可传 (203,2) 或 (1,203,2)；前 68 点遵循 iBUG 68 点约定。
        这是消除"脸变形"的关键一步：粗检测给的 5 点会带旋转/平移误差，
        用精修 203 点反推的 5 点做对齐裁剪，源图姿态才稳。"""
        np = __import__("numpy")
        if lmk.ndim == 3:
            lmk = lmk[0]
        le = lmk[36:42].mean(axis=0)      # 左眼（图中右眼）
        re = lmk[42:48].mean(axis=0)      # 右眼（图中左眼）
        nose = lmk[30]
        ml = lmk[48]                       # 左嘴角
        mr = lmk[54]                       # 右嘴角
        return np.array([le, re, nose, ml, mr], dtype=np.float32)

    def crop_source_image(self, img_rgb):
        cv2 = self.cv2; np = self.np
        gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)
        faces = []
        if self.cascade is not None:
            # 收紧检测：scaleFactor 1.12、minNeighbors 6、minSize 80，减少误检；
            # 只保留「面积最大且最靠中心」的脸，避免小脸/侧脸导致裁剪歪斜——这正是
            # 之前"脸部变形/肿胀/双下颌"的隐藏根因（裁剪框歪了，整张脸被重新解算）。
            cand = self.cascade.detectMultiScale(gray, 1.12, 6, minSize=(80, 80))
            if len(cand):
                ch, cw = gray.shape[:2]
                cx, cy = cw / 2.0, ch / 2.0
                def _score(f):
                    x, y, w, h = f
                    fx, fy = x + w / 2.0, y + h / 2.0
                    d = ((fx - cx) ** 2 + (fy - cy) ** 2) ** 0.5
                    return float(f[2]) * float(f[3]) - 0.002 * d   # 面积优先，轻微偏向居中
                faces = np.array([max(cand, key=_score)])
        if len(faces) == 0:
            # 退路：整图中心区域作为人脸框（仅对"已裁好的人脸图"有效）
            h, w = img_rgb.shape[:2]
            faces = np.array([[int(w*0.15), int(h*0.12), int(w*0.7), int(h*0.76)]])
        x, y, w, h = faces[0]
        # 由 bbox 派生初值 5 点（两眼/鼻/两嘴角），供 landmark.onnx 精修
        five = np.array([
            [x + w*0.35, y + h*0.40],
            [x + w*0.65, y + h*0.40],
            [x + w*0.50, y + h*0.62],
            [x + w*0.35, y + h*0.78],
            [x + w*0.65, y + h*0.78],
        ], dtype=np.float32)
        from src.utils.crop import crop_image
        # 第一遍：用粗 5 点裁出初始 256，喂给 landmark.onnx 精修 203 点
        ret0 = crop_image(img_rgb, five, dsize=self.crop_cfg.dsize,
                          scale=self.crop_cfg.scale, vy_ratio=self.crop_cfg.vy_ratio,
                          flag_do_rot=True)
        lmk = None
        try:
            lmk = self.lm_runner.run(img_rgb, five)            # 203x2（或 1,203,2）
            five2 = self._five_from_landmark(lmk)              # 用精修 203 点反推精确 5 点
            # 第二遍：以精确 5 点再对齐裁剪一次，消除粗检测的旋转/平移误差。
            ret = crop_image(img_rgb, five2, dsize=self.crop_cfg.dsize,
                             scale=self.crop_cfg.scale, vy_ratio=self.crop_cfg.vy_ratio,
                             flag_do_rot=True)
            ret["lmk_crop"] = lmk
        except Exception:
            # landmark 异常则回退第一遍（粗 5 点）结果，保证不崩
            ret = ret0
            try:
                ret["lmk_crop"] = self.lm_runner.run(img_rgb, five)
            except Exception:
                ret["lmk_crop"] = None
        ret["img_crop_256x256"] = cv2.resize(ret["img_crop"], (256, 256), interpolation=cv2.INTER_AREA)
        return ret

    # ---------- 音频 -> 张嘴比例（逐帧） ----------
    def _audio_lip_ratios(self, wav_path, c_s_lip, amp=None):
        """音频能量包络 -> 逐帧「唇开合比」。

        ⚠ amp 必须克制：rettargeting MLP 只在训练分布内可靠，超过就外推，
          表现就是嘴部炸开、连带下半张脸一起变形。
          0.55/0.7（初版）→ 0.32/0.42（第二批）→ 0.15/0.22（第三批，口型极简）。
          用户实测薄唇中老年肖像在 0.32/0.42 下仍呲牙变形，第三批再砍半；
          需要更明显的口型时可设 MYME_LP_LIP_AMP=0.3 恢复。
        """
        np = self.np
        amp = AVATAR_LIP_AMP if amp is None else float(amp)
        import soundfile as sf
        data, sr = sf.read(wav_path, dtype="float32", always_2d=False)
        if data.ndim > 1:
            data = data[:, 0]
        dur = len(data) / sr
        n_frames = int(dur * self.fps) + 1  # 略多 1 帧，保证音频完整（-shortest 截断）
        win = max(1, int(sr / self.fps))
        rms = np.zeros(n_frames, dtype=np.float32)
        for i in range(n_frames):
            a = data[i*win:(i+1)*win]
            if a.size:
                rms[i] = np.sqrt(np.mean(a.astype(np.float32) ** 2))
        m = rms.max()
        if m > 1e-6:
            rms = rms / m
        rms = np.clip(rms, 0, 1) ** 0.7  # 提升小音量可见度
        # 轻平滑（移动平均），避免嘴部抖动
        k = 3
        rms = np.convolve(rms, np.ones(k) / k, mode="same")
        # 只在「闭口基线」之上加开合量，并严格夹在训练分布内
        lo = float(c_s_lip)
        hi = min(1.0, lo + AVATAR_LIP_MAX)
        ratios = lo + amp * rms
        ratios = np.clip(ratios, lo, max(lo, hi))
        return ratios, n_frames, dur, rms

    # ---------- 自然微动规划：眨眼 / 眼珠 / 头部 / 微表情 ----------
    def _motion_plan(self, n_frames, fps, energy=None, amp=1.0, seed=20240918):
        """生成逐帧的"活人感"微动参数（全部为极小幅度，避免夸张/失真）。

        返回 dict，各字段均为长度 n_frames 的数组：
          pitch/yaw/roll  头部三轴微摆（度，约 ±1~2°）
          breath          整体极轻微上下起伏
          gaze            眼珠注视方向 (gx, gy)，分段扫视 + 平滑
          blink           眨眼包络 0~1（单次约 0.14s）
          smile/brow      嘴角/眉毛的微表情强度（与语音能量轻微相关）
        """
        np = self.np
        fps = float(fps or 25)
        t = np.arange(n_frames, dtype=np.float32) / fps
        rng = np.random.default_rng(int(seed) % (2 ** 32))
        amp = float(np.clip(amp, 0.0, 2.0))

        def osc(period, a, ph):
            # 三个不同周期叠加，避免机械的正弦感
            return a * (np.sin(2 * np.pi * t / period + ph)
                        + 0.30 * np.sin(2 * np.pi * t / (period * 0.41) + ph * 1.7)
                        + 0.15 * np.sin(2 * np.pi * t / (period * 0.17) + ph * 2.3))

        pitch = osc(4.3, 1.5, 0.4) * amp     # 点头
        yaw = osc(5.9, 2.0, 1.1) * amp       # 左右微转
        roll = osc(7.3, 1.0, 2.2) * amp      # 轻微歪头
        breath = 0.0015 * np.sin(2 * np.pi * t / 4.2 + 0.9) * amp

        # —— 眼珠：分段扫视（saccade）+ 平滑过渡 ——
        gaze = np.zeros((n_frames, 2), dtype=np.float32)
        i = 0
        while i < n_frames:
            hold = int(rng.integers(int(fps * 0.7), int(fps * 2.4)))
            j = min(n_frames, i + max(2, hold))
            gaze[i:j, 0] = float(rng.uniform(-1.0, 1.0))
            gaze[i:j, 1] = float(rng.uniform(-0.55, 0.55))
            i = j
        k = max(3, int(fps * 0.12))
        ker = np.ones(k, dtype=np.float32) / k
        gaze[:, 0] = np.convolve(gaze[:, 0], ker, mode="same")
        gaze[:, 1] = np.convolve(gaze[:, 1], ker, mode="same")
        gaze *= amp

        # —— 眨眼：随机间隔（1.6~4.2s），单次 ~0.14s 的闭-开包络 ——
        blink = np.zeros(n_frames, dtype=np.float32)
        i = int(rng.integers(int(fps * 0.8), int(fps * 2.6)))
        while i < n_frames:
            dur = max(3, int(round(0.14 * fps)))
            for kk in range(dur):
                if i + kk < n_frames:
                    blink[i + kk] = float(np.sin(np.pi * (kk + 0.5) / dur))
            i += dur + int(rng.integers(int(fps * 1.6), int(fps * 4.2)))

        # —— 微表情：说话时略带笑意与眉动，静音时更平静 ——
        # v2.6.1：smile 与语音能量耦合是「呲牙假笑/嘴角涂抹」的主要推手（实测中老年
        # 薄唇肖像尤其严重），整体乘 AVATAR_SMILE_MULT（默认 0.25，0=关闭）压回去。
        e = np.zeros(n_frames, dtype=np.float32) if energy is None else np.asarray(energy, dtype=np.float32)
        wn = max(3, int(fps * 0.2))
        esm = np.convolve(e, np.ones(wn) / wn, mode="same") if n_frames > 4 else e
        smile = AVATAR_SMILE_MULT * (0.05 + 0.13 * esm + 0.04 * np.sin(2 * np.pi * t / 6.1 + 0.3)) * amp
        brow = (0.18 * np.sin(2 * np.pi * t / 5.3 + 1.4) + 0.35 * esm) * amp
        return dict(pitch=pitch, yaw=yaw, roll=roll, breath=breath,
                    gaze=gaze, blink=blink, smile=smile, brow=brow)

    # ---------- 主推理：音频驱动口型 + 自然微动 ----------
    def drive(self, audio_path, image_path, out_mp4, fps=None, motion=None, seed=None):
        np = self.np; cv2 = self.cv2
        from src.utils.io import load_image_rgb, resize_to_limit
        from src.utils.crop import prepare_paste_back, paste_back
        import imageio
        import torch

        fps = fps or self.fps
        if not os.path.exists(image_path):
            image_path = AVATAR_IMAGE_DEFAULT
        if not os.path.exists(image_path):
            raise RuntimeError(f"肖像图缺失且无默认演示图: {image_path}")
        if not os.path.exists(audio_path):
            raise RuntimeError(f"音频缺失: {audio_path}")

        img_rgb = load_image_rgb(image_path)
        img_rgb = resize_to_limit(img_rgb, 1280, 2)
        crop_info = self.crop_source_image(img_rgb)
        if crop_info is None:
            raise RuntimeError("未检测到人脸，请换一张正面平视高清图")
        source_lmk = crop_info["lmk_crop"]                 # 203x2
        img_crop_256 = crop_info["img_crop_256x256"]

        w = self.wrapper
        I_s = w.prepare_source(img_crop_256)
        x_s_info = w.get_kp_info(I_s)
        f_s = w.extract_feature_3d(I_s)
        x_s = w.transform_keypoint(x_s_info)

        mask_ori = prepare_paste_back(
            w.inference_cfg.mask_crop, crop_info["M_c2o"],
            dsize=(img_rgb.shape[1], img_rgb.shape[0]))

        # 源图自然张嘴 / 睁眼比例（静音、不眨眼时保持原样）
        # calc_*_close_ratio 约定传入 (1, N, 2) 三维数组（lmk[:,idx] 取第 idx 个关键点）
        c_s_lip = float(self.calc_lip_close_ratio(source_lmk[None])[0, 0])
        c_s_eye = float(self.calc_eye_close_ratio(source_lmk[None])[0].mean())
        ratios, n_frames, _, rms = self._audio_lip_ratios(audio_path, c_s_lip)

        # —— 静音唇形归一化（对齐官方 flag_normalize_lip）——
        # 先把「唇开合比 = 源值」时的 retarget 输出也算出来，逐帧做差。
        # 不做这一步，静止帧也会带一个恒定唇形偏移 → 下半张脸被顶起来（双下颌/肿胀感）。
        _combined0 = w.calc_combined_lip_ratio([[c_s_lip]], source_lmk)
        lip_delta_base = w.retarget_lip(x_s, _combined0)

        # —— 微动规划（眨眼 / 眼珠 / 头部微摆 / 微表情）——
        amp = float(motion if motion is not None else AVATAR_MOTION_DEFAULT)
        if seed is None:
            seed = abs(hash(os.path.basename(audio_path))) % 100000
        mp = self._motion_plan(n_frames, fps, energy=rms, amp=amp, seed=seed) if amp > 0.001 else None
        if mp is not None:
            x_c_s = x_s_info["kp"]            # 规范空间关键点 (1,k,3)
            exp_base = x_s_info["exp"]        # 表情基底
            scale_new = x_s_info["scale"]     # (1,1)
            t_new = x_s_info["t"]             # (1,3)
            R_s = self.get_rotation_matrix(x_s_info["pitch"], x_s_info["yaw"], x_s_info["roll"])
            dev = x_c_s.device
            dtyp = x_c_s.dtype

        frames = []
        t0 = time.time()
        mult = float(AVATAR_DRIVE_MULT)
        for i in range(n_frames):
            # 1) 口型：音频能量 -> 张嘴比例（减去静音基线，保证静音时零偏移）
            combined = w.calc_combined_lip_ratio([[float(ratios[i])]], source_lmk)
            lip_delta = w.retarget_lip(x_s, combined) - lip_delta_base

            eyes_delta = None
            if mp is None:
                x_d = x_s.clone()
            else:
                # 2) 表情基底：眼珠转动 + 微笑 + 眉动（沿用官方 retarget 面板系数）
                delta_new = exp_base.clone()
                gx, gy = float(mp["gaze"][i, 0]), float(mp["gaze"][i, 1])
                if gx > 0:
                    delta_new[0, 11, 0] += gx * 0.0007
                    delta_new[0, 15, 0] += gx * 0.001
                else:
                    delta_new[0, 11, 0] += gx * 0.001
                    delta_new[0, 15, 0] += gx * 0.0007
                delta_new[0, 11, 1] += gy * -0.001
                delta_new[0, 15, 1] += gy * -0.001
                s = float(mp["smile"][i])
                if abs(s) > 1e-4:
                    delta_new[0, 20, 1] += s * -0.01
                    delta_new[0, 14, 1] += s * -0.02
                    delta_new[0, 17, 1] += s * 0.0065
                    delta_new[0, 17, 2] += s * 0.003
                    delta_new[0, 13, 1] += s * -0.00275
                    delta_new[0, 16, 1] += s * -0.00275
                    delta_new[0, 3, 1] += s * -0.0035
                    delta_new[0, 7, 1] += s * -0.0035
                b = float(mp["brow"][i])
                if abs(b) > 1e-4:
                    if b > 0:
                        delta_new[0, 1, 1] += b * 0.001
                        delta_new[0, 2, 1] += b * -0.001
                    else:
                        delta_new[0, 1, 0] += b * -0.001
                        delta_new[0, 2, 0] += b * 0.001
                        delta_new[0, 1, 1] += b * 0.0003
                        delta_new[0, 2, 1] += b * -0.0003
                # 3) 头部微摆：★ 必须「源姿态 R_s × 小摆动 R_d」，不能拿 R_d 覆盖源姿态。
                #    早先写成 (R_d @ R_s^T) @ R_s ≡ R_d，等于把人物的原始头姿完全抹掉，
                #    每帧都按"小角度重算一张脸"→ 脸型/下颌被重新解算，就是用户看到的
                #    「脸部变形严重、好像脸部肿胀、双下颌多嘴巴子」。
                R_d = self.get_rotation_matrix(
                    torch.tensor([float(mp["pitch"][i])], device=dev, dtype=dtyp),
                    torch.tensor([float(mp["yaw"][i])], device=dev, dtype=dtyp),
                    torch.tensor([float(mp["roll"][i])], device=dev, dtype=dtyp))
                R_d_new = R_d @ R_s
                x_d = scale_new * (x_c_s @ R_d_new + delta_new)
                x_d[..., 0:2] += t_new[..., None, 0:2]
                x_d[..., 1] += float(mp["breath"][i])          # 呼吸起伏（极轻）
                # 4) 眨眼：改变目标睁眼比例（比例越小越闭）
                blink = float(mp["blink"][i])
                if blink > 0.02:
                    c_d_eye = c_s_eye * (1.0 - 0.78 * blink)
                    eyes_delta = w.retarget_eye(
                        x_s, w.calc_combined_eye_ratio([[float(c_d_eye)]], source_lmk))

            # 5) 口型 + 眨眼叠加到目标关键点
            x_d = x_d + lip_delta
            if eyes_delta is not None:
                x_d = x_d + eyes_delta
            # 6) 缝合：非驱动区域回贴源图，避免整脸被重建
            x_d = w.stitching(x_s, x_d)
            # 7) ★ 驱动强度收敛（对齐官方 driving_multiplier）：
            #    所有偏离源的量按 mult 压回去，是抑制"夸张/变形"最有效的一步。
            x_d = x_s + (x_d - x_s) * mult
            out = w.warp_decode(f_s, x_s, x_d)
            I_p = w.parse_output(out["out"])[0]
            I_p_pstbk = paste_back(I_p, crop_info["M_c2o"], img_rgb, mask_ori)
            frames.append(I_p_pstbk)
        gen = time.time() - t0

        # 写无声视频（imageio 自带 ffmpeg，避免依赖系统 ffmpeg 的编码器差异）
        silent = out_mp4 + ".silent.mp4"
        os.makedirs(os.path.dirname(os.path.abspath(out_mp4)), exist_ok=True)
        writer = imageio.get_writer(silent, fps=fps, codec="libx264", quality=None,
                                    ffmpeg_params=["-crf", "18"], pixelformat="yuv420p",
                                    macro_block_size=2)
        for f in frames:
            writer.append_data(f)
        writer.close()

        # 混流炎冰声音（系统 ffmpeg，绝对路径）
        cmd = [FFMPEG, "-y", "-i", silent, "-i", audio_path,
               "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-shortest", out_mp4]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if not (os.path.exists(out_mp4) and os.path.getsize(out_mp4) > 0):
            raise RuntimeError(f"视频合成失败:\n{r.stderr[-400:]}")

        try:
            os.remove(silent)
        except Exception:
            pass
        feat = "口型+眨眼+眼珠+微表情+头部微摆" if mp is not None else "仅口型"
        return {"ok": True, "out_mp4": out_mp4,
                # 不写死人名：本函数是通用驱动，声纹由调用方指定（炎冰/金子/任意分身）
                "note": f"音频驱动数字人完成（克隆声纹 + LivePortrait，{feat}），{n_frames}帧/{gen:.1f}s",
                "frames": n_frames, "gen": round(gen, 1), "motion": amp}


def get_driver(repo=LIVEPORTRAIT_REPO, fps=25, force_cpu=False):
    global DRIVER
    if DRIVER is None:
        DRIVER = LivePortraitDriver(repo=repo, fps=fps, force_cpu=force_cpu)
    return DRIVER


def drive_avatar(image, audio, out_mp4, resolution=AVATAR_RES, prepare=True,
                liveportrait_dir=None, force_cpu=False, motion=None):
    """兼容入口：优先常驻服务；否则冷启动嵌式 python 子进程跑 LivePortrait 驱动。
    motion：微动强度（1.0=自然，0=仅口型）。
    返回 dict: {ok, out_mp4, prepared_audio, note}。模型只加载一次（常驻）。"""
    ensure_avatar_dir()
    # 音频预处理（始终执行，保证喂给模型的音频合规）
    prepared = os.path.join(config.DIR_AVATAR, "driving_prepared.wav")
    if prepare:
        try:
            prepared = prepare_audio(audio, prepared)
        except Exception as e:
            prepared = audio
            note_prep = f"音频预处理跳过：{e}"
    else:
        prepared = audio
        note_prep = ""
    res = {"ok": False, "out_mp4": out_mp4, "prepared_audio": prepared, "note": note_prep or ""}
    # 冷启动子进程：用嵌式 python 直接调用本模块的驱动（绕过 insightface）
    if not os.path.exists(EMBEDDED_PY):
        res["note"] = (res["note"] + "\n") if res["note"] else ""
        res["note"] += f"嵌式 python 缺失: {EMBEDDED_PY}"
        return res
    _self = config.svc_script("avatar.py")
    cmd = [EMBEDDED_PY, _self, "--drive",
           "--audio", prepared, "--image", image, "--out", out_mp4,
           "--motion", str(motion)]
    if force_cpu:
        cmd.append("--cpu")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       cwd=os.path.dirname(_self))
    if r.returncode == 0 and os.path.exists(out_mp4):
        res["ok"] = True
    else:
        res["ok"] = False
        res["note"] = (res["note"] + "\nLivePortrait 推理失败：\n" + (r.stderr or r.stdout)[-600:]).strip()
    return res


def main():
    ap = argparse.ArgumentParser(description="数字人音频驱动（LivePortrait，绕开 insightface）")
    ap.add_argument("--drive", action="store_true", help="执行一次音频驱动（嵌式 python）")
    ap.add_argument("--audio", help="驱动音频（TTS 产出 wav）")
    ap.add_argument("--image", default=AVATAR_IMAGE, help="肖像图（正面平视高清）")
    ap.add_argument("--out", default=os.path.join(config.DIR_AVATAR, "clip.mp4"), help="输出视频")
    ap.add_argument("--res", default=AVATAR_RES, help="分辨率 w,h（默认 688,368，实际按肖像原图）")
    ap.add_argument("--fps", type=int, default=25)
    ap.add_argument("--motion", type=float, default=1.0,
                    help="微动强度：1.0=自然（眨眼/眼珠/微表情/头部微摆），0=仅口型")
    ap.add_argument("--cpu", action="store_true", help="强制 CPU 推理")
    ap.add_argument("--prepare-only", action="store_true", help="仅做音频预处理")
    args = ap.parse_args()

    if args.prepare_only:
        out = prepare_audio(args.audio, args.out)
        print("✅ 音频预处理完成:", out)
        return

    if args.drive:
        if not args.audio:
            print("[!] --drive 需要 --audio"); return
        drv = get_driver(fps=args.fps, force_cpu=args.cpu)
        with LOCK:
            r = drv.drive(args.audio, args.image, args.out, fps=args.fps, motion=args.motion)
        if r.get("ok"):
            print("✅ 数字人视频:", r["out_mp4"], "|", r.get("note", ""))
        else:
            print("[!] 失败:", r.get("note", ""))
        return

    # 默认：提示用法
    print("用法：")
    print("  python avatar.py --drive --audio <wav> --image <肖像> --out <mp4>")
    print("  python avatar.py --prepare-only --audio <wav> --out <wav>")


if __name__ == "__main__":
    main()
