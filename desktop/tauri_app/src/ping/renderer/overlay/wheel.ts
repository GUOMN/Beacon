import { WHEEL, pickSlice } from '../../shared/geometry';
import { WHEEL_SLOTS } from '../../shared/pings';

const NS = 'http://www.w3.org/2000/svg';

function el<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number>, parent: Element): SVGElementTagNameMap[K] {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v));
  parent.append(e);
  return e;
}

const polar = (r: number, deg: number): [number, number] => [r * Math.cos((deg * Math.PI) / 180), r * Math.sin((deg * Math.PI) / 180)];

function sector(r0: number, r1: number, a0: number, a1: number): string {
  const [x0, y0] = polar(r0, a0);
  const [x1, y1] = polar(r1, a0);
  const [x2, y2] = polar(r1, a1);
  const [x3, y3] = polar(r0, a1);
  return `M${x0},${y0} L${x1},${y1} A${r1},${r1} 0 0 1 ${x2},${y2} L${x3},${y3} A${r0},${r0} 0 0 0 ${x0},${y0}Z`;
}

/** One slice: `key` is what release() hands back, `icon` the image URL drawn in the slice, `label` shown in the centre on hover. */
export interface WheelItem {
  key: string;
  icon: string;
  label?: string;
}

/** Longer labels are cut so they stay inside the wheel's centre. */
const CENTER_LABEL_MAX = 14;
const fitLabel = (text: string): string => {
  const chars = [...text];
  return chars.length > CENTER_LABEL_MAX ? `${chars.slice(0, CENTER_LABEL_MAX - 1).join('')}…` : text;
};

/**
 * The League-style wheel (see prototype/wheel-demo.html), for pings or emotes: the slices are whatever items it is given.
 * Each wheel draws into its own svg, whose id keeps its gradient ids apart from the other wheel's.
 */
export class Wheel {
  private readonly wedges: SVGPathElement[] = [];
  private readonly icons: SVGImageElement[] = [];
  private hub: SVGImageElement | null = null;
  private centerTxt: SVGTextElement | null = null;
  /** Replaces the constructor's centre text while set. */
  private idleLabel: string | null = null;
  private dimmed = new Set<string>();
  private items: readonly WheelItem[] = [];
  private hot = -1;
  private ox = 0;
  private oy = 0;
  private opened = false;
  private scale = 1;

  constructor(
    private readonly svg: SVGSVGElement,
    private readonly centerLabel: string,
    private readonly centerIcon: string,
    private readonly onHover: (slice: number) => void,
  ) {
    this.build();
  }

  /** Changes what each slice holds, top (N) then clockwise. */
  setItems(items: readonly WheelItem[]): void {
    this.items = items;
    this.icons.forEach((icon, i) => {
      const item = items[i];
      if (item) icon.setAttribute('href', item.icon);
      else icon.removeAttribute('href');
    });
    this.setDimmed([...this.dimmed]);
    this.showCenterText();
  }

  /** The centre's text while no labelled slice is hovered; null puts the wheel's own back. */
  setCenterLabel(text: string | null): void {
    this.idleLabel = text;
    this.showCenterText();
  }

  /** Greys out the slices holding these keys (an ask wheel's switched-off intents). */
  setDimmed(keys: string[]): void {
    this.dimmed = new Set(keys);
    this.icons.forEach((icon, i) => {
      const item = this.items[i];
      if (item && this.dimmed.has(item.key)) icon.classList.add('dim');
      else icon.classList.remove('dim');
    });
  }

  /** Changes the icon in the middle of the wheel. */
  setCenterIcon(url: string): void {
    this.hub?.setAttribute('href', url);
  }

  /** Scales the SVG and its dead zone together, keeping the press point at the centre. */
  setSize(sizePx: number): void {
    this.scale = sizePx / WHEEL.size;
    this.svg.style.width = `${sizePx}px`;
    this.svg.style.height = `${sizePx}px`;
    this.svg.style.marginLeft = `${-sizePx / 2}px`;
    this.svg.style.marginTop = `${-sizePx / 2}px`;
  }

  get isOpen(): boolean {
    return this.opened;
  }

  open(x: number, y: number): void {
    this.ox = x;
    this.oy = y;
    this.svg.style.left = `${x}px`;
    this.svg.style.top = `${y}px`;
    this.setHot(-1);
    this.opened = true;
    this.svg.classList.add('open');
  }

  move(x: number, y: number): void {
    if (this.opened) this.setHot(pickSlice(x - this.ox, y - this.oy, WHEEL.innerR * this.scale));
  }

