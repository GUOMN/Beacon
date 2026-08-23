from __future__ import annotations

import copy
import colorsys
import json
import math
import os
import queue
import base64
import subprocess
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from codex_status_core.models import DashboardSnapshot, StateStyle, TaskSlot, TaskState
from windows_app.ble_worker import BLEWorker, identify_status_device, scan_status_devices
from codex_status_core.event_data_source import EventDataSource
from codex_status_core.event_store import BridgeSnapshot, EventIngestServer, StatusEventStore
from codex_status_core.codex_session_source import CodexSessionSource
from codex_status_core.tray import TrayController
from codex_status_core.hook_adapter import report_codex_notification, report_hook
from codex_status_core.hook_manager import install as install_hook, providers as hook_providers, status as hook_status, uninstall as uninstall_hook

class WindowsDashboardApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Codex 状态灯")
        self.root.geometry("1120x760")
        self.root.minsize(860, 640)
        self._settings_path = Path(os.getenv("APPDATA", str(Path.home()))) / \
            "CodexStatusBridge" / "settings.json"

        self._events: queue.SimpleQueue[object] = queue.SimpleQueue()
        self._snapshot = DashboardSnapshot()
        self._remaining = tk.IntVar(value=100)
        self._period_used = tk.IntVar(value=0)
        self._master_brightness = tk.IntVar(value=60)
        self._busy_weight_vars = {
            "task": tk.IntVar(value=30),
            "token": tk.IntVar(value=20),
            "cpu": tk.IntVar(value=20),
            "memory": tk.IntVar(value=10),
            "disk": tk.IntVar(value=10),
            "network": tk.IntVar(value=10),
        }
        self._busy_formula_text = tk.StringVar()
        self._system_color_source = tk.StringVar(value=self._load_system_color_source())
        self._latest_resource_availability = {
            "CPU 可用程度": 100,
            "内存可用程度": 100,
            "磁盘可用程度": 100,
            "账号余量": 100,
        }
        self._total_led_count = tk.IntVar(value=6)
        self._total_led_text = tk.StringVar(value="总灯数：6")
        self._sleep_timeout_minutes = tk.IntVar(value=self._load_sleep_timeout_minutes())
        self._status = tk.StringVar(value="连接中")
        self._preview_active = False
        self._five_hour_tokens = tk.StringVar(value="等待数据源")
        self._seven_day_tokens = tk.StringVar(value="等待数据源")
        self._plan_balance = tk.StringVar(value="暂不可获取")
        self._status_tree: ttk.Treeview | None = None
        self._status_task_ids: dict[str, str] = {}
        self._status_records: dict[str, dict[str, object]] = {}
        self._lamp_color_images: dict[tuple[int, int, int], tk.PhotoImage] = {}
        self._operation_icons: dict[tuple[bool, bool], tk.PhotoImage] = {}
        self._lamp_swatch_labels: dict[str, tk.Label] = {}
        self._checked_task_ids: set[str] = set()
        self._state_filters = {
            state: tk.BooleanVar(value=True)
            for state in (TaskState.RUNNING, TaskState.WAITING, TaskState.SUCCESS, TaskState.WARNING, TaskState.FAILURE)
        }
        self._state_filter_summary = tk.StringVar(value="状态筛选（5）")
        self._drag_task_item: str | None = None
        self._drag_task_moved = False
        self._style_colors: dict[TaskState, tuple[int, int, int]] = {}
        self._style_effects: dict[TaskState, tk.StringVar] = {}
        self._style_frequencies: dict[TaskState, tk.IntVar] = {}
        self._style_auto_frequency: dict[TaskState, tk.BooleanVar] = {}
        self._style_duties: dict[TaskState, tk.IntVar] = {}
        self._style_buttons: dict[TaskState, ttk.Button] = {}
        self._style_previews: dict[TaskState, tk.Label] = {}
        self._device_tree: ttk.Treeview | None = None
        self._scanned_devices: dict[str, dict[str, object]] = {}
        for key, value in self._load_busy_weights().items():
            self._busy_weight_vars[key].set(value)
        self._busy_weights_cache = tuple(float(value.get()) for value in self._busy_weight_vars.values())
        self._update_busy_formula_text()
        self._bound_device_id = self._load_bound_device_id()
        self._calibration = self._load_device_calibration(self._bound_device_id)

        self._build_ui()
        self._worker: BLEWorker | None = None
        if self._bound_device_id:
            self._start_bound_worker(self._bound_device_id)
        else:
            self._status.set("未连接")
        self._event_store = StatusEventStore()
        self._event_server = EventIngestServer(self._event_store)
        self._event_server.start()
        self._codex_session_source = CodexSessionSource(self._event_store, self._post_status)
        self._codex_session_source.start()
        self._codex_data_source = EventDataSource(
            self._event_store,
            self._post_codex_snapshot,
            self._post_status,
            lambda: max(1, self._total_led_count.get() - 1),
            lambda: self._busy_weights_cache,
        )
        self._codex_data_source.start()
        self._tray = TrayController(
            lambda: self.root.after(0, self._show_from_tray),
            lambda: self.root.after(0, self._quit_app),
        )
        self._tray.start()
        self.root.after(200, self._refresh_status_page)
        self.root.after(100, self._drain_events)
        self.root.protocol("WM_DELETE_WINDOW", self._hide_to_tray)

    def _build_ui(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        # 采用接近 Codex 浅色模式的中性灰白色系，避免大面积高饱和蓝色。
        self.root.configure(background="#F7F7F7")
        self.root.option_add("*Font", ("Microsoft YaHei UI", 10))
        style.configure(".", font=("Microsoft YaHei UI", 10))
        style.configure("TFrame", background="#F7F7F7")
        style.configure("TLabel", background="#F7F7F7", foreground="#252525")
        style.configure("Modern.TNotebook", background="#F7F7F7", borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure(
            "Modern.TNotebook.Tab", background="#F7F7F7", foreground="#737A86",
            borderwidth=0, padding=(22, 11), font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.map(
            "Modern.TNotebook.Tab",
            background=[("selected", "#FFFFFF"), ("active", "#F1F4F8")],
            foreground=[("selected", "#2563EB"), ("active", "#344054")],
        )
        style.configure("Settings.TFrame", background="#F3F3F1")
        style.configure("Card.TFrame", background="#FAFAF8")
        style.configure(
            "Section.TButton",
            background="#FFFFFF",
            foreground="#2A2A2A",
            bordercolor="#E3E3E0",
            lightcolor="#FFFFFF",
            darkcolor="#FFFFFF",
            relief="flat",
            anchor=tk.W,
            padding=(12, 9),
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.map("Section.TButton", background=[("active", "#F0F0EC")])
        style.configure(
            "Modern.Vertical.TScrollbar",
            background="#C9C9C5",
            troughcolor="#F3F3F1",
            bordercolor="#F3F3F1",
            arrowcolor="#777773",
            gripcount=0,
            width=10,
        )
        style.map("Modern.Vertical.TScrollbar", background=[("active", "#AFAFAA")])
        style.configure(
            "TLabelframe",
            background="#F7F7F7",
            bordercolor="#E1E1E1",
            borderwidth=1,
            relief="solid",
        )
        style.configure(
            "TLabelframe.Label",
            background="#F7F7F7",
            foreground="#252525",
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.configure(
            "TButton",
            background="#ECECEC",
            foreground="#292929",
            borderwidth=0,
            padding=(14, 9),
            focusthickness=0,
        )
        style.map("TButton", background=[("active", "#E2E2E2"), ("pressed", "#D8D8D8")])
        style.configure(
            "Preview.TButton",
            background="#E9E9E9",
            foreground="#202020",
            font=("Microsoft YaHei UI", 11, "bold"),
            padding=(22, 14),
        )
        style.map("Preview.TButton", background=[("active", "#DEDEDE"), ("pressed", "#D4D4D4")])
        style.configure(
            "Primary.TButton",
            background="#202020",
            foreground="#FFFFFF",
            font=("Microsoft YaHei UI", 11, "bold"),
            padding=(20, 11),
        )
        style.configure(
            "Apply.TButton",
            background="#2563EB",
            foreground="#FFFFFF",
            font=("Microsoft YaHei UI", 11, "bold"),
            padding=(24, 10),
            borderwidth=1,
        )
        style.map("Apply.TButton", background=[("active", "#1D4ED8"), ("pressed", "#1E40AF")])
        style.map(
            "Primary.TButton",
            background=[("active", "#343434"), ("pressed", "#111111")],
            foreground=[("disabled", "#BDBDBD")],
        )
        style.configure(
            "Danger.TButton",
            background="#F7E8E8",
            foreground="#A12B2B",
            borderwidth=0,
            padding=(12, 7),
        )
        style.map("Danger.TButton", background=[("active", "#F0DADA"), ("pressed", "#E9CCCC")])
        style.configure(
            "FilterOn.TButton",
            background="#E8EEF9",
            foreground="#2457A7",
            padding=(8, 4),
            font=("Microsoft YaHei UI", 9),
        )
        style.map("FilterOn.TButton", background=[("active", "#DDE7F7"), ("pressed", "#D4E0F3")])
        style.configure(
            "FilterOff.TButton",
            background="#F1F1F1",
            foreground="#777777",
            padding=(8, 4),
            font=("Microsoft YaHei UI", 9),
        )
        style.map("FilterOff.TButton", background=[("active", "#E8E8E8"), ("pressed", "#DEDEDE")])
        style.configure(
            "TCombobox",
            padding=6,
            fieldbackground="#FFFFFF",
            background="#F2F2F2",
            foreground="#292929",
            arrowcolor="#626262",
            bordercolor="#D8D8D8",
            lightcolor="#D8D8D8",
            darkcolor="#D8D8D8",
            borderwidth=1,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", "#FFFFFF")],
            background=[("readonly", "#F2F2F2"), ("active", "#E8E8E8")],
            bordercolor=[("focus", "#A8A8A8")],
        )
        style.configure("TSpinbox", padding=5, fieldbackground="#FFFFFF")
        style.configure(
            "Horizontal.TScale",
            background="#F7F7F7",
            troughcolor="#DEDEDE",
            bordercolor="#DEDEDE",
            lightcolor="#555555",
            darkcolor="#555555",
            sliderthickness=16,
            borderwidth=0,
        )
        style.configure(
            "Treeview",
            background="#FFFFFF",
            fieldbackground="#FFFFFF",
            foreground="#252525",
            rowheight=36,
            font=("Microsoft YaHei UI", 11),
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            background="#EEEEEE",
            foreground="#303030",
            font=("Microsoft YaHei UI", 10, "bold"),
            padding=7,
        )
        style.map("Treeview", background=[("selected", "#E2E2E2")], foreground=[("selected", "#202020")])

        outer = ttk.Frame(self.root, padding=(24, 18, 24, 20))
        outer.pack(fill=tk.BOTH, expand=True)

        top_bar = ttk.Frame(outer)
        top_bar.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(top_bar, text="Codex Status Bridge", font=("Microsoft YaHei UI", 15, "bold"), foreground="#101828").pack(side=tk.LEFT)
        ttk.Label(
            top_bar,
            textvariable=self._status,
            font=("Microsoft YaHei UI", 10, "bold"),
            foreground="#168A52",
        ).pack(side=tk.RIGHT, anchor=tk.E)

        notebook = ttk.Notebook(outer, style="Modern.TNotebook")
        notebook.pack(fill=tk.BOTH, expand=True)
        status_tab = ttk.Frame(notebook, padding=16)
        settings_tab = ttk.Frame(notebook)
        notebook.add(status_tab, text="任务面板")
        notebook.add(settings_tab, text="配置")

        metrics = ttk.Frame(status_tab)
        metrics.pack(fill=tk.X, pady=(0, 14))
        for column, (title, value, detail) in enumerate((
            ("计划余额", self._plan_balance, "官方暂无桌面套餐余量接口"),
            ("五小时 Token", self._five_hour_tokens, "等待独立用量数据源"),
            ("七天 Token", self._seven_day_tokens, "等待独立用量数据源"),
        )):
            card = ttk.LabelFrame(metrics, text=title, padding=12)
            card.grid(row=0, column=column, sticky=tk.NSEW, padx=(0 if column == 0 else 8, 0))
            ttk.Label(card, textvariable=value, font=("Microsoft YaHei UI", 17, "bold")).pack(anchor=tk.W)
            ttk.Label(card, text=detail, foreground="#777777").pack(anchor=tk.W, pady=(5, 0))
            metrics.columnconfigure(column, weight=1)

        task_header = ttk.Frame(status_tab)
        task_header.pack(fill=tk.X)
        ttk.Label(task_header, text="任务与灯位", font=("Microsoft YaHei UI", 12, "bold")).pack(side=tk.LEFT)
        ttk.Button(task_header, text="数据源", command=self._open_data_sources).pack(side=tk.RIGHT)
        filter_button = ttk.Menubutton(task_header, textvariable=self._state_filter_summary)
        filter_button.pack(side=tk.LEFT, padx=(18, 0))
        filter_menu = tk.Menu(filter_button, tearoff=False, font=("Microsoft YaHei UI", 10))
        for state in (TaskState.RUNNING, TaskState.WAITING, TaskState.SUCCESS, TaskState.WARNING, TaskState.FAILURE):
            filter_menu.add_checkbutton(
                label=state.chinese_name,
                variable=self._state_filters[state],
                command=self._state_filter_changed,
            )
        filter_menu.add_separator()
        filter_menu.add_command(label="全部显示", command=lambda: self._set_all_state_filters(True))
        filter_menu.add_command(label="全部隐藏", command=lambda: self._set_all_state_filters(False))
        filter_button.configure(menu=filter_menu)
        task_actions = ttk.Frame(status_tab)
        task_actions.pack(fill=tk.X, pady=(4, 8))
        ttk.Button(task_actions, text="删除选中任务", command=self._delete_selected_tasks, style="Danger.TButton").pack(side=tk.RIGHT)
        ttk.Button(task_actions, text="删除已完成任务", command=self._delete_completed_tasks, style="Danger.TButton").pack(side=tk.RIGHT, padx=(0, 8))
        ttk.Label(task_actions, text="第一个灯是系统状态灯，从第二个灯开始计数，灯位为 1；任务信息只读，使用操作列选择、拖动或固定。", foreground="#777777").pack(side=tk.LEFT)
        self._status_tree = ttk.Treeview(status_tab, columns=("led", "effect", "task", "state", "source"), show="tree headings", selectmode="none")
        self._status_tree.heading("#0", text="🔷 操作")
        self._status_tree.column("#0", width=112, anchor=tk.CENTER)
        for key, title, width in (("led", "灯位", 60), ("effect", "当前灯效", 120), ("task", "任务", 360), ("state", "任务状态", 100), ("source", "来源", 90)):
            self._status_tree.heading(key, text=title, anchor=tk.CENTER)
            self._status_tree.column(key, width=width, anchor=tk.CENTER)
        self._status_tree.pack(fill=tk.BOTH, expand=True)
        self._status_tree.bind("<ButtonPress-1>", self._begin_task_drag, add="+")
        self._status_tree.bind("<B1-Motion>", self._move_task_drag, add="+")
        self._status_tree.bind("<ButtonRelease-1>", self._finish_task_drag, add="+")
        self._status_tree.bind("<Configure>", lambda _event: self.root.after_idle(self._position_lamp_swatches), add="+")
        self._status_tree.bind("<MouseWheel>", lambda _event: self.root.after_idle(self._position_lamp_swatches), add="+")

        # 设置项较多，使用画布承载可滚动内容，避免小窗口下底部配置被裁掉。
        settings_canvas = tk.Canvas(settings_tab, highlightthickness=0, background="#F3F3F1")
        settings_scrollbar = ttk.Scrollbar(
            settings_tab, orient=tk.VERTICAL, command=settings_canvas.yview,
            style="Modern.Vertical.TScrollbar",
        )
        settings_canvas.configure(yscrollcommand=settings_scrollbar.set)
        settings_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        settings_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        settings_content = ttk.Frame(settings_canvas, padding=18, style="Settings.TFrame")
        settings_window = settings_canvas.create_window((0, 0), window=settings_content, anchor=tk.NW)

        def refresh_settings_scroll(_event: object = None) -> None:
            settings_canvas.configure(scrollregion=settings_canvas.bbox("all"))

        def resize_settings_content(event: tk.Event) -> None:
            settings_canvas.itemconfigure(settings_window, width=event.width)

        def scroll_settings(event: tk.Event) -> None:
            settings_canvas.yview_scroll(int(-event.delta / 120), "units")

        settings_content.bind("<Configure>", refresh_settings_scroll)
        settings_canvas.bind("<Configure>", resize_settings_content)
        settings_canvas.bind("<Enter>", lambda _event: settings_canvas.bind_all("<MouseWheel>", scroll_settings))
        settings_canvas.bind("<Leave>", lambda _event: settings_canvas.unbind_all("<MouseWheel>"))

        def collapsible_section(title: str, *, expanded: bool = True) -> ttk.Frame:
            """创建可独立折叠的设置区块，返回用于放置实际控件的内容容器。"""
            section = tk.Frame(
                settings_content, background="#FFFFFF", borderwidth=0,
                highlightthickness=1, highlightbackground="#DEDEDA",
            )
            section.pack(fill=tk.X, pady=(0, 12))
            body = ttk.Frame(section, padding=(14, 6, 14, 14), style="Card.TFrame")
            opened = tk.BooleanVar(value=expanded)
            toggle = ttk.Button(section, style="Section.TButton")

            def apply_state() -> None:
                toggle.configure(text=("▾  " if opened.get() else "▸  ") + title)
                if opened.get():
                    body.pack(fill=tk.X)
                else:
                    body.pack_forget()
                settings_content.after_idle(refresh_settings_scroll)

            def toggle_state() -> None:
                opened.set(not opened.get())
                apply_state()

            toggle.configure(command=toggle_state)
            toggle.pack(fill=tk.X, anchor=tk.W)
            apply_state()
            return body

        settings_header = ttk.Frame(settings_content)
        settings_header.pack(fill=tk.X, pady=(0, 12))
        header_text = ttk.Frame(settings_header)
        header_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(header_text, text="灯板设置", font=("Microsoft YaHei UI", 14, "bold")).pack(anchor=tk.W)
        ttk.Label(header_text, text="统一配置灯珠数量、系统灯以及所有任务状态的颜色和动画。", foreground="#777777").pack(anchor=tk.W, pady=(3, 0))
        ttk.Button(settings_header, text="设备管理", command=self._open_device_manager).pack(side=tk.RIGHT)
        ttk.Button(settings_header, text="灯带颜色校准", command=self._open_calibration).pack(
            side=tk.RIGHT, padx=(0, 8)
        )
        ttk.Button(settings_header, text="保存并生效", command=lambda: self._send(human_action=True), style="Apply.TButton").pack(
            side=tk.RIGHT, padx=(0, 8)
        )

        global_section = collapsible_section("全局灯带")
        global_card = ttk.LabelFrame(global_section, text="整体亮度", padding=12)
        global_card.pack(fill=tk.X)
        self._add_scale(global_card, "所有灯光亮度", self._master_brightness, 0)

        usage_section = collapsible_section("第一颗灯 · 系统用量")
        usage = ttk.LabelFrame(usage_section, text="闪烁频率与颜色来源", padding=12)
        usage.pack(fill=tk.X)
        weight_row = ttk.Frame(usage)
        weight_row.grid(row=0, column=0, columnspan=3, sticky=tk.W, pady=(2, 0))
        ttk.Label(weight_row, text="繁忙度权重").pack(side=tk.LEFT, padx=(0, 8))
        for key, title in (("task", "任务数"), ("token", "Token"), ("cpu", "CPU"), ("memory", "内存"), ("disk", "磁盘"), ("network", "网络")):
            ttk.Label(weight_row, text=title).pack(side=tk.LEFT, padx=(8, 3))
            field = ttk.Spinbox(weight_row, from_=0, to=100, width=4, textvariable=self._busy_weight_vars[key])
            field.pack(side=tk.LEFT)
            field.bind("<FocusOut>", self._busy_weights_changed)
            field.bind("<Return>", self._busy_weights_changed)
            ttk.Label(weight_row, text="%").pack(side=tk.LEFT)
        ttk.Label(
            usage,
            textvariable=self._busy_formula_text,
        ).grid(row=1, column=0, columnspan=3, sticky=tk.W, pady=(8, 12))
        color_row = ttk.Frame(usage)
        color_row.grid(row=2, column=0, columnspan=3, sticky=tk.W)
        ttk.Label(color_row, text="颜色指标来源").pack(side=tk.LEFT, padx=(0, 10))
        color_source = ttk.Combobox(
            color_row, textvariable=self._system_color_source,
            values=("CPU 可用程度", "内存可用程度", "磁盘可用程度", "账号余量"),
            state="readonly", width=18,
        )
        color_source.pack(side=tk.LEFT)
        color_source.bind("<<ComboboxSelected>>", self._system_color_source_changed)
        ttk.Label(color_row, text="绿色表示最可用，随可用程度降低平滑过渡到红色", foreground="#667085").pack(side=tk.LEFT, padx=(12, 0))
        usage.columnconfigure(1, weight=1)

        led_section = collapsible_section("灯带")
        led_summary = ttk.LabelFrame(led_section, text="灯珠数量", padding=12)
        led_summary.pack(fill=tk.X)
        ttk.Button(
            led_summary,
            textvariable=self._total_led_text,
            command=self._open_led_settings,
        ).pack(anchor=tk.W)

        themes_section = collapsible_section("状态颜色与行为（对所有任务灯生效）")
        themes = ttk.LabelFrame(themes_section, text="任务状态样式", padding=12)
        themes.pack(fill=tk.X)
        defaults = {
            TaskState.RUNNING: ((0, 90, 255), "呼吸", 50, 15),
            TaskState.WAITING: ((255, 150, 0), "常亮", 50, 15),
            TaskState.SUCCESS: ((0, 255, 60), "呼吸", 38, 15),
            TaskState.WARNING: ((255, 80, 0), "呼吸", 67, 15),
            TaskState.FAILURE: ((255, 0, 0), "常亮", 50, 15),
        }
        automatic_states = self._load_auto_frequency_states()
        for row, (state, (color, effect_name, frequency, duty)) in enumerate(defaults.items()):
            self._style_colors[state] = color
            effect_var = tk.StringVar(value=effect_name)
            self._style_effects[state] = effect_var
            frequency_var = tk.IntVar(value=frequency)
            duty_var = tk.IntVar(value=duty)
            self._style_frequencies[state] = frequency_var
            self._style_duties[state] = duty_var
            auto_var = tk.BooleanVar(value=state.name in automatic_states)
            self._style_auto_frequency[state] = auto_var
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
            auto_input = ttk.Checkbutton(themes, text="自动", variable=auto_var)
            auto_input.grid(row=row, column=4, padx=(8, 2))
            frequency_input = ttk.Spinbox(
                themes, from_=6, to=300, textvariable=frequency_var, width=6,
            )
            frequency_input.grid(row=row, column=5, padx=(8, 2))
            frequency_label = ttk.Label(themes, text="次/分")
            frequency_label.grid(row=row, column=6, sticky=tk.W)
            duty_input = ttk.Spinbox(
                themes, from_=1, to=100, textvariable=duty_var, width=5,
            )
            duty_input.grid(row=row, column=7, padx=(8, 2))
            duty_label = ttk.Label(themes, text="占空比 %")
            duty_label.grid(row=row, column=8, sticky=tk.W)

            def update_timing_inputs(
                _event: object = None,
                selected_effect: tk.StringVar = effect_var,
                automatic: tk.BooleanVar = auto_var,
                automatic_widget: ttk.Checkbutton = auto_input,
                frequency_widget: ttk.Spinbox = frequency_input,
                frequency_text: ttk.Label = frequency_label,
                duty_widget: ttk.Spinbox = duty_input,
                duty_text: ttk.Label = duty_label,
            ) -> None:
                effect_name = selected_effect.get()
                if effect_name == "常亮":
                    automatic_widget.grid_remove()
                    frequency_widget.grid_remove()
                    frequency_text.grid_remove()
                    duty_widget.grid_remove()
                    duty_text.grid_remove()
                elif effect_name == "呼吸":
                    automatic_widget.grid()
                    frequency_widget.grid()
                    frequency_text.grid()
                    duty_widget.grid_remove()
                    duty_text.grid_remove()
                else:
                    automatic_widget.grid()
                    frequency_widget.grid()
                    frequency_text.grid()
                    duty_widget.grid()
                    duty_text.grid()
                frequency_widget.configure(state=tk.DISABLED if automatic.get() and effect_name != "常亮" else tk.NORMAL)

            effect_input.bind("<<ComboboxSelected>>", update_timing_inputs)
            auto_input.configure(command=update_timing_inputs)
            update_timing_inputs()
        preview_area = ttk.Frame(themes)
        preview_area.grid(row=0, column=9, rowspan=5, padx=(28, 4), sticky=tk.NSEW)
        ttk.Button(
            preview_area,
            text="设置状态并预览",
            command=self._open_debug_preview,
            style="Preview.TButton",
        ).pack(fill=tk.X)
        ttk.Label(
            preview_area,
            text="设置一组 Mock 数据，\n即时查看灯带效果",
            foreground="#707070",
            justify=tk.CENTER,
        ).pack(pady=(10, 0))

        log_section = collapsible_section("连接日志", expanded=False)
        log_header = ttk.Frame(log_section)
        log_header.pack(fill=tk.X)
        ttk.Label(
            log_header,
            text="连接日志",
            font=("Microsoft YaHei UI", 10, "bold"),
        ).grid(row=0, column=0, sticky=tk.W)
        ttk.Button(log_header, text="一键清空", command=self._clear_log).grid(row=0, column=2, sticky=tk.E)
        log_header.columnconfigure(1, weight=1)
        self._log = tk.Text(
            log_section,
            height=12,
            state=tk.DISABLED,
            font=("Cascadia Mono", 9),
            background="#FFFFFF",
            foreground="#3A3A3A",
            insertbackground="#202020",
            selectbackground="#DDDDDD",
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

    def _open_data_sources(self) -> None:
        """管理官方 Hook；只有用户点击时才修改对应工具配置。"""
        dialog = tk.Toplevel(self.root)
        dialog.title("任务数据源")
        dialog.transient(self.root)
        dialog.configure(background="#F7F7F7")
        body = ttk.Frame(dialog, padding=20)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="官方 Hook 数据源", font=("Microsoft YaHei UI", 15, "bold")).pack(anchor=tk.W)
        ttk.Label(
            body,
            text="任务事件会自动映射到灯板。不会保存提示词正文；状态同步失败不影响原任务。",
            foreground="#666666",
            wraplength=690,
        ).pack(anchor=tk.W, pady=(6, 16))
        rows = ttk.Frame(body)
        rows.pack(fill=tk.BOTH, expand=True)

        def refresh() -> None:
            for child in rows.winfo_children():
                child.destroy()
            for index, provider in enumerate(hook_providers()):
                state = hook_status(provider)
                ttk.Label(rows, text=provider.name, width=22, font=("Microsoft YaHei UI", 10, "bold")).grid(row=index, column=0, sticky=tk.W, pady=7)
                ttk.Label(rows, text=state, width=10, foreground="#39734D" if state == "已启用" else "#666666").grid(row=index, column=1, sticky=tk.W)
                note = provider.note or "开始、等待、完成和失败事件"
                ttk.Label(rows, text=note, foreground="#777777", wraplength=300).grid(row=index, column=2, sticky=tk.W, padx=(8, 12))
                if provider.supported:
                    action = uninstall_hook if state == "已启用" else install_hook
                    label = "停用" if state == "已启用" else "启用"

                    def run(selected=provider, operation=action) -> None:
                        try:
                            operation(selected)
                            self._post_status(f"{selected.name} 数据源配置已更新")
                        except Exception as exc:
                            self._post_status(f"{selected.name} 配置失败：{exc}")
                        refresh()

                    ttk.Button(rows, text=label, command=run).grid(row=index, column=3, sticky=tk.E)
            rows.columnconfigure(2, weight=1)

        refresh()
        ttk.Button(body, text="关闭", command=dialog.destroy).pack(anchor=tk.E, pady=(16, 0))
        self._fit_dialog(dialog, 780, 520, 620, 420)
        dialog.grab_set()

    def _send(self, *, human_action: bool = False) -> None:
        # 主界面不再人工编辑任务。发送时保留外部数据源已有状态，新增灯位默认空闲。
        task_count = max(1, min(63, self._total_led_count.get() - 1))
        tasks = []
        for index in range(task_count):
            if index < len(self._snapshot.tasks):
                task = copy.deepcopy(self._snapshot.tasks[index])
                automatic = self._style_auto_frequency.get(task.state)
                task.automatic_frequency = bool(automatic and automatic.get())
                if not task.automatic_frequency:
                    frequency = self._style_frequencies.get(task.state)
                    task.animation_period_ms = (
                        max(200, min(10000, round(60000 / max(6, min(300, frequency.get())))))
                        if frequency is not None else 0
                    )
                tasks.append(task)
            else:
                tasks.append(TaskSlot(title=f"任务 {index + 1}", state=TaskState.IDLE, progress=0))
        self._snapshot = DashboardSnapshot(
            remaining_percent=max(0, min(100, self._remaining.get())),
            period_used_percent=max(0, min(100, self._period_used.get())),
            master_brightness_percent=max(0, min(100, self._master_brightness.get())),
            sleep_timeout_minutes=max(1, min(1440, self._sleep_timeout_minutes.get())),
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
        if human_action:
            self._save_auto_frequency_states()
            self._post_status("灯板配置已下发")

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
        ttk.Label(content, text="断连后休眠（分钟）").grid(row=2, column=0, sticky=tk.W)
        sleep_value = tk.IntVar(value=self._sleep_timeout_minutes.get())
        ttk.Spinbox(content, from_=1, to=1440, textvariable=sleep_value, width=8).grid(
            row=2, column=1, padx=(12, 0)
        )
        ttk.Label(content, text="休眠后灯光和蓝牙全部关闭，短按 RESET 重新启动。", foreground="#666666").grid(
            row=3, column=0, columnspan=2, sticky=tk.W, pady=(10, 16)
        )

        def apply_and_close() -> None:
            try:
                led_count = value.get()
            except tk.TclError:
                led_count = self._total_led_count.get()
            led_count = max(2, min(64, led_count))
            self._total_led_count.set(led_count)
            self._total_led_text.set(f"总灯数：{led_count}")
            try:
                sleep_minutes = max(1, min(1440, sleep_value.get()))
            except tk.TclError:
                sleep_minutes = self._sleep_timeout_minutes.get()
            self._sleep_timeout_minutes.set(sleep_minutes)
            self._save_sleep_timeout_minutes(sleep_minutes)
            dialog.destroy()

        buttons = ttk.Frame(content)
        buttons.grid(row=4, column=0, columnspan=2, sticky=tk.E)
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
            columns=("name", "device_id", "rssi", "firmware", "binding"),
            show="headings",
            selectmode="browse",
            height=8,
        )
        tree.heading("name", text="设备名称")
        tree.heading("device_id", text="唯一 ID")
        tree.heading("rssi", text="信号强度")
        tree.heading("firmware", text="固件版本 / OTA 槽")
        tree.heading("binding", text="绑定状态")
        tree.column("name", width=210)
        tree.column("device_id", width=130, anchor=tk.CENTER)
        tree.column("rssi", width=90, anchor=tk.CENTER)
        tree.column("firmware", width=210, anchor=tk.CENTER)
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
                values=(
                    display_name, self._bound_device_id, "--",
                    self._worker.firmware_info if connected and self._worker else "--",
                    "已连接" if connected else "连接中",
                ),
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
            if is_bound:
                ota_button.pack(side=tk.RIGHT, padx=8, before=bind_button)
            else:
                ota_button.pack_forget()

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
        ota_button = ttk.Button(actions, text="固件升级", command=self._start_ota_update)
        tree.bind("<<TreeviewSelect>>", update_actions)
        scan_status_devices(self._post_scan_results, self._post_status)
        self._fit_dialog(dialog, 760, 520, 680, 460)

    def _start_ota_update(self) -> None:
        """选择应用固件并交给共享蓝牙核心写入备用 OTA 分区。"""
        if self._worker is None or not self._worker.is_connected:
            self._post_status("请先连接已绑定灯板再升级")
            return
        path = filedialog.askopenfilename(
            parent=self.root,
            title="选择 ESP32-C3 应用固件",
            filetypes=(("ESP32 固件", "*.bin"), ("所有文件", "*.*")),
        )
        if not path:
            return
        if "initial" in Path(path).stem.lower():
            self._post_status("首次串口整包不能用于蓝牙升级，请选择 OTA 应用固件")
            return
        try:
            firmware = Path(path).read_bytes()
        except OSError as exc:
            self._post_status(f"读取固件失败：{exc}")
            return
        if len(firmware) < 1024 or len(firmware) > 2 * 1024 * 1024:
            self._post_status("固件文件大小无效")
            return
        if not messagebox.askyesno(
            "蓝牙升级",
            "升级期间请保持灯板供电和电脑蓝牙连接。校验完成后灯板会自动重启。\n\n现在开始吗？",
            parent=self.root,
        ):
            return
        self._worker.submit_ota(firmware)
        self._post_status("蓝牙固件升级已开始")

    def _load_bound_device_id(self) -> str | None:
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
            value = str(data.get("bound_device_id", "")).upper()
            return value if len(value) == 6 else None
        except (OSError, ValueError, TypeError):
            return None

    def _load_busy_weights(self) -> dict[str, int]:
        defaults = {"task": 30, "token": 20, "cpu": 20, "memory": 10, "disk": 10, "network": 10}
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
            stored = data.get("busy_weights", {})
            return {key: max(0, min(100, int(stored.get(key, value)))) for key, value in defaults.items()}
        except (OSError, ValueError, TypeError, AttributeError):
            return defaults

    def _load_system_color_source(self) -> str:
        allowed = {"CPU 可用程度", "内存可用程度", "磁盘可用程度", "账号余量"}
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
            source = str(data.get("system_color_source", "账号余量"))
            return source if source in allowed else "账号余量"
        except (OSError, ValueError, TypeError):
            return "账号余量"

    def _system_color_source_changed(self, _event: object = None) -> None:
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            data = {}
        data["system_color_source"] = self._system_color_source.get()
        self._settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self._remaining.set(self._latest_resource_availability.get(self._system_color_source.get(), 100))
        if not self._preview_active and self._worker is not None and self._worker.is_connected:
            self._send()

    def _load_auto_frequency_states(self) -> set[str]:
        defaults = {TaskState.RUNNING.name, TaskState.SUCCESS.name, TaskState.WARNING.name}
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
            stored = data.get("auto_frequency_states")
            if not isinstance(stored, list):
                return defaults
            valid = {state.name for state in TaskState if state != TaskState.IDLE}
            return {str(name) for name in stored if str(name) in valid}
        except (OSError, ValueError, TypeError):
            return defaults

    def _save_auto_frequency_states(self) -> None:
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            data = {}
        data["auto_frequency_states"] = [
            state.name for state, enabled in self._style_auto_frequency.items() if enabled.get()
        ]
        self._settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_sleep_timeout_minutes(self) -> int:
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
            return max(1, min(1440, int(data.get("sleep_timeout_minutes", 10))))
        except (OSError, ValueError, TypeError):
            return 10

    def _save_sleep_timeout_minutes(self, minutes: int) -> None:
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            data = {}
        data["sleep_timeout_minutes"] = max(1, min(1440, int(minutes)))
        self._settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _update_busy_formula_text(self) -> None:
        values = {key: max(0, min(100, int(value.get()))) for key, value in self._busy_weight_vars.items()}
        total = sum(values.values())
        self._busy_formula_text.set(
            "闪烁速度表示综合繁忙度："
            f"任务数 {values['task']} + Token {values['token']} + CPU {values['cpu']} + 内存 {values['memory']} + "
            f"磁盘 {values['disk']} + 网络 {values['network']}（合计 {total}，计算时自动归一化）。"
        )

    def _save_busy_weights(self) -> None:
        values = {key: max(0, min(100, int(value.get()))) for key, value in self._busy_weight_vars.items()}
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            data = {}
        data["busy_weights"] = values
        self._settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self._busy_weights_cache = tuple(float(values[key]) for key in self._busy_weight_vars)
        self._update_busy_formula_text()

    def _busy_weights_changed(self, _event: object = None) -> None:
        try:
            self._save_busy_weights()
        except (tk.TclError, ValueError):
            return

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

    def _post_codex_snapshot(self, snapshot: BridgeSnapshot) -> None:
        self._events.put(("codex_snapshot", snapshot))

    def _state_filter_changed(self) -> None:
        selected_count = sum(1 for enabled in self._state_filters.values() if enabled.get())
        self._state_filter_summary.set(f"状态筛选（{selected_count}）")
        self._refresh_status_page()

    def _set_all_state_filters(self, enabled: bool) -> None:
        for state_filter in self._state_filters.values():
            state_filter.set(enabled)
        self._state_filter_changed()

    def _operation_icon(self, checked: bool, pinned: bool) -> tk.PhotoImage:
        """绘制稳定的操作区图标，避免彩色 Emoji 在不同 Windows 字体下错位。"""
        key = (checked, pinned)
        cached = self._operation_icons.get(key)
        if cached is not None:
            return cached
        image = tk.PhotoImage(width=104, height=24)
        image.put("#FFFFFF", to=(0, 0, 104, 24))
        # 复选框。
        image.put("#AEB4BE", to=(5, 4, 23, 20))
        image.put("#FFFFFF", to=(7, 6, 21, 18))
        if checked:
            image.put("#2563EB", to=(7, 6, 21, 18))
            for x, y in ((10, 12), (11, 13), (12, 14), (13, 13), (14, 12), (15, 11), (16, 10), (17, 9)):
                image.put("#FFFFFF", to=(x, y, x + 2, y + 2))
        # 拖动点阵。
        for x in (39, 46):
            for y in (7, 12, 17):
                image.put("#89919E", to=(x, y, x + 3, y + 3))
        # 独立图钉按钮：固定后以黄色背景和倾斜图钉明确高亮。
        button_fill = "#FFF1A8" if pinned else "#FFFFFF"
        button_border = "#E0B100" if pinned else "#C7CCD4"
        image.put(button_border, to=(67, 2, 102, 22))
        image.put(button_fill, to=(69, 4, 100, 20))
        pin_color = "#A66A00" if pinned else "#68707C"
        if pinned:
            image.put(pin_color, to=(79, 7, 91, 10))
            image.put(pin_color, to=(87, 9, 90, 14))
            image.put(pin_color, to=(85, 12, 88, 16))
            image.put(pin_color, to=(82, 15, 86, 18))
        else:
            image.put(pin_color, to=(80, 6, 91, 9))
            image.put(pin_color, to=(83, 9, 88, 14))
            image.put(pin_color, to=(81, 14, 90, 16))
            image.put(pin_color, to=(85, 16, 87, 20))
        self._operation_icons[key] = image
        return image

    def _position_lamp_swatches(self) -> None:
        """把真实 RGB 色块覆盖到当前灯效单元格，并跟随表格滚动。"""
        tree = self._status_tree
        if tree is None:
            return
        for iid, label in self._lamp_swatch_labels.items():
            bounds = tree.bbox(iid, "effect")
            if not bounds:
                label.place_forget()
                continue
            x, y, _width, height = bounds
            label.place(x=x + 10, y=y + max(3, (height - 14) // 2), width=18, height=14)

    def _refresh_status_page(self) -> None:
        tree = self._status_tree
        if tree is None or not tree.winfo_exists():
            return
        for label in self._lamp_swatch_labels.values():
            label.destroy()
        self._lamp_swatch_labels.clear()
        records = self._event_store.latest_records(500)
        position_by_task_id = {
            str(record["task_id"]): index + 1
            for index, record in enumerate(records)
        }
        enabled_states = {state.name.lower() for state, enabled in self._state_filters.items() if enabled.get()}
        records = [record for record in records if record["state"] in enabled_states]
        tree.delete(*tree.get_children())
        self._status_task_ids.clear()
        self._status_records.clear()
        for index, record in enumerate(records):
            iid = f"task_{index}"
            self._status_task_ids[iid] = str(record["task_id"])
            self._status_records[iid] = record
            state = TaskState[record["state"].upper()].chinese_name
            state_enum = TaskState[record["state"].upper()]
            color = self._style_colors.get(state_enum, (0, 0, 0))
            effect = self._style_effects[state_enum].get() if state_enum in self._style_effects else "熄灭"
            lamp_effect = f"       {effect}"
            task_capacity = max(1, self._total_led_count.get() - 1)
            actual_position = position_by_task_id[str(record["task_id"])]
            led_position: object = actual_position if actual_position <= task_capacity else "未分配"
            checked = str(record["task_id"]) in self._checked_task_ids
            pinned = bool(record.get("pinned"))
            tree.insert(
                "", tk.END, iid=iid,
                text="",
                image=self._operation_icon(checked, pinned),
                values=(led_position, lamp_effect, record["title"], state, record["source"]),
            )
            self._lamp_swatch_labels[iid] = tk.Label(
                tree, background=self._hex_color(color), borderwidth=1, relief=tk.SOLID,
            )
        self.root.after_idle(self._position_lamp_swatches)

    def _begin_task_drag(self, event: tk.Event) -> str | None:
        tree = self._status_tree
        if tree is None:
            return None
        row = tree.identify_row(event.y)
        self._drag_task_item = None
        self._drag_task_moved = False
        if not row:
            return None
        column = tree.identify_column(event.x)
        task_id = self._status_task_ids.get(row)
        if column != "#0":
            return None
        bounds = tree.bbox(row, "#0")
        relative_x = event.x - bounds[0] if bounds else 0
        if relative_x >= 70 and task_id:
            record = self._status_records.get(row, {})
            # 固定前先把当前自动排序写入灯位，避免点击固定后任务跳到旧位置。
            current_order = [str(item["task_id"]) for item in self._event_store.latest_records(500)]
            self._event_store.reorder_tasks(current_order)
            self._event_store.set_pinned(task_id, not bool(record.get("pinned")))
            self._refresh_status_page()
            return "break"
        if relative_x < 35 and task_id:
            if task_id in self._checked_task_ids:
                self._checked_task_ids.remove(task_id)
            else:
                self._checked_task_ids.add(task_id)
            self._refresh_status_page()
            return "break"
        if relative_x >= 70:
            return "break"
        self._drag_task_item = row
        tree.focus(row)
        return "break"

    def _move_task_drag(self, event: tk.Event) -> None:
        tree = self._status_tree
        source = self._drag_task_item
        if tree is None or not source:
            return
        target = tree.identify_row(event.y)
        if target and target != source:
            children = list(tree.get_children())
            tree.move(source, "", children.index(target))
            self._drag_task_moved = True

    def _finish_task_drag(self, event: tk.Event) -> None:
        tree = self._status_tree
        source = self._drag_task_item
        self._drag_task_item = None
        if tree is None or not source:
            return
        if not self._drag_task_moved:
            return
        ordered_ids = [self._status_task_ids[item] for item in tree.get_children() if item in self._status_task_ids]
        self._event_store.reorder_tasks(ordered_ids)
        source_task_id = self._status_task_ids.get(source)
        if source_task_id:
            self._event_store.set_pinned(source_task_id, True)
        self._refresh_status_page()
        self._post_status("任务灯位顺序已保存")

    def _delete_selected_tasks(self) -> None:
        tree = self._status_tree
        if tree is None:
            return
        task_ids = list(self._checked_task_ids)
        if not task_ids:
            return
        self._event_store.delete_tasks(task_ids)
        self._checked_task_ids.difference_update(task_ids)
        self._refresh_status_page()
        self._post_status(f"已永久删除 {len(task_ids)} 个任务")

    def _delete_completed_tasks(self) -> None:
        records = self._event_store.latest_records(1000)
        task_ids = [str(record["task_id"]) for record in records if record["state"] == "success"]
        if not task_ids:
            self._post_status("当前没有已完成任务可删除")
            return
        self._event_store.delete_tasks(task_ids)
        self._refresh_status_page()
        self._post_status(f"已永久删除 {len(task_ids)} 个已完成任务")

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
            foreground="#707070",
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
            foreground="#707070",
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
        def add_preview_scale(label: str, variable: tk.IntVar, row: int) -> None:
            item = ttk.Frame(system)
            item.grid(row=row, column=0, sticky=tk.EW, pady=(3, 7))
            item.columnconfigure(0, weight=1)
            ttk.Label(item, text=label).grid(row=0, column=0, sticky=tk.W)
            ttk.Label(item, textvariable=variable, width=4, anchor=tk.E).grid(row=0, column=1, sticky=tk.E)
            ttk.Label(item, text="%", foreground="#666666").grid(row=0, column=2, sticky=tk.E)
            ttk.Scale(item, from_=0, to=100, variable=variable, orient=tk.HORIZONTAL).grid(
                row=1, column=0, columnspan=3, sticky=tk.EW, pady=(4, 0)
            )

        add_preview_scale("总体额度剩余", remaining, 0)
        add_preview_scale("短时繁忙程度", period_used, 1)
        ttk.Label(
            system,
            text=(
                "第一颗系统灯：颜色表示总体额度剩余，绿色充足、红色紧张；\n"
                "闪烁频率表示近期任务或请求的繁忙程度，数值越高闪烁越快。"
            ),
            foreground="#666666",
            justify=tk.LEFT,
        ).grid(row=2, column=0, sticky=tk.W, pady=(8, 0))
        system.columnconfigure(0, weight=1)

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
            preview_tasks = [
                TaskSlot(
                    title=f"任务 {index + 1}",
                    state=name_to_state[state_var.get()],
                    progress=0,
                )
                for index, state_var in enumerate(state_vars)
            ]
            preview_snapshot = DashboardSnapshot(
                remaining_percent=max(0, min(100, remaining.get())),
                period_used_percent=max(0, min(100, period_used.get())),
                master_brightness_percent=max(0, min(100, self._master_brightness.get())),
                tasks=preview_tasks,
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
            self._preview_active = True
            if self._worker is not None:
                self._worker.submit(preview_snapshot)
                self._post_status("已应用临时预览数据")

        def close_preview() -> None:
            # Mock 数据只存在于弹窗生命周期中；关闭后立即恢复正式数据源快照。
            self._preview_active = False
            dialog.destroy()
            self._send()

        actions = ttk.Frame(outer)
        actions.pack(fill=tk.X)
        ttk.Button(actions, text="结束预览并恢复正式数据", command=close_preview).pack(side=tk.RIGHT)
        ttk.Button(
            actions, text="应用预览数据", command=send_preview, style="Primary.TButton"
        ).pack(side=tk.RIGHT, padx=(0, 8))
        dialog.protocol("WM_DELETE_WINDOW", close_preview)
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
        dialog.configure(background="#F7F7F7")

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
            outer, width=wheel_size, height=wheel_size, background="#F7F7F7",
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
                    row.append("#F7F7F7")
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

    @staticmethod
    def _should_log_message(message: str) -> bool:
        """日志只记录用户可感知的管理操作，不记录任务刷新、心跳等后台流量。"""
        prefixes = (
            "正在扫描", "扫描完成", "扫描失败", "蓝牙已连接", "蓝牙已断开",
            "正在连接选中", "识别命令", "识别失败", "灯板配置已下发",
            "蓝牙固件升级", "蓝牙升级", "固件校验", "提交固件升级失败",
            "已绑定", "已取消绑定",
        )
        return message.startswith(prefixes)

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
                                self._worker.firmware_info if is_bound and self._worker else "--",
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
                                self._worker.firmware_info if connected and self._worker else "--",
                                "已连接" if connected else "离线",
                            ),
                        )
                continue
            if isinstance(event, tuple) and event[0] == "codex_snapshot":
                local_snapshot = event[1]
                self._period_used.set(local_snapshot.busy_percent)
                self._latest_resource_availability.update({
                    "CPU 可用程度": local_snapshot.cpu_available_percent,
                    "内存可用程度": local_snapshot.memory_available_percent,
                    "磁盘可用程度": local_snapshot.disk_available_percent,
                    "账号余量": self._remaining.get() if self._system_color_source.get() == "账号余量" else 100,
                })
                self._remaining.set(
                    self._latest_resource_availability.get(self._system_color_source.get(), 100)
                )
                self._snapshot.tasks = local_snapshot.tasks
                self._refresh_status_page()
                if not self._preview_active and self._worker is not None and self._worker.is_connected:
                    self._send()
                continue
            message = str(event)
            # 顶部只展示连接生命周期；业务发送与校准消息仅写入日志。
            if message == "蓝牙已连接":
                self._status.set("已连接")
                # 重连后立即补发最新 Codex 状态，不等待下一次数据库变化。
                self._send()
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
                        values=(
                            "已绑定灯板（已连接）", self._bound_device_id, "--",
                            self._worker.firmware_info if self._worker else "读取中", "已连接",
                        ),
                    )
                elif message == "蓝牙已断开":
                    tree.item(
                        "bound_device",
                        values=(
                            "已绑定灯板（当前离线）", self._bound_device_id, "--",
                            self._worker.firmware_info if self._worker else "--", "离线",
                        ),
                    )
                elif message.startswith("固件信息："):
                    current = tree.item("bound_device", "values")
                    if current:
                        tree.item("bound_device", values=(*current[:3], message.removeprefix("固件信息："), current[4]))
            if self._should_log_message(message):
                timestamp = datetime.now().strftime("%H:%M:%S")
                self._log.configure(state=tk.NORMAL)
                self._log.insert(tk.END, f"{timestamp}  {message}\n")
                self._log.see(tk.END)
                self._log.configure(state=tk.DISABLED)
        self.root.after(100, self._drain_events)

    def _hide_to_tray(self) -> None:
        self.root.withdraw()
        self._post_status("窗口已隐藏，后台服务继续运行")

    def _show_from_tray(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _quit_app(self) -> None:
        self._codex_session_source.stop()
        self._codex_data_source.stop()
        self._event_server.stop()
        if self._worker is not None:
            self._worker.stop()
        self._tray.stop()
        self.root.destroy()


def main() -> None:
    if len(sys.argv) >= 4 and sys.argv[1] == "--status-bridge-hook":
        raise SystemExit(report_hook(sys.argv[2], sys.argv[3]))
    if len(sys.argv) >= 3 and sys.argv[1] == "--status-bridge-codex-notify":
        raw_payload = sys.argv[3] if len(sys.argv) >= 4 else "{}"
        try:
            previous = json.loads(base64.urlsafe_b64decode(sys.argv[2]).decode("utf-8"))
            if previous:
                # Codex 每轮结束都会调用这里。旧通知程序必须完全静默启动，
                # 否则 Windows 会短暂显示控制台窗口，影响常驻上位机体验。
                creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                subprocess.Popen(
                    [*previous, raw_payload],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creation_flags,
                )
        except Exception:
            pass
        raise SystemExit(report_codex_notification(raw_payload))
    if len(sys.argv) >= 4 and sys.argv[1] == "--manage-hook":
        selected = next((item for item in hook_providers() if item.key == sys.argv[3]), None)
        if selected is None:
            raise SystemExit(2)
        (install_hook if sys.argv[2] == "install" else uninstall_hook)(selected)
        raise SystemExit(0)
    root = tk.Tk()
    WindowsDashboardApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
