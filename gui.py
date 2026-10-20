#!/usr/bin/env python3
"""
ant-py GUI — 深色主题桌面窗口（复刻 ant_vpn 布局）
  顶部: 大按钮连接/断开 + 状态
  中部: 节点分组树（双击切换）
  底部: 系统代理开关 / 全局分流 / 流量统计 / 日志
"""
import json, os, socket, sys, threading, time, tkinter as tk
from tkinter import ttk, messagebox

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths
import proxycore
from proxycore import ENGINE, STATS, log

import sysproxy
import tunmode

try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

ICO_PATH = paths.resource(os.path.join("assets", "icon.ico"))

BG      = "#1e1f24"
BG2     = "#26272e"
BG3     = "#2d2e36"
FG      = "#e8e8ea"
FG_DIM  = "#9a9aa2"
ACCENT  = "#4f8cff"
GREEN   = "#3ecf6f"
RED     = "#ff5c5c"
ORANGE  = "#f0a63a"
BORDER  = "#3a3b44"

FLAGS = {"ID":"🇮🇩","GB":"🇬🇧","US":"🇺🇸","SG":"🇸🇬","JP":"🇯🇵","TW":"🇹🇼","AU":"🇦🇺",
         "HK":"🇭🇰","KR":"🇰🇷","DE":"🇩🇪","FR":"🇫🇷","CA":"🇨🇦","NL":"🇳🇱","CN":"🇨🇳"}

def fmt_bytes(n):
    for u in ("B","KB","MB","GB","TB"):
        if n < 1024 or u == "TB": return f"{n:.1f} {u}" if u!="B" else f"{n} B"
        n /= 1024

def probe_node(node, timeout=6):
    """TCP(+TLS)+ws 握手测速，返回 (总ms, OK)"""
    host, port = node["Address"], int(node["Port"])
    proto = (node.get("Protocol") or "ws").lower()
    t0 = time.time()
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        if proto.startswith("wss") or port == 443:
            import ssl, base64, secrets as sk
            ctx = ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
            s = ctx.wrap_socket(s, server_hostname=host)
        import base64, secrets as sk
        key = base64.b64encode(sk.token_bytes(16)).decode()
        ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36")
        s.sendall((f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: Upgrade\r\n"
                   f"User-Agent: {ua}\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\n"
                   f"Sec-WebSocket-Key: {key}\r\n\r\n").encode())
        s.settimeout(timeout)
        resp = s.recv(64)
        ok = b" 101 " in resp.split(b"\r\n",1)[0]
        s.close()
        return (time.time()-t0)*1000, ok
    except Exception:
        return None, False

