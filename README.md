# Campus Customs HW5

## Known Limitations

The historical reset is unverified. Ticket 101 is simulated fulfillment, Ticket 102 is paid rent after an incomplete agent run, and Ticket 103 is price review only. No actual customer handoff or customer acceptance is established.

Known workflow bugs remain: reset can reuse an already executed approval and block a new identical payment proposal, the dashboard has no ticket-completion path, and its latest-100-events display hides older agent contributions. Use the project-root backend command below. The assignment's backend-folder `main:app` command does not work with the current package imports. The standalone offline smoke helper also fails token preflight and is not exercised by the listed unittest command.

All eleven problems have prompt entries, including Problem 6 later in the log. Four recovered prompts match saved user messages exactly. Some older entries contain punctuation changes, merged excerpts, or misplaced headings, so the whole log is not certified verbatim. The original prompt history is preserved.

## Setup From a Clean Clone

Public repository: https://github.com/grant-wei/mgt-409-hw5. Published on `main` in commit `0e422f40c3d11d3e6ca8ae6e6b6fb8bf9b71409a`. Canvas submission has not been performed as part of this work.

Use Python 3.14 and Node.js 22.12 or newer. Run from the cloned project root on Windows:

```powershell
git clone https://github.com/grant-wei/mgt-409-hw5.git
cd mgt-409-hw5
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
Copy-Item .env.example .env
```

Put your own OpenAI key in local `.env`. The process environment takes precedence, followed by project `.env`, followed by the existing assignments-level `.env` if present. Shared files are never modified or committed. Every agent uses direct OpenAI Responses with `gpt-6-luna` and reasoning effort `none`. There is no provider or model fallback. Missing credentials or model incompatibility are errors.

The course requirement is separately recorded as `gpt-6-luna` through Portkey. Grant selected direct OpenAI with his own key. This differs from the course provider requirement, and no instructor exception is established.

## Start MCP

```powershell
.venv\Scripts\python.exe mcp_server\server.py
```

This starts stdio MCP waiting for a client, not an HTTP page. `.mcp.json` provides a Windows project-relative entry for clients supporting that file. Launch the client from the project root. On other systems, change its interpreter to `.venv/bin/python`. FastAPI and the team launch their own MCP subprocesses with their active interpreter, so they do not need a separately running server. See `mcp_server/README.md` for tools and approval boundaries.

## Start Backend and Frontend

From the project root in one terminal:

```powershell
.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

In another terminal:

```powershell
cd frontend
npm ci
npm run dev
```

Open **http://localhost:5174**. The exact backend CORS origin is `http://localhost:5174`. Vite uses strict port 5174 and leaves apps on 5173 alone. API docs are at http://localhost:8000/docs. `GET /runtime-settings` shows the active limits. The labelled browser-only demo at `?demo=1` never contacts the API. Use the ordinary URL for real records.

Runs may incur API charges. Current ceilings are 12 shared provider requests, 8 agent invocations, 8 delegations, depth 4, and 12 MCP calls. Input is limited to 10,000 tokens per invocation. Each provider response is capped at 1,000 output tokens, and each invocation has a cumulative 1,000-output-token usage limit. Delegations, retries, and resumes consume the applicable budgets. See `output/harness.md` for enforcement details. A ticket run does not approve writes or resolve tickets automatically.

## Reset Before a Full Three-Ticket Run

Both databases are included. `data/campus_customs.db` is untouched. `data/campus_customs_new.db` contains final outcomes, $160 checking, and explicitly labelled simulations. Preserve evidence before starting another experiment.

Before a fresh full 101–103 run, reset from the original. With the backend running and no active run:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8000/database/reset
Invoke-RestMethod -Uri http://localhost:8000/cash/balance
Invoke-RestMethod -Uri http://localhost:8000/tickets
```

Confirm $3,400 checking and three open tickets. Reset replaces the entire working database, including added rent, purchase, and receipt ledgers. It cancels pending or blocked approvals and preserves audit history. It does not change the original. The recorded historical sequence has inconclusive reset evidence, and these instructions do not claim it started after a reset.

Alternatively, with the backend stopped, copy the original for an offline database:

```powershell
Copy-Item data\campus_customs.db data\campus_customs_new.db -Force
```

Manual copying does not cancel pending approvals. API reset also retains executed approval records, which can block identical payment proposals in a later run. Neither method repairs the approval-reuse bug described above.

## Approval and Evidence

Agents run read-only and prepare proposals. Dashboard approval rechecks exact arguments and facts, consumes approval once, and rejects duplicates or insufficient funds. Payments, purchases, simulations, and status updates need exact human authorization. Purchases are ordered, not delivered inventory. Drafts stay on the board, and no customer or vendor messages are sent.

Double-click `output/desk_tickets.html` and `output/resolved_board.html`. The board embeds three real dashboard screenshots and includes separate JPEG copies. JSON results and historical approvals are in `output/resolved_tickets.json` and `output/audit_trail.json`. Ticket 101 is simulated fulfillment, 102 is paid rent, and 103 is price review only. No actual customer handoff or acceptance is established. Cash is $3,400 - $840 - $2,400 = $160.

`output/approval_requests.sqlite3` is an excluded local queue. Historical evidence remains in the JSON outputs and audit. A fresh clone creates its own empty queue.

## Offline Checks

```powershell
.venv\Scripts\python.exe -m pytest backend -q
.venv\Scripts\python.exe -m unittest backend.offline_smoke_test backend.financial_tools_test -v
cd frontend
npm run build
```

Tests use mocked provider responses, temporary databases, and temporary audit files. They do not establish successful live behavior. No paid smoke or ticket run is part of setup. `backend.smoke` makes paid requests and requires separate authorization.

The manifest and verification results are in `output/problem11_checks.md`. Environment files, caches, dependency directories, logs, build output, and the local approval queue are excluded. Further publication changes and Canvas submission require approval.
