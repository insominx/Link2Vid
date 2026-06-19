# Documentation index

Stable docs for Link2Vid. Ephemeral agent notes belong in `docs/scratch/` (local only — do not commit).

## Start here

| Doc | Audience | Purpose |
|---|---|---|
| [../README.md](../README.md) | Users | Setup, usage, `developer.json` |
| [../AGENTS.md](../AGENTS.md) | Agents | Repo rules, what not to commit, validation commands |
| [architecture.md](./architecture.md) | Engineers | Current module layout and fetch/download flow |

## Operations

| Doc | Purpose |
|---|---|
| [cookie-js-troubleshooting.md](./cookie-js-troubleshooting.md) | Cookie, JS runtime, yt-dlp, and ffmpeg troubleshooting |
| [verification-checklist.md](./verification-checklist.md) | Manual regression checklist |
| [windows-packaging.md](./windows-packaging.md) | Build portable Windows exe (PyInstaller onedir) |
| [media-discovery-pipeline.md](./media-discovery-pipeline.md) | Selenium candidate collapse, multi-item pages, filenames |

## Agent playbooks

| Doc | Purpose |
|---|---|
| [agent-playbook-external-integrations.md](./agent-playbook-external-integrations.md) | Patterns for external-site and downloader reliability work |

Link2Vid-specific behavior belongs in `architecture.md` and `media-discovery-pipeline.md`; the playbook covers reusable strategies.
