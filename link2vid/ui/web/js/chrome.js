// App chrome: omnibox header, results header + batch controls, status bar, activity drawer.
import { html, useEffect, useLayoutEffect, useRef, useState } from "../vendor/preact-htm.module.js";
import { call } from "./bridge.js";
import { BrandMark, Icon } from "./icons.js";
import { closePopover, openPopover } from "./popover.js";
import { setDrawer, state, update, useStore } from "./store.js";

export function looksLikeUrl(text) {
  const t = (text || "").trim();
  return /^(https?:\/\/|www\.)\S+$/i.test(t) || /^[\w-]+(\.[\w-]+)+\/\S*$/i.test(t);
}

export function submitUrl(raw) {
  const url = (raw || "").trim();
  if (!url || state.fetching.busy) return;
  update((s) => {
    s.url = url;
    s.hasFetched = true;
    s.lastFetchLine = "";
    s.suggestion = null;
  });
  call("fetch", url);
}

function shortUrl(url) {
  return url.replace(/^https?:\/\/(www\.)?/i, "");
}

export function Header({ mode }) {
  const storeUrl = useStore((s) => s.url);
  const busy = useStore((s) => s.fetching.busy);
  const suggestion = useStore((s) => s.suggestion);
  const [value, setValue] = useState(storeUrl);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef(null);
  const timer = useRef(0);

  // Adopt external changes (prefill, normalization) unless the user is typing.
  useEffect(() => {
    if (document.activeElement !== inputRef.current || !value) setValue(storeUrl);
  }, [storeUrl]);

  useEffect(() => {
    const focus = () => inputRef.current && inputRef.current.focus();
    window.addEventListener("l2v:focus-omnibox", focus);
    if (mode === "home") requestAnimationFrame(focus);
    return () => window.removeEventListener("l2v:focus-omnibox", focus);
  }, []);

  const onInput = (e) => {
    const next = e.currentTarget.value;
    setValue(next);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => call("set_url", next), 150);
    if (suggestion) update((s) => (s.suggestion = null));
  };

  const submit = (e) => {
    e && e.preventDefault();
    clearTimeout(timer.current);
    submitUrl(value);
    inputRef.current && inputRef.current.blur();
  };

  const onDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    const text = e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain");
    const first = (text || "").split(/\r?\n/).find((l) => l && !l.startsWith("#"));
    if (first) {
      setValue(first.trim());
      submitUrl(first);
    }
  };

  return html`<header class="header">
    <div class="hero">
      <${BrandMark} />
      <h1>Paste a link. Keep the video.</h1>
      <p>Save video, MP3 audio, captions, or a local Whisper transcript from YouTube and over a thousand other sites.</p>
    </div>
    <div class="header-row">
      <div class="brand"><${BrandMark} /> Link2Vid</div>
      <form class="omnibox-wrap" onSubmit=${submit}>
        <div
          class=${"omnibox" + (dragging ? " dragging" : "")}
          onDragOver=${(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave=${() => setDragging(false)}
          onDrop=${onDrop}
        >
          <${Icon} name="link" />
          <input
            ref=${inputRef}
            type="text"
            spellcheck="false"
            autocomplete="off"
            placeholder="Paste a video, playlist, or channel link"
            aria-label="Video link"
            value=${value}
            onInput=${onInput}
            onKeyDown=${(e) => {
              if (e.key === "Escape" && value) {
                setValue("");
                call("set_url", "");
              }
            }}
          />
          ${value && !busy
            ? html`<button
                type="button"
                class="clear"
                aria-label="Clear"
                onClick=${() => {
                  setValue("");
                  call("set_url", "");
                  inputRef.current.focus();
                }}
              >
                <${Icon} name="x" size="15" />
              </button>`
            : null}
          <button type="submit" class="btn btn-primary btn-fetch" disabled=${busy || !value.trim()}>
            ${busy ? html`<span class="spinner"></span> Fetching` : html`Fetch <${Icon} name="arrowRight" />`}
          </button>
        </div>
        ${suggestion && suggestion !== value.trim()
          ? html`<div class="clip-suggest">
              <${Icon} name="clipboard" />
              <span class="url">Link on your clipboard: ${shortUrl(suggestion)}</span>
              <button
                type="button"
                class="btn btn-secondary btn-sm"
                onClick=${() => {
                  setValue(suggestion);
                  submitUrl(suggestion);
                }}
              >
                Fetch it
              </button>
              <button
                type="button"
                class="icon-btn"
                aria-label="Dismiss"
                onClick=${() => update((s) => (s.suggestion = null))}
              >
                <${Icon} name="x" size="14" />
              </button>
            </div>`
          : null}
      </form>
    </div>
    <div class="hints">
      <span class="hint-chip"><${Icon} name="film" /> Video</span>
      <span class="hint-chip"><${Icon} name="music" /> MP3 audio</span>
      <span class="hint-chip"><${Icon} name="captions" /> Captions</span>
      <span class="hint-chip"><${Icon} name="sparkles" /> Whisper transcripts</span>
      <span class="hint-chip"><${Icon} name="globe" /> Playlists & channels</span>
    </div>
    <div class="shortcut-hint">
      Press <span class="kbd">Ctrl</span><span class="kbd">V</span> anywhere to paste a link, or drop one here.
    </div>
  </header>`;
}

