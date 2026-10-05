import type { ApprovalRequest, BoardData, BoardEvent, RunResult, Ticket } from './types'

const API_ROOT = 'http://localhost:8000'

export class ApiError extends Error {
  constructor(message: string, readonly detail: unknown) { super(message); this.name = 'ApiError' }
}
export const DEMO_MODE = new URLSearchParams(window.location.search).has('demo')
const demoKey = 'campus-customs-demo-state-v1'

type DemoState = BoardData & { failNext?: boolean }

const seed: DemoState = {
  tickets: [
    { id: 101, type: 'Inventory', requester: 'Mara Ellis', subject: 'Reprint for white logo tees', sku: 'CC-TEE-WHITE', size: 'S', qty: 12, status: 'open', notes: 'Customer needs twelve small white logo tees.', invoice_id: 501, invoice_status: 'open', invoice_amount: 840, invoice_due: '2026-10-10', invoice_vendor: 'Northstar Print Co.' },
    { id: 102, type: 'Facilities', requester: 'Shop desk', subject: 'October studio rent', status: 'open', notes: 'Review the lease payment due this month.', lease_id: 1, space_name: 'Campus Customs Studio', landlord: 'Elm Street Properties', monthly_rent: 2400, rent_due: '2026-10-05' },
    { id: 103, type: 'Inventory', requester: 'Jon Bell', subject: 'Bulk hoodie order', sku: 'CC-HOOD-NAVY', size: 'M', qty: 20, status: 'open', notes: 'Customer asks about a bulk order for twenty navy hoodies.' },
  ],
  events: [],
  requests: [
    { request_id: 'demo-invoice-101', run_id: 'demo-run-101', ticket_id: 101, tool_name: 'record_invoice_payment', arguments: { invoice_id: 501, account: 'checking', amount: 840 }, reason: 'Mock proposal: pay the open invoice linked to ticket 101.', status: 'pending', approval_fields_required: [] },
    { request_id: 'demo-rent-102', run_id: 'demo-run-102', ticket_id: 102, tool_name: 'record_rent_payment', arguments: { lease_id: 1, account: 'checking', amount: 2400 }, reason: 'Mock proposal: pay the recorded rent for ticket 102.', status: 'pending', approval_fields_required: [] },
    { request_id: 'demo-purchase-103', run_id: 'demo-run-103', ticket_id: 103, tool_name: 'record_purchase_order', arguments: { ticket_id: 103, vendor_id: 2, sku: 'CC-HOOD-NAVY', size: 'M', quantity: 12, account: 'checking' }, reason: 'Mock proposal: restock the verified shortage after a human selects a supplier and supplies a quote.', status: 'pending', approval_fields_required: ['supplier selection confirmation', 'supplier quote reference', 'quoted unit price', 'exact total'] },
  ],
  balance: 3400,
  balanceDate: '2026-10-04',
}

function readDemo(): DemoState {
  try {
    const stored = localStorage.getItem(demoKey)
    return stored ? JSON.parse(stored) as DemoState : structuredClone(seed)
  } catch {
    return structuredClone(seed)
  }
}

function writeDemo(state: DemoState) { localStorage.setItem(demoKey, JSON.stringify(state)) }
let demo = DEMO_MODE ? readDemo() : null
const delay = (ms = 260) => new Promise(resolve => window.setTimeout(resolve, ms))

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (DEMO_MODE) {
    await delay()
    if (demo?.failNext && path !== '/health') {
      demo.failNext = false
      writeDemo(demo)
      throw new Error('Mock API error: the local fixture rejected this request.')
    }
    return demoRequest(path, init) as T
  }
  const response = await fetch(`${API_ROOT}${path}`, { ...init, headers: { 'Content-Type': 'application/json', ...init?.headers } })
  const body = await response.json().catch(() => ({})) as { detail?: unknown }
  if (!response.ok) {
    const detail = typeof body.detail === 'string' ? body.detail : body.detail ? JSON.stringify(body.detail) : `HTTP ${response.status}`
    throw new ApiError(detail, body.detail)
  }
  return body as T
}

