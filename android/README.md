# ant-py Android 版

桌面版 `F:\111\py\ant-py` 的安卓移植：Flet UI + 复用 `proxycore.py` 引擎 +
VpnService 全接管（tun2socks）。

```
所有 app → VpnService TUN → tun2socks(libtun2socks.so) → SOCKS5 127.0.0.1:1080 → proxycore.Engine → ant 节点
```

## 文件

| 文件 | 作用 |
|---|---|
| `main.py` | Flet 界面（连接/节点树/测速/全局开关/流量/日志） |
| `service.py` | p4a 前台服务：跑 Engine + tun2socks，响应状态文件指令 |
| `bridge.py` | pyjnius：VpnService 权限/建立/通知/UID |
| `buildozer.spec` | APK 打包配置 |
| `jniLibs/arm64-v8a/libtun2socks.so` | hev-socks5-tunnel（CI 自动下载，本地构建需手动放） |

## 构建

推荐 GitHub Actions：push 到 `android/**` 触发 `.github/workflows/android.yml`，
从 Actions artifact `antpy-apk` 下载 APK，`adb install` 安装。

本地（WSL2 + Docker）：

```bash
cd android
docker run --rm -v $(pwd):/home/user/hostcwd -v ~/.buildozer:/home/user/.buildozer \
  ghcr.io/artemsbulgakov/buildozer-action:master android debug
```

## 关键点

- **防回环**：`bridge.build_vpn_fd` 用 `addDisallowedApplication(own_uid)` 排除本 app，
  引擎出站的 WS 连接走物理网络，不会被 TUN 重新捞回。
- **tun2socks 位置**：必须放在 `jniLibs/arm64-v8a/libtun2socks.so`（Android 允许 exec
  自己 nativeLibraryDir），放在 filesDir 会被 SELinux 拦。
- **VPN 授权流**：`bridge.vpn_prepare()` 弹系统对话框；授权后需要再点一次「连接」
  （简化实现，未用 onActivityResult 链路）。
- **回环/UDP**：`--enable-udp` 走 UDP-over-SOCKS5；若引擎 SOCKS5 不支持 UDP，
  DNS 会挂 —— 回退方案是 Python 侧加 DNS-over-TCP 转发。
- **状态文件**：`HOME/vpn_state.json` 是 main → service 的指令通道（轮询 1s）。

## 桌面调试

```bash
cd android
python main.py    # 无 VPN，直接跑本地代理（同桌面版 proxy.py）
```
