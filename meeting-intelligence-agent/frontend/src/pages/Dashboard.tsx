import React from 'react'
import { useQuery } from 'react-query'
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
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

const Dashboard: React.FC = () => {
  const { data: analytics } = useQuery('analytics', async () => {
    const response = await api.get('/api/v1/analytics/dashboard')
    return response.data
  })

  const { data: meetings } = useQuery('dashboard-meetings', async () => {
    const response = await api.get('/api/v1/meetings/', { params: { limit: 5 } })
    return response.data
  })

  const { data: actionItems } = useQuery('dashboard-actions', async () => {
    const response = await api.get('/api/v1/action-items/', { params: { limit: 5 } })
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

  const recentMeetings = meetings || []
  const urgentActions = (actionItems || []).filter((item: any) => item.status !== 'completed').slice(0, 3)

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-slate-900">Dashboard</h1>
          <p className="mt-1.5 text-slate-500 font-medium">
            Overview of your meeting workspace and team activity.
          </p>
        </div>
        <div className="flex space-x-3">
           <Button variant="outline" className="border-slate-200">
              <FileText className="w-4 h-4 mr-2" />
              Reports
           </Button>
           <Button className="bg-blue-600 hover:bg-blue-700 shadow-lg shadow-blue-200">
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
              <CardTitle className="text-xl font-bold">Recent Meetings</CardTitle>
              <CardDescription>Your latest collaborations and recordings.</CardDescription>
            </div>
            <Button variant="ghost" size="sm" className="text-blue-600 font-bold hover:text-blue-700 hover:bg-blue-50">View all</Button>
          </CardHeader>
          <CardContent className="pt-4">
            <div className="space-y-4">
              {recentMeetings.length > 0 ? recentMeetings.map((meeting: any) => (
                <div key={meeting.id} className="flex items-center justify-between p-4 rounded-xl border border-slate-100 hover:bg-slate-50/50 hover:border-slate-200 transition-all group cursor-pointer">
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
                    </div>
                  </div>
                  <div className="flex items-center space-x-4">
                     <Badge variant={meeting.status === 'completed' ? 'secondary' : 'outline'} className="capitalize font-bold text-[10px] tracking-wide px-2.5">
                        {meeting.status}
                     </Badge>
                     <ChevronRight className="w-4 h-4 text-slate-300 group-hover:text-blue-500 transition-colors" />
                  </div>
                </div>
              )) : (
                <div className="py-12 text-center">
                   <div className="bg-slate-50 w-12 h-12 rounded-full flex items-center justify-center mx-auto mb-4">
                      <Calendar className="w-6 h-6 text-slate-300" />
                   </div>
                   <p className="text-sm text-slate-400 font-medium">No recent meetings found.</p>
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Urgent Action Items */}
        <Card className="border-slate-200/60 shadow-sm flex flex-col">
          <CardHeader>
            <CardTitle className="text-xl font-bold">Urgent Actions</CardTitle>
            <CardDescription>Items needing immediate attention.</CardDescription>
          </CardHeader>
          <CardContent className="flex-1">
            <div className="space-y-4">
              {urgentActions.length > 0 ? urgentActions.map((action: any, idx: number) => (
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
             <Button variant="outline" className="w-full border-slate-200 text-slate-600 font-bold hover:bg-slate-50 transition-colors">
                View All Actions
             </Button>
          </div>
        </Card>
      </div>
    </div>
  )
}

export default Dashboard
