import React, { useMemo, useState } from 'react'
import { Navigate, useSearchParams } from 'react-router-dom'
import { AlertCircle, Eye, EyeOff, Lock, User, UserPlus } from 'lucide-react'
import { useQuery } from 'react-query'

import { api } from '../lib/api'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'

const AcceptInvite: React.FC = () => {
  const [searchParams] = useSearchParams()
  const token = useMemo(() => searchParams.get('invite') || '', [searchParams])

  const [fullName, setFullName] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [showPassword, setShowPassword] = useState(false)

  const { data: sessionUser } = useQuery(
    ['accept-invite-session'],
    async () => (await api.get('/api/v1/auth/me')).data,
    { retry: false, refetchOnWindowFocus: false },
  )

  const { data: invitePreview, isLoading: isPreviewLoading } = useQuery(
    ['invite-preview', token],
    async () => (await api.get('/api/v1/org/invite-preview', { params: { token } })).data,
    { enabled: Boolean(token), retry: false, refetchOnWindowFocus: false },
  )

  if (sessionUser) {
    return <Navigate to="/dashboard" replace />
  }

  if (!token) {
    return <Navigate to="/login" replace />
  }

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    setLoading(true)
    setError('')

    try {
      await api.post('/api/v1/org/accept-invite', {
        token,
        full_name: fullName,
        username,
        password,
      })
      window.location.href = '/dashboard'
    } catch (e: any) {
      const detail = e?.response?.data?.detail
      if (detail) {
        setError(String(detail))
      } else if (e?.code === 'ECONNABORTED') {
        setError('Invite request timed out. Please try again.')
      } else if (!e?.response) {
        const target = String(api.defaults.baseURL || 'http://localhost:8002')
        setError(`Cannot reach backend at ${target}. Make sure backend is running and reachable.`)
      } else {
        setError('Unable to accept invite right now. Please try again.')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 p-6">
      <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-blue-600 to-indigo-600"></div>

      <Card className="w-full max-w-md shadow-xl border-slate-200">
        <CardHeader className="space-y-1 text-center">
          <div className="mx-auto bg-blue-100 w-12 h-12 rounded-full flex items-center justify-center mb-4">
            <UserPlus className="w-6 h-6 text-blue-600" />
          </div>
          <CardTitle className="text-2xl font-bold tracking-tight">Accept Invite</CardTitle>
          <CardDescription>
            {isPreviewLoading
              ? 'Loading organization invite...'
              : `Create your account to join ${invitePreview?.organization?.name || 'the organization'} workspace.`}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {invitePreview?.organization?.name ? (
            <div className="mb-4 rounded-md border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-700">
              You are joining <span className="font-semibold">{invitePreview.organization.name}</span>
              {invitePreview?.email ? ` as ${invitePreview.email}` : ''}.
            </div>
          ) : null}

          <form onSubmit={handleSubmit} className="space-y-4">
            <Input
              placeholder="Full name"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              required
            />

            <div className="relative">
              <User className="absolute left-3 top-3 h-4 w-4 text-slate-400" />
              <Input
                placeholder="Username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="pl-10"
                required
              />
            </div>

            <div className="relative">
              <Lock className="absolute left-3 top-3 h-4 w-4 text-slate-400" />
              <Input
                type={showPassword ? 'text' : 'password'}
                placeholder="Password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="pl-10 pr-10"
                minLength={8}
                required
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute right-3 top-3 text-slate-400 hover:text-slate-600 focus:outline-none"
                aria-label={showPassword ? 'Hide password' : 'Show password'}
              >
                {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>

            {error && (
              <div className="bg-red-50 border border-red-200 text-red-600 px-4 py-3 rounded-md flex items-center text-sm">
                <AlertCircle className="w-4 h-4 mr-2 flex-shrink-0" />
                {error}
              </div>
            )}

            <Button
              type="submit"
              disabled={loading}
              className="w-full h-11 bg-blue-600 hover:bg-blue-700 transition-all font-semibold"
            >
              {loading ? 'Joining...' : 'Join Organization'}
            </Button>
          </form>
        </CardContent>
        <CardFooter className="justify-center">
          <p className="text-xs text-slate-500 text-center">
            This invite link is single-use and may expire.
          </p>
        </CardFooter>
      </Card>
    </div>
  )
}

export default AcceptInvite
