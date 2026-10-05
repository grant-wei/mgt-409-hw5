export type Ticket = {
  id: number
  type?: string
  requester?: string
  subject?: string
  sku?: string | null
  size?: string | null
  qty?: number | null
  status?: string
  notes?: string
  invoice_id?: number | null
  invoice_status?: string | null
  invoice_amount?: number | null
  invoice_due?: string | null
  invoice_vendor?: string | null
  lease_id?: number | null
  space_name?: string | null
  landlord?: string | null
  monthly_rent?: number | null
  rent_due?: string | null
}

export type BoardEvent = {
  event_id?: string
  timestamp?: string
  run_id?: string
  agent?: string
  ticket_id?: number | null
  step?: string
  node_type?: string
  detail?: Record<string, unknown>
}

export type ApprovalRequest = {
  request_id: string
  run_id: string
  ticket_id: number
  tool_name: string
  arguments: Record<string, unknown>
  reason: string
  status: 'pending' | 'blocked' | 'executing' | 'executed' | 'failed' | 'cancelled'
  block_reason?: string | null
  approval_fields_required?: string[]
  incomplete_run?: boolean
  source_agent?: string | null
}

export type AgentUsage = { agent: string; requests: number; tool_calls: number; input_tokens: number; output_tokens: number; details?: Record<string, number> }

export type RunResult = {
  run_id: string
  ticket_id: number
  ticket_status?: string
  ticket_status_changed?: boolean
  summary?: string
  facts?: string[]
  unresolved_questions?: string[]
  proposed_updates?: string[]
  pending_actions?: ApprovalRequest[]
  usage?: AgentUsage[] | null
  usage_totals?: { requests: number; tool_calls: number; input_tokens: number; output_tokens: number } | null
}

export type BoardData = {
  tickets: Ticket[]
  events: BoardEvent[]
  requests: ApprovalRequest[]
  balance: number
  balanceDate?: string
}
