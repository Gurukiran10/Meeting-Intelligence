/**
 * useLiveMeeting — user-aware live meeting hook.
 *
 * Events processed:
 *   transcript, mention, action_item, decision, confirmation,
 *   event_enrichment, recommendation, interrupt, status, heartbeat
 *
 * Usage:
 *   const live = useLiveMeeting(meetingId, { currentUserId: user.id })
 */
import { useCallback, useEffect, useReducer, useRef } from 'react'
import { getAccessToken } from '../lib/auth'
import { getApiBaseUrl } from '../lib/api'

// ── Event shapes ──────────────────────────────────────────────────────────────

export interface RelatedEvent {
  meeting_id: string; meeting_title: string; meeting_date: string
  text: string; event_type: string
}

export interface TranscriptSegment {
  type: 'transcript'; text: string; speaker: string | null
  t: number; final: boolean; meeting_id: string; ts: string
}

export interface MentionEvent {
  type: 'mention'; name: string; text: string; t: number; meeting_id: string; ts: string
}

export interface ActionItemEvent {
  type: 'action_item'; event_id: string; text: string
  assignee: string | null; target_user_id: string | null
  priority: 'critical' | 'important' | 'info'
  reason: string; explanation: string       // WHY this matters
  confidence_score: number                   // 0–1 float
  detection_method: 'regex' | 'llm'
  urgency_flag: boolean
  related_context: string
  confirmed: boolean
  related_to_previous: RelatedEvent | null
  t: number; meeting_id: string; ts: string
}

export interface DecisionEvent {
  type: 'decision'; event_id: string; text: string
  target_user_id: string | null
  priority: 'critical' | 'important' | 'info'
  reason: string; explanation: string
  confidence_score: number
  detection_method: 'regex' | 'llm'
  urgency_flag: boolean
  related_context: string
  related_to_previous: RelatedEvent | null
  t: number; meeting_id: string; ts: string
}

export interface ConfirmationEvent {
  type: 'confirmation'; event_id: string
  action_event_id: string; action_text: string; raw: string
  reason: string; explanation: string
  confidence_score: number; detection_method: 'regex' | 'llm'
  urgency_flag: boolean
  t: number; meeting_id: string; ts: string
}

export interface RecommendationEvent {
  type: 'recommendation'
  action: 'join_now' | 'can_skip' | 'already_handling'
  reason: string; explanation: string
  urgency: 'high' | 'medium' | 'low'
  confidence: number
  t: number; meeting_id: string; ts: string
}

export interface InterruptEvent {
  type: 'interrupt'; event_id: string; action_event_id: string
  action_text: string; assignee: string | null; target_user_id: string | null
  reason: string; explanation: string
  urgency_flag: boolean
  t: number; meeting_id: string; ts: string
}

export interface EventEnrichment {
  type: 'event_enrichment'; event_id: string; original_type: string
  related_to_previous: RelatedEvent
}

export interface StatusEvent  { type: 'status';    status: 'recording' | 'stopped'; elapsed_s: number }
export interface HeartbeatEvent { type: 'heartbeat'; elapsed_s: number }

export interface ToastEntry {
  id: string
  kind: 'action_item' | 'decision' | 'mention' | 'confirmation' | 'interrupt' | 'recommendation'
  text: string; assignee?: string | null; name?: string
  priority?: string; source?: string; action?: string
  isForMe: boolean; urgency?: string
  addedAt: number
}

type LiveEvent =
  | TranscriptSegment | MentionEvent | ActionItemEvent | DecisionEvent
  | ConfirmationEvent | RecommendationEvent | InterruptEvent
  | EventEnrichment | StatusEvent | HeartbeatEvent
  | { type: 'ping' } | { type: 'error'; message: string }

// ── State ─────────────────────────────────────────────────────────────────────

export interface LiveMeetingState {
  connected: boolean; pipelineStatus: 'idle' | 'recording' | 'stopped' | 'error'
  elapsedS: number
  segments: TranscriptSegment[]; mentions: MentionEvent[]
  actionItems: ActionItemEvent[]; decisions: DecisionEvent[]
  recommendation: RecommendationEvent | null
  toasts: ToastEntry[]
  error: string | null
}

