#!/usr/bin/env python3
"""tunmode — 用 mihomo 内核给 ant-py 提供 TUN（全局虚拟网卡）模式。

为什么是"内核对 + 复用本地代理"的结构：
  ant 节点用的是自定义协议（TLS → WS upgrade → 带 Aff 前缀的 aes-256-cfb 流），
  mihomo 不认识它。所以 mihomo 只干两件事——建 TUN 网卡、劫持路由与 DNS；
  真正的出站交给本项目已验证的本地 SOCKS5（默认 127.0.0.1:1080）。
  这样任何软件（含 Node/Go/Java/Docker/游戏）不必支持代理也能被接管。

最容易踩的坑是**路由回环**：TUN 抢走默认路由后，本地代理进程自己连"节点"的包
也会被 TUN 捞走，于是变成 引擎→TUN→kernel→SOCKS5→引擎→节点 的绕行甚至死循环。
因此生成配置时会把所有节点地址（域名先解析成 IP）排除在 TUN 路由之外。
"""
import ctypes, ipaddress, json, os, re, socket, subprocess, sys, time

import paths

HERE = os.path.dirname(os.path.abspath(__file__))
# 内核所在目录：打包后指向 onefile 的临时解压目录（进程存活期间有效），
# 未打包时就是源码里的 bin/。
# 注意不能用 resource("bin") 整目录判断：RUNDIR 会在 exe 同级创建 bin\mihomo
# 工作目录，使 dist\bin 存在但里面没有内核，导致误用空目录。按文件逐个解析。
EXE = paths.resource("bin", "mihomo.exe")
WINTUN = paths.resource("bin", "wintun.dll")
# 工作目录必须可写：内核要在这里写 config.yaml 和 mihomo.log，
# 临时目录退出即删，所以固定落在 exe 同级。
RUNDIR = paths.data_dir("bin", "mihomo")      # 内核工作目录（-d）
CONF = os.path.join(RUNDIR, "config.yaml")
BYPASS_FILE = paths.resource("bypass_list.txt")

ADAPTER = "ant-tun"                            # TUN 网卡名
MIXED_PORT = 7890                              # 内核自带 http/socks 混合端口（仅本机备用）
CTL_PORT = 9090                                # 外部控制端口
DNS_PORT = 1053

DNS_SERVERS = ["223.5.5.5", "119.29.29.29"]

# 401 之外的本地/保留网段一律直连
DIRECT_CIDRS = ["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                "169.254.0.0/16", "224.0.0.0/4", "255.255.255.255/32"]


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def available() -> bool:
    """内核与驱动是否就位。"""
    return os.path.isfile(EXE) and os.path.isfile(WINTUN)


def _run(cmd, timeout=20):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True,
                              timeout=timeout).stdout.decode("gbk", "replace")
    except Exception as e:
        return f"<{type(e).__name__}>"


def resolve_node_ips(nodes, timeout=4.0) -> set:
    """把节点地址（含域名）全部解析成 IP，用于 TUN 路由排除。"""
    ips = set()
    # 不改进程级默认超时（setdefaulttimeout 是全局副作用），只在本函数内临时设置
    old = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        for n in nodes:
            addr = str(n.get("Address", "")).strip()
            if not addr:
                continue
            try:
                ipaddress.ip_address(addr)
                ips.add(addr)
                continue
            except ValueError:
                pass
            try:
                for fam, _, _, _, sa in socket.getaddrinfo(addr, None):
                    if fam in (socket.AF_INET, socket.AF_INET6):
                        ips.add(sa[0])
            except Exception:
                pass
    finally:
        socket.setdefaulttimeout(old)
    return {ip for ip in ips if ":" not in ip}


def load_bypass_domains():
    """读直连名单（仅分流模式用；全局模式下返回 None，即全部走代理）。"""
    if not os.path.isfile(BYPASS_FILE):
        return None
    with open(BYPASS_FILE, encoding="utf-8") as f:
        doms = [ln.strip().lower() for ln in f if ln.strip()]
    return doms or None


def _yaml_list(items, indent):
    pad = " " * indent
    return "".join(f"{pad}- {json.dumps(str(i))}\n" for i in items)


