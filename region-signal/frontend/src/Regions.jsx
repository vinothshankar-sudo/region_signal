import { useEffect, useState } from 'react'
import { api } from './api'

const fmt = (s) => { const m = Math.floor(s / 60); return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m` }

export default function Regions({ session, status }) {
  const [rows, setRows] = useState([]); const [meta, setMeta] = useState({})
  const [msg, setMsg] = useState(null); const [busy, setBusy] = useState(false)
  const open = status ? status.open : true

  const load = () => api.regions().then(d => { setRows(d.regions); setMeta(d) }).catch(e => setMsg({ err: e.message }))
  useEffect(() => { load() }, [])

  const toggle = (code) => setRows(rows.map(r => r.region === code ? { ...r, checked: !r.checked } : r))
  const submit = async () => {
    setBusy(true); setMsg(null)
    try {
      const r = await api.submit(rows.map(r => ({ region: r.region, checked: r.checked })))
      const n = r.changed.length
      setMsg({ ok: n ? `Saved ${n} change${n > 1 ? 's' : ''} (${r.changed.join(', ')}); ${r.unchanged} unchanged. You can re-submit until the window closes.`
                     : 'No changes - everything already matches the saved values.' }); load()
    } catch (e) { setMsg({ err: e.message }) } finally { setBusy(false) }
  }
  const off = rows.filter(r => !r.checked).length

  return (
    <div className="card">
      <div className="row">
        <h2>Use current week's forecast?</h2>
        {status && <span className={`pill ${open ? 'ok' : 'bad'}`}>{open ? `Open · ${fmt(status.seconds_left)} left` : 'Closed'}</span>}
      </div>
      <p className="hint">Checked = use this week's forecast (TRUE). Uncheck a market to fall back to the previous valid forecast (FALSE).
        Nothing is saved until you press Submit.</p>
      {!open && <p className="banner closed">The window has closed — updates are no longer accepted.</p>}
      <table>
        <thead><tr><th>Market</th><th>Use Current Week Forecast?</th></tr></thead>
        <tbody>{rows.map(r => (
          <tr key={r.region} className={r.checked ? '' : 'off'}>
            <td>{r.region}{r.region_name && r.region_name !== r.region ? ` – ${r.region_name}` : ''}</td>
            <td><input type="checkbox" checked={r.checked} disabled={!open || busy}
                 onChange={() => toggle(r.region)} aria-label={`Use current week forecast for ${r.region}`} /></td>
          </tr>))}
        </tbody>
      </table>
      <div className="row">
        <span className="hint">{off} of {rows.length} markets set to FALSE</span>
        <button className="primary" onClick={submit} disabled={!open || busy || !rows.length}>{busy ? 'Submitting…' : 'Submit'}</button>
      </div>
      {meta.last_submitted_at && <p className="hint">Last submission this session: {meta.last_submitted_by}, {new Date(meta.last_submitted_at).toLocaleString()}</p>}
      {msg?.ok && <p className="banner ok" role="status">{msg.ok}</p>}
      {msg?.err && <p className="err" role="alert">{msg.err}</p>}
    </div>
  )
}
