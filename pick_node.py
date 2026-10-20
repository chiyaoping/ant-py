#!/usr/bin/env python3
"""Generates config.json from nodes.json — picks fastest node by TCP+TLS+ws handshake."""
import json, os, socket, ssl, time, base64, secrets, sys

import paths

HERE = os.path.dirname(os.path.abspath(__file__))
NODES = paths.resource("nodes.json")

data = json.load(open(NODES, encoding="utf-8"))
all_nodes = [n for g in data["groups"].values() for n in g]

def probe(node, timeout=6):
    host, port, proto = node["Address"], int(node["Port"]), (node.get("Protocol") or "ws").lower()
    t0 = time.time()
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        if proto.startswith("wss"):
            ctx = ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
            s = ctx.wrap_socket(s, server_hostname=host)
        t1 = time.time()
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        req = (f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: Upgrade\r\n"
               f"Upgrade: websocket\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {key}\r\n\r\n").encode()
        s.settimeout(timeout)
        s.sendall(req)
        resp = s.recv(256)
        if b" 101 " not in resp.split(b"\r\n",1)[0]: return None
        t2 = time.time()
        s.close()
        return (t1-t0, t2-t1)   # (tcp+tls, ws handshake)
    except Exception:
        return None

results = []
for n in all_nodes:
    r = probe(n)
    if r:
        results.append((r[0]+r[1], n))
        print(f"  OK  {r[0]*1000:6.0f}+{r[1]*1000:6.0f}ms  {n['Description']:24s} {n['Address']}:{n['Port']} {n['Protocol']}")
    else:
        print(f"  --   dead                {n['Description']:24s} {n['Address']}:{n['Port']} {n['Protocol']}")

if not results:
    print("no live node found"); sys.exit(1)

results.sort(key=lambda x: x[0])
best = results[0][1]
sel = {k: best[k] for k in ("Group","City","Address","Port","Password","Description","Plugin","Plugin_opts","IsLegacyUrl","Protocol","CountryCode","Method")}

CFGP = paths.data_file("config.json")
old = {}
if os.path.exists(CFGP):
    try:
        old = json.load(open(CFGP, encoding="utf-8"))
    except Exception:
        old = {}

cfg = {"listen_port": data.get("local_proxy_port", 1080), "bind_addr": "127.0.0.1",
       "is_global": data.get("is_global", True), "selected": sel}

# aff 必须保留：服务端对 aff <= 0 会静默挂起（不回错误也不关连接），
# 缺失时默认 0 会让代理完全无法上网。只有旧配置里也没有时才回落到节点数据。
aff = old.get("aff")
if aff is None:
    aff = data.get("aff")
if aff is None:
    print("警告: 未找到 aff，将写 0 —— 服务端会拒绝转发，需手动填入账号 Aff")
    aff = 0
cfg["aff"] = int(aff)

json.dump(cfg, open(CFGP, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"\nwrote config.json → selected: {best['Description']} ({best['Protocol']}), aff={cfg['aff']}")
