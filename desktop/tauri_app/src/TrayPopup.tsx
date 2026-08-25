import { useCallback, useEffect, useRef, useState, type CSSProperties, type MouseEvent } from "react";
import { invoke } from "@tauri-apps/api/core";
import { CircleAlert, ExternalLink, GripVertical, Lightbulb, Pin, Trash2 } from "lucide-react";
import beaconIcon from "./assets/beacon.png";
import type { ThemeId } from "./theme";

type DashboardTask = {
  task_id: string;
  title: string;
  source: string;
  state: string;
  progress: number;
  occurred_at_ms: number;
  pinned: number;
};

type Dashboard = { tasks: DashboardTask[] };

const stateMeta: Record<string, { label: string; color: string }> = {
  idle: { label: "无任务", color: "#8b95a7" },
  running: { label: "进行中", color: "#4aa8f5" },
  waiting: { label: "等待操作", color: "#f7c84b" },
  success: { label: "已完成", color: "#60caa0" },
  warning: { label: "警告", color: "#f18d6f" },
  failure: { label: "失败", color: "#e26772" },
};

function savedTheme(): ThemeId {
  const value = localStorage.getItem("beacon-theme");
  return value === "mecha" || value === "aldnoah" ? value : "default";
}

export default function TrayPopup() {
  const [tasks, setTasks] = useState<DashboardTask[]>([]);
  const [slotCount, setSlotCount] = useState(5);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [theme, setTheme] = useState<ThemeId>(savedTheme);
  const [detail, setDetail] = useState<{ task: DashboardTask; x: number; y: number } | null>(null);
  const [opening, setOpening] = useState(0);
  const [origin, setOrigin] = useState<"top" | "bottom">("top");
  const [dragged, setDragged] = useState<string | null>(null);
  const [dragPoint, setDragPoint] = useState<{ x: number; y: number } | null>(null);
  const orderRef = useRef(tasks);
  const draggedRef = useRef<string | null>(null);

  useEffect(() => {
    if (!draggedRef.current) orderRef.current = tasks;
  }, [tasks]);

  const refresh = useCallback(async () => {
    try {
      const [dashboard, settings] = await Promise.all([
        invoke<Dashboard>("get_dashboard"),
        invoke<{ led_count: number }>("bridge_action", { action: "settings", payload: {} }),
      ]);
      setTasks(dashboard.tasks);
      setSlotCount(Math.max(1, settings.led_count - 1));
      setError("");
    } catch (reason) {
      setError(String(reason));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(timer);
  }, [refresh]);
  useEffect(() => {
    const syncTheme = () => setTheme(savedTheme());
    const reveal = () => {
      syncTheme();
      setDetail(null);
      const availableTop = (window.screen as Screen & { availTop?: number }).availTop ?? 0;
      const screenMiddle = availableTop + window.screen.availHeight / 2;
      setOrigin(window.screenY + window.outerHeight / 2 > screenMiddle ? "bottom" : "top");
      setOpening(value => value + 1);
    };
    window.addEventListener("storage", syncTheme);
    window.addEventListener("beacon-tray-open", reveal);
    return () => {
      window.removeEventListener("storage", syncTheme);
      window.removeEventListener("beacon-tray-open", reveal);
    };
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    void invoke("sync_widget_context", { theme });
  }, [theme]);

  const manage = async (payload: object) => {
    if (busy) return;
    setDetail(null);
    setBusy(true);
    try {
      await invoke("bridge_action", { action: "manage-tasks", payload });
      await refresh();
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  };

  const previewMove = (targetId: string) => {
    const movingId = draggedRef.current;
    if (!movingId || movingId === targetId) return;
    const reordered = [...orderRef.current];
    const from = reordered.findIndex(task => task.task_id === movingId);
    const target = reordered.findIndex(task => task.task_id === targetId);
    if (from < 0 || target < 0) return;
    const [task] = reordered.splice(from, 1);
    reordered.splice(target, 0, task);
    orderRef.current = reordered;
    setTasks(reordered);
  };

  useEffect(() => {
    const move = (event: PointerEvent) => {
      if (draggedRef.current) setDragPoint({ x: event.clientX, y: event.clientY });
    };
    const finish = () => {
      const movingId = draggedRef.current;
      if (!movingId) return;
      draggedRef.current = null;
      setDragged(null);
      setDragPoint(null);
      document.body.classList.remove("tray-task-dragging");
      const reordered = [...orderRef.current];
      const target = reordered.findIndex(task => task.task_id === movingId);
      void manage({
        operation: "reorder",
        task_ids: reordered.map(task => task.task_id),
        dragged_id: movingId,
        target_slot: target < slotCount ? target : null,
      });
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", finish);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", finish);
      document.body.classList.remove("tray-task-dragging");
    };
  }, [slotCount]);

  const rows = Array.from({ length: Math.max(slotCount, tasks.length) }, (_, index) => ({
    slot: index < slotCount ? index + 1 : null,
    task: tasks[index],
  }));

  const showDetail = (event: MouseEvent<HTMLDivElement>, task: DashboardTask) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const width = 330;
    const height = 178;
    const x = Math.max(10, Math.min(rect.left + 52, window.innerWidth - width - 10));
    const y = rect.bottom + height + 8 <= window.innerHeight
      ? rect.bottom + 8
      : Math.max(10, rect.top - height - 8);
    setDetail({ task, x, y });
  };

  return <div className="tray-panel tray-opening" data-theme={theme} data-origin={origin} key={opening}>
    <div className="tray-open-fx" aria-hidden="true"><i/><i/><i/></div>
    <header className="tray-header">
      <div className="tray-brand"><img src={beaconIcon} alt=""/><span><strong>Beacon</strong><small>任务与灯位</small></span></div>
      <div className="tray-head-actions"><button type="button" className="tray-open-main" onClick={()=>void invoke("open_main_window")}><ExternalLink size={14}/><span>打开主窗口</span></button><span className="tray-count">{tasks.length} 个任务</span><button type="button" title="清理已完成任务" aria-label="清理已完成任务" disabled={busy} onClick={() => void manage({ operation: "delete-completed" })}><Trash2 size={14}/></button></div>
    </header>
    <div className="tray-columns"><span>灯位</span><span>任务</span><span>状态</span><span>操作</span></div>
    <div className="tray-list">
      {rows.map(({ slot, task }, index) => {
        const meta = stateMeta[task?.state ?? "idle"] ?? stateMeta.warning;
        return <div className={`tray-row ${task ? "" : "empty"} ${task?.task_id===dragged?"dragging":""}`} style={{"--tray-row-delay":`${Math.min(index,8)*35}ms`} as CSSProperties} key={task?.task_id ?? `slot-${index}`} onPointerEnter={()=>task&&previewMove(task.task_id)} onMouseEnter={event=>task&&!draggedRef.current&&showDetail(event,task)} onMouseLeave={()=>setDetail(null)}>
          <span className="tray-slot"><Lightbulb size={14}/>{slot ?? "—"}</span>
          <span className="tray-task">
            <strong>{task?.title ?? "空闲灯位"}</strong>
            <small>{task ? task.source : "暂无任务映射"}</small>
          </span>
          <span className="tray-state" style={{ color: meta.color }}><i style={{ background: meta.color }}/>{slot ? meta.label : "未上灯"}</span>
          <span className="tray-actions">
            {task && <>
              <button type="button" className="tray-drag-handle" title="拖动排序" aria-label={`拖动排序 ${task.title}`} disabled={busy} onPointerDown={event=>{event.preventDefault();setDetail(null);orderRef.current=tasks;draggedRef.current=task.task_id;setDragged(task.task_id);setDragPoint({x:event.clientX,y:event.clientY});document.body.classList.add("tray-task-dragging")}}><GripVertical size={15} pointerEvents="none"/></button>
              <button type="button" className={task.pinned ? "active" : ""} title={task.pinned ? "取消固定" : "固定灯位"} aria-label={`${task.pinned ? "取消固定" : "固定"} ${task.title}`} disabled={busy} onClick={() => void manage({ operation: "pin", task_id: task.task_id, pinned: !task.pinned })}><Pin size={13}/></button>
              <button type="button" className="danger" title="删除任务" aria-label={`删除 ${task.title}`} disabled={busy} onClick={() => void manage({ operation: "delete", task_ids: [task.task_id] })}><Trash2 size={13}/></button>
            </>}
          </span>
        </div>;
      })}
    </div>
    {dragged&&dragPoint&&<div className="tray-drag-ghost" style={{left:dragPoint.x,top:dragPoint.y}}><GripVertical size={15}/><i style={{background:(stateMeta[tasks.find(task=>task.task_id===dragged)?.state??"idle"]??stateMeta.warning).color}}/><strong>{tasks.find(task=>task.task_id===dragged)?.title}</strong></div>}
    {detail&&<div className="tray-task-detail" style={{left:detail.x,top:detail.y}}>
      <div className="tray-detail-head"><strong>{detail.task.title}</strong>{Boolean(detail.task.pinned)&&<span><Pin size={14}/>已固定</span>}</div>
      <div className="tray-detail-grid">
        <span>来源</span><b>{detail.task.source}</b>
        <span>状态</span><b style={{color:(stateMeta[detail.task.state]??stateMeta.warning).color}}>{(stateMeta[detail.task.state]??stateMeta.warning).label}</b>
        <span>更新</span><b>{new Date(detail.task.occurred_at_ms).toLocaleString()}</b>
      </div>
      <div className="tray-detail-progress"><span><i style={{width:`${Math.max(0,Math.min(100,detail.task.progress))}%`,background:(stateMeta[detail.task.state]??stateMeta.warning).color}}/></span><b>{Math.round(detail.task.progress)}%</b></div>
    </div>}
    {error && <div className="tray-error"><CircleAlert size={14}/><span>数据暂不可用</span></div>}
  </div>;
}