def build_config(nodes, selected, engine_port, extra_direct=None, bypass_domains=None):
    """生成 mihomo 配置（手写 YAML，避免引入 PyYAML 依赖）。"""
    node_ips = resolve_node_ips(nodes)
    if extra_direct:
        node_ips |= set(extra_direct)

    # 排除网段：节点 IP（防回环）+ 本地保留网段
    exclude = [f"{ip}/32" for ip in sorted(node_ips)] + DIRECT_CIDRS

    # fake-ip 过滤：节点域名必须返回真实 IP，否则引擎会把 fake IP 当成目标地址发出去
    node_domains = [str(n.get("Address")) for n in nodes
                    if not re.match(r"^\d+\.\d+\.\d+\.\d+$", str(n.get("Address", "")))]
    fake_filter = sorted(set(node_domains)) + [
        "localhost.ptlogin2.qq.com", "*.lan", "*.local", "*.localdomain",
        "*.localhost", "time.*.com", "*.ntp.org", "ntp.*.com",
    ]

    y = []
    y.append("# 由 ant-py/tunmode.py 自动生成，请勿手工编辑\n")
    y.append(f"mixed-port: {MIXED_PORT}\n")
    y.append("allow-lan: false\n")
    y.append("mode: rule\n")
    y.append("log-level: info\n")
    y.append("ipv6: false\n")
    y.append("unified-delay: true\n")
    y.append("tcp-concurrent: true\n")
    y.append("find-process-mode: 'off'\n")
    y.append(f"external-controller: 127.0.0.1:{CTL_PORT}\n")
    y.append("external-ui: ''\n")
    y.append("secret: ''\n")
    y.append("profile:\n  store-selected: false\n  store-fake-ip: false\n")

    # ---- DNS：fake-ip + 劫持 53，所有查询交由内核处理 ----
    y.append("dns:\n")
    y.append("  enable: true\n")
    y.append(f"  listen: 127.0.0.1:{DNS_PORT}\n")
    y.append("  ipv6: false\n")
    y.append("  enhanced-mode: fake-ip\n")
    y.append("  fake-ip-range: 198.18.0.1/16\n")
    y.append("  use-hosts: true\n")
    y.append("  fake-ip-filter:\n")
    y.append(_yaml_list(fake_filter, 4))
    y.append("  default-nameserver:\n")
    y.append(_yaml_list(DNS_SERVERS, 4))
    y.append("  nameserver:\n")
    y.append(_yaml_list(DNS_SERVERS, 4))

    # ---- 出站：一个指向本地 ant 引擎的 SOCKS5 ----
    y.append("proxies:\n")
    y.append("  - name: ant-local\n")
    y.append("    type: socks5\n")
    y.append("    server: 127.0.0.1\n")
    y.append(f"    port: {int(engine_port)}\n")
    y.append("    udp: false\n")

    y.append("proxy-groups:\n")
    y.append("  - name: PROXY\n")
    y.append("    type: select\n")
    y.append("    proxies:\n      - ant-local\n")

    # ---- 规则：节点/内网/直连名单直连，其余全部走隧道 ----
    # 直连名单必须由内核判定：TUN 生效时引擎若自己"直连"某域名，
    # 那个连接会被 TUN 重新捞回来交给引擎 → 无限递归。
    # 内核的 DIRECT 出口绑定物理网卡，不会回环。
    if bypass_domains:
        # 用 rule-provider 而不是把 6000 条直接写进 rules：内核会为其建前缀树，
        # 匹配快，配置也小。
        y.append("rule-providers:\n")
        y.append("  bypass:\n")
        y.append("    type: file\n")
        y.append("    behavior: domain\n")
        y.append("    format: text\n")
        y.append(f"    path: {json.dumps(BYPASS_FILE.replace(chr(92), '/'))}\n")
    y.append("rules:\n")
    for ip in sorted(node_ips):
        y.append(f"  - IP-CIDR,{ip}/32,DIRECT,no-resolve\n")
    for cidr in DIRECT_CIDRS:
        y.append(f"  - IP-CIDR,{cidr},DIRECT,no-resolve\n")
    for s in DNS_SERVERS:
        y.append(f"  - IP-CIDR,{s}/32,DIRECT\n")
    if bypass_domains:
        y.append("  - RULE-SET,bypass,DIRECT\n")
    y.append("  - MATCH,PROXY\n")

    # ---- TUN ----
    y.append("tun:\n")
    y.append("  enable: true\n")
    y.append("  stack: mixed\n")
    y.append(f"  device: {ADAPTER}\n")
    y.append("  auto-route: true\n")
    y.append("  auto-detect-interface: true\n")
    y.append("  strict-route: false\n")
    y.append("  mtu: 1500\n")
    y.append("  route-exclude-address:\n")
    y.append(_yaml_list(exclude, 4))
    y.append("  dns-hijack:\n    - any:53\n")
    y.append("  udp-timeout: 300\n")

    return "".join(y), node_ips


