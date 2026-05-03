/**
 * LiveIntelligencePanel — user-aware action items + decisions panel.
 *
 * Shows:
 *   • Priority indicator: 🔴 critical / 🟠 important / ○ info
 *   • "FOR YOU" badge when item is assigned to / targets the current user
 *   • "⟳ Revisited" link when item has a cross-meeting memory match
 *   • "✓ Confirmed" state when someone verbally confirmed the action
 *   • ✦ label when item was detected by the LLM (higher accuracy)
 */
import React, { useState } from 'react'
import { ActionItemEvent, DecisionEvent, RelatedEvent } from '../hooks/useLiveMeeting'

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatTime(t: number): string {
  const m = Math.floor(t / 60)
  const s = Math.floor(t % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

// ── Priority indicator ────────────────────────────────────────────────────────

const PRIORITY_DOT: Record<string, string> = {
  critical:  'bg-red-500',
  important: 'bg-orange-400',
  info:      'bg-gray-300',
}
const PRIORITY_LABEL: Record<string, string> = {
  critical:  'text-red-600',
  important: 'text-orange-600',
  info:      'text-gray-400',
}

function PriorityDot({ priority }: { priority: string }) {
  const dot = PRIORITY_DOT[priority] ?? PRIORITY_DOT.info
  return <span className={`w-2 h-2 rounded-full shrink-0 ${dot}`} title={priority} />
}

// ── Confidence + source badge ─────────────────────────────────────────────────

function ConfBadge({ confidence, source }: { confidence: string; source: string }) {
  const color =
    confidence === 'high'   ? 'bg-emerald-50 border-emerald-200 text-emerald-700' :
    confidence === 'medium' ? 'bg-yellow-50  border-yellow-200  text-yellow-700'  :
                              'bg-gray-50    border-gray-200    text-gray-400'
  return (
    <span className={`inline-flex items-center text-[10px] font-medium border px-1.5 py-0.5 rounded ${color}`}>
      {source === 'llm' && <span className="mr-0.5">✦</span>}{confidence}
    </span>
  )
}

// ── Related past event badge ──────────────────────────────────────────────────

function RelatedBadge({ related }: { related: RelatedEvent }) {
  return (
    <span
      className="inline-flex items-center gap-1 text-[10px] font-medium bg-violet-50 border border-violet-200 text-violet-700 px-1.5 py-0.5 rounded cursor-help"
      title={`"${related.text}" — ${related.meeting_title} (${related.meeting_date})`}
    >
      ⟳ Revisited
    </span>
  )
}

// ── Confirmed badge ───────────────────────────────────────────────────────────

function ConfirmedBadge() {
  return (
    <span className="inline-flex items-center gap-0.5 text-[10px] font-medium bg-green-50 border border-green-200 text-green-700 px-1.5 py-0.5 rounded">
      ✓ Confirmed
    </span>
  )
}

// ── "For you" badge ───────────────────────────────────────────────────────────

function ForYouBadge() {
  return (
    <span className="inline-flex items-center text-[10px] font-bold bg-red-50 border border-red-300 text-red-700 px-1.5 py-0.5 rounded uppercase tracking-wide">
      For you
    </span>
  )
}

// ── Action item row ───────────────────────────────────────────────────────────

function ActionItemRow({
  item,
  isForMe,
}: {
  item:    ActionItemEvent
  isForMe: boolean
}) {
  return (
    <div className={`flex gap-2.5 py-2 border-b border-gray-100 last:border-0 group
      ${item.priority === 'critical' ? 'bg-red-50/40' : ''}
    `}>
      <div className="flex flex-col items-center gap-1 mt-1">
        <PriorityDot priority={item.priority} />
        {/* Checkbox visual */}
        <div className={`w-3.5 h-3.5 rounded border-2 transition-colors
          ${item.confirmed
            ? 'bg-green-500 border-green-500'
            : 'border-gray-300 group-hover:border-blue-400'}`}
        />
      </div>
      <div className="flex-1 min-w-0">
        <p className={`text-sm leading-snug ${item.confirmed ? 'line-through text-gray-400' : 'text-gray-800'}`}>
          {item.text}
        </p>
        <div className="flex flex-wrap items-center gap-1.5 mt-1">
          {isForMe && <ForYouBadge />}
          {item.confirmed && <ConfirmedBadge />}
          {item.related_to_previous && <RelatedBadge related={item.related_to_previous} />}
          {item.assignee && !isForMe && (
            <span className="text-xs font-medium text-indigo-600 bg-indigo-50 px-1.5 py-0.5 rounded">
              @{item.assignee}
            </span>
          )}
          <ConfBadge confidence={item.confidence} source={item.source} />
          <span className="text-[10px] text-gray-400 font-mono">{formatTime(item.t)}</span>
        </div>
      </div>
    </div>
  )
}

// ── Decision row ──────────────────────────────────────────────────────────────

function DecisionRow({ item }: { item: DecisionEvent }) {
  return (
    <div className="flex gap-2.5 py-2 border-b border-gray-100 last:border-0">
      <div className="flex flex-col items-center gap-1 mt-1">
        <PriorityDot priority={item.priority} />
        <span className="text-purple-500 text-[11px] font-bold leading-none">✓</span>
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm text-gray-800 leading-snug">{item.text}</p>
        <div className="flex flex-wrap items-center gap-1.5 mt-1">
          {item.related_to_previous && <RelatedBadge related={item.related_to_previous} />}
          <ConfBadge confidence={item.confidence} source={item.source} />
          <span className="text-[10px] text-gray-400 font-mono">{formatTime(item.t)}</span>
        </div>
      </div>
    </div>
  )
}

// ── Section wrapper ───────────────────────────────────────────────────────────

function Section({
  title, count, accentClass, children,
}: {
  title: string; count: number; accentClass: string; children: React.ReactNode
}) {
  return (
    <div className="flex flex-col min-h-0">
      <div className={`flex items-center justify-between px-3 py-2 border-b ${accentClass}`}>
        <span className="text-xs font-semibold text-gray-700 uppercase tracking-wide">{title}</span>
        {count > 0 && (
          <span className="text-xs font-bold text-gray-500 bg-white border border-gray-200 rounded-full px-2 py-0.5">
            {count}
          </span>
        )}
      </div>
      <div className="overflow-y-auto px-3" style={{ maxHeight: '300px' }}>
        {children}
      </div>
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

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  actionItems:    ActionItemEvent[]
  decisions:      DecisionEvent[]
  currentUserId?: string
  personalFilter: boolean
  className?:     string
}

export default function LiveIntelligencePanel({
  actionItems,
  decisions,
  currentUserId,
  personalFilter,
  className = '',
}: Props) {
  const filteredActions = personalFilter
    ? actionItems.filter(ai =>
        !currentUserId || ai.target_user_id === currentUserId || ai.priority === 'critical'
      )
    : actionItems

  const filteredDecisions = personalFilter
    ? decisions.filter(d => d.priority !== 'info')
    : decisions

  if (filteredActions.length === 0 && filteredDecisions.length === 0) return null

  const criticalCount = actionItems.filter(
    ai => ai.priority === 'critical' && !ai.confirmed
  ).length

  return (
    <div className={`rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden divide-y divide-gray-100 ${className}`}>
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-2.5 bg-gray-50">
        <span className="text-sm font-semibold text-gray-700">Meeting Intelligence</span>
        <span className="text-[10px] text-gray-400 bg-gray-100 border border-gray-200 rounded px-1.5 py-0.5">
          LIVE
        </span>
        {criticalCount > 0 && (
          <span className="ml-1 text-[10px] font-bold text-white bg-red-500 rounded-full px-2 py-0.5 animate-pulse">
            {criticalCount} critical
          </span>
        )}
        <span className="ml-auto flex items-center gap-3 text-[10px] text-gray-400">
          <span><span className="inline-block w-2 h-2 rounded-full bg-red-500 mr-1" />critical</span>
          <span><span className="inline-block w-2 h-2 rounded-full bg-orange-400 mr-1" />important</span>
          <span>✦ AI-confirmed</span>
        </span>
      </div>

      {/* Two-column layout */}
      <div className="grid grid-cols-1 md:grid-cols-2 divide-y md:divide-y-0 md:divide-x divide-gray-100">
        <Section
          title="Action Items"
          count={filteredActions.length}
          accentClass="bg-blue-50 border-blue-100"
        >
          {filteredActions.length === 0 ? (
            <EmptyState icon="☑️" label="No action items yet" />
          ) : (
            [...filteredActions].reverse().map((item, i) => (
              <ActionItemRow
                key={item.event_id || i}
                item={item}
                isForMe={!!(currentUserId && item.target_user_id === currentUserId)}
              />
            ))
          )}
        </Section>

        <Section
          title="Decisions"
          count={filteredDecisions.length}
          accentClass="bg-purple-50 border-purple-100"
        >
          {filteredDecisions.length === 0 ? (
            <EmptyState icon="⚖️" label="No decisions yet" />
          ) : (
            [...filteredDecisions].reverse().map((item, i) => (
              <DecisionRow key={item.event_id || i} item={item} />
            ))
          )}
        </Section>
      </div>
    </div>
  )
}
