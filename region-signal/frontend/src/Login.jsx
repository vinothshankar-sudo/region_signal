import { useState } from 'react'
import { api } from './api'

export default function Login({ onLogin, status }) {
  const [u, setU] = useState(''); const [p, setP] = useState('')
  const [err, setErr] = useState(''); const [busy, setBusy] = useState(false)
  const go = async (e) => {
    e.preventDefault(); setErr(''); setBusy(true)
    try { onLogin(await api.login(u, p)) } catch (x) { setErr(x.message) } finally { setBusy(false) }
  }
  return (
    <form className="card narrow" onSubmit={go}>
      <h2>Sign in</h2>
      {status && !status.open && <p className="banner closed">The submission window has closed. You can view but not update.</p>}
      <label>User ID<input value={u} onChange={e => setU(e.target.value)} autoFocus autoComplete="username" /></label>
      <label>Password<input type="password" value={p} onChange={e => setP(e.target.value)} autoComplete="current-password" /></label>
      {err && <p className="err" role="alert">{err}</p>}
      <button className="primary" disabled={busy || !u || !p}>{busy ? 'Signing in…' : 'Sign in'}</button>
    </form>
  )
}
