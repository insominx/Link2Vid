// Result card: thumbnail, metadata, live status, and per-item actions.
import { html, useMemo, useRef, useState, useEffect } from "../vendor/preact-htm.module.js";
import { call } from "./bridge.js";
import { Icon } from "./icons.js";
import { closePopover, openPopover } from "./popover.js";
import { toast, useStore, setDrawer } from "./store.js";

const FORMAT_UI = {
  "bestvideo+bestaudio/best": { label: "Video", hint: "Best quality, with sound", icon: "film" },
  bestvideo: { label: "Video only", hint: "Best picture, no audio track", icon: "video" },
  bestaudio: { label: "Audio", hint: "MP3 soundtrack", icon: "music" },
};

export function formatUi(format) {
  const known = FORMAT_UI[format.id];
  return known ? { ...known, id: format.id } : { id: format.id, label: format.label, hint: "", icon: "download" };
}

const lastIntent = new Map();

function send(card, intent, value) {
  if (["download", "transcript", "whisper"].includes(intent)) lastIntent.set(card.key, intent);
  return call("card", card.generation, card.index, intent, value === undefined ? null : value);
}

function hue(text) {
  let h = 0;
  for (const ch of text || "link2vid") h = (h * 31 + ch.charCodeAt(0)) % 360;
  return h;
}

function fileName(path) {
  return (path || "").split(/[\\/]/).pop();
}

function fileType(path) {
  const name = fileName(path);
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toUpperCase() : "file";
}

function prettyStatus(card, formats) {
  const text = card.status || "";
  const byLabel = Object.fromEntries(formats.map((f) => [f.label, formatUi(f).label]));
  let m;
  if ((m = text.match(/^Downloading \((.+)\)$/))) return `Downloading ${(byLabel[m[1]] || m[1]).toLowerCase()}`;
  if ((m = text.match(/^Retrying \((.+)\)$/))) return `Retrying with ${byLabel[m[1]] || m[1]}`;
  if ((m = text.match(/^Failed \((.+)\)$/))) return `Download failed · ${byLabel[m[1]] || m[1]}`;
  if (text === "Complete") return card.outputs && card.outputs.length ? `Saved · ${fileType(card.outputs[0])}` : "Saved";
  if (text === "Already downloaded") return "Already in your folder";
  if ((m = text.match(/^Transcript saved \((.+)\)$/))) return `Captions saved · ${m[1]}`;
  if (text.startsWith("Whisper complete")) return text.includes("CPU fallback") ? "Transcript ready · ran on CPU" : "Transcript ready";
  if (text === "Finalizing") return "Finishing up…";
  return text;
}

function Thumb({ card }) {
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(!card.thumbnail);
  return html`<div class="thumb">
    ${failed || !loaded
      ? html`<div class="thumb-fallback" style=${`--h:${hue(card.site || card.title)}`}>
          <${Icon} name=${card.direct ? "film" : "play"} />
        </div>`
      : null}
    ${!failed
      ? html`<img
          src=${card.thumbnail}
          alt=""
          loading="lazy"
          decoding="async"
          referrerpolicy="no-referrer"
          draggable="false"
          class=${loaded ? "loaded" : ""}
          onLoad=${() => setLoaded(true)}
          onError=${() => setFailed(true)}
        />`
      : null}
    ${card.duration ? html`<span class="badge-duration">${card.duration}</span>` : null}
  </div>`;
}

function FormatMenu({ cardKey }) {
  const card = useStore((s) => s.cards.get(cardKey));
  const formats = useStore((s) => s.formats);
  if (!card) return null;
  return html`<div class="menu-label">Download as</div>
    ${formats.map((f) => {
      const ui = formatUi(f);
      const active = card.format === f.id;
      return html`<button
        class=${"menu-item" + (active ? " active" : "")}
        role="menuitemradio"
        aria-checked=${active}
        onClick=${() => {
          send(card, "format", f.id);
          closePopover();
        }}
      >
        <${Icon} name=${ui.icon} />
        <span class="grow">${ui.label}<span class="sub">${ui.hint}</span></span>
        ${active ? html`<${Icon} name="check" class="tick" />` : null}
      </button>`;
    })}`;
}

