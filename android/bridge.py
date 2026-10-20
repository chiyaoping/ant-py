"""bridge — pyjnius 胶水：VpnService 权限流、建立 TUN、通知、UID 查询。

仅 Android 可用（pyjnius）；Windows 上导入本模块会抛 ImportError，调用方需自行容错。
"""
import os

from jnius import autoclass, PythonJavaClass, java_method

Context = autoclass("android.content.Context")
Intent = autoclass("android.content.Intent")
VpnService = autoclass("android.net.VpnService")
ParcelFileDescriptor = autoclass("android.os.ParcelFileDescriptor")
BuildVersion = autoclass("android.os.Build$VERSION")
NotificationChannel = autoclass("android.app.NotificationChannel")
NotificationManager = autoclass("android.app.NotificationManager")
Notification = autoclass("android.app.Notification")
# flet build 的 APK 没有 Kivy 的 PythonActivity;通过当前运行的 activity 拿 Context
ActivityThread = autoclass("android.app.ActivityThread")

CHANNEL_ID = "ant_py_vpn"
NOTIFY_ID = 1
SERVICE_INTENT_ACTION = "org.antpy.VPN_SERVICE"

_activity = None


def ctx():
    """当前应用 Context。优先用已缓存的 activity;否则用 system context（足以发起 VPN 授权对话框）。"""
    global _activity
    if _activity is None:
        try:
            from jnius import autoclass as _ac
            act_cls = _ac("android.app.Activity")
            _activity = ActivityThread.currentActivity()
        except Exception:
            _activity = None
    if _activity is not None:
        return _activity
    return ActivityThread.currentApplication()


def app_uid() -> int:
    return ctx().getApplicationInfo().uid


def vpn_prepare() -> bool:
    """检查/请求 VPN 权限。返回 True 表示已授权；False 表示已弹出系统授权对话框。"""
    act = ctx()
    intent = VpnService.prepare(act)
    if intent is None:
        return True
    act.startActivityForResult(intent, 7001)
    return False


def build_vpn_fd(socks_addr: str, socks_port: int, dns: str):
    """建立 VpnService TUN，返回 (fd:int, ParcelFileDescriptor)。

    - addDisallowedApplication(自己) 是防回环关键：引擎出站的 WS 连接
      走物理网络，不会被 TUN 重新捞回。
    """
    b = VpnService.Builder()
    b.setMtu(1500)
    b.addAddress("10.66.0.2", 32)
    b.addRoute("0.0.0.0", 0)
    b.addDnsServer(dns)
    b.addDisallowedApplication(app_uid())
    b.setSession("ant-py")
    fd_obj = b.establish()
    if fd_obj is None:
        raise RuntimeError("VpnService.establish() 失败（权限被拒绝或已撤销）")
    return fd_obj.getFd(), fd_obj


def tun2socks_cmd() -> str:
    """jniLibs 里的 tun2socks 可执行路径（打包为 libtun2socks.so）。"""
    return os.path.join(ctx().getApplicationInfo().nativeLibraryDir, "libtun2socks.so")


def notify(title: str, text: str):
    """更新前台服务通知。"""
    c = ctx()
    nm = c.getSystemService(Context.NOTIFICATION_SERVICE)
    if BuildVersion.SDK_INT >= 26:
        ch = NotificationChannel(CHANNEL_ID, "ant-py VPN", NotificationManager.IMPORTANCE_LOW)
        nm.createNotificationChannel(ch)
    builder_cls = autoclass("android.app.Notification$Builder")
    b = builder_cls(c, CHANNEL_ID)
    b.setContentTitle(title)
    b.setContentText(text)
    b.setSmallIcon(c.getApplicationInfo().icon)
    b.setOngoing(True)
    nm.notify(NOTIFY_ID, b.build())
