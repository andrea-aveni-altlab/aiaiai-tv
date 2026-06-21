import { useState, useEffect, useRef } from 'react'
import { Search, TrendingUp, TrendingDown } from 'lucide-react'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, CartesianGrid } from 'recharts'
import { api } from '../api'

const TV_COLORS = {
  '0001': '#007bc0', '0002': '#e4003a', '0003': '#00843d',
  '0004': '#ff6b35', '0005': '#0066cc', '0006': '#9c27b0', '0013': '#1a73e8',
}

function fmt(n) {
  if (!n && n !== 0) return '—'
  return Number(n).toLocaleString('it-IT')
}

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-gray-900 border border-gray-700 rounded p-3 text-xs shadow-xl">
      <div className="font-semibold text-gray-300 mb-1">{label}</div>
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full inline-block" style={{background: p.color}} />
          <span className="text-gray-400">{p.name}:</span>
          <span className="text-white font-semibold">{p.value}%</span>
        </div>
      ))}
    </div>
  )
}

export default function Programma({ date, target }) {
  const [query, setQuery]           = useState('')
  const [suggestions, setSugg]      = useState([])
  const [selected, setSelected]     = useState(null)
  const [storico, setStorico]       = useState([])
  const [loading, setLoading]       = useState(false)
  const [showReale, setShowReale]   = useState(false)
  const inputRef                    = useRef(null)
  const debounceRef                 = useRef(null)

  useEffect(() => {
    if (query.length < 2) { setSugg([]); return }
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      api.search(query).then(setSugg).catch(() => setSugg([]))
    }, 300)
  }, [query])

  useEffect(() => {
    if (!selected) return
    setLoading(true)
    api.storico(selected.programma, target, null, null, selected.cod_emit)
      .then(data => { setStorico(data); setLoading(false) })
      .catch(() => { setStorico([]); setLoading(false) })
  }, [selected, target])

  function handleSelect(s) {
    setSelected(s); setQuery(s.programma); setSugg([])
  }

  const shareKey = showReale ? 'share_reale' : 'share_auditel'
  const shares   = storico.map(r => r[shareKey]).filter(Boolean)
  const avgShare = shares.length ? (shares.reduce((a,b) => a+b, 0) / shares.length).toFixed(1) : null
  const maxShare = shares.length ? Math.max(...shares).toFixed(1) : null
  const trend    = shares.length >= 2 ? shares[shares.length-1] - shares[0] : null

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-lg font-bold text-white">Storico Programma</h2>
        <div className="flex items-center gap-2 bg-gray-800 rounded p-0.5">
          <button onClick={() => setShowReale(false)}
            className={`px-3 py-1 rounded text-xs font-medium ${!showReale ? 'bg-white text-gray-900' : 'text-gray-400'}`}>
            Auditel
          </button>
          <button onClick={() => setShowReale(true)}
            className={`px-3 py-1 rounded text-xs font-medium ${showReale ? 'text-white' : 'text-gray-400'}`}
            style={showReale ? {background:'#E8860C'} : {}}>
            Reale
          </button>
        </div>
      </div>

      <div className="relative mb-6">
        <div className="relative">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
          <input type="text"
            placeholder="Cerca un programma (es. TG1, Affari Tuoi, La Ruota...)"
            value={query}
            onChange={e => { setQuery(e.target.value); if (!e.target.value) setSelected(null) }}
            ref={inputRef}
            className="w-full pl-9 pr-4 py-3 bg-gray-800 border border-gray-700 rounded-xl
                       text-white placeholder-gray-500 focus:outline-none focus:border-blue-500
                       focus:ring-1 focus:ring-blue-500 transition-colors" />
        </div>
        {suggestions.length > 0 && (
          <div className="absolute z-10 w-full mt-1 bg-gray-800 border border-gray-700
                          rounded-xl shadow-xl overflow-hidden">
            {suggestions.map((s, i) => (
              <button key={i} onClick={() => handleSelect(s)}
                className="w-full px-4 py-2.5 text-left hover:bg-gray-700 transition-colors
                           flex items-center justify-between">
                <div>
                  <span className="text-white text-sm font-medium">{s.programma}</span>
                  <span className="text-gray-500 text-xs ml-2">{s.tv_label}</span>
                </div>
                <span className="text-gray-600 text-xs">{s.ultima_data}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {loading && <div className="text-gray-500 text-sm text-center py-8">Caricamento storico...</div>}

      {!loading && selected && storico.length === 0 && (
        <div className="text-gray-500 text-sm text-center py-8">
          Nessun dato storico trovato per "{selected.programma}"
        </div>
      )}

      {!loading && storico.length > 0 && (
        <>
          <div className="grid grid-cols-3 gap-3 mb-6">
            <div className="bg-gray-900 rounded-xl p-4">
              <div className="text-gray-500 text-xs mb-1">Share media</div>
              <div className="text-2xl font-black text-white">{avgShare}%</div>
              <div className="text-gray-600 text-xs mt-1">{showReale ? 'Share reale' : 'Share Auditel'}</div>
            </div>
            <div className="bg-gray-900 rounded-xl p-4">
              <div className="text-gray-500 text-xs mb-1">Picco</div>
              <div className="text-2xl font-black" style={{color:'#E8860C'}}>{maxShare}%</div>
              <div className="text-gray-600 text-xs mt-1">
                {storico.find(r => r[shareKey]?.toFixed(1) === maxShare)?.data}
              </div>
            </div>
            <div className="bg-gray-900 rounded-xl p-4">
              <div className="text-gray-500 text-xs mb-1">Trend</div>
              <div className={`text-2xl font-black flex items-center gap-1 ${
                trend > 0 ? 'text-green-400' : trend < 0 ? 'text-red-400' : 'text-gray-400'
              }`}>
                {trend > 0 ? <TrendingUp size={20} /> : trend < 0 ? <TrendingDown size={20} /> : null}
                {trend !== null ? `${trend > 0 ? '+' : ''}${trend.toFixed(1)}pp` : '—'}
              </div>
              <div className="text-gray-600 text-xs mt-1">primo vs ultimo dato</div>
            </div>
          </div>

          <div className="bg-gray-900 rounded-xl p-4 mb-6">
            <h3 className="text-sm font-semibold text-gray-300 mb-4">
              {selected.programma}
              <span className="ml-2 text-gray-500 font-normal">{selected.tv_label}</span>
            </h3>
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={storico}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                <XAxis dataKey="data" tick={{fill:'#6b7280',fontSize:10}}
                  axisLine={false} tickLine={false} interval="preserveStartEnd" />
                <YAxis tick={{fill:'#6b7280',fontSize:11}} axisLine={false} tickLine={false}
                  tickFormatter={v=>`${v}%`} />
                <Tooltip content={<CustomTooltip />} />
                {avgShare && (
                  <ReferenceLine y={parseFloat(avgShare)} stroke="#4b5563" strokeDasharray="4 4"
                    label={{value:`media ${avgShare}%`, fill:'#6b7280', fontSize:10}} />
                )}
                <Line type="monotone" dataKey={shareKey} name="Share"
                  stroke={TV_COLORS[selected.cod_emit] || '#E8860C'}
                  strokeWidth={2}
                  dot={{r:3, fill: TV_COLORS[selected.cod_emit] || '#E8860C'}}
                  activeDot={{r:5}} />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="bg-gray-900 rounded-xl p-4 overflow-x-auto">
            <h3 className="text-sm font-semibold text-gray-300 mb-3">Dati storici</h3>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-500 text-xs border-b border-gray-800">
                  <th className="text-left pb-2">Data</th>
                  <th className="text-left pb-2">Ora</th>
                  <th className="text-right pb-2 pr-4">Audience</th>
                  <th className="text-right pb-2 pr-4">Share Auditel</th>
                  <th className="text-right pb-2">Share Reale</th>
                </tr>
              </thead>
              <tbody>
                {storico.map((r, i) => (
                  <tr key={i} className="border-b border-gray-800 hover:bg-gray-800">
                    <td className="py-1.5 text-gray-400">{r.data}</td>
                    <td className="py-1.5 text-gray-400">{r.ora_inizio}</td>
                    <td className="py-1.5 pr-4 text-right text-gray-300 tabular-nums">{fmt(r.audience)}</td>
                    <td className="py-1.5 pr-4 text-right font-semibold text-white tabular-nums">{r.share_auditel}%</td>
                    <td className="py-1.5 text-right tabular-nums" style={{color:'#E8860C'}}>{r.share_reale}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {!selected && !loading && (
        <div className="flex items-center justify-center h-64 text-gray-600">
          <div className="text-center">
            <TrendingUp size={48} className="mx-auto mb-3 opacity-20" />
            <p>Cerca un programma per vedere il suo andamento storico</p>
          </div>
        </div>
      )}
    </div>
  )
}
