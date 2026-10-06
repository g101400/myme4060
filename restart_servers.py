# -*- coding: utf-8 -*-
"""重启 myme 服务（完整版 7860 / 便携播放版 7861），用最新代码。"""
import os, subprocess, sys, time

BASE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
FLAGS = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
LOG = os.path.join(BASE, "_server_log_%d.txt")


def kill_port(port):
    r = subprocess.run("netstat -ano | findstr :%d" % port, shell=True,
                       capture_output=True, text=True)
    pids = set()
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1].startswith("LISTENING"):
            pids.add(parts[-1])
    for pid in pids:
        subprocess.run("taskkill /F /PID %s" % pid, shell=True,
                       capture_output=True, text=True)
        print("killed pid", pid, "on", port)
    return pids


def start(port):
    kill_port(port)
    env = dict(os.environ)
    env["MYME_PORT"] = str(port)
    env["PYTHONIOENCODING"] = "utf-8"
    f = open(LOG % port, "w", encoding="utf-8", errors="replace")
    p = subprocess.Popen([PY, "app.py"], cwd=BASE, env=env,
                         stdout=f, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL,
                         creationflags=FLAGS,
                         close_fds=True)
    print("started pid=%s port=%s" % (p.pid, port))
    return p


if __name__ == "__main__":
    ps = [start(7860), start(7861)]
    for _ in range(40):
        time.sleep(0.5)
        up = []
        for port in (7860, 7861):
            r = subprocess.run("netstat -ano | findstr :%d" % port, shell=True,
                               capture_output=True, text=True)
            if "LISTENING" in (r.stdout or ""):
                up.append(port)
            else:
                up.append(port)
        if all(u in up for u in (7860, 7861)):
            break
    print("up:", up)
