#!/usr/bin/env python3
"""paths — 统一解析「打包内只读资源」与「exe 同级可写数据」的位置。

PyInstaller onefile 把整包解压到临时目录（sys._MEIPASS），进程退出即删除。
因此 `os.path.dirname(__file__)` 在打包后指向临时目录：

  - 读：没问题，打包进去的文件都在那儿；
  - 写：写进去的东西下次启动就没了 —— config.json 会被静默丢弃，
        表现为「切换节点 / 全局开关」重启后失效。

所以这里把两者分开：

  BUNDLE  打包内的只读资源（节点表、分流名单、mihomo 内核、图标）
  DATA    exe 同级目录（config.json、mihomo 工作目录）—— 可写且持久

未打包（python gui.py）时两者都是源码目录，行为与改动前完全一致。
"""
import os
import shutil
import sys

_SRC = os.path.dirname(os.path.abspath(__file__))

# Android (p4a) 环境: HOME 指向 app 可写目录, __file__ 所在目录即解包后的只读资源
_IS_ANDROID = "ANDROID_DATA" in os.environ or "ANDROID_ROOT" in os.environ


def _bundle() -> str:
    if _IS_ANDROID:
        return _SRC
    return getattr(sys, "_MEIPASS", _SRC)


def _data() -> str:
    if _IS_ANDROID:
        return os.environ.get("HOME") or _SRC
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return _SRC


BUNDLE = _bundle()
DATA = _data()


def resource(*parts) -> str:
    """只读资源路径。exe 同级若存在同名文件则优先用（便于用户覆盖节点表）。"""
    override = os.path.join(DATA, *parts)
    if os.path.isdir(override) or os.path.isfile(override):
        return override
    return os.path.join(BUNDLE, *parts)


def data_dir(*parts) -> str:
    """可写目录路径，位于 exe 同级；不存在则创建。"""
    p = os.path.join(DATA, *parts)
    os.makedirs(p, exist_ok=True)
    return p


def data_file(*parts, seed=True) -> str:
    """可写文件路径，位于 exe 同级。

    seed=True 时首次运行会从打包内复制一份出来，这样 config.json 有初值，
    用户后来的修改也留在 exe 旁边而不是临时目录里。
    """
    p = os.path.join(DATA, *parts)
    if seed and not os.path.exists(p):
        src = os.path.join(BUNDLE, *parts)
        if os.path.isfile(src):
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
            shutil.copy2(src, p)
    return p
