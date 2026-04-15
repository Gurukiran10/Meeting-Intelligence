import React, { useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery } from 'react-query'
import { api } from '../lib/api'
import { clearTokens } from '../lib/auth'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { AlertCircle, Lock, User, Eye, EyeOff } from 'lucide-react'

const Login: React.FC = () => {
  const [mode, setMode] = useState<'login' | 'signup'>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [email, setEmail] = useState('')
  const [fullName, setFullName] = useState('')
  const [organizationName, setOrganizationName] = useState('')
  const [organizationSlug, setOrganizationSlug] = useState('')
  const [createOrganization, setCreateOrganization] = useState(true)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [showPassword, setShowPassword] = useState(false)

  const { data: sessionUser } = useQuery(
    'login-session',
    async () => (await api.get('/api/v1/auth/me')).data,
    { retry: false, refetchOnWindowFocus: false },
  )

  if (sessionUser) {
    return <Navigate to="/dashboard" replace />
  }

  const handleLogin = async (event: React.FormEvent) => {
    event.preventDefault()
    setLoading(true)
    setError('')

    try {
      if (mode === 'login') {
        const params = new URLSearchParams()
        params.append('username', username)
        params.append('password', password)
        await api.post('/api/v1/auth/login', params, {
          headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
          },
        })
      } else {
        await api.post('/api/v1/auth/signup', {
          email,
          username,
          full_name: fullName,
          password,
          create_organization: createOrganization,
          organization_name: createOrganization ? organizationName : undefined,
          organization_slug: organizationSlug || undefined,
        })
      }

      window.location.href = '/dashboard'
    } catch (e: any) {
      clearTokens()
      const detail = e?.response?.data?.detail
      if (detail) {
        setError(String(detail))
      } else if (e?.code === 'ECONNABORTED') {
        setError('Login request timed out. Backend may be busy. Please try again in a few seconds.')
      } else if (!e?.response) {
        const target = String(api.defaults.baseURL || 'http://localhost:8002')
        setError(`Cannot reach backend at ${target}. Make sure backend is running and reachable.`)
      } else {
        setError('Login failed. Please check your credentials.')
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
            <Lock className="w-6 h-6 text-blue-600" />
          </div>
          <CardTitle className="text-2xl font-bold tracking-tight">SyncMinds Login</CardTitle>
          <CardDescription>
            {mode === 'login' ? 'Sign in to your organization workspace' : 'Create your account and organization'}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleLogin} className="space-y-4">
            {mode === 'signup' && (
              <>
                <div className="space-y-2">
                  <Input
                    placeholder="Full name"
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                    required
                  />
                </div>
                <div className="space-y-2">
                  <Input
                    type="email"
                    placeholder="Email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    required
                  />
                </div>
              </>
            )}
            <div className="space-y-2">
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
            </div>
            <div className="space-y-2">
              <div className="relative">
                <Lock className="absolute left-3 top-3 h-4 w-4 text-slate-400" />
                <Input
                  type={showPassword ? "text" : "password"}
                  placeholder="Password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="pl-10 pr-10"
                  required
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-3 text-slate-400 hover:text-slate-600 focus:outline-none"
                  aria-label={showPassword ? "Hide password" : "Show password"}
                >
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>
            {mode === 'signup' && (
              <>
                <div className="flex items-center justify-between rounded-md border border-slate-200 px-3 py-2 text-sm">
                  <label className="font-medium text-slate-700">Create a new organization</label>
                  <input
                    type="checkbox"
                    checked={createOrganization}
                    onChange={(e) => setCreateOrganization(e.target.checked)}
                  />
                </div>
                {createOrganization ? (
                  <Input
                    placeholder="Organization name"
                    value={organizationName}
                    onChange={(e) => setOrganizationName(e.target.value)}
                    required
                  />
                ) : (
                  <Input
                    placeholder="Organization slug to join"
                    value={organizationSlug}
                    onChange={(e) => setOrganizationSlug(e.target.value)}
                    required
                  />
                )}
              </>
            )}

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
              {loading ? (
                <div className="flex items-center">
                  <div className="animate-spin mr-2 h-4 w-4 border-2 border-white border-t-transparent rounded-full"></div>
                  Signing in...
                </div>
              ) : (
                mode === 'login' ? 'Sign in' : 'Create account'
              )}
            </Button>
            <button
              type="button"
              className="w-full text-sm text-blue-600 hover:text-blue-700"
              onClick={() => {
                setMode((current) => (current === 'login' ? 'signup' : 'login'))
                setError('')
              }}
            >
              {mode === 'login' ? 'Need an account? Sign up' : 'Already have an account? Sign in'}
            </button>
          </form>
        </CardContent>
        <CardFooter className="flex flex-col space-y-4">
          <div className="relative w-full">
            <div className="absolute inset-0 flex items-center">
              <span className="w-full border-t border-slate-200"></span>
            </div>
            <div className="relative flex justify-center text-xs uppercase">
              <span className="bg-white px-2 text-slate-500">Need help?</span>
            </div>
          </div>
          <p className="text-center text-xs text-slate-500">
            Contact your administrator for account access or password recovery.
          </p>
        </CardFooter>
      </Card>
      
      <p className="fixed bottom-8 text-slate-400 text-sm">
        &copy; 2026 SyncMinds AI. All rights reserved.
      </p>
    </div>
  )
}

export default Login
