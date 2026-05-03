import React, { useEffect, useMemo, useState } from 'react'

const toLocalDate = (dt: string) => new Date(dt.endsWith('Z') || dt.includes('+') ? dt : dt + 'Z')
import { useQuery } from 'react-query'
import { Link, useParams } from 'react-router-dom'
import { AlertCircle, Check, Clock3, Copy, ExternalLink, FileText, MessageSquareQuote, Mic, UploadCloud, ListChecks, Users2, GitBranch } from 'lucide-react'

import { api } from '../lib/api'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

type ImportanceScore = {
  label: 'critical' | 'important' | 'optional' | 'skip'
  score: number
  emoji: string
  recommendation: string
  reasons: string[]
  warnings: string[]
}

const IMPORTANCE_BG: Record<string, string> = {
  critical: 'bg-red-50 border-red-200',
  important: 'bg-orange-50 border-orange-200',
  optional: 'bg-yellow-50 border-yellow-200',
  skip: 'bg-gray-50 border-gray-200',
}
const IMPORTANCE_TEXT: Record<string, string> = {
  critical: 'text-red-700',
  important: 'text-orange-700',
  optional: 'text-yellow-700',
  skip: 'text-gray-600',
}

const ImportanceCard: React.FC<{ importance: ImportanceScore }> = ({ importance }) => {
  const bg = IMPORTANCE_BG[importance.label] ?? IMPORTANCE_BG.optional
  const text = IMPORTANCE_TEXT[importance.label] ?? IMPORTANCE_TEXT.optional
  return (
    <Card className={`border shadow-sm ${bg}`}>
      <CardContent className="pt-4 pb-4">
        <div className="flex items-center justify-between mb-2">
          <span className={`text-base font-semibold ${text}`}>
            {importance.emoji} {importance.label.charAt(0).toUpperCase() + importance.label.slice(1)} Meeting
            <span className="ml-2 text-xs font-normal opacity-70">({importance.score}/100)</span>
          </span>
        </div>
        <p className={`text-sm mb-3 ${text}`}>{importance.recommendation}</p>
        {importance.reasons.length > 0 && (
          <ul className="space-y-1 mb-2">
            {importance.reasons.map((r, i) => (
              <li key={i} className="text-xs text-gray-600 flex items-start gap-1.5">
                <span className="text-green-500 mt-0.5">✓</span>{r}
              </li>
            ))}
          </ul>
        )}
        {importance.warnings.length > 0 && (
          <ul className="space-y-1">
            {importance.warnings.map((w, i) => (
              <li key={i} className="text-xs text-gray-500 flex items-start gap-1.5">
                <span className="text-yellow-500 mt-0.5">⚠</span>{w}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

type ParticipationData = {
  total_invited: number
  total_speakers: number
  silent_count: number
  total_duration_seconds: number
  speakers: { label: string; seconds: number; percent: number }[]
  silent_attendees: { user_id: string; name: string; email: string }[]
  recommendation: string
  effectiveness_score: number
}

const ParticipationCard: React.FC<{ data: ParticipationData }> = ({ data }) => {
  const score = data.effectiveness_score
  const scoreColour = score >= 70 ? 'text-green-600' : score >= 40 ? 'text-yellow-600' : 'text-red-600'
  const barColour = score >= 70 ? 'bg-green-500' : score >= 40 ? 'bg-yellow-400' : 'bg-red-400'

  return (
    <Card className="border-slate-200/60 shadow-sm">
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-lg font-semibold text-gray-900">Meeting Effectiveness</CardTitle>
            <CardDescription>Who spoke vs who was invited</CardDescription>
          </div>
          <div className="text-right">
            <span className={`text-3xl font-bold ${scoreColour}`}>{score}</span>
            <span className="text-gray-400 text-sm">/100</span>
          </div>
        </div>
        {/* Score bar */}
        <div className="mt-2 h-2 w-full bg-gray-100 rounded-full overflow-hidden">
          <div className={`h-full rounded-full ${barColour}`} style={{ width: `${score}%` }} />
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Summary stats */}
        <div className="grid grid-cols-3 gap-3 text-center">
          <div className="rounded-lg bg-slate-50 p-3">
            <p className="text-2xl font-bold text-gray-900">{data.total_invited}</p>
            <p className="text-xs text-gray-500">Invited</p>
          </div>
          <div className="rounded-lg bg-slate-50 p-3">
            <p className="text-2xl font-bold text-green-600">{data.total_speakers}</p>
            <p className="text-xs text-gray-500">Spoke</p>
          </div>
          <div className="rounded-lg bg-slate-50 p-3">
            <p className={`text-2xl font-bold ${data.silent_count > 0 ? 'text-orange-500' : 'text-gray-400'}`}>{data.silent_count}</p>
            <p className="text-xs text-gray-500">Silent</p>
          </div>
        </div>

        {/* Speaker breakdown */}
        {data.speakers.length > 0 && (
          <div>
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Speaking Time</p>
            <div className="space-y-2">
              {data.speakers.map((s) => (
                <div key={s.label}>
                  <div className="flex justify-between text-xs text-gray-600 mb-0.5">
                    <span className="font-medium">{s.label}</span>
                    <span>{Math.floor(s.seconds / 60)}m {Math.round(s.seconds % 60)}s ({s.percent}%)</span>
                  </div>
                  <div className="h-1.5 w-full bg-gray-100 rounded-full overflow-hidden">
                    <div className="h-full bg-indigo-500 rounded-full" style={{ width: `${s.percent}%` }} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Silent attendees */}
        {data.silent_attendees.length > 0 && (
          <div>
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Did Not Speak</p>
            <div className="flex flex-wrap gap-1.5">
              {data.silent_attendees.map((a) => (
                <span key={a.user_id} className="px-2 py-0.5 bg-orange-50 text-orange-700 text-xs rounded-full border border-orange-100">
                  {a.name}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Recommendation */}
        <div className="rounded-lg bg-blue-50 border border-blue-100 px-3 py-2">
          <p className="text-xs text-blue-700">{data.recommendation}</p>
        </div>
      </CardContent>
    </Card>
  )
}

const PRIORITY_DOT: Record<string, string> = {
  high: 'bg-red-400',
  urgent: 'bg-red-600',
  medium: 'bg-amber-400',
  low: 'bg-green-400',
}

const PrepCard: React.FC<{ data: any }> = ({ data }) => {
  const suggestions: any[] = data?.agenda_suggestions ?? []
  const attendeeOpts = data?.attendee_optimization ?? {}
  const hasAgenda: boolean = data?.has_agenda ?? false

  return (
    <Card className="border-blue-100 bg-blue-50/30 shadow-sm">
      <CardHeader className="pb-2">
        <CardTitle className="text-base font-semibold text-gray-900 flex items-center gap-2">
          <ListChecks size={16} className="text-blue-500" />
          Meeting Prep
        </CardTitle>
        <CardDescription>
          {hasAgenda ? 'Agenda set · Suggested follow-ups below' : 'No agenda set yet — review suggestions'}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {suggestions.length > 0 && (
          <div>
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Agenda Suggestions</p>
            <ul className="space-y-2">
              {suggestions.map((s: any, i: number) => (
                <li key={i} className="flex items-start gap-2 text-sm">
                  <span className={`mt-1.5 h-2 w-2 rounded-full shrink-0 ${PRIORITY_DOT[s.priority] ?? 'bg-gray-300'}`} />
                  <div>
                    <p className="text-gray-800">{s.text}</p>
                    {s.detail && <p className="text-xs text-gray-400 mt-0.5">{s.detail}</p>}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}

        {(attendeeOpts.add?.length > 0 || attendeeOpts.remove?.length > 0) && (
          <div className="border-t border-blue-100 pt-3">
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2 flex items-center gap-1">
              <Users2 size={12} /> Attendee Suggestions
              <span className="ml-1 normal-case font-normal text-gray-400">({attendeeOpts.current_count} → {attendeeOpts.suggested_count})</span>
            </p>
            {attendeeOpts.add?.length > 0 && (
              <div className="mb-2">
                <p className="text-xs text-green-700 font-medium mb-1">Add</p>
                {attendeeOpts.add.map((u: any, i: number) => (
                  <span key={i} className="inline-block mr-1 mb-1 text-xs bg-green-100 text-green-800 px-2 py-0.5 rounded-full">{u.name}</span>
                ))}
              </div>
            )}
            {attendeeOpts.remove?.length > 0 && (
              <div>
                <p className="text-xs text-amber-700 font-medium mb-1">Consider removing</p>
                {attendeeOpts.remove.map((u: any, i: number) => (
                  <span key={i} className="inline-block mr-1 mb-1 text-xs bg-amber-100 text-amber-800 px-2 py-0.5 rounded-full">{u.name}</span>
                ))}
              </div>
            )}
          </div>
        )}

        {suggestions.length === 0 && !attendeeOpts.add?.length && !attendeeOpts.remove?.length && (
          <p className="text-sm text-gray-400">No prep suggestions — meeting looks good to go.</p>
        )}
      </CardContent>
    </Card>
  )
}

const DecisionThreadCard: React.FC<{ threads: any[] }> = ({ threads }) => {
  if (!threads?.length) return null
  return (
    <Card className="border-violet-100 bg-violet-50/20 shadow-sm">
      <CardHeader className="pb-2">
        <CardTitle className="text-base font-semibold text-gray-900 flex items-center gap-2">
          <GitBranch size={16} className="text-violet-500" />
          Decision Evolution
        </CardTitle>
        <CardDescription>How decisions from this meeting relate to past discussions</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {threads.slice(0, 5).map((thread: any, i: number) => (
          <div key={i} className="rounded-lg border border-violet-100 bg-white p-3">
            <div className="flex items-start justify-between gap-2 mb-1">
              <p className="text-sm font-medium text-gray-800 leading-snug">{thread.topic}</p>
              {thread.revisited && (
                <span className="shrink-0 text-xs bg-violet-100 text-violet-700 px-1.5 py-0.5 rounded">revisited</span>
              )}
            </div>
            <div className="flex flex-wrap gap-1 mt-2">
              {thread.meetings?.map((m: any, j: number) => (
                <span key={j} className="text-xs text-gray-400 bg-gray-50 border border-gray-100 rounded px-1.5 py-0.5">
                  {m.meeting_title} · {m.date ? new Date(m.date).toLocaleDateString() : '—'}
                </span>
              ))}
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  )
}

const MeetingDetail: React.FC = () => {
  const { id } = useParams()
  const [isUploading, setIsUploading] = useState(false)
  const [uploadMessage, setUploadMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [copied, setCopied] = useState(false)

  const copyMeetingUrl = (url: string) => {
    navigator.clipboard.writeText(url)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const { data: meeting, isLoading, refetch } = useQuery(
    ['meeting', id],
    async () => {
      const response = await api.get(`/api/v1/meetings/${id}`)
      return response.data
    },
    {
      refetchInterval: (data: any) => {
        if (!data) return false
        const status = String(data.status || '').toLowerCase()
        const transcriptionStatus = String(data.transcription_status || '').toLowerCase()
        const analysisStatus = String(data.analysis_status || '').toLowerCase()
        const isProcessing =
          ['transcribing', 'processing', 'in_progress'].includes(status) ||
          transcriptionStatus === 'processing' ||
          analysisStatus === 'processing'
        return isProcessing ? 2500 : false
      },
    },
  )

  const { data: allActionItems } = useQuery(
    ['meeting-action-items', id],
    async () => {
      const response = await api.get('/api/v1/action-items/')
      return response.data
    },
    { enabled: Boolean(id) },
  )

  const { data: allMentions } = useQuery(
    ['meeting-mentions', id],
    async () => {
      const response = await api.get('/api/v1/mentions/')
      return response.data
    },
    { enabled: Boolean(id) },
  )

  const { data: participation } = useQuery(
    ['meeting-participation', id],
    async () => {
      const response = await api.get(`/api/v1/meetings/${id}/participation`)
      return response.data
    },
    { enabled: Boolean(id) && meeting?.status === 'completed' },
  )

  const { data: preBrief } = useQuery(
    ['meeting-pre-brief', id],
    async () => {
      const response = await api.get(`/api/v1/meetings/${id}/pre-brief`)
      return response.data
    },
    { enabled: Boolean(id) },
  )

  const { data: prepData } = useQuery(
    ['meeting-prep', id],
    async () => {
      const response = await api.get(`/api/v1/meetings/${id}/prep`)
      return response.data
    },
    { enabled: Boolean(id) && meeting?.status === 'scheduled' },
  )

  const { data: decisionGraph } = useQuery(
    ['decision-graph'],
    async () => {
      const response = await api.get('/api/v1/decisions/graph?days=90')
      return response.data
    },
    { enabled: Boolean(id) },
  )

  const { data: transcriptSegments } = useQuery(
    ['meeting-transcript', id],
    async () => {
      const response = await api.get(`/api/v1/transcripts/?meeting_id=${id}`)
      return response.data as Array<{
        id: string
        segment_number: number
        speaker_id: string | null
        text: string
        start_time: number | null
        end_time: number | null
        confidence: number | null
      }>
    },
    { enabled: Boolean(id) },
  )

  const meetingActionItems = useMemo(() => {
    // Prefer inline action items from API (meeting-scoped, not user-scoped)
    if (Array.isArray(meeting?.action_items) && meeting.action_items.length > 0) {
      return meeting.action_items
    }
    // Fallback to filtered global list
    return (allActionItems || []).filter((item: any) => item.meeting_id === id)
  }, [meeting?.action_items, allActionItems, id])

  const meetingMentions = useMemo(() => {
    // Prefer inline mentions from API (meeting-scoped, not user-scoped)
    if (Array.isArray(meeting?.mentions) && meeting.mentions.length > 0) {
      return meeting.mentions
    }
    // Fallback to filtered global list
    return (allMentions || []).filter((mention: any) => mention.meeting_id === id)
  }, [meeting?.mentions, allMentions, id])

  const decisions = Array.isArray(meeting?.key_decisions) ? meeting.key_decisions : []
  const topics = Array.isArray(meeting?.discussion_topics) ? meeting.discussion_topics : []
  const briefImportance = String(preBrief?.importance || 'optional').toLowerCase()
  const importanceVariant = briefImportance === 'critical' ? 'destructive' : briefImportance === 'important' ? 'secondary' : 'outline'
  const relevantMentions = Array.isArray(preBrief?.user_preparation?.relevant_mentions) ? preBrief.user_preparation.relevant_mentions : []
  const pendingTasks = Array.isArray(preBrief?.user_preparation?.pending_tasks) ? preBrief.user_preparation.pending_tasks : []
  const expectedQuestions = Array.isArray(preBrief?.user_preparation?.expected_questions) ? preBrief.user_preparation.expected_questions : []
  const recentDevelopments = Array.isArray(preBrief?.recent_developments) ? preBrief.recent_developments : []
  const suggestedPoints = Array.isArray(preBrief?.suggested_points) ? preBrief.suggested_points : []

  const processingText = useMemo(() => {
    if (!meeting) return ''
    const status = String(meeting.status || '').toLowerCase()
    const transcriptionStatus = String(meeting.transcription_status || '').toLowerCase()
    const analysisStatus = String(meeting.analysis_status || '').toLowerCase()

    if (transcriptionStatus === 'processing' || status === 'transcribing') {
      return 'Recording uploaded. Transcription is in progress...'
    }
    if (analysisStatus === 'processing' || status === 'processing' || status === 'in_progress') {
      return 'Transcription completed. AI analysis is in progress...'
    }
    if (status === 'failed' || transcriptionStatus === 'failed' || analysisStatus === 'failed') {
      return 'Processing failed. Please upload again.'
    }
    return ''
  }, [meeting])

  useEffect(() => {
    if (!meeting || !uploadMessage || uploadMessage.type !== 'success') return

    const status = String(meeting.status || '').toLowerCase()
    const transcriptionStatus = String(meeting.transcription_status || '').toLowerCase()
    const analysisStatus = String(meeting.analysis_status || '').toLowerCase()

    const isProcessing =
      ['transcribing', 'processing', 'in_progress'].includes(status) ||
      transcriptionStatus === 'processing' ||
      analysisStatus === 'processing'

    const isFailed =
      status === 'failed' ||
      transcriptionStatus === 'failed' ||
      analysisStatus === 'failed'

    if (isFailed) {
      setUploadMessage({ type: 'error', text: 'Processing failed. Please upload again.' })
      return
    }

    if (!isProcessing && uploadMessage.text === 'Upload successful. Processing started.') {
      setUploadMessage({ type: 'success', text: 'Processing complete.' })
    }
  }, [meeting, uploadMessage])

  const handleUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file || !id) return

    const formData = new FormData()
    formData.append('file', file)

    try {
      setIsUploading(true)
      setUploadMessage(null)
      await api.post(`/api/v1/meetings/${id}/upload`, formData, { timeout: 30000 })
      setUploadMessage({ type: 'success', text: 'Upload successful. Processing started.' })
      await refetch()
    } catch (err: any) {
      const message = err?.response?.data?.detail || 'Upload failed. Please try again.'
      setUploadMessage({ type: 'error', text: message })
    } finally {
      setIsUploading(false)
      event.target.value = ''
    }
  }

  if (isLoading) {
    return <div className="text-gray-600">Loading meeting...</div>
  }

  if (!meeting) {
    return <div className="text-red-600">Meeting not found.</div>
  }

  return (
    <div className="space-y-6">
      <Card className="border-slate-200/60 shadow-sm">
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <CardTitle className="text-3xl font-bold text-gray-900">{meeting.title}</CardTitle>
              <CardDescription className="mt-2 text-base text-gray-600">
                {meeting.description || 'No description provided.'}
              </CardDescription>
            </div>
            <Badge variant={meeting.status === 'completed' ? 'secondary' : 'outline'} className="capitalize">
              {meeting.status}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="pt-0">
          {processingText ? (
            <div className={`mb-5 rounded-lg px-4 py-3 text-sm ${processingText.includes('failed') ? 'bg-red-50 text-red-700' : 'bg-blue-50 text-blue-700'}`}>
              {processingText}
            </div>
          ) : null}
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4 text-sm">
            <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-4">
              <p className="text-gray-500">Platform</p>
              <p className="font-medium text-gray-900 capitalize">{meeting.platform}</p>
            </div>
            <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-4">
              <p className="text-gray-500">Status</p>
              <p className="font-medium text-gray-900 capitalize">{meeting.status}</p>
            </div>
            <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-4">
              <p className="text-gray-500">Scheduled Start</p>
              <p className="font-medium text-gray-900">{toLocalDate(meeting.scheduled_start).toLocaleString()}</p>
            </div>
            <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-4">
              <p className="text-gray-500">Scheduled End</p>
              <p className="font-medium text-gray-900">{toLocalDate(meeting.scheduled_end).toLocaleString()}</p>
            </div>
          </div>

          {meeting.meeting_url && (
            <div className="mt-4 flex items-center gap-3 rounded-xl border border-blue-100 bg-blue-50/60 px-4 py-3">
              <ExternalLink className="w-4 h-4 text-blue-500 shrink-0" />
              <span className="flex-1 text-sm font-mono text-blue-800 truncate">{meeting.meeting_url}</span>
              <button
                onClick={() => copyMeetingUrl(meeting.meeting_url)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-blue-200 bg-white text-xs font-medium text-blue-700 hover:bg-blue-50 transition-colors shrink-0"
              >
                {copied ? <Check className="w-3.5 h-3.5 text-green-500" /> : <Copy className="w-3.5 h-3.5" />}
                {copied ? 'Copied!' : 'Copy'}
              </button>
              <a
                href={meeting.meeting_url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-600 text-white text-xs font-medium hover:bg-blue-700 transition-colors shrink-0"
              >
                <ExternalLink className="w-3.5 h-3.5" />
                Join
              </a>
            </div>
          )}
        </CardContent>
      </Card>

      {meeting.importance && <ImportanceCard importance={meeting.importance} />}
      {participation && meeting?.status === 'completed' && <ParticipationCard data={participation} />}
      {prepData && meeting?.status === 'scheduled' && <PrepCard data={prepData} />}
      {decisionGraph?.threads?.length > 0 && <DecisionThreadCard threads={decisionGraph.threads} />}

      <Card className="border-slate-200/60 shadow-sm">
        <CardHeader>
          <CardTitle className="text-lg font-semibold text-gray-900">Recording Upload</CardTitle>
          <CardDescription>Upload audio or video to trigger transcription and analysis.</CardDescription>
        </CardHeader>
        <CardContent>
          <label className={`inline-flex items-center px-4 py-2 bg-primary-600 text-white rounded-lg cursor-pointer ${isUploading ? 'opacity-60 cursor-not-allowed' : 'hover:bg-primary-700'}`}>
            <UploadCloud className="w-5 h-5 mr-2" />
            {isUploading ? 'Uploading...' : 'Upload Recording'}
            <input type="file" className="hidden" onChange={handleUpload} disabled={isUploading} />
          </label>
          {uploadMessage && (
            <div className={`mt-3 text-sm ${uploadMessage.type === 'success' ? 'text-green-700' : 'text-red-600'}`}>
              {uploadMessage.text}
            </div>
          )}
        </CardContent>
      </Card>

      {meeting.has_recording && (
        <Card className="border-slate-200/60 shadow-sm">
          <CardHeader>
            <div className="flex items-center gap-3">
              <Mic className="h-5 w-5 text-green-600" />
              <div>
                <CardTitle className="text-lg font-semibold text-gray-900">Recording</CardTitle>
                <CardDescription>Listen to the meeting audio.</CardDescription>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <audio
              controls
              className="w-full"
              src={`/api/v1/meetings/${id}/recording`}
            >
              Your browser does not support the audio element.
            </audio>
          </CardContent>
        </Card>
      )}

      <Card className="border-slate-200/60 shadow-sm">
        <CardHeader>
          <div className="flex items-center gap-3">
            <MessageSquareQuote className="h-5 w-5 text-blue-600" />
            <div>
              <CardTitle className="text-lg font-semibold text-gray-900">Summary</CardTitle>
              <CardDescription>Executive recap generated from the recording.</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-gray-700 whitespace-pre-wrap">{meeting.summary || 'Summary will appear after processing.'}</p>
        </CardContent>
      </Card>

      <Card className="border-slate-200/60 shadow-sm">
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div className="flex items-center gap-3">
            <FileText className="h-5 w-5 text-green-600" />
            <div>
              <CardTitle className="text-lg font-semibold text-gray-900">Transcript</CardTitle>
              <CardDescription>Full text of what was said in the meeting.</CardDescription>
            </div>
          </div>
          {transcriptSegments && transcriptSegments.length > 0 && (
            <span className="text-sm text-gray-500">{transcriptSegments.length} segment{transcriptSegments.length === 1 ? '' : 's'}</span>
          )}
        </CardHeader>
        <CardContent>
          {!transcriptSegments || transcriptSegments.length === 0 ? (
            <p className="text-sm text-gray-500">
              {meeting.transcription_status === 'completed'
                ? 'No transcript segments saved for this meeting.'
                : meeting.transcription_status === 'processing'
                ? 'Transcription in progress…'
                : 'Transcript will appear here after the meeting is processed.'}
            </p>
          ) : (
            <div className="space-y-0 divide-y divide-gray-100 max-h-[500px] overflow-y-auto rounded-lg border border-gray-100">
              {transcriptSegments.map((seg) => {
                const formatTime = (s: number | null) => {
                  if (s == null) return ''
                  const m = Math.floor(s / 60)
                  const sec = Math.floor(s % 60)
                  return `${m}:${String(sec).padStart(2, '0')}`
                }
                const speaker = seg.speaker_id || 'Speaker'
                return (
                  <div key={seg.id} className="flex gap-4 px-4 py-3 hover:bg-slate-50">
                    <div className="w-20 shrink-0 text-right">
                      <span className="text-xs text-gray-400 font-mono">{formatTime(seg.start_time)}</span>
                    </div>
                    <div className="flex-1 min-w-0">
                      <span className="text-xs font-semibold text-primary-600 uppercase tracking-wide mr-2">{speaker}</span>
                      <span className="text-sm text-gray-800">{seg.text}</span>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>

      <Card className="border-slate-200/60 shadow-sm">
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle className="text-lg font-semibold text-gray-900">Pre-Meeting Brief</CardTitle>
              <CardDescription>What you should prepare before this meeting starts.</CardDescription>
            </div>
            <Badge variant={importanceVariant as 'destructive' | 'secondary' | 'outline'} className="capitalize">
              {briefImportance}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-4">
              <p className="text-sm font-semibold text-slate-900">Preparation Checklist</p>
              <ul className="mt-3 space-y-2 text-sm text-slate-600">
                <li className="flex items-start gap-2">
                  <span className="mt-1 h-2 w-2 rounded-full bg-blue-500" />
                  <span>Review {pendingTasks.length} pending task{pendingTasks.length === 1 ? '' : 's'} tied to your work.</span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="mt-1 h-2 w-2 rounded-full bg-amber-500" />
                  <span>Look over {relevantMentions.length} recent mention{relevantMentions.length === 1 ? '' : 's'} that may come up.</span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="mt-1 h-2 w-2 rounded-full bg-emerald-500" />
                  <span>Prepare {suggestedPoints.length || expectedQuestions.length ? 'talking points and answers' : 'a short update on your current work'}.</span>
                </li>
              </ul>
            </div>

            <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-4">
              <p className="text-sm font-semibold text-slate-900">Meeting Context</p>
              <div className="mt-3 space-y-3 text-sm text-slate-600">
                <div>
                  <p className="text-xs uppercase tracking-wide text-slate-400">Agenda</p>
                  <p className="mt-1">{preBrief?.meeting_context?.agenda || meeting.description || 'Agenda not provided yet.'}</p>
                </div>
                <div>
                  <p className="text-xs uppercase tracking-wide text-slate-400">Attendees</p>
                  <p className="mt-1">{(preBrief?.meeting_context?.attendees || []).slice(0, 6).join(', ') || 'Attendees will appear here once added.'}</p>
                </div>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <div>
              <h3 className="text-sm font-semibold text-slate-900">Tasks to Complete</h3>
              <div className="mt-3 space-y-3">
                {pendingTasks.length ? pendingTasks.map((task: any) => (
                  <div key={task.id} className="rounded-lg border border-slate-200 p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="font-medium text-slate-900">{task.title}</p>
                        <p className="mt-1 text-sm text-slate-600">{task.description || 'No description provided.'}</p>
                      </div>
                      <Badge variant="outline" className="capitalize">{task.status}</Badge>
                    </div>
                    <div className="mt-2 text-xs text-slate-500">
                      Priority {task.priority || 'medium'}{task.due_date ? ` • Due ${toLocalDate(task.due_date).toLocaleString()}` : ''}
                    </div>
                  </div>
                )) : (
                  <p className="rounded-lg border border-dashed border-slate-200 p-4 text-sm text-slate-500">No open tasks are blocking you right now.</p>
                )}
              </div>
            </div>

            <div>
              <h3 className="text-sm font-semibold text-slate-900">Suggested Talking Points</h3>
              <div className="mt-3 space-y-3">
                {suggestedPoints.length ? (
                  suggestedPoints.map((point: string, index: number) => (
                    <div key={`${point}-${index}`} className="rounded-lg border border-slate-200 p-4 text-sm text-slate-700">
                      {point}
                    </div>
                  ))
                ) : (
                  <p className="rounded-lg border border-dashed border-slate-200 p-4 text-sm text-slate-500">Suggested points will appear when enough context is available.</p>
                )}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <div>
              <h3 className="text-sm font-semibold text-slate-900">Expected Questions</h3>
              <div className="mt-3 space-y-3">
                {expectedQuestions.length ? (
                  expectedQuestions.map((question: string, index: number) => (
                    <div key={`${question}-${index}`} className="rounded-lg border border-slate-200 p-4 text-sm text-slate-700">
                      {question}
                    </div>
                  ))
                ) : (
                  <p className="rounded-lg border border-dashed border-slate-200 p-4 text-sm text-slate-500">No specific questions predicted for you yet.</p>
                )}
              </div>
            </div>

            <div>
              <h3 className="text-sm font-semibold text-slate-900">Recent Mentions</h3>
              <div className="mt-3 space-y-3">
                {relevantMentions.length ? (
                  relevantMentions.map((mention: any) => (
                    <div key={mention.id} className="rounded-lg border border-slate-200 p-4">
                      <p className="text-sm font-medium text-slate-900">{mention.text}</p>
                      <div className="mt-2 flex items-center gap-2 text-xs text-slate-500">
                        <span className="capitalize">{mention.type}</span>
                        {mention.confidence ? <span>• Confidence {Math.round(Number(mention.confidence) * 100)}%</span> : null}
                      </div>
                    </div>
                  ))
                ) : (
                  <p className="rounded-lg border border-dashed border-slate-200 p-4 text-sm text-slate-500">No relevant mentions found for this meeting.</p>
                )}
              </div>
            </div>
          </div>

          <div>
            <h3 className="text-sm font-semibold text-slate-900">Recent Developments</h3>
            <div className="mt-3 space-y-3">
              {recentDevelopments.length ? (
                recentDevelopments.map((development: any, index: number) => (
                  <div key={`${development.title}-${index}`} className="rounded-lg border border-slate-200 p-4">
                    <div className="flex items-center gap-2 text-xs text-slate-500">
                      <AlertCircle className="h-3.5 w-3.5" />
                      <span className="uppercase tracking-wide">{development.type}</span>
                    </div>
                    <p className="mt-2 font-medium text-slate-900">{development.title}</p>
                    <p className="mt-1 text-sm text-slate-600">{development.summary}</p>
                  </div>
                ))
              ) : (
                <p className="rounded-lg border border-dashed border-slate-200 p-4 text-sm text-slate-500">No recent developments were found for this topic yet.</p>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card className="border-slate-200/60 shadow-sm">
          <CardHeader className="flex flex-row items-center justify-between space-y-0">
            <div>
              <CardTitle className="text-lg font-semibold text-gray-900">Decisions</CardTitle>
              <CardDescription>Key calls captured from the meeting.</CardDescription>
            </div>
            <span className="text-sm text-gray-500">{decisions.length}</span>
          </CardHeader>
          <CardContent>
            {decisions.length ? (
              <div className="space-y-3">
                {decisions.map((decision: any, index: number) => (
                  <div key={index} className="rounded-lg border border-gray-200 p-4">
                    <p className="font-medium text-gray-900">{decision.decision || `Decision ${index + 1}`}</p>
                    {decision.reasoning ? <p className="mt-1 text-sm text-gray-600">{decision.reasoning}</p> : null}
                    <div className="mt-2 text-xs text-gray-500">Impact: {decision.impact_level || 'n/a'}</div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-gray-500">No decisions extracted yet.</p>
            )}
          </CardContent>
        </Card>

        <Card className="border-slate-200/60 shadow-sm">
          <CardHeader className="flex flex-row items-center justify-between space-y-0">
            <div>
              <CardTitle className="text-lg font-semibold text-gray-900">Topics</CardTitle>
              <CardDescription>Main themes discussed in the call.</CardDescription>
            </div>
            <span className="text-sm text-gray-500">{topics.length}</span>
          </CardHeader>
          <CardContent>
            {topics.length ? (
              <div className="flex flex-wrap gap-2">
                {topics.map((topic: string, index: number) => (
                  <span key={index} className="rounded-full bg-primary-50 px-3 py-1 text-sm text-primary-700">
                    {topic}
                  </span>
                ))}
              </div>
            ) : (
              <p className="text-sm text-gray-500">Topics will appear after analysis.</p>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card className="border-slate-200/60 shadow-sm">
          <CardHeader className="flex flex-row items-center justify-between space-y-0">
            <div>
              <CardTitle className="text-lg font-semibold text-gray-900">Action Items</CardTitle>
              <CardDescription>Follow-ups and assignments linked to this meeting.</CardDescription>
            </div>
            <Link to="/action-items" className="text-sm text-primary-600 hover:text-primary-700">View all</Link>
          </CardHeader>
          <CardContent>
            {meetingActionItems.length ? (
              <div className="space-y-3">
                {meetingActionItems.slice(0, 5).map((item: any) => (
                  <div key={item.id} className="rounded-lg border border-gray-200 p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="font-medium text-gray-900">{item.title}</p>
                        <p className="mt-1 text-sm text-gray-600">{item.description || 'No description'}</p>
                      </div>
                      <span className="rounded-full bg-gray-100 px-2 py-1 text-xs text-gray-700 capitalize">{item.status}</span>
                    </div>
                    {item.confidence_score ? (
                      <div className="mt-2 flex items-center gap-2 text-xs text-slate-500">
                        <Clock3 className="h-3.5 w-3.5" />
                        Confidence {(Number(item.confidence_score) * 100).toFixed(0)}%
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-gray-500">No action items linked to this meeting yet.</p>
            )}
          </CardContent>
        </Card>

        <Card className="border-slate-200/60 shadow-sm">
          <CardHeader className="flex flex-row items-center justify-between space-y-0">
            <div>
              <CardTitle className="text-lg font-semibold text-gray-900">Mentions</CardTitle>
              <CardDescription>People referenced during the meeting.</CardDescription>
            </div>
            <Link to="/mentions" className="text-sm text-primary-600 hover:text-primary-700">View all</Link>
          </CardHeader>
          <CardContent>
            {meetingMentions.length ? (
              <div className="space-y-3">
                {meetingMentions.slice(0, 5).map((mention: any) => (
                  <div key={mention.id} className="rounded-lg border border-gray-200 p-4">
                    <p className="text-sm font-medium text-gray-900">{mention.mentioned_text}</p>
                    <div className="mt-2 flex items-center gap-2 text-xs text-gray-500">
                      <span className="capitalize">{mention.mention_type}</span>
                      <span>•</span>
                      <span>{mention.sentiment || 'neutral'}</span>
                      {mention.relevance_score ? (
                        <>
                          <span>•</span>
                          <span>Confidence {Math.round(Number(mention.relevance_score))}%</span>
                        </>
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-gray-500">No mentions linked to this meeting yet.</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

export default MeetingDetail