def _make_tray_icon_image(running: bool):
    """生成托盘图标：蓝底 ants，连接时绿色、断开时灰色。"""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    color = (62, 207, 111, 255) if running else (79, 140, 255, 255)
    d.rounded_rectangle([4, 4, 60, 60], radius=14, fill=color)
    d.ellipse([22, 30, 32, 40], fill=(255, 255, 255, 255))
    d.line([(30, 35), (48, 35)], fill=(255, 255, 255, 255), width=5)
    d.line([(40, 27), (49, 35), (40, 43)], fill=(255, 255, 255, 255), width=5, joint="curve")
    return img

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ant-py")
        self._refresh_title()
        self.configure(bg=BG)
        self.geometry("760x560")
        self.minsize(680, 480)
        self._set_window_icon()
        self._build_style()
        self._build_ui()
        self._probe_results = {}
        self._speed = {}
        self._item_node = {}
        self._tun = tunmode.TunMode(engine_port=ENGINE.listen_port)
        self._tun_busy = False
        self.refresh_nodes()
        self._sync_tun_state()
        self._tick()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Unmap>", self._on_unmap)          # 最小化 → 托盘
        self._tray = None
        self._tray_thread = None
        self._init_tray()

    # ---------- style ----------
    def _refresh_title(self):
        """管理员身份在标题中体现，避免用户不知道当前是不是管理员模式。"""
        t = "ant-py"
        if tunmode.is_admin():
            t += "（管理员）"
        self.title(t)

    def _set_window_icon(self):
        """窗口/任务栏图标。Windows 的 Tk 原生支持 .ico；缺文件时静默跳过。"""
        if not os.path.isfile(ICO_PATH):
            return
        try:
            self.iconbitmap(ICO_PATH)
        except Exception:
            pass

    def _build_style(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure(".", background=BG, foreground=FG, fieldbackground=BG3,
                     bordercolor=BORDER, lightcolor=BG3, darkcolor=BG3)
        st.configure("Treeview", background=BG2, foreground=FG, fieldbackground=BG2,
                     bordercolor=BORDER, rowheight=26)
        st.map("Treeview", background=[("selected", ACCENT)],
               foreground=[("selected", "#ffffff")])
        st.configure("Treeview.Heading", background=BG3, foreground=FG_DIM,
                     bordercolor=BORDER, relief="flat")
        st.configure("TCheckbutton", background=BG2, foreground=FG)
        st.map("TCheckbutton", background=[("active", BG2)])
        st.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=BG3,
                     bordercolor=BG3, lightcolor=ACCENT, darkcolor=ACCENT)

    # ---------- ui ----------
    def _build_ui(self):
        # === header: status + big button ===
        head = tk.Frame(self, bg=BG2, pady=14, padx=18)
        head.pack(fill="x")
        self.btn_connect = tk.Button(head, text="连  接", font=("Segoe UI", 15, "bold"),
             bg=ACCENT, fg="#ffffff", activebackground="#3d78e0", activeforeground="#ffffff",
             relief="flat", bd=0, cursor="hand2", width=10, pady=6, command=self.toggle_connect)
        self.btn_connect.pack(side="left")
        self.lbl_status = tk.Label(head, text="未连接", font=("Segoe UI", 16, "bold"),
                                   bg=BG2, fg=FG_DIM)
        self.lbl_status.pack(side="left", padx=(22, 0))
        self.lbl_node_now = tk.Label(head, text="", font=("Segoe UI", 10),
                                     bg=BG2, fg=FG_DIM, anchor="w", justify="left")
        self.lbl_node_now.pack(side="left", padx=(16, 0), fill="x", expand=True)

        # === toolbar: switches ===
        bar = tk.Frame(self, bg=BG, pady=7, padx=18)
        bar.pack(fill="x")
        self.var_sys = tk.BooleanVar(value=sysproxy.status()[0])
        cb_sys = tk.Checkbutton(bar, text="系统代理（WinINet）", variable=self.var_sys,
             command=self._toggle_sys, bg=BG, fg=FG, selectcolor=BG3,
             activebackground=BG, activeforeground=FG, highlightthickness=0,
             font=("Segoe UI", 10), cursor="hand2")
        cb_sys.pack(side="left")
        self.var_global = tk.BooleanVar(value=ENGINE.is_global)
        cb_glb = tk.Checkbutton(bar, text="全局模式（关闭=按名单分流）", variable=self.var_global,
             command=self._toggle_global, bg=BG, fg=FG, selectcolor=BG3,
             activebackground=BG, activeforeground=FG, highlightthickness=0,
             font=("Segoe UI", 10), cursor="hand2")
        cb_glb.pack(side="left", padx=(24, 0))
        self.var_tun = tk.BooleanVar(value=False)
        self.cb_tun = tk.Checkbutton(bar, text="TUN 模式（全软件接管）", variable=self.var_tun,
             command=self._toggle_tun, bg=BG, fg=FG, selectcolor=BG3,
             activebackground=BG, activeforeground=FG, highlightthickness=0,
             font=("Segoe UI", 10), cursor="hand2")
        self.cb_tun.pack(side="left", padx=(24, 0))
        if not tunmode.available():
            self.cb_tun.configure(state="disabled", fg=FG_DIM)
        self.lbl_tun = tk.Label(bar, text="", bg=BG, fg=FG_DIM, font=("Segoe UI", 9))
        self.lbl_tun.pack(side="left", padx=(10, 0))
        self.lbl_speed = tk.Label(bar, text="", bg=BG, fg=FG_DIM, font=("Segoe UI", 9))
        self.lbl_speed.pack(side="right", padx=(0, 12))
        btn_speed = tk.Button(bar, text="⚡ 测速全部节点", font=("Segoe UI", 9),
             bg=BG3, fg=FG, activebackground=BORDER, activeforeground=FG,
             relief="flat", bd=0, cursor="hand2", command=self.speed_all)
        btn_speed.pack(side="right")

        # === node tree ===
        mid = tk.Frame(self, bg=BG, padx=18, pady=6)
        mid.pack(fill="both", expand=True)
        cols = ("cc", "addr", "proto", "ms")
        self.tree = ttk.Treeview(mid, columns=cols, show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="节点 / 分组", anchor="w")
        self.tree.heading("cc", text="地区", anchor="w")
        self.tree.heading("addr", text="地址", anchor="w")
        self.tree.heading("proto", text="协议", anchor="w")
        self.tree.heading("ms", text="延迟", anchor="e")
        self.tree.column("#0", width=230)
        self.tree.column("cc", width=70, anchor="w")
        self.tree.column("addr", width=190, anchor="w")
        self.tree.column("proto", width=60, anchor="w")
        self.tree.column("ms", width=70, anchor="e")
        vsb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._pick_node)
        self.tree.tag_configure("sel", foreground="#7fd08a")
        self.tree.tag_configure("dead", foreground="#77777f")

        # === stats strip ===
        st = tk.Frame(self, bg=BG2, pady=8, padx=18)
        st.pack(fill="x", side="bottom")
        self.lbl_up   = tk.Label(st, text="↑ 0 B",    bg=BG2, fg=ACCENT, font=("Consolas", 11, "bold"))
        self.lbl_down = tk.Label(st, text="↓ 0 B",    bg=BG2, fg=GREEN,  font=("Consolas", 11, "bold"))
        self.lbl_conn = tk.Label(st, text="活动 0",   bg=BG2, fg=FG_DIM, font=("Segoe UI", 10))
        self.lbl_err  = tk.Label(st, text="错误 0",   bg=BG2, fg=FG_DIM, font=("Segoe UI", 10))
        self.lbl_up.pack(side="left", padx=(0, 18)); self.lbl_down.pack(side="left")
        self.lbl_conn.pack(side="right", padx=(18, 0)); self.lbl_err.pack(side="right")
        self.lbl_total = tk.Label(st, text="累计 0", bg=BG2, fg=FG_DIM, font=("Segoe UI", 10))
        self.lbl_total.pack(side="right")

        # === log ===
        lgf = tk.Frame(self, bg=BG, padx=18, pady=6)
        lgf.pack(fill="x", side="bottom")
        self.txt_log = tk.Text(lgf, height=5, bg=BG2, fg=FG_DIM, relief="flat",
              font=("Consolas", 9), state="disabled", insertbackground=FG)
        self.txt_log.pack(fill="x")
        proxycore.LOG_SUBS.append(self._on_log)

    # ---------- actions ----------
    def toggle_connect(self):
        if ENGINE.running:
            # 先拆 TUN 再停引擎：否则 TUN 仍在劫持流量，而它的出站已断，全网会静默失联。
            # 这里以 _tun.running 为准（而不是 var_tun），避免两者不同步时漏拆。
            if self._tun.running or self.var_tun.get():
                self.var_tun.set(False)
                try: self._tun.stop()
                except Exception: pass
                self.lbl_tun.configure(text="", fg=FG_DIM)
            ENGINE.stop()
            if self.var_sys.get(): sysproxy.disable()
        else:
            try:
                ENGINE.start()
                if self.var_sys.get(): sysproxy.enable("127.0.0.1", ENGINE.listen_port)
            except proxycore.PortInUseError as e:
                messagebox.showerror("端口被占用", str(e) + "\n\n"
                    f"现在点「确定」尝试自动结束占用进程 (PID {ENGINE.port_conflict_pid() or '未知'})。")
                try:
                    pid = ENGINE.port_conflict_pid()
                    if pid:
                        import subprocess as _sp
                        _sp.run(f"taskkill /F /PID {pid}", shell=True, capture_output=True, timeout=15)
                        time.sleep(1)
                        ENGINE.start()
                        if self.var_sys.get(): sysproxy.enable("127.0.0.1", ENGINE.listen_port)
                        log(f"已结束占用进程 {pid} 并重新启动代理")
                    else:
                        return
                except Exception as e2:
                    messagebox.showerror("启动失败", f"自动清理失败:\n{e2}")
                    return
            except OSError as e:
                messagebox.showerror("启动失败", f"端口可能被占用:\n{e}")
                return
        self._sync_status()
        self._tray_update_icon()

    def _toggle_sys(self):
        if self.var_sys.get(): sysproxy.enable("127.0.0.1", ENGINE.listen_port)
        else: sysproxy.disable()

    def _toggle_tun(self):
        want = self.var_tun.get()
        # TUN 依赖本地引擎出站，且与系统代理互斥（同一份流量会被处理两次）
        if want and not ENGINE.running:
            self.var_tun.set(False)
            messagebox.showwarning("TUN 模式", "请先点击「连接」启动本地代理，再开启 TUN。")
            return
        if want and self.var_sys.get():
            self.var_sys.set(False)
            sysproxy.disable()
            log("已关闭系统代理（TUN 模式下不需要）")
        if want and not tunmode.is_admin():
            self.var_tun.set(False)
            messagebox.showerror("TUN 模式", "需要管理员权限：\n创建虚拟网卡并修改路由表。\n\n请以管理员身份重新运行 gui.py。")
            return
        self._tun_apply(want)

    def _tun_restart(self):
        """节点变更后重建 TUN：先停（清掉旧排除路由），再按新节点集合启动。"""
        if not self._tun.running:
            return
        self._tun_apply(True, restart=True)

    def _tun_apply(self, want, restart=False):
        if getattr(self, "_tun_busy", False):
            # 重建期间又来了新请求（例如快速连点两个节点）：记下来，等这次做完再跑，
            # 否则第二次请求被静默丢弃，TUN 仍按旧节点排除路由 → 回环。
            if restart:
                self._tun_pending_restart = True
            return
        self._tun_busy = True
        self.cb_tun.configure(state="disabled")
        self.lbl_tun.configure(text="TUN 重建中…" if restart else
                                    ("TUN 启动中…" if want else "TUN 关闭中…"), fg=ORANGE)

        def work():
            err = None
            try:
                if want:
                    # TUN 会接管全部流量。若上游此刻不通，启用了就会让整机静默断网，
                    # 所以先用本地引擎实测一次真实出网，再建网卡。
                    if not restart:
                        self.after(0, lambda: self.lbl_tun.configure(
                            text="TUN 检测上游…", fg=ORANGE))
                    ok, msg = tunmode.preflight(ENGINE.listen_port, timeout=20)
                    if not ok:
                        raise RuntimeError(
                            f"上游代理不可用，未{'重建' if restart else '启动'} TUN。\n{msg}\n\n"
                            f"建议先点「⚡ 测速全部节点」换一个可用节点。")
                    if restart:
                        self._tun.stop()
                    nodes = [n for lst in ENGINE.groups.values() for n in lst]
                    # 分流模式下由内核负责直连判定（引擎自己直连会被 TUN 捞回来递归）
                    bypass = None if ENGINE.is_global else tunmode.load_bypass_domains()
                    self._tun.start(nodes, ENGINE.selected, bypass_domains=bypass)
                else:
                    self._tun.stop()
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
            self.after(0, lambda: self._tun_done(want, err))

        threading.Thread(target=work, daemon=True).start()

    def _tun_done(self, want, err):
        self._tun_busy = False
        self.cb_tun.configure(state="normal")
        if err:
            # 以实际状态为准，而不是简单取反：重建失败时旧 TUN 仍在运行。
            actual = bool(self._tun.running)
            self.var_tun.set(actual)
            self.lbl_tun.configure(
                text="TUN 已接管全部流量" if actual else "", fg=GREEN if actual else FG_DIM)
            log(f"TUN 失败: {err}")
            messagebox.showerror("TUN 模式", err)
            if actual:
                self.after(15000, self._tun_watchdog)
            return
        self.var_tun.set(want)
        self.lbl_tun.configure(
            text="TUN 已接管全部流量" if want else "", fg=GREEN if want else FG_DIM)
        log("TUN 模式已开启" if want else "TUN 模式已关闭")
        self._tray_update_icon()
        if want:
            self._tun_wd_fails = 0
            self.after(15000, self._tun_watchdog)
        # 处理重建期间被合并掉的请求
        if getattr(self, "_tun_pending_restart", False) and self._tun.running:
            self._tun_pending_restart = False
            self.after(200, self._tun_restart)

    def _toggle_global(self):
        ENGINE.is_global = self.var_global.get()
        cfgp = paths.data_file("config.json")
        cfg = json.load(open(cfgp, encoding="utf-8"))
        cfg["is_global"] = ENGINE.is_global
        json.dump(cfg, open(cfgp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        log(f"全局模式 = {ENGINE.is_global}")

    def _tun_watchdog(self):
        """TUN 运行时周期性确认上游仍通；一旦上游死掉就自动拆 TUN。

        TUN 会接管全机流量，若上游此刻失效而 TUN 还在，整机会静默断网
        （所有应用走 MATCH,PROXY → 死掉的 ant-local，没有兜底）。
        同样地，节点域名若被解析到新的 IP，该 IP 不在排除表里会被 TUN 劫持成回环，
        表现为"刚还正常，突然全断"——这个看门狗一并兜住。
        """
        if not getattr(self, "_tun.running", False):
            return
        if getattr(self, "_tun_busy", False) or getattr(self, "_wd_busy", False):
            self.after(15000, self._tun_watchdog)
            return
        self._wd_busy = True

        def work():
            ok, msg = tunmode.preflight(ENGINE.listen_port, timeout=10)
            self.after(0, lambda: self._wd_done(ok, msg))

        threading.Thread(target=work, daemon=True).start()

    def _wd_done(self, ok, msg):
        self._wd_busy = False
        if not self._tun.running:
            return
        if ok:
            self._tun_wd_fails = 0
        else:
            self._tun_wd_fails = getattr(self, "_tun_wd_fails", 0) + 1
            log(f"TUN 看门狗：上游探测失败 {self._tun_wd_fails}/3（{msg}）")
            if self._tun_wd_fails >= 3:
                self._tun_wd_fails = 0
                log("TUN 看门狗：上游持续不可用，自动关闭 TUN 以恢复联网")
                if not HAS_TRAY or not self._tray:
                    messagebox.showwarning("TUN 已自动关闭",
                        f"上游代理持续不可用，已关闭 TUN 以免整机断网。\n\n{msg}")
                self.var_tun.set(False)
                self._tun_apply(False)
                return
        self.after(15000, self._tun_watchdog)

    def _pick_node(self, ev):
        item = self.tree.identify_row(ev.y)
        if not item: return
        vals = self.tree.item(item, "values")
        meta = self._probe_results.get(item)
        node = self._item_node.get(item)
        if not node: return   # group row
        if self.tree.parent(item) == "":
            return
        ENGINE.selected = dict(node)
        cfgp = paths.data_file("config.json")
        cfg = json.load(open(cfgp, encoding="utf-8"))
        cfg["selected"] = dict(node)
        json.dump(cfg, open(cfgp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        log(f"切换节点 → {node['Description']}")
        self._sync_status()
        self.refresh_nodes(keep_open=True)
        if self.var_tun.get():
            # 节点 IP 集合变了，需重建 TUN 配置重写排除路由，否则新节点可能被 TUN 劫持成回环
            self._tun_restart()

    def speed_all(self):
        if getattr(self, "_speeding", False): return
        self._speeding = True
        self.lbl_speed.configure(text="测速中…", fg=ORANGE)
        log("开始测速全部节点…")
        def work():
            res = {}
            alln = [(g, n) for g, lst in ENGINE.groups.items() for n in lst]
            for g, n in alln:
                ms, ok = probe_node(n)
                res[(g, n["Address"], str(n["Port"]))] = (ms, ok)
            self.after(0, lambda: self._speed_done(res))
        threading.Thread(target=work, daemon=True).start()

    def _speed_done(self, res):
        self._speeding = False
        self._speed = res
        self.refresh_nodes(keep_open=True)
        best = min([(v[0], k) for k, v in res.items() if v[0]], default=None)
        if best:
            self.lbl_speed.configure(text=f"最快 {best[0]:.0f} ms", fg=GREEN)
            log(f"测速完成，最快 {best[0]:.0f}ms")
        else:
            self.lbl_speed.configure(text="全部超时", fg=RED)
            log("测速完成，无可用节点")

    # ---------- rendering ----------
    def refresh_nodes(self, keep_open=False):
        self._item_node = {}
        self.tree.delete(*self.tree.get_children())
        open_groups = set()
        if keep_open:
            for iid in self.tree.get_children():
                pass
        for gname in sorted(ENGINE.groups.keys(), key=lambda x: int(x) if x.isdigit() else 99):
            nodes = ENGINE.groups[gname]
            giid = self.tree.insert("", "end", open=True,
                    text=f"分组 {gname}（{len(nodes)} 节点）", values=("", "", "", ""))
            def _lat(node, _g=gname):
                """有测速结果 → 按延迟升序；未测/超时 → 排到最后（稳定排序保留原序）。"""
                ms, _ = (self._speed or {}).get((_g, node["Address"], str(node["Port"])), (None, True))
                return (0, ms) if ms else (1, 0.0)
            for n in sorted(nodes, key=_lat):
                ms, ok = (self._speed or {}).get((gname, n["Address"], str(n["Port"])), (None, True))
                cc = n.get("CountryCode", "")
                flag = FLAGS.get(cc.upper(), "🌐")
                is_sel = (ENGINE.selected.get("Address") == n["Address"] and
                          ENGINE.selected.get("Port") == n["Port"])
                ms_txt = f"{ms:.0f} ms" if ms else ("超时" if not ok and ms is None and ok is False else "—")
                tags = []
                if is_sel: tags.append("sel")
                if ms is None and not ok and (gname, n["Address"], str(n["Port"])) in (self._speed or {}):
                    tags.append("dead")
                label = ("● " if is_sel else "") + n["Description"]
                iid = self.tree.insert(giid, "end", text=f"{flag} {label}",
                        values=(cc, f"{n['Address']}:{n['Port']}", n.get("Protocol","ws"), ms_txt),
                        tags=tags)
                self._item_node[iid] = n
        self._sync_status()

    def _sync_status(self):
        if ENGINE.running:
            self.btn_connect.configure(text="断  开", bg=RED, activebackground="#e04c4c")
            self.lbl_status.configure(text="已连接", fg=GREEN)
            n = ENGINE.selected
            self.lbl_node_now.configure(
                text=f"{n['Description']}\n{n['Address']}:{n['Port']} · {n.get('Protocol')} · {n.get('Method')}")
        else:
            self.btn_connect.configure(text="连  接", bg=ACCENT, activebackground="#3d78e0")
            self.lbl_status.configure(text="未连接", fg=FG_DIM)
            n = ENGINE.selected
            self.lbl_node_now.configure(
                text=f"已选: {n['Description']}\n{n['Address']}:{n['Port']} · {n.get('Protocol')}")

    def _sync_tun_state(self):
        """启动时清理上次异常退出残留的内核，避免旧路由继续劫持流量。"""
        st = self._tun.status()
        if tunmode.adapter_present() and not st["running"]:
            try:
                self._tun.cleanup_stale()
                log("已清理残留的 TUN 内核进程")
            except Exception:
                pass
        if st["running"]:
            self.var_tun.set(True)
            self.lbl_tun.configure(text="TUN 已接管全部流量", fg=GREEN)

    # ---------- timers ----------
    def _tick(self):
        with STATS._lock:
            up, down = STATS.up, STATS.down
            du, dd = STATS.direct_up, STATS.direct_down
            conns, total, errs = STATS.conns, STATS.total, STATS.errors
        self.lbl_up.configure(text=f"↑ {fmt_bytes(up+du)}")
        self.lbl_down.configure(text=f"↓ {fmt_bytes(down+dd)}")
        self.lbl_conn.configure(text=f"活动 {conns}")
        self.lbl_total.configure(text=f"累计 {total}")
        self.lbl_err.configure(text=f"错误 {errs}", fg=ORANGE if errs else FG_DIM)
        self.after(1000, self._tick)

    def _on_log(self, line):
        def append():
            self.txt_log.configure(state="normal")
            self.txt_log.insert("end", line + "\n")
            self.txt_log.see("end")
            self.txt_log.configure(state="disabled")
        self.after(0, append)

    # ---------- tray ----------
    def _init_tray(self):
        if not HAS_TRAY: return
        self._tray_running = True
        self._tray_icon_image = _make_tray_icon_image(False)
        self._tray = pystray.Icon(
            "ant-py", self._tray_icon_image, "ant-py（未连接）",
            menu=pystray.Menu(
                pystray.MenuItem("显示主窗口", self._tray_show, default=True),
                pystray.MenuItem(self._tray_toggle_text, self._tray_toggle),
                pystray.MenuItem(self._tray_tun_text, self._tray_tun_toggle),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", self._tray_quit),
            ))
        self._tray_thread = threading.Thread(target=self._tray.run, daemon=True)
        self._tray_thread.start()

    def _tray_update_icon(self):
        """状态变化时刷新托盘图标与提示。"""
        if not (HAS_TRAY and self._tray): return
        def work():
            try:
                self._tray.icon = _make_tray_icon_image(ENGINE.running)
                self._tray.title = f"ant-py（{'已连接 ' + ENGINE.selected['Description'] if ENGINE.running else '未连接'}）"
            except Exception: pass
        threading.Thread(target=work, daemon=True).start()

    def _tray_show(self, icon=None, item=None):
        self.after(0, self._restore)

    def _tray_toggle_text(self, item):
        return "断开代理" if ENGINE.running else "连接代理"

    def _tray_toggle(self, icon=None, item=None):
        self.after(0, self.toggle_connect)

    def _tray_tun_text(self, item):
        # 托盘菜单在 pystray 线程里求值，不能读 Tk 变量（会抛 "main thread is not in main loop"），
        # 所以这里只看 TunMode 上的普通属性。
        return "关闭 TUN 模式" if self._tun.running else "开启 TUN 模式"

    def _tray_tun_toggle(self, icon=None, item=None):
        def go():
            self.var_tun.set(not self.var_tun.get())
            self._toggle_tun()
        self.after(0, go)

    def _tray_quit(self, icon=None, item=None):
        self.after(0, self._real_quit)

    def _on_unmap(self, ev):
        # 窗口被最小化（iconified）时隐藏到托盘
        if ev.widget is self and self.state() == "iconic":
            self.withdraw()
            self._tray_notify()

    def _hide_to_tray(self):
        self.withdraw()

    def _restore(self):
        try:
            self.state("normal")
            self.deiconify()
            self.lift()
            self.focus_force()
        except Exception: pass

    def _tray_notify(self):
        if HAS_TRAY and self._tray:
            try:
                st = "已连接" if ENGINE.running else "未连接"
                self._tray.notify(f"代理已最小化到托盘（{st}）", "ant-py")
            except Exception: pass

    def _on_close(self):
        # 点 X → 隐藏到托盘（不退出）
        self.withdraw()
        self._tray_notify()

    def _real_quit(self):
        if self._tun.running or self.var_tun.get():
            try: self._tun.stop()
            except Exception: pass
        if ENGINE.running:
            ENGINE.stop()
            if self.var_sys.get(): sysproxy.disable()
        if self._tray:
            try: self._tray.stop()
            except Exception: pass
        self.destroy()

if __name__ == "__main__":
    App().mainloop()
