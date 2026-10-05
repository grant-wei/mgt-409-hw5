import { useCallback, useEffect, useMemo, useState } from 'react'
import { Activity, ArrowDownRight, ArrowUpRight, Check, ChevronDown, CircleHelp, Clock3, Coins, LoaderCircle, PackageCheck, RefreshCw, RotateCcw, ShieldCheck, Sparkles, Store, TriangleAlert, Truck, Users } from 'lucide-react'
import { agentLabel, api, ApiError, DEMO_MODE, money } from '../lib/api'
import type { ApprovalRequest, BoardData, BoardEvent, RunResult, Ticket } from '../lib/types'
import './approvals.css'

const roleStyles: Record<string, string> = { boss: 'boss', inventory: 'inventory', accounting: 'accounting', facilities: 'facilities', customer_service: 'service', human: 'human' }
const roles = ['boss', 'inventory', 'accounting', 'facilities', 'customer_service']
const eventText = (event: BoardEvent) => {
  const detail = event.detail ?? {}
  if (typeof detail.summary === 'string') return detail.summary
  const target = typeof detail.target === 'string' ? ` → ${agentLabel(detail.target)}` : ''
  return `${String(event.step ?? 'Activity').replaceAll('_', ' ')}${target}`
}
const titleCase = (value?: string | null) => value ? value.replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase()) : 'Open'

