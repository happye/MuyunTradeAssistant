"""tests/data_sources 专属 pytest 适配（ISS-078，2026-09-03）

本目录 test_all_api.py 的连通性测试是双协议：`(ok, detail)` 元组供脚本模式
（`python test_all_api.py`）汇总判定；但 pytest 下裸元组返回会被记成
pass-with-warning（假阴性——外源挂了/配置坏了也显示绿，test_kimi 的 404
是被异常撞出来的，返回 False 的失败全部隐身）。

本 hook 只对 test_all_api 把元组翻译成 pytest 语义，脚本模式零影响：
- (True, detail)  → 通过（detail 打印到 stdout 留排查痕迹）
- (False, detail) 且含「未配/未登录/外源/间歇」→ pytest.skip（可见跳过，非假绿）
- 其余 (False, detail) → AssertionError（真失败，如模型 404、代理未生效）
- 异常自然上抛 = 失败（与原行为一致）
"""
import functools

import pytest

_SKIP_MARKS = ("未配", "未登录", "外源", "间歇")


def pytest_collection_modifyitems(session, config, items):
    for item in items:
        if not getattr(item.module, "__name__", "").endswith("test_all_api"):
            continue
        fn = item.obj
        if not callable(fn):
            continue

        @functools.wraps(fn)
        def _honest(*a, _fn=fn, **k):
            result = _fn(*a, **k)
            if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], bool):
                ok, detail = result
                if ok:
                    print(f"  [连通性 ok] {detail}")
                    return None
                if any(kw in str(detail) for kw in _SKIP_MARKS):
                    pytest.skip(str(detail))
                raise AssertionError(str(detail))
            return result

        item.obj = _honest
