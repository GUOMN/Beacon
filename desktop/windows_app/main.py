from __future__ import annotations

import copy
import queue
import tkinter as tk
from datetime import datetime
from tkinter import colorchooser, ttk

from codex_status_core.models import DashboardSnapshot, StateStyle, TaskSlot, TaskState
from windows_app.ble_worker import BLEWorker

class WindowsDashboardApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Codex 六灯蓝牙桥接")
        self.root.geometry("1120x760")
        self.root.minsize(720, 580)

        self._events: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._snapshot = DashboardSnapshot()
        self._remaining = tk.IntVar(value=100)
        self._period_used = tk.IntVar(value=0)
        self._master_brightness = tk.IntVar(value=60)
        self._total_led_count = tk.IntVar(value=6)
        self._total_led_text = tk.StringVar(value="总灯数：6")
        self._status = tk.StringVar(value="蓝牙后台启动中")
        self._style_colors: dict[TaskState, tuple[int, int, int]] = {}
        self._style_effects: dict[TaskState, tk.StringVar] = {}
        self._style_frequencies: dict[TaskState, tk.IntVar] = {}
        self._style_duties: dict[TaskState, tk.IntVar] = {}
        self._style_buttons: dict[TaskState, ttk.Button] = {}
        self._style_previews: dict[TaskState, tk.Label] = {}

        self._build_ui()
        self._worker = BLEWorker(self._post_status)
        self._worker.start()
        self.root.after(100, self._drain_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        style = ttk.Style(self.root)
        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 11, "bold"), padding=(18, 10))

        menu_bar = tk.Menu(self.root)
        settings_menu = tk.Menu(menu_bar, tearoff=False)
        settings_menu.add_command(label="灯带设置…", command=self._open_led_settings)
        menu_bar.add_cascade(label="设置", menu=settings_menu)
        self.root.configure(menu=menu_bar)

        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill=tk.BOTH, expand=True)

        ttk.Label(outer, textvariable=self._status, font=("Microsoft YaHei UI", 12, "bold")).pack(
            anchor=tk.W, pady=(0, 12)
        )

        usage = ttk.LabelFrame(outer, text="第一颗灯 · 系统用量", padding=12)
        usage.pack(fill=tk.X)
        self._add_scale(usage, "整体亮度", self._master_brightness, 0)
        ttk.Label(
            usage,
            text="颜色由绿到红表示余量；闪烁越快表示五小时或当天用量越高",
        ).grid(row=1, column=0, columnspan=3, sticky=tk.W, pady=(8, 0))
        ttk.Button(
            usage,
            text="发送状态",
            command=self._send,
            style="Primary.TButton",
        ).grid(
            row=0, rowspan=2, column=3, sticky=tk.NSEW, padx=(16, 0)
        )
        usage.columnconfigure(1, weight=1)

        led_summary = ttk.LabelFrame(outer, text="灯带", padding=12)
        led_summary.pack(fill=tk.X, pady=(14, 0))
        ttk.Button(
            led_summary,
            textvariable=self._total_led_text,
            command=self._open_led_settings,
        ).pack(anchor=tk.W)

        themes = ttk.LabelFrame(outer, text="状态颜色与行为（对所有任务灯生效）", padding=12)
        themes.pack(fill=tk.X, pady=14)
        defaults = {
            TaskState.RUNNING: ((0, 90, 255), "呼吸", 50, 15),
            TaskState.WAITING: ((255, 150, 0), "常亮", 50, 15),
            TaskState.SUCCESS: ((0, 255, 60), "呼吸", 38, 15),
            TaskState.WARNING: ((255, 80, 0), "呼吸", 67, 15),
            TaskState.FAILURE: ((255, 0, 0), "常亮", 50, 15),
        }
        for row, (state, (color, effect_name, frequency, duty)) in enumerate(defaults.items()):
            self._style_colors[state] = color
            effect_var = tk.StringVar(value=effect_name)
            self._style_effects[state] = effect_var
            frequency_var = tk.IntVar(value=frequency)
            duty_var = tk.IntVar(value=duty)
            self._style_frequencies[state] = frequency_var
            self._style_duties[state] = duty_var
            ttk.Label(themes, text=state.chinese_name, width=10).grid(row=row, column=0, sticky=tk.W, pady=3)
            preview = tk.Label(
                themes, width=7, height=1, relief=tk.SOLID, borderwidth=1,
                background=self._hex_color(color), cursor="hand2",
            )
            preview.grid(row=row, column=1, padx=(8, 4), pady=3)
            preview.bind("<Button-1>", lambda _event, selected=state: self._choose_style_color(selected))
            self._style_previews[state] = preview
            button = ttk.Button(
                themes, text=self._hex_color(color), width=10,
                command=lambda selected=state: self._choose_style_color(selected),
            )
            button.grid(row=row, column=2, padx=(4, 8))
            self._style_buttons[state] = button
            effect_input = ttk.Combobox(
                themes, textvariable=effect_var, values=("常亮", "闪烁", "呼吸"),
                state="readonly", width=9,
            )
            effect_input.grid(row=row, column=3)
            frequency_input = ttk.Spinbox(
                themes, from_=6, to=300, textvariable=frequency_var, width=6,
            )
            frequency_input.grid(row=row, column=4, padx=(8, 2))
            ttk.Label(themes, text="次/分").grid(row=row, column=5, sticky=tk.W)
            duty_input = ttk.Spinbox(
                themes, from_=1, to=100, textvariable=duty_var, width=5,
            )
            duty_input.grid(row=row, column=6, padx=(8, 2))
            ttk.Label(themes, text="%亮").grid(row=row, column=7, sticky=tk.W)

            def update_timing_inputs(
                _event: object = None,
                selected_effect: tk.StringVar = effect_var,
                frequency_widget: ttk.Spinbox = frequency_input,
                duty_widget: ttk.Spinbox = duty_input,
            ) -> None:
                effect_name = selected_effect.get()
                frequency_widget.configure(state="normal" if effect_name != "常亮" else "disabled")
                duty_widget.configure(state="normal" if effect_name == "闪烁" else "disabled")

            effect_input.bind("<<ComboboxSelected>>", update_timing_inputs)
            update_timing_inputs()
        ttk.Button(
            themes,
            text="预览 / Debug",
            command=self._open_debug_preview,
        ).grid(row=0, column=8, padx=(24, 0), sticky=tk.N)

        log_header = ttk.Frame(outer)
        log_header.pack(fill=tk.X)
        ttk.Label(
            log_header,
            text="连接日志",
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(side=tk.LEFT)
        ttk.Button(log_header, text="一键清空", command=self._clear_log).pack(side=tk.RIGHT)
        self._log = tk.Text(outer, height=12, state=tk.DISABLED, font=("Consolas", 9))
        self._log.pack(fill=tk.BOTH, expand=True, pady=(6, 0))

    @staticmethod
    def _add_readonly_percent(parent: ttk.LabelFrame, label: str, variable: tk.IntVar, row: int) -> None:
        """展示由外部数据源更新的只读百分比，不在桌面端人工配置。"""
        ttk.Label(parent, text=label, width=9).grid(row=row, column=0, sticky=tk.W, pady=5)
        ttk.Label(
            parent,
            textvariable=variable,
            font=("Microsoft YaHei UI", 10, "bold"),
        ).grid(row=row, column=1, sticky=tk.W, padx=8)
        ttk.Label(parent, text="%", foreground="#666666").grid(row=row, column=2, sticky=tk.W)

    @staticmethod
    def _add_scale(parent: ttk.LabelFrame, label: str, variable: tk.IntVar, row: int) -> None:
        ttk.Label(parent, text=label, width=9).grid(row=row, column=0, sticky=tk.W, pady=5)
        ttk.Scale(parent, from_=0, to=100, variable=variable, orient=tk.HORIZONTAL).grid(
            row=row, column=1, columnspan=2, sticky=tk.EW, padx=8
        )
        ttk.Label(parent, textvariable=variable, width=4).grid(row=row, column=3)

    def _send(self) -> None:
        # 主界面不再人工编辑任务。发送时保留外部数据源已有状态，新增灯位默认空闲。
        task_count = max(1, min(63, self._total_led_count.get() - 1))
        tasks = []
        for index in range(task_count):
            if index < len(self._snapshot.tasks):
                tasks.append(copy.deepcopy(self._snapshot.tasks[index]))
            else:
                tasks.append(TaskSlot(title=f"任务 {index + 1}", state=TaskState.IDLE, progress=0))
        self._snapshot = DashboardSnapshot(
            remaining_percent=max(0, min(100, self._remaining.get())),
            period_used_percent=max(0, min(100, self._period_used.get())),
            master_brightness_percent=max(0, min(100, self._master_brightness.get())),
            tasks=tasks,
            state_styles={
                state: StateStyle(
                    color=color,
                    effect={"常亮": 1, "闪烁": 2, "呼吸": 3}[self._style_effects[state].get()],
                    period_ms=max(200, min(10000, round(60000 / max(6, min(300, self._style_frequencies[state].get()))))),
                    blink_duty_percent=max(1, min(100, self._style_duties[state].get())),
                )
                for state, color in self._style_colors.items()
            },
        )
        self._worker.submit(copy.deepcopy(self._snapshot))

    def _open_led_settings(self) -> None:
        """打开二级设置窗口；这里只配置物理灯带总灯数。"""
        dialog = tk.Toplevel(self.root)
        dialog.title("灯带设置")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        content = ttk.Frame(dialog, padding=18)
        content.pack(fill=tk.BOTH, expand=True)
        ttk.Label(content, text="总灯珠数（2~64）").grid(row=0, column=0, sticky=tk.W)
        value = tk.IntVar(value=self._total_led_count.get())
        ttk.Spinbox(content, from_=2, to=64, textvariable=value, width=8).grid(
            row=0, column=1, padx=(12, 0)
        )
        ttk.Label(content, text="第一颗固定为系统灯，其余灯位自动用于任务。", foreground="#666666").grid(
            row=1, column=0, columnspan=2, sticky=tk.W, pady=(10, 16)
        )

        def apply_and_close() -> None:
            try:
                led_count = value.get()
            except tk.TclError:
                led_count = self._total_led_count.get()
            led_count = max(2, min(64, led_count))
            self._total_led_count.set(led_count)
            self._total_led_text.set(f"总灯数：{led_count}")
            dialog.destroy()

        buttons = ttk.Frame(content)
        buttons.grid(row=2, column=0, columnspan=2, sticky=tk.E)
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(buttons, text="应用", command=apply_and_close).pack(side=tk.LEFT)
        dialog.bind("<Return>", lambda _event: apply_and_close())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())

    def _open_debug_preview(self) -> None:
        """在真实任务数据接入前，手动预览系统灯与各任务状态。"""
        dialog = tk.Toplevel(self.root)
        dialog.title("灯光状态预览 / Debug")
        dialog.geometry("520x620")
        dialog.minsize(460, 420)
        dialog.transient(self.root)

        outer = ttk.Frame(dialog, padding=16)
        outer.pack(fill=tk.BOTH, expand=True)

        system = ttk.LabelFrame(outer, text="系统灯预览", padding=10)
        system.pack(fill=tk.X)
        remaining = tk.IntVar(value=self._remaining.get())
        period_used = tk.IntVar(value=self._period_used.get())
        self._add_scale(system, "剩余额度", remaining, 0)
        self._add_scale(system, "周期已用", period_used, 1)
        system.columnconfigure(1, weight=1)

        task_frame = ttk.LabelFrame(outer, text="任务灯状态预览", padding=10)
        task_frame.pack(fill=tk.BOTH, expand=True, pady=12)
        canvas = tk.Canvas(task_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(task_frame, orient=tk.VERTICAL, command=canvas.yview)
        rows = ttk.Frame(canvas)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.create_window((0, 0), window=rows, anchor=tk.NW)
        rows.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        state_names = [state.chinese_name for state in TaskState]
        task_count = max(1, min(63, self._total_led_count.get() - 1))
        state_vars: list[tk.StringVar] = []
        for index in range(task_count):
            current = (
                self._snapshot.tasks[index].state.chinese_name
                if index < len(self._snapshot.tasks)
                else TaskState.IDLE.chinese_name
            )
            state_var = tk.StringVar(value=current)
            state_vars.append(state_var)
            ttk.Label(rows, text=f"任务灯 {index + 1}", width=12).grid(
                row=index, column=0, sticky=tk.W, pady=4
            )
            ttk.Combobox(
                rows,
                textvariable=state_var,
                values=state_names,
                state="readonly",
                width=14,
            ).grid(row=index, column=1, sticky=tk.W, padx=8, pady=4)

        def send_preview() -> None:
            name_to_state = {state.chinese_name: state for state in TaskState}
            self._remaining.set(max(0, min(100, remaining.get())))
            self._period_used.set(max(0, min(100, period_used.get())))
            self._snapshot.tasks = [
                TaskSlot(
                    title=f"任务 {index + 1}",
                    state=name_to_state[state_var.get()],
                    progress=0,
                )
                for index, state_var in enumerate(state_vars)
            ]
            self._send()

        actions = ttk.Frame(outer)
        actions.pack(fill=tk.X)
        ttk.Button(actions, text="关闭", command=dialog.destroy).pack(side=tk.RIGHT)
        ttk.Button(actions, text="发送预览", command=send_preview).pack(side=tk.RIGHT, padx=(0, 8))

    @staticmethod
    def _hex_color(color: tuple[int, int, int]) -> str:
        return f"#{color[0]:02X}{color[1]:02X}{color[2]:02X}"

    def _choose_style_color(self, state: TaskState) -> None:
        selected = colorchooser.askcolor(color=self._hex_color(self._style_colors[state]), parent=self.root)
        if selected[0] is None:
            return
        self._style_colors[state] = tuple(int(channel) for channel in selected[0])
        self._style_previews[state].configure(background=self._hex_color(self._style_colors[state]))
        self._style_buttons[state].configure(text=self._hex_color(self._style_colors[state]))

    def _post_status(self, message: str) -> None:
        self._events.put(message)

    def _clear_log(self) -> None:
        """只清空界面日志，不改变蓝牙连接和灯板配置。"""
        self._log.configure(state=tk.NORMAL)
        self._log.delete("1.0", tk.END)
        self._log.configure(state=tk.DISABLED)

    def _drain_events(self) -> None:
        while not self._events.empty():
            message = self._events.get()
            self._status.set(message)
            timestamp = datetime.now().strftime("%H:%M:%S")
            self._log.configure(state=tk.NORMAL)
            self._log.insert(tk.END, f"{timestamp}  {message}\n")
            self._log.see(tk.END)
            self._log.configure(state=tk.DISABLED)
        self.root.after(100, self._drain_events)

    def _on_close(self) -> None:
        self._worker.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    WindowsDashboardApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
