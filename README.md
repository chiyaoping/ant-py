# ant-py — 免登录 Python 代理客户端

逆向 `ant_vpn.exe` (2.0.9.0) 后用 Python 复刻的代理客户端，**无任何登录/鉴权**。
节点来自你本机 `%APPDATA%\ant\ant.db` 的缓存（36 个，5 组），协议已实测打通。
（`ant.db` 是 **msgpack** 不是 sqlite，解析时别用 `sqlite3`；`ServerList` 是 `{组号: [节点]}`。）

## 使用

```
cd ant-py
python gui.py              # 图形界面（推荐）
python proxy.py            # 纯命令行启动本地代理 (127.0.0.1:1080)
python sysproxy.py on      # 接管 Windows 系统代理
python sysproxy.py off     # 恢复系统代理
python pick_node.py        # 测速并切换最快节点（重写 config.json）
python aff_test4.py        # 探测服务端 Aff 校验行为
python tunmode.py config   # 生成并校验 mihomo 配置（不开 TUN）
python tunmode.py check    # 检查管理员权限 / 内核 / 上游可用性
```

**GUI 功能**：连接/断开、节点分组树（双击切换）、⚡全节点测速、系统代理开关、
TUN 模式开关、全局/分流切换、实时流量统计、日志。**测速完成后组内节点自动按延迟升序排列**
（最快的排在最前，超时/未测的排到最后）。
**点 X 或最小化 → 缩到右下角托盘**：左键/菜单"显示主窗口"还原，托盘右键可 直连切换/退出。

- SOCKS5 和 HTTP 代理都在同一个端口 `1080`（自动识别协议）
- 支持 CONNECT 隧道（HTTPS）与绝对地址 GET/POST
- `is_global: false` 时按 `bypass_list.txt`（内置 5974 条域名，来自原程序）直连分流
- 切节点：改 `config.json` 里的 `selected`，或直接跑 `pick_node.py`

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

## TUN 模式（全软件接管）

系统代理只有"会读系统代理设置"的程序才认（浏览器/Electron 认，Node/Go/Java/Docker/终端
里的 git、curl、npm 都不认）。**TUN 模式**在网络层劫持，进程本身不需要支持代理，
因此上述程序全部会被接管。

```bash
# 需要管理员权限（创建虚拟网卡 + 改路由表）
python gui.py            # GUI 里勾选「TUN 模式（全软件接管）」
```

**结构**：ant 节点用的是自定义协议（TLS → WS upgrade → 带 Aff 前缀的 aes-256-cfb），
mihomo 不认识。所以内核只负责**建 TUN 网卡、劫持路由与 DNS**，真正的出站交给本项目
已验证的本地 SOCKS5（`127.0.0.1:1080`）。即：

```
所有软件 → TUN 网卡(ant-tun) → mihomo → SOCKS5 127.0.0.1:1080 → ant 节点
```

配置由 `tunmode.py` 按 `config.json` / `nodes.json` 自动生成到 `bin/mihomo/config.yaml`。

**使用要点**：

- **需要管理员权限**。非管理员时勾选框会提示并拒绝开启。
- **与系统代理互斥**：开启 TUN 会自动关闭系统代理（同一份流量被处理两次会让规则、
  DNS、日志都难以推理）。
- **必须先「连接」**：TUN 依赖本地引擎出站。未连接时开启会提示先连接。
- **开启前会自动探测上游**：`preflight` 会真的发一次请求，上游不通就拒绝开启 TUN，
  避免"接管了全部流量但出不去"→ 整机静默断网。
- **换节点会自动重建 TUN**：节点 IP 集合变化后需重写路由排除，否则新节点可能被
  TUN 劫持成回环。
- **退出时自动清理**：关窗口/退出程序会先拆 TUN 再停引擎；启动时也会清理上次异常
  退出残留的内核进程，避免旧路由继续劫持流量。

