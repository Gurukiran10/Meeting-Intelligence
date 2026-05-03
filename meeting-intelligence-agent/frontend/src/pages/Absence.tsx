import React, { useState } from 'react'
import { useQuery, useMutation } from 'react-query'
import { Link } from 'react-router-dom'
import {
  UserX, AlertTriangle, Info, CheckCircle2, Send,
  ChevronDown, ChevronUp, Calendar, Clock, Users,
  MessageSquare, Zap, ShieldCheck, SkipForward,
} from 'lucide-react'
import { api } from '../lib/api'

// ── Types ────────────────────────────────────────────────────────────────────

interface Meeting {
  id: string
  title: string
  platform: string
  status: string
  scheduled_start: string
  duration_minutes?: number
  attendee_count?: number
  importance?: { score: number; label: string; recommendation: string }
}

interface Highlight { type: string; text: string; context: string; urgency_score?: number }
interface ActionAssigned { task: string; description: string; deadline?: string; priority: string; urgency: string }
interface PriorityItem { type: string; title: string; reason: string; action_required?: string; deadline?: string }
interface CatchUp {
  meeting_id: string
  meeting_title: string
  meeting_date?: string
  personalized_highlights: { mention_count: number; highlights: Highlight[]; summary?: string }
  decisions_affecting_work: { decision: string; impact: string; context: string }[]
  actions_assigned: ActionAssigned[]
  questions_about_projects: { question: string; context: string }[]
  smart_prioritization: { critical: PriorityItem[]; important: PriorityItem[]; fyi: PriorityItem[] }
  skip_recommendation: { recommendation: string; message: string; reasons: string[]; score: number }
}

// ── Sub-components ───────────────────────────────────────────────────────────

const SkipBadge: React.FC<{ rec: string; score: number }> = ({ rec, score }) => {
  const configs: Record<string, { label: string; cls: string }> = {
    safe_to_skip:      { label: 'Safe to skip',    cls: 'bg-green-100 text-green-700 border-green-200' },
    consider_attending:{ label: 'Consider attending', cls: 'bg-yellow-100 text-yellow-700 border-yellow-200' },
    should_attend:     { label: 'Should attend',   cls: 'bg-red-100 text-red-700 border-red-200' },
  }
  const { label, cls } = configs[rec] || { label: rec, cls: 'bg-gray-100 text-gray-600 border-gray-200' }
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full border ${cls}`}>
      <SkipForward className="w-3 h-3" />
      {label} (score: {score})
    </span>
  )
}

const ImportanceBadge: React.FC<{ label?: string }> = ({ label }) => {
  const map: Record<string, string> = {
    critical: 'bg-red-100 text-red-700',
    important: 'bg-orange-100 text-orange-700',
    optional: 'bg-yellow-100 text-yellow-700',
    skip: 'bg-gray-100 text-gray-500',
  }
  if (!label) return null
  return (
    <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${map[label] ?? 'bg-gray-100 text-gray-500'}`}>
      {label.charAt(0).toUpperCase() + label.slice(1)}
    </span>
  )
}

