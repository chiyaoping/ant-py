"""service — p4a 前台服务入口：跑代理引擎 + tun2socks。

p4a 的 services=Vpn:service.py:foreground 会在 VPN 授权后由 main.py startService
拉起本服务。服务里：
  1. 启动 proxycore.ENGINE（SOCKS5/HTTP 127.0.0.1:1080）
  2. 建 VpnService TUN（fd），pass_fds 交给 tun2socks 子进程
  3. 断开时按 tun2socks → TUN fd → ENGINE 的顺序清理
"""
import json
import os
import subprocess
import sys
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import proxycore
import bridge

ENGINE = proxycore.ENGINE
STATS = proxycore.STATS

_tun2socks = None
_tun_fd_obj = None
_lock = threading.Lock()
_vpn_running = threading.Event()

# main.py 经 unix socket 或 shared state 传入的指令（简化：轮询状态文件）
STATE_FILE = os.path.join(os.environ.get("HOME", "."), "vpn_state.json")


def _read_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"connected": False}


def _write_state(connected: bool):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"connected": connected, "ts": time.time()}, f)
    except Exception:
        pass


def start_vpn():
    """建 TUN + 启 tun2socks。Engine 由服务常驻逻辑保证已启动。"""
    global _tun2socks, _tun_fd_obj
    with _lock:
        if _vpn_running.is_set():
            return True
        if not ENGINE.running:
            ENGINE.start()
        cfg = ENGINE
        dns = "1.1.1.1"
        fd, fd_obj = bridge.build_vpn_fd(cfg.bind_addr, cfg.listen_port, dns)
        cmd = [
            bridge.tun2socks_cmd(),
            "--netif-ipaddr", "26.26.26.1",
            "--netif-netmask", "255.255.255.0",
            "--socks-server-addr", f"{cfg.bind_addr}:{cfg.listen_port}",
            "--proxy-protocol", "socks5",
            "--mtu", "1500",
            # fd 由 Android 侧 hev-socks5-tunnel 的 --tunfd 参数读取
            "--tunfd", str(fd),
            "--enable-udp",
            "--log-level", "warn",
        ]
        _tun2socks = subprocess.Popen(cmd, pass_fds=(fd,))
        _tun_fd_obj = fd_obj
        _vpn_running.set()
        _write_state(True)
        bridge.notify("ant-py 已连接", f"节点: {ENGINE.selected.get('Description', '')}")
        proxycore.log("VPN 已建立 (tun fd=%s)" % fd)
        return True


def stop_vpn():
    global _tun2socks, _tun_fd_obj
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
        _vpn_running.clear()
        _write_state(False)
        ENGINE.stop()
        bridge.notify("ant-py 已断开", "VPN 已停止")
        proxycore.log("VPN 已断开")


def _watchdog():
    """前台服务守护：按状态文件响应连接/断开指令，并保持通知。"""
    last_connected = None
    while True:
        state = _read_state()
        connected = bool(state.get("connected"))
        if connected != last_connected:
            try:
                if connected:
                    start_vpn()
                else:
                    stop_vpn()
            except Exception as e:
                proxycore.log("VPN 切换失败: %s" % e)
                _write_state(False)
            last_connected = _read_state().get("connected")
        time.sleep(1)


# p4a foreground service 入口
if __name__ == "__main__":
    threading.Thread(target=_watchdog, daemon=True).start()
    bridge.notify("ant-py 服务已启动", "等待连接指令")
    while True:
        time.sleep(60)
