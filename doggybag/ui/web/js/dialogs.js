// Modal dialogs requested by Python (one at a time, queued) and toasts.
import { html, useEffect, useRef, useState } from "../vendor/preact-htm.module.js";
import { call } from "./bridge.js";
import { BROWSER_COLORS } from "./chrome.js";
import { Icon } from "./icons.js";
import { dismissToast, shiftDialog, useStore } from "./store.js";

function answer(dialog, value) {
  shiftDialog(dialog.id);
  call("resolve_dialog", dialog.id, value === undefined ? null : value);
}

function useDialogKeys(dialog, onCancel, onConfirm) {
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
      } else if (e.key === "Enter" && onConfirm && !(e.target instanceof HTMLButtonElement)) {
        e.preventDefault();
        onConfirm();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [dialog.id, onConfirm]);
}

function useAutofocus() {
  const ref = useRef(null);
  useEffect(() => {
    const el = ref.current && ref.current.querySelector("[data-autofocus]");
    if (el) el.focus();
  }, []);
  return ref;
}

function ConfirmDialog({ dialog }) {
  const ref = useAutofocus();
  const warn = dialog.kind === "guidance" || /unavailable|missing|failed/i.test(dialog.title || "");
  useDialogKeys(dialog, () => answer(dialog, false));
  return html`<div class="dialog" role="alertdialog" aria-modal="true" aria-labelledby="dlg-title" ref=${ref}>
    <div class=${"dialog-icon" + (warn ? " warn" : "")}><${Icon} name=${warn ? "alert" : "info"} /></div>
    <h2 id="dlg-title">${dialog.title}</h2>
    <p>${dialog.message}</p>
    <div class="dialog-actions">
      <button class="btn btn-ghost" onClick=${() => answer(dialog, false)}>${dialog.cancel || "Cancel"}</button>
      <button class="btn btn-primary" data-autofocus onClick=${() => answer(dialog, true)}>${dialog.confirm || "OK"}</button>
    </div>
  </div>`;
}

function ChoiceDialog({ dialog }) {
  const ref = useAutofocus();
  useDialogKeys(dialog, () => answer(dialog, null));
  return html`<div class="dialog" role="dialog" aria-modal="true" aria-labelledby="dlg-title" ref=${ref}>
    <div class="dialog-icon warn"><${Icon} name="alert" /></div>
    <h2 id="dlg-title">${dialog.title}</h2>
    <p>${dialog.message}</p>
    <div class="choices">
      ${(dialog.options || []).map(
        (option, i) => html`<button class="choice" data-autofocus=${i === 0 ? "" : undefined} onClick=${() => answer(dialog, option.id)}>
          <span class="grow"><strong>${option.label}</strong><span>${option.detail || ""}</span></span>
          <${Icon} name="arrowRight" />
        </button>`
      )}
    </div>
    <div class="dialog-actions">
      <button class="btn btn-ghost" onClick=${() => answer(dialog, null)}>Cancel download</button>
    </div>
  </div>`;
}

function CookieBrowserDialog({ dialog }) {
  const browsers = useStore((s) => s.cookieBrowsers);
  const ref = useAutofocus();
  useDialogKeys(dialog, () => answer(dialog, null));
  return html`<div class="dialog" role="dialog" aria-modal="true" aria-labelledby="dlg-title" ref=${ref}>
    <div class="dialog-icon"><${Icon} name="cookie" /></div>
    <h2 id="dlg-title">${dialog.title}</h2>
    <p>${dialog.message}</p>
    <div class="browser-grid">
      ${browsers.map(
        (b) => html`<button
          class=${"browser-tile" + (b.id === dialog.suggested ? " suggested" : "")}
          data-autofocus=${b.id === dialog.suggested ? "" : undefined}
          onClick=${() => answer(dialog, b.id)}
        >
          <span class="browser-logo" style=${`background:${BROWSER_COLORS[b.id] || "#555"}`}>${b.label[0]}</span>
          ${b.label}
          ${b.id === dialog.suggested ? html`<span class="tag">Suggested</span>` : null}
        </button>`
      )}
    </div>
    <div class="dialog-actions">
      <button class="btn btn-ghost" onClick=${() => answer(dialog, null)}>Not now</button>
    </div>
  </div>`;
}

function CredentialsDialog({ dialog }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [reveal, setReveal] = useState(false);
  const ref = useAutofocus();
  const valid = username.trim() && password;
  const confirm = () => valid && answer(dialog, { username: username.trim(), password });
  useDialogKeys(dialog, () => answer(dialog, null), confirm);
  return html`<div class="dialog" role="dialog" aria-modal="true" aria-labelledby="dlg-title" ref=${ref}>
    <div class="dialog-icon"><${Icon} name="key" /></div>
    <h2 id="dlg-title">${dialog.title}</h2>
    <p>${dialog.message}</p>
    <div class="field">
      <label for="dlg-user">Email or username</label>
      <div class="input">
        <input id="dlg-user" data-autofocus autocomplete="username" value=${username} onInput=${(e) => setUsername(e.currentTarget.value)} />
      </div>
    </div>
    <div class="field">
      <label for="dlg-pass">Password</label>
      <div class="input">
        <input
          id="dlg-pass"
          type=${reveal ? "text" : "password"}
          autocomplete="current-password"
          value=${password}
          onInput=${(e) => setPassword(e.currentTarget.value)}
        />
        <button class="icon-btn" type="button" aria-label=${reveal ? "Hide password" : "Show password"} onClick=${() => setReveal(!reveal)}>
          <${Icon} name=${reveal ? "eyeOff" : "eye"} />
        </button>
      </div>
    </div>
    <div class="dialog-actions">
      <button class="btn btn-ghost" onClick=${() => answer(dialog, null)}>Cancel</button>
      <button class="btn btn-primary" disabled=${!valid} onClick=${confirm}>Sign in</button>
    </div>
  </div>`;
}

const KINDS = {
  confirm: ConfirmDialog,
  guidance: ConfirmDialog,
  choice: ChoiceDialog,
  cookie_browser: CookieBrowserDialog,
  credentials: CredentialsDialog,
};

export function DialogHost() {
  const dialogs = useStore((s) => s.dialogs);
  const dialog = dialogs[0];
  if (!dialog) return null;
  const Kind = KINDS[dialog.kind] || ConfirmDialog;
  return html`<div class="scrim" key=${dialog.id}>
    <${Kind} dialog=${dialog} />
  </div>`;
}

const TOAST_ICONS = { success: "check", warning: "alert", error: "alert", info: "info" };

export function ToastHost() {
  const toasts = useStore((s) => s.toasts);
  return html`<div class="toasts" role="status">
    ${toasts.map(
      (t) => html`<div class=${`toast ${t.level}${t.leaving ? " leaving" : ""}`} key=${t.id}>
        <${Icon} name=${TOAST_ICONS[t.level] || "info"} />
        <div class="grow">
          <strong>${t.title}</strong>
          ${t.message ? html`<span>${t.message}</span>` : null}
        </div>
        <button class="icon-btn" aria-label="Dismiss" onClick=${() => dismissToast(t.id)}><${Icon} name="x" size="13" /></button>
      </div>`
    )}
  </div>`;
}
