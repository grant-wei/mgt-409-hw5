# Campus Customs Desk Design

The dashboard uses a compact shop ledger layout: a persistent ticket rail on the left, the selected ticket and team activity in the center, and approvals in a separate right-hand column. The checking balance sits above the work area so cash is visible before an action is reviewed.

The palette pairs paper-like sage with deep shop green and small copper accents. It feels like a considered campus retail workspace rather than a generic admin panel. Serif italics add a little warmth to the greeting, while compact monospaced labels make ticket numbers, states, and audit metadata easy to scan.

Each employee has a distinct avatar tint: violet for Boss, leaf green for Inventory, amber for Accounting, blue for Facilities, and rose for Customer Service. Activity rows name the contributor and show the latest recorded summary and facts from the backend audit trail. Empty roles explicitly say they have not received a task.

Status chips distinguish running, awaiting approval, blocked, failed, and resolved. A ticket only receives the resolved state when the ticket API reports `resolved`. Agent completion alone does not resolve a ticket. Approval cards show the exact tool action, amount or quoted total, account, and linked ticket details. Purchases say “ordered” and explicitly remain separate from received inventory.

On smaller screens the navigation rail collapses, and the approvals move below the selected ticket. The page keeps controls short and labels the mock mode persistently. Open the frontend with `?demo=1` to use browser-local fixtures for demo runs, approvals, blocked purchases, errors, and checking updates. In that mode the frontend does not call FastAPI, MCP, or the agent team.
