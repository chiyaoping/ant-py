#!/usr/bin/env python3
"""Windows system proxy setter — mirrors SystemProxy.Wininet usage in the original app."""
import winreg, sys

KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
def _open(): return winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_ALL_ACCESS)

def enable(host="127.0.0.1", port=1080, bypass="<local>"):
    k = _open()
    winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1)
    winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, f"{host}:{port}")
    winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ, bypass)
    winreg.CloseKey(k); refresh()

def disable():
    k = _open()
    winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 0)
    winreg.CloseKey(k); refresh()

def status():
    k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY)
    try: en, _ = winreg.QueryValueEx(k, "ProxyEnable")
    except FileNotFoundError: en = 0
    try: sv, _ = winreg.QueryValueEx(k, "ProxyServer")
    except FileNotFoundError: sv = ""
    return bool(en), sv

def refresh():
    import ctypes
    INTERNET_OPTION_SETTINGS_CHANGED, INTERNET_OPTION_REFRESH = 39, 37
    wininet = ctypes.windll.wininet
    wininet.InternetSetOptionW(None, INTERNET_OPTION_SETTINGS_CHANGED, None, 0)
    wininet.InternetSetOptionW(None, INTERNET_OPTION_REFRESH, None, 0)

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "on":
        host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
        port = int(sys.argv[3]) if len(sys.argv) > 3 else 1080
        enable(host, port)
        print("system proxy enabled:", host, port)
    elif cmd == "off":
        disable(); print("system proxy disabled")
    else:
        print(status())
