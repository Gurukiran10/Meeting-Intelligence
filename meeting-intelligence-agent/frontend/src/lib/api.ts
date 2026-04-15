import axios from 'axios'
import { clearTokens } from './auth'

const envApiBaseUrl = import.meta.env.VITE_API_BASE_URL || import.meta.env.VITE_API_URL
const defaultApiBaseUrl = ''
const API_BASE_URL = envApiBaseUrl || defaultApiBaseUrl

export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 60000,
  withCredentials: true,
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error?.response?.status === 401) {
      clearTokens()
      if (window.location.pathname !== '/login') {
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  },
)
