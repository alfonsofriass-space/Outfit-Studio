import { fetchJson } from './outfits'

export interface AuthenticatedUser {
  id: number
  username: string
  role: 'admin' | 'user'
  is_active: boolean
  created_at: string
}

interface Credentials {
  username: string
  password: string
}

export function getCurrentUser(): Promise<AuthenticatedUser> {
  return fetchJson<AuthenticatedUser>('/auth/me')
}

export function login(credentials: Credentials): Promise<AuthenticatedUser> {
  return fetchJson<AuthenticatedUser>('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(credentials),
  })
}

export function register(credentials: Credentials): Promise<AuthenticatedUser> {
  return fetchJson<AuthenticatedUser>('/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(credentials),
  })
}

export function logout(): Promise<void> {
  return fetchJson<void>('/auth/logout', { method: 'POST' })
}
