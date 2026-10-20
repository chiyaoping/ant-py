# ant-py Android 版

桌面版 `F:\111\py\ant-py` 的安卓移植：Flet UI + 复用 `proxycore.py` 引擎 +
VpnService 全接管（tun2socks）。**无独立 Android Service**——flet build 没有
p4a 的 services 机制，`vpnctl.py` 作为普通模块在主 Python 进程内完成全部工作。

```
所有 app → VpnService TUN → tun2socks(libtun2socks.so) → SOCKS5 127.0.0.1:1080 → proxycore.Engine → ant 节点
```

## 文件

| 文件 | 作用 |
|---|---|
| `main.py` | Flet 界面（连接/断开/节点平铺列表/全节点测速/全局开关/流量统计/日志） |
| `vpnctl.py` | VPN 控制：启动 Engine → pyjnius 建 TUN 拿 fd → tun2socks 子进程（fd 经 pass_fds 继承） |
| `bridge.py` | pyjnius 胶水：VpnService 权限流/建 TUN/前台服务通知/UID 查询 |
| `pyproject.toml` | Flet 打包配置（org=org.antpy、分 ABI 拆分、FOREGROUND_SERVICE 权限、依赖锁版本） |
| `flet-template/` | vendor 的 flet build 模板（jniLibs 目录约定；.so 由 CI 下载，不入库） |
| `config.json` / `nodes.json` / `bypass_list.txt` / `proxycore.py` / `paths.py` | 构建时由 CI 从仓库根拷贝进 `android/` |

## 构建

推荐 GitHub Actions：push 到 `android/**`、`proxycore.py`、`paths.py`、`nodes.json`、
`bypass_list.txt`、`config.json` 或 workflow 本身会触发 `.github/workflows/android.yml`。
CI 流程：拷贝共享源码 → 下载 hev-socks5-tunnel 2.19.1 (arm64) 到模板 jniLibs →
`flet build apk --module-name main --template flet-template` → 上传 artifact `antpy-apk`。
从 Actions artifact 下载 zip 解压，`adb install` arm64 APK 安装。

**已知限制**：CI 只给 arm64-v8a 打入 tun2socks（x86_64 APK 缺该二进制，
不能装 x86 模拟器实测；如需要，改 workflow 给 x86_64 ABI 也下载对应二进制）。

本地构建（需 flet CLI）：

```bash
cd android
# 先手动放 jniLibs/arm64-v8a/libtun2socks.so（从 hev-socks5-tunnel releases 下载）
flet build apk --module-name main --template "$PWD/flet-template"
```

## 关键点

- **防回环**：`bridge.build_vpn_fd` 用 `addDisallowedApplication(own_uid)` 排除本 app，
  引擎出站的 WS 连接走物理网络，不会被 TUN 重新捞回。
- **tun2socks 位置**：必须放在 `jniLibs/arm64-v8a/libtun2socks.so`（Android 允许 exec
  自己 nativeLibraryDir，`bridge.tun2socks_cmd()` 从该目录取路径），放在 filesDir 会被
  SELinux W^X 拦截。
- **VPN 授权流**：`bridge.vpn_prepare()` 必须把 `VpnService.prepare()` +
  `startActivity()` 调度回**主线程**（`Handler(Looper.getMainLooper()).post()`）——
  flet 的 on_click 在后台线程跑，直接调 `ActivityThread.currentActivity()` 返回 None，
  退化为 Application 上下文后系统会**静默拦截**授权对话框（不弹窗不报错）。
  用户授权后需再点一次「连接」（简化实现，未用 onActivityResult 链路）。
- **测速**：全节点测速在后台线程逐个测，结果按延迟升序排列，超时/未测排最后。

## 桌面调试

```bash
cd android
python main.py    # 无 VPN，直接跑本地代理（同桌面版 proxy.py）
```
