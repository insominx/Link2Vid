# Agent playbook: external integrations reliability

Reusable patterns for features that depend on fast-moving external tools (downloaders, runtimes, codecs) and external sites.

**Doggybag-specific:** see [architecture.md](./architecture.md) and [media-discovery-pipeline.md](./media-discovery-pipeline.md) for fetch routing, Selenium collapse, and filenames.

## 1) Triage: classify before changing code

Bucket the failure first:

- **Auth/session** — consent, login, age, bot checks; cookies required
- **Runtime/scripts missing** — challenge solver scripts absent or misconfigured
- **Codec/tooling missing** — ffmpeg or similar not available
- **Extractor drift** — upstream site changed; update yt-dlp
- **Network/throttle** — 429/403, timeouts
- **Service-side gating** — the remote accepts the request but serves only a degraded subset

Do not add retries until you know the bucket; retries can mask root cause and worsen throttling.

Two rules that prevent the most expensive mistakes:

- A generic transport status does not identify a bucket on its own. The same code appears in auth, throttle, and gating failures. Require a **distinctive** signal before assigning a bucket.
- Treat any classification emitted by the system under investigation as a **hypothesis, not evidence**. If remediation derived from that label keeps failing, suspect the label before adding more remediation.

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

## 12) Isolate the layer before theorizing

Establish *where* the failure lives before designing any fix. Each step is cheap and eliminates a whole class of theories:

1. **Reproduce with the third-party tool alone**, outside the app. If the bare tool fails identically, the defect is not in your integration and no app-side change can fix it — stop editing app code.
2. **Enable the tool's verbose/debug output early.** It routinely names the mechanism, the client profile in use, and which sub-component produced the failure. Hidden diagnostics end speculation faster than any amount of reasoning.
3. **Drop to the protocol layer.** If the tool still reports an opaque remote error, issue the request directly with explicit parameters. A raw request converts a vague status into the service's actual contract.

Record which layer each observation came from. A conclusion drawn at one layer does not transfer to another without retesting.

## 13) Design controls that can actually falsify

Most wasted investigation time comes from controls that could not have failed.

- Before trusting a passing control, state the **dimension the failure depends on** (size, offset, duration, count, concurrency, auth state). A control that does not vary that dimension proves nothing.
- Beware convenience flags that truncate work (sample, preview, limit, dry-run). They change the governing dimension and can turn a genuinely failing case green.
- Any control must be known to **exceed the suspected threshold**. A control below it is indistinguishable from success.
- When failure depends on a quantity, **bisect the boundary** rather than theorizing about cause. An exact threshold usually identifies the rule outright.
- Re-run a previously passing case late in a long investigation to confirm the environment has not shifted underneath you.

## 14) Remedy ordering: cheapest operational cost first

Rank candidate fixes by the burden they impose on the user, and exhaust cheaper tiers first:

1. Configuration within the tool already shipped — alternate client profile, mode, protocol, or parameter.
2. Updating an existing dependency.
3. A one-time local artifact the user can produce.
4. New always-on infrastructure — background services, daemons, containers.

Never propose tier 4 before demonstrating tiers 1–3 fail. A long-lived service is a heavy ask for a desktop feature; if you reach for one, state explicitly what was tried and ruled out. Re-examine tier 1 whenever a user rejects an operational burden — a configuration-level fix is often still unexplored.

## 15) Costly capabilities are escalations, not defaults

- A capability that is slow, environment-fragile, or frequently unavailable must be **triggered by evidence**, never applied to every operation.
- A stored preference records **availability, not consent to always use**. It must not silently force a costly path on every future operation.
- Honor an explicit in-session choice immediately, and keep it distinguishable from a preference merely loaded from storage.
- Order fallbacks cheapest-and-most-likely-first. A ladder that ends with the option that usually works pays for every failed attempt before it.
- Classify on distinctive signals only. Matching a generic status broadly routes unrelated failures into the wrong recovery path and reports a misleading cause to the user — which then becomes the thing they chase.

## 16) Symptom → strategy map

| Symptom | Likely bucket | First strategy |
|---|---|---|
| Tool rejects URLs that look like video | Embed mistaken for direct media | Trace discovery return; playable filter at normalization |
| UI count > logical assets on page | Raw ≠ collapsed count | Collapse by stable identity; preserve first-seen order |
| Tests pass; user distrusts fix | Validation gap | Live proof on real target; record counts + one download |
| Long review, no code change | Review–implement gap | TDD + live proof in one session after proceed |
| Titles misaligned after dedupe | Index coupling | Verify collapsed order; tier filename acceptance |
| Bare third-party tool reproduces the failure | Not an integration defect | Stop editing app code; isolate at the protocol layer |
| Small requests succeed, full ones fail | Service-side gating by threshold | Bisect the boundary; retry under an alternate client profile |
| Remediation advice contradicts observed behavior | Bucket assigned from a generic status | Reclassify on a distinctive signal; suspect the classifier |
| Every operation pays a slow fallback ladder | Costly capability used as a default | Make it evidence-triggered; reorder cheapest-first |
| Fix requires a new background service | Premature infrastructure escalation | Exhaust tool-configuration tiers first |
| Controls all pass but the bug persists | Control never varied the governing dimension | Re-run controls above the suspected threshold |
