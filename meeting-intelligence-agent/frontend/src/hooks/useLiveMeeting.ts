/**
 * useLiveMeeting — user-aware live meeting hook.
 *
 * Connects to the WebSocket live feed and maintains state for:
 *   • transcript segments
 *   • mentions, action items, decisions
 *   • confirmations (links back to action items via event_id)
 *   • event enrichments (memory links from past meetings)
 *   • toast queue (ephemeral alerts for critical/important events)
 *   • personal filter (show only items relevant to currentUserId)
 *
 * Usage:
 *   const live = useLiveMeeting(meetingId, { currentUserId: user.id })
 */
import { useCallback, useEffect, useReducer, useRef } from 'react'
import { getAccessToken } from '../lib/auth'
import { getApiBaseUrl } from '../lib/api'

// ── Event shape definitions ───────────────────────────────────────────────────

export interface RelatedEvent {
  meeting_id:    string
  meeting_title: string
  meeting_date:  string
  text:          string
  event_type:    string
}

export interface TranscriptSegment {
  type: 'transcript'
  text: string; speaker: string | null; t: number; final: boolean
  meeting_id: string; ts: string
}

export interface MentionEvent {
  type: 'mention'
  name: string; text: string; t: number
  meeting_id: string; ts: string
}

export interface ActionItemEvent {
  type:              'action_item'
  event_id:          string
  text:              string
  assignee:          string | null
  target_user_id:    string | null
  priority:          'critical' | 'important' | 'info'
  confirmed:         boolean
  confidence:        'high' | 'medium' | 'low'
  source:            'regex' | 'llm'
  related_to_previous: RelatedEvent | null
  t:                 number
  meeting_id:        string; ts: string
}

export interface DecisionEvent {
  type:              'decision'
  event_id:          string
  text:              string
  target_user_id:    string | null
  priority:          'critical' | 'important' | 'info'
  confidence:        'high' | 'medium' | 'low'
  source:            'regex' | 'llm'
  related_to_previous: RelatedEvent | null
  t:                 number
  meeting_id:        string; ts: string
}

export interface ConfirmationEvent {
  type:            'confirmation'
  event_id:        string
  action_event_id: string   // event_id of the confirmed action item
  action_text:     string
  t:               number
  meeting_id:      string; ts: string
}

export interface EventEnrichment {
  type:                'event_enrichment'
  event_id:            string    // original event to update
  original_type:       string
  related_to_previous: RelatedEvent
}

export interface StatusEvent  { type: 'status';    status: 'recording' | 'stopped'; elapsed_s: number }
export interface HeartbeatEvent { type: 'heartbeat'; elapsed_s: number }

export interface ToastEntry {
  id:         string
  kind:       'action_item' | 'decision' | 'mention' | 'confirmation'
  text:       string
  assignee?:  string | null
  name?:      string
  priority?:  string
  source?:    string
  isForMe:    boolean
  addedAt:    number
}

type LiveEvent =
  | TranscriptSegment | MentionEvent | ActionItemEvent | DecisionEvent
  | ConfirmationEvent | EventEnrichment | StatusEvent | HeartbeatEvent
  | { type: 'ping' } | { type: 'error'; message: string }

// ── State ─────────────────────────────────────────────────────────────────────

export interface LiveMeetingState {
  connected:       boolean
  pipelineStatus:  'idle' | 'recording' | 'stopped' | 'error'
  elapsedS:        number
  segments:        TranscriptSegment[]
  mentions:        MentionEvent[]
  actionItems:     ActionItemEvent[]
  decisions:       DecisionEvent[]
  toasts:          ToastEntry[]
  error:           string | null
}

