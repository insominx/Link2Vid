// One app-level popover layer. Cards clip overflow and use containment, so
// menus render here (position: fixed) instead of inside their anchors.
import { html, useEffect, useLayoutEffect, useRef, useState } from "../vendor/preact-htm.module.js";

let current = null;
let seq = 0;
const listeners = new Set();

function changed() {
  for (const listener of [...listeners]) listener();
}

export function openPopover(anchor, render, options = {}) {
  if (current && current.anchor === anchor) {
    closePopover();
    return;
  }
  current = { id: ++seq, anchor, render, align: options.align || "end", className: options.className || "" };
  changed();
}

export function closePopover() {
  if (!current) return;
  const anchor = current.anchor;
  current = null;
  changed();
  if (anchor && document.activeElement === document.body) anchor.focus({ preventScroll: true, focusVisible: false });
}

export function PopoverHost() {
  const [, setTick] = useState(0);
  const ref = useRef(null);
  const [pos, setPos] = useState(null);

  useEffect(() => {
    const listener = () => setTick((n) => n + 1);
    listeners.add(listener);
    return () => listeners.delete(listener);
  }, []);

  const pop = current;

  useLayoutEffect(() => {
    if (!pop || !ref.current || !pop.anchor.isConnected) {
      setPos(null);
      return;
    }
    const a = pop.anchor.getBoundingClientRect();
    const m = ref.current.getBoundingClientRect();
    const margin = 8;
    let left = pop.align === "start" ? a.left : a.right - m.width;
    left = Math.max(margin, Math.min(left, innerWidth - m.width - margin));
    let top = a.bottom + 6;
    let origin = pop.align === "start" ? "top left" : "top right";
    if (top + m.height > innerHeight - margin && a.top - m.height - 6 > margin) {
      top = a.top - m.height - 6;
      origin = origin.replace("top", "bottom");
    }
    top = Math.max(margin, Math.min(top, innerHeight - m.height - margin));
    setPos({ left, top, origin });
  }, [pop && pop.id]);

  useEffect(() => {
    if (!pop) return;
    const onDown = (e) => {
      if (ref.current && ref.current.contains(e.target)) return;
      if (pop.anchor.contains(e.target)) return;
      closePopover();
    };
    const onKey = (e) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        closePopover();
      }
    };
    const onScroll = (e) => {
      if (ref.current && ref.current.contains(e.target)) return;
      closePopover();
    };
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("resize", closePopover);
    document.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("pointerdown", onDown, true);
      document.removeEventListener("keydown", onKey, true);
      window.removeEventListener("resize", closePopover);
      document.removeEventListener("scroll", onScroll, true);
    };
  }, [pop && pop.id]);

  if (!pop) return null;
  const style = pos
    ? `left:${pos.left}px;top:${pos.top}px;--origin:${pos.origin}`
    : "left:-9999px;top:-9999px;visibility:hidden";
  return html`<div class=${"menu " + pop.className} ref=${ref} style=${style} role="menu" key=${pop.id}>
    ${pop.render()}
  </div>`;
}
