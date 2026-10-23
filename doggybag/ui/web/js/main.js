import { html, render, useEffect, useRef } from "../vendor/preact-htm.module.js";
import { call, connect } from "./bridge.js";
import { Card, SkeletonList } from "./card.js";
import { Drawer, Header, ResultsHead, StatusBar, checkClipboard, looksLikeUrl, submitUrl } from "./chrome.js";
import { DialogHost, ToastHost } from "./dialogs.js";
import { Icon } from "./icons.js";
import { PopoverHost } from "./popover.js";
import { applyBoot, receive, setDrawer, state, useStore } from "./store.js";

// Python flushes event batches here.
window.DBG = { receive };

function TopProgress() {
  const progress = useStore((s) => s.progress);
  const running = useStore((s) => s.batch.running);
  const on = progress > 0 && progress < 1;
  return html`<div class=${"topline" + (on || running ? " on" : "")}>
    <i style=${`--value:${Math.round(progress * 100)}%`}></i>
  </div>`;
}

function LoadMore() {
  const rendered = useStore((s) => s.rendered);
  const total = useStore((s) => (s.results ? s.results.total : 0));
  const ref = useRef(null);
  const pending = useRef(false);
  useEffect(() => {
    pending.current = false;
  }, [rendered]);
  useEffect(() => {
    if (!ref.current) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting) && !pending.current) {
          pending.current = true;
          call("load_more");
        }
      },
      { rootMargin: "600px 0px" }
    );
    io.observe(ref.current);
    return () => io.disconnect();
  }, [rendered < total]);
  if (rendered >= total) return null;
  return html`<div class="load-sentinel" ref=${ref}>
    <span class="spinner" style="margin-right:8px"></span> Loading ${(total - rendered).toLocaleString()} more…
  </div>`;
}

function Results() {
  const results = useStore((s) => s.results);
  const order = useStore((s) => s.order);
  const busy = useStore((s) => s.fetching.busy);
  const line = useStore((s) => s.lastFetchLine);
  const resultsState = useStore((s) => s.resultsState);

  if (busy && !results) {
    return html`<div class="results">
      <div class="fetching-line">
        <span class="spinner" style="color:var(--accent-text)"></span>
        <span>Looking for media…</span>
        <span class="live">${line}</span>
      </div>
      <${SkeletonList} count=${3} />
    </div>`;
  }

  if (!results) {
    return html`<div class="results">
      <div class="state">
        <div class="state-icon"><${Icon} name="search" /></div>
        <h3>Nothing to download here</h3>
        <p>
          ${resultsState && resultsState !== "Fetching..."
            ? resultsState
            : "Doggybag couldn't find playable media at that link."}${" "}
          The Activity log has the details.
        </p>
        <div class="row">
          <button class="btn btn-secondary" onClick=${() => setDrawer(true)}><${Icon} name="terminal" /> Show activity</button>
          <button class="btn btn-ghost" onClick=${() => call("copy_diagnostics")}><${Icon} name="copy" /> Copy diagnostics</button>
        </div>
      </div>
    </div>`;
  }

  return html`<div class="results">
    <${ResultsHead} />
    <div class="list">
      ${order.map((key, i) => html`<${Card} key=${key} cardKey=${key} position=${i} total=${results.total} />`)}
    </div>
    <${LoadMore} />
  </div>`;
}

function App() {
  const booted = useStore((s) => s.booted);
  const hasResults = useStore((s) => !!s.results);
  const busy = useStore((s) => s.fetching.busy);
  const hasFetched = useStore((s) => !!s.hasFetched);
  const mode = hasResults || busy || hasFetched ? "results" : "home";
  return html`<div class="shell" data-mode=${mode} style=${booted ? "" : "opacity:0"}>
    <${TopProgress} />
    <${Header} mode=${mode} />
    <main class="main">${mode === "results" ? html`<${Results} />` : null}</main>
    <${Drawer} />
    <${StatusBar} />
    <${PopoverHost} />
    <${DialogHost} />
    <${ToastHost} />
  </div>`;
}

function isTextField(el) {
  return el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
}

function installGlobalHandlers() {
  document.addEventListener("paste", (e) => {
    if (isTextField(e.target)) return;
    const text = (e.clipboardData && e.clipboardData.getData("text")) || "";
    if (looksLikeUrl(text)) {
      e.preventDefault();
      submitUrl(text);
    }
  });

  document.addEventListener("keydown", (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && (e.key === "l" || e.key === "k")) {
      e.preventDefault();
      window.dispatchEvent(new Event("dbg:focus-omnibox"));
    } else if (mod && (e.key === "j" || e.key === "`")) {
      e.preventDefault();
      setDrawer(!state.drawerOpen);
    } else if (e.key === "Escape" && state.drawerOpen && !state.dialogs.length) {
      setDrawer(false);
    }
  });

  // Block the browser's own file-drop navigation; the omnibox handles link drops.
  document.addEventListener("dragover", (e) => e.preventDefault());
  document.addEventListener("drop", (e) => {
    if (!e.defaultPrevented) {
      e.preventDefault();
      const text = e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain");
      const first = (text || "").split(/\r?\n/).find((l) => l && !l.startsWith("#"));
      if (first && looksLikeUrl(first)) submitUrl(first);
    }
  });

  window.addEventListener("focus", () => setTimeout(checkClipboard, 120));

  const media = matchMedia("(prefers-color-scheme: light)");
  const applyTheme = () => {
    const dark = !media.matches;
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    call("set_theme", dark);
  };
  media.addEventListener("change", applyTheme);
  applyTheme();
}

async function start() {
  render(html`<${App} />`, document.getElementById("app"));
  await connect();
  installGlobalHandlers();
  const boot = await call("ready");
  if (boot) applyBoot(boot);
}

start();