const INITIAL: LiveMeetingState = {
  connected: false, pipelineStatus: 'idle', elapsedS: 0,
  segments: [], mentions: [], actionItems: [], decisions: [],
  recommendation: null, toasts: [], error: null,
}

// ── Reducer ───────────────────────────────────────────────────────────────────

const MAX_SEGMENTS = 500
const TOAST_TTL_MS = 8_000

type Action =
  | { type: 'CONNECTED' }
  | { type: 'DISCONNECTED' }
  | { type: 'ERROR'; message: string }
  | { type: 'EVENT'; event: LiveEvent; currentUserId?: string }
  | { type: 'DISMISS_TOAST'; id: string }
  | { type: 'EXPIRE_TOASTS' }

let _seq = 0
const nextId = () => `t${++_seq}`

function _shouldToast(ev: ActionItemEvent | DecisionEvent | RecommendationEvent, currentUserId?: string): boolean {
  if ('priority' in ev && ev.priority === 'info') return false
  if (ev.type === 'recommendation') return ev.urgency === 'high' || ev.urgency === 'medium'
  if ('priority' in ev && ev.priority === 'critical') return true
  if (ev.type === 'action_item' && 'target_user_id' in ev && ev.target_user_id && ev.target_user_id === currentUserId) return true
  return false
}

function reducer(state: LiveMeetingState, action: Action): LiveMeetingState {
  switch (action.type) {
    case 'CONNECTED':    return { ...state, connected: true, error: null }
    case 'DISCONNECTED': return { ...state, connected: false }
    case 'ERROR':        return { ...state, connected: false, pipelineStatus: 'error', error: action.message }

    case 'EXPIRE_TOASTS': {
      const now = Date.now()
      return { ...state, toasts: state.toasts.filter(t => now - t.addedAt < TOAST_TTL_MS) }
    }
    case 'DISMISS_TOAST':
      return { ...state, toasts: state.toasts.filter(t => t.id !== action.id) }

    case 'EVENT': {
      const { event: ev, currentUserId } = action
      switch (ev.type) {

        case 'transcript':
          return { ...state, segments: [...state.segments, ev as TranscriptSegment].slice(-MAX_SEGMENTS) }

        case 'mention': {
          const me = ev as MentionEvent
          const toast: ToastEntry = {
            id: nextId(), kind: 'mention', text: me.text, name: me.name,
            isForMe: false, addedAt: Date.now(),
          }
          return { ...state, mentions: [...state.mentions, me], toasts: [...state.toasts, toast] }
        }

        case 'action_item': {
          const ai = ev as ActionItemEvent
          const isForMe = !!(ai.target_user_id && ai.target_user_id === currentUserId)
          const newToasts = _shouldToast(ai, currentUserId)
            ? [...state.toasts, {
                id: nextId(), kind: 'action_item' as const,
                text: ai.text, assignee: ai.assignee,
                priority: ai.priority, source: ai.detection_method,
                isForMe, addedAt: Date.now(),
              }]
            : state.toasts
          return { ...state, actionItems: [...state.actionItems, ai], toasts: newToasts }
        }

        case 'decision': {
          const de = ev as DecisionEvent
          const newToasts = _shouldToast(de, currentUserId)
            ? [...state.toasts, {
                id: nextId(), kind: 'decision' as const,
                text: de.text, priority: de.priority, source: de.detection_method,
                isForMe: false, addedAt: Date.now(),
              }]
            : state.toasts
          return { ...state, decisions: [...state.decisions, de], toasts: newToasts }
        }

        case 'confirmation': {
          const cf = ev as ConfirmationEvent
          const updated = state.actionItems.map(ai =>
            ai.event_id === cf.action_event_id ? { ...ai, confirmed: true } : ai
          )
          const toast: ToastEntry = {
            id: nextId(), kind: 'confirmation',
            text: `✓ Confirmed: ${cf.action_text.slice(0, 60)}`,
            isForMe: false, addedAt: Date.now(),
          }
          return { ...state, actionItems: updated, toasts: [...state.toasts, toast] }
        }

        case 'recommendation': {
          const re = ev as RecommendationEvent
          const toast: ToastEntry = {
            id: nextId(), kind: 'recommendation',
            text: re.explanation, action: re.action,
            priority: re.urgency, source: 'regex',
            isForMe: true, urgency: re.urgency, addedAt: Date.now(),
          }
          return { ...state, recommendation: re, toasts: [...state.toasts, toast] }
        }

        case 'interrupt': {
          const ir = ev as InterruptEvent
          const toast: ToastEntry = {
            id: nextId(), kind: 'interrupt',
            text: ir.explanation, assignee: ir.assignee,
            priority: 'critical', source: 'regex',
            isForMe: true, urgency: 'high', addedAt: Date.now(),
          }
          return { ...state, toasts: [...state.toasts, toast] }
        }

        case 'event_enrichment': {
          const ee = ev as EventEnrichment
          const updAI = state.actionItems.map(ai =>
            ai.event_id === ee.event_id ? { ...ai, related_to_previous: ee.related_to_previous } : ai
          )
          const updDec = state.decisions.map(d =>
            d.event_id === ee.event_id ? { ...d, related_to_previous: ee.related_to_previous } : d
          )
          return { ...state, actionItems: updAI, decisions: updDec }
        }

        case 'status': {
          const se = ev as StatusEvent
          return {
            ...state,
            pipelineStatus: se.status === 'stopped' ? 'stopped' : 'recording',
            elapsedS: se.elapsed_s,
          }
        }
        case 'heartbeat':
          return { ...state, elapsedS: (ev as HeartbeatEvent).elapsed_s }

        case 'error':
          return { ...state, error: (ev as { type: 'error'; message: string }).message }

        default: return state
      }
    }
    default: return state
  }
}

