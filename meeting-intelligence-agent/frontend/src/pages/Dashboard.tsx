import React, { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from 'react-query'
import { useNavigate } from 'react-router-dom'
import {
  Calendar,
  Clock,
  TrendingUp,
  CheckCircle,
  AlertCircle,
  ChevronRight,
  Plus,
  Video,
  FileText,
  Users,
  Play,
  Square,
  Loader2,
  RefreshCw,
} from 'lucide-react'
import { api } from '../lib/api'
import { getAccessToken } from '../lib/auth'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

// ── toast helper (inline, no dependency) ──────────────────────────────────────
function showToast(msg: string, type: 'info' | 'error' = 'info') {
  const el = document.createElement('div')
  el.textContent = msg
  el.style.cssText = `
    position:fixed;bottom:24px;right:24px;z-index:9999;padding:12px 20px;
    border-radius:10px;font-size:14px;font-weight:600;color:#fff;
    background:${type === 'error' ? '#ef4444' : '#2563eb'};
    box-shadow:0 4px 20px rgba(0,0,0,.18);animation:fadein .2s;
  `
  document.body.appendChild(el)
  setTimeout(() => el.remove(), 4000)
}

const Dashboard: React.FC = () => {
  const navigate = useNavigate()
  const hasToken = Boolean(getAccessToken())
  const qc = useQueryClient()
  const [joiningUrl, setJoiningUrl] = useState<string | null>(null)
  const [zoomJoiningUrl, setZoomJoiningUrl] = useState<string | null>(null)
  
  const { data: currentUser } = useQuery('dashboard-current-user', async () => {
    const response = await api.get('/api/v1/auth/me')
    return response.data
  }, {
    enabled: hasToken,
  })

  const { data: myDashboard } = useQuery('my-dashboard', async () => {
    const response = await api.get('/api/v1/me/dashboard')
    return response.data
  })

  const { data: preBriefs } = useQuery('my-pre-briefs', async () => {
    const response = await api.get('/api/v1/me/pre-briefs')
    return response.data
  })

  const { data: orgDashboard } = useQuery('org-dashboard', async () => {
    const response = await api.get('/api/v1/org/dashboard')
    return response.data
  }, {
    enabled: currentUser?.role === 'admin',
    retry: false,
  })

  const { data: analytics } = useQuery('analytics', async () => {
    const response = await api.get('/api/v1/analytics/dashboard')
    return response.data
  })

  // Upcoming Google Meet events (requires Google OAuth connected)
  const {
    data: upcomingMeetData,
    isLoading: meetLoading,
    refetch: refetchMeets,
  } = useQuery(
    'upcoming-google-meets',
    async () => {
      const res = await api.get('/api/v1/integrations/google/meet/upcoming?days_ahead=7')
      return res.data
    },
    {
      enabled: hasToken,
      retry: false,
      staleTime: 60_000,
    }
  )

  // Bot status polling — auto-refetch every 5s when a bot is active
  const { data: botStatus } = useQuery(
    'meet-bot-status',
    async () => {
      const res = await api.get('/api/v1/integrations/google/meet/join/status')
      return res.data
    },
    {
      enabled: hasToken,
      refetchInterval: joiningUrl ? 5000 : false,
      retry: false,
    }
  )

  const joinMeetMutation = useMutation(
    async (meetUrl: string) => {
      console.log('[BOT] API call starting — POST /api/v1/integrations/google/meet/join', { meet_url: meetUrl })
      const res = await api.post('/api/v1/integrations/google/meet/join', {
        meet_url: meetUrl,
        stay_duration_seconds: 600,
      })
      console.log('[BOT] API response', res.status, res.data)
      return res.data
    },
    {
      onMutate: (meetUrl) => {
        console.log('[BOT] mutation started for', meetUrl)
        setJoiningUrl(meetUrl)
      },
      onSuccess: (data) => {
        console.log('[BOT] mutation success', data)
        showToast('Opened meeting tab · Bot is joining too…')
        qc.invalidateQueries('meet-bot-status')
      },
      onError: (err: any) => {
        const detail = err?.response?.data?.detail || err?.message || 'Failed to start bot'
        console.error('[BOT] mutation error', err?.response?.status, detail, err)
        showToast(detail, 'error')
        setJoiningUrl(null)
      },
    }
  )

  const stopBotMutation = useMutation(
    async () => {
      const res = await api.delete('/api/v1/integrations/google/meet/join')
      return res.data
    },
    {
      onSuccess: () => {
        showToast('Bot stopped.')
        setJoiningUrl(null)
        qc.invalidateQueries('meet-bot-status')
      },
    }
  )

  // Zoom Meetings Hooks
  const {
    data: upcomingZoomData,
    isLoading: zoomLoading,
    refetch: refetchZooms,
  } = useQuery(
    'upcoming-zoom-meets',
    async () => {
      const res = await api.get('/api/v1/integrations/zoom/meetings')
      return res.data
    },
    {
      enabled: hasToken,
      retry: false,
      staleTime: 60_000,
    }
  )

  const { data: zoomBotStatus } = useQuery(
    'zoom-bot-status',
    async () => {
      const res = await api.get('/api/v1/integrations/zoom/bot/status')
      return res.data
    },
    {
      enabled: hasToken,
      refetchInterval: zoomJoiningUrl ? 5000 : false,
      retry: false,
    }
  )

  const joinZoomMutation = useMutation(
    async ({ zoomUrl, topic }: { zoomUrl: string, topic?: string }) => {
      const res = await api.post('/api/v1/integrations/zoom/bot/join', {
        zoom_url: zoomUrl,
        stay_duration_seconds: 600,
        topic: topic,
      })
      return res.data
    },
    {
      onMutate: ({ zoomUrl }) => {
        setZoomJoiningUrl(zoomUrl)
      },
      onSuccess: () => {
        showToast('Opened Zoom tab · Bot is joining too…')
        qc.invalidateQueries('zoom-bot-status')
      },
      onError: (err: any) => {
        const detail = err?.response?.data?.detail || err?.message || 'Failed to start bot'
        showToast(detail, 'error')
        setZoomJoiningUrl(null)
      },
    }
  )

  const stopZoomBotMutation = useMutation(
    async () => {
      const res = await api.delete('/api/v1/integrations/zoom/bot/join')
      return res.data
    },
    {
      onSuccess: () => {
        showToast('Zoom Bot stopped.')
        setZoomJoiningUrl(null)
        qc.invalidateQueries('zoom-bot-status')
      },
    }
  )

  const upcomingMeetEvents: any[] = upcomingMeetData?.events || []
  const activeBotStatus: string | undefined = botStatus?.status
  const botIsActive = activeBotStatus && !['idle', 'completed', 'failed', 'cancelled'].includes(activeBotStatus)

  const upcomingZoomEvents: any[] = upcomingZoomData || []
  const activeZoomBotStatus: string | undefined = zoomBotStatus?.status
  const zoomBotIsActive = activeZoomBotStatus && !['idle', 'completed', 'failed', 'cancelled'].includes(activeZoomBotStatus)

  const stats = [
    {
      name: 'Meetings This Week',
      value: analytics?.meeting_stats?.this_week_count ?? 0,
      icon: Calendar,
      color: 'text-blue-600',
      bg: 'bg-blue-50',
    },
    {
      name: 'Time Saved',
      value: `${analytics?.time_saved_hours ?? 0}h`,
      icon: Clock,
      color: 'text-green-600',
      bg: 'bg-green-50',
    },
    {
      name: 'Action Completion',
      value: `${analytics?.action_item_stats?.completion_rate ?? 0}%`,
      icon: CheckCircle,
      color: 'text-indigo-600',
      bg: 'bg-indigo-50',
    },
    {
      name: 'Decision Velocity',
      value: `${analytics?.decision_velocity ?? 0}/hr`,
      icon: TrendingUp,
      color: 'text-orange-600',
      bg: 'bg-orange-50',
    },
  ]

  const meetings = myDashboard?.my_meetings || []
  const allTasks = myDashboard?.my_tasks || []
  const allMentions = myDashboard?.my_mentions || []
  const pendingTasks = (myDashboard?.my_tasks || [])
    .filter((item: any) => item.status !== 'completed')
    .sort((a: any, b: any) => Number(new Date(a.due_date || 0)) - Number(new Date(b.due_date || 0)))
    .slice(0, 4)
  const myMentions = (myDashboard?.my_mentions || []).slice(0, 4)
  const meetingsById = Object.fromEntries(meetings.map((meeting: any) => [meeting.id, meeting]))
  const upcomingMeetings = (preBriefs || [])
    .slice(0, 3)
    .map((brief: any) => {
      const meeting = meetingsById[brief.meeting_id]
      const meetingTaskCount = allTasks.filter(
        (task: any) => task.meeting_id === brief.meeting_id && task.status !== 'completed',
      ).length
      const meetingMentionCount = allMentions.filter((mention: any) => mention.meeting_id === brief.meeting_id).length
      return {
        id: brief.meeting_id,
        title: brief.title,
        scheduled_start: brief.scheduled_start,
        importance: String(brief.importance || 'optional').toLowerCase(),
        platform: meeting?.platform || 'meeting',
        pending_tasks_count: meetingTaskCount,
        mentions_count: meetingMentionCount,
      }
    })

  const importanceBadgeVariant = (importance: string) => {
    if (importance === 'critical') return 'destructive'
    if (importance === 'important') return 'default'
    return 'secondary'
  }

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-slate-900">Dashboard</h1>
          <p className="mt-1.5 text-slate-500 font-medium">
            {currentUser?.role === 'admin'
              ? `Overview for ${currentUser?.organization?.name || 'your organization'}.`
              : 'Your meetings, mentions, and tasks in one place.'}
          </p>
        </div>
        <div className="flex space-x-3">
           <Button variant="outline" className="border-slate-200" onClick={() => navigate('/analytics')}>
              <FileText className="w-4 h-4 mr-2" />
              Reports
           </Button>
           <Button className="bg-blue-600 hover:bg-blue-700 shadow-lg shadow-blue-200" onClick={() => navigate('/meetings')}>
              <Plus className="w-4 h-4 mr-2" />
              New Meeting
           </Button>
        </div>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        {stats.map((stat) => (
          <Card key={stat.name} className="border-slate-200/60 shadow-sm hover:shadow-md transition-shadow">
            <CardContent className="p-6">
              <div className="flex items-center justify-between mb-4">
                 <div className={`${stat.bg} p-2.5 rounded-xl`}>
                    <stat.icon className={`w-5 h-5 ${stat.color}`} />
                 </div>
                 <div className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Live</div>
              </div>
              <div className="space-y-1">
                <p className="text-sm font-bold text-slate-500 uppercase tracking-tighter">{stat.name}</p>
                <p className="text-3xl font-black text-slate-900 tracking-tight">{stat.value}</p>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Recent Meetings */}
        <Card className="lg:col-span-2 border-slate-200/60 shadow-sm">
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <div className="space-y-1">
              <CardTitle className="text-xl font-bold">Upcoming Meetings</CardTitle>
              <CardDescription>
                {currentUser?.role === 'admin' && orgDashboard
                  ? `${orgDashboard.meetings_count} meetings across the organization.`
                  : 'Upcoming meetings with pre-meeting intelligence.'}
              </CardDescription>
            </div>
            <Button variant="ghost" size="sm" className="text-blue-600 font-bold hover:text-blue-700 hover:bg-blue-50" onClick={() => navigate('/meetings')}>View all</Button>
          </CardHeader>
          <CardContent className="pt-4">
            <div className="space-y-4">
              {upcomingMeetings.length > 0 ? upcomingMeetings.map((meeting: any) => (
                <div key={meeting.id} onClick={() => navigate(`/meetings/${meeting.id}`)} className="flex items-center justify-between p-4 rounded-xl border border-slate-100 hover:bg-slate-50/50 hover:border-slate-200 transition-all group cursor-pointer">
                  <div className="flex items-center space-x-4">
                    <div className="bg-slate-100 p-2.5 rounded-lg group-hover:bg-white transition-colors">
                      <Video className="w-5 h-5 text-slate-600" />
                    </div>
                    <div>
                      <h3 className="text-sm font-bold text-slate-900 group-hover:text-blue-600 transition-colors">{meeting.title}</h3>
                      <div className="flex items-center space-x-3 mt-1">
                         <div className="flex items-center text-[11px] text-slate-500 font-medium">
                            <Clock className="w-3 h-3 mr-1" />
                            {new Date(meeting.scheduled_start).toLocaleDateString()} at {new Date(meeting.scheduled_start).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}
                         </div>
                         <span className="text-slate-300">•</span>
                         <div className="flex items-center text-[11px] text-slate-500 font-medium">
                            <Users className="w-3 h-3 mr-1" />
                            {meeting.platform}
                         </div>
                      </div>
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide text-slate-600">
                          {meeting.pending_tasks_count} pending task{meeting.pending_tasks_count === 1 ? '' : 's'}
                        </span>
                        <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide text-slate-600">
                          {meeting.mentions_count} mention{meeting.mentions_count === 1 ? '' : 's'}
                        </span>
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center space-x-4">
                     <Badge variant={importanceBadgeVariant(meeting.importance) as 'destructive' | 'default' | 'secondary'} className="capitalize font-bold text-[10px] tracking-wide px-2.5">
                        {meeting.importance}
                     </Badge>
                     <ChevronRight className="w-4 h-4 text-slate-300 group-hover:text-blue-500 transition-colors" />
                  </div>
                </div>
              )) : (
                <div className="py-12 text-center">
                   <div className="bg-slate-50 w-12 h-12 rounded-full flex items-center justify-center mx-auto mb-4">
                      <Calendar className="w-6 h-6 text-slate-300" />
                   </div>
                   <p className="text-sm text-slate-400 font-medium">No upcoming meetings found.</p>
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        <Card className="border-slate-200/60 shadow-sm flex flex-col">
          <CardHeader>
            <CardTitle className="text-xl font-bold">My Tasks</CardTitle>
            <CardDescription>Pending work assigned to you.</CardDescription>
          </CardHeader>
          <CardContent className="flex-1">
            <div className="space-y-4">
              {pendingTasks.length > 0 ? pendingTasks.map((action: any, idx: number) => (
                <div key={idx} className="p-4 rounded-xl bg-slate-50/70 border border-slate-100 flex items-start space-x-3">
                  <div className={cn(
                     "mt-1 p-1 rounded-md",
                     action.priority === 'high' ? "bg-red-100 text-red-600" : "bg-orange-100 text-orange-600"
                  )}>
                     <AlertCircle className="w-4 h-4" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-bold text-slate-900 leading-tight">{action.title}</p>
                    <div className="mt-2 flex items-center justify-between">
                       <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest">{action.priority} Priority</span>
                       <span className="text-[10px] font-bold text-red-500">
                          Due {action.due_date ? new Date(action.due_date).toLocaleDateString() : 'TBD'}
                       </span>
                    </div>
                  </div>
                </div>
              )) : (
                <div className="py-12 text-center">
                   <div className="bg-green-50 w-12 h-12 rounded-full flex items-center justify-center mx-auto mb-4">
                      <CheckCircle className="w-6 h-6 text-green-400" />
                   </div>
                   <p className="text-sm text-slate-400 font-medium">Inbox clear! Great job.</p>
                </div>
              )}
            </div>
          </CardContent>
          <div className="p-6 pt-0 mt-auto">
             <Button variant="outline" className="w-full border-slate-200 text-slate-600 font-bold hover:bg-slate-50 transition-colors" onClick={() => navigate('/action-items')}>
                View All Actions
             </Button>
          </div>
        </Card>
      </div>

      <Card className="border-slate-200/60 shadow-sm">
        <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
          <div className="space-y-1">
            <CardTitle className="text-xl font-bold">Mentions</CardTitle>
            <CardDescription>Recent references and mentions that may need a response.</CardDescription>
          </div>
          <Button variant="ghost" size="sm" className="text-blue-600 font-bold hover:text-blue-700 hover:bg-blue-50" onClick={() => navigate('/mentions')}>View all</Button>
        </CardHeader>
        <CardContent className="pt-4">
          {myMentions.length ? (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {myMentions.map((mention: any) => (
                <div key={mention.id} className="rounded-xl border border-slate-100 p-4 hover:bg-slate-50/60 transition-colors">
                  <div className="flex items-center justify-between gap-3">
                    <Badge variant={mention.notification_read ? 'secondary' : 'default'} className="capitalize">
                      {mention.mention_type.replace('_', ' ')}
                    </Badge>
                    <span className="text-xs text-slate-400">{new Date(mention.created_at).toLocaleDateString()}</span>
                  </div>
                  <p className="mt-3 text-sm font-medium text-slate-900">{mention.mentioned_text}</p>
                </div>
              ))}
            </div>
          ) : (
            <div className="py-12 text-center">
              <div className="bg-slate-50 w-12 h-12 rounded-full flex items-center justify-center mx-auto mb-4">
                <Users className="w-6 h-6 text-slate-300" />
              </div>
              <p className="text-sm text-slate-400 font-medium">No recent mentions for you.</p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Integrations Grid: Google & Zoom side-by-side */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-8">
        {/* Upcoming Google Meet Events with Join Bot */}
        <Card className="border-slate-200/60 shadow-sm flex flex-col">
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <div className="space-y-1">
              <CardTitle className="text-xl font-bold flex items-center gap-2">
                <Video className="w-5 h-5 text-blue-500" />
                Upcoming Google Meets
              </CardTitle>
              <CardDescription>
                Silent bot for Google Meet capture.
              </CardDescription>
            </div>
            <Button
              variant="ghost"
              size="sm"
              className="text-slate-500 hover:text-blue-600"
              onClick={() => refetchMeets()}
            >
              <RefreshCw className="w-4 h-4 mr-1" />
              Refresh
            </Button>
          </CardHeader>
          <CardContent className="pt-4 flex-1">
            {/* Bot status banner */}
            {activeBotStatus && activeBotStatus !== 'idle' && (
              <div
                className={cn(
                  'mb-4 flex items-center justify-between rounded-xl border px-4 py-3 text-sm font-semibold',
                  botIsActive
                    ? 'border-blue-200 bg-blue-50 text-blue-800'
                    : activeBotStatus === 'completed'
                    ? 'border-green-200 bg-green-50 text-green-800'
                    : 'border-red-200 bg-red-50 text-red-800',
                )}
              >
                <span className="flex items-center gap-2">
                  {botIsActive && <Loader2 className="w-4 h-4 animate-spin" />}
                  Status: <span className="capitalize">{
                    activeBotStatus === 'waiting_admission' ? 'Waiting Room' : 
                    activeBotStatus === 'pre_join' ? 'Preparing' :
                    activeBotStatus === 'recording' ? 'Recording' :
                    activeBotStatus?.replace(/_/g, ' ')
                  }</span>
                </span>
                {botIsActive && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="border-red-300 text-red-600 hover:bg-red-50"
                    onClick={() => stopBotMutation.mutate()}
                    disabled={stopBotMutation.isLoading}
                  >
                    <Square className="w-3 h-3 mr-1 fill-current" />
                    Stop
                  </Button>
                )}
              </div>
            )}

            {meetLoading ? (
              <div className="py-10 flex justify-center">
                <Loader2 className="w-6 h-6 animate-spin text-slate-300" />
              </div>
            ) : upcomingMeetEvents.length === 0 ? (
              <div className="py-12 text-center">
                <div className="bg-slate-50 w-12 h-12 rounded-full flex items-center justify-center mx-auto mb-4">
                  <Video className="w-6 h-6 text-slate-300" />
                </div>
                <p className="text-sm text-slate-400 font-medium">
                  {upcomingMeetData === undefined
                    ? 'Connect Google Calendar.'
                    : 'No Google Meet events found.'}
                </p>
              </div>
            ) : (
              <div className="space-y-3">
                {upcomingMeetEvents.slice(0, 5).map((event: any, idx: number) => {
                  const meetUrl: string = event._meet_url || event.hangoutLink || ''
                  const title: string = event.summary || 'Untitled Meeting'
                  const startRaw: string = event.start?.dateTime || event.start?.date || ''
                  const startDate = startRaw ? new Date(startRaw) : null
                  const isJoiningThis = joiningUrl === meetUrl && joinMeetMutation.isLoading
                  const isThisBotActive = botIsActive && botStatus?.meet_url === meetUrl

                  return (
                    <div
                      key={event.id || idx}
                      className="flex items-center justify-between rounded-xl border border-slate-100 px-4 py-3 hover:bg-slate-50/50 transition-all"
                    >
                      <div className="flex items-center gap-3 flex-1 min-w-0">
                        <div className="min-w-0">
                          <p className="text-sm font-bold text-slate-900 truncate">{title}</p>
                          {startDate && (
                            <p className="text-[11px] text-slate-500 mt-0.5 font-medium flex items-center gap-1">
                              <Clock className="w-3 h-3" />
                              {startDate.toLocaleDateString()} {startDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                            </p>
                          )}
                        </div>
                      </div>

                      <div className="flex items-center gap-2 flex-shrink-0">
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-8 text-[11px] text-blue-600 font-bold"
                          onClick={() => window.open(meetUrl, '_blank')}
                          disabled={!meetUrl}
                        >
                          Open
                        </Button>
                        <Button
                          size="sm"
                          disabled={!meetUrl || isJoiningThis || isThisBotActive}
                          className={cn(
                            'text-xs font-bold h-8 px-3',
                            isThisBotActive
                              ? 'bg-green-600 hover:bg-green-700'
                              : 'bg-blue-600 hover:bg-blue-700',
                          )}
                          onClick={() => {
                            if (!meetUrl) return
                            setJoiningUrl(meetUrl)
                            joinMeetMutation.mutate(meetUrl)
                          }}
                        >
                          {isJoiningThis ? (
                            <Loader2 className="w-3 h-3 animate-spin" />
                          ) : isThisBotActive ? (
                            'Joined'
                          ) : (
                            'Join'
                          )}
                        </Button>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Upcoming Zoom Meets Card */}
        <Card className="border-slate-200/60 shadow-sm flex flex-col">
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <div className="space-y-1">
              <CardTitle className="text-xl font-bold flex items-center gap-2">
                <Video className="w-5 h-5 text-purple-500" />
                Upcoming Zoom Meets
              </CardTitle>
              <CardDescription>
                Silent bot for Zoom meeting capture.
              </CardDescription>
            </div>
            <Button
              variant="ghost"
              size="sm"
              className="text-slate-500 hover:text-blue-600"
              onClick={() => refetchZooms()}
            >
              <RefreshCw className="w-4 h-4 mr-1" />
              Refresh
            </Button>
          </CardHeader>
          <CardContent className="pt-4 flex-1">
            {/* Zoom Bot status banner */}
            {activeZoomBotStatus && activeZoomBotStatus !== 'idle' && (
              <div
                className={cn(
                  'mb-4 flex items-center justify-between rounded-xl border px-4 py-3 text-sm font-semibold',
                  zoomBotIsActive
                    ? 'border-blue-200 bg-blue-50 text-blue-800'
                    : activeZoomBotStatus === 'completed'
                    ? 'border-green-200 bg-green-50 text-green-800'
                    : 'border-red-200 bg-red-50 text-red-800',
                )}
              >
                <span className="flex items-center gap-2">
                  {zoomBotIsActive && <Loader2 className="w-4 h-4 animate-spin" />}
                  Status: <span className="capitalize">{
                    activeZoomBotStatus === 'waiting_admission' ? 'Waiting Room' : 
                    activeZoomBotStatus === 'pre_join' ? 'Preparing' :
                    activeZoomBotStatus === 'recording' ? 'Recording' :
                    activeZoomBotStatus?.replace(/_/g, ' ')
                  }</span>
                </span>
                {zoomBotIsActive && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="border-red-300 text-red-600 hover:bg-red-50"
                    onClick={() => stopZoomBotMutation.mutate()}
                    disabled={stopZoomBotMutation.isLoading}
                  >
                    <Square className="w-3 h-3 mr-1 fill-current" />
                    Stop
                  </Button>
                )}
              </div>
            )}

            {zoomLoading ? (
              <div className="py-10 flex justify-center">
                <Loader2 className="w-6 h-6 animate-spin text-slate-300" />
              </div>
            ) : upcomingZoomEvents.length === 0 ? (
              <div className="py-12 text-center">
                <div className="bg-slate-50 w-12 h-12 rounded-full flex items-center justify-center mx-auto mb-4">
                  <Video className="w-6 h-6 text-slate-300" />
                </div>
                <p className="text-sm text-slate-400 font-medium">
                  {upcomingZoomData === undefined
                    ? 'Connect Zoom.'
                    : 'No Zoom events found.'}
                </p>
              </div>
            ) : (
              <div className="space-y-3">
                {upcomingZoomEvents.slice(0, 5).map((event: any, idx: number) => {
                  const meetUrl: string = event.join_url || ''
                  const title: string = event.topic || 'Untitled Zoom'
                  const startRaw: string = event.start_time || ''
                  const startDate = startRaw ? new Date(startRaw) : null
                  const isJoiningThis = zoomJoiningUrl === meetUrl && joinZoomMutation.isLoading
                  const isThisBotActive = zoomBotIsActive && zoomBotStatus?.zoom_url === meetUrl

                  return (
                    <div
                      key={event.id || idx}
                      className="flex items-center justify-between rounded-xl border border-slate-100 px-4 py-3 hover:bg-slate-50/50 transition-all"
                    >
                      <div className="flex items-center gap-3 flex-1 min-w-0">
                        <div className="min-w-0">
                          <p className="text-sm font-bold text-slate-900 truncate">{title}</p>
                          {startDate && (
                            <p className="text-[11px] text-slate-500 mt-0.5 font-medium flex items-center gap-1">
                              <Clock className="w-3 h-3" />
                              {startDate.toLocaleDateString()} {startDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                            </p>
                          )}
                        </div>
                      </div>

                      <div className="flex items-center gap-2 flex-shrink-0">
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-8 text-[11px] text-blue-600 font-bold"
                          onClick={() => window.open(meetUrl, '_blank')}
                          disabled={!meetUrl}
                        >
                          Open
                        </Button>
                        <Button
                          size="sm"
                          disabled={!meetUrl || isJoiningThis || isThisBotActive}
                          className={cn(
                            'text-xs font-bold h-8 px-3',
                            isThisBotActive
                              ? 'bg-green-600 hover:bg-green-700'
                              : 'bg-purple-600 hover:bg-purple-700',
                          )}
                          onClick={() => {
                            if (!meetUrl) return
                            joinZoomMutation.mutate({ zoomUrl: meetUrl, topic: title })
                          }}
                        >
                          {isJoiningThis ? (
                            <Loader2 className="w-3 h-3 animate-spin" />
                          ) : isThisBotActive ? (
                            'Joined'
                          ) : (
                            'Join'
                          )}
                        </Button>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

export default Dashboard
