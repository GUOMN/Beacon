from __future__ import annotations

import copy
import colorsys
import json
import math
import os
import queue
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk

from codex_status_core.models import DashboardSnapshot, StateStyle, TaskSlot, TaskState
from windows_app.ble_worker import BLEWorker, identify_status_device, scan_status_devices

class WindowsDashboardApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Codex 状态灯")
        self.root.geometry("1120x760")
        self.root.minsize(860, 640)

        self._events: queue.SimpleQueue[object] = queue.SimpleQueue()
        self._snapshot = DashboardSnapshot()
        self._remaining = tk.IntVar(value=100)
        self._period_used = tk.IntVar(value=0)
        self._master_brightness = tk.IntVar(value=60)
        self._total_led_count = tk.IntVar(value=6)
        self._total_led_text = tk.StringVar(value="总灯数：6")
        self._status = tk.StringVar(value="连接中")
        self._style_colors: dict[TaskState, tuple[int, int, int]] = {}
        self._style_effects: dict[TaskState, tk.StringVar] = {}
        self._style_frequencies: dict[TaskState, tk.IntVar] = {}
        self._style_duties: dict[TaskState, tk.IntVar] = {}
        self._style_buttons: dict[TaskState, ttk.Button] = {}
        self._style_previews: dict[TaskState, tk.Label] = {}
        self._device_tree: ttk.Treeview | None = None
        self._scanned_devices: dict[str, dict[str, object]] = {}
        self._settings_path = Path(os.getenv("APPDATA", str(Path.home()))) / \
            "CodexStatusBridge" / "settings.json"
        self._bound_device_id = self._load_bound_device_id()
        self._calibration = self._load_device_calibration(self._bound_device_id)

        self._build_ui()
        self._worker: BLEWorker | None = None
        if self._bound_device_id:
            self._start_bound_worker(self._bound_device_id)
        else:
            self._status.set("未连接")
        self.root.after(100, self._drain_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        self.root.configure(background="#F4F7FB")
        self.root.option_add("*Font", ("Microsoft YaHei UI", 10))
        style.configure(".", font=("Microsoft YaHei UI", 10))
        style.configure("TFrame", background="#F4F7FB")
        style.configure("TLabel", background="#F4F7FB", foreground="#172033")
        style.configure(
            "TLabelframe",
            background="#F4F7FB",
            bordercolor="#D9E2EF",
            borderwidth=1,
            relief="solid",
        )
        style.configure(
            "TLabelframe.Label",
            background="#F4F7FB",
            foreground="#172033",
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.configure(
            "TButton",
            background="#EEF3FA",
            foreground="#24324A",
            borderwidth=0,
            padding=(14, 9),
            focusthickness=0,
        )
        style.map("TButton", background=[("active", "#D9E5F3"), ("pressed", "#CAD9EC")])
        style.configure(
            "Preview.TButton",
            background="#E8F0FF",
            foreground="#1D4ED8",
            font=("Microsoft YaHei UI", 11, "bold"),
            padding=(22, 14),
        )
        style.map("Preview.TButton", background=[("active", "#DCE8FF"), ("pressed", "#CEDDFF")])
        style.configure(
            "Primary.TButton",
            background="#2563EB",
            foreground="#FFFFFF",
            font=("Microsoft YaHei UI", 11, "bold"),
            padding=(20, 11),
        )
        style.map(
            "Primary.TButton",
            background=[("active", "#1D4ED8"), ("pressed", "#1E40AF")],
            foreground=[("disabled", "#DCE7FF")],
        )
        style.configure(
            "Danger.TButton",
            background="#FFE5E8",
            foreground="#B42335",
            borderwidth=0,
            padding=(12, 7),
        )
        style.map("Danger.TButton", background=[("active", "#FFD2D8"), ("pressed", "#FFC2CA")])
        style.configure(
            "TCombobox",
            padding=6,
            fieldbackground="#FFFFFF",
            background="#EAF2FF",
            foreground="#26364D",
            arrowcolor="#5276A7",
            bordercolor="#C9DAF2",
            lightcolor="#C9DAF2",
            darkcolor="#C9DAF2",
            borderwidth=1,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", "#FFFFFF")],
            background=[("readonly", "#EAF2FF"), ("active", "#DCEAFF")],
            bordercolor=[("focus", "#77A7F8")],
        )
        style.configure("TSpinbox", padding=5, fieldbackground="#FFFFFF")
        style.configure(
            "Horizontal.TScale",
            background="#F4F7FB",
            troughcolor="#DDEAFF",
            bordercolor="#DDEAFF",
            lightcolor="#4F86F7",
            darkcolor="#4F86F7",
            sliderthickness=16,
            borderwidth=0,
        )
        style.configure(
            "Treeview",
            background="#FFFFFF",
            fieldbackground="#FFFFFF",
            foreground="#172033",
            rowheight=30,
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            background="#E8EEF7",
            foreground="#26364D",
            font=("Microsoft YaHei UI", 10, "bold"),
            padding=7,
        )
        style.map("Treeview", background=[("selected", "#DCE8FF")], foreground=[("selected", "#163A75")])

        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill=tk.BOTH, expand=True)

        top_bar = ttk.Frame(outer)
        top_bar.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(
            top_bar,
            textvariable=self._status,
            font=("Microsoft YaHei UI", 15, "bold"),
            foreground="#163A75",
        ).pack(side=tk.LEFT, anchor=tk.W)
        ttk.Button(top_bar, text="设备管理", command=self._open_device_manager).pack(side=tk.RIGHT)
        ttk.Button(top_bar, text="灯带校准", command=self._open_calibration).pack(
            side=tk.RIGHT, padx=(0, 8)
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
        preview_area = ttk.Frame(themes)
        preview_area.grid(row=0, column=8, rowspan=5, padx=(28, 4), sticky=tk.NSEW)
        ttk.Button(
            preview_area,
            text="设置状态并预览",
            command=self._open_debug_preview,
            style="Preview.TButton",
        ).pack(fill=tk.X)
        ttk.Label(
            preview_area,
            text="设置一组 Mock 数据，\n即时查看灯带效果",
            foreground="#718096",
            justify=tk.CENTER,
        ).pack(pady=(10, 0))

        log_header = ttk.Frame(outer)
        log_header.pack(fill=tk.X)
        ttk.Label(
            log_header,
            text="连接日志",
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(side=tk.LEFT)
        ttk.Button(log_header, text="一键清空", command=self._clear_log).pack(side=tk.RIGHT)
        self._log = tk.Text(
            outer,
            height=12,
            state=tk.DISABLED,
            font=("Cascadia Mono", 9),
            background="#111827",
            foreground="#D1E0F5",
            insertbackground="#FFFFFF",
            selectbackground="#31598F",
            relief=tk.FLAT,
            borderwidth=0,
            padx=12,
            pady=10,
        )
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

    def _fit_dialog(
        self,
        dialog: tk.Toplevel,
        preferred_width: int,
        preferred_height: int,
        minimum_width: int,
        minimum_height: int,
    ) -> None:
        """让弹窗适配当前屏幕并居中，避免低分辨率下底部操作按钮被裁切。"""
        dialog.update_idletasks()
        screen_width = dialog.winfo_screenwidth()
        screen_height = dialog.winfo_screenheight()
        width = min(preferred_width, max(minimum_width, screen_width - 80))
        height = min(preferred_height, max(minimum_height, screen_height - 120))
        x = max(20, (screen_width - width) // 2)
        y = max(20, (screen_height - height) // 2)
        dialog.minsize(min(minimum_width, width), min(minimum_height, height))
        dialog.geometry(f"{width}x{height}+{x}+{y}")

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
                    color=self._calibrate_color(color),
                    effect={"常亮": 1, "闪烁": 2, "呼吸": 3}[self._style_effects[state].get()],
                    period_ms=max(200, min(10000, round(60000 / max(6, min(300, self._style_frequencies[state].get()))))),
                    blink_duty_percent=max(1, min(100, self._style_duties[state].get())),
                )
                for state, color in self._style_colors.items()
            },
        )
        if self._worker is None:
            self._post_status("尚未绑定灯板，请先在设备管理中选择并绑定")
            return
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
        self._fit_dialog(dialog, 480, 240, 420, 220)

    def _open_device_manager(self) -> None:
        """扫描、识别并绑定唯一灯板，避免同名设备之间串扰。"""
        dialog = tk.Toplevel(self.root)
        dialog.title("设备管理")
        dialog.resizable(True, True)
        dialog.transient(self.root)

        outer = ttk.Frame(dialog, padding=16)
        outer.pack(fill=tk.BOTH, expand=True)
        bound_text = self._bound_device_id or "未绑定"
        ttk.Label(
            outer,
            text=f"当前绑定：{bound_text}",
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(anchor=tk.W, pady=(0, 12))

        tree = ttk.Treeview(
            outer,
            columns=("name", "device_id", "rssi", "binding"),
            show="headings",
            selectmode="browse",
            height=8,
        )
        tree.heading("name", text="设备名称")
        tree.heading("device_id", text="唯一 ID")
        tree.heading("rssi", text="信号强度")
        tree.heading("binding", text="绑定状态")
        tree.column("name", width=260)
        tree.column("device_id", width=130, anchor=tk.CENTER)
        tree.column("rssi", width=90, anchor=tk.CENTER)
        tree.column("binding", width=90, anchor=tk.CENTER)
        tree.pack(fill=tk.BOTH, expand=True)
        self._device_tree = tree
        self._scanned_devices.clear()

        # 即使已绑定灯板暂时不在广播，也始终在列表中保留一行，方便取消绑定。
        if self._bound_device_id:
            connected = bool(self._worker and self._worker.is_connected)
            display_name = "已绑定灯板（已连接）" if connected else "已绑定灯板（正在连接）"
            self._scanned_devices["bound_device"] = {
                "name": display_name,
                "device_id": self._bound_device_id,
                "address": "",
                "rssi": None,
            }
            tree.insert(
                "", tk.END, iid="bound_device",
                values=(display_name, self._bound_device_id, "--", "已连接" if connected else "连接中"),
            )

        def selected_device() -> dict[str, object] | None:
            selected = tree.selection()
            return self._scanned_devices.get(selected[0]) if selected else None

        def identify_selected() -> None:
            device = selected_device()
            if device is None:
                self._post_status("请先在列表中选择一块灯板")
                return
            address = str(device.get("address", ""))
            if not address:
                self._post_status("已绑定灯板当前未被扫描到，暂时不能识别")
                return
            identify_status_device(address, self._post_status)

        def bind_selected() -> None:
            device = selected_device()
            if device is None:
                self._post_status("请先在列表中选择一块灯板")
                return
            device_id = str(device["device_id"])
            self._save_bound_device_id(device_id)
            self._start_bound_worker(device_id)
            dialog.destroy()

        def unbind() -> None:
            if self._worker is not None:
                self._worker.stop()
                self._worker = None
            self._bound_device_id = None
            self._save_bound_device_id(None)
            self._status.set("未连接")
            dialog.destroy()

        def bind_or_unbind_selected() -> None:
            device = selected_device()
            if device is None:
                self._post_status("请先在列表中选择一块灯板")
                return
            if str(device["device_id"]).upper() == (self._bound_device_id or "").upper():
                unbind()
            else:
                bind_selected()

        def update_actions(_event: object = None) -> None:
            device = selected_device()
            is_bound = bool(
                device
                and str(device["device_id"]).upper() == (self._bound_device_id or "").upper()
            )
            identify_button.configure(state=tk.DISABLED if is_bound else tk.NORMAL)
            bind_button.configure(
                text="取消绑定" if is_bound else "绑定选中设备",
                style="Danger.TButton" if is_bound else "Primary.TButton",
            )

        actions = ttk.Frame(outer)
        actions.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(
            actions,
            text="重新扫描",
            command=lambda: scan_status_devices(self._post_scan_results, self._post_status),
        ).pack(side=tk.LEFT)
        identify_button = ttk.Button(actions, text="识别（白色流水）", command=identify_selected)
        identify_button.pack(
            side=tk.LEFT, padx=8
        )
        bind_button = ttk.Button(actions, text="绑定选中设备", command=bind_or_unbind_selected)
        bind_button.pack(
            side=tk.RIGHT, padx=8
        )
        tree.bind("<<TreeviewSelect>>", update_actions)
        scan_status_devices(self._post_scan_results, self._post_status)
        self._fit_dialog(dialog, 760, 520, 680, 460)

    def _load_bound_device_id(self) -> str | None:
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
            value = str(data.get("bound_device_id", "")).upper()
            return value if len(value) == 6 else None
        except (OSError, ValueError, TypeError):
            return None

    def _save_bound_device_id(self, device_id: str | None) -> None:
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            data = {}
        data["bound_device_id"] = device_id
        self._settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self._bound_device_id = device_id
        self._calibration = self._load_device_calibration(device_id)

    def _load_device_calibration(self, device_id: str | None) -> dict[str, float]:
        defaults = {"red": 1.0, "green": 1.0, "blue": 1.0, "gamma": 2.2}
        if not device_id:
            return defaults
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
            stored = data.get("device_calibrations", {}).get(device_id, {})
            return {
                "red": float(stored.get("red", 1.0)),
                "green": float(stored.get("green", 1.0)),
                "blue": float(stored.get("blue", 1.0)),
                "gamma": float(stored.get("gamma", 2.2)),
            }
        except (OSError, ValueError, TypeError, AttributeError):
            return defaults

    def _save_device_calibration(self, calibration: dict[str, float]) -> None:
        if not self._bound_device_id:
            return
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            data = {}
        calibrations = data.setdefault("device_calibrations", {})
        calibrations[self._bound_device_id] = calibration
        self._settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self._calibration = calibration.copy()

    def _calibrate_color(
        self,
        color: tuple[int, int, int],
        calibration: dict[str, float] | None = None,
    ) -> tuple[int, int, int]:
        """将屏幕 sRGB 颜色转换为灯带 PWM，并应用当前灯板的通道校准。"""
        values = calibration or self._calibration
        gamma = max(0.8, min(3.5, values["gamma"]))
        gains = (values["red"], values["green"], values["blue"])
        return tuple(
            max(0, min(255, round(((channel / 255) ** gamma) * 255 * gain)))
            for channel, gain in zip(color, gains)
        )

    def _start_bound_worker(self, device_id: str) -> None:
        if self._worker is not None:
            self._worker.stop()
        self._worker = BLEWorker(self._post_status, device_id)
        self._worker.start()
        self._bound_device_id = device_id
        self._status.set("连接中")

    def _post_scan_results(self, devices: list[dict[str, object]]) -> None:
        self._events.put(("scan_results", devices))

    def _open_calibration(self) -> None:
        """为当前绑定灯板调整通道增益和伽马，并按唯一 ID 保存。"""
        if not self._bound_device_id or self._worker is None:
            self._post_status("请先在设备管理中绑定一块灯板")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("灯带色彩校准")
        dialog.transient(self.root)
        dialog.grab_set()

        outer = ttk.Frame(dialog, padding=22)
        outer.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            outer, text="校准当前绑定灯板", font=("Microsoft YaHei UI", 15, "bold")
        ).pack(anchor=tk.W)
        ttk.Label(
            outer,
            text="先发送测试色，再对照实际灯光调整。参数只保存到当前设备。",
            foreground="#718096",
        ).pack(anchor=tk.W, pady=(4, 18))

        red = tk.IntVar(value=round(self._calibration["red"] * 100))
        green = tk.IntVar(value=round(self._calibration["green"] * 100))
        blue = tk.IntVar(value=round(self._calibration["blue"] * 100))
        gamma = tk.DoubleVar(value=self._calibration["gamma"])

        controls = ttk.LabelFrame(outer, text="色彩参数", padding=14)
        controls.pack(fill=tk.X)
        for row, (label, variable) in enumerate(
            (("红色增益", red), ("绿色增益", green), ("蓝色增益", blue))
        ):
            ttk.Label(controls, text=label, width=10).grid(row=row, column=0, sticky=tk.W, pady=7)
            ttk.Scale(controls, from_=50, to=150, variable=variable).grid(
                row=row, column=1, sticky=tk.EW, padx=10
            )
            ttk.Label(controls, textvariable=variable, width=5).grid(row=row, column=2)
        ttk.Label(controls, text="伽马", width=10).grid(row=3, column=0, sticky=tk.W, pady=7)
        ttk.Scale(controls, from_=0.8, to=3.5, variable=gamma).grid(
            row=3, column=1, sticky=tk.EW, padx=10
        )
        gamma_text = ttk.Label(controls, width=5)
        gamma_text.grid(row=3, column=2)
        controls.columnconfigure(1, weight=1)

        def refresh_gamma(_value: object = None) -> None:
            gamma_text.configure(text=f"{gamma.get():.2f}")

        refresh_gamma()
        gamma.trace_add("write", lambda *_args: refresh_gamma())

        test_colors = {
            "中性白": (180, 180, 180),
            "红色": (255, 0, 0),
            "绿色": (0, 255, 0),
            "蓝色": (0, 0, 255),
            "黄色": (255, 180, 0),
            "青色": (0, 220, 220),
            "紫色": (180, 0, 255),
            "中灰": (128, 128, 128),
        }
        test_name = tk.StringVar(value="中性白")
        test_row = ttk.Frame(outer)
        test_row.pack(fill=tk.X, pady=18)
        ttk.Label(test_row, text="测试颜色").pack(side=tk.LEFT)
        ttk.Combobox(
            test_row, textvariable=test_name, values=tuple(test_colors), state="readonly", width=12
        ).pack(side=tk.LEFT, padx=10)

        def current_calibration() -> dict[str, float]:
            return {
                "red": red.get() / 100,
                "green": green.get() / 100,
                "blue": blue.get() / 100,
                "gamma": gamma.get(),
            }

        def send_test() -> None:
            color = self._calibrate_color(test_colors[test_name.get()], current_calibration())
            task_count = max(1, min(63, self._total_led_count.get() - 1))
            preview = DashboardSnapshot(
                remaining_percent=100,
                period_used_percent=0,
                master_brightness_percent=max(10, self._master_brightness.get()),
                tasks=[
                    TaskSlot(title=f"校准 {index + 1}", state=TaskState.RUNNING, progress=0)
                    for index in range(task_count)
                ],
                state_styles={TaskState.RUNNING: StateStyle(color=color, effect=1)},
            )
            self._worker.submit(preview)
            self._post_status(f"已向当前灯板发送{test_name.get()}测试色")

        ttk.Button(
            test_row, text="发送测试色", command=send_test, style="Preview.TButton"
        ).pack(side=tk.LEFT)

        ttk.Label(
            outer,
            text="建议顺序：先用中性白调三个通道，再用中灰调整伽马，最后检查综合色。",
            foreground="#718096",
            wraplength=520,
        ).pack(anchor=tk.W)

        def save_and_close() -> None:
            self._save_device_calibration(current_calibration())
            self._post_status("当前灯板的色彩校准已保存")
            dialog.destroy()

        def reset_calibration() -> None:
            defaults = {"red": 1.0, "green": 1.0, "blue": 1.0, "gamma": 2.2}
            red.set(100)
            green.set(100)
            blue.set(100)
            gamma.set(2.2)
            self._save_device_calibration(defaults)
            self._post_status("当前灯板的色彩校准已恢复默认值")

        actions = ttk.Frame(outer)
        actions.pack(fill=tk.X, side=tk.BOTTOM, pady=(20, 0))
        ttk.Button(
            actions, text="一键重置", command=reset_calibration, style="Danger.TButton"
        ).pack(side=tk.LEFT)
        ttk.Button(actions, text="取消", command=dialog.destroy).pack(side=tk.RIGHT)
        ttk.Button(
            actions, text="保存校准", command=save_and_close, style="Primary.TButton"
        ).pack(side=tk.RIGHT, padx=(0, 8))
        self._fit_dialog(dialog, 620, 610, 560, 520)

    def _open_debug_preview(self) -> None:
        """在真实任务数据接入前，手动预览系统灯与各任务状态。"""
        dialog = tk.Toplevel(self.root)
        dialog.title("灯光状态预览 / Debug")
        dialog.resizable(True, True)
        dialog.transient(self.root)

        outer = ttk.Frame(dialog, padding=16)
        outer.pack(fill=tk.BOTH, expand=True)

        system = ttk.LabelFrame(outer, text="系统灯预览", padding=10)
        system.pack(fill=tk.X)
        remaining = tk.IntVar(value=self._remaining.get())
        period_used = tk.IntVar(value=self._period_used.get())
        self._add_scale(system, "总体额度剩余", remaining, 0)
        self._add_scale(system, "短时繁忙程度", period_used, 1)
        ttk.Label(
            system,
            text=(
                "第一颗系统灯：颜色表示总体额度剩余，绿色充足、红色紧张；\n"
                "闪烁频率表示近期任务或请求的繁忙程度，数值越高闪烁越快。"
            ),
            foreground="#666666",
            justify=tk.LEFT,
        ).grid(row=2, column=0, columnspan=4, sticky=tk.W, pady=(8, 0))
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
        ttk.Button(
            actions, text="应用 Mock 数据并预览", command=send_preview, style="Primary.TButton"
        ).pack(side=tk.RIGHT, padx=(0, 8))
        self._fit_dialog(dialog, 620, 700, 540, 520)

    @staticmethod
    def _hex_color(color: tuple[int, int, int]) -> str:
        return f"#{color[0]:02X}{color[1]:02X}{color[2]:02X}"

    def _choose_style_color(self, state: TaskState) -> None:
        """使用 HSV 色环选择颜色，明度单独调节。"""
        dialog = tk.Toplevel(self.root)
        dialog.title(f"选择颜色 · {state.chinese_name}")
        dialog.resizable(True, True)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.configure(background="#F4F7FB")

        outer = ttk.Frame(dialog, padding=20)
        outer.pack(fill=tk.BOTH, expand=True)
        current = self._style_colors[state]
        hue, saturation, brightness = colorsys.rgb_to_hsv(*(channel / 255 for channel in current))
        selected_hue = hue
        selected_saturation = saturation
        brightness_value = tk.IntVar(value=round(brightness * 100))
        hex_value = tk.StringVar(value=self._hex_color(current))

        ttk.Label(outer, text="拖动色环选择色相与饱和度").pack(anchor=tk.W)
        wheel_size = 260
        radius = wheel_size // 2 - 4
        center = wheel_size // 2
        canvas = tk.Canvas(
            outer, width=wheel_size, height=wheel_size, background="#F4F7FB",
            highlightthickness=0, cursor="crosshair",
        )
        canvas.pack(pady=(8, 12))
        wheel = tk.PhotoImage(width=wheel_size, height=wheel_size)
        rows: list[str] = []
        for y in range(wheel_size):
            row: list[str] = []
            for x in range(wheel_size):
                dx, dy = x - center, y - center
                distance = math.hypot(dx, dy)
                if distance <= radius:
                    pixel_hue = (math.atan2(dy, dx) / (2 * math.pi)) % 1.0
                    pixel_saturation = distance / radius
                    channels = colorsys.hsv_to_rgb(pixel_hue, pixel_saturation, 1.0)
                    row.append("#%02X%02X%02X" % tuple(round(value * 255) for value in channels))
                else:
                    row.append("#F4F7FB")
            rows.append("{" + " ".join(row) + "}")
        wheel.put(" ".join(rows))
        canvas.create_image(0, 0, image=wheel, anchor=tk.NW)
        canvas.wheel_image = wheel
        marker = canvas.create_oval(0, 0, 12, 12, outline="white", width=3)

        preview_row = ttk.Frame(outer)
        preview_row.pack(fill=tk.X)
        preview = tk.Label(preview_row, width=8, background=hex_value.get(), relief=tk.FLAT)
        preview.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))

        hex_row = ttk.Frame(outer)
        hex_row.pack(fill=tk.X, pady=(10, 12))
        ttk.Label(hex_row, text="色值", width=8).pack(side=tk.LEFT)
        hex_entry = ttk.Entry(hex_row, textvariable=hex_value, width=12)
        hex_entry.pack(side=tk.LEFT, padx=8)

        def selected_color() -> tuple[int, int, int]:
            channels = colorsys.hsv_to_rgb(
                selected_hue, selected_saturation, brightness_value.get() / 100
            )
            return tuple(round(value * 255) for value in channels)

        def update_preview(_value: object = None) -> None:
            value = self._hex_color(selected_color())
            preview.configure(background=value)
            hex_value.set(value)

        def update_marker() -> None:
            angle = selected_hue * 2 * math.pi
            distance = selected_saturation * radius
            x = center + math.cos(angle) * distance
            y = center + math.sin(angle) * distance
            canvas.coords(marker, x - 6, y - 6, x + 6, y + 6)

        def select_from_wheel(event: tk.Event) -> None:
            nonlocal selected_hue, selected_saturation
            dx, dy = event.x - center, event.y - center
            distance = math.hypot(dx, dy)
            if distance > radius:
                scale = radius / distance
                dx, dy, distance = dx * scale, dy * scale, radius
            selected_hue = (math.atan2(dy, dx) / (2 * math.pi)) % 1.0
            selected_saturation = distance / radius
            update_marker()
            update_preview()

        # 只在明确单击时更新颜色，避免选中后圆点继续跟随鼠标移动。
        canvas.bind("<Button-1>", select_from_wheel)
        update_marker()

        brightness_row = ttk.Frame(outer)
        brightness_row.pack(fill=tk.X)
        ttk.Label(brightness_row, text="明度", width=8).pack(side=tk.LEFT)
        ttk.Scale(
            brightness_row, from_=0, to=100, variable=brightness_value,
            orient=tk.HORIZONTAL, command=update_preview,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        ttk.Label(brightness_row, textvariable=brightness_value, width=4).pack(side=tk.RIGHT)

        def apply_hex(_event: object = None) -> None:
            nonlocal selected_hue, selected_saturation
            value = hex_value.get().strip().lstrip("#")
            if len(value) != 6:
                return
            try:
                channels = tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))
            except ValueError:
                return
            selected_hue, selected_saturation, selected_brightness = colorsys.rgb_to_hsv(
                *(channel / 255 for channel in channels)
            )
            brightness_value.set(round(selected_brightness * 100))
            update_marker()
            update_preview()

        hex_entry.bind("<Return>", apply_hex)
        hex_entry.bind("<FocusOut>", apply_hex)

        def save_color() -> None:
            color = selected_color()
            self._style_colors[state] = color
            self._style_previews[state].configure(background=self._hex_color(color))
            self._style_buttons[state].configure(text=self._hex_color(color))
            dialog.destroy()

        actions = ttk.Frame(outer)
        actions.pack(fill=tk.X)
        ttk.Button(actions, text="取消", command=dialog.destroy).pack(side=tk.RIGHT)
        ttk.Button(actions, text="应用颜色", command=save_color, style="Primary.TButton").pack(
            side=tk.RIGHT, padx=(0, 8)
        )
        self._fit_dialog(dialog, 500, 620, 460, 520)

    def _post_status(self, message: str) -> None:
        self._events.put(message)

    def _clear_log(self) -> None:
        """只清空界面日志，不改变蓝牙连接和灯板配置。"""
        self._log.configure(state=tk.NORMAL)
        self._log.delete("1.0", tk.END)
        self._log.configure(state=tk.DISABLED)

    def _drain_events(self) -> None:
        while not self._events.empty():
            event = self._events.get()
            if isinstance(event, tuple) and event[0] == "scan_results":
                devices = event[1]
                tree = self._device_tree
                if tree is not None and tree.winfo_exists():
                    for item in tree.get_children():
                        tree.delete(item)
                    self._scanned_devices.clear()
                    bound_found = False
                    for index, device in enumerate(devices):
                        item_id = f"device_{index}"
                        self._scanned_devices[item_id] = device
                        is_bound = str(device["device_id"]).upper() == (
                            self._bound_device_id or ""
                        ).upper()
                        bound_found = bound_found or is_bound
                        tree.insert(
                            "",
                            tk.END,
                            iid=item_id,
                            values=(
                                device["name"],
                                device["device_id"],
                                f'{device["rssi"]} dBm',
                                "已绑定" if is_bound else "未绑定",
                            ),
                        )
                    if self._bound_device_id and not bound_found:
                        connected = bool(self._worker and self._worker.is_connected)
                        display_name = "已绑定灯板（已连接）" if connected else "已绑定灯板（当前离线）"
                        placeholder = {
                            "name": display_name,
                            "device_id": self._bound_device_id,
                            "address": "",
                            "rssi": None,
                        }
                        self._scanned_devices["bound_device"] = placeholder
                        tree.insert(
                            "", 0, iid="bound_device",
                            values=(
                                placeholder["name"], self._bound_device_id, "--",
                                "已连接" if connected else "离线",
                            ),
                        )
                continue
            message = str(event)
            # 顶部只展示连接生命周期；业务发送与校准消息仅写入日志。
            if message == "蓝牙已连接":
                self._status.set("已连接")
            elif message == "蓝牙已断开":
                self._status.set("未连接")
            elif message == "正在扫描附近灯板":
                self._status.set("扫描中")
            elif message.startswith("扫描完成"):
                self._status.set(
                    "已连接" if self._worker and self._worker.is_connected else "未连接"
                )
            elif message.startswith("正在查找") or message.startswith("正在连接已绑定"):
                self._status.set("连接中")
            tree = self._device_tree
            if tree is not None and tree.winfo_exists() and tree.exists("bound_device"):
                if message == "蓝牙已连接":
                    tree.item(
                        "bound_device",
                        values=("已绑定灯板（已连接）", self._bound_device_id, "--", "已连接"),
                    )
                elif message == "蓝牙已断开":
                    tree.item(
                        "bound_device",
                        values=("已绑定灯板（当前离线）", self._bound_device_id, "--", "离线"),
                    )
            timestamp = datetime.now().strftime("%H:%M:%S")
            self._log.configure(state=tk.NORMAL)
            self._log.insert(tk.END, f"{timestamp}  {message}\n")
            self._log.see(tk.END)
            self._log.configure(state=tk.DISABLED)
        self.root.after(100, self._drain_events)

    def _on_close(self) -> None:
        if self._worker is not None:
            self._worker.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    WindowsDashboardApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
