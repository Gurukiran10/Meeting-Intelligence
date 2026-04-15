import React from 'react'
import { BrowserRouter as Router, Routes, Route, Navigate, useSearchParams } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from 'react-query'
import { api } from './lib/api'
import Dashboard from './pages/Dashboard'
import Meetings from './pages/Meetings'
import MeetingDetail from './pages/MeetingDetail'
import ActionItems from './pages/ActionItems'
import Mentions from './pages/Mentions'
import Analytics from './pages/Analytics'
import Integrations from './pages/Integrations'
import Team from './pages/Team'
import Settings from './pages/Settings'
import Login from './pages/Login'
import AcceptInvite from './pages/AcceptInvite'
import Layout from './components/Layout'
import './index.css'
import { useQuery } from 'react-query'

const LoginEntry: React.FC = () => {
  const [searchParams] = useSearchParams()
  const inviteToken = searchParams.get('invite')

  if (inviteToken) {
    return <AcceptInvite />
  }

  return <Login />
}

const ProtectedRoute: React.FC<{ children: React.ReactElement }> = ({ children }) => {
  const { data, isLoading, isError } = useQuery(
    'auth-session',
    async () => (await api.get('/api/v1/auth/me')).data,
    { retry: false, refetchOnWindowFocus: false },
  )

  if (isLoading) {
    return <div className="min-h-screen flex items-center justify-center text-slate-500">Loading workspace...</div>
  }

  if (isError || !data) {
    return <Navigate to="/login" replace />
  }
  return children
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Router future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route path="/login" element={<LoginEntry />} />
          <Route
            path="/"
            element={(
              <ProtectedRoute>
                <Layout />
              </ProtectedRoute>
            )}
          >
            <Route index element={<Navigate to="/dashboard" replace />} />
            <Route path="dashboard" element={<Dashboard />} />
            <Route path="meetings" element={<Meetings />} />
            <Route path="meetings/:id" element={<MeetingDetail />} />
            <Route path="action-items" element={<ActionItems />} />
            <Route path="mentions" element={<Mentions />} />
            <Route path="analytics" element={<Analytics />} />
            <Route path="team" element={<Team />} />
            <Route path="integrations" element={<Integrations />} />
            <Route path="settings" element={<Settings />} />
          </Route>
        </Routes>
      </Router>
    </QueryClientProvider>
  )
}

export default App
