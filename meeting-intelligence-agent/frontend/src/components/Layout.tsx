import React from 'react'
import { Outlet, NavLink } from 'react-router-dom'
import { useQuery } from 'react-query'
import {
  LayoutDashboard,
  Calendar,
  CheckSquare,
  Bell,
  BarChart3,
  Settings,
  LogOut,
  Plug,
  User as UserIcon,
  ChevronRight
} from 'lucide-react'
import { api } from '../lib/api'
import { clearTokens } from '../lib/auth'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { Separator } from '@/components/ui/separator'

const Layout: React.FC = () => {
  const { data: currentUser } = useQuery('current-user', async () => {
    const response = await api.get('/api/v1/auth/me')
    return response.data
  })

  const handleLogout = () => {
    clearTokens()
    window.location.href = '/login'
  }

  const navigation = [
    { name: 'Dashboard', to: '/dashboard', icon: LayoutDashboard },
    { name: 'Meetings', to: '/meetings', icon: Calendar },
    { name: 'Action Items', to: '/action-items', icon: CheckSquare },
    { name: 'Mentions', to: '/mentions', icon: Bell },
    { name: 'Analytics', to: '/analytics', icon: BarChart3 },
    { name: 'Integrations', to: '/integrations', icon: Plug },
    { name: 'Settings', to: '/settings', icon: Settings },
  ]

  const initials = (currentUser?.full_name || 'User')
    .split(' ')
    .map((p: string) => p[0])
    .join('')
    .slice(0, 2)
    .toUpperCase();

  return (
    <TooltipProvider>
      <div className="min-h-screen bg-slate-50/50">
        {/* Sidebar */}
        <aside className="fixed inset-y-0 left-0 w-64 bg-white border-r border-slate-200/60 shadow-sm z-30">
          <div className="flex flex-col h-full">
            {/* Logo */}
            <div className="px-6 h-16 flex items-center border-b border-slate-100">
              <div className="bg-blue-600 p-1.5 rounded-lg mr-3 shadow-md shadow-blue-200">
                <LayoutDashboard className="w-5 h-5 text-white" />
              </div>
              <h1 className="text-xl font-bold tracking-tight text-slate-900 ring-offset-background">
                SyncMinds
              </h1>
            </div>

            {/* Navigation */}
            <nav className="flex-1 px-3 py-6 space-y-1 overflow-y-auto">
              <div className="px-3 mb-2 text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
                Main Menu
              </div>
              {navigation.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) =>
                    cn(
                      "group flex items-center justify-between px-3 py-2.5 text-sm font-medium rounded-md transition-all duration-200",
                      isActive
                        ? "bg-blue-50 text-blue-700 shadow-sm shadow-blue-100/50"
                        : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
                    )
                  }
                >
                  <div className="flex items-center">
                    <item.icon className={cn(
                      "w-5 h-5 mr-3 transition-colors",
                      "group-hover:text-blue-600"
                    )} />
                    {item.name}
                  </div>
                  <ChevronRight className={cn(
                    "w-3.5 h-3.5 opacity-0 transition-opacity",
                    "group-hover:opacity-100"
                  )} />
                </NavLink>
              ))}
            </nav>

            {/* Footer / User section */}
            <div className="p-4 mt-auto">
              <div className="bg-slate-50 rounded-xl border border-slate-100 p-3 shadow-sm">
                <div className="flex items-center">
                  <Avatar className="h-9 w-9 border-2 border-white shadow-sm ring-1 ring-slate-200">
                    <AvatarImage src={currentUser?.avatar_url} />
                    <AvatarFallback className="bg-blue-600 text-white text-xs">{initials}</AvatarFallback>
                  </Avatar>
                  <div className="ml-3 flex-1 min-w-0">
                    <p className="text-xs font-bold text-slate-900 truncate">
                      {currentUser?.full_name || 'System Admin'}
                    </p>
                    <p className="text-[10px] text-slate-500 font-medium truncate uppercase tracking-tighter">
                      {currentUser?.role || 'Administrator'}
                    </p>
                  </div>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button variant="ghost" size="icon" className="h-8 w-8 text-slate-400 hover:text-red-500 hover:bg-red-50" onClick={handleLogout}>
                        <LogOut className="h-4 w-4" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent side="right">
                      <p>Logout</p>
                    </TooltipContent>
                  </Tooltip>
                </div>
              </div>
            </div>
          </div>
        </aside>

        {/* Header (Top bar for search/notifications) */}
        <header className="fixed top-0 left-64 right-0 h-16 bg-white/80 backdrop-blur-md border-b border-slate-200/60 z-20 px-8 flex items-center justify-between">
          <div className="flex items-center text-sm text-slate-500 font-medium">
             <span className="text-slate-400">Workspace</span>
             <ChevronRight className="w-4 h-4 mx-2" />
             <span className="text-slate-900">General</span>
          </div>
          <div className="flex items-center space-x-3">
             <Button variant="outline" size="sm" className="hidden md:flex items-center text-slate-500 border-slate-200">
                <Plug className="w-4 h-4 mr-2" />
                Connect Integrations
             </Button>
             <Separator orientation="vertical" className="h-6 mx-1 hidden md:block" />
             <Button variant="ghost" size="icon" className="relative text-slate-500 hover:text-blue-600 transition-colors">
                <Bell className="w-5 h-5" />
                <span className="absolute top-2 right-2 w-2 h-2 bg-red-500 rounded-full border-2 border-white"></span>
             </Button>
             <Button variant="ghost" size="icon" className="text-slate-500 hover:text-blue-600 transition-colors">
                <Settings className="w-5 h-5" />
             </Button>
          </div>
        </header>

        {/* Main content */}
        <main className="pl-64 pt-16 min-h-screen">
          <div className="py-8 px-10 max-w-7xl mx-auto">
            <Outlet />
          </div>
        </main>
      </div>
    </TooltipProvider>
  )
}

export default Layout
