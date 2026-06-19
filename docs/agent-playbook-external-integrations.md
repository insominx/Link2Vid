# Agent playbook: external integrations reliability

Reusable patterns for features that depend on fast-moving external tools (downloaders, runtimes, codecs) and external sites.

**Link2Vid-specific:** see [architecture.md](./architecture.md) and [media-discovery-pipeline.md](./media-discovery-pipeline.md) for fetch routing, Selenium collapse, and filenames.

## 1) Triage: classify before changing code

Bucket the failure first:

- **Auth/session** — consent, login, age, bot checks; cookies required
- **Runtime/scripts missing** — challenge solver scripts absent or misconfigured
- **Codec/tooling missing** — ffmpeg or similar not available
- **Extractor drift** — upstream site changed; update yt-dlp
- **Network/throttle** — 429/403, timeouts

Do not add retries until you know the bucket; retries can mask root cause and worsen throttling.

## 2) Evidence-first debugging

Before risky refactors, ensure diagnostics answer:

- Sanitized inputs and execution mode
- Ordered fallback attempts
- Dependency versions and paths
- Last classified error (reason + hint)

Reproducible failures need a Copy Diagnostics artifact sufficient to reconstruct dependency state and strategy path.

## 3) CLI flags vs SDK params

When wiring a third-party tool through an SDK:

- Do not assume CLI flag names match SDK option names.
- Verify against upstream source when docs are unclear.
- Log effective configuration in diagnostics (not secrets).

## 4) Automation with fallbacks (cookies/auth)

For session automation (browser cookies, etc.):

- Try ordered sources; log each attempt and failure reason.
- Expect locked databases, keychain failures, missing profiles.
- Provide a manual fallback (`cookies.txt`).
- Never log secret material.

## 5) Dependency preflight

- Ship a setup script for Python deps and optional system tools.
- Prefer an isolated venv.
- Default to upgrade-on-setup for fast-moving deps like yt-dlp.

## 6) Validation strategy

When full GUI E2E is impractical:

- Smoke imports and startup.
- Push logic into pure helpers for unit tests.
- Mock external tool boundaries for determinism.
- Exercise the same orchestration path headlessly when possible.
- Record validation gaps explicitly.
- **Unit-test green is not task-done** for integration bugs — require live proof on a real target.

## 7) High-ROI unit tests

Prefer pure, table-driven, policy-focused tests:

- Error classification
- Fallback ordering decisions
- Candidate collapse/normalization (embed vs direct URL, duplicate variants)
- Diagnostics field assembly

## 8) Security hygiene

- Dev defaults disabled by default.
- Never ship real credentials.
- Keep session exports out of logs, docs, and commits.

## 9) Embed/page URLs vs direct media URLs

When browser automation discovers many URLs:

- Distinguish **embed/player URLs** from **directly consumable media/manifest URLs**.
- Use a broad filter for collection context and a strict filter for return values.
- Apply the strict filter at the discovery return boundary.
- Fix at the discovery owner, not with download-time patches.

## 10) Collapsing duplicate scraped candidates

When one logical item produces many URLs:

- Group by stable identity key (asset id, path segment), not full URL string.
- Preserve first-seen group order for index-aligned metadata.
- Pick one best-ranked variant per group.
- Implement as a pure, testable function with sanitized fixtures.
- Player-only sets → empty playable output (trigger follow-up/no-media paths).

## 11) Review → ship

When plan review says proceed:

- Implement in the same session unless blocked on a human decision.
- Pin answered open questions in tests instead of another review pass.
- Tier acceptance: P0 (correct count, playable inputs, minimum naming) vs P1 (rich metadata).
- Fix display-only defects at the presentation boundary without changing download semantics.

## 12) Symptom → strategy map

| Symptom | Likely bucket | First strategy |
|---|---|---|
| Tool rejects URLs that look like video | Embed mistaken for direct media | Trace discovery return; playable filter at normalization |
| UI count > logical assets on page | Raw ≠ collapsed count | Collapse by stable identity; preserve first-seen order |
| Tests pass; user distrusts fix | Validation gap | Live proof on real target; record counts + one download |
| Long review, no code change | Review–implement gap | TDD + live proof in one session after proceed |
| Titles misaligned after dedupe | Index coupling | Verify collapsed order; tier filename acceptance |
