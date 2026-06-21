import { useState, useEffect } from 'react'
import { ChevronUp, ChevronDown, Filter } from 'lucide-react'
import { api } from '../api'

const TV_COLORS = {
  '0001': '#007bc0', '0002': '#e4003a', '0003': '#00843d',
  '0004': '#ff6b35', '0005': '#0066cc', '0006': '#9c27b0', '0013': '#1a73e8',
}
const TV_DISPLAY = {
  '0001': 'RAI 1', '0002': 'RAI 2', '0003': 'RAI 3',
  '0004': 'Canale 5', '0005': 'Italia 1', '0006': 'Rete 4', '0013': 'LA7',
}

function fmt(n) {
  if (!n && n !== 0) return '—'
  return Number(n).toLocaleString('it-IT')
}

function ShareBadge({ auditel, reale, showReale }) {
  const val   = showReale ? reale : auditel
  const other = showReale ? auditel : reale
  if (!val) return <span className="text-gray-600">—</span>
  return (
    <span className="inline-flex flex-col items-end leading-none">
      <span className="font-semibold text-white">{val}%</span>
      <span className="text-gray-500 text-xs">{other}%</span>
    </span>
  )
}

function AudienceBar({ value, max }) {
  const pct = max > 0 ? (value / max) * 100 : 0
  return (
    <div className="flex items-center gap-2">
      <div className="w-20 h-1.5 bg-gray-800 rounded-full overflow-hidden">
        <div className="h-full rounded-full" style={{ width: `${pct}%`, background: '#E8860C' }} />
      </div>
      <span className="text-white tabular-nums text-sm">{fmt(value)}</span>
    </div>
  )
}

