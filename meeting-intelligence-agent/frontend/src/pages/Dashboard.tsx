import React from 'react'
import { useQuery } from 'react-query'
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
  Users
} from 'lucide-react'
import { api } from '../lib/api'
import { getAccessToken } from '../lib/auth'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

const Dashboard: React.FC = () => {
  const navigate = useNavigate()
  const hasToken = Boolean(getAccessToken())
  
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
    </div>
  )
}

export default Dashboard
