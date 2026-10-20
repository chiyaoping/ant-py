#!/usr/bin/env python3
"""Aff 校验探测 —— 判断服务端是否真的按 Aff 查账号。

结论（2026-10-13 实测，节点 ants-台湾29 59.124.193.203:8080 ws）：
  Aff  1 / 2 / 100 / 12345 / 99999999 / 21347001(真实) / 2147483647  => 正常转发
  Aff  0 / -1 / -2 / -100 / -2147483648                              => 挂起 12s 无数据
  即：服务端只做 aff <= 0 的单调阈值检查，不查询账号是否存在。

判定分三层：
  L1 WS 101          WS 层不查 Aff（所有值都能拿到 101）
  L2 首包+请求发出     SS 解密成功（密码不对会直接被断）
  L3 收到响应字节      aff > 0 才转发；aff <= 0 静默挂起

用法：
  python aff_test4.py            # 用 config.json 里的真实 Aff 作对照，测边界值
  python aff_test4.py 123456     # 只测指定 Aff
"""
import socket, ssl, struct, json, os, time, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import proxy  # 复用已验证的 build_ws_header / SSStream / addr_bytes

CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
NODE = CFG["selected"]
HOST, PORT = NODE["Address"], int(NODE["Port"])
PROTO = (NODE.get("Protocol") or "ws").lower()
PASSWORD = NODE["Password"]
REAL_AFF = int(CFG.get("aff", 0))
TARGET_HOST = "www.gstatic.com"


def upstream(aff, timeout=12):
    """照抄 proxy.connect_upstream，Aff 参数化。"""
    is_tls = PROTO.startswith("wss") or int(PORT) == 443
    raw = socket.create_connection((HOST, int(PORT)), timeout=timeout)
    if is_tls:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        raw = ctx.wrap_socket(raw, server_hostname=HOST)
    raw.sendall(proxy.build_ws_header(HOST, PORT))
    raw.settimeout(timeout)
    resp = b""
    while b"\r\n\r\n" not in resp:
        c = raw.recv(4096)
        if not c:
            raise ConnectionError("ws handshake EOF")
        resp += c
    if b" 101 " not in resp.split(b"\r\n", 1)[0]:
        raise ConnectionError("ws refused")
    crypto = proxy.SSStream(PASSWORD)
    raw.sendall(crypto.encrypt(struct.pack(">i", aff)
                               + proxy.addr_bytes(TARGET_HOST, 80)))
    raw.settimeout(None)
    return raw, crypto


def probe(aff, timeout=12):
    """返回 (bytes, how, sec)；how ∈ ok/closed/timeout/<异常类名>"""
    t0 = time.time()
    raw = None
    try:
        raw, crypto = upstream(aff, timeout)
        raw.sendall(crypto.encrypt(
            f"GET /generate_204 HTTP/1.1\r\nHost: {TARGET_HOST}\r\n"
            f"Connection: close\r\n\r\n".encode()))
        raw.settimeout(timeout)
        buf = b""
        while b"\r\n\r\n" not in buf and len(buf) < 8192:
            d = raw.recv(4096)
            if not d:
                break
            buf += crypto.decrypt_feed(d)
        return len(buf), ("ok" if buf else "closed"), round(time.time() - t0, 2)
    except socket.timeout:
        return 0, "timeout", round(time.time() - t0, 2)
    except Exception as e:
        return 0, type(e).__name__, round(time.time() - t0, 2)
    finally:
        try: raw.close()
        except Exception: pass


CASES = [
    ("real",        REAL_AFF),
    ("0",           0),
    ("0",           0),
    ("0",           0),
    ("-1",          -1),
    ("-1",          -1),
    ("-1",          -1),
    ("-2",          -2),
    ("-100",        -100),
    ("-2147483648", -2147483648),
    ("real",        REAL_AFF),
    ("1",           1),
    ("2",           2),
    ("100",         100),
    ("12345",       12345),
    ("real",        REAL_AFF),
    ("real",        REAL_AFF),
]

if __name__ == "__main__":
    if len(sys.argv) > 1:
        aff = int(sys.argv[1])
        n, how, sec = probe(aff)
        print(f"aff={aff}: bytes={n} how={how} sec={sec} "
              f"=> {'RELAYED (aff>0 放行)' if n else 'BLOCKED/HANG'}")
        sys.exit(0)

    print(f"node: {NODE['Description']} {HOST}:{PORT} {PROTO}")
    print(f"target: {TARGET_HOST}:80  GET /generate_204")
    print(f"realAff = {REAL_AFF}\n")
    print(f"{'aff':<14}{'bytes':<8}{'how':<12}{'sec':<7}verdict")
    print("-" * 68)
    summary = {}
    for label, aff in CASES:
        n, how, sec = probe(aff)
        verdict = ("RELAYED" if n > 0
                   else "HANG" if how == "timeout"
                   else f"REJECT({how})")
        print(f"{label:<14}{str(n)+'B':<8}{how:<12}{sec:<7}{verdict}", flush=True)
        if label != "real":
            summary.setdefault(label, []).append(verdict)

    print("\n--- 汇总（非对照用例） ---")
    for k, v in summary.items():
        print(f"aff={k:<14} {v}")
