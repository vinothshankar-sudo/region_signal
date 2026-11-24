import { useEffect, useState } from 'react'
import { api, getSession, setSession } from './api'
import Login from './Login.jsx'
import Regions from './Regions.jsx'
import Admin from './Admin.jsx'

export default function App() {
  const [session, setSess] = useState(getSession())
  const [status, setStatus] = useState(null)
  const [tab, setTab] = useState('forecast')

  useEffect(() => {
    const tick = () => api.status().then(setStatus).catch(() => {})
    tick(); const t = setInterval(tick, 15000); return () => clearInterval(t)
  }, [])

  const onLogin = (s) => { setSession(s); setSess(s) }
  const logout = () => { setSession(null); setSess(null); setTab('forecast') }

  return (
    <div className="shell">
      <header>
        <h1>Forecast Market Signal</h1>
        {session && <div className="who">{session.user_name} · <button className="link" onClick={logout}>Log out</button></div>}
      </header>
      {session && session.is_admin && (
        <nav className="tabs">
          <button className={tab === 'forecast' ? 'on' : ''} onClick={() => setTab('forecast')}>Forecast signal</button>
          <button className={tab === 'admin' ? 'on' : ''} onClick={() => setTab('admin')}>Manage regions</button>
        </nav>)}
      {!session ? <Login onLogin={onLogin} status={status} />
        : tab === 'admin' ? <Admin status={status} />
        : <Regions session={session} status={status} />}
    </div>
  )
}