export function ResultsHead() {
  const results = useStore((s) => s.results);
  const batch = useStore((s) => s.batch);
  if (!results) return null;
  const total = results.total;
  const noun = total === 1 ? "video" : "videos";
  const all = batch.selected === batch.total && batch.total > 0;
  const none = batch.selected === 0;
  return html`<div class="results-head">
    <div class="results-title">
      <h2>${total.toLocaleString()} ${noun}</h2>
      ${results.collection ? html`<span class="collection" title=${results.collection}>${results.collection}</span>` : null}
    </div>
    ${batch.visible
      ? html`<div class="batch">
          ${batch.running
            ? html`<span class="spinner" style="color:var(--accent-text)"></span>
                <span class="batch-status" title=${batch.status}>${batch.status}</span>
                <button class="btn btn-danger btn-sm" onClick=${() => call("cancel_batch")}>
                  <${Icon} name="stop" size="13" /> Stop
                </button>`
            : html`${batch.status ? html`<span class="batch-status" title=${batch.status}>${batch.status}</span>` : null}
                <div class="batch-select">
                  <button
                    class=${"check" + (all ? " on" : none ? "" : " mixed")}
                    role="checkbox"
                    aria-checked=${all ? "true" : none ? "false" : "mixed"}
                    aria-label=${all ? "Select none" : "Select all"}
                    disabled=${!batch.enabled}
                    onClick=${() => call(all ? "select_none" : "select_all")}
                  >
                    ${all ? html`<${Icon} name="check" />` : null}
                  </button>
                  <span>${batch.selected} of ${batch.total}</span>
                </div>
                <button
                  class="btn btn-secondary btn-sm"
                  disabled=${!batch.enabled || none}
                  onClick=${() => call("batch", "transcript")}
                >
                  <${Icon} name="captions" size="14" /> Save captions
                </button>
                <button
                  class="btn btn-primary btn-sm"
                  disabled=${!batch.enabled || none}
                  onClick=${() => call("batch", "media")}
                >
                  <${Icon} name="download" size="14" /> Download ${none ? "" : batch.selected}
                </button>`}
        </div>`
      : null}
  </div>`;
}

function CookieMenu() {
  const settings = useStore((s) => s.settings);
  const browsers = useStore((s) => s.cookieBrowsers);
  const pick = (id) => {
    call("set_cookies_browser", id);
    closePopover();
  };
  const current = settings.cookies_browser;
  return html`<div class="menu-label">Sign-in cookies</div>
    <button class=${"menu-item" + (!current ? " active" : "")} onClick=${() => pick(null)}>
      <${Icon} name="cookie" />
      <span class="grow">Auto-detect<span class="sub">Only ask a browser when a site needs it</span></span>
      ${!current ? html`<${Icon} name="check" class="tick" />` : null}
    </button>
    ${browsers.map(
      (b) => html`<button class=${"menu-item" + (current === b.id ? " active" : "")} onClick=${() => pick(b.id)}>
        <span class="browser-logo" style=${`width:18px;height:18px;border-radius:6px;font-size:10px;background:${BROWSER_COLORS[b.id] || "#666"}`}>${b.label[0]}</span>
        <span class="grow">${b.label}</span>
        ${current === b.id ? html`<${Icon} name="check" class="tick" />` : null}
      </button>`
    )}
    <div class="menu-sep"></div>
    <button
      class="menu-item"
      onClick=${() => {
        closePopover();
        call("choose_cookies_file");
      }}
    >
      <${Icon} name="file" />
      <span class="grow"
        >${settings.cookies_path ? "Choose another cookies.txt…" : "Use a cookies.txt file…"}
        ${settings.cookies_path ? html`<span class="sub" title=${settings.cookies_path}>${baseName(settings.cookies_path)}</span>` : null}</span
      >
    </button>
    ${settings.cookies_path
      ? html`<button
          class="menu-item"
          onClick=${() => {
            closePopover();
            call("clear_cookies_file");
          }}
        >
          <${Icon} name="x" /> <span class="grow">Stop using cookies.txt</span>
        </button>`
      : null}`;
}