export default function Giornaliera({ date, target }) {
  const [rows, setRows]             = useState([])
  const [loading, setLoading]       = useState(true)
  const [error, setError]           = useState(null)
  const [showReale, setShowReale]   = useState(false)
  const [filterEmit, setFilterEmit] = useState('ALL')
  const [sortCol, setSortCol]       = useState('t_start')
  const [sortAsc, setSortAsc]       = useState(true)
  const [minAud, setMinAud]         = useState(100000)

  useEffect(() => {
    setLoading(true); setError(null)
    api.programmiGiorno(date, target).then(data => {
      setRows(data); setLoading(false)
    }).catch(err => { setError(err.message); setLoading(false) })
  }, [date, target])

  const emittenti = [...new Set(rows.map(r => r.cod_emit))]
  const maxAud = Math.max(...rows.map(r => r.audience || 0), 1)

  const filtered = rows
    .filter(r => filterEmit === 'ALL' || r.cod_emit === filterEmit)
    .filter(r => (r.audience || 0) >= minAud)
    .sort((a, b) => {
      const va = a[sortCol] ?? 0, vb = b[sortCol] ?? 0
      return sortAsc ? (va > vb ? 1 : -1) : (va < vb ? 1 : -1)
    })

  function toggleSort(col) {
    if (sortCol === col) setSortAsc(!sortAsc)
    else { setSortCol(col); setSortAsc(false) }
  }

  function SortIcon({ col }) {
    if (sortCol !== col) return <ChevronUp size={12} className="text-gray-700" />
    return sortAsc ? <ChevronUp size={12} className="text-orange-400" />
                   : <ChevronDown size={12} className="text-orange-400" />
  }

  if (loading) return <div className="text-gray-500 text-sm py-8 text-center">Caricamento...</div>
  if (error)   return <div className="text-red-400 text-sm py-8 text-center">{error}</div>

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <div className="flex items-center gap-2">
          <Filter size={14} className="text-gray-500" />
          <div className="flex gap-1 flex-wrap">
            <button onClick={() => setFilterEmit('ALL')}
              className={`px-2 py-0.5 rounded text-xs font-medium ${filterEmit==='ALL' ? 'bg-white text-gray-900' : 'bg-gray-800 text-gray-400'}`}>
              Tutte
            </button>
            {emittenti.map(e => (
              <button key={e} onClick={() => setFilterEmit(e === filterEmit ? 'ALL' : e)}
                className={`px-2 py-0.5 rounded text-xs font-medium transition-colors ${filterEmit===e ? 'text-white' : 'bg-gray-800 text-gray-400'}`}
                style={filterEmit===e ? {background:'#0C447C'} : {}}>
                {TV_DISPLAY[e] || e}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-2 ml-auto">
          <span className="text-gray-500 text-xs">Min aud:</span>
          <select value={minAud} onChange={e => setMinAud(Number(e.target.value))}
            className="text-xs bg-gray-800 border border-gray-700 rounded px-2 py-1 text-gray-300">
            {[0,50000,100000,300000,500000].map(v => (
              <option key={v} value={v}>{v === 0 ? 'Tutti' : fmt(v)}</option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-2 bg-gray-800 rounded p-0.5">
          <button onClick={() => setShowReale(false)}
            className={`px-2 py-1 rounded text-xs font-medium ${!showReale ? 'bg-white text-gray-900' : 'text-gray-400'}`}>
            Share Auditel
          </button>
          <button onClick={() => setShowReale(true)}
            className={`px-2 py-1 rounded text-xs font-medium ${showReale ? 'text-white' : 'text-gray-400'}`}
            style={showReale ? {background:'#E8860C'} : {}}>
            Share Reale
          </button>
        </div>
      </div>

      <div className="text-xs text-gray-600 mb-3">
        {showReale
          ? '⚠️  Share reale: include Netflix, Prime e tutto il non attribuito'
          : 'Share Auditel ufficiale: esclude i consumi non attribuiti'}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 text-gray-500 text-xs">
              <th className="text-left pb-2 pr-3 font-medium">Rete</th>
              <th className="text-left pb-2 pr-3 font-medium cursor-pointer select-none"
                  onClick={() => toggleSort('t_start')}>
                <span className="flex items-center gap-1">Ora <SortIcon col="t_start" /></span>
              </th>
              <th className="text-left pb-2 pr-6 font-medium">Programma</th>
              <th className="text-right pb-2 pr-3 font-medium">Durata</th>
              <th className="text-right pb-2 pr-6 font-medium cursor-pointer select-none"
                  onClick={() => toggleSort('audience')}>
                <span className="flex items-center justify-end gap-1">Audience <SortIcon col="audience" /></span>
              </th>
              <th className="text-right pb-2 font-medium cursor-pointer select-none"
                  onClick={() => toggleSort(showReale ? 'share_reale' : 'share_auditel')}>
                <span className="flex items-center justify-end gap-1">
                  Share <SortIcon col={showReale ? 'share_reale' : 'share_auditel'} />
                </span>
              </th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((r, i) => (
              <tr key={i} className="border-b border-gray-900 hover:bg-gray-900 transition-colors">
                <td className="py-2 pr-3">
                  <span className="text-xs font-bold px-1.5 py-0.5 rounded"
                    style={{
                      background: `${TV_COLORS[r.cod_emit] || '#555'}22`,
                      color: TV_COLORS[r.cod_emit] || '#aaa',
                      border: `1px solid ${TV_COLORS[r.cod_emit] || '#555'}44`,
                    }}>
                    {TV_DISPLAY[r.cod_emit] || r.tv}
                  </span>
                </td>
                <td className="py-2 pr-3 text-gray-400 tabular-nums whitespace-nowrap">{r.ora_inizio}</td>
                <td className="py-2 pr-6 font-medium max-w-xs"><span className="line-clamp-1">{r.programma}</span></td>
                <td className="py-2 pr-3 text-right text-gray-500 tabular-nums whitespace-nowrap">{r.durata_min}′</td>
                <td className="py-2 pr-6"><AudienceBar value={r.audience} max={maxAud} /></td>
                <td className="py-2 text-right">
                  <ShareBadge auditel={r.share_auditel} reale={r.share_reale} showReale={showReale} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {filtered.length === 0 && (
          <div className="text-center text-gray-600 py-12">Nessun programma con questi filtri</div>
        )}
      </div>
      <div className="text-xs text-gray-700 mt-3">
        {filtered.length} programmi — sotto il valore principale: share alternativa
      </div>
    </div>
  )
}
