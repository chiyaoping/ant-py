#!/usr/bin/env python3
"""验证 GUI 测速后节点树按延迟升序渲染。

必须用真正的 mainloop()：speed_all() 的工作线程通过 self.after(0,...) 回主线程，
用 update() 循环代替 mainloop 会导致 "main thread is not in main loop"。

用法: python test_speed_sort.py [组名]   # 默认只测第1组，避免 36 节点全测耗时
"""
import sys, time

sys.path.insert(0, ".")
import gui
from gui import App, ENGINE

GROUP = sys.argv[1] if len(sys.argv) > 1 else None
app = App()
app.withdraw()

if GROUP:
    ENGINE.groups = {GROUP: ENGINE.groups[GROUP]}

t0 = time.time()
app.speed_all()


def check():
    if getattr(app, "_speeding", False):
        if time.time() - t0 > 300:
            print("TIMEOUT")
            app.destroy()
            return
        app.after(300, check)
        return

    print("测速耗时 %.1fs, 结果数 %d" % (time.time() - t0, len(app._speed)))
    print("最快标签:", app.lbl_speed.cget("text"))
    print()

    total_good = 0
    for giid in app.tree.get_children():
        gname = app.tree.item(giid, "text")
        kids = app.tree.get_children(giid)
        vals = [app.tree.item(k, "values")[3] for k in kids]
        names = [app.tree.item(k, "text") for k in kids]
        print(gname)
        for v, n in zip(vals, names):
            print(f"    {v:<8} {n}")

        good = [float(v.split()[0]) for v in vals if v not in ("超时", "—")]
        tail = [i for i, v in enumerate(vals) if v in ("超时", "—")]
        assert good == sorted(good), ("未按延迟升序", gname, good)
        if tail:
            assert min(tail) >= len(good), ("超时/未测未排在末尾", gname, vals)
        total_good += len(good)

    assert total_good > 0, "没有拿到任何有效延迟，测试无效（不能空过）"
    print()
    print(f"OK: {total_good} 个有效节点组内升序，超时/未测在末尾")
    app.destroy()


app.after(300, check)
app.mainloop()
