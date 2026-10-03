# coding: utf-8
"""
One-time converter: turn ComfyUI's LivePortrait .safetensors weights into the
.pth layout that LivePortrait's src/utils/helper.load_model expects.

  base_models/appearance_feature_extractor.pth
  base_models/motion_extractor.pth
  base_models/spade_generator.pth
  base_models/warping_module.pth
  retargeting_models/stitching_retargeting_module.pth   (nested dict)

The stitching .safetensors is flattened as retarget_{eye,mouth,shoulder}_module.*
and must be regrouped into a dict with keys retarget_{eye,mouth,shoulder}.

landmark.onnx is copied as-is.
InsightFace (buffalo_l) is NOT copied -- the service points insightface_root
directly at the existing ComfyUI models folder.
"""

import os
import glob
import shutil

import safetensors.torch as st
import torch

COMFY_LP = r"D:\ComfyUI_Mie_2026_V8.0\ComfyUI\models\liveportrait"
OUT_ROOT = r"D:\Users\Claw\myme\LivePortrait\pretrained_weights\liveportrait"

BASE_MAP = {
    "appearance_feature_extractor.safetensors": "base_models/appearance_feature_extractor.pth",
    "motion_extractor.safetensors": "base_models/motion_extractor.pth",
    "spade_generator.safetensors": "base_models/spade_generator.pth",
    "warping_module.safetensors": "base_models/warping_module.pth",
}


def ensure_dir(p):
    os.makedirs(os.path.dirname(p), exist_ok=True)


def convert_base(name, rel):
    src = os.path.join(COMFY_LP, name)
    dst = os.path.join(OUT_ROOT, rel)
    ensure_dir(dst)
    print(f"[base] {name} -> {rel}")
    sd = st.load_file(src, device="cpu")
    # strip any (unlikely) DDP 'module.' prefix
    sd = {k[len("module."):] if k.startswith("module.") else k: v for k, v in sd.items()}
    torch.save(sd, dst)
    print(f"       saved {len(sd)} tensors")


def convert_stitching():
    src = os.path.join(COMFY_LP, "stitching_retargeting_module.safetensors")
    dst = os.path.join(OUT_ROOT, "retargeting_models/stitching_retargeting_module.pth")
    ensure_dir(dst)
    print("[stitch] stitching_retargeting_module.safetensors -> retargeting_models/stitching_retargeting_module.pth")
    sd = st.load_file(src, device="cpu")
    groups = {"retarget_shoulder": {}, "retarget_mouth": {}, "retarget_eye": {}}
    for k, v in sd.items():
        # k = "retarget_shoulder_module.mlp.0.bias"
        prefix, rest = k.split(".", 1)
        name = prefix.replace("_module", "")
        groups[name][rest] = v
    for g, sub in groups.items():
        print(f"       {g}: {len(sub)} tensors")
    torch.save(groups, dst)
    print("       saved nested dict")


def copy_landmark():
    src = os.path.join(COMFY_LP, "landmark.onnx")
    dst = os.path.join(OUT_ROOT, "landmark.onnx")
    ensure_dir(dst)
    if os.path.exists(src):
        shutil.copyfile(src, dst)
        print(f"[landmark] copied {src} -> {dst}")
    else:
        print(f"[landmark] WARNING missing {src}")


def main():
    for name, rel in BASE_MAP.items():
        convert_base(name, rel)
    convert_stitching()
    copy_landmark()
    print("\nDONE. Weights ready under:")
    print("  " + OUT_ROOT)


if __name__ == "__main__":
    main()