  /** Closes the wheel. Returns the chosen item's key at the wheel centre, or null when released in the dead zone. */
  release(x: number, y: number): { key: string; x: number; y: number } | null {
    if (!this.opened) return null;
    const slice = pickSlice(x - this.ox, y - this.oy, WHEEL.innerR * this.scale);
    this.close();
    const item = slice >= 0 ? this.items[slice] : undefined;
    return item ? { key: item.key, x: this.ox, y: this.oy } : null;
  }

  cancel(): void {
    if (this.opened) this.close();
  }

  private close(): void {
    this.opened = false;
    this.svg.classList.remove('open');
    this.setHot(-1);
  }

  private setHot(i: number): void {
    if (i === this.hot) return;
    if (this.hot >= 0) {
      this.wedges[this.hot].setAttribute('fill-opacity', '0');
      this.icons[this.hot].classList.remove('hot');
    }
    this.hot = i;
    if (i >= 0) {
      this.wedges[i].setAttribute('fill-opacity', '0.55');
      this.icons[i].classList.add('hot');
      if (this.opened) this.onHover(i);
    }
    this.showCenterText();
  }

  private showCenterText(): void {
    if (!this.centerTxt) return;
    const label = this.hot >= 0 ? this.items[this.hot]?.label : undefined;
    this.centerTxt.textContent = label ? fitLabel(label) : this.idleLabel ?? this.centerLabel;
  }

  private build(): void {
    const { innerR, outerR, iconR, iconSize } = WHEEL;
    const svg = this.svg;
    const id = `lp-${svg.id}`;
    el('defs', {}, svg).innerHTML = `
      <radialGradient id="${id}-ring" cx="0" cy="0" r="${outerR}" gradientUnits="userSpaceOnUse">
        <stop offset="${innerR / outerR}" stop-color="#152741"/><stop offset="1" stop-color="#263d58"/></radialGradient>
      <radialGradient id="${id}-hot" cx="0" cy="0" r="${outerR}" gradientUnits="userSpaceOnUse">
        <stop offset="${innerR / outerR}" stop-color="#416c9d"/><stop offset="1" stop-color="#5389bd"/></radialGradient>
      <radialGradient id="${id}-core" cx="0" cy="-10" r="${innerR}" gradientUnits="userSpaceOnUse">
        <stop offset="0" stop-color="#28486b"/><stop offset="1" stop-color="#102035"/></radialGradient>`;

    const slots = [...Array(WHEEL_SLOTS).keys()];
    el('circle', { r: (innerR + outerR) / 2, fill: 'none', stroke: `url(#${id}-ring)`, 'stroke-width': outerR - innerR, opacity: 0.92 }, svg);
    slots.forEach((i) => {
      const mid = -90 + i * 45;
      this.wedges.push(el('path', { d: sector(innerR, outerR, mid - 22.5, mid + 22.5), fill: `url(#${id}-hot)`, 'fill-opacity': 0, class: 'wedge' }, svg));
      const [x0, y0] = polar(innerR, mid - 22.5);
      const [x1, y1] = polar(outerR, mid - 22.5);
      el('line', { x1: x0, y1: y0, x2: x1, y2: y1, stroke: '#6d94b8', 'stroke-opacity': 0.45, 'stroke-width': 1.2 }, svg);
    });
    slots.forEach((i) => {
      const [ix, iy] = polar(iconR, -90 + i * 45);
      this.icons.push(el('image', { x: ix - iconSize / 2, y: iy - iconSize / 2, width: iconSize, height: iconSize, class: 'icon' }, svg));
    });

    el('circle', { r: innerR + 4, fill: 'none', stroke: '#1a1a1a', 'stroke-width': 6, opacity: 0.6 }, svg);
    el('circle', { r: innerR, fill: `url(#${id}-core)`, stroke: '#82bbec', 'stroke-width': 3, class: 'core' }, svg);
    el('circle', { r: innerR - 7, fill: 'none', stroke: '#3a5778', 'stroke-width': 1, opacity: 0.6 }, svg);
    this.hub = el('image', { href: this.centerIcon, x: -20, y: -58, width: 40, height: 40, class: 'hub' }, svg);
    this.centerTxt = el('text', { x: 0, y: 6, 'text-anchor': 'middle', class: 'centerTxt' }, svg);
    this.centerTxt.textContent = this.centerLabel;
    el('text', { x: 6, y: 50, 'text-anchor': 'middle', class: 'backTxt' }, svg).textContent = 'BACK';
    const mouse = el('g', { opacity: 0.55, transform: 'translate(-26,40)' }, svg);
    el('rect', { x: 0, y: 0, width: 11, height: 16, rx: 5.5, fill: 'none', stroke: '#6c7a7c', 'stroke-width': 1.3 }, mouse);
    el('path', { d: 'M5.5,0 V7 H11 V5.5 A5.5,5.5 0 0 0 5.5,0Z', fill: '#6c7a7c' }, mouse);
  }
}