export default function App() {
  const [data, setData] = useState<BoardData>({ tickets: [], events: [], requests: [], balance: 0 })
  const [selectedId, setSelectedId] = useState(101)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [loadingError, setLoadingError] = useState('')
  const [runError, setRunError] = useState('')
  const [busyTicket, setBusyTicket] = useState<number | null>(null)
  const [activeRun, setActiveRun] = useState<number | null>(null)
  const [failedTicket, setFailedTicket] = useState<number | null>(null)
  const [approving, setApproving] = useState<string | null>(null)
  const [approvalError, setApprovalError] = useState<Record<string, string>>({})
  const [approver, setApprover] = useState('')
  const [quoteForms, setQuoteForms] = useState<Record<string, { supplierConfirmed: boolean; reference: string; unit: string; total: string }>>({})
  const [lastRun, setLastRun] = useState<Record<number, { runId: string; summary: string; facts: string[]; questions: string[]; usage?: RunResult['usage_totals'] }>>({})

  const refresh = useCallback(async (initial = false) => {
    if (initial) setLoading(true); else setRefreshing(true)
    const [ticketsResult, eventsResult, approvalsResult, cashResult] = await Promise.allSettled([api.tickets(), api.events(), api.approvals(), api.balance()])
    const errors: string[] = []
    if (ticketsResult.status === 'rejected') errors.push(`Tickets: ${ticketsResult.reason instanceof Error ? ticketsResult.reason.message : 'Could not load tickets.'}`)
    if (eventsResult.status === 'rejected') errors.push(`Events: ${eventsResult.reason instanceof Error ? eventsResult.reason.message : 'Could not load events.'}`)
    if (approvalsResult.status === 'rejected') errors.push(`Approvals: ${approvalsResult.reason instanceof Error ? approvalsResult.reason.message : 'Could not load approvals.'}`)
    if (cashResult.status === 'rejected') errors.push(`Checking balance: ${cashResult.reason instanceof Error ? cashResult.reason.message : 'Could not load balance.'}`)
    setData(current => {
      const next = { ...current }
      if (ticketsResult.status === 'fulfilled') next.tickets = ticketsResult.value.tickets
      if (eventsResult.status === 'fulfilled') next.events = eventsResult.value.events
      if (approvalsResult.status === 'fulfilled') next.requests = approvalsResult.value.requests
      if (cashResult.status === 'fulfilled') { next.balance = cashResult.value.balance; next.balanceDate = cashResult.value.date }
      return next
    })
    setLoadingError(errors.join(' '))
    setLoading(false); setRefreshing(false)
  }, [])

  useEffect(() => { void refresh(true) }, [refresh])
  useEffect(() => {
    if (activeRun === null) return
    const timer = window.setInterval(() => { void refresh() }, 1600)
    return () => window.clearInterval(timer)
  }, [activeRun, refresh])

  const tickets = data.tickets
  const selected = tickets.find(ticket => ticket.id === selectedId) ?? tickets[0]
  const ticketEvents = useMemo(() => data.events.filter(e => e.ticket_id === selected?.id).slice().reverse(), [data.events, selected?.id])
  const pendingCount = data.requests.filter(r => r.status === 'pending').length

  function statusFor(ticket: Ticket) {
    if (ticket.status?.toLowerCase() === 'resolved') return 'resolved'
    if (activeRun === ticket.id) return 'running'
    if (data.requests.some(r => r.ticket_id === ticket.id && r.status === 'pending')) return 'awaiting approval'
    if (data.requests.some(r => r.ticket_id === ticket.id && r.status === 'blocked')) return 'blocked'
    if (failedTicket === ticket.id) return 'failed'
    return ticket.status?.toLowerCase() === 'pending' ? 'pending' : 'open'
  }

  async function startRun(ticketId: number) {
    if (busyTicket !== null || activeRun !== null) return
    setBusyTicket(ticketId); setActiveRun(ticketId); setFailedTicket(null); setRunError('')
    try {
      const result = await api.run(ticketId)
      setLastRun(prev => ({ ...prev, [ticketId]: { runId: result.run_id, summary: result.summary ?? 'Run finished.', facts: result.facts ?? [], questions: result.unresolved_questions ?? [], usage: result.usage_totals } }))
      await refresh()
    } catch (error) {
      setFailedTicket(ticketId)
      setRunError(error instanceof Error ? error.message : 'The team run failed.')
      const detail = error instanceof ApiError && typeof error.detail === 'object' && error.detail !== null ? error.detail as { usage_totals?: RunResult['usage_totals'] } : null
      if (detail?.usage_totals) setLastRun(prev => ({ ...prev, [ticketId]: { runId: '', summary: 'Run failed after provider usage was recorded.', facts: [], questions: [], usage: detail.usage_totals } }))
      await refresh()
    } finally { setActiveRun(null); setBusyTicket(null) }
  }

  async function approve(item: ApprovalRequest) {
    if (approving !== null) return
    setApprovalError(prev => ({ ...prev, [item.request_id]: '' }))
    const quote = quoteForms[item.request_id]
    if (!approver.trim()) { setApprovalError(prev => ({ ...prev, [item.request_id]: 'Enter the human approver name first.' })); return }
    const payload: Parameters<typeof api.approve>[1] = { approved_by: approver.trim() }
    if (item.tool_name === 'record_purchase_order') {
      const unit = Number(quote?.unit), total = Number(quote?.total)
      if (!quote?.supplierConfirmed || !quote.reference.trim() || !Number.isFinite(unit) || !Number.isFinite(total) || unit < 0 || total <= 0) {
        setApprovalError(prev => ({ ...prev, [item.request_id]: 'Confirm the recorded vendor and add the quote reference, unit price, and exact total to continue.' })); return
      }
      payload.supplier_selection_confirmed = quote.supplierConfirmed
      payload.supplier_quote_reference = quote.reference.trim()
      payload.quoted_unit_cost = unit
      payload.expected_total_amount = total
    }
    setApproving(item.request_id)
    try { await api.approve(item, payload); await refresh() }
    catch (error) { setApprovalError(prev => ({ ...prev, [item.request_id]: error instanceof Error ? error.message : 'Approval failed.' })); await refresh() }
    finally { setApproving(null) }
  }

  function setQuote(id: string, key: 'supplier' | 'reference' | 'unit' | 'total', value: string) {
    setQuoteForms(prev => {
      const current = prev[id] ?? { supplierConfirmed: false, reference: '', unit: '', total: '' }
      return { ...prev, [id]: { ...current, [key]: value } }
    })
  }

  function setSupplierConfirmed(id: string, checked: boolean) {
    setQuoteForms(prev => {
      const current = prev[id] ?? { supplierConfirmed: false, reference: '', unit: '', total: '' }
      return { ...prev, [id]: { ...current, supplierConfirmed: checked } }
    })
  }

  function mockControls() {
    api.resetDemo(); setLastRun({}); setFailedTicket(null); setRunError(''); setApprover(''); setApprovalError({}); setQuoteForms({}); void refresh(true)
  }

  const running = activeRun !== null
  const currentRun = selected ? lastRun[selected.id] : undefined
  const currentRoleEvents = roles.map(role => ({ role, events: ticketEvents.filter(e => e.agent === role) }))

  return <div className="app-shell">
    <aside className="rail">
      <a className="brand" href="#home" aria-label="Campus Customs home"><span className="brand-mark"><Store size={19} /></span><span>CAMPUS<br />CUSTOMS</span></a>
      <div className="rail-label">Workspace</div>
      <button className="rail-link selected"><Activity size={17} />Team desk<span className="rail-dot" /></button>
      <div className="rail-label tickets-label">Today’s tickets <span>{tickets.length || '—'}</span></div>
      <nav className="ticket-nav" aria-label="Tickets">
        {tickets.map(t => <button key={t.id} className={`ticket-nav-item ${selected?.id === t.id ? 'active' : ''}`} onClick={() => setSelectedId(t.id)}>
          <span className="ticket-nav-num">{String(t.id).slice(-2)}</span><span className="ticket-nav-copy"><b>{t.subject || `Ticket ${t.id}`}</b><small>{t.requester || t.type || 'Shop desk'}</small></span><span className={`tiny-status ${statusFor(t).replaceAll(' ', '-')}`} />
        </button>)}
      </nav>
      <div className="rail-bottom"><div className="rail-avatar">CC</div><div><b>Campus Customs</b><small>Shop operations</small></div><ChevronDown size={14} /></div>
    </aside>

    <main className="main-area">
      <header className="topbar"><div className="breadcrumb">Shop floor <span>/</span> Team desk</div><div className="top-actions">
        {DEMO_MODE && <span className="demo-pill"><span /> DEMO · Mock data</span>}
        <button className="icon-button" onClick={() => void refresh()} disabled={refreshing} aria-label="Refresh dashboard"><RefreshCw size={16} className={refreshing ? 'spin' : ''} /></button>
        <div className="today-chip"><span className="live-dot" />Local desk</div>
      </div></header>

      <div className="content-wrap">
        {loadingError && <div className="error-banner" role="alert"><TriangleAlert size={17} /><span>{loadingError}</span><button onClick={() => void refresh()}>Retry</button></div>}
        {DEMO_MODE && <div className="demo-notice"><Sparkles size={16} /><span><b>Mock mode is on.</b> Runs, approvals, events, and cash changes are stored only in this browser.</span><button onClick={mockControls}><RotateCcw size={14} />Reset demo</button><button onClick={() => { api.demoFailNext(); void refresh() }}>Test API error</button></div>}
        {runError && <div className="error-banner run-error" role="alert"><TriangleAlert size={17} /><span><b>Run failed.</b> {runError}</span><button onClick={() => setRunError('')} aria-label="Dismiss run error">Dismiss</button></div>}

        <div className="page-heading"><div><div className="eyebrow">CAMPUS CUSTOMS · SHOP OPERATIONS</div><h1>Good morning, <i>team.</i></h1><p>Keep the work moving. Keep the facts straight.</p></div><div className="heading-actions"><span className="sync-label"><span className="green-dot" />{refreshing ? 'Updating' : 'Desk is current'}</span><button className="secondary-button" onClick={() => void refresh()} disabled={refreshing}><RefreshCw size={15} />Refresh</button></div></div>

        <section className="metrics" aria-label="Shop overview">
          <div className="metric-card balance-card"><div className="metric-top"><span>Checking balance</span><span className="metric-icon green"><Coins size={17} /></span></div><div className="metric-value">{loading ? '······' : money(data.balance)}</div><div className="metric-foot"><span>{data.balanceDate ? `As of ${data.balanceDate}` : 'From the shop ledger'}</span><span className="metric-spark"><i /><i /><i /><i /><i /><i /><i /><i /></span></div></div>
          <div className="metric-card"><div className="metric-top"><span>Open tickets</span><span className="metric-icon amber"><CircleHelp size={17} /></span></div><div className="metric-value">{loading ? '—' : tickets.filter(t => t.status?.toLowerCase() !== 'resolved').length}<small> / 3</small></div><div className="metric-foot"><span>{pendingCount} awaiting approval</span><span className="metric-arrow"><ArrowDownRight size={14} /></span></div></div>
          <div className="metric-card"><div className="metric-top"><span>Team activity</span><span className="metric-icon blue"><Users size={17} /></span></div><div className="metric-value">{data.events.length}<small> steps</small></div><div className="metric-foot"><span>{running ? 'A team run is in progress' : 'Latest board activity'}</span><span className="metric-arrow"><ArrowUpRight size={14} /></span></div></div>
        </section>

        {loading ? <section className="skeleton-board"><div /><div /><div /></section> : !selected ? <section className="empty-state">No tickets came back from the desk API.<button onClick={() => void refresh()}>Try again</button></section> : <div className="desk-grid">
          <section className="ticket-column">
            <div className="section-heading"><div><div className="eyebrow">SELECTED TICKET</div><h2>Ticket {selected.id}</h2></div><StatusPill value={statusFor(selected)} /></div>
            <article className="ticket-card">
              <div className="ticket-category"><span className="category-icon"><PackageCheck size={16} /></span><span>{titleCase(selected.type)} request</span><span className="ticket-date">{selected.requester || 'Campus Customs'}</span></div>
              <h3>{selected.subject || `Ticket ${selected.id}`}</h3><p className="ticket-notes">{selected.notes || 'No additional ticket notes.'}</p>
              <div className="ticket-facts">
                {selected.sku && <Fact label="Item" value={`${selected.sku} · ${selected.size || 'Size n/a'}`} />}
                {selected.qty != null && <Fact label="Requested" value={`${selected.qty} units`} />}
                {selected.invoice_id != null && <Fact label="Linked invoice" value={`#${selected.invoice_id} · ${money(selected.invoice_amount)} · ${selected.invoice_status || 'status unavailable'}`} />}
                {selected.invoice_due && <Fact label="Invoice due" value={selected.invoice_due} />}
                {selected.lease_id != null && <Fact label="Lease" value={`${selected.space_name || `Lease ${selected.lease_id}`} · ${selected.landlord || 'landlord not listed'}`} />}
                {selected.monthly_rent != null && <Fact label="Recorded rent" value={`${money(selected.monthly_rent)} · due ${selected.rent_due || 'date not listed'}`} />}
              </div>
              <div className="ticket-controls"><div><span className="run-caption"><ShieldCheck size={14} /> Read-only team run</span><span className="run-caption-help">Requests are prepared for review. The ticket only resolves when the backend confirms it.</span></div><button className="primary-button" onClick={() => void startRun(selected.id)} disabled={busyTicket !== null || activeRun !== null || selected.status?.toLowerCase() === 'resolved'}>{busyTicket === selected.id ? <><LoaderCircle size={16} className="spin" /> Starting</> : <><Sparkles size={16} />Start team</>}</button></div>
              {currentRun && <div className="run-summary"><span className="summary-kicker">LATEST CONTRIBUTION · {currentRun.runId.slice(0, 15)}</span><p>{currentRun.summary}</p>{currentRun.facts.map((fact, i) => <div className="summary-line" key={i}><Check size={14} />{fact}</div>)}{currentRun.usage && <div className="summary-line">Actual provider usage: {currentRun.usage.input_tokens} input, {currentRun.usage.output_tokens} output tokens across {currentRun.usage.requests} requests.</div>}{currentRun.questions.map((q, i) => <div className="summary-line warning" key={`q${i}`}><TriangleAlert size={14} />{q}</div>)}</div>}
            </article>

            <div className="section-heading activity-heading"><div><div className="eyebrow">THE PEOPLE ON THIS TICKET</div><h2>Team activity</h2></div><span className="activity-count"><span className={running ? 'pulse-dot' : 'green-dot'} />{running ? 'Updating live' : `${currentRoleEvents.filter(r => r.events.length).length} active`}</span></div>
            <section className="people-list">
              {currentRoleEvents.map(({ role, events }) => {
                const latest = events[0]
                const contribution = latest ? eventText(latest) : 'Waiting for a task on this ticket.'
                const detail = latest?.detail ?? {}
                const facts = Array.isArray(detail.facts) ? detail.facts.filter((x): x is string => typeof x === 'string') : []
                return <article className={`person-row role-${roleStyles[role]}`} key={role}><div className="person-avatar">{agentLabel(role).split(' ').map(s => s[0]).join('')}</div><div className="person-main"><div className="person-name-line"><b>{agentLabel(role)}</b>{latest && <span className="contributed">{latest.step?.replaceAll('_', ' ')}</span>}</div><p>{contribution}</p>{facts.slice(0, 2).map((f, i) => <small className="person-fact" key={i}>{f}</small>)}</div><span className={`person-presence ${latest ? 'has-activity' : ''}`} title={latest ? 'Has contributed' : 'No activity yet'} /></article>
              })}
            </section>
            <section className="timeline-card"><div className="timeline-title"><div><div className="eyebrow">AUDIT TRAIL</div><h2>Recent steps</h2></div><span>{ticketEvents.length} events</span></div>
              {ticketEvents.length ? <div className="timeline">{ticketEvents.slice(0, 8).map((e, i) => <div className="timeline-item" key={e.event_id ?? `${e.run_id}-${e.agent}-${i}`}><div className={`timeline-avatar role-${roleStyles[e.agent ?? ''] ?? 'human'}`}>{(agentLabel(e.agent).split(' ').map(s => s[0]).join('') || '·').slice(0, 2)}</div><div className="timeline-copy"><b>{agentLabel(e.agent)}</b><span>{eventText(e)}</span><small>{e.timestamp ? new Date(e.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'Recent'}</small></div></div>)}</div> : <div className="timeline-empty"><Clock3 size={16} />No recorded activity yet. Start the team to see real steps here.</div>}
            </section>
          </section>

          <aside className="right-column">
            <section className="approvals-card"><div className="approvals-heading"><div><div className="eyebrow">HUMAN REVIEW</div><h2>Approval desk</h2></div><span className="approval-count">{data.requests.filter(r => r.status === 'pending').length}</span></div><p className="approvals-intro">Review the exact action before you approve it.</p>
              <label className="approver-label" htmlFor="approver">Your name</label><input id="approver" className="text-input" value={approver} onChange={e => setApprover(e.target.value)} placeholder="Human approver" autoComplete="name" />
              <div className="approval-list">
                {data.requests.length === 0 && <div className="approval-empty"><Check size={17} />No pending requests.</div>}
                {data.requests.map(item => <ApprovalCard key={item.request_id} item={item} busy={approving === item.request_id} anyBusy={approving !== null} error={approvalError[item.request_id] ?? ''} approve={() => void approve(item)} quote={quoteForms[item.request_id] ?? { supplierConfirmed: false, reference: '', unit: '', total: '' }} setQuote={(key, value) => setQuote(item.request_id, key, value)} setSupplierConfirmed={checked => setSupplierConfirmed(item.request_id, checked)} />)}
              </div>
            </section>
            <section className="balance-note"><div className="note-icon"><Coins size={17} /></div><div><b>Cash stays grounded</b><p>Every approved outflow is checked against the live checking balance. No sales are added.</p></div></section>
            <section className="fulfillment-note"><div className="fulfillment-icon"><Truck size={17} /></div><div><b>Ordered is not delivered</b><p>Purchase requests remain orders until delivery is separately confirmed in the shop records.</p></div></section>
            {DEMO_MODE && <div className="mock-run-note">Mock controls never contact the agent team, MCP server, or approval API.</div>}
          </aside>
        </div>}
        <footer className="footer"><span>Campus Customs · Operations desk</span><span>Facts and status from the local shop API</span></footer>
      </div>
    </main>
  </div>
}

function Fact({ label, value }: { label: string; value: string }) { return <div className="fact"><span>{label}</span><b>{value}</b></div> }

function StatusPill({ value }: { value: string }) {
  const label = value === 'awaiting approval' ? 'Awaiting approval' : value[0].toUpperCase() + value.slice(1)
  return <span className={`status-pill state-${value.replaceAll(' ', '-')}`}><span />{label}</span>
}

function ApprovalCard({ item, busy, anyBusy, error, approve, quote, setQuote, setSupplierConfirmed }: { item: ApprovalRequest; busy: boolean; anyBusy: boolean; error: string; approve: () => void; quote: { supplierConfirmed: boolean; reference: string; unit: string; total: string }; setQuote: (key: 'reference' | 'unit' | 'total', value: string) => void; setSupplierConfirmed: (checked: boolean) => void }) {
  const isPurchase = item.tool_name === 'record_purchase_order'
  const blocked = item.status === 'blocked'
  const action = item.tool_name === 'record_invoice_payment' ? 'Pay invoice' : item.tool_name === 'record_rent_payment' ? 'Pay rent' : 'Place purchase order'
  const args = item.arguments
  const number = Number(isPurchase ? args.quantity : args.amount)
  const purchaseTotal = Number(quote.total)
  return <article className={`approval-card ${blocked ? 'is-blocked' : ''}`}>
    <div className="approval-card-top"><span className={`approval-type ${isPurchase ? 'type-purchase' : 'type-payment'}`}>{isPurchase ? <PackageCheck size={14} /> : <Coins size={14} />}{action}</span><span className={`request-status ${item.status}`}>{blocked ? 'Blocked' : item.status === 'pending' ? 'Pending' : titleCase(item.status)}</span></div>
    {item.incomplete_run && <p className="incomplete-run-label">From incomplete run{item.source_agent ? ` · ${agentLabel(item.source_agent)}` : ''}</p>}
    <h3>{isPurchase ? `${String(args.sku)} · ${String(args.size)} · ${number} units` : item.tool_name === 'record_invoice_payment' ? `Invoice #${args.invoice_id}` : `Lease #${args.lease_id} · due rent`}</h3>
    <p className="approval-reason">{item.reason}</p>
    <dl className="action-details">
      {isPurchase ? <><Detail label="Action" value={`Order ${number} × ${String(args.sku)} (${String(args.size)})`} /><Detail label="Supplier" value={args.vendor_id ? `Vendor #${args.vendor_id} · human confirmation required` : 'Not selected'} /><Detail label="Quote" value={quote.reference ? `${quote.reference} · ${money(quote.unit)} each` : 'Required from human approver'} /><Detail label="Exact total" value={Number.isFinite(purchaseTotal) && purchaseTotal > 0 ? money(purchaseTotal) : 'Awaiting quote'} /><Detail label="Account" value={String(args.account ?? 'Not specified')} /><Detail label="Result" value="Ordered only · no inventory received" />
      </> : <><Detail label="Action" value={action} /><Detail label="Amount" value={money(args.amount)} /><Detail label="Account" value={String(args.account ?? 'Not specified')} />{item.tool_name === 'record_invoice_payment' && <Detail label="Invoice status" value="Must be open and linked to this ticket" />}{item.tool_name === 'record_rent_payment' && <Detail label="Lease" value={`Lease #${args.lease_id}`} />}</>}
    </dl>
    {isPurchase && item.status === 'pending' && <div className="quote-fields"><div className="quote-caption">Supplier and quote for this exact order</div><label className="vendor-confirm"><input type="checkbox" checked={quote.supplierConfirmed} onChange={e => setSupplierConfirmed(e.target.checked)} /><span>I confirm vendor #{String(args.vendor_id)} for this order</span></label><div className="quote-grid"><label>Quote reference<input value={quote.reference} onChange={e => setQuote('reference', e.target.value)} placeholder="Quote ID" /></label><label>Unit price<input type="number" min="0" step="0.01" value={quote.unit} onChange={e => setQuote('unit', e.target.value)} placeholder="$0.00" /></label><label>Total<input type="number" min="0.01" step="0.01" value={quote.total} onChange={e => setQuote('total', e.target.value)} placeholder="$0.00" /></label></div><small>Human confirmation is required. The total must equal quantity × quoted unit price.</small></div>}
    {blocked && <div className="blocked-note"><TriangleAlert size={14} />{item.block_reason || 'This request is blocked until the missing facts are resolved.'}</div>}
    {error && !blocked && <div className="approval-error" role="alert"><TriangleAlert size={14} />{error}</div>}
    {item.status === 'pending' && <button className="approve-button" disabled={anyBusy || !item.request_id} onClick={approve}>{busy ? <><LoaderCircle size={15} className="spin" />Processing</> : <><Check size={15} />Approve exact request</>}</button>}
    {item.status === 'executed' && <div className="approved-note"><Check size={14} />Executed · mock or backend verified</div>}
  </article>
}

function Detail({ label, value }: { label: string; value: string }) { return <><dt>{label}</dt><dd>{value}</dd></> }
