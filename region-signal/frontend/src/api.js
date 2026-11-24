// Token lives in sessionStorage only -> cleared when the tab closes; nothing else is persisted client-side.
const KEY = 'sig_session'
export const getSession = () => { try { return JSON.parse(sessionStorage.getItem(KEY)) } catch { return null } }
export const setSession = (s) => { try { s ? sessionStorage.setItem(KEY, JSON.stringify(s)) : sessionStorage.removeItem(KEY) } catch {} }

async function req(path, opts = {}) {
  const s = getSession()
  const res = await fetch(path, { ...opts, headers: { 'Content-Type': 'application/json',
    ...(s ? { Authorization: `Bearer ${s.token}` } : {}) } })
  const data = await res.json().catch(() => ({}))
  if (res.status === 401 && s) { setSession(null); location.reload() }
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`)
  return data
}
export const api = {
  status: () => req('/api/status'),
  login: (user_id, password) => req('/api/login', { method: 'POST', body: JSON.stringify({ user_id, password }) }),
  regions: () => req('/api/regions'),
  adminList: () => req('/api/admin/regions'),
  adminAdd: (region_code, region_name) => req('/api/admin/regions', { method: 'POST', body: JSON.stringify({ region_code, region_name }) }),
  adminDelete: (code) => req(`/api/admin/regions/${encodeURIComponent(code)}`, { method: 'DELETE' }),
  submit: (regions) => req('/api/submit', { method: 'POST', body: JSON.stringify({ regions }) }),
}
