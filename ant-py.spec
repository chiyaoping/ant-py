# -*- mode: python ; coding: utf-8 -*-
"""ant-py 的 PyInstaller 打包配置。

onefile：所有 Python 代码 + 节点表 + 分流名单 + mihomo 内核 + wintun.dll
都进单个 exe（约 80MB，启动时解压到临时目录）。

关键点：
  - bin/ 下的内核和驱动按原目录结构打进包，tunmode.py 运行时从 sys._MEIPASS 读；
  - config.json 作为**只读种子**打进包，运行时由 paths.data_file() 首次复制到
    exe 同级，之后的修改留在 exe 旁边，不会被临时目录回收掉；
  - 不加 UAC manifest：双击直接开，要用 TUN 时手动右键「以管理员身份运行」。
"""

a = Analysis(
    ['gui.py'],
    pathex=[],
    binaries=[
        ('bin/mihomo.exe', 'bin'),
        ('bin/wintun.dll', 'bin'),
    ],
    datas=[
        ('config.json', '.'),
        ('nodes.json', '.'),
        ('bypass_list.txt', '.'),
        ('assets/icon.ico', 'assets'),
    ],
    hiddenimports=['pystray', 'pystray._win32'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['cairosvg', 'pycairo'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ant-py',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
    version=None,
)
