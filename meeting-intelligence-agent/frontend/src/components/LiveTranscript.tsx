/**
 * LiveTranscript — user-aware live transcript + intelligence panel.
 *
 * Props:
 *   meetingId     — required
 *   currentUserId — the logged-in user's ID; enables priority=critical
 *                   detection and the personal filter toggle
 *
 * Usage:
 *   <LiveTranscript meetingId={meeting.id} currentUserId={user.id} />
 */
import React, { useEffect, useRef, useState } from 'react'
import { useLiveMeeting, TranscriptSegment, ToastEntry } from '../hooks/useLiveMeeting'
import LiveIntelligencePanel from './LiveIntelligencePanel'

interface Props {
  meetingId:      string
  currentUserId?: string
  className?:     string
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

// ── Status badge ──────────────────────────────────────────────────────────────

function StatusBadge({ status, elapsedS }: { status: string; elapsedS: number }) {
  if (status === 'idle') return null
  const animate = status === 'recording'
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-medium text-gray-700">
      <span className={`w-2 h-2 rounded-full ${animate ? 'bg-red-500 animate-pulse' : 'bg-gray-400'}`} />
      {status === 'recording' ? `REC ${formatElapsed(elapsedS)}` :
       status === 'stopped'   ? 'Ended' : '⚠ Disconnected'}
    </span>
  )
}

// ── Transcript row ────────────────────────────────────────────────────────────

function SegmentRow({ seg }: { seg: TranscriptSegment }) {
  return (
    <div className="flex gap-3 py-1.5 border-b border-gray-50 last:border-0">
      <span className="text-[11px] text-gray-400 font-mono mt-0.5 w-10 shrink-0">
        {formatElapsed(Math.floor(seg.t))}
      </span>
      <div className="flex-1 min-w-0">
        {seg.speaker && (
          <span className="text-xs font-semibold text-indigo-600 mr-1">{seg.speaker}:</span>
        )}
        <span className="text-sm text-gray-800">{seg.text}</span>
      </div>
    </div>
  )
}

// ── Toast ─────────────────────────────────────────────────────────────────────

const TOAST_META: Record<string, { icon: string; label: string; color: string }> = {
  action_item:  { icon: '☑️', label: 'Action item',  color: 'border-blue-200   bg-blue-50   text-blue-900'   },
  decision:     { icon: '⚖️', label: 'Decision',     color: 'border-purple-200 bg-purple-50 text-purple-900' },
  mention:      { icon: '👤', label: 'Mention',       color: 'border-amber-200  bg-amber-50  text-amber-900'  },
  confirmation: { icon: '✅', label: 'Confirmed',     color: 'border-green-200  bg-green-50  text-green-900'  },
  interrupt:     { icon: '🔴', label: 'Urgent',       color: 'border-red-200    bg-red-50    text-red-900'    },
  recommendation: { icon: '⚡', label: 'Recommendation', color: 'border-orange-200 bg-orange-50 text-orange-900' },
}

function Toast({ toast, onDismiss }: { toast: ToastEntry; onDismiss: (id: string) => void }) {
  const meta   = TOAST_META[toast.kind] ?? TOAST_META.action_item
  const isForMe = toast.isForMe
  return (
    <div
      className={`flex items-start gap-2.5 border rounded-lg px-3 py-2.5 shadow-lg max-w-xs w-full animate-slide-in
        ${isForMe ? 'border-red-300 bg-red-50 text-red-900' : meta.color}`}
      role="alert"
    >
      <span className="text-base mt-0.5 shrink-0">{isForMe ? '🔴' : meta.icon}</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[10px] font-bold uppercase tracking-wide opacity-70">
            {isForMe ? 'FOR YOU — ' : ''}{meta.label}
            {toast.source === 'llm' && ' ✦'}
          </span>
          <button onClick={() => onDismiss(toast.id)} className="opacity-40 hover:opacity-80 text-sm" aria-label="Dismiss">×</button>
        </div>
        <p className="text-xs mt-0.5 line-clamp-2 leading-snug font-medium">{toast.text}</p>
        {toast.assignee && <p className="text-[10px] mt-0.5 opacity-60">→ {toast.assignee}</p>}
        {toast.name     && <p className="text-[10px] mt-0.5 opacity-60">@{toast.name}</p>}
      </div>
    </div>
  )
}

