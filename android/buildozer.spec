[app]
title = ant-py
version = 1.0.0
package.name = antpy
package.domain = org.antpy
package.icon = assets/icon.png

# source.dir 以 android/ 为主（main.py / service.py / bridge.py），
# 桌面版核心 proxycore.py / paths.py / nodes.json / bypass_list.txt 用
# source.include_dirs 从仓库根打包进 APK
source.dir = .
source.include_exts = py,json,txt,png
source.include_dirs = ..:.
source.exclude_dirs = build,dist,bin,__pycache__,.omc

requirements = python3,flet==0.24.1,cryptography==43.0.3,pyjnius,android

# Flet 需要网络/前台服务/通知权限；VPN 权限由 pyjnius 调 VpnService 获取
android.permissions = INTERNET,FOREGROUND_SERVICE,FOREGROUND_SERVICE_SPECIAL_USE,POST_NOTIFICATIONS,WAKE_LOCK

services = Vpn:service.py:foreground

android.api = 33
android.minapi = 26
android.archs = arm64-v8a
android.allow_backup = False

orientation = portrait
fullscreen = False

[buildozer]
log_level = 2
warn_on_root = 1
