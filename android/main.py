"""main — Flet UI：ant-py 安卓版。

复用桌面版 proxycore.ENGINE / STATS / LOG_SUBS 引擎。
VPN 全接管在主进程内完成：bridge（pyjnius 建 TUN）+ vpnctl（tun2socks 子进程）。
"""
import json
import os
import socket
import sys
import threading
import time

import flet as ft

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import paths

IS_ANDROID = "ANDROID_DATA" in os.environ or "ANDROID_ROOT" in os.environ

import proxycore

ENGINE = proxycore.ENGINE
STATS = proxycore.STATS

FLAGS = {"ID":"🇮🇩","GB":"🇬🇧","US":"🇺🇸","SG":"🇸🇬","JP":"🇯🇵","TW":"🇹🇼","AU":"🇦🇺",
         "HK":"🇭🇰","KR":"🇰🇷","DE":"🇩🇪","FR":"🇫🇷","CA":"🇨🇦","NL":"🇳🇱","CN":"🇨🇳"}

BG     = "#1b1c22"
BG2    = "#26272e"
BG3    = "#2d2e36"
FG     = "#e8e8ea"
FG_DIM = "#9a9aa2"
ACCENT = "#4f8cff"
GREEN  = "#3ecf6f"
RED    = "#ff5c5c"


def fmt_bytes(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.1f} {u}" if u != "B" else f"{n} B"
        n /= 1024


def probe_node(node, timeout=6):
    """TCP(+TLS)+ws 握手测速，返回 (总ms, OK)——与桌面 gui.py 同款。"""
    host, port = node["Address"], int(node["Port"])
    proto = (node.get("Protocol") or "ws").lower()
    t0 = time.time()
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        if proto.startswith("wss") or port == 443:
            import ssl, base64, secrets as sk
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            s = ctx.wrap_socket(s, server_hostname=host)
        key = base64.b64encode(sk.token_bytes(16)).decode()
        ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36")
        s.sendall((f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: Upgrade\r\n"
                   f"User-Agent: {ua}\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\n"
                   f"Sec-WebSocket-Key: {key}\r\n\r\n").encode())
        s.settimeout(timeout)
        resp = s.recv(64)
        ok = b" 101 " in resp.split(b"\r\n", 1)[0]
        s.close()
        return (time.time() - t0) * 1000, ok
    except Exception:
        return None, False


