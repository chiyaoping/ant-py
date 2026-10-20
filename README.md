# ant-py — 免登录 Python 代理客户端

逆向 `ant_vpn.exe` (2.0.9.0) 后用 Python 复刻的代理客户端，**无任何登录/鉴权**。
节点来自本机 `%APPDATA%\ant\ant.db` 的缓存（36 个，5 组），协议已实测打通。

> **AI 快速导读**：本项目有两条产品线——**桌面版**（Windows，PyInstaller 打包，系统代理
> + TUN 双模式）和**安卓版**（`android/`，Flet UI，CI 自动构建 APK）。两者共用
> `proxycore.py` 代理引擎。自研协议（TLS → WS upgrade → Aff 前缀 aes-256-cfb），
> 详细协议分析见下文「协议」和「关于 aff」。修改安卓相关文件
> （`android/**`、`proxycore.py`、`nodes.json` 等）后 push 会自动触发 APK 构建。

## 项目结构

```
ant-py/
├── proxycore.py          # ★ 核心引擎：SOCKS5/HTTP 本地代理 + 上游协议隧道（桌面/安卓共用）
├── proxy.py              # 桌面版入口：纯命令行启动本地代理 (127.0.0.1:1080)
├── gui.py                # 桌面版 GUI（Tkinter）：连接/节点/测速/系统代理/TUN/托盘
├── sysproxy.py           # Windows 系统代理开关（WinINet 注册表）
├── tunmode.py            # TUN 模式：生成 mihomo 配置、内核启停、回环防护、上游预检
├── pick_node.py          # 节点测速选优（重写 config.json，显式保留 aff）
├── aff_test4.py          # Aff 校验探测（边界矩阵/单值）
├── test_speed_sort.py    # GUI 测速排序测试
├── paths.py              # 路径解析：开发/打包(onedir)/安卓(APK assets) 三态资源定位
├── nodes.json            # 从 ant.db 移植的 36 个节点（msgpack，勿用 sqlite3 解析源库）
├── config.json           # 运行配置：listen_port / is_global / selected 节点 / aff
├── bypass_list.txt       # 原程序内置直连域名名单（5974 条）
├── bin/                  # 桌面 TUN 依赖（gitignore 大文件：mihomo.exe / wintun.dll）
├── assets/               # 图标等静态资源
├── 打包.bat              # 桌面版 PyInstaller 一键打包 → dist/ant-py.exe
│
└── android/              # ★ 安卓版（Flet UI）
    ├── main.py           # Flet 界面：连接/节点平铺列表/全节点测速/全局开关/流量/日志
    ├── vpnctl.py         # VPN 控制：主进程内建 TUN + 启 tun2socks 子进程（无独立 Service）
    ├── bridge.py         # pyjnius 胶水：VpnService 权限流(vpn_prepare)/建 TUN/通知/UID
    ├── paths.py 等       # 构建时由 CI 从仓库根拷贝进来（proxycore/nodes/bypass_list…）
    ├── flet-template/    # vendor 的 flet build 模板（jniLibs 目录约定，.so 由 CI 下载）
    ├── pyproject.toml    # Flet 配置：org/product/权限/分 ABI 拆分/依赖锁版本
    └── README.md         # 安卓版构建与关键点详解
```

## 数据流

```
桌面(分流模式):  应用 → 系统代理/TUN → proxycore (1080) ─┬→ 直连（命中 bypass_list）
                                                        └→ ant 节点（TLS→WS→SS aes-256-cfb）
桌面(TUN模式):   应用 → TUN 网卡 → mihomo → SOCKS5 127.0.0.1:1080 → proxycore → 节点
安卓:            App → VpnService TUN → tun2socks → SOCKS5 127.0.0.1:1080 → proxycore → 节点
```

防回环是两条 TUN 链路的关键：
- 桌面 `tunmode.py` 把所有节点 IP 写进 mihomo `route-exclude-address` + DIRECT 规则；
- 安卓 `bridge.build_vpn_fd` 用 `addDisallowedApplication(own_uid)` 排除本 app。

## 使用（桌面）

```
python gui.py              # 图形界面（推荐）
python proxy.py            # 纯命令行启动本地代理 (127.0.0.1:1080)
python sysproxy.py on/off  # 接管/恢复 Windows 系统代理
python pick_node.py        # 测速并切换最快节点
python tunmode.py config|check  # 生成/校验 mihomo 配置
```

- SOCKS5 和 HTTP 代理同端口 `1080`（自动识别协议），支持 CONNECT 隧道
- `is_global: false` 时按 `bypass_list.txt` 直连分流；`true` 全部走节点
- TUN 模式需管理员权限，与系统代理互斥，必须先连接，开启前自动探测上游

## 安卓版构建

push 到 `android/**`、`proxycore.py`、`paths.py`、`nodes.json`、`bypass_list.txt`、
`config.json` 或 workflow 本身会触发 `.github/workflows/android.yml`：
下载 hev-socks5-tunnel 2.19.1 (arm64) 进模板 jniLibs → `flet build apk`
（vendor 模板）→ 上传 artifact `antpy-apk`（含 arm64/armv7/x86_64 三个 APK）。
**注意：目前 CI 只给 arm64 打入 tun2socks，x86_64 APK 不能用于模拟器实测。**

## 协议（逆向还原，已验证）

```
客户端 → 节点(443): TLS
       → "GET / HTTP/1.1" + Upgrade: websocket 握手 (Sec-WebSocket-Key = b64(随机16B))
       → 101 之后 raw 字节流 = Shadowsocks aes-256-cfb
           KDF: EVP_BytesToKey(MD5, 32B key)，每连接随机 16B IV 前缀
           首包明文 = Aff(int32 大端) + ATYP + ADDR + PORT
           之后 = 原始双向数据流
```

8080 端口的 `ws` 节点：无 TLS，其余相同。

## 关于 `aff`（实测结论）

`Aff` 取自账号的用户 ID（`ant.db → ApiHome.UserInfo.MyData.Aff`），
以**大端 int32** 拼在 SS 首包最前面。2026-10-13 实测（17 次探测）：

| Aff 取值 | 结果 |
|---|---|
| `> 0`（含真实 ID、`99999999` 等不存在号段） | ✅ 正常转发 |
| `<= 0`（`0`/负数） | ❌ WS 握手成功但**静默挂起**无数据 |

检查分三层：WS 层不看 Aff → SS 层不看（挂起≠断开）→ 转发层是唯一检查点
（`aff > 0` 放行，典型"守卫条件不满足直接 return"）。
**换账号不必更新 `aff`，但建议填真实值**（服务端可按 Aff 后台记账，假 ID 记账可能异常）。

## 注意

- `ant.db` 是 **msgpack** 不是 sqlite；`ServerList` 是 `{组号: [节点]}`。
- 服务器列表可能被服务端变更；节点失效重跑 `pick_node.py`。
- `pick_node.py` 显式保留 `aff`（缺失时默认 0 正好落在静默挂起区间，曾导致无法上网，已修复）。