function demoRequest(path: string, init?: RequestInit): unknown {
  if (!demo) throw new Error('Mock fixtures are unavailable.')
  const method = init?.method ?? 'GET'
  if (path === '/tickets') return { tickets: demo.tickets }
  if (path === '/events?limit=100') return { events: demo.events.slice(-100) }
  if (path === '/approvals/pending') return { requests: demo.requests.filter(r => ['pending', 'blocked'].includes(r.status)) }
  if (path === '/cash/balance') return { account: 'checking', balance: demo.balance, date: demo.balanceDate }
  if (method === 'POST' && path.match(/^\/tickets\/\d+\/run$/)) {
    const id = Number(path.split('/')[2])
    const runId = `mock-${id}-${Date.now()}`
    const ticket = demo.tickets.find(item => item.id === id)
    if (!ticket) throw new Error('Ticket not found.')
    const sequence = id === 102 ? ['boss', 'facilities', 'accounting'] : ['boss', 'inventory', 'accounting', 'customer_service']
    demo.events.push(...sequence.map((agent, index) => ({ event_id: `${runId}-${index}`, timestamp: new Date(Date.now() + index).toISOString(), run_id: runId, ticket_id: id, agent, step: index === 0 ? 'agent_run_completed' : 'delegation_completed', node_type: index === 0 ? 'result' : 'agent_tool_result', detail: { summary: `${agentLabel(agent)} checked ${ticket.subject} using mock ticket facts.`, facts: [ticket.subject ?? `Ticket ${id}`, ticket.status === 'resolved' ? 'Backend mock confirms resolved.' : `Backend mock confirms status ${ticket.status}.`], proposed_updates: index === 0 ? ['No ticket status change was made.'] : [] } })))
    const action = demo.requests.find(r => r.ticket_id === id && r.status === 'pending')
    if (action) action.run_id = runId
    writeDemo(demo)
    return { run_id: runId, ticket_id: id, ticket_status: ticket.status, ticket_status_changed: false, summary: `Mock team run completed for ${ticket.subject}.`, facts: [`Ticket status: ${ticket.status}.`, 'No live agent request was sent.'], unresolved_questions: [], proposed_updates: [], pending_actions: action ? [action] : [] } satisfies RunResult
  }
  const approveMatch = path.match(/^\/approvals\/([^/]+)\/approve$/)
  if (method === 'POST' && approveMatch) {
    const payload = JSON.parse(String(init?.body ?? '{}')) as { approved_by?: string }
    const item = demo.requests.find(r => r.request_id === approveMatch[1])
    if (!item || item.status !== 'pending') throw new Error('Mock request is no longer pending.')
    if (item.tool_name === 'record_purchase_order') {
      item.status = 'blocked'
      const total = Number((payload as { expected_total_amount?: number }).expected_total_amount)
      item.block_reason = demo.balance < total
        ? `Mock purchase blocked: ${money(total)} exceeds the ${money(demo.balance)} checking balance. Nothing was ordered.`
        : 'Mock purchase blocked: no verified supplier quote is available in the fixture. Nothing was ordered.'
      writeDemo(demo)
      throw new Error(item.block_reason)
    }
    item.status = 'executed'
    const amount = Number(item.arguments.amount ?? 0)
    demo.balance = Math.round((demo.balance - amount) * 100) / 100
    demo.events.push({ event_id: `mock-approval-${Date.now()}`, timestamp: new Date().toISOString(), run_id: item.run_id, ticket_id: item.ticket_id, agent: 'human', step: 'mock_approval_recorded', node_type: 'human_approval', detail: { summary: `Mock approval by ${payload.approved_by || 'Shop manager'}. Checking is now ${money(demo.balance)}.`, action: item.tool_name, amount } })
    writeDemo(demo)
    return { request_id: item.request_id, status: 'executed', result: { updated: true, new_balance: demo.balance }, verified: true }
  }
  throw new Error(`No mock route for ${method} ${path}`)
}

export function agentLabel(agent?: string) {
  return ({ boss: 'Boss', inventory: 'Inventory', accounting: 'Accounting', facilities: 'Facilities', customer_service: 'Customer Service', human: 'Shop manager' } as Record<string, string>)[agent ?? ''] ?? agent ?? 'Team'
}
export const money = (amount: unknown) => Number.isFinite(Number(amount)) ? new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(Number(amount)) : '—'

export const api = {
  tickets: () => request<{ tickets: Ticket[] }>('/tickets'),
  events: () => request<{ events: BoardEvent[] }>('/events?limit=100'),
  approvals: () => request<{ requests: ApprovalRequest[] }>('/approvals/pending'),
  balance: () => request<{ account: string; balance: number; date?: string }>('/cash/balance'),
  run: (id: number) => request<RunResult>(`/tickets/${id}/run`, { method: 'POST', body: JSON.stringify({}) }),
  approve: (item: ApprovalRequest, data: { approved_by: string; quoted_unit_cost?: number; expected_total_amount?: number; supplier_quote_reference?: string; supplier_selection_confirmed?: boolean }) => request(`/approvals/${encodeURIComponent(item.request_id)}/approve`, { method: 'POST', body: JSON.stringify({ ...data, confirm_exact_request: true, expected_arguments: item.arguments }) }),
  demoFailNext: () => { if (demo) { demo.failNext = true; writeDemo(demo) } },
  resetDemo: () => { if (DEMO_MODE) { demo = structuredClone(seed); writeDemo(demo) } },
}