class AntApp:
    def __init__(self, page: ft.Page):
        self.page = page
        page.title = "ant-py"
        page.bgcolor = BG
        page.padding = 12
        page.theme_mode = ft.ThemeMode.DARK

        self.speed = {}
        self.log_lines = []

        self.status_dot = ft.Container(width=12, height=12, border_radius=6, bgcolor=RED)
        self.status_text = ft.Text("未连接", color=FG, size=15, weight=ft.FontWeight.BOLD)
        self.node_label = ft.Text("", color=FG_DIM, size=12)

        self.btn_connect = ft.ElevatedButton(
            "连接", icon=ft.icons.PLAY_ARROW, bgcolor=GREEN, color="#111",
            on_click=self.on_connect)
        self.btn_disconnect = ft.ElevatedButton(
            "断开", icon=ft.icons.STOP, bgcolor=RED, color="#111",
            on_click=self.on_disconnect)

        self.switch_global = ft.Switch(
            label="全局模式（关=按内置名单分流）", value=ENGINE.is_global,
            active_color=ACCENT, on_change=self.on_toggle_global)

        self.stats_text = ft.Text("", color=FG_DIM, size=11, selectable=True)
        self.btn_speed = ft.ElevatedButton(
            "⚡ 全节点测速", icon=ft.icons.BOLT, bgcolor=ACCENT, color="#111",
            on_click=self.on_speed_all)
        self.speed_hint = ft.Text("", color=FG_DIM, size=11)

        # 节点区：平铺 ListView（组头 + 节点行），比 ExpansionTile 可靠
        self.nodes_list = ft.ListView(expand=True, spacing=2, padding=4)

        self.log_text = ft.Text("", color=FG_DIM, size=10, selectable=True,
                                font_family="monospace")
        self.log_container = ft.Container(
            content=ft.Column([self.log_text], scroll=ft.ScrollMode.AUTO, auto_scroll=True),
            bgcolor=BG3, border_radius=8, padding=8, height=140)

        page.add(
            ft.Row([self.status_dot, self.status_text, self.node_label], spacing=8),
            ft.Row([self.btn_connect, self.btn_disconnect], spacing=8),
            self.switch_global,
            self.stats_text,
            ft.Row([self.btn_speed, self.speed_hint], spacing=8),
            ft.Container(content=self.nodes_list, expand=True, bgcolor=BG2,
                         border_radius=8, padding=4),
            ft.Container(content=self.log_container, height=150),
        )

        proxycore.LOG_SUBS.append(self._on_log)
        self.refresh_nodes()
        self._sync_ui()
        self._start_timer()

    # ---------- timers / log ----------
    def _start_timer(self):
        t = threading.Timer(1.0, self._tick_timer)
        t.daemon = True
        t.start()

    def _tick_timer(self):
        try:
            s = STATS
            self.stats_text.value = (
                f"↑ {fmt_bytes(s.up)}   ↓ {fmt_bytes(s.down)}   "
                f"连接 {s.conns}   总计 {s.total}   错误 {s.errors}")
            self.page.update()
        except Exception:
            pass
        self._start_timer()

    def _on_log(self, line: str):
        self.log_lines.append(line)
        if len(self.log_lines) > 200:
            del self.log_lines[0]
        self.log_text.value = "\n".join(self.log_lines[-60:])
        try:
            self.page.update()
        except Exception:
            pass

    def _sync_ui(self):
        connected = False
        if IS_ANDROID:
            try:
                import vpnctl
                connected = vpnctl.is_running()
            except Exception:
                connected = False
        else:
            connected = ENGINE.running
        if connected:
            self.status_dot.bgcolor = GREEN
            self.status_text.value = "已连接"
            self.status_text.color = GREEN
        else:
            self.status_dot.bgcolor = RED
            self.status_text.value = "未连接"
            self.status_text.color = FG
        sel = ENGINE.selected or {}
        self.node_label.value = sel.get("Description", "")
        try:
            self.page.update()
        except Exception:
            pass

    # ---------- connect / disconnect ----------
    def on_connect(self, e):
        if not IS_ANDROID:
            try:
                ENGINE.start()
                proxycore.log("桌面调试：本地代理已启动（无 VPN）")
            except Exception as ex:
                proxycore.log(f"启动失败: {ex}")
            self._sync_ui()
            return
        def worker():
            try:
                import bridge, vpnctl
                if not bridge.vpn_prepare():
                    proxycore.log("等待 VPN 授权…授权后请再点一次「连接」")
                    self._sync_ui()
                    return
                vpnctl.start_vpn()
                proxycore.log("VPN 已连接")
            except Exception as ex:
                proxycore.log(f"VPN 建立失败: {ex}")
            self._sync_ui()
        threading.Thread(target=worker, daemon=True).start()

    def on_disconnect(self, e):
        if not IS_ANDROID:
            ENGINE.stop()
            self._sync_ui()
            return
        def worker():
            try:
                import vpnctl
                vpnctl.stop_vpn()
            except Exception as ex:
                proxycore.log(f"断开失败: {ex}")
            self._sync_ui()
        threading.Thread(target=worker, daemon=True).start()

    # ---------- config / nodes ----------
    def on_toggle_global(self, e):
        ENGINE.is_global = self.switch_global.value
        self._save_config()

    def _save_config(self):
        cfg_path = paths.data_file("config.json")
        try:
            cfg = json.load(open(cfg_path, encoding="utf-8"))
        except Exception:
            cfg = {}
        cfg["is_global"] = ENGINE.is_global
        cfg["selected"] = ENGINE.selected
        cfg["aff"] = ENGINE.aff
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

    def on_speed_all(self, e):
        self.btn_speed.disabled = True
        self.speed_hint.value = "测速中…"
        self.page.update()
        threading.Thread(target=self._speed_all_worker, daemon=True).start()

    def _speed_all_worker(self):
        results = {}
        for gid, nodes in ENGINE.groups.items():
            for n in nodes:
                ms, ok = probe_node(n)
                results[(gid, n["Address"], n["Port"])] = (ms, ok)
        self.speed = results
        for gid, nodes in ENGINE.groups.items():
            nodes.sort(key=lambda n: (results.get((gid, n["Address"], n["Port"]), (None, False))[0]
                                      if results.get((gid, n["Address"], n["Port"]), (None, False))[0] is not None
                                      else 9e9))
        ok_count = sum(1 for v in results.values() if v[1])
        self.speed_hint.value = f"完成：{ok_count}/{len(results)} 可用"
        self.btn_speed.disabled = False
        self.refresh_nodes()
        self._sync_ui()

    def refresh_nodes(self):
        self.nodes_list.controls.clear()
        for gid in sorted(ENGINE.groups.keys(), key=lambda x: int(x)):
            nodes = ENGINE.groups[gid]
            gname = nodes[0].get("Group", f"组 {gid}") if nodes else f"组 {gid}"
            self.nodes_list.controls.append(ft.Container(
                content=ft.Text(f"{gname}（{len(nodes)}）", color=FG_DIM, size=12,
                                weight=ft.FontWeight.BOLD),
                padding=ft.padding.only(left=6, top=6, bottom=2)))
            for n in nodes:
                key = (gid, n["Address"], n["Port"])
                ms, ok = self.speed.get(key, (None, None))
                if ms is not None:
                    ms_str, color = f"{ms:.0f}ms", GREEN
                elif ok is False:
                    ms_str, color = "超时", RED
                else:
                    ms_str, color = "", FG_DIM
                flag = FLAGS.get(n.get("CountryCode", ""), "")
                selected = (ENGINE.selected.get("Address") == n["Address"]
                            and ENGINE.selected.get("Port") == n["Port"])
                self.nodes_list.controls.append(ft.ListTile(
                    leading=ft.Text(flag, size=15),
                    title=ft.Text(n.get("Description", f"{n['Address']}:{n['Port']}"),
                                  color=ACCENT if selected else FG, size=12,
                                  weight=ft.FontWeight.BOLD if selected else ft.FontWeight.NORMAL),
                    subtitle=ft.Text(f"{n['Address']}:{n['Port']}  {ms_str}",
                                     color=color, size=10),
                    dense=True,
                    on_click=lambda e, node=n: self._pick_node(node),
                ))
        try:
            self.page.update()
        except Exception:
            pass

    def _pick_node(self, node):
        ENGINE.selected = node
        self._save_config()
        proxycore.log(f"已选节点: {node.get('Description')} {node['Address']}:{node['Port']}")
        self._sync_ui()
        self.refresh_nodes()


def main(page: ft.Page):
    AntApp(page)


if __name__ == "__main__":
    ft.app(target=main)
