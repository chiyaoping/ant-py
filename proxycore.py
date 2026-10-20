#!/usr/bin/env python3
"""proxycore — 把 proxy.py 包成可启动/停止的引擎，供 GUI 调用。"""
import base64, hashlib, importlib, json, os, socket, ssl, struct, threading, secrets, re, subprocess, time


class PortInUseError(OSError):
    """listen_port 已被其他进程占用（典型原因：多开的 proxy.py / ant-py.exe 残留）。"""

import paths

HERE = os.path.dirname(os.path.abspath(__file__))

class Stats:
    def __init__(self):
        self.up = 0          # bytes sent to node
        self.down = 0        # bytes received from node
        self.direct_up = 0
        self.direct_down = 0
        self.conns = 0       # active connections
        self.total = 0       # total connections handled
        self.errors = 0
        self.started_at = None
        self._lock = threading.Lock()

STATS = Stats()
LOG_LINES = []
LOG_SUBS = []            # callbacks(str)
_listeners = []

def log(msg):
    line = time.strftime("%H:%M:%S ") + str(msg)
    LOG_LINES.append(line)
    if len(LOG_LINES) > 500: del LOG_LINES[0]
    for cb in list(LOG_SUBS):
        try: cb(line)
        except Exception: pass

class Engine:
    def __init__(self):
        self.srv = None
        self.thread = None
        self.running = False
        # TUN 生效时必须置 True：此时引擎自己开直连会被 TUN 重新捞回来，
        # 形成 引擎→TUN→内核→SOCKS5→引擎 的递归。直连改由内核判定。
        self.bypass_disabled = False
        self.reload_config()

    def reload_config(self):
        cfg = json.load(open(paths.data_file("config.json"), encoding="utf-8"))
        self.listen_port = int(cfg.get("listen_port", 1080))
        self.bind_addr = cfg.get("bind_addr", "127.0.0.1")
        self.is_global = cfg.get("is_global", True)
        self.selected = cfg["selected"]
        self.aff = int(cfg.get("aff", 0))
        nodes = json.load(open(paths.resource("nodes.json"), encoding="utf-8"))
        self.groups = nodes.get("groups", {})
        bp = paths.resource("bypass_list.txt")
        self.bypass = set()
        if os.path.exists(bp):
            with open(bp, encoding="utf-8") as f:
                self.bypass = {ln.strip().lower() for ln in f if ln.strip()}

    # ---- lifecycle ----
    def port_conflict_pid(self):
        """返回已占用 listen_port 的其他进程 PID（排除自己），用于诊断多开抢端口。"""
        try:
            out = subprocess.run(
                f'netstat -ano | findstr ":{self.listen_port} " | findstr "LISTENING"',
                shell=True, capture_output=True, timeout=10
            ).stdout.decode("gbk", "replace")
        except Exception:
            return None
        import re as _re
        for m in _re.finditer(r"LISTENING\s+(\d+)", out):
            pid = int(m.group(1))
            if pid != os.getpid():
                return pid
        return None

    def start(self):
        if self.running: return False
        conflict = self.port_conflict_pid()
        if conflict is not None:
            raise PortInUseError(
                f"端口 {self.listen_port} 已被其他进程占用 (PID {conflict})。\n"
                "可能是之前多开的 proxy.py / ant-py.exe / gui.py 残留。\n"
                "请在任务管理器结束该进程，或重启本程序。")
        self.reload_config()
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind((self.bind_addr, self.listen_port))
        self.srv.listen(128)
        self.srv.settimeout(0.5)
        self.running = True
        STATS.started_at = time.time()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        log(f"代理已启动 {self.bind_addr}:{self.listen_port}  节点: {self.selected['Description']}")
        return True

    def stop(self):
        if not self.running: return False
        self.running = False
        try: self.srv.close()
        except Exception: pass
        STATS.started_at = None
        log("代理已停止")
        return True

    def _loop(self):
        while self.running:
            try:
                c, a = self.srv.accept()
                threading.Thread(target=self._handle, args=(c, a), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break

    # ---- protocol (ported from proxy.py) ----
    def _handle(self, conn, _):
        try:
            conn.settimeout(60)
            first = conn.recv(256)
            if not first: return
            STATS.total += 1
            STATS.conns += 1
            if first[0] == 5:
                host, port, target, rewrite = self._socks5(conn, first)
            else:
                host, port, target, rewrite = self._http(conn, first)
            if not host: return
            if not self.is_global and not self.bypass_disabled and self._bypass(host):
                try:
                    remote = socket.create_connection((host, port), timeout=10)
                    if first[0] == 5: conn.sendall(b"\x05\x00\x00\x01" + b"\x00"*6)
                    else: conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    t1 = threading.Thread(target=self._pump, args=(conn, remote, False), daemon=True)
                    t2 = threading.Thread(target=self._pump, args=(remote, conn, True), daemon=True)
                    t1.start(); t2.start(); t1.join(); t2.join()
                except Exception:
                    STATS.errors += 1
                return
            node = self.selected
            try:
                up, crypto = self._connect_upstream(node, target)
            except Exception as e:
                STATS.errors += 1
                log(f"上游失败: {host}:{port} {type(e).__name__}")
                if first[0] == 5: conn.sendall(b"\x05\x01\x00\x01" + b"\x00"*6)
                else: conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                return
            if rewrite:
                up.sendall(crypto.encrypt(rewrite))
            elif first[0] == 5: conn.sendall(b"\x05\x00\x00\x01" + b"\x00"*6)
            else: conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            t1 = threading.Thread(target=self._up_loop, args=(conn, up, crypto), daemon=True)
            t2 = threading.Thread(target=self._down_loop, args=(up, conn, crypto), daemon=True)
            t1.start(); t2.start(); t1.join(); t2.join()
        except Exception:
            STATS.errors += 1
        finally:
            STATS.conns = max(0, STATS.conns - 1)
            try: conn.close()
            except Exception: pass

    def _socks5(self, conn, first):
        conn.sendall(b"\x05\x00")
        req = conn.recv(300)
        if len(req) < 7 or req[0] != 5 or req[1] != 1:
            return None, None, None, None
        atyp = req[3]
        if atyp == 0x01:   host, port = socket.inet_ntoa(req[4:8]), struct.unpack(">H", req[8:10])[0]
        elif atyp == 0x03:
            n = req[4]; host = req[5:5+n].decode("idna"); port = struct.unpack(">H", req[5+n:7+n])[0]
        elif atyp == 0x04: host, port = socket.inet_ntop(socket.AF_INET6, req[4:20]), struct.unpack(">H", req[20:22])[0]
        else: return None, None, None, None
        return host, port, self._addr_bytes(host, port), None

    def _http(self, conn, first):
        buf = first
        while b"\r\n\r\n" not in buf and len(buf) < 8192:
            c = conn.recv(4096)
            if not c: return None, None, None, None
            buf += c
        m = re.match(rb"CONNECT\s+([A-Za-z0-9._\-]+):(\d+)\s+HTTP/\d", buf)
        if m:
            host, port = m.group(1).decode(), int(m.group(2))
            return host, port, self._addr_bytes(host, port), None
        m = re.match(rb"([A-Z]+)\s+https?://([^/:]+)(?::(\d+))?(/\S*)?\s+HTTP/\d", buf)
        if m:
            host = m.group(2).decode(); port = int(m.group(3) or 80)
            path = m.group(4) or b"/"
            lines = buf.split(b"\r\n")
            lines[0] = m.group(1) + b" " + path + b" HTTP/1.1"
            return host, port, self._addr_bytes(host, port), b"\r\n".join(lines)
        conn.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
        return None, None, None, None

    def _addr_bytes(self, host, port):
        try:
            return bytes([0x01]) + socket.inet_aton(host) + struct.pack(">H", port)
        except OSError:
            pass
        hb = host.encode("idna") if any(ord(ch) > 127 for ch in host) else host.encode()
        return bytes([0x03, len(hb)]) + hb + struct.pack(">H", port)

    def _bypass(self, host):
        h = host.lower()
        return any(h == d or h.endswith("." + d) for d in self.bypass)

    @staticmethod
    def _evp(password: bytes, key_len: int = 32) -> bytes:
        d, prev = b"", b""
        while len(d) < key_len:
            prev = hashlib.md5(prev + password).digest()
            d += prev
        return d[:key_len]

    class _SS:
        def __init__(self, password: str):
            self.key = Engine._evp(password.encode("utf-8"), 32)
            self._enc = None; self._dec = None; self._pend = b""
        def encrypt(self, data: bytes) -> bytes:
            if self._enc is None:
                from cryptography.hazmat.decrepit.ciphers.modes import CFB
                from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
                iv = secrets.token_bytes(16)
                self._enc = Cipher(algorithms.AES(self.key), CFB(iv)).encryptor()
                return iv + self._enc.update(data)
            return self._enc.update(data)
        def decrypt_feed(self, data: bytes) -> bytes:
            if self._dec is None:
                self._pend += data
                if len(self._pend) < 16: return b""
                iv, rest = self._pend[:16], self._pend[16:]
                from cryptography.hazmat.decrepit.ciphers.modes import CFB
                from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
                self._dec = Cipher(algorithms.AES(self.key), CFB(iv)).decryptor()
                self._pend = b""
                return self._dec.update(rest) if rest else b""
            return self._dec.update(data)

    def _connect_upstream(self, node, target):
        host, port = node["Address"], int(node["Port"])
        proto = (node.get("Protocol") or "ws").lower()
        is_tls = proto.startswith("wss") or port == 443
        raw = socket.create_connection((host, port), timeout=15)
        if is_tls:
            ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
            raw = ctx.wrap_socket(raw, server_hostname=host)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36")
        raw.sendall((f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: Upgrade\r\n"
                     f"User-Agent: {ua}\r\nUpgrade: websocket\r\n"
                     f"Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {key}\r\n\r\n").encode())
        raw.settimeout(15)
        resp = b""
        while b"\r\n\r\n" not in resp:
            c = raw.recv(4096)
            if not c: raise ConnectionError("ws EOF")
            resp += c
        if b" 101 " not in resp.split(b"\r\n", 1)[0]:
            raise ConnectionError("ws refused")
        crypto = self._SS(node["Password"])
        raw.sendall(crypto.encrypt(struct.pack(">i", self.aff) + target))
        raw.settimeout(None)
        return raw, crypto

    # ---- pumps with byte counting ----
    def _pump(self, src, dst, is_down):
        try:
            while True:
                d = src.recv(65536)
                if not d: break
                with STATS._lock:
                    if is_down: STATS.direct_down += len(d)
                    else: STATS.direct_up += len(d)
                dst.sendall(d)
        except Exception: pass
        finally:
            try: dst.shutdown(socket.SHUT_WR)
            except Exception: pass

    def _up_loop(self, client, up, crypto):
        try:
            while True:
                d = client.recv(65536)
                if not d: break
                up.sendall(crypto.encrypt(d))
                with STATS._lock: STATS.up += len(d)
        except Exception: pass
        finally:
            try: up.shutdown(socket.SHUT_WR)
            except Exception: pass

    def _down_loop(self, up, client, crypto):
        try:
            while True:
                d = up.recv(65536)
                if not d: break
                client.sendall(crypto.decrypt_feed(d))
                with STATS._lock: STATS.down += len(d)
        except Exception: pass
        finally:
            try: client.shutdown(socket.SHUT_WR)
            except Exception: pass

ENGINE = Engine()