const PrioritySection: React.FC<{
  title: string; icon: React.ReactNode; items: PriorityItem[]; color: string
}> = ({ title, icon, items, color }) => {
  if (!items.length) return null
  return (
    <div className={`rounded-lg border ${color} p-3 mb-2`}>
      <div className="flex items-center gap-2 font-semibold text-sm mb-2">{icon}{title}</div>
      <ul className="space-y-1">
        {items.map((item, i) => (
          <li key={i} className="text-sm">
            <span className="font-medium">{item.title}</span>
            {item.reason && <span className="text-gray-600"> — {item.reason}</span>}
            {item.action_required && (
              <span className="ml-2 text-xs text-blue-600 italic">{item.action_required}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

const CatchUpPanel: React.FC<{ meetingId: string }> = ({ meetingId }) => {
  const { data, isLoading, isError } = useQuery<CatchUp>(
    ['catchup', meetingId],
    () => api.get(`/api/v1/meetings/${meetingId}/catchup`).then(r => r.data),
    { staleTime: 300_000 }
  )

  const sendMutation = useMutation(
    () => api.post(`/api/v1/meetings/${meetingId}/catchup/send`),
    { onSuccess: () => alert('Catch-up sent to your Slack!') }
  )

  if (isLoading) return <div className="p-4 text-sm text-gray-500 animate-pulse">Loading catch-up…</div>
  if (isError || !data) return <div className="p-4 text-sm text-red-500">Could not load catch-up. Meeting may not be transcribed yet.</div>

  const { personalized_highlights: ph, smart_prioritization: sp, actions_assigned, skip_recommendation: sr, decisions_affecting_work, questions_about_projects } = data

  return (
    <div className="p-4 space-y-4 border-t border-gray-100">
      {/* Skip recommendation */}
      {sr && (
        <div className="flex items-start gap-3 p-3 rounded-lg bg-gray-50 border border-gray-200">
          <ShieldCheck className="w-4 h-4 mt-0.5 text-gray-500 shrink-0" />
          <div>
            <p className="text-sm font-medium text-gray-700">{sr.message}</p>
            {sr.reasons.length > 0 && (
              <ul className="mt-1 text-xs text-gray-500 list-disc list-inside">
                {sr.reasons.map((r, i) => <li key={i}>{r}</li>)}
              </ul>
            )}
          </div>
        </div>
      )}

      {/* Personalized mentions */}
      {ph.mention_count > 0 && (
        <div>
          <p className="text-sm font-semibold text-gray-700 mb-1 flex items-center gap-1">
            <MessageSquare className="w-4 h-4 text-violet-500" />
            Mentioned {ph.mention_count} time{ph.mention_count !== 1 ? 's' : ''}
          </p>
          <div className="space-y-1">
            {ph.highlights.slice(0, 3).map((h, i) => (
              <p key={i} className="text-xs text-gray-600 bg-violet-50 border border-violet-100 rounded px-2 py-1">
                <span className="font-medium capitalize">{h.type.replace(/_/g, ' ')}:</span> {h.context || h.text}
              </p>
            ))}
          </div>
        </div>
      )}

      {/* Priority sections */}
      <PrioritySection
        title="Critical — Immediate Action"
        icon={<AlertTriangle className="w-4 h-4 text-red-500" />}
        items={sp.critical}
        color="border-red-200 bg-red-50"
      />
      <PrioritySection
        title="Important — Review within 24h"
        icon={<Zap className="w-4 h-4 text-orange-500" />}
        items={sp.important}
        color="border-orange-200 bg-orange-50"
      />

      {/* Actions assigned */}
      {actions_assigned.length > 0 && (
        <div>
          <p className="text-sm font-semibold text-gray-700 mb-1">Actions assigned to you</p>
          <ul className="space-y-1">
            {actions_assigned.map((a, i) => (
              <li key={i} className="text-sm flex items-start gap-2 bg-blue-50 border border-blue-100 rounded px-2 py-1">
                <CheckCircle2 className="w-4 h-4 text-blue-500 mt-0.5 shrink-0" />
                <span>
                  <span className="font-medium">{a.task}</span>
                  {a.deadline && <span className="text-xs text-gray-500 ml-1">· Due {a.deadline.slice(0, 10)}</span>}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Questions */}
      {questions_about_projects.length > 0 && (
        <div>
          <p className="text-sm font-semibold text-gray-700 mb-1">Questions about your work</p>
          {questions_about_projects.slice(0, 3).map((q, i) => (
            <p key={i} className="text-xs text-amber-700 bg-amber-50 border border-amber-100 rounded px-2 py-1 mb-1">
              {q.question}
            </p>
          ))}
        </div>
      )}

      {/* FYI */}
      {sp.fyi.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-gray-500 hover:text-gray-700">
            {sp.fyi.length} FYI items (background awareness)
          </summary>
          <ul className="mt-1 text-xs text-gray-500 list-disc list-inside space-y-0.5">
            {sp.fyi.map((f, i) => <li key={i}>{f.title} — {f.reason}</li>)}
          </ul>
        </details>
      )}

      {/* Send to Slack */}
      <div className="flex items-center gap-2 pt-2 border-t border-gray-100">
        <button
          onClick={() => sendMutation.mutate()}
          disabled={sendMutation.isLoading}
          className="inline-flex items-center gap-2 text-sm bg-green-600 hover:bg-green-700 text-white px-3 py-1.5 rounded-lg disabled:opacity-50"
        >
          <Send className="w-4 h-4" />
          {sendMutation.isLoading ? 'Sending…' : 'Send to Slack'}
        </button>
        <Link
          to={`/meetings/${meetingId}`}
          className="text-sm text-blue-600 hover:underline"
        >
          View full meeting →
        </Link>
      </div>
    </div>
  )
}

// ── Main page ────────────────────────────────────────────────────────────────

const Absence: React.FC = () => {
  const [expanded, setExpanded] = useState<string | null>(null)

  const { data: meetings, isLoading } = useQuery<Meeting[]>(
    'all-meetings-absence',
    () => api.get('/api/v1/meetings/', { params: { limit: 100 } }).then(r => r.data),
    { staleTime: 60_000 }
  )

  // Missed = completed meetings where the importance score data is available
  const completed = (meetings || []).filter(m => m.status === 'completed')

  // Upcoming = scheduled meetings, show skip recommendation
  const upcoming = (meetings || []).filter(m => m.status === 'scheduled')

  const toggle = (id: string) => setExpanded(prev => prev === id ? null : id)

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
          <UserX className="w-6 h-6 text-violet-600" />
          Absence & Catch-Up
        </h1>
        <p className="text-gray-500 mt-1">
          Meetings you missed, personalised catch-ups, and skip recommendations for upcoming meetings.
        </p>
      </div>

      {/* Upcoming meetings — skip recommendations */}
      {upcoming.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold text-gray-800 mb-3 flex items-center gap-2">
            <Calendar className="w-5 h-5 text-blue-500" />
            Upcoming — Should you attend?
          </h2>
          <div className="space-y-3">
            {upcoming.map(m => (
              <div key={m.id} className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <Link to={`/meetings/${m.id}`} className="font-medium text-gray-900 hover:text-blue-600 truncate block">
                      {m.title}
                    </Link>
                    <div className="flex flex-wrap items-center gap-2 mt-1">
                      <span className="text-xs text-gray-400 flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {m.scheduled_start ? new Date(m.scheduled_start).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }) : '—'}
                      </span>
                      {m.attendee_count && (
                        <span className="text-xs text-gray-400 flex items-center gap-1">
                          <Users className="w-3 h-3" />{m.attendee_count}
                        </span>
                      )}
                      <ImportanceBadge label={m.importance?.label} />
                    </div>
                  </div>
                  {m.importance && (
                    <SkipBadge rec={m.importance.recommendation} score={m.importance.score} />
                  )}
                </div>
                {m.importance?.recommendation === 'safe_to_skip' && (
                  <p className="mt-2 text-xs text-green-700 bg-green-50 border border-green-100 rounded px-2 py-1">
                    This meeting looks like a status update — safe to skip and read the summary afterwards.
                  </p>
                )}
                {m.importance?.recommendation === 'should_attend' && (
                  <p className="mt-2 text-xs text-red-700 bg-red-50 border border-red-100 rounded px-2 py-1">
                    Decisions or approvals likely needed from you — recommend attending.
                  </p>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Completed meetings — catch-up */}
      <section>
        <h2 className="text-lg font-semibold text-gray-800 mb-3 flex items-center gap-2">
          <Info className="w-5 h-5 text-violet-500" />
          Completed — Get Caught Up
        </h2>

        {isLoading && (
          <div className="space-y-3">
            {[1, 2, 3].map(i => (
              <div key={i} className="h-20 bg-gray-100 animate-pulse rounded-xl" />
            ))}
          </div>
        )}

        {!isLoading && completed.length === 0 && (
          <div className="text-center py-12 text-gray-400">
            <CheckCircle2 className="w-10 h-10 mx-auto mb-2 text-gray-300" />
            <p>No completed meetings yet.</p>
          </div>
        )}

        <div className="space-y-3">
          {completed.map(m => (
            <div key={m.id} className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
              {/* Meeting row */}
              <div className="flex items-start justify-between gap-3 p-4">
                <div className="flex-1 min-w-0">
                  <Link to={`/meetings/${m.id}`} className="font-medium text-gray-900 hover:text-blue-600 truncate block">
                    {m.title}
                  </Link>
                  <div className="flex flex-wrap items-center gap-2 mt-1">
                    <span className="text-xs text-gray-400 capitalize">{m.platform}</span>
                    <span className="text-xs text-gray-400">·</span>
                    <span className="text-xs text-gray-400 flex items-center gap-1">
                      <Clock className="w-3 h-3" />
                      {m.scheduled_start ? new Date(m.scheduled_start).toLocaleDateString(undefined, { dateStyle: 'medium' }) : '—'}
                    </span>
                    {m.duration_minutes && (
                      <span className="text-xs text-gray-400">{m.duration_minutes} min</span>
                    )}
                    <ImportanceBadge label={m.importance?.label} />
                  </div>
                </div>

                <button
                  onClick={() => toggle(m.id)}
                  className="inline-flex items-center gap-1.5 text-sm text-violet-700 bg-violet-50 hover:bg-violet-100 border border-violet-200 px-3 py-1.5 rounded-lg whitespace-nowrap"
                >
                  {expanded === m.id ? (
                    <><ChevronUp className="w-4 h-4" /> Hide</>
                  ) : (
                    <><ChevronDown className="w-4 h-4" /> Get Catch-Up</>
                  )}
                </button>
              </div>

              {/* Catch-up panel */}
              {expanded === m.id && <CatchUpPanel meetingId={m.id} />}
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}

export default Absence
