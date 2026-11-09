# -*- coding: utf-8 -*-
"""换肤自检(ttkbootstrap 版)。

跑法(在项目根目录):
    python tools/theme_selftest.py

不读也不写真实配置: 配置读写函数在测试内被替换成内存桩, 模型目录指向空临时目录,
所以不会改到 config.json / models_config.json, 也不会拉起 llama-server。

ttk 时代换肤 = `style.theme_use(name)` 一句话, 控件自动跟随, 因此不再检查
"逐控件颜色对 token"那套注册表契约 —— 检查的东西换成:
  1. 30 套主题逐个切换: 不抛异常、ui_theme.CURRENT 跟上;
  2. 换肤是"零重建": 关键控件对象在切换前后是同一批(PyObject 不变);
  3. 状态天然保留: 选中模型 / 参数输入值 / 额外参数在切换后仍在;
  4. 自定义 style 在每次切换后被 on_theme_change 重建(font 不丢);
  5. 主题面板: 15 组 × 浅深 = 30 个预览块, 点选即切;
  6. 逐套切换耗时(写 logs/ui_perf.log, 调性能先看它)。
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import test_stubs            # noqa: E402
import main as app_main      # noqa: E402
import ui_theme as ut        # noqa: E402


def main() -> int:
    fails = []
    test_stubs.stub_config_io(block_dialogs=True)

    app = app_main.App()
    app.geometry("1200x760+20+10")
    for _ in range(8):
        app.update(); time.sleep(0.02)

    if ut.CURRENT != "bootstrap-dark":
        fails.append(f"起始主题应为 bootstrap-dark, 实际 {ut.CURRENT}")

    # ── 2. 零重建的关键证人: 记住几个控件对象, 换肤后必须还是它们 ──
    witnesses = [app.status_label, app.cmd_textbox, app.extra_entry,
                 app.params_panel, app.btn_update]

    # 给状态留点内容, 换肤后核对
    app.selected_model = "test-model.gguf"
    app.extra_entry.delete(0, "end")
    app.extra_entry.insert(0, "--verbose 1")
    probe_row = next(iter(app.params_panel.rows.values()))
    probe_key = probe_row.param.key
    probe_row.set_value("12345")

    # ── 1+6. 30 套主题逐个切换 ──
    times = []
    themes = ut.all_themes()
    for name in themes:
        t0 = time.perf_counter()
        got = ut.set_theme(name)
        app.update_idletasks(); app.update()
        dt = (time.perf_counter() - t0) * 1000
        times.append((name, dt))
        if got != name or ut.CURRENT != name:
            fails.append(f"切换到 {name} 后 CURRENT={ut.CURRENT}")
    times_sorted = sorted(t for _, t in times)
    slowest_name, slowest_ms = max(times, key=lambda kv: kv[1])
    print(f"30 套主题全部切换成功, 中位 {times_sorted[len(times_sorted)//2]:.1f}ms  "
          f"最慢 {slowest_name} {slowest_ms:.1f}ms")

    # ── 2. 控件对象没有重建 ──
    for i, w in enumerate(witnesses):
        try:
            alive = w.winfo_exists()
        except Exception:
            alive = False
        if not alive or w is not witnesses[i]:
            fails.append(f"证人控件 #{i} 在换肤后被重建/销毁了(ttk 时代不允许)")

    # ── 3. 状态保留 ──
    if app.selected_model != "test-model.gguf":
        fails.append(f"选中模型丢了: {app.selected_model}")
    if app.extra_entry.get() != "--verbose 1":
        fails.append(f"额外参数丢了: {app.extra_entry.get()!r}")
    if probe_row.get_value() != "12345":
        fails.append(f"参数值丢了({probe_key}): {probe_row.get_value()!r}")
    print("状态保留: 选中模型 / 参数值 / 额外参数 均在")

    # ── 4. 自定义 style 在切换后仍有效 ──
    if not app.style.configure(ut.S_STATUS):
        fails.append(f"自定义 style {ut.S_STATUS} 在换肤后丢失")
    else:
        font = app.style.lookup(ut.S_STATUS, "font")
        print(f"自定义 style: {ut.S_STATUS} font={font}")

    # ── 4b. 紧凑 padding: 30 次换肤之后仍然生效, 且控件真的矮了 ──
    def pads(v):
        """padding 归一化成 (左,上,右,下) —— ttk 保存的可能是 '8 1' / 5 / 元组"""
        if isinstance(v, str):
            v = v.replace(",", " ").split()
        elif isinstance(v, (int, float)):
            v = [v]
        try:
            nums = [int(float(x)) for x in (v or [])]
        except (TypeError, ValueError):
            return None
        if len(nums) == 1:
            nums *= 4
        elif len(nums) == 2:
            nums = [nums[0], nums[1], nums[0], nums[1]]
        return tuple(nums) if nums else None

    for style_name, expect in ut.COMPACT_PADDING.items():
        # 用 configure 读: lookup 只认元素自己声明的选项, 拿不到 padding
        got = pads((app.style.configure(style_name) or {}).get("padding"))
        if got != pads(expect):
            fails.append(f"紧凑 padding 未生效/被换肤还原: {style_name} "
                         f"{got} != {pads(expect)}")

    heights = {"额外参数框": app.extra_entry.winfo_reqheight()}
    if app.model_buttons:
        heights["模型按钮"] = next(iter(app.model_buttons.values())).winfo_reqheight()
    tall = [f"{k} {v}px" for k, v in heights.items() if v > 28]
    if tall:
        fails.append("控件仍然偏高(>28px): " + ", ".join(tall))
    print(f"紧凑 padding: {len(ut.COMPACT_PADDING)} 个基类样式保持, "
          f"实测高度 {heights}")

    # ── 5. 主题面板 ──
    app.on_open_theme_panel()
    for _ in range(6):
        app.update(); time.sleep(0.02)
    win = getattr(app, "_theme_dlg", None)
    if win is None or not win.winfo_exists():
        fails.append("主题面板没建起来")
    else:
        n_cells = len(getattr(app, "_theme_cells", []))
        if n_cells != len(themes):
            fails.append(f"预览块数量 {n_cells} != 主题数 {len(themes)}")
        # 点一个非当前主题的卡片 → 应切换并重绘
        target = next(n for n in themes if n != ut.CURRENT)
        app._pick_theme(target)
        app.update()
        if ut.CURRENT != target:
            fails.append(f"点选卡片后主题没变: {ut.CURRENT} != {target}")
        print(f"主题面板: {n_cells} 个预览块, 点选 {target} 生效")

    try:
        app.monitor.stop()
    except Exception:
        pass
    app.after(0, app.destroy)

    # 耗时日志
    try:
        (ROOT / "logs").mkdir(exist_ok=True)
        with open(ROOT / "logs" / "ui_perf.log", "a", encoding="utf-8") as fp:
            for name, dt in times:
                fp.write(f"selftest theme→{name}  {dt:.1f}ms\n")
    except Exception:
        pass

    print()
    if fails:
        print("FAIL")
        for f in fails:
            print("  - " + f)
        return 1
    print("PASS  换肤: 30 套全切换 / 零重建 / 状态保留 / 自定义 style / 主题面板 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