def write_config(cfg_text):
    os.makedirs(RUNDIR, exist_ok=True)
    with open(CONF, "w", encoding="utf-8") as f:
        f.write(cfg_text)
    return CONF


def validate_config():
    """调用内核对配置做语法/语义校验。"""
    r = _run(f'"{EXE}" -d "{RUNDIR}" -t', timeout=40)
    ok = "configuration file" in r and "test failed" not in r.lower()
    return ok, r.strip()


def adapter_present() -> bool:
    out = _run("ipconfig /all", timeout=20)
    return ADAPTER.lower() in out.lower()


def adapter_ready() -> bool:
    """网卡已存在**且**已拿到 TUN 网关地址，说明内核已真正接管。"""
    out = _run(f'netsh interface ip show addresses name="{ADAPTER}"', timeout=20)
    return "198.18.0.1" in out


def _proc_alive(pid) -> bool:
    if not pid:
        return False
    out = _run(f'tasklist /FI "PID eq {pid}" /FO CSV /NH', timeout=15)
    return str(pid) in out


class TunMode:
    """TUN 模式的启停与状态。"""

    def __init__(self, engine_port=1080):
        self.engine_port = int(engine_port)
        self.proc = None
        self.pid = None
        self.running = False
        self.direct_ips = set()
        self.last_error = ""

    # ---- 状态 ----
    def status(self):
        alive = self.running and _proc_alive(self.pid)
        return {
            "running": bool(alive),
            "pid": self.pid,
            "adapter": adapter_present(),
            "admin": is_admin(),
            "available": available(),
            "config": CONF,
            "direct_ips": sorted(self.direct_ips),
        }

    # ---- 启停 ----
    def start(self, nodes, selected, bypass_domains=None):
        if not available():
            raise RuntimeError(f"缺少内核文件，需要 {EXE} 和 {WINTUN}")
        if not is_admin():
            raise PermissionError("TUN 模式需要管理员权限（创建虚拟网卡 + 改路由）")

        self.cleanup_stale()

        cfg_text, node_ips = build_config(nodes, selected, self.engine_port,
                                          bypass_domains=bypass_domains)
        self.direct_ips = node_ips
        write_config(cfg_text)

        ok, out = validate_config()
        if not ok:
            self.last_error = out
            raise RuntimeError(f"内核配置校验失败:\n{out}")

        # 内核从解压目录启动，工作目录也用同一个（有 wintun.dll 供加载）
        creationflags = 0x08000000  # CREATE_NO_WINDOW
        logf = open(os.path.join(RUNDIR, "mihomo.log"), "ab", buffering=0)
        try:
            self.proc = subprocess.Popen(
                [EXE, "-d", RUNDIR, "-f", CONF],
                cwd=os.path.dirname(EXE), stdout=logf, stderr=logf,
                creationflags=creationflags)
        finally:
            # Popen 已复制句柄，这里必须关掉自己这份，否则每次 start/restart 泄漏一个 fd
            logf.close()

        # 等网卡真正就绪：不仅名字出现，还要拿到 198.18.0.1 地址，
        # 否则上次崩溃残留的同名网卡会让这里的判断提前通过。
        for _ in range(60):
            if self.proc.poll() is not None:
                self.kill()
                self.last_error = self._tail_log()
                raise RuntimeError(f"内核提前退出:\n{self.last_error}")
            if adapter_ready():
                break
            time.sleep(0.25)
        else:
            self.kill()
            self.last_error = self._tail_log()
            raise RuntimeError(f"等待 TUN 网卡 {ADAPTER} 就绪超时:\n{self.last_error}")

        self.pid = self.proc.pid
        self.running = True
        self.last_error = ""
        return self.status()

    def stop(self):
        self.kill()
        # 兜底：清掉任何游离的内核进程（例如句柄丢失的历史实例），
        # 否则残留进程会继续持有路由表，导致关掉 TUN 后仍然无法上网。
        self.cleanup_stale()
        # 等内核清理路由与网卡（正常 1~3s，异常时最长 ~15s）
        for _ in range(60):
            if not adapter_present():
                break
            time.sleep(0.25)
        self.running = False
        self.pid = None
        return self.status()

    def kill(self):
        # 优先用 Popen 句柄；若句柄丢失（例如从状态文件恢复的实例），退回按 PID 杀。
        pids = []
        if self.proc is not None:
            try:
                if self.proc.poll() is None:
                    pids.append(self.proc.pid)
            except Exception:
                pass
            self.proc = None
        if self.pid and self.pid not in pids and _proc_alive(self.pid):
            pids.append(self.pid)
        for pid in pids:
            try:
                subprocess.run(f"taskkill /F /T /PID {pid}", shell=True,
                               capture_output=True, timeout=20)
            except Exception:
                pass
        self.running = False
        self.pid = None

    # ---- 遗留清理 ----
    def cleanup_stale(self):
        """清掉上次异常退出残留的内核进程。"""
        out = _run('tasklist /FI "IMAGENAME eq mihomo.exe" /FO CSV /NH', timeout=15)
        for m in re.finditer(r'"mihomo\.exe","(\d+)"', out):
            try:
                subprocess.run(f"taskkill /F /T /PID {m.group(1)}", shell=True,
                               capture_output=True, timeout=20)
            except Exception:
                pass

    def _tail_log(self, n=1200):
        try:
            with open(os.path.join(RUNDIR, "mihomo.log"), "rb") as f:
                return f.read()[-n:].decode("utf-8", "replace")
        except Exception:
            return ""


