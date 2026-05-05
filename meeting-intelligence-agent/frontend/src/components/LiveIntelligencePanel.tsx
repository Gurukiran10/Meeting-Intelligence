/**
 * LiveIntelligencePanel — user-aware action items + decisions panel.
 *
 * Shows:
 *   • Priority dot + label: 🔴 critical / 🟠 important / ○ info
 *   • "FOR YOU" badge when assigned to the current user
 *   • "⟳ Revisited" badge when past-meeting context found
 *   • "✓ Confirmed" when someone verbally confirmed
 *   • "⚡ URGENT" badge when urgency_flag=true
 *   • ✦ AI label when detection_method=llm
 *   • reason + explanation (expandable on click)
 *   • confidence score bar (0–1)
 *   • personal filter toggle (show only FOR YOU + critical)
 */
import React, { useState } from 'react'
import { ActionItemEvent, DecisionEvent, RelatedEvent, RecommendationEvent } from '../hooks/useLiveMeeting'

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatTime(t: number): string {
  const m = Math.floor(t / 60), s = Math.floor(t % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

// ── Priority ───────────────────────────────────────────────────────────────────

const PRIORITY_DOT: Record<string, string> = {
  critical:  'bg-red-500', important: 'bg-orange-400', info: 'bg-gray-300',
}
const PRIORITY_LABEL: Record<string, string> = {
  critical:  'text-red-600', important: 'text-orange-600', info: 'text-gray-400',
}
const PRIORITY_BG: Record<string, string> = {
  critical:  'bg-red-50', important: 'bg-orange-50', info: '',
}

function PriorityDot({ priority }: { priority: string }) {
  return <span className={`w-2 h-2 rounded-full shrink-0 ${PRIORITY_DOT[priority] ?? PRIORITY_DOT.info}`} title={priority} />
}

// ── Badges ────────────────────────────────────────────────────────────────────

function UrgentBadge() {
  return <span className="text-[10px] font-bold text-red-600 bg-red-100 border border-red-300 px-1.5 py-0.5 rounded uppercase">⚡ Urgent</span>
}

function ForYouBadge() {
  return <span className="text-[10px] font-bold text-white bg-red-500 px-1.5 py-0.5 rounded uppercase tracking-wide">For you</span>
}

function ConfirmedBadge() {
  return <span className="text-[10px] font-medium text-green-700 bg-green-50 border border-green-200 px-1.5 py-0.5 rounded">✓ Confirmed</span>
}

function RelatedBadge({ related }: { related: RelatedEvent }) {
  return (
    <span className="text-[10px] font-medium text-violet-700 bg-violet-50 border border-violet-200 px-1.5 py-0.5 rounded cursor-help"
      title={`"${related.text}" — ${related.meeting_title} (${related.meeting_date})`}>
      ⟳ Revisited
    </span>
  )
}

function ConfBadge({ confidence_score, detection_method }: { confidence_score: number; detection_method: string }) {
  const pct = Math.round((confidence_score) * 100)
  const color = confidence_score >= 0.88 ? 'bg-emerald-400' : confidence_score >= 0.70 ? 'bg-yellow-400' : 'bg-gray-300'
  return (
    <div className="flex items-center gap-1" title={`${pct}% confidence`}>
      <div className="w-8 h-1.5 rounded-full bg-gray-200 overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] text-gray-500">{pct}%</span>
      {detection_method === 'llm' && <span className="text-[10px] text-indigo-500 font-medium">✦</span>}
    </div>
  )
}

// ── Expandable explanation ────────────────────────────────────────────────────

function ExplanationRow({ label, text }: { label: string; text: string }) {
  const [open, setOpen] = useState(false)
  if (!text) return null
  return (
    <div className="mt-1">
      <button onClick={() => setOpen(o => !o)} className="text-[10px] text-gray-400 hover:text-gray-600 flex items-center gap-0.5">
        {open ? '▾' : '▸'} {label}
      </button>
      {open && <p className="text-[11px] text-gray-500 mt-0.5 pl-3 border-l-2 border-gray-200 leading-snug">{text}</p>}
    </div>
  )
}

// ── Row components ───────────────────────────────────────────────────────────

function ActionItemRow({ item, isForMe }: { item: ActionItemEvent; isForMe: boolean }) {
  return (
    <div className={`py-2.5 border-b border-gray-100 last:border-0 group ${item.priority === 'critical' ? PRIORITY_BG.critical : ''}`}>
      <div className="flex gap-2.5">
        <div className="flex flex-col items-center gap-1.5 mt-1">
          <PriorityDot priority={item.priority} />
          <div className={`w-3.5 h-3.5 rounded border-2 ${item.confirmed ? 'bg-green-500 border-green-500' : 'border-gray-300 group-hover:border-blue-400'}`} />
        </div>
        <div className="flex-1 min-w-0">
          <p className={`text-sm leading-snug ${item.confirmed ? 'line-through text-gray-400' : 'text-gray-800'}`}>
            {item.text}
          </p>
          <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
            {item.urgency_flag && <UrgentBadge />}
            {isForMe && <ForYouBadge />}
            {item.confirmed && <ConfirmedBadge />}
            {item.related_to_previous && <RelatedBadge related={item.related_to_previous} />}
            {item.assignee && !isForMe && (
              <span className="text-xs font-medium text-indigo-600 bg-indigo-50 px-1.5 py-0.5 rounded">@{item.assignee}</span>
            )}
            <ConfBadge confidence_score={item.confidence_score} detection_method={item.detection_method} />
            <span className="text-[10px] text-gray-400 font-mono">{formatTime(item.t)}</span>
          </div>
          <ExplanationRow label="why?" text={item.reason} />
          <ExplanationRow label="context" text={item.related_context} />
        </div>
      </div>
    </div>
  )
}

function DecisionRow({ item }: { item: DecisionEvent }) {
  return (
    <div className="py-2.5 border-b border-gray-100 last:border-0 group">
      <div className="flex gap-2.5">
        <div className="flex flex-col items-center gap-1.5 mt-1">
          <PriorityDot priority={item.priority} />
          <span className="text-purple-500 text-[11px] font-bold leading-none">✓</span>
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm text-gray-800 leading-snug">{item.text}</p>
          <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
            {item.urgency_flag && <UrgentBadge />}
            {item.related_to_previous && <RelatedBadge related={item.related_to_previous} />}
            <ConfBadge confidence_score={item.confidence_score} detection_method={item.detection_method} />
            <span className="text-[10px] text-gray-400 font-mono">{formatTime(item.t)}</span>
          </div>
          <ExplanationRow label="why?" text={item.reason} />
        </div>
      </div>
    </div>
  )
}

// ── Recommendation banner ────────────────────────────────────────────────────

function RecommendationBanner({ rec }: { rec: RecommendationEvent }) {
  const color = rec.action === 'join_now' ? 'border-red-300 bg-red-50' : 'border-gray-200 bg-gray-50'
  const icon  = rec.action === 'join_now' ? '🚨' : rec.action === 'already_handling' ? '✅' : '💤'
  return (
    <div className={`flex items-start gap-2.5 px-4 py-3 border rounded-xl ${color}`}>
      <span className="text-xl">{icon}</span>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-gray-800 capitalize">{rec.action.replace('_', ' ')}</p>
        <p className="text-xs text-gray-600 mt-0.5">{rec.explanation}</p>
        <span className="text-[10px] text-gray-400 mt-1 inline-block">{rec.reason}</span>
      </div>
    </div>
  )
}

// ── Section ──────────────────────────────────────────────────────────────────

function Section({ title, count, accentClass, children }: { title: string; count: number; accentClass: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col min-h-0">
      <div className={`flex items-center justify-between px-3 py-2 border-b ${accentClass}`}>
        <span className="text-xs font-semibold text-gray-700 uppercase tracking-wide">{title}</span>
        {count > 0 && <span className="text-xs font-bold text-gray-500 bg-white border border-gray-200 rounded-full px-2 py-0.5">{count}</span>}
      </div>
      <div className="overflow-y-auto px-3" style={{ maxHeight: '320px' }}>{children}</div>
    </div>
  )
}

function EmptyState({ icon, label }: { icon: string; label: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-6 text-gray-400">
      <span className="text-2xl mb-1">{icon}</span>
      <p className="text-xs">{label}</p>
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────

interface Props {
  actionItems: ActionItemEvent[]; decisions: DecisionEvent[]
  recommendation: RecommendationEvent | null
  currentUserId?: string; personalFilter: boolean
  className?: string
}

export default function LiveIntelligencePanel({
  actionItems, decisions, recommendation,
  currentUserId, personalFilter, className = '',
}: Props) {
  const filteredActions = personalFilter
    ? actionItems.filter(ai => !currentUserId || ai.target_user_id === currentUserId || ai.priority === 'critical' || ai.urgency_flag)
    : actionItems

  const criticalCount = actionItems.filter(ai => ai.priority === 'critical' && !ai.confirmed && (!currentUserId || ai.target_user_id === currentUserId)).length

  return (
    <div className={`rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden divide-y divide-gray-100 ${className}`}>
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-2.5 bg-gray-50">
        <span className="text-sm font-semibold text-gray-700">Meeting Intelligence</span>
        <span className="text-[10px] text-gray-400 bg-gray-100 border border-gray-200 rounded px-1.5 py-0.5">LIVE</span>
        {criticalCount > 0 && (
          <span className="text-[10px] font-bold text-white bg-red-500 rounded-full px-2 py-0.5 animate-pulse">
            {criticalCount} urgent
          </span>
        )}
        <span className="ml-auto flex items-center gap-3 text-[10px] text-gray-400">
          <span><span className="inline-block w-2 h-2 rounded-full bg-red-500 mr-1" />critical</span>
          <span><span className="inline-block w-2 h-2 rounded-full bg-orange-400 mr-1" />important</span>
        </span>
      </div>

      {/* Recommendation banner */}
      {recommendation && <div className="px-4 py-2"><RecommendationBanner rec={recommendation} /></div>}

      {/* Two-column panels */}
      <div className="grid grid-cols-1 md:grid-cols-2 divide-y md:divide-y-0 md:divide-x divide-gray-100">
        <Section title="Action Items" count={filteredActions.length} accentClass="bg-blue-50 border-blue-100">
          {filteredActions.length === 0 ? (
            <EmptyState icon="☑️" label="No action items yet" />
          ) : (
            [...filteredActions].reverse().map((item, i) => (
              <ActionItemRow key={item.event_id || i} item={item}
                isForMe={!!(currentUserId && item.target_user_id === currentUserId)} />
            ))
          )}
        </Section>

        <Section title="Decisions" count={decisions.length} accentClass="bg-purple-50 border-purple-100">
          {decisions.length === 0 ? (
            <EmptyState icon="⚖️" label="No decisions yet" />
          ) : (
            [...decisions].reverse().map((item, i) => (
              <DecisionRow key={item.event_id || i} item={item} />
            ))
          )}
        </Section>
      </div>
    </div>
  )
}