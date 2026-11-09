# dashboard_apptest.py
"""
面板逐页渲染验收（Streamlit AppTest）。

用法：
    & "E:\\agent_project\\.venv\\Scripts\\python.exe" dashboard_apptest.py

验证内容：
- dashboard.py 能加载，主页无异常
- 侧边栏页数 = 内置 8 页 + 插件声明页
- 每一页都能渲染，且不抛异常（exception 为空）

发布门槛「AppTest 逐页渲染无异常」靠这个脚本复现（2026-11-09 补）。
"""
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from streamlit.testing.v1 import AppTest   # noqa: E402

APP = os.path.join(ROOT, "dashboard.py")
TIMEOUT = 90


def _page_labels():
    """从 dashboard.py 里读路由表 + 插件页，避免两处维护"""
    from core.plugin import registry
    from core.plugin.loader import DEFAULT_ROOTS
    try:
        registry.discover(DEFAULT_ROOTS)
    except Exception:
        pass
    labels = ["🧭 Agent调度", "🪟 视觉工作台",
              "🔐 视觉ACL", "🌐 地址管理器", "🧩 插件管理",
              "📡 监控会话", "❓ 帮助文档"]
    labels += [p["label"] for p in registry.list_panels()]
    return labels


def main():
    labels = _page_labels()
    print("目标页数：%d" % len(labels))
    for x in labels:
        print("  -", x)

    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    if at.exception:
        print("\n[主页] 异常：")
        for e in at.exception:
            print(e.message)
        return 1

    radios = at.sidebar.radio
    if not radios:
        print("\n[失败] 侧边栏没有页面选择器")
        return 1
    actual = list(radios[0].options)
    print("\n侧边栏实际页数：%d" % len(actual))
    for x in actual:
        print("  -", x)
    if actual != labels:
        print("\n[失败] 侧边栏页与预期不一致")
        print("  预期：", labels)
        print("  实际：", actual)
        return 1

    failed = []
    for x in actual:
        at.sidebar.radio[0].set_value(x).run()
        if at.exception:
            msgs = [e.message for e in at.exception]
            print("\n[异常] %s -> %s" % (x, msgs))
            failed.append((x, msgs))
        else:
            print("[ok] %s" % x)

    print("\n结果：%d/%d 页通过" % (len(actual) - len(failed), len(actual)))
    if failed:
        print("\n失败页：")
        for x, msgs in failed:
            print(" -", x)
            for m in msgs:
                print("   ", m)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
