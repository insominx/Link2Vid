// Browser-only fake backend for UI work: open index.html?mock from any static
// server. Mirrors the Python bridge methods and event shapes; never shipped as
// behavior because the app always has window.pywebview.
const FORMATS = [
  { id: "bestvideo+bestaudio/best", label: "Best (A+V)" },
  { id: "bestvideo", label: "Best video" },
  { id: "bestaudio", label: "Audio (MP3)" },
];
const MODELS = ["tiny", "base", "small", "medium", "large-v3", "distil-large-v3.5"];
const TITLES = [
  "How Rust's borrow checker actually works — a visual guide",
  "Lo-fi beats to refactor legacy code to",
  "Building a synth from scratch in 20 minutes",
  "The surprising physics of a perfect espresso shot",
  "GDC 2024: Designing levels that teach without words",
  "Why every map you've seen is wrong",
  "Live coding a ray tracer in one evening (full stream)",
  "Interview: the engineer behind the Mars helicopter",
];

export function createMock() {
  let generation = 0;
  let cards = new Map();
  let entries = [];
  let rendered = 0;
  let settings = { output_path: "C:\\Users\\you\\Downloads", cookies_browser: null, cookies_path: null };
  let batch = { visible: false, running: false, enabled: true, status: "", selected: 0, total: 0 };
  let selected = new Set();
  const pendingDialogs = new Map();
  let dialogSeq = 0;

  const emit = (t, p) => setTimeout(() => window.L2V.receive([{ t, p }]), 0);
  const log = (text, level = "info") =>
    emit("log", { time: new Date().toLocaleTimeString([], { hour12: false }), level, text });
  const ask = (kind, payload) =>
    new Promise((resolve) => {
      const id = `d${++dialogSeq}`;
      pendingDialogs.set(id, resolve);
      emit("dialog", { id, kind, ...payload });
    });

  function cardState(c) {
    return {
      key: c.key,
      selected: selected.has(c.index),
      format: c.format,
      transcript_choice: c.transcript_choice,
      whisper_model: c.whisper_model,
      status: c.status,
      state: c.state,
      progress: c.progress,
      mode: c.mode,
      enabled: !c.busy,
      can_download: !c.busy,
      can_transcript: !c.busy && c.transcript_available,
      can_whisper: !c.busy,
      outputs: c.outputs,
    };
  }
  const push = (c) => emit("card", cardState(c));
  const pushBatch = () => {
    batch = { ...batch, selected: selected.size, total: entries.length };
    emit("batch", batch);
  };

  function makeEntry(i, playlist) {
    const id = ["dQw4w9WgXcQ", "M7lc1UVf-VE", "aqz-KE-bpKQ", "jNQXAC9IVRw", "9bZkp7q19f0", "kJQP7kiw5Fk"][i % 6];
    return {
      title: playlist ? TITLES[i % TITLES.length] + (i >= TITLES.length ? ` (part ${Math.floor(i / TITLES.length) + 1})` : "") : TITLES[0],
      site: i % 5 === 3 ? "Vimeo" : "YouTube",
      duration: `${(i * 7) % 54 + 2}:${String((i * 13) % 60).padStart(2, "0")}`,
      uploader: ["Fireship", "Lofi Girl", "Andrew Huang", "James Hoffmann", "GDC", "Vsauce"][i % 6],
      thumbnail: i % 7 === 5 ? null : `https://i.ytimg.com/vi/${id}/hqdefault.jpg`,
      webpage_url: `https://www.youtube.com/watch?v=${id}`,
      direct: false,
      transcripts: i % 4 === 2 ? ["No caption tracks"] : ["Auto-select", "English", "English (auto)", "French (auto)", "German (auto)", "Japanese", "Spanish"],
      transcript_available: i % 4 !== 2,
    };
  }

  function renderBatch() {
    const items = [];
    const end = Math.min(rendered + 40, entries.length);
    for (let index = rendered; index < end; index++) {
      const e = entries[index];
      const c = {
        ...e,
        key: `${generation}:${index}`,
        index,
        generation,
        format: FORMATS[0].id,
        transcript_choice: 0,
        whisper_model: "large-v3",
        whisper_available: true,
        status: "Ready",
        state: "ready",
        progress: 0,
        mode: "determinate",
        busy: false,
        outputs: [],
      };
      cards.set(c.key, c);
      items.push({ ...c, ...cardState(c) });
    }
    rendered = end;
    emit("cards_add", { generation, items, rendered, total: entries.length });
  }

  function simulate(c, label, steps, onDone) {
    c.busy = true;
    c.state = "downloading";
    c.status = label;
    c.progress = 0;
    c.mode = "determinate";
    push(c);
    let p = 0;
    const tick = setInterval(() => {
      p += 0.04 + Math.random() * 0.08;
      c.progress = Math.min(1, p);
      push(c);
      emit("progress", { value: c.progress });
      if (p >= 1) {
        clearInterval(tick);
        c.busy = false;
        onDone();
        push(c);
        emit("progress", { value: 0 });
      }
    }, steps);
  }

  const api = {
    ready() {
      setTimeout(() => {
        log("Link2Vid mock backend ready.");
        log("yt-dlp version: 2026.10.01");
        log("[download] WARNING: example warning line", "warn");
      }, 300);
      return {
        url: "",
        formats: FORMATS,
        whisper_models: MODELS,
        cookie_browsers: [
          { id: "brave", label: "Brave" },
          { id: "chrome", label: "Chrome" },
          { id: "edge", label: "Edge" },
          { id: "firefox", label: "Firefox" },
        ],
        settings,
        batch,
        fetching: false,
        health: { yt_dlp: "2026.10.01", ffmpeg: true, js_runtimes: ["node"], whisper_models: MODELS.length },
        log: [],
        debug_log_path: "C:\\Users\\you\\Downloads\\link2vid-debug.log",
      };
    },
    set_theme() {},
    set_url() {},
    async fetch(url) {
      emit("fetching", { busy: true, can_fetch: false });
      emit("results_reset", {});
      batch = { ...batch, visible: false };
      emit("batch", batch);
      log(`Fetching ${url}`);
      setTimeout(() => log("[youtube] Extracting URL"), 300);
      setTimeout(() => log("[youtube] Downloading webpage"), 700);
      setTimeout(() => log("[youtube] Downloading player API JSON"), 1100);
      await new Promise((r) => setTimeout(r, 1600));
      if (/private|login/.test(url)) {
        const browser = await ask("cookie_browser", {
          title: "This site wants you signed in",
          message: "Link2Vid can borrow your login cookies from a browser. Pick the one you use for this site; Link2Vid remembers it. Close that browser first if it is running.",
          suggested: "chrome",
        });
        if (browser) {
          settings = { ...settings, cookies_browser: browser };
          emit("settings", settings);
        }
      }
      if (/nothing|404/.test(url)) {
        log("[Fetch] DownloadError: ERROR: Unsupported URL", "error");
        emit("results_state", { text: "No videos found or unsupported site." });
        emit("fetching", { busy: false, can_fetch: true });
        return;
      }
      const playlist = /list|channel|playlist/.test(url);
      generation += 1;
      cards = new Map();
      rendered = 0;
      entries = Array.from({ length: playlist ? 64 : 1 }, (_, i) => makeEntry(i, playlist));
      selected = new Set(entries.map((_, i) => i));
      emit("results", { generation, total: entries.length, collection: playlist ? "Tools & craft — a 64-video playlist" : null });
      batch = { visible: playlist, running: false, enabled: true, status: "", selected: selected.size, total: entries.length };
      emit("batch", batch);
      renderBatch();
      log(`Found ${entries.length} video(s).`);
      emit("fetching", { busy: false, can_fetch: true });
    },
    load_more() {
      setTimeout(renderBatch, 250);
    },
    async card(gen, index, intent, value) {
      const c = cards.get(`${gen}:${index}`);
      if (!c) return;
      if (intent === "select") {
        value ? selected.add(index) : selected.delete(index);
        push(c);
        pushBatch();
      } else if (intent === "format") {
        c.format = value;
        push(c);
      } else if (intent === "transcript_choice") {
        c.transcript_choice = value;
        push(c);
      } else if (intent === "whisper_model") {
        c.whisper_model = value;
        push(c);
      } else if (intent === "download") {
        const label = FORMATS.find((f) => f.id === c.format).label;
        if (index === 2) {
          const ok = await ask("confirm", {
            title: "Format unavailable",
            message: "The selected format is not available for this video.\nRetry with Best (single file)?",
            confirm: "Continue",
            cancel: "Not now",
          });
          if (!ok) return;
        }
        simulate(c, `Downloading (${label})`, 160, () => {
          if (index === 3) {
            c.state = "failed";
            c.status = `Failed (${label})`;
            log(`[Download] DownloadError: HTTP Error 403: Forbidden (Reason: forbidden)`, "error");
          } else {
            c.state = "complete";
            c.status = "Complete";
            c.outputs = [`C:\\Users\\you\\Downloads\\${c.title}.${c.format === "bestaudio" ? "mp3" : "mp4"}`];
          }
        });
      } else if (intent === "transcript") {
        simulate(c, "Downloading transcript", 60, () => {
          c.state = "complete";
          c.status = "Transcript saved (subtitles)";
          c.outputs = [`C:\\Users\\you\\Downloads\\${c.title}.transcript.en.vtt`];
        });
      } else if (intent === "whisper") {
        c.busy = true;
        c.state = "downloading";
        c.mode = "indeterminate";
        const phases = ["Preparing audio", "Loading model", "Transcribing", "Writing transcript"];
        for (const phase of phases) {
          c.status = phase;
          push(c);
          await new Promise((r) => setTimeout(r, 1100));
        }
        c.busy = false;
        c.mode = "determinate";
        c.state = "complete";
        c.status = "Whisper complete: a.txt, a.srt";
        c.outputs = [`C:\\Users\\you\\Downloads\\${c.title}.whisper.en.txt`, `C:\\Users\\you\\Downloads\\${c.title}.whisper.en.srt`];
        push(c);
      }
    },
    select_all() {
      selected = new Set(entries.map((_, i) => i));
      for (const c of cards.values()) push(c);
      pushBatch();
    },
    select_none() {
      selected = new Set();
      for (const c of cards.values()) push(c);
      pushBatch();
    },
    async batch(kind) {
      const items = [...selected].sort((a, b) => a - b).slice(0, 4);
      batch = { ...batch, running: true, enabled: false, status: `Starting batch ${kind}…` };
      emit("batch", batch);
      for (const [n, index] of items.entries()) {
        if (!batch.running) break;
        const c = cards.get(`${generation}:${index}`);
        batch = { ...batch, status: `Batch ${n + 1}/${items.length} · ${c ? c.title : index}` };
        emit("batch", batch);
        if (c) {
          await new Promise((resolve) =>
            simulate(c, kind === "media" ? "Downloading (Best (A+V))" : "Downloading transcript", 70, () => {
              c.state = "complete";
              c.status = kind === "media" ? "Complete" : "Transcript saved (subtitles)";
              c.outputs = [`C:\\Users\\you\\Downloads\\${c.title}.mp4`];
              resolve();
            })
          );
        }
      }
      batch = { ...batch, running: false, enabled: true, status: `Batch ${kind} finished: ${items.length} complete, 0 failed, 0 skipped.` };
      emit("batch", batch);
    },
    cancel_batch() {
      batch = { ...batch, running: false, status: "Stopping after current item…" };
      emit("batch", batch);
    },
    resolve_dialog(id, value) {
      const resolve = pendingDialogs.get(id);
      pendingDialogs.delete(id);
      if (resolve) resolve(value);
    },
    clipboard_url() {
      return null;
    },
    choose_folder() {
      settings = { ...settings, output_path: "D:\\Media\\Saved videos" };
      emit("settings", settings);
    },
    set_cookies_browser(browser) {
      settings = { ...settings, cookies_browser: browser };
      emit("settings", settings);
      log(`Cookie source set to ${browser || "Auto-detect"}.`);
    },
    choose_cookies_file() {
      settings = { ...settings, cookies_path: "C:\\Users\\you\\cookies.txt" };
      emit("settings", settings);
    },
    clear_cookies_file() {
      settings = { ...settings, cookies_path: null };
      emit("settings", settings);
    },
    copy_diagnostics() {
      emit("toast", { level: "success", title: "Diagnostics copied", message: "Paste them into a bug report or chat." });
    },
    open_folder() {},
    open_log() {},
    open_output() {},
    open_url(url) {
      window.open(url, "_blank");
    },
    copy_text(text) {
      return navigator.clipboard ? navigator.clipboard.writeText(text).then(() => true, () => false) : false;
    },
  };
  return api;
}