const INITIAL: LiveMeetingState = {
  connected: false, pipelineStatus: 'idle', elapsedS: 0,
  segments: [], mentions: [], actionItems: [], decisions: [],
  toasts: [], error: null,
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

function shouldToast(ev: ActionItemEvent | DecisionEvent, currentUserId?: string): boolean {
  if (ev.priority === 'info') return false
  // Always toast critical; only toast important for things assigned/related to the user
  if (ev.priority === 'critical') return true
  if (ev.type === 'action_item' && ev.target_user_id && ev.target_user_id === currentUserId) return true
  return ev.priority === 'important' && ev.confidence === 'high'
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
          return { ...state, segments: [...state.segments, ev].slice(-MAX_SEGMENTS) }

        case 'mention': {
          const toast: ToastEntry = {
            id: nextId(), kind: 'mention', text: ev.text, name: ev.name,
            isForMe: false, addedAt: Date.now(),
          }
          return { ...state, mentions: [...state.mentions, ev], toasts: [...state.toasts, toast] }
        }

        case 'action_item': {
          const isForMe = !!(ev.target_user_id && ev.target_user_id === currentUserId)
          const newItems = [...state.actionItems, ev]
          const newToasts = shouldToast(ev, currentUserId)
            ? [...state.toasts, {
                id: nextId(), kind: 'action_item' as const,
                text: ev.text, assignee: ev.assignee,
                priority: ev.priority, source: ev.source,
                isForMe, addedAt: Date.now(),
              }]
            : state.toasts
          return { ...state, actionItems: newItems, toasts: newToasts }
        }

        case 'decision': {
          const newDecs = [...state.decisions, ev]
          const newToasts = shouldToast(ev, currentUserId)
            ? [...state.toasts, {
                id: nextId(), kind: 'decision' as const,
                text: ev.text, priority: ev.priority, source: ev.source,
                isForMe: false, addedAt: Date.now(),
              }]
            : state.toasts
          return { ...state, decisions: newDecs, toasts: newToasts }
        }

        case 'confirmation': {
          // Mark the linked action item as confirmed
          const updated = state.actionItems.map(ai =>
            ai.event_id === ev.action_event_id ? { ...ai, confirmed: true } : ai
          )
          const toast: ToastEntry = {
            id: nextId(), kind: 'confirmation',
            text: `Confirmed: ${ev.action_text.slice(0, 60)}`,
            isForMe: false, addedAt: Date.now(),
          }
          return { ...state, actionItems: updated, toasts: [...state.toasts, toast] }
        }

        case 'event_enrichment': {
          // Merge related_to_previous into the matching event
          const updatedAI = state.actionItems.map(ai =>
            ai.event_id === ev.event_id
              ? { ...ai, related_to_previous: ev.related_to_previous }
              : ai
          )
          const updatedDec = state.decisions.map(d =>
            d.event_id === ev.event_id
              ? { ...d, related_to_previous: ev.related_to_previous }
              : d
          )
          return { ...state, actionItems: updatedAI, decisions: updatedDec }
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

        default:
          return state
      }
    }
    default: return state
  }
}

// ── Hook ──────────────────────────────────────────────────────────────────────

interface Options {
  currentUserId?: string
}

export function useLiveMeeting(
  meetingId: string | null | undefined,
  options: Options = {},
): LiveMeetingState & { dismissToast: (id: string) => void } {
  const { currentUserId } = options
  const [state, dispatch] = useReducer(reducer, INITIAL)
  const wsRef             = useRef<WebSocket | null>(null)
  const reconnectAttempts = useRef(0)
  const reconnectTimer    = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mounted           = useRef(true)

  const connect = useCallback(() => {
    if (!meetingId) return
    const token = getAccessToken()
    if (!token) { dispatch({ type: 'ERROR', message: 'Not authenticated' }); return }

    const base   = getApiBaseUrl() || window.location.origin
    const url    = `${base.replace(/^http/, 'ws')}/api/v1/live/ws/${meetingId}?token=${encodeURIComponent(token)}`
    const ws     = new WebSocket(url)
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
      } catch { /* ignore malformed */ }
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

  // Toast expiry interval
  useEffect(() => {
    const id = setInterval(() => dispatch({ type: 'EXPIRE_TOASTS' }), 2_000)
    return () => clearInterval(id)
  }, [])

  // Connect / disconnect
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
