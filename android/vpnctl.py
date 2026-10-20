"""vpnctl — VPN 控制：建 TUN + 启 tun2socks，供 Flet 主进程直接调用。

flet build 没有 p4a 的 services 机制，因此这里不做独立 Android Service，
而是作为普通模块被 main.py import，在主 Python 进程内完成：
  1. 启动 proxycore.ENGINE（SOCKS5/HTTP 127.0.0.1:1080）
  2. 经 pyjnius 建 VpnService TUN，拿 fd
  3. tun2socks（libtun2socks.so）以子进程启动，fd 通过 pass_fds 继承
"""
import os
import subprocess
import sys
import threading

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import proxycore  # noqa: E402

ENGINE = proxycore.ENGINE
STATS = proxycore.STATS

_tun2socks = None
_tun_fd_obj = None
_lock = threading.Lock()
_running = False


def is_running() -> bool:
    return _running


def start_vpn(dns="1.1.1.1"):
    """建 TUN + 启 tun2socks。需已获 VPN 授权，否则抛异常。"""
    global _tun2socks, _tun_fd_obj, _running
    import bridge

    with _lock:
        if _running:
            return True
        if not ENGINE.running:
            ENGINE.start()

        fd, fd_obj = bridge.build_vpn_fd(ENGINE.bind_addr, ENGINE.listen_port, dns)
        cmd = [
            bridge.tun2socks_cmd(),
            "--netif-ipaddr", "26.26.26.1",
            "--netif-netmask", "255.255.255.0",
            "--socks-server-addr", f"{ENGINE.bind_addr}:{ENGINE.listen_port}",
            "--proxy-protocol", "socks5",
            "--mtu", "1500",
            "--tunfd", str(fd),
            "--enable-udp",
            "--log-level", "warn",
        ]
        _tun2socks = subprocess.Popen(cmd, pass_fds=(fd,))
        _tun_fd_obj = fd_obj
        _running = True
        bridge.notify("ant-py 已连接",
                      f"节点: {ENGINE.selected.get('Description', '')}")
        proxycore.log("VPN 已建立 (tun fd=%s)" % fd)
        return True


def stop_vpn():
    global _tun2socks, _tun_fd_obj, _running
    with _lock:
        if _tun2socks is not None:
            try:
                _tun2socks.terminate()
                _tun2socks.wait(timeout=5)
            except Exception:
                try:
                    _tun2socks.kill()
                except Exception:
                    pass
            _tun2socks = None
        if _tun_fd_obj is not None:
            try:
                _tun_fd_obj.close()
            except Exception:
                pass
            _tun_fd_obj = None
        _running = False
        ENGINE.stop()
        try:
            import bridge
            bridge.notify("ant-py 已断开", "VPN 已停止")
        except Exception:
            pass
        proxycore.log("VPN 已断开")
