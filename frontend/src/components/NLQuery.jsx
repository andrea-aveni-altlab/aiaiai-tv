import { useState, useRef, useEffect } from 'react'
import { X, Send } from 'lucide-react'
import { api } from '../api'

export default function NLQuery({ date, target, onClose }) {
  const [input, setInput]       = useState('')
  const [messages, setMessages] = useState([])
  const [loading, setLoading]   = useState(false)
  const bottomRef               = useRef(null)
  const inputRef                = useRef(null)

  useEffect(() => { inputRef.current?.focus() }, [])
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  async function send() {
    const q = input.trim()
    if (!q || loading) return
    setInput('')
    setMessages(prev => [...prev, { role: 'user', text: q }])
    setLoading(true)
    try {
      const res = await api.nl(q, date, target)
      setMessages(prev => [...prev, { role: 'assistant', text: res.response }])
    } catch (err) {
      setMessages(prev => [...prev, { role: 'error', text: err.message }])
    } finally {
      setLoading(false)
    }
  }

  function handleKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
    if (e.key === 'Escape') onClose()
  }

  const suggestions = [
    'Qual è il programma più visto di ieri?',
    'Mostrami la share del TG1 delle 20',
    'Chi ha vinto il prime time?',
    'Confronta RAI 1 e Canale 5 in prima serata',
  ]

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full sm:max-w-2xl bg-gray-900 border border-gray-700
                      rounded-t-2xl sm:rounded-2xl flex flex-col shadow-2xl"
           style={{maxHeight:'85vh'}}>

        <div className="flex items-center justify-between p-4 border-b border-gray-800">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full animate-pulse" style={{background:'#E8860C'}} />
            <span className="font-semibold text-white text-sm">Chiedi agli ascolti</span>
            <span className="text-gray-500 text-xs">{date} · {target}</span>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white transition-colors">
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-4" style={{minHeight:200}}>
          {messages.length === 0 && (
            <div>
              <p className="text-gray-500 text-sm mb-4">Fai una domanda sugli ascolti TV del {date}</p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {suggestions.map((s, i) => (
                  <button key={i} onClick={() => { setInput(s); inputRef.current?.focus() }}
                    className="text-left text-xs bg-gray-800 hover:bg-gray-700 border border-gray-700
                               rounded-lg px-3 py-2 text-gray-400 hover:text-gray-200 transition-colors">
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div className={`max-w-[85%] rounded-xl px-4 py-2.5 text-sm ${
                m.role === 'user' ? 'text-white rounded-br-sm'
                : m.role === 'error' ? 'bg-red-900/50 border border-red-800 text-red-300'
                : 'bg-gray-800 text-gray-200 rounded-bl-sm'
              }`} style={m.role === 'user' ? {background:'#0C447C'} : {}}>
                <p className="whitespace-pre-wrap leading-relaxed">{m.text}</p>
              </div>
            </div>
          ))}
          {loading && (
            <div className="flex justify-start">
              <div className="bg-gray-800 rounded-xl rounded-bl-sm px-4 py-3">
                <div className="flex gap-1.5">
                  {[0,1,2].map(i => (
                    <div key={i} className="w-1.5 h-1.5 rounded-full bg-gray-500 animate-bounce"
                         style={{animationDelay:`${i*0.15}s`}} />
                  ))}
                </div>
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        <div className="p-4 border-t border-gray-800">
          <div className="flex items-end gap-2 bg-gray-800 rounded-xl px-3 py-2">
            <textarea ref={inputRef} value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKey}
              placeholder="Es: quanti spettatori ha fatto Affari Tuoi?"
              rows={1}
              className="flex-1 bg-transparent text-white text-sm resize-none
                         placeholder-gray-500 focus:outline-none leading-relaxed"
              style={{maxHeight:120}} />
            <button onClick={send} disabled={!input.trim() || loading}
              className="p-1.5 rounded-lg transition-colors disabled:opacity-30"
              style={{background: input.trim() ? '#E8860C' : '#374151'}}>
              <Send size={14} className="text-white" />
            </button>
          </div>
          <p className="text-gray-700 text-xs mt-2 text-center">Invio per inviare · Esc per chiudere</p>
        </div>
      </div>
    </div>
  )
}