function ToastContainer({ toasts, onDismiss }: { toasts: ToastEntry[]; onDismiss: (id: string) => void }) {
  if (toasts.length === 0) return null
  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 items-end pointer-events-none">
      {toasts.slice(-4).map(t => (
        <div key={t.id} className="pointer-events-auto">
          <Toast toast={t} onDismiss={onDismiss} />
        </div>
      ))}
    </div>
  )
}

// ── Mention pills ─────────────────────────────────────────────────────────────

function MentionBar({ names }: { names: string[] }) {
  if (names.length === 0) return null
  const unique = [...new Set(names)]
  return (
    <div className="flex flex-wrap gap-1.5 px-4 py-2 border-b border-gray-100 bg-amber-50/60">
      <span className="text-[11px] text-gray-400 self-center mr-1">Mentioned:</span>
      {unique.slice(-8).map((name, i) => (
        <span key={i} className="text-xs font-medium bg-amber-100 border border-amber-200 text-amber-800 px-2 py-0.5 rounded-full">
          @{name}
        </span>
      ))}
    </div>
  )
}

// ── Personal filter button ────────────────────────────────────────────────────

function FilterToggle({
  active,
  criticalCount,
  onChange,
}: {
  active:        boolean
  criticalCount: number
  onChange:      (v: boolean) => void
}) {
  return (
    <button
      onClick={() => onChange(!active)}
      className={`inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full border transition-colors
        ${active
          ? 'bg-indigo-600 text-white border-indigo-600'
          : 'bg-white text-gray-600 border-gray-300 hover:border-indigo-400 hover:text-indigo-600'}`}
      title={active ? 'Showing only items relevant to you' : 'Show only my items'}
    >
      <span>👤 My items</span>
      {criticalCount > 0 && (
        <span className={`text-[10px] font-bold rounded-full px-1.5 py-0.5 ${active ? 'bg-white text-indigo-700' : 'bg-red-500 text-white'}`}>
          {criticalCount}
        </span>
      )}
    </button>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function LiveTranscript({ meetingId, currentUserId, className = '' }: Props) {
  const [personalFilter, setPersonalFilter] = useState(false)

  const {
    connected, pipelineStatus, elapsedS,
    segments, mentions, actionItems, decisions,
    recommendation, toasts, error, dismissToast,
  } = useLiveMeeting(meetingId, { currentUserId })

  // Auto-scroll transcript
  const scrollRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [segments.length])

  // Don't render while idle and empty
  const hasData =
    pipelineStatus !== 'idle' ||
    segments.length > 0 || actionItems.length > 0 || decisions.length > 0

  if (!hasData) return null

  const mentionNames   = mentions.map(m => m.name)
  const criticalCount  = actionItems.filter(
    ai => ai.priority === 'critical' && !ai.confirmed &&
          (!currentUserId || ai.target_user_id === currentUserId)
  ).length

  return (
    <>
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />

      <div className={`space-y-3 ${className}`}>
        {/* Transcript panel */}
        <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-100 bg-gray-50 gap-3">
            <span className="text-sm font-semibold text-gray-700">Live Transcript</span>

            <div className="flex items-center gap-3 ml-auto">
              {currentUserId && (
                <FilterToggle
                  active={personalFilter}
                  criticalCount={criticalCount}
                  onChange={setPersonalFilter}
                />
              )}
              {error && (
                <span className="text-xs text-yellow-600 max-w-[160px] truncate" title={error}>
                  ⚠ {error}
                </span>
              )}
              <StatusBadge status={pipelineStatus} elapsedS={elapsedS} />
              <span
                className={`w-2 h-2 rounded-full ${connected ? 'bg-green-400' : 'bg-gray-300'}`}
                title={connected ? 'Connected' : 'Disconnected'}
              />
            </div>
          </div>

          <MentionBar names={mentionNames} />

          <div
            ref={scrollRef}
            className="overflow-y-auto px-4 py-2"
            style={{ maxHeight: '300px', minHeight: '72px' }}
          >
            {segments.length === 0 ? (
              <p className="text-sm text-gray-400 italic py-4 text-center">Waiting for speech…</p>
            ) : (
              segments.map((seg, i) => <SegmentRow key={i} seg={seg} />)
            )}
          </div>
        </div>

        {/* Intelligence panel */}
        <LiveIntelligencePanel
          actionItems={actionItems}
          decisions={decisions}
          recommendation={recommendation}
          currentUserId={currentUserId}
          personalFilter={personalFilter}
        />
      </div>
    </>
  )
}