// ── Hook ──────────────────────────────────────────────────────────────────────

interface Options { currentUserId?: string }

export function useLiveMeeting(
  meetingId: string | null | undefined,
  options: Options = {},
): LiveMeetingState & { dismissToast: (id: string) => void } {
  const { currentUserId } = options
  const [state, dispatch] = useReducer(reducer, INITIAL)
  const wsRef           = useRef<WebSocket | null>(null)
  const reconnectAttempts = useRef(0)
  const reconnectTimer    = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mounted          = useRef(true)

  const connect = useCallback(() => {
    if (!meetingId) return
    const token = getAccessToken()
    if (!token) { dispatch({ type: 'ERROR', message: 'Not authenticated' }); return }

    const base = getApiBaseUrl() || window.location.origin
    const url  = `${base.replace(/^http/, 'ws')}/api/v1/live/ws/${meetingId}?token=${encodeURIComponent(token)}`
    const ws   = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => {
      if (!mounted.current) { ws.close(); return }
      reconnectAttempts.current = 0
      dispatch({ type: 'CONNECTED' })
    }
    ws.onmessage = (evt) => {
      if (!mounted.current) return
      try {
        const event: LiveEvent = JSON.parse(evt.data)
        dispatch({ type: 'EVENT', event, currentUserId })
      } catch { /* ignore */ }
    }
    ws.onerror = () => {
      if (!mounted.current) return
      dispatch({ type: 'ERROR', message: 'WebSocket connection error' })
    }
    ws.onclose = () => {
      if (!mounted.current) return
      dispatch({ type: 'DISCONNECTED' })
      if (reconnectAttempts.current < 5) {
        reconnectAttempts.current += 1
        reconnectTimer.current = setTimeout(connect, 3_000)
      }
    }
  }, [meetingId, currentUserId])

  useEffect(() => {
    const id = setInterval(() => dispatch({ type: 'EXPIRE_TOASTS' }), 2_000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    mounted.current = true
    if (!meetingId) return
    connect()
    return () => {
      mounted.current = false
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      if (wsRef.current) { wsRef.current.onclose = null; wsRef.current.close(); wsRef.current = null }
    }
  }, [meetingId, connect])

  const dismissToast = useCallback((id: string) => dispatch({ type: 'DISMISS_TOAST', id }), [])
  return { ...state, dismissToast }
}