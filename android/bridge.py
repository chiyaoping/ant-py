"""bridge — pyjnius 胶水：VpnService 权限流、建立 TUN、通知、UID 查询。

仅 Android 可用（pyjnius）；Windows 上导入本模块会抛 ImportError，调用方需自行容错。
"""
import os

from jnius import autoclass, PythonJavaClass, java_method
from threading import Event

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
Handler = autoclass("android.os.Handler")
Looper = autoclass("android.os.Looper")

CHANNEL_ID = "ant_py_vpn"
NOTIFY_ID = 1
SERVICE_INTENT_ACTION = "org.antpy.VPN_SERVICE"

_activity = None


def get_activity():
    """尝试拿当前 Activity(可能为 None —— flet 的 FlutterActivity 不在 pyjnius 线程可见范围)。"""
    global _activity
    if _activity is None:
        try:
            _activity = ActivityThread.currentActivity()
        except Exception:
            _activity = None
    return _activity


def ctx():
    """当前应用 Context。可能返回 None（currentApplication 在线程早期为 null）。"""
    act = get_activity()
    if act is not None:
        return act
    try:
        return ActivityThread.currentApplication()
    except Exception:
        return None


def app_uid() -> int:
    c = ctx()
    if c is None:
        raise RuntimeError("拿不到 Context，无法查询 UID")
    return c.getApplicationInfo().uid


def vpn_prepare() -> bool:
    """检查/请求 VPN 权限。返回 True 表示已授权；False 表示已弹出系统授权对话框。

    在后台线程（flet on_click）直接调用 ActivityThread.currentActivity() 返回 None，
    退化为 Application 上下文后 startActivity 会被系统静默拦截 —— 必须把对话框
    的启动 post 回主线程（Looper.mainLooper 的 Handler），在主线程拿 Activity 再启动。
    用户授权后需再点一次「连接」(简化实现)。
    """
    result = {"granted": True}

    def _run_on_main():
        try:
            c = ctx()
            if c is None:
                proxy_log("vpn_prepare: 拿不到 Context（Activity/Application 均为 null）")
                result["granted"] = False
                return
            intent = VpnService.prepare(c)
            if intent is not None:
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                c.startActivity(intent)
                result["granted"] = False
        except Exception as ex:
            proxy_log(f"vpn_prepare 异常: {ex}")
            result["granted"] = False
        finally:
            done.set()

    done = Event()

    def proxy_log(msg):
        try:
            import proxycore
            proxycore.log(msg)
        except Exception:
            pass

    if Looper.myLooper() == Looper.getMainLooper():
        _run_on_main()
    else:
        Handler(Looper.getMainLooper()).post(_run_on_main)
        if not done.wait(10):
            proxy_log("vpn_prepare 主线程超时")
            return False
    return result["granted"]


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
    c = ctx()
    if c is None:
        raise RuntimeError("拿不到 Context，无法定位 tun2socks")
    return os.path.join(c.getApplicationInfo().nativeLibraryDir, "libtun2socks.so")


def notify(title: str, text: str):
    """更新前台服务通知。"""
    c = ctx()
    if c is None:
        return
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
