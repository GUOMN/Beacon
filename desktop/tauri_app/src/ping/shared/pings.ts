export const WHEEL_SLOTS = 8;

export const MARKERS = [
  { id: "attention", name: "请看这里", color: "#75dcff", frequency: 660, art: '<circle cx="24" cy="24" r="6"/><path d="M17 10h-7v7m21-7h7v7m0 14v7h-7m-14 0h-7v-7"/>' },
  { id: "idea", name: "有个想法", color: "#ffd68a", frequency: 698, art: '<path d="M19 32v-4a10 10 0 1 1 10 0v4Zm1 5h8m-6 4h4"/>' },
  { id: "question", name: "有个问题", color: "#ffc18b", frequency: 495, art: '<path d="M37 30a7 7 0 0 1-7 7H19l-8 5v-8a7 7 0 0 1-3-6V16a7 7 0 0 1 7-7h15a7 7 0 0 1 7 7Z"/><path d="M19 18a4 4 0 1 1 6 4c-2 1-2 2-2 3m0 5h.01"/>' },
  { id: "wait", name: "稍等一下", color: "#b8caff", frequency: 440, art: '<circle cx="24" cy="24" r="14"/><path d="M24 15v10l6 4"/>' },
  { id: "help", name: "需要协助", color: "#e8b6ff", frequency: 550, art: '<circle cx="24" cy="24" r="14"/><circle cx="24" cy="24" r="6"/><path d="m14 14 6 6m8 8 6 6m0-20-6 6m-8 8-6 6"/>' },
  { id: "review", name: "请帮看看", color: "#a8dcff", frequency: 587, art: '<path d="M19 12h-6v24h20V12h-6m-8 0v-3h8v6h-8Zm0 10h8m-8 6h6"/>' },
  { id: "done", name: "已完成", color: "#9af5bd", frequency: 780, art: '<path d="m14 24 7 7 14-15"/><circle cx="24" cy="24" r="15"/>' },
  { id: "thanks", name: "谢谢", color: "#ffa9c4", frequency: 740, art: '<path d="M24 37 11 24a8 8 0 0 1 13-9 8 8 0 0 1 13 9Z"/>' },
] as const;
