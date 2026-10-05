# Problem 11 Package Review

Current status, October 5, 2026: the public repository is published on `main` at `0e422f40c3d11d3e6ca8ae6e6b6fb8bf9b71409a`. The preparation report and manifest below describe the pre-publication snapshot. Their unpublished status and destination-availability statements are historical. Current known limitations are summarized in README.md. Canvas submission has not been performed as part of this work.

## Proposed Publication

Destination: **https://github.com/grant-wei/mgt-409-hw5**. Proposed visibility: **public**. Proposed branch: **main**. The repository root would contain only this HW5 project, not the Life repository or other homework. Authentication is verified as `grant-wei`. Authenticated lookup returned 404, and the full owned-repository list does not contain this name. The destination is available at the time of checking, but no repository was created or published. Recheck immediately before creation and stop if it exists then.

The complete file list is in `problem11_manifest.json`. `problem11_public_preview.zip` is the refreshed local review snapshot and is excluded from publication. The earlier exact snapshot is preserved as `problem11_public_preview.before-followup.zip`, also excluded. No push, repository creation, or Canvas submission was performed.

## Verification

| Check | Result |
|---|---|
| Assignment-required files | Present, including `.env.example`, `.mcp.json`, both databases, all role prompts, and `github_url.txt` |
| Original database | SQLite integrity OK and byte-identical to the official `data.zip` database |
| Working database | SQLite integrity OK and unchanged |
| Screenshots | All three embedded JPEGs loaded in Chrome at 1263 × 1289 and match the separate screenshot files |
| Cash via actual MCP | $3,400 - $840 invoice 501 - $2,400 lease 1 rent = $160 |
| Backend regression suite | Project `.venv` pytest: 26 passed, exit code 0 |
| Isolated package installation | Fresh temporary Python environment installed requirements successfully, exit code 0 |
| Isolated backend checks | 26 pytest tests and 7 unittest checks passed, both exit code 0 |
| Isolated MCP configuration | `.mcp.json` stdio client connected and listed 17 tools, read cash, payments, and statuses |
| Isolated FastAPI startup | `/tickets`, `/cash/balance`, `/events`, `/runtime-settings` returned HTTP 200 |
| Credential loading | Project-local `.env` verified using a fake offline value, shared loading retained |
| Frontend | Clean `npm ci` and `npm run build` passed, npm reported zero vulnerabilities |
| Source and JSON | Python sources compiled and package JSON files parsed |
| Secret scans | No configured local secret value or genuine credential shape detected in public candidates |
| Injection scans | No known course-injection fingerprints or honeypot artifacts detected in generated files or database text fields |
| Preserved records | Both database hashes, 366 audit events, and the complete Expected/Actual/Cash/Reflection HTML are unchanged |

The secret-shape scan matched only the explicit `.env.example` placeholder. Hidden-Unicode checks found a leading UTF-8 BOM in the existing prompt log and runtime-settings test file. These are encoding markers, not injected instructions. Historical prompt bytes were preserved. Embedded screenshot Base64, HTML tab hiding, ordinary scripts, and role instructions are expected app content. This is a reviewed scan, not a claim that all possible injected content can be ruled out.

Exact test commands:

```powershell
.venv\Scripts\python.exe -m pytest backend -q
.venv\Scripts\python.exe -m unittest backend.offline_smoke_test backend.financial_tools_test -v
```

The clean-copy checks used freshly installed dependencies in a temporary isolated package, not the source project's dependency folders. The package was not cloned from GitHub because it has not been published. Mocked test success is not evidence of successful live agent proposals.

## Repairs

Added missing `.env.example`, project-relative `.mcp.json`, and the intended `github_url.txt`. Expanded README setup, MCP/backend/frontend startup, exact localhost origin, reset commands, financial outcomes, and test instructions. Added project-local credential loading while retaining the shared key path. Expanded exclusions for caches, logs, environment files, SQLite sidecars, and local queues. Removed a broken frontend browser-test command that referenced an absent script. Pinned the already-tested OpenAI SDK.

The initial pytest run had one stale receipt test that copied the final working database while expecting pre-simulation stock of 8. It now copies the untouched original into a temporary database and verifies the unchanged original cash and ticket statuses there. The repaired full suite passed. No project shop data was reset or written.

## Prompt History and Remaining Gaps

All 11 numbered headings are present. The packaging and followup prompts were appended exactly. The four previously flagged missing prompts were recovered verbatim from saved user-message records:

- Initial hoodie simulation scenario, original timestamp `2026-10-05T15:54:53.868Z`, source line 8623.
- Durable MCP hoodie receipt workflow, original timestamp `2026-10-05T16:01:50.858Z`, source line 8662.
- Exact simulated clock advance and receipt approval, original timestamp `2026-10-05T16:26:49.380Z`, source line 8869.
- Problem 9 missing-evidence followup, original timestamp `2026-10-05T17:06:01.634Z`, source line 9033.

Source: `rollout-2026-10-03T23-24-07-01a104f0-bae3-7480-a107-f163036fd362.jsonl`. Each record has message role `user`. The exact content, timestamp, line reference, and SHA-256 are recorded in `problem11_prompt_recovery.json`. Raw conversation files were not included in the package. The recovered messages are labelled as late recovery, not contemporaneous logging. All four targeted texts are recoverable, with no reconstruction from prose or assistant summaries.

Earlier log bytes were preserved and the verified texts appended. Some other older entries still contain replaced punctuation or repeated repair excerpts. This followup verifies the four targeted recoveries, not every historical prompt. The original public preview is retained unchanged for comparison. Reflection and shop evidence were not rewritten.

Historical reset evidence remains inconclusive. The final outcome distinctions are preserved: 101 is simulated fulfillment, 102 is paid rent, and 103 is price review only. Failed agent runs remain separate from later approved actions. No customer handoff, customer acceptance, or live supplier shipment is invented.

Direct OpenAI is Grant's runtime choice. The course specifies Portkey, and no instructor exception is established. This mismatch remains recorded separately.

The initial CLI login returned HTTP 401. It was repaired using the valid existing `grant-wei` credential from Git Credential Manager, validated through GitHub before saving it to the CLI. No token was printed or included in the package. Subsequent CLI calls authenticated as `grant-wei`. Target lookup returned 404 and a complete owned-repository listing contained 22 repositories, none with this target name. No existing repository was changed. Publication still requires approval of the refreshed exact package and destination. Canvas submission is a separate unperformed action.

## Unchanged Evidence Hashes

| File | SHA-256 |
|---|---|
| `data/campus_customs.db` | `23686a90d698f7fa6f901a3f0a9774db9cbb31e13151c9e5717275f3321f360a` |
| `data/campus_customs_new.db` | `d4df3d50cff38bfffff37be0534e53d1f0731fcf0a3020752e8decbc0b2a0733` |
| `output/audit_trail.json` | `fab727b0268ea0a866ab761296360766778ed0a8f0c362e6aeb50f2a38ab1a5d` |
| `output/desk_tickets.html` | `beddf019e01f17412729c02e3e1dfa73222ce52027bb29c088693faff0d1175c` |
