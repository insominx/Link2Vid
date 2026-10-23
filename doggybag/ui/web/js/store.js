// Single app store. Python pushes event batches into `receive`; components
// subscribe with `useStore(selector)` and re-render only when their slice changes.
import { useEffect, useReducer, useRef } from "../vendor/preact-htm.module.js";

const LOG_LIMIT = 1500;

export const state = {
  booted: false,
  url: "",
  formats: [],
  whisperModels: [],
  cookieBrowsers: [],
  settings: { output_path: "", cookies_browser: null, cookies_path: null },
  batch: { visible: false, running: false, enabled: false, status: "", selected: 0, total: 0 },
  fetching: { busy: false, can_fetch: false },
  health: { yt_dlp: "", ffmpeg: true, js_runtimes: [], whisper_models: 0 },
  debugLogPath: "",
  results: null, // { generation, total, collection }
  resultsState: "",
  cards: new Map(), // key -> card object (replaced on every change)
  order: [], // card keys in display order
  rendered: 0,
  progress: 0,
  log: [],
  unseenErrors: 0,
  lastFetchLine: "",
  hasFetched: false, // leaves the home hero after the first fetch
  suggestion: null, // clipboard link offered under the omnibox
  lastSuggestion: null, // never offer the same clipboard link twice
  dialogs: [],
  toasts: [],
  drawerOpen: false,
};

const listeners = new Set();
let scheduled = false;

export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function notify() {
  if (scheduled) return;
  scheduled = true;
  requestAnimationFrame(() => {
    scheduled = false;
    for (const listener of [...listeners]) listener();
  });
}

export function update(mutator) {
  mutator(state);
  notify();
}

export function useStore(selector) {
  const [, force] = useReducer((n) => n + 1, 0);
  const selectorRef = useRef(selector);
  selectorRef.current = selector;
  const valueRef = useRef();
  valueRef.current = selector(state);
  useEffect(
    () =>
      subscribe(() => {
        const next = selectorRef.current(state);
        if (!Object.is(next, valueRef.current)) {
          valueRef.current = next;
          force();
        }
      }),
    []
  );
  return valueRef.current;
}

// ── Toasts ────────────────────────────────────────────────
let toastSeq = 0;

export function toast(level, title, message = "", timeout = 4800) {
  const id = ++toastSeq;
  update((s) => {
    s.toasts = [...s.toasts.slice(-3), { id, level, title, message, leaving: false }];
  });
  if (timeout) setTimeout(() => dismissToast(id), timeout);
  return id;
}

export function dismissToast(id) {
  update((s) => {
    s.toasts = s.toasts.map((t) => (t.id === id ? { ...t, leaving: true } : t));
  });
  setTimeout(() => update((s) => (s.toasts = s.toasts.filter((t) => t.id !== id))), 220);
}

// ── Python → page events ──────────────────────────────────
function cleanLogLine(text) {
  return text.replace(/^\[(yt-dlp|dev|Fallback|ffmpeg)\]\s*/i, "").replace(/^(\[\w+\])\s*/, "");
}

function appendLog(s, entries) {
  const lines = entries.map((e) => ({ time: e.time || "", level: e.level || "info", text: e.text || "" }));
  s.log = [...s.log, ...lines].slice(-LOG_LIMIT);
  if (!s.drawerOpen) s.unseenErrors += lines.filter((l) => l.level === "error").length;
  if (s.fetching.busy && lines.length) s.lastFetchLine = cleanLogLine(lines[lines.length - 1].text);
}

const handlers = {
  boot(s, p) {
    s.booted = true;
    s.url = p.url || "";
    s.formats = p.formats || [];
    s.whisperModels = p.whisper_models || [];
    s.cookieBrowsers = p.cookie_browsers || [];
    s.settings = p.settings || s.settings;
    s.batch = p.batch || s.batch;
    s.fetching = { busy: !!p.fetching, can_fetch: !!(p.url || "").trim() };
    s.health = p.health || s.health;
    s.debugLogPath = p.debug_log_path || "";
    s.log = (p.log || []).slice(-LOG_LIMIT);
  },
  url(s, p) {
    s.url = p.value || "";
  },
  fetching(s, p) {
    const started = p.busy && !s.fetching.busy;
    s.fetching = p;
    if (started) s.lastFetchLine = "";
  },
  results_reset(s) {
    s.results = null;
    s.cards = new Map();
    s.order = [];
    s.rendered = 0;
  },
  results(s, p) {
    s.results = p;
    s.cards = new Map();
    s.order = [];
    s.rendered = 0;
  },
  results_state(s, p) {
    s.resultsState = p.text || "";
  },
  cards_add(s, p) {
    if (!s.results || p.generation !== s.results.generation) return;
    const cards = new Map(s.cards);
    const order = [...s.order];
    for (const item of p.items) {
      if (!cards.has(item.key)) order.push(item.key);
      cards.set(item.key, item);
    }
    s.cards = cards;
    s.order = order;
    s.rendered = p.rendered;
  },
  card(s, p) {
    const existing = s.cards.get(p.key);
    if (!existing) return;
    const cards = new Map(s.cards);
    cards.set(p.key, { ...existing, ...p });
    s.cards = cards;
  },
  batch(s, p) {
    s.batch = p;
  },
  settings(s, p) {
    s.settings = p;
  },
  health(s, p) {
    s.health = p;
  },
  progress(s, p) {
    s.progress = p.value || 0;
  },
  log(s, p) {
    appendLog(s, [p]);
  },
  dialog(s, p) {
    s.dialogs = [...s.dialogs, p];
  },
};

export function receive(batch) {
  const logs = [];
  const toasts = [];
  update((s) => {
    for (const event of batch) {
      if (event.t === "log") {
        logs.push(event.p);
        continue;
      }
      if (event.t === "toast") {
        toasts.push(event.p);
        continue;
      }
      if (logs.length) appendLog(s, logs.splice(0));
      const handler = handlers[event.t];
      if (handler) handler(s, event.p || {});
    }
    if (logs.length) appendLog(s, logs);
  });
  for (const t of toasts) toast(t.level, t.title, t.message);
}

export function applyBoot(payload) {
  update((s) => handlers.boot(s, payload));
}

export function shiftDialog(id) {
  update((s) => {
    s.dialogs = s.dialogs.filter((d) => d.id !== id);
  });
}

export function setDrawer(open) {
  update((s) => {
    s.drawerOpen = open;
    if (open) s.unseenErrors = 0;
  });
}