**回环防护**（最关键的一点）：TUN 抢走默认路由后，本地引擎自己连"节点"的包也会被
TUN 捞走，形成 `引擎→TUN→内核→SOCKS5→引擎→节点` 的绕行甚至死循环。因此生成配置时
会把所有节点地址（域名先解析成真实 IP）写进 `route-exclude-address`，并配 `IP-CIDR,...,DIRECT`
规则。实测节点 IP 的最长前缀匹配落在物理网关（`/0 gw=192.168.199.1`），而普通公网 IP
走 TUN（`/5 gw=198.18.0.2`），即节点流量确实绕开了 TUN。

**依赖文件**（已随仓库放在 `bin/`，无需再下载）：

| 文件 | 说明 |
|---|---|
| `bin/mihomo.exe` | mihomo 内核 v1.19.32（windows-amd64-compatible） |
| `bin/wintun.dll` | Wintun 0.14.1 驱动（TUN 网卡必需） |
| `bin/mihomo/config.yaml` | 自动生成的配置（每次启动重写） |
| `bin/mihomo/mihomo.log` | 内核日志，排查 TUN 问题先看这里 |

## 文件

| 文件 | 作用 |
|---|---|
| `proxy.py` | 主程序（本地代理 + 上游隧道） |
| `sysproxy.py` | Windows 系统代理开关（WinINet 同款注册表项） |
| `tunmode.py` | TUN 模式：生成 mihomo 配置、内核启停、回环防护、上游预检 |
| `pick_node.py` | 节点测速选优 |
| `aff_test4.py` | Aff 校验探测（边界矩阵 / 单值） |
| `test_speed_sort.py` | 验证 GUI 测速后按延迟升序渲染 |
| `nodes.json` | 从 `ant.db` 移植的 36 个节点 |
| `config.json` | 运行配置（监听端口/全局开关/选中节点/Aff） |
| `bypass_list.txt` | 原程序内置的直连域名名单 |

## 关于 `aff`（实测结论）

`Aff` 取自账号的用户 ID（`ant.db → ApiHome.UserInfo.MyData.Aff`），
以**大端 int32** 拼在 SS 首包最前面：`Aff(int32 BE) + ATYP + ADDR + PORT`。

2026-10-13 实测（节点 `ants-台湾29 59.124.193.203:8080`，17 次探测、真实 Aff 前后对照）：

| Aff 取值 | 结果 |
|---|---|
| `1` / `2` / `100` / `12345` / `99999999` / `2147483647` | ✅ 正常转发（146B / ~0.3s） |
| `21347001`（真实 ID） | ✅ 正常转发 |
| `0` / `-1` / `-2` / `-100` / `-2147483648` | ❌ WS 握手 101 成功，但**静默挂起** 12s 无数据 |

**服务端只做 `aff > 0` 的阈值检查，不查询账号是否存在**（`99999999` 这种不存在的号段照样放行）。
检查分三层，只有最后一层才看 Aff：

1. **WS 层不看 Aff** —— 任何值（含 `-2147483648`）都能拿到 `101 Switching Protocols`；
   握手包里没有任何身份信息，只有 `UA / Upgrade / Sec-WebSocket-Key`。
2. **SS 层不看 Aff** —— `aff <= 0` 时首包发出后连接不被断开，只是挂起；
   若密码错误通常会直接 RST，二者形态不同。
3. **转发层是唯一检查点** —— `aff > 0` 转发，`aff <= 0` 静默丢包（不回错误、不关连接），
   典型的"守卫条件不满足直接 return"写法。

**实践含义**：

- **换账号不必更新 `aff`** —— 填 `1` 和填真实 ID 在建连行为上完全一致。
  但**仍建议填真实值**：服务端完全可以在放行的同时在后台按 Aff 记账，
  填假 ID 会导致你的流量记到假 ID 名下，配额/计费行为可能异常。
- 探测脚本：`python aff_test4.py`（跑全量边界矩阵）或 `python aff_test4.py 123456`（单测）。

## 注意

- 服务器列表可能被服务端变更；若节点失效，重跑 `pick_node.py`。
- `pick_node.py` 会重写整个 `config.json`，因此它现在**显式保留 `aff`**
  （优先级：旧 `config.json` 的 `aff` → `nodes.json` 的 `aff` → 警告并写 0）。
  早期版本会丢掉该字段，而缺失时默认值为 `0`，正好落在服务端静默挂起的区间，
  会导致代理完全无法上网——已修复。
