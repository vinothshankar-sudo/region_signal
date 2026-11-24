import { useEffect, useState } from 'react'
import { api } from './api'

export default function Admin({ status }) {
  const [rows, setRows] = useState([]); const [code, setCode] = useState(''); const [name, setName] = useState('')
  const [confirm, setConfirm] = useState(null); const [msg, setMsg] = useState(null)
  const open = status ? status.open : true
  const load = () => api.adminList().then(setRows).catch(e => setMsg({ err: e.message }))
  useEffect(() => { load() }, [])

  const add = async (e) => {
    e.preventDefault(); setMsg(null)
    try { await api.adminAdd(code, name); setMsg({ ok: `${code.toUpperCase()} added` }); setCode(''); setName(''); load() }
    catch (x) { setMsg({ err: x.message }) }
  }
  const del = async (c) => {
    try { await api.adminDelete(c); setMsg({ ok: `${c} removed. Its history is kept.` }); setConfirm(null); load() }
    catch (x) { setMsg({ err: x.message }) }
  }
  return (
    <div className="card">
      <h2>Manage regions</h2>
      <p className="hint">Admin only. New regions appear on the forecast screen (checked by default). Deleting hides a region but keeps its audit history.</p>
      <form className="row addrow" onSubmit={add}>
        <input placeholder="Code e.g. DE" value={code} onChange={e => setCode(e.target.value)} maxLength={10} aria-label="Region code" />
        <input placeholder="Name (optional)" value={name} onChange={e => setName(e.target.value)} aria-label="Region name" />
        <button className="primary" disabled={!code.trim() || !open}>Add</button>
      </form>
      <table>
        <thead><tr><th>Region</th><th></th></tr></thead>
        <tbody>{rows.map(r => (
          <tr key={r.region_code}>
            <td>{r.region_code}{r.region_name && r.region_name !== r.region_code ? ` - ${r.region_name}` : ''}</td>
            <td>{confirm === r.region_code
              ? <><button className="link danger" onClick={() => del(r.region_code)} disabled={!open}>Confirm delete</button> · <button className="link" onClick={() => setConfirm(null)}>Cancel</button></>
              : <button className="link danger" onClick={() => setConfirm(r.region_code)} disabled={!open}>Delete</button>}</td>
          </tr>))}</tbody>
      </table>
      {msg?.ok && <p className="banner ok" role="status">{msg.ok}</p>}
      {msg?.err && <p className="err" role="alert">{msg.err}</p>}
    </div>
  )
}
