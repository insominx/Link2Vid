# AGENTS.md

Link2Vid is a desktop app for fetching and downloading video (and transcript) content from URLs, using yt-dlp, FFmpeg, and Selenium fallbacks where needed. Read this file before making changes or proposing commits.

## Do not commit agent or workflow artifacts

This is a **public repository**. Internal agent workflow output must stay **local only** and must **never** be staged, committed, or pushed.

### Never commit

| Path / pattern | Why |
|---|---|
| `.workflow/**` | Task folders, plans, progress logs, handoffs, reviews, distillations — all local agent workflow state |
| `docs/scratch/**` | Ephemeral investigation and planning notes |
| `scratch.py` | Ad-hoc local experimentation |
| `.cursor/**` | Editor/agent session config if present locally |
| `developer.json` | Local credentials and cookie preferences (see `.gitignore`) |

If any of these appear in `git status`, **stop** and unstage them before committing. Do not use `git add -f` to bypass `.gitignore`. `docs/scratch/` is also listed in `.gitignore` — it often contains stale notes that reference removed docs.

Before every commit, verify the diff contains only intended product changes (code, tests, user-facing docs). If asked to commit, explicitly exclude the paths above.

### What *is* OK to commit

Stable, public engineering docs under `docs/` that describe product behavior or how to extend the codebase — for example `docs/architecture.md`, `docs/agent-playbook-external-integrations.md`, `docs/media-discovery-pipeline.md`, and `docs/verification-checklist.md`. These are maintained project documentation, not ephemeral `.workflow/` task output.

## Where to read project context

- [README.md](README.md) — setup, usage, `developer.json` notes
- [docs/INDEX.md](docs/INDEX.md) — doc map
- [docs/architecture.md](docs/architecture.md) — current module layout and fetch/download flow
- [docs/media-discovery-pipeline.md](docs/media-discovery-pipeline.md) — Selenium candidate collapse, filenames
- [docs/agent-playbook-external-integrations.md](docs/agent-playbook-external-integrations.md) — patterns for external-site / downloader reliability work
- [docs/cookie-js-troubleshooting.md](docs/cookie-js-troubleshooting.md) — cookie, JS runtime, yt-dlp, and ffmpeg troubleshooting
- [docs/verification-checklist.md](docs/verification-checklist.md) — manual regression checklist

Use installed Cursor/g-skills for workflow procedures (planning, review, implementation). Do not copy skill doctrine into this repo.

## Local workflow (`.workflow/`)

You may create and use `.workflow/tasks/` and `.workflow/distillations/` locally for task tracking. That directory is gitignored. When closing a task, promote durable facts into stable `docs/` files — then delete or leave the task folder local; do not commit it.

## Validation

From the repo root (prefer a venv):

```bash
python -m pytest tests/
```

Focused runs while iterating:

```bash
python -m pytest tests/test_selenium_fallback.py
python -m pytest tests/test_fetcher.py
python -m pytest tests/test_ui_progress.py
```

## Repo layout (high level)

| Area | Role |
|---|---|
| `link2vid/core/` | Fetch, extract, download, Selenium fallback, diagnostics |
| `link2vid/ui/` | CustomTkinter UI |
| `tests/` | Unit tests |
| `video_downloader.py` | Application entry point |

## Safety reminders

- Never commit secrets, cookies, or credentials.
- Prefer minimal, focused diffs; match existing naming and test style.
- Only create git commits when the user explicitly asks.
