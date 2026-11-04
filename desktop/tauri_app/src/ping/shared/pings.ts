// PNGs from the Feishu emoji catalog. Vite bundles them for offline use.
// https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message-reaction/emojis-introduce
export const WHEEL_SLOTS = 8;

const emojiImages = import.meta.glob<string>("../assets/feishu/*.png", {
  eager: true, import: "default", query: "?url",
});

const emojiNames: Record<string, string> = {
  Yes: "同意", OK: "收到", CrossMark: "不同意", CheckMark: "确认",
  LOL: "笑哭", DONE: "已完成", HUSKY: "狗头", THUMBSUP: "点赞",
  THANKS: "谢谢", THINKING: "思考", OneSecond: "稍等一下",
};

export const DEFAULT_EMOJI = ["Yes", "OK", "CrossMark", "CheckMark", "LOL", "DONE", "HUSKY", "THUMBSUP"];
export const emojiId = (key: string) => `feishu:${key}`;

const catalog = Object.entries(emojiImages).map(([path, icon]) => {
  const key = path.slice(path.lastIndexOf("/") + 1, -4);
  const name = emojiNames[key] ?? key;
  return { id: emojiId(key), name, icon, frequency: 660, searchKey: key };
});

export const MARKERS = [
  ...DEFAULT_EMOJI.flatMap(key => catalog.filter(marker => marker.id === emojiId(key))),
  ...catalog.filter(marker => !DEFAULT_EMOJI.includes(marker.searchKey)),
];

// Old saved slots and incoming pings still use these wire IDs.
const legacy = [
  ["attention", "请看这里", "Loudspeaker", 660],
  ["idea", "有个想法", "THINKING", 698],
  ["question", "有个问题", "WHAT", 495],
  ["wait", "稍等一下", "OneSecond", 440],
  ["help", "需要协助", "FISTBUMP", 550],
  ["review", "请帮看看", "GLANCE", 587],
  ["done", "已完成", "DONE", 780],
  ["thanks", "谢谢", "THANKS", 740],
] as const;

export const LEGACY_MARKERS = legacy.map(([id, name, key, frequency]) => ({
  id, name, frequency, icon: emojiImages[`../assets/feishu/${key}.png`],
}));