export const BROWSER_COLORS = {
  brave: "linear-gradient(135deg,#ff7a3d,#f1562b)",
  chrome: "linear-gradient(135deg,#34a853,#4285f4)",
  edge: "linear-gradient(135deg,#1fb0a6,#2a7fd8)",
  firefox: "linear-gradient(135deg,#ff9a2e,#c13ce6)",
};

function baseName(path) {
  return (path || "").split(/[\\/]/).pop();
}

export function StatusBar() {
  const settings = useStore((s) => s.settings);
  const browsers = useStore((s) => s.cookieBrowsers);
  const health = useStore((s) => s.health);
  const drawerOpen = useStore((s) => s.drawerOpen);
  const unseen = useStore((s) => s.unseenErrors);
  const browserLabel = (browsers.find((b) => b.id === settings.cookies_browser) || {}).label;
  const cookieText = settings.cookies_path ? "cookies.txt" : browserLabel || "Auto-detect";
  return html`<footer class="statusbar">
    <button class="sb-item" onClick=${() => call("choose_folder")} data-tip="Change download folder">
      <${Icon} name="folder" />
      <span class="path">${settings.output_path || "Choose a folder"}</span>
    </button>
    <button class="icon-btn" style="width:26px;height:26px" aria-label="Open download folder" data-tip="Open folder" onClick=${() => call("open_folder")}>
      <${Icon} name="folderOpen" size="14" />
    </button>
    <span class="sb-sep"></span>
    <button class="sb-item" onClick=${(e) => openPopover(e.currentTarget, () => html`<${CookieMenu} />`, { align: "start" })}>
      <${Icon} name="cookie" />
      Cookies: ${cookieText}
      <${Icon} name="chevronUp" size="12" />
    </button>
    <span class="sb-spacer"></span>
    ${!health.ffmpeg
      ? html`<span class="sb-item" data-tip="Merging video+audio and MP3 need ffmpeg" style="color:var(--warning)">
          <span class="health-dot warn"></span> ffmpeg missing
        </span>`
      : null}
    <button class="sb-item" onClick=${() => setDrawer(!drawerOpen)} aria-expanded=${drawerOpen}>
      <${Icon} name="terminal" />
      Activity
      ${unseen ? html`<span class="count-badge">${unseen > 99 ? "99+" : unseen}</span>` : null}
      <${Icon} name=${drawerOpen ? "chevronDown" : "chevronUp"} size="12" />
    </button>
  </footer>`;
}

export function Drawer() {
  const open = useStore((s) => s.drawerOpen);
  const log = useStore((s) => s.log);
  const health = useStore((s) => s.health);
  const ref = useRef(null);
  const stick = useRef(true);

  useLayoutEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [log, open]);

  const runtimes = health.js_runtimes || [];
  return html`<section class=${"drawer" + (open ? " open" : "")} aria-hidden=${!open} aria-label="Activity">
    <div class="drawer-head">
      <h3>Activity</h3>
      <div class="chips">
        ${health.yt_dlp ? html`<span class="chip">yt-dlp ${health.yt_dlp}</span>` : null}
        <span class=${"chip " + (health.ffmpeg ? "ok" : "bad")}>ffmpeg ${health.ffmpeg ? "" : "missing"}</span>
        <span class=${"chip " + (runtimes.length ? "ok" : "bad")}>JS runtime ${runtimes.length ? runtimes[0] : "missing"}</span>
        <span class=${"chip " + (health.whisper_models ? "ok" : "bad")}
          >Whisper ${health.whisper_models ? `${health.whisper_models} models` : "unavailable"}</span
        >
      </div>
      <button class="btn btn-ghost btn-sm" onClick=${() => call("copy_diagnostics")}>
        <${Icon} name="copy" size="14" /> Copy diagnostics
      </button>
      <button class="btn btn-ghost btn-sm" onClick=${() => call("open_log")}>
        <${Icon} name="file" size="14" /> Log file
      </button>
      <button class="icon-btn" aria-label="Close activity" onClick=${() => setDrawer(false)}>
        <${Icon} name="x" />
      </button>
    </div>
    <div
      class="log"
      ref=${ref}
      onScroll=${(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
      }}
    >
      ${log.map(
        (line) => html`<div class=${"log-line " + line.level}><time>${line.time}</time><span>${line.text}</span></div>`
      )}
    </div>
  </section>`;
}

export async function checkClipboard() {
  if (state.fetching.busy) return;
  const url = await call("clipboard_url");
  if (!url) return;
  const current = (state.url || "").trim();
  if (url === current || url === state.lastSuggestion) return;
  update((s) => {
    s.suggestion = url;
    s.lastSuggestion = url;
  });
}
