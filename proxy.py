#!/usr/bin/env python3
"""
ant-py — 免登录本地代理客户端
协议（逆向自 ant_vpn.exe 2.0.9.0，已实测打通）:
  本地 SOCKS5/HTTP(127.0.0.1:listen_port)
    → wss 节点: TLS → ws-upgrade(GET / HTTP/1.1, Sec-WebSocket-Key=b64(random16))
      → SS aes-256-cfb (EVP_BytesToKey-MD5, IV 随机16B前缀)
        → 首包明文 = Aff(大端int32) + ATYP+ADDR+PORT, 之后为原始数据流
"""
import base64, hashlib, json, os, re, socket, ssl, struct, sys, threading, secrets

HERE = os.path.dirname(os.path.abspath(__file__))
CFG  = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
NODES = os.path.join(HERE, "nodes.json")
BYPASS_FILE = os.path.join(HERE, "bypass_list.txt")

LISTEN_PORT = int(CFG.get("listen_port", 1080))
BIND_ADDR   = CFG.get("bind_addr", "127.0.0.1")
IS_GLOBAL   = CFG.get("is_global", True)
SELECTED    = CFG["selected"]
AFF         = int(CFG.get("aff", 0))          # 用户 Aff（大端int32，参与首包）

BYPASS_SET = set()
if os.path.exists(BYPASS_FILE):
    with open(BYPASS_FILE, encoding="utf-8") as f:
        BYPASS_SET = {ln.strip().lower() for ln in f if ln.strip()}

WS_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36")
ADDR_V4, ADDR_DOM, ADDR_V6 = 0x01, 0x03, 0x04

def evp_bytes_to_key(password: bytes, key_len: int) -> bytes:
    d, prev = b"", b""
    while len(d) < key_len:
        prev = hashlib.md5(prev + password).digest()
        d += prev
    return d[:key_len]

class SSStream:
    """aes-256-cfb，首包自动带 16B 随机 IV（与 StreamCrypto.EncryptTCP 一致）。"""
    def __init__(self, password: str):
        self.key = evp_bytes_to_key(password.encode("utf-8"), 32)
        self._enc = None
        self._dec = None
        self._pend = b""

    def encrypt(self, data: bytes) -> bytes:
        if self._enc is None:
            from cryptography.hazmat.decrepit.ciphers.modes import CFB
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
            self._iv = secrets.token_bytes(16)
            self._enc = Cipher(algorithms.AES(self.key), CFB(self._iv)).encryptor()
            return self._iv + self._enc.update(data)
        return self._enc.update(data)

    def decrypt_feed(self, data: bytes) -> bytes:
        if self._dec is None:
            self._pend += data
            if len(self._pend) < 16:
                return b""
            iv, rest = self._pend[:16], self._pend[16:]
            from cryptography.hazmat.decrepit.ciphers.modes import CFB
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
            self._dec = Cipher(algorithms.AES(self.key), CFB(iv)).decryptor()
            self._pend = b""
            return self._dec.update(rest) if rest else b""
        return self._dec.update(data)

def build_ws_header(host: str, port: int) -> bytes:
    key = base64.b64encode(secrets.token_bytes(16)).decode()
    return (f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: Upgrade\r\n"
            f"User-Agent: {WS_UA}\r\nUpgrade: websocket\r\n"
            f"Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {key}\r\n\r\n").encode()

def addr_bytes(host: str, port: int) -> bytes:
    try:
        ip = socket.inet_aton(host)
        return bytes([ADDR_V4]) + ip + struct.pack(">H", port)
    except OSError:
        pass
    hb = host.encode("idna") if any(ord(c) > 127 for c in host) else host.encode()
    return bytes([ADDR_DOM, len(hb)]) + hb + struct.pack(">H", port)

def connect_upstream(host: str, port: int, proto: str, password: str, target: bytes) -> socket.socket:
    """按节点 Protocol 建立上游并完成 SS 握手，返回已就绪的原始 socket。"""
    is_tls = proto.startswith("wss") or int(port) == 443
    raw = socket.create_connection((host, int(port)), timeout=15)
    if is_tls:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        raw = ctx.wrap_socket(raw, server_hostname=host)
    raw.sendall(build_ws_header(host, port))
    raw.settimeout(15)
    resp = b""
    while b"\r\n\r\n" not in resp:
        c = raw.recv(4096)
        if not c:
            raise ConnectionError("ws handshake EOF")
        resp += c
    line = resp.split(b"\r\n", 1)[0]
    if b" 101 " not in line:
        raise ConnectionError(f"ws refused: {line[:60]!r}")
    crypto = SSStream(password)
    first = struct.pack(">i", AFF) + target     # Aff(大端) + 目标地址
    raw.sendall(crypto.encrypt(first))
    raw.settimeout(None)
    return raw, crypto

# ---------- 本地服务 ----------
def handle_socks5(conn, first):
    conn.sendall(b"\x05\x00")                       # 无需认证
    req = conn.recv(300)
    if len(req) < 7 or req[0] != 5 or req[1] != 1:
        return None, None, None
    atyp = req[3]
    if atyp == ADDR_V4:   host, port, hb = socket.inet_ntoa(req[4:8]), struct.unpack(">H", req[8:10])[0], req[4:8]
    elif atyp == ADDR_DOM:
        n = req[4]; hb = req[5:5+n]
        host, port = hb.decode("idna"), struct.unpack(">H", req[5+n:7+n])[0]
    elif atyp == ADDR_V6: host, port, hb = socket.inet_ntop(socket.AF_INET6, req[4:20]), struct.unpack(">H", req[20:22])[0], req[4:20]
    else: return None, None, None
    return host, port, addr_bytes(host, port)

