export type ThemeId = "default" | "mecha" | "aldnoah";

export const THEMES = {
  default: {
    brand: "Beacon", brandSub: "信标", navTasks: "任务面板", navSettings: "配置",
    tasksTitle: "任务面板", tasksSubtitle: "实时查看任务状态与灯位映射",
    settingsTitle: "灯板配置", settingsSubtitle: "调整设备、灯效和数据源",
    online: "蓝牙已连接", offline: "蓝牙未连接", coreOnline: "Rust 原生连接正常", coreWaiting: "本地核心已连接，等待灯板",
    local: "本地运行", preview: "设置状态并预览", previewing: "正在预览",
    save: "保存并生效", saving: "正在保存…", saved: "已保存并生效", saveFailed: "保存失败",
    busy: "繁忙程度", busyDetail: "任务与 Token 综合", fiveHours: "五小时用量", sevenDays: "七天用量", localStats: "Tokens · 本机统计",
    taskSection: "任务与灯位", taskHint: "从拖动手柄调整灯位；固定后不会被自动任务顶替",
    filter: "状态", allStates: "全部状态", deleteSelected: "删除选中", clearCompleted: "清理已完成",
    operation: "操作", slot: "灯位", task: "任务", source: "来源", state: "状态", offStrip: "未上灯",
    sourceTitle: "数据源", sourceSubtitle: "任务状态采集", manageSources: "管理数据源",
    privacyTitle: "所有数据仅保存在本机", privacyText: "任务内容不会上传到外部服务。",
  },
  mecha: {
    brand: "BEACON // MS", brandSub: "MOBILE SUIT CONTROL",
    navTasks: "战术阵列", navSettings: "机体整备",
    tasksTitle: "作战单元监控", tasksSubtitle: "TACTICAL UNIT STATUS / 灯位同步矩阵",
    settingsTitle: "机体控制终端", settingsSubtitle: "SYSTEM CONFIG / 装甲、光学与链路整备",
    online: "LINK ESTABLISHED", offline: "LINK STANDBY", coreOnline: "控制核心在线 · 通讯稳定", coreWaiting: "控制核心在线 · 等待机体接入",
    local: "LOCAL CONTROL", preview: "模拟战况", previewing: "SIMULATION ACTIVE",
    save: "写入机体参数", saving: "参数写入中…", saved: "参数同步完成", saveFailed: "同步异常",
    busy: "作战负载", busyDetail: "任务 / TOKEN 复合负载", fiveHours: "短周期消耗", sevenDays: "长期资源消耗", localStats: "LOCAL TELEMETRY",
    taskSection: "战术单元与灯位矩阵", taskHint: "拖动单元调整阵位；锁定后保持当前灯位编组",
    filter: "战况", allStates: "全部信号", deleteSelected: "清除选中单元", clearCompleted: "归档完成单元",
    operation: "控制", slot: "阵位", task: "作战单元", source: "通讯链路", state: "战况", offStrip: "后备阵列",
    sourceTitle: "通讯链路", sourceSubtitle: "任务信号接入矩阵", manageSources: "整备通讯链路",
    privacyTitle: "LOCAL SECURITY MODE", privacyText: "战术数据仅驻留本机，不向外部链路传输。",
  },
  aldnoah: {
    brand: "BEACON // AZ", brandSub: "ORBITAL COMMAND LINK",
    navTasks: "作战轨道", navSettings: "骑士终端",
    tasksTitle: "战场态势阵列", tasksSubtitle: "ORBITAL COMBAT STATUS / 灯位同步矩阵",
    settingsTitle: "火星骑士终端", settingsSubtitle: "VERS COMMAND / 光学阵列与通讯链路",
    online: "UPLINK ESTABLISHED", offline: "UPLINK STANDBY", coreOnline: "轨道链路稳定 · 原生核心在线", coreWaiting: "轨道核心在线 · 等待机体接入",
    local: "MARTIAN LOCAL NODE", preview: "模拟交战", previewing: "BATTLE SIMULATION",
    save: "同步作战参数", saving: "轨道同步中…", saved: "参数已写入", saveFailed: "同步中断",
    busy: "战术负载", busyDetail: "任务 / TOKEN 战场负载", fiveHours: "短期消耗", sevenDays: "长期消耗", localStats: "ORBITAL TELEMETRY",
    taskSection: "作战序列与灯位矩阵", taskHint: "拖动单元调整阵位；锁定后保持当前灯位编组",
    filter: "战况", allStates: "全部信号", deleteSelected: "撤销选中单元", clearCompleted: "归档结束单元",
    operation: "控制", slot: "阵位", task: "作战单元", source: "轨道链路", state: "战况", offStrip: "后备序列",
    sourceTitle: "轨道通讯链路", sourceSubtitle: "任务信号接入矩阵", manageSources: "整备轨道链路",
    privacyTitle: "LOCAL ORBITAL SECURITY", privacyText: "战术数据仅驻留本机，不向外部链路传输。",
  },
} as const;

const DEFAULT_STATES: Record<string, string> = {
  "无任务": "无任务", "进行中": "进行中", "等待操作": "等待操作", "已完成": "已完成", "警告": "警告", "失败": "失败",
};
const MECHA_STATES: Record<string, string> = {
  "无任务": "待机", "进行中": "作战中", "等待操作": "待授权", "已完成": "任务完成", "警告": "异常预警", "失败": "单元失效",
};
const ALDNOAH_STATES: Record<string, string> = {
  "无任务": "待命", "进行中": "交战中", "等待操作": "等待指令", "已完成": "任务结束", "警告": "战术预警", "失败": "单元损失",
};

export function stateLabel(theme: ThemeId, state: string): string {
  return (theme === "mecha" ? MECHA_STATES : theme === "aldnoah" ? ALDNOAH_STATES : DEFAULT_STATES)[state] ?? state;
}
