# -*- coding: utf-8 -*-
"""
01_run_init.py —— 初始化一次采集会话（影刀流程的第一个「执行Python代码」）
==========================================================================

影刀输入变量（在指令面板里填）：
    {"base_dir": "D:\\blog\\yysls-coordinate-webui\\raw\\rpa", "tag": "清河补漏", "mode": "clipboard"}

影刀输出变量：
    ${会话目录}   ${输出文件}   ${启动信息}

它会：按时间戳建一个不重名的会话目录、算出坐标落盘文件名、把元信息写成 session.json。
后续所有片段都往 ${输出文件} 里追加，这样一次采集 = 一个目录，便于回溯。

单独跑：
    python 01_run_init.py --base-dir raw/rpa --tag 清河补漏
"""

import json
import os
from datetime import datetime


def _in(name, default=None):
    """从影刀注入的变量里取值；没注入就返回默认值（保证单独跑也不炸）。"""
    return globals().get(name, default)


def init_run(base_dir: str, tag: str = "", mode: str = "clipboard") -> dict:
    base = os.path.abspath(base_dir or os.path.join("raw", "rpa"))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_tag = "".join(c for c in (tag or "") if c not in '\\/:*?"<>|').strip()
    run_name = f"{stamp}_{safe_tag}" if safe_tag else stamp
    run_dir = os.path.join(base, run_name)
    os.makedirs(run_dir, exist_ok=True)

    out_file = os.path.join(run_dir, "collected.ini")
    meta = {
        "runName": run_name,
        "startedAt": datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
        "tag": safe_tag,
        "outFile": out_file,
    }
    with open(os.path.join(run_dir, "session.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return {
        "runDir": run_dir,
        "outFile": out_file,
        "runName": run_name,
        "message": f"会话已就绪：{run_dir}（模式 {mode}）",
    }


# ---- 影刀调用入口 ---------------------------------------------------------
_base = _in("base_dir", "")
_tag = _in("tag", "")
_mode = _in("mode", "clipboard")
if _base or _tag:
    _r = init_run(_base or os.path.join("raw", "rpa"), _tag, _mode)
    会话目录 = _r["runDir"]
    输出文件 = _r["outFile"]
    启动信息 = _r["message"]


# ---- 命令行入口（用于脱离影刀自测）----------------------------------------
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--base-dir", default=os.path.join("raw", "rpa"))
    ap.add_argument("--tag", default="selftest")
    ap.add_argument("--mode", default="clipboard")
    a = ap.parse_args()
    print(json.dumps(init_run(a.base_dir, a.tag, a.mode), ensure_ascii=False, indent=2))
