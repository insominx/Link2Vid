// Inline stroke icons (24x24 grid, Lucide-style geometry).
import { html } from "../vendor/preact-htm.module.js";

const PATHS = {
  link: html`<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" /><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />`,
  download: html`<path d="M12 3v12" /><path d="m7 10 5 5 5-5" /><path d="M5 21h14" />`,
  film: html`<rect x="3" y="4" width="18" height="16" rx="2.5" /><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4" />`,
  video: html`<rect x="2.5" y="6" width="13" height="12" rx="2.5" /><path d="m15.5 10.5 5-3v9l-5-3" />`,
  music: html`<path d="M9 18V5l12-2v13" /><circle cx="6" cy="18" r="3" /><circle cx="18" cy="16" r="3" />`,
  captions: html`<rect x="3" y="5" width="18" height="14" rx="3" /><path d="M7 11h3M13 11h4M7 15h6M15 15h2" />`,
  sparkles: html`<path d="M12 3l1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9z" /><path d="M19 15l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8zM5 3l.6 1.4L7 5l-1.4.6L5 7l-.6-1.4L3 5l1.4-.6z" />`,
  folder: html`<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2.5h7.5A2.5 2.5 0 0 1 21 10v7.5a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z" />`,
  folderOpen: html`<path d="M3 17.5V7.5A2.5 2.5 0 0 1 5.5 5H9l2 2.5h6A2.5 2.5 0 0 1 19.5 10v1" /><path d="M3 17.5 5.3 12a2 2 0 0 1 1.85-1.25H20a1.2 1.2 0 0 1 1.1 1.68L19 17.6a3 3 0 0 1-2.75 1.9H5a2 2 0 0 1-2-2" />`,
  cookie: html`<path d="M12 2a10 10 0 1 0 10 10 4 4 0 0 1-5-5 4 4 0 0 1-5-5" /><path d="M8.5 8.5v.01M16 15.5v.01M12 12v.01M11 17v.01M7 14v.01" />`,
  check: html`<path d="M20 6 9 17l-5-5" />`,
  x: html`<path d="M18 6 6 18M6 6l12 12" />`,
  alert: html`<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" /><path d="M12 9v4M12 17h.01" />`,
  info: html`<circle cx="12" cy="12" r="9" /><path d="M12 16v-4M12 8h.01" />`,
  chevronDown: html`<path d="m6 9 6 6 6-6" />`,
  chevronUp: html`<path d="m18 15-6-6-6 6" />`,
  more: html`<circle cx="12" cy="12" r="1" /><circle cx="19" cy="12" r="1" /><circle cx="5" cy="12" r="1" />`,
  terminal: html`<path d="m4 17 6-6-6-6M12 19h8" />`,
  copy: html`<rect x="9" y="9" width="12" height="12" rx="2.5" /><path d="M5 15H4.5A1.5 1.5 0 0 1 3 13.5v-9A1.5 1.5 0 0 1 4.5 3h9A1.5 1.5 0 0 1 15 4.5V5" />`,
  external: html`<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />`,
  play: html`<path d="M7 4.5v15l12.5-7.5z" />`,
  stop: html`<rect x="6" y="6" width="12" height="12" rx="2" />`,
  file: html`<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5M9 13h6M9 17h4" />`,
  refresh: html`<path d="M21 12a9 9 0 1 1-2.64-6.36L21 8" /><path d="M21 3v5h-5" />`,
  clipboard: html`<rect x="8" y="2.5" width="8" height="4" rx="1.2" /><path d="M16 4.5h1.5A2.5 2.5 0 0 1 20 7v12.5a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 19.5V7a2.5 2.5 0 0 1 2.5-2.5H8" />`,
  search: html`<circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />`,
  key: html`<circle cx="7.5" cy="15.5" r="4.5" /><path d="m10.7 12.3 9.8-9.8M17 6l3 3M14.5 8.5l2 2" />`,
  eye: html`<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" />`,
  eyeOff: html`<path d="M9.9 4.24A9.1 9.1 0 0 1 12 4c6.5 0 10 8 10 8a17.6 17.6 0 0 1-2.16 3.19M6.6 6.6A17.3 17.3 0 0 0 2 12s3.5 8 10 8a9.7 9.7 0 0 0 5.4-1.6M14.12 14.12a3 3 0 1 1-4.24-4.24M2 2l20 20" />`,
  arrowRight: html`<path d="M5 12h14M13 6l6 6-6 6" />`,
  globe: html`<circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" />`,
  activity: html`<path d="M22 12h-4l-3 9L9 3l-3 9H2" />`,
  wand: html`<path d="m15 4 5 5M14 5 4 15l5 5 10-10M17 2v2M22 7h-2M20 2l-1 1" />`,
};

export function Icon({ name, class: className = "", size }) {
  const style = size ? `width:${size}px;height:${size}px` : undefined;
  return html`<svg class=${"icon " + className} viewBox="0 0 24 24" aria-hidden="true" style=${style}>
    ${PATHS[name] || PATHS.info}
  </svg>`;
}

export function BrandMark({ class: className = "brand-mark" }) {
  return html`<svg class=${className} viewBox="0 0 48 48" aria-hidden="true">
    <defs>
      <linearGradient id="l2v-g" x1="6" y1="4" x2="42" y2="44" gradientUnits="userSpaceOnUse">
        <stop offset="0" stop-color="#9d8cff" />
        <stop offset="0.55" stop-color="#7b6cff" />
        <stop offset="1" stop-color="#d16bff" />
      </linearGradient>
    </defs>
    <rect x="2" y="2" width="44" height="44" rx="13" fill="url(#l2v-g)" />
    <path
      d="M19 13.6c0-1.5 1.63-2.43 2.93-1.67l10.3 6.04a1.93 1.93 0 0 1 0 3.34l-10.3 6.04c-1.3.76-2.93-.17-2.93-1.67z"
      fill="#fff"
    />
    <path d="M15.5 34.5h17" fill="none" stroke="#fff" stroke-opacity="0.8" stroke-width="3" stroke-linecap="round" />
  </svg>`;
}
