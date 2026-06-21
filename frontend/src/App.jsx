import { useState, useEffect } from 'react'
import { Tv, BarChart2, TrendingUp, MessageSquare } from 'lucide-react'
import { api } from './api'
import Giornaliera from './views/Giornaliera'
import PrimeTime from './views/PrimeTime'
import Programma from './views/Programma'
import NLQuery from './components/NLQuery'

const VIEWS = [
  { id: 'giornaliera', label: 'Giornaliera', Icon: Tv },
  { id: 'primetime',   label: 'Prime Time',  Icon: BarChart2 },
  { id: 'programma',   label: 'Programma',   Icon: TrendingUp },
]

export default function App() {
  const [view, setView]       = useState('giornaliera')
  const [targets, setTargets] = useState([])
  const [target, setTarget]   = useState('4plus')
  const [dates, setDates]     = useState([])
  const [date, setDate]       = useState('')
  const [nlOpen, setNlOpen]   = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([api.targets(), api.status()]).then(([tgts, status]) => {
      setTargets(tgts)
      const avail = status.available_dates || []
      setDates(avail)
      if (avail.length > 0) setDate(avail[0])
      setLoading(false)
    }).catch(err => { console.error(err); setLoading(false) })
  }, [])

  if (loading) return (
    <div className="min-h-screen flex items-center justify-center bg-gray-950">
      <div className="text-center">
        <div className="text-4xl font-black text-white tracking-tight mb-2">
          AIAIAI<span style={{color:'#E8860C'}}>TV</span>
        </div>
        <div className="text-gray-400 text-sm animate-pulse">Caricamento...</div>
      </div>
    </div>
  )

  return (
    <div className="min-h-screen bg-gray-950 text-white flex flex-col">
      <header className="border-b border-gray-800" style={{background:'#0C447C'}}>
        <div className="max-w-screen-xl mx-auto px-4 h-14 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-xl font-black tracking-tight">
              AIAIAI<span style={{color:'#E8860C'}}>TV</span>
            </span>
            <span className="text-blue-200 text-xs hidden sm:block">
              AI-powered TV Audience Analytics
            </span>
          </div>
          <div className="flex items-center gap-3">
            <select
              value={date}
              onChange={e => setDate(e.target.value)}
              className="text-sm bg-blue-900 border border-blue-700 rounded px-2 py-1
                         text-white focus:outline-none focus:ring-1 focus:ring-orange-400"
            >
              {dates.map(d => <option key={d} value={d}>{d}</option>)}
              {dates.length === 0 && <option value="">Nessun dato disponibile</option>}
            </select>
            <button
              onClick={() => setNlOpen(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium"
              style={{background:'#E8860C'}}
            >
              <MessageSquare size={14} />
              <span className="hidden sm:inline">Chiedi</span>
            </button>
          </div>
        </div>
      </header>

      <nav className="border-b border-gray-800 bg-gray-900">
        <div className="max-w-screen-xl mx-auto px-4 flex gap-1">
          {VIEWS.map(({ id, label, Icon }) => (
            <button
              key={id}
              onClick={() => setView(id)}
              className={`flex items-center gap-1.5 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                view === id ? 'border-orange-400 text-white' : 'border-transparent text-gray-400 hover:text-gray-200'
              }`}
              style={view === id ? {borderColor:'#E8860C'} : {}}
            >
              <Icon size={14} />{label}
            </button>
          ))}
        </div>
      </nav>

      <div className="bg-gray-900 border-b border-gray-800 px-4 py-2">
        <div className="max-w-screen-xl mx-auto flex items-center gap-2 overflow-x-auto">
          <span className="text-gray-500 text-xs shrink-0">Target:</span>
          {targets.map(t => (
            <button
              key={t.id}
              onClick={() => setTarget(t.id)}
              className={`shrink-0 px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                target === t.id ? 'text-white' : 'bg-gray-800 text-gray-400 hover:text-gray-200'
              }`}
              style={target === t.id ? {background:'#0C447C'} : {}}
            >
              {t.short}
            </button>
          ))}
        </div>
      </div>

      <main className="flex-1 max-w-screen-xl mx-auto w-full px-4 py-6">
        {!date ? (
          <div className="flex items-center justify-center h-64">
            <div className="text-center text-gray-500">
              <Tv size={48} className="mx-auto mb-3 opacity-30" />
              <p>Nessun dato ingerito.</p>
              <p className="text-sm mt-1">
                Esegui: <code className="text-orange-400">curl -X POST http://localhost:8000/api/ingest/2026-04-27 -H "x-api-key: changeme"</code>
              </p>
            </div>
          </div>
        ) : (
          <>
            {view === 'giornaliera' && <Giornaliera date={date} target={target} />}
            {view === 'primetime'   && <PrimeTime   date={date} target={target} />}
            {view === 'programma'   && <Programma   date={date} target={target} />}
          </>
        )}
      </main>

      {nlOpen && <NLQuery date={date} target={target} onClose={() => setNlOpen(false)} />}
    </div>
  )
}
