[app]
title = ant-py
version = 1.0.0
package.name = antpy
package.domain = org.antpy
package.icon = assets/icon.png

# source.dir 以 android/ 为主（main.py / service.py / bridge.py），
# 共享文件 proxycore.py / paths.py / nodes.json / bypass_list.txt 由 CI 复制进本目录后打包
source.dir = .
source.include_exts = py,json,txt,png
source.exclude_dirs = build,dist,bin,__pycache__,.omc,.buildozer,jniLibs

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
