import { useState, useEffect } from 'react'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts'
import { api } from '../api'

const TV_COLORS = {
  '0001': '#007bc0', '0002': '#e4003a', '0003': '#00843d',
  '0004': '#ff6b35', '0005': '#0066cc', '0006': '#9c27b0', '0013': '#1a73e8',
}
const TV_LABELS = {
  '0001': 'RAI 1', '0002': 'RAI 2', '0003': 'RAI 3',
  '0004': 'Canale 5', '0005': 'Italia 1', '0006': 'Rete 4', '0013': 'LA7',
}

function fmt(n) {
  if (!n && n !== 0) return '—'
  return Number(n).toLocaleString('it-IT')
}

const CustomTooltip = ({ active, payload }) => {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  return (
    <div className="bg-gray-900 border border-gray-700 rounded p-3 text-sm shadow-xl">
      <div className="font-bold text-white mb-1">{d.tv_label}</div>
      <div className="text-gray-300">Audience: <span className="text-white">{fmt(d.audience_media)}</span></div>
      <div className="text-gray-300">Share Auditel: <span className="text-white">{d.share_auditel}%</span></div>
      <div className="text-gray-300">Share Reale: <span style={{color:'#E8860C'}}>{d.share_reale}%</span></div>
    </div>
  )
}

export default function PrimeTime({ date, target }) {
  const [data, setData]             = useState(null)
  const [loading, setLoading]       = useState(true)
  const [error, setError]           = useState(null)
  const [showReale, setShowReale]   = useState(false)
  const [selectedEmit, setSelectedEmit] = useState(null)

  useEffect(() => {
    setLoading(true); setError(null)
    api.primeTime(date, target).then(d => {
      setData(d); setLoading(false)
    }).catch(err => { setError(err.message); setLoading(false) })
  }, [date, target])

  if (loading) return <div className="text-gray-500 text-sm py-8 text-center">Caricamento...</div>
  if (error)   return <div className="text-red-400 text-sm py-8 text-center">{error}</div>
  if (!data)   return null

  const { summary, programmi } = data
  const shareKey = showReale ? 'share_reale' : 'share_auditel'
  const chartData = [...summary].sort((a, b) => b[shareKey] - a[shareKey])
  const detail = selectedEmit ? programmi.filter(p => p.cod_emit === selectedEmit) : []

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-lg font-bold text-white">Prime Time 20:00 – 23:00</h2>
          <p className="text-gray-500 text-sm">{date}</p>
        </div>
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

      <div className="bg-gray-900 rounded-xl p-4 mb-6">
        <p className="text-xs text-gray-500 mb-4">
          Share % {showReale ? '(incluso non attribuito)' : '(metodo Auditel ufficiale)'}
          &nbsp;— click su una barra per il dettaglio
        </p>
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={chartData} onClick={d => {
            const emit = d?.activePayload?.[0]?.payload?.cod_emit
            setSelectedEmit(selectedEmit === emit ? null : emit)
          }}>
            <XAxis dataKey="tv_label" tick={{fill:'#9ca3af',fontSize:12}} axisLine={false} tickLine={false} />
            <YAxis tick={{fill:'#6b7280',fontSize:11}} axisLine={false} tickLine={false} tickFormatter={v=>`${v}%`} />
            <Tooltip content={<CustomTooltip />} />
            <Bar dataKey={shareKey} radius={[4,4,0,0]} cursor="pointer">
              {chartData.map(entry => (
                <Cell key={entry.cod_emit}
                  fill={selectedEmit === entry.cod_emit ? '#E8860C' : TV_COLORS[entry.cod_emit] || '#4b5563'}
                  opacity={selectedEmit && selectedEmit !== entry.cod_emit ? 0.4 : 1} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="bg-gray-900 rounded-xl p-4 mb-6">
        <h3 className="text-sm font-semibold text-gray-300 mb-3">Riepilogo fascia</h3>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-gray-500 text-xs border-b border-gray-800">
              <th className="text-left pb-2">Rete</th>
              <th className="text-right pb-2 pr-4">Audience media</th>
              <th className="text-right pb-2 pr-4">Share Auditel</th>
              <th className="text-right pb-2">Share Reale</th>
            </tr>
          </thead>
          <tbody>
            {summary.map((r, i) => (
              <tr key={i}
                onClick={() => setSelectedEmit(selectedEmit === r.cod_emit ? null : r.cod_emit)}
                className={`border-b border-gray-800 cursor-pointer transition-colors ${selectedEmit === r.cod_emit ? 'bg-gray-800' : 'hover:bg-gray-850'}`}>
                <td className="py-2">
                  <span className="inline-block w-2 h-2 rounded-full mr-2"
                    style={{background: TV_COLORS[r.cod_emit] || '#555'}} />
                  <span className="font-medium text-white">{r.tv_label}</span>
                </td>
                <td className="py-2 pr-4 text-right tabular-nums text-gray-300">
                  {Number(r.audience_media).toLocaleString('it-IT')}
                </td>
                <td className="py-2 pr-4 text-right font-semibold text-white">{r.share_auditel}%</td>
                <td className="py-2 text-right" style={{color:'#E8860C'}}>{r.share_reale}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selectedEmit && detail.length > 0 && (
        <div className="bg-gray-900 rounded-xl p-4">
          <h3 className="text-sm font-semibold text-gray-300 mb-3">
            Programmi {TV_LABELS[selectedEmit]} — Prime Time
          </h3>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-gray-500 text-xs border-b border-gray-800">
                <th className="text-left pb-2">Ora</th>
                <th className="text-left pb-2 pr-4">Programma</th>
                <th className="text-right pb-2 pr-4">Audience</th>
                <th className="text-right pb-2">Share</th>
              </tr>
            </thead>
            <tbody>
              {detail.map((r, i) => (
                <tr key={i} className="border-b border-gray-800">
                  <td className="py-1.5 pr-3 text-gray-400 tabular-nums whitespace-nowrap">{r.ora_inizio}</td>
                  <td className="py-1.5 pr-4 text-white font-medium">{r.programma}</td>
                  <td className="py-1.5 pr-4 text-right text-gray-300 tabular-nums">
                    {Number(r.audience).toLocaleString('it-IT')}
                  </td>
                  <td className="py-1.5 text-right font-semibold text-white">
                    {showReale ? r.share_reale : r.share_auditel}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