function TextPanel({ cardKey }) {
  const card = useStore((s) => s.cards.get(cardKey));
  const models = useStore((s) => s.whisperModels);
  const [query, setQuery] = useState("");
  const searchRef = useRef(null);
  useEffect(() => {
    if (searchRef.current) searchRef.current.focus({ preventScroll: true });
  }, []);
  const options = useMemo(() => {
    if (!card) return [];
    const all = card.transcripts.map((label, index) => ({ label, index }));
    const q = query.trim().toLowerCase();
    if (!q) return all;
    const starts = all.filter((o) => o.label.toLowerCase().startsWith(q));
    const contains = all.filter((o) => !o.label.toLowerCase().startsWith(q) && o.label.toLowerCase().includes(q));
    return [...starts, ...contains];
  }, [card && card.transcripts, query]);
  if (!card) return null;
  const busy = !card.enabled;
  return html`<div class="panel-section">
      <h4><${Icon} name="captions" /> Captions from the site</h4>
      ${card.transcript_available
        ? html`<p>Subtitles or automatic captions the site already provides.</p>
            ${card.transcripts.length > 6
              ? html`<label class="lang-search">
                  <${Icon} name="search" />
                  <input
                    ref=${searchRef}
                    placeholder="Search languages"
                    value=${query}
                    onInput=${(e) => setQuery(e.currentTarget.value)}
                    onKeyDown=${(e) => {
                      if (e.key === "Enter" && options.length) send(card, "transcript_choice", options[0].index);
                    }}
                  />
                </label>`
              : null}
            <div class="lang-list" role="listbox">
              ${options.length
                ? options.map(
                    (o) => html`<button
                      class=${"menu-item" + (card.transcript_choice === o.index ? " active" : "")}
                      role="option"
                      aria-selected=${card.transcript_choice === o.index}
                      onClick=${() => send(card, "transcript_choice", o.index)}
                    >
                      <span class="grow">${o.label}</span>
                      ${card.transcript_choice === o.index ? html`<${Icon} name="check" class="tick" />` : null}
                    </button>`
                  )
                : html`<div class="empty-note">No matching language.</div>`}
            </div>
            <button
              class="btn btn-secondary"
              style="width:100%"
              disabled=${!card.can_transcript}
              onClick=${() => {
                send(card, "transcript");
                closePopover();
              }}
            >
              <${Icon} name="download" /> Save captions
            </button>`
        : html`<p style="margin:0">This video has no caption tracks. Try Whisper instead.</p>`}
    </div>
    <div class="panel-section">
      <h4><${Icon} name="sparkles" /> Transcribe with Whisper</h4>
      ${card.whisper_available
        ? html`<p>Runs speech-to-text on this computer. Saves .txt and .srt files.</p>
            <div class="panel-row">
              <div class="select">
                <select
                  aria-label="Whisper model"
                  value=${card.whisper_model}
                  onChange=${(e) => send(card, "whisper_model", e.currentTarget.value)}
                >
                  ${models.map((m) => html`<option value=${m}>${m}</option>`)}
                </select>
                <${Icon} name="chevronDown" />
              </div>
              <button
                class="btn btn-primary"
                disabled=${!card.can_whisper}
                onClick=${() => {
                  send(card, "whisper");
                  closePopover();
                }}
              >
                Transcribe
              </button>
            </div>`
        : html`<p style="margin:0">Whisper isn't available in this install. Captions and downloads still work.</p>`}
      ${busy ? html`<p style="margin:10px 0 0">Wait for the current action on this item to finish.</p>` : null}
    </div>`;
}

function MoreMenu({ card }) {
  return html`${card.webpage_url
      ? html`<button
            class="menu-item"
            onClick=${() => {
              call("open_url", card.webpage_url);
              closePopover();
            }}
          >
            <${Icon} name="external" /> <span class="grow">Open page in browser</span>
          </button>
          <button
            class="menu-item"
            onClick=${async () => {
              closePopover();
              const ok = await call("copy_text", card.webpage_url);
              toast(ok ? "success" : "error", ok ? "Link copied" : "Could not copy link");
            }}
          >
            <${Icon} name="link" /> <span class="grow">Copy link</span>
          </button>`
      : null}
    <button
      class="menu-item"
      onClick=${async () => {
        closePopover();
        const ok = await call("copy_text", card.title);
        toast(ok ? "success" : "error", ok ? "Title copied" : "Could not copy title");
      }}
    >
      <${Icon} name="copy" /> <span class="grow">Copy title</span>
    </button>`;
}

function Status({ card, formats }) {
  const state_ = card.state;
  const text = prettyStatus(card, formats);
  const hidden = state_ === "ready" && (!text || text === "Ready" || text === "No caption tracks");
  if (hidden) return null;
  const pct = Math.round((card.progress || 0) * 100);
  const determinate = card.mode === "determinate";
  const outputs = card.outputs || [];
  const primary = outputs.find((p) => /\.txt$/i.test(p)) || outputs[0];
  return html`<div>
    <div class="card-status" data-state=${state_}>
      ${state_ === "complete"
        ? html`<span class="status-icon"><svg class="icon check-draw" viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5" /></svg></span>`
        : state_ === "failed"
        ? html`<span class="status-icon"><${Icon} name="x" /></span>`
        : state_ === "downloading"
        ? html`<span class="spinner" style="width:13px;height:13px"></span>`
        : null}
      <span class="status-text" title=${primary || card.status}>${text}</span>
      ${state_ === "downloading" && determinate && pct > 0 ? html`<span class="pct">${pct}%</span>` : null}
      ${state_ === "complete" && primary
        ? html`<span class="status-actions">
            <button class="btn btn-ghost btn-sm" onClick=${() => call("open_output", primary, false)}>Open</button>
            <button class="btn btn-ghost btn-sm" onClick=${() => call("open_output", primary, true)}>Show in folder</button>
          </span>`
        : null}
      ${state_ === "failed"
        ? html`<span class="status-actions">
            ${lastIntent.has(card.key) && card.enabled
              ? html`<button class="btn btn-ghost btn-sm" onClick=${() => send(card, lastIntent.get(card.key))}>
                  <${Icon} name="refresh" size="13" /> Retry
                </button>`
              : null}
            <button class="btn btn-ghost btn-sm" onClick=${() => setDrawer(true)}>Details</button>
          </span>`
        : null}
    </div>
    ${state_ === "downloading"
      ? html`<div class=${"progress" + (determinate ? "" : " indeterminate")}>
          <i style=${determinate ? `--value:${pct}%` : ""}></i>
        </div>`
      : null}
  </div>`;
}

export function Card({ cardKey, position, total }) {
  const card = useStore((s) => s.cards.get(cardKey));
  const formats = useStore((s) => s.formats);
  if (!card) return null;
  const hero = total === 1;
  const selectable = total > 1;
  const current = formats.find((f) => f.id === card.format) || formats[0];
  const ui = current ? formatUi(current) : { label: "Download", icon: "download" };

  const classes = ["card"];
  if (hero) classes.push("card-hero");
  if (!selectable) classes.push("no-select");
  if (selectable && !card.selected) classes.push("unselected");

  return html`<article class=${classes.join(" ")} data-state=${card.state} style=${`--i:${position % 40}`}>
    ${selectable
      ? html`<button
          class=${"check" + (card.selected ? " on" : "")}
          role="checkbox"
          aria-checked=${card.selected}
          aria-label=${(card.selected ? "Deselect " : "Select ") + card.title}
          onClick=${() => send(card, "select", !card.selected)}
        >
          <${Icon} name="check" />
        </button>`
      : null}
    <${Thumb} card=${card} key=${card.thumbnail || "none"} />
    <div class="card-body">
      <h3 class="card-title" title=${card.title}>${card.title}</h3>
      <div class="card-meta">
        ${card.site ? html`<span class="site-pill">${card.site}</span>` : null}
        ${card.uploader ? html`<span>${card.uploader}</span>` : null}
        ${hero && card.duration ? html`<span class="dot"></span><span>${card.duration}</span>` : null}
        ${card.direct ? html`<span class="dot"></span><span>Direct stream</span>` : null}
      </div>
      <${Status} card=${card} formats=${formats} />
      ${hero ? html`<${Actions} card=${card} ui=${ui} hero=${true} />` : null}
    </div>
    ${hero ? null : html`<${Actions} card=${card} ui=${ui} hero=${false} />`}
  </article>`;
}

function Actions({ card, ui, hero }) {
  const size = hero ? " btn-lg" : "";
  return html`<div class="card-actions">
    <div class="split">
      <button class=${"btn btn-primary" + size} disabled=${!card.can_download} onClick=${() => send(card, "download")}>
        <${Icon} name="download" /> ${hero ? `Download ${ui.label.toLowerCase()}` : "Download"}
      </button>
      <button
        class=${"btn btn-primary" + size}
        aria-label="Choose format"
        data-tip=${ui.label}
        disabled=${!card.enabled}
        onClick=${(e) => openPopover(e.currentTarget, () => html`<${FormatMenu} cardKey=${card.key} />`)}
      >
        <${Icon} name=${ui.icon} />
        <${Icon} name="chevronDown" size="13" />
      </button>
    </div>
    <button
      class=${"btn btn-secondary" + size}
      onClick=${(e) =>
        openPopover(e.currentTarget, () => html`<${TextPanel} cardKey=${card.key} />`, { className: "panel" })}
    >
      <${Icon} name="captions" /> Transcript
      <${Icon} name="chevronDown" size="13" />
    </button>
    <button
      class="icon-btn"
      aria-label="More actions"
      onClick=${(e) => openPopover(e.currentTarget, () => html`<${MoreMenu} card=${card} />`)}
    >
      <${Icon} name="more" />
    </button>
  </div>`;
}

export function SkeletonList({ count = 3 }) {
  return html`<div>
    ${Array.from({ length: count }, (_, i) => html`<div class="skeleton" style=${`opacity:${1 - i * 0.22}`}>
      <div class="sk sk-thumb"></div>
      <div class="lines">
        <div class="sk" style="height:14px;width:72%"></div>
        <div class="sk" style="height:12px;width:38%"></div>
        <div class="sk" style="height:10px;width:24%;margin-top:8px"></div>
      </div>
    </div>`)}
  </div>`;
}

