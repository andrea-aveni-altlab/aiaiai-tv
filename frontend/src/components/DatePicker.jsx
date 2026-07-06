import { useState, useEffect, useRef } from 'react'
import { Calendar, ChevronLeft, ChevronRight } from 'lucide-react'

const MESI = ['gennaio','febbraio','marzo','aprile','maggio','giugno',
              'luglio','agosto','settembre','ottobre','novembre','dicembre']
const MESI_ABBR = ['gen','feb','mar','apr','mag','giu','lug','ago','set','ott','nov','dic']
const WD_ABBR   = ['dom','lun','mar','mer','gio','ven','sab']
const WD_HEAD   = ['Lun','Mar','Mer','Gio','Ven','Sab','Dom']

// Parsing locale (mai new Date('YYYY-MM-DD'): interpreta UTC e in fuso
// negativo slitta al giorno prima). Formato di ritorno costruito a mano.
function parseISO(s) { const [y,m,d] = s.split('-').map(Number); return new Date(y, m-1, d) }
function toISO(y, m, d) {
  return `${y}-${String(m+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`
}
function monthIdx(iso) { const dt = parseISO(iso); return dt.getFullYear()*12 + dt.getMonth() }

function fmtSingle(iso) {
  if (!iso) return 'Seleziona data'
  const dt = parseISO(iso)
  return `${WD_ABBR[dt.getDay()]} ${dt.getDate()} ${MESI_ABBR[dt.getMonth()]} ${dt.getFullYear()}`
}
function fmtDayMon(iso) { const dt = parseISO(iso); return `${dt.getDate()} ${MESI_ABBR[dt.getMonth()]}` }
function fmtRange(from, to) {
  if (!from || !to) return 'Seleziona intervallo'
  const a = parseISO(from), b = parseISO(to)
  if (a.getFullYear() !== b.getFullYear())
    return `${fmtDayMon(from)} ${a.getFullYear()} – ${fmtDayMon(to)} ${b.getFullYear()}`
  return `${fmtDayMon(from)} – ${fmtDayMon(to)} ${b.getFullYear()}`
}

// Griglia del mese con settimana che parte da lunedì; celle vuote (null) in testa.
function monthCells(y, m) {
  const lead = (new Date(y, m, 1).getDay() + 6) % 7   // offset lunedì-first
  const dim  = new Date(y, m+1, 0).getDate()
  const cells = []
  for (let i = 0; i < lead; i++) cells.push(null)
  for (let d = 1; d <= dim; d++) cells.push(d)
  return cells
}

export default function DatePicker({ mode, value, onChange, availableDates, min, max }) {
  const [open, setOpen]             = useState(false)
  const [pendingFrom, setPending]   = useState(null)
  const [viewMonth, setViewMonth]   = useState(() => anchor())
  const rootRef = useRef(null)

  function anchor() {
    const iso = (mode === 'single' ? value : (value && value.to) || (value && value.from)) || max
    const dt = parseISO(iso || max)
    return { y: dt.getFullYear(), m: dt.getMonth() }
  }

  function toggle() {
    if (!open) { setViewMonth(anchor()); setPending(null) }
    setOpen(o => !o)
  }

  useEffect(() => {
    if (!open) return
    function onDoc(e) { if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  const curIdx = viewMonth.y * 12 + viewMonth.m
  const minIdx = min ? monthIdx(min) : -Infinity
  const maxIdx = max ? monthIdx(max) :  Infinity
  function go(delta) {
    const idx = curIdx + delta
    if (idx < minIdx || idx > maxIdx) return
    setViewMonth({ y: Math.floor(idx / 12), m: ((idx % 12) + 12) % 12 })
  }

  function clickDay(d) {
    const iso = toISO(viewMonth.y, viewMonth.m, d)
    if (!availableDates.has(iso)) return
    if (mode === 'single') { onChange(iso); setOpen(false); return }
    if (!pendingFrom) { setPending(iso); return }
    let f = pendingFrom, t = iso
    if (t < f) { [f, t] = [t, f] }        // ISO zero-padded: confronto = cronologico
    onChange({ from: f, to: t }); setPending(null); setOpen(false)
  }

  function cellState(d) {
    const iso = toISO(viewMonth.y, viewMonth.m, d)
    if (!availableDates.has(iso)) return 'na'
    if (mode === 'single') return iso === value ? 'sel' : 'av'
    const from = pendingFrom || (value && value.from)
    const to   = pendingFrom ? null : (value && value.to)
    if (iso === from || iso === to) return 'sel'
    if (from && to && iso > from && iso < to) return 'inr'
    return 'av'
  }

  const label = mode === 'single' ? fmtSingle(value) : fmtRange(value && value.from, value && value.to)

  return (
    <div className="relative" ref={rootRef}>
      <button
        onClick={toggle}
        className="flex items-center gap-1.5 text-sm bg-blue-900 border border-blue-700 rounded
                   px-2 py-1 text-white focus:outline-none focus:ring-1 focus:ring-orange-400"
      >
        <Calendar size={14} className="text-blue-200" />
        <span className="tabular-nums">{label}</span>
      </button>

      {open && (
        <div className="absolute right-0 mt-2 z-20 w-72 bg-gray-900 border border-gray-700
                        rounded-xl shadow-xl p-3">
          <div className="flex items-center justify-between mb-3">
            <button onClick={() => go(-1)} disabled={curIdx <= minIdx}
              className="w-6 h-6 flex items-center justify-center rounded bg-gray-800 text-gray-400
                         hover:text-white disabled:opacity-30 disabled:cursor-not-allowed">
              <ChevronLeft size={16} />
            </button>
            <span className="text-sm font-medium text-white capitalize">
              {MESI[viewMonth.m]} {viewMonth.y}
            </span>
            <button onClick={() => go(1)} disabled={curIdx >= maxIdx}
              className="w-6 h-6 flex items-center justify-center rounded bg-gray-800 text-gray-400
                         hover:text-white disabled:opacity-30 disabled:cursor-not-allowed">
              <ChevronRight size={16} />
            </button>
          </div>

          <div className="grid grid-cols-7 gap-1 mb-1">
            {WD_HEAD.map(w => (
              <div key={w} className="text-center text-xs text-gray-500 py-0.5">{w}</div>
            ))}
          </div>

          <div className="grid grid-cols-7 gap-1">
            {monthCells(viewMonth.y, viewMonth.m).map((d, i) => {
              if (d === null) return <div key={`e${i}`} />
              const st = cellState(d)
              const base = 'h-9 flex items-center justify-center text-sm rounded-md select-none'
              if (st === 'na')
                return <div key={d} className={`${base} text-gray-600 cursor-not-allowed`}>{d}</div>
              if (st === 'sel')
                return <button key={d} onClick={() => clickDay(d)}
                  className={`${base} font-medium`} style={{ background:'#8f7547', color:'#ffffff' }}>{d}</button>
              if (st === 'inr')
                return <button key={d} onClick={() => clickDay(d)}
                  className={`${base} text-white`} style={{ background:'rgba(143,117,71,0.3)' }}>{d}</button>
              return <button key={d} onClick={() => clickDay(d)}
                className={`${base} text-gray-200 bg-gray-800 hover:bg-gray-700`}>{d}</button>
            })}
          </div>

          {mode === 'range' && (
            <div className="text-xs text-gray-500 mt-3 text-center">
              {pendingFrom ? 'Seleziona il secondo estremo' : 'Clicca due giorni disponibili'}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