def preflight(engine_port=1080, timeout=25):
    """确认本地 SOCKS5 引擎真的能把流量送出去（TUN 只是转发到它）。"""
    try:
        s = socket.create_connection(("127.0.0.1", int(engine_port)), timeout=timeout)
    except Exception as e:
        return False, f"连接本地代理失败: {type(e).__name__} {e}"
    try:
        s.sendall(b"\x05\x01\x00")
        if s.recv(2) != b"\x05\x00":
            return False, "SOCKS5 握手失败"
        host = b"www.gstatic.com"
        s.sendall(b"\x05\x01\x00\x03" + bytes([len(host)]) + host
                  + (80).to_bytes(2, "big"))
        rep = s.recv(10)
        if len(rep) < 2 or rep[1] != 0:
            return False, f"SOCKS5 上游拒绝: {rep.hex()}"
        s.sendall(b"GET /generate_204 HTTP/1.1\r\nHost: www.gstatic.com\r\n"
                  b"Connection: close\r\n\r\n")
        s.settimeout(timeout)
        data = b""
        while b"\r\n\r\n" not in data and len(data) < 8192:
            c = s.recv(4096)
            if not c:
                break
            data += c
        return (bool(data), "ok" if data else "上游无响应（节点可能已失效）")
    except Exception as e:
        return False, f"{type(e).__name__} {e}"
    finally:
        try: s.close()
        except Exception: pass


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "check":
        print("admin:", is_admin(), "| kernel:", available())
        ok, msg = preflight()
        print("preflight:", ok, msg)
    elif cmd == "config":
        import paths
        cfg = json.load(open(paths.data_file("config.json"), encoding="utf-8"))
        nodes = json.load(open(paths.resource("nodes.json"), encoding="utf-8"))
        alln = [n for lst in nodes.get("groups", {}).values() for n in lst]
        text, ips = build_config(alln, cfg["selected"],
                                 int(cfg.get("listen_port", 1080)))
        write_config(text)
        print(f"written {CONF}; 排除 {len(ips)} 个节点 IP")
        ok, out = validate_config()
        print("validate:", ok)
        print(out)
    else:
        t = TunMode()
        print(json.dumps(t.status(), ensure_ascii=False, indent=2))
