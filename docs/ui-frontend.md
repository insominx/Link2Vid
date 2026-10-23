# UI frontend

How the Doggybag window is built, how it talks to Python, and how to iterate on it. For the module map and threading model, see [architecture.md](./architecture.md).

## Stack

- **Host:** [pywebview](https://pywebview.flowrl.com/) (`doggybag/ui/webview_app.py`). On Windows it uses the Edge WebView2 runtime. pywebview serves `doggybag/ui/web/` over its local HTTP server so ES modules load normally.
- **Frontend:** plain ES modules with [Preact](https://preactjs.com/) and [htm](https://github.com/developit/htm) tagged templates, from the vendored `web/vendor/preact-htm.module.js` (htm 3.1.1 standalone build, unmodified). There is no bundler or Node build step; edit a file and reload.
- **Styling:** one stylesheet (`web/styles.css`) built on CSS custom properties. Dark and light themes follow the Windows setting (`prefers-color-scheme`). The native title bar is recolored to match through DWM (`style_title_bar`).

| File | Responsibility |
|---|---|
| `web/index.html` | Shell; sets `data-theme` before first paint |
| `web/js/main.js` | `App`, results list, infinite scroll, global shortcuts / paste / drop, theme sync |
| `web/js/store.js` | Single store, `receive()` event reducer, `useStore(selector)` hook, toasts |
| `web/js/bridge.js` | `call(method, ...args)` → `window.pywebview.api`; loads `mock.js` for `?mock` |
| `web/js/chrome.js` | Omnibox header/hero, results header + batch controls, status bar, Activity drawer |
| `web/js/card.js` | Result card, format menu, Transcript panel (captions + Whisper), skeletons |
| `web/js/dialogs.js` | Modal dialogs requested by Python, toast stack |
| `web/js/popover.js` | App-level popover layer (cards clip overflow, so menus render here) |
| `web/js/icons.js` | Inline stroke icons, the brand tile, and the start-screen pup |
| `web/assets/` | `mark.png` (app tile, also the `.ico` source) and `dog.png` (full origami pup, transparent) |
| `web/js/mock.js` | Browser-only fake backend for UI work |

## Python ↔ page protocol

Python owns all state. View models in `doggybag/app/viewmodels.py` and the controller emit `(event, payload)` pairs. `WebViewPresenter` batches them and calls `window.DBG.receive([{t, p}, ...])` at most about 30 times per second. It keeps only the latest `card` per key and the latest `progress`, `batch`, `settings`, `fetching`, `url`, and `results_state`.

| Event | Payload | Meaning |
|---|---|---|
| `url` | `{value}` | Address text changed by Python (prefill, normalization) |
| `fetching` | `{busy, can_fetch}` | Fetch started/finished |
| `results_reset` | `{}` | Clear the list |
| `results` | `{generation, total, collection}` | New result set header |
| `cards_add` | `{generation, items[], rendered, total}` | A page of cards (`CardModel.describe()`) |
| `card` | `CardModel.state()` | One card's dynamic state, keyed by `"generation:index"` |
| `results_state` | `{text}` | Empty/fetch status text |
| `batch` | `{visible, running, enabled, status, selected, total}` | Batch controls |
| `settings` | `{output_path, cookies_browser, cookies_path}` | Status bar settings |
| `progress` | `{value}` | Global progress (top line) |
| `log` | `{time, level, text}` | Activity line (`level`: info/warn/error) |
| `dialog` | `{id, kind, ...}` | Modal request; answered with `resolve_dialog(id, answer)` |
| `toast` | `{level, title, message}` | Non-blocking notice |

The page starts with `ready()`, which returns `AppController.boot_state()` (formats, Whisper models, cookie browsers, settings, health, log tail). Intents go back through `Bridge` methods such as `fetch(url)`, `card(generation, index, intent, value)`, `batch(kind)`, `choose_folder()`, `set_cookies_browser(id)`, `copy_diagnostics()`, and `open_output(path, reveal)`. Card intents are `select`, `format`, `transcript_choice`, `whisper_model`, `download`, `transcript`, and `whisper`. Stale generations are ignored.

Dialog kinds: `confirm`, `guidance` (known-error help with **Copy diagnostics**), `choice` (ffmpeg fallback), `cookie_browser`, `credentials`. Dialogs queue and show one at a time.

Safety rules in `Bridge`: `open_output` only opens paths a card reported as saved output, `open_url` only opens `http(s)` URLs, and `entry_details` only exposes `http(s)` thumbnail and page URLs to the page.

## UX conventions

- **Home → results:** the hero omnibox collapses into a header bar once a fetch starts.
- **Single result:** a large hero card with labelled actions. **Several results:** compact rows plus a sticky header with selection and batch actions.
- **Cards:** a split **Download** button (the chevron picks Video / Video only / Audio MP3) and a **Transcript** panel (site captions with language search, or local Whisper with a model picker). Status text, progress, and post-completion **Open** / **Show in folder** / **Retry** live inline on the card.
- **Status bar:** download folder (click to change, folder icon to open), cookie source menu (browsers + cookies.txt), ffmpeg warning, Activity toggle with an unseen-error badge.
- **Shortcuts:** `Ctrl+V` outside inputs pastes and fetches; `Ctrl+L`/`Ctrl+K` focuses the omnibox; `Ctrl+J` toggles Activity; `Esc` closes menus, dialogs, and the drawer. Links can be dropped on the window.
- **Motion** respects `prefers-reduced-motion`.

## Iterating on the UI

Run the real app with developer tools:

```bash
python video_downloader.py --devtools
```

To work on the frontend without Python, serve the folder and open the mock:

```bash
python -m http.server 8765 --directory doggybag/ui/web
# open http://127.0.0.1:8765/index.html?mock
```

The mock (`web/js/mock.js`) mirrors the bridge methods and event shapes:

- A URL containing `list`, `playlist`, or `channel` returns 64 items.
- `private` or `login` triggers the cookie-browser dialog.
- `nothing` or `404` shows the empty state.
- Card 3 asks the format-fallback question and card 4 fails its download.

When you add a bridge method or event, update the mock with it.

Keep new colors as tokens in `:root` (dark) and `:root[data-theme="light"]`.