def handle_http(conn, first):
    buf = first
    while b"\r\n\r\n" not in buf and len(buf) < 8192:
        c = conn.recv(4096)
        if not c: return None, None, None, None
        buf += c
    m = re.match(rb"CONNECT\s+([A-Za-z0-9._\-]+):(\d+)\s+HTTP/\d", buf)
    if m:
        host, port = m.group(1).decode(), int(m.group(2))
        return host, port, addr_bytes(host, port), None
    # absolute-form HTTP proxy request (GET/POST http://host/path)
    m = re.match(rb"([A-Z]+)\s+https?://([^/:]+)(?::(\d+))?(/\S*)?\s+HTTP/\d", buf)
    if m:
        host = m.group(2).decode()
        port = int(m.group(3) or 80)
        # strip absolute URL → origin-form for upstream
        path = m.group(4) or b"/"
        lines = buf.split(b"\r\n")
        lines[0] = m.group(1) + b" " + path + b" HTTP/1.1"
        return host, port, addr_bytes(host, port), b"\r\n".join(lines)
    conn.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
    return None, None, None, None

def pump(src, dst, dec=None):
    try:
        while True:
            d = src.recv(65536)
            if not d: break
            dst.sendall(dec.update(d) if dec else d)
    except Exception: pass
    finally:
        try: dst.shutdown(socket.SHUT_WR)
        except Exception: pass

def is_bypass(host):
    h = host.lower()
    return any(h == d or h.endswith("." + d) for d in BYPASS_SET)

def handle_client(conn, _):
    try:
        conn.settimeout(60)
        first = conn.recv(256)
        if not first: return
        if first[0] == 5:
            host, port, target = handle_socks5(conn, first)
            rewrite = None
        else:
            host, port, target, rewrite = handle_http(conn, first)
        if not host:
            conn.close(); return

        if not IS_GLOBAL and is_bypass(host):
            try:
                remote = socket.create_connection((host, port), timeout=10)
                if first[0] == 5: conn.sendall(b"\x05\x00\x00\x01" + b"\x00"*6)
                else: conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                t1 = threading.Thread(target=pump, args=(conn, remote), daemon=True)
                t2 = threading.Thread(target=pump, args=(remote, conn), daemon=True)
                t1.start(); t2.start(); t1.join(); t2.join()
            except Exception: pass
            return

        node = SELECTED
        try:
            up, crypto = connect_upstream(node["Address"], node["Port"],
                                          node.get("Protocol", "ws"), node["Password"], target)
        except Exception:
            if first[0] == 5: conn.sendall(b"\x05\x01\x00\x01" + b"\x00"*6)
            else: conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            conn.close(); return

        if rewrite:
            up.sendall(crypto.encrypt(rewrite))   # plain HTTP request relayed once
        elif first[0] == 5: conn.sendall(b"\x05\x00\x00\x01" + b"\x00"*6)
        else: conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")

        t1 = threading.Thread(target=_local_to_remote, args=(conn, up, crypto), daemon=True)
        t2 = threading.Thread(target=_remote_to_local, args=(up, conn, crypto), daemon=True)
        t1.start(); t2.start(); t1.join(); t2.join()
    except Exception: pass
    finally:
        try: conn.close()
        except Exception: pass

def _local_to_remote(client, up, crypto):
    try:
        while True:
            d = client.recv(65536)
            if not d: break
            up.sendall(crypto.encrypt(d))
    except Exception: pass
    finally:
        try: up.shutdown(socket.SHUT_WR)
        except Exception: pass

def _remote_to_local(up, client, crypto):
    try:
        while True:
            d = up.recv(65536)
            if not d: break
            client.sendall(crypto.decrypt_feed(d))
    except Exception: pass
    finally:
        try: client.shutdown(socket.SHUT_WR)
        except Exception: pass

def port_conflict_pid(port):
    """返回已占用 port 的其他进程 PID，用于诊断多开抢端口。"""
    import subprocess
    try:
        out = subprocess.run(
            f'netstat -ano | findstr ":{port} " | findstr "LISTENING"',
            shell=True, capture_output=True, timeout=10
        ).stdout.decode("gbk", "replace")
    except Exception:
        return None
    for m in re.finditer(r"LISTENING\s+(\d+)", out):
        pid = int(m.group(1))
        if pid != os.getpid():
            return pid
    return None

def main():
    conflict = port_conflict_pid(LISTEN_PORT)
    if conflict is not None:
        print(f"[ant-py] 错误: 端口 {LISTEN_PORT} 已被 PID {conflict} 占用。")
        print(f"[ant-py] 可能是之前多开的 proxy.py / ant-py.exe / gui.py 残留。")
        print(f"[ant-py] 请先结束该进程: taskkill /F /PID {conflict}")
        sys.exit(1)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((BIND_ADDR, LISTEN_PORT))
    srv.listen(128)
    node = SELECTED
    print(f"[ant-py] 本地代理 {BIND_ADDR}:{LISTEN_PORT} (SOCKS5 + HTTP CONNECT)")
    print(f"[ant-py] 节点: {node['Description']} {node['Address']}:{node['Port']} {node['Protocol']} {node['Method']}")
    print(f"[ant-py] 分流: is_global={IS_GLOBAL}, 直连名单 {len(BYPASS_SET)} 条")
    while True:
        c, a = srv.accept()
        threading.Thread(target=handle_client, args=(c, a), daemon=True).start()

if __name__ == "__main__":
    main()
