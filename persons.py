# -*- coding: utf-8 -*-
"""
myme.persons — 数字分身管理（多人 × 每人多形象）

数据文件：<当前项目>/avatar_out/portraits/persons.json
结构：
{
  "default": "yanbing",
  "persons": [
    {"id":"yanbing","name":"炎冰","note":"",
     "avatars":[{"id":"selfie","name":"日常·炎冰","scene":"公众号科普","file":"selfie.jpg"}]}
  ]
}
兼容：若 persons.json 不存在，自动从旧的 portraits.json 迁移。

注意：路径随「当前激活项目」动态变化（config.PORTRAITS_DIR / config.PERSONS_JSON /
config.PORTRAITS_JSON），因此一律经 config.X 读取，禁止在 import 期把路径绑成常量
（否则切换项目后仍读写旧项目位置）。
"""
import os
import re
import json
import uuid
import config


def _load_raw():
    if os.path.exists(config.PERSONS_JSON):
        try:
            with open(config.PERSONS_JSON, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _migrate_from_legacy():
    """从旧 portraits.json（扁平 persona 列表）迁移为单人。"""
    legacy = config.PORTRAITS_JSON
    if not os.path.exists(legacy):
        return None
    try:
        with open(legacy, "r", encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        return None
    raw = old.get("personas", [])
    # 旧格式可能是 list[{id,file,...}]，也可能已被写成 dict{id:{file,...}}
    if isinstance(raw, dict):
        items = [dict(v, id=k) for k, v in raw.items()]
    else:
        items = list(raw)
    avatars = [{"id": p.get("id"), "name": p.get("name") or p.get("id"),
                "scene": p.get("scene", ""), "file": p.get("file")}
               for p in items if p.get("file")]
    if not avatars:
        return None
    return {"default": "yanbing",
            "persons": [{"id": "yanbing", "name": "炎冰", "note": "从旧版象库迁移",
                         "avatars": avatars}]}


def _default_data():
    m = _migrate_from_legacy()
    if m:
        return m
    return {"default": "yanbing",
            "persons": [{"id": "yanbing", "name": "炎冰", "note": "", "avatars": []}]}


def load():
    d = _load_raw() or _default_data()
    d.setdefault("default", (d.get("persons") or [{}])[0].get("id", "yanbing"))
    d.setdefault("persons", [])
    d.setdefault("active_avatar", "")
    return d


def active():
    """当前激活分身 / 形象：{"person": id, "avatar": id, "name": 名}。

    **这是全系统唯一的「当前分身」真相源**（persons.json）。
    以前前端把选择写进 settings.json、后端 voice/avatar 读 persons.json.default，
    两边各说各话，才会出现「界面选了金子、语音却还是炎冰」。
    现在统一走这里：前端选人 → set_active() → 落盘 persons.json → 后端各处直接读。
    """
    d = load()
    ps = d.get("persons") or []
    pid = d.get("default") or (ps[0].get("id") if ps else "")
    p = next((x for x in ps if x.get("id") == pid), None) or (ps[0] if ps else None)
    if p is None:
        return {"person": "", "avatar": "", "name": ""}
    avs = [a for a in p.get("avatars", []) if os.path.exists(
        os.path.join(config.PORTRAITS_DIR, a.get("file", "")))]
    aid = d.get("active_avatar") or ""
    if not any(a.get("id") == aid for a in avs):
        aid = avs[0]["id"] if avs else ""
    return {"person": p.get("id", ""), "avatar": aid, "name": p.get("name") or p.get("id", "")}


def set_active(pid=None, aid=None):
    """切换当前分身 / 形象。两者都是全系统生效（声音 + 形象 + 讲解人称）。"""
    d = load()
    if pid:
        if not any(p.get("id") == pid for p in d.get("persons", [])):
            return d, False
        d["default"] = pid
        d["active_avatar"] = ""
    if aid:
        d["active_avatar"] = aid
    save(d)
    return d, True


def save(d):
    os.makedirs(config.PORTRAITS_DIR, exist_ok=True)
    with open(config.PERSONS_JSON, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return d


def _slug(name):
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "-", (name or "").strip()).strip("-")
    return s or ("p" + uuid.uuid4().hex[:6])


def list_persons():
    """返回前端所需结构：{default, persons:[{id,name,note,avatars:[{id,name,scene,url}]}]}"""
    d = load()
    out = []
    for p in d["persons"]:
        avs = []
        for a in p.get("avatars", []):
            fp = os.path.join(config.PORTRAITS_DIR, a.get("file", ""))
            if not os.path.exists(fp):
                continue
            avs.append({"id": a["id"], "name": a.get("name") or a["id"],
                        "scene": a.get("scene", ""),
                        "url": "/api/portrait?file=" + a["file"]})
        out.append({"id": p["id"], "name": p.get("name") or p["id"],
                    "note": p.get("note", ""), "avatars": avs})
    return {"default": d.get("default"), "persons": out}


# ---------------- 人（Person）CRUD ----------------
def add_person(name, note=""):
    d = load()
    pid = _slug(name)
    base, i = pid, 2
    while any(p["id"] == pid for p in d["persons"]):
        pid = f"{base}{i}"; i += 1
    d["persons"].append({"id": pid, "name": name, "note": note, "avatars": []})
    if len(d["persons"]) == 1:
        d["default"] = pid
    return save(d), pid


def rename_person(pid, name):
    d = load()
    for p in d["persons"]:
        if p["id"] == pid:
            p["name"] = name
            return save(d), True
    return d, False


def delete_person(pid):
    d = load()
    d["persons"] = [p for p in d["persons"] if p["id"] != pid]
    if d.get("default") == pid:
        d["default"] = d["persons"][0]["id"] if d["persons"] else ""
    return save(d)


def set_default(pid):
    d = load()
    d["default"] = pid
    return save(d)


# ---------------- 形象（Avatar）CRUD ----------------
def add_avatar(pid, name, src_path, scene=""):
    """把 src_path 图片复制进象库并挂到该人。返回 (data, avatar_id)。"""
    d = load()
    person = next((p for p in d["persons"] if p["id"] == pid), None)
    if person is None:
        raise ValueError(f"分身不存在: {pid}")
    ext = os.path.splitext(src_path)[1].lower() or ".jpg"
    aid = _slug(name) or ("a" + uuid.uuid4().hex[:6])
    base, i = aid, 2
    while any(a["id"] == aid for a in person.get("avatars", [])):
        aid = f"{base}{i}"; i += 1
    fname = f"{pid}__{aid}{ext}"
    os.makedirs(config.PORTRAITS_DIR, exist_ok=True)
    dst = os.path.join(config.PORTRAITS_DIR, fname)
    with open(src_path, "rb") as s, open(dst, "wb") as t:
        t.write(s.read())
    person.setdefault("avatars", []).append(
        {"id": aid, "name": name, "scene": scene, "file": fname})
    return save(d), aid


def update_avatar(pid, aid, name=None, scene=None, src_path=None):
    d = load()
    person = next((p for p in d["persons"] if p["id"] == pid), None)
    if person is None:
        raise ValueError(f"分身不存在: {pid}")
    for a in person.get("avatars", []):
        if a["id"] == aid:
            if name:
                a["name"] = name
            if scene is not None:
                a["scene"] = scene
            if src_path:
                ext = os.path.splitext(src_path)[1].lower() or ".jpg"
                fname = f"{pid}__{aid}{ext}"
                dst = os.path.join(config.PORTRAITS_DIR, fname)
                with open(src_path, "rb") as s, open(dst, "wb") as t:
                    t.write(s.read())
                a["file"] = fname
            return save(d), True
    return d, False


def delete_avatar(pid, aid):
    d = load()
    person = next((p for p in d["persons"] if p["id"] == pid), None)
    if person is None:
        return d
    avs = person.get("avatars", [])
    for a in list(avs):
        if a["id"] == aid:
            fp = os.path.join(config.PORTRAITS_DIR, a.get("file", ""))
            if os.path.exists(fp):
                try:
                    os.remove(fp)
                except Exception:
                    pass
            avs.remove(a)
    return save(d)


def resolve(pid=None, aid=None):
    """解析到实际图片绝对路径；缺省时回落到「当前激活分身 + 当前激活形象」。"""
    d = load()
    ps = d.get("persons") or []
    pid = pid or d.get("default")
    person = next((p for p in ps if p.get("id") == pid), None) \
        or (ps[0] if ps else None)
    if person is None:
        return None
    avs = person.get("avatars", [])
    # aid 未显式给定时，用「当前激活分身」记住的那个形象（而不是永远取第一个）
    if not aid and person.get("id") == d.get("default"):
        aid = d.get("active_avatar") or ""
    a = next((x for x in avs if x["id"] == aid), None) or (avs[0] if avs else None)
    if not a:
        return None
    fp = os.path.join(config.PORTRAITS_DIR, a.get("file", ""))
    return fp if os.path.exists(fp) else None
