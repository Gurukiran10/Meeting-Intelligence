import axios, { AxiosHeaders } from 'axios'
import { clearTokens, getAccessToken } from './auth'

const API_BASE_URL = String(import.meta.env.VITE_API_URL || '').trim()

export function getApiBaseUrl(): string {
  return API_BASE_URL
}

export function getApiTargetLabel(): string {
  return API_BASE_URL || window.location.origin
}

console.log('[api] baseURL', API_BASE_URL || '(same-origin)')

export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 60000,
  withCredentials: true,
})

api.interceptors.request.use((config) => {
  const headers = AxiosHeaders.from(config.headers)
  const token = getAccessToken()

  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
    console.log('[auth] token sent in headers', `Bearer ${token}`)
  }

  config.headers = headers

  const requestUrl = `${config.baseURL || ''}${config.url || ''}`
  console.log('[api] request', (config.method || 'get').toUpperCase(), requestUrl)

  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const requestUrl = String(error?.config?.url || '')
    console.error('[api] request failed', requestUrl, error?.message || error)

    if (error?.response?.status === 401) {
      clearTokens()
      if (window.location.pathname !== '/login' && !requestUrl.includes('/api/v1/auth/me')) {
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  },
)
