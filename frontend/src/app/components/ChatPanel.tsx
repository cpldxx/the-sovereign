import { useState, useRef, useEffect } from 'react';
import { motion } from 'motion/react';
import { HERMES_API } from '../api';


interface Msg {
  role: 'user' | 'head';
  text: string;
}

export function ChatPanel({ domain }: { domain: string }) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages, busy]);

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    setMessages(m => [...m, { role: 'user', text }]);
    setInput('');
    setBusy(true);
    try {
      // Prior turns give the Head Agent conversational context.
      const history = messages.map(m => ({
        role: m.role === 'user' ? 'user' : 'assistant',
        content: m.text,
      }));
      const res = await fetch(`${HERMES_API}/domains/${encodeURIComponent(domain)}/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, history }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail ?? `HTTP ${res.status}`);
      setMessages(m => [...m, { role: 'head', text: data.answer ?? '(no answer)' }]);
    } catch (e) {
      const reason = e instanceof TypeError
        ? 'Head Agent unreachable — is the Hermes service (port 8090) running?'
        : String(e instanceof Error ? e.message : e);
      setMessages(m => [...m, { role: 'head', text: `⚠ ${reason}` }]);
    } finally {
      setBusy(false);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  };

  return (
    <div style={{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* Header */}
      <div style={{ padding: '16px 28px 14px', borderBottom: '1px solid var(--border)' }}>
        <div style={{
          fontFamily: 'var(--mono)', fontSize: 10, letterSpacing: '0.2em',
          color: 'var(--text-faint)', textTransform: 'uppercase', marginBottom: 4,
        }}>
          Head Agent
        </div>
        <div style={{ fontFamily: 'var(--sans)', fontSize: 12, color: 'var(--text-dim)' }}>
          Grounded in the {domain} knowledge graph
        </div>
      </div>

      {/* Messages */}
      <div ref={scrollRef} style={{
        flex: 1, overflowY: 'auto', padding: '24px 28px',
        display: 'flex', flexDirection: 'column', gap: 18,
        scrollbarWidth: 'thin', scrollbarColor: 'var(--border-3) transparent',
      }}>
        {messages.length === 0 && !busy && (
          <div style={{
            margin: 'auto', textAlign: 'center', fontFamily: 'var(--mono)',
            fontSize: 11, color: 'var(--text-ghost)', letterSpacing: '0.1em', lineHeight: 2,
          }}>
            ASK THE HEAD AGENT<br />
            <span style={{ fontSize: 10, color: 'var(--text-ghost)' }}>
              "What do we know about X?" · "Ingest this: ..."
            </span>
          </div>
        )}

        {messages.map((m, i) => (
          <motion.div
            key={i}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2 }}
            style={{
              alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start',
              maxWidth: '78%',
            }}
          >
            <div style={{
              fontFamily: 'var(--mono)', fontSize: 9, letterSpacing: '0.16em',
              textTransform: 'uppercase', marginBottom: 5,
              color: m.role === 'user' ? 'var(--text-faint)' : 'var(--accent)',
              textAlign: m.role === 'user' ? 'right' : 'left',
            }}>
              {m.role === 'user' ? 'You' : '◆ Head'}
            </div>
            <div style={{
              fontFamily: 'var(--sans)', fontSize: 13.5, lineHeight: 1.6,
              padding: '11px 15px', borderRadius: 4, whiteSpace: 'pre-wrap',
              color: 'var(--text)',
              background: m.role === 'user' ? 'var(--surface-2)' : 'var(--surface)',
              border: `1px solid ${m.role === 'user' ? 'var(--border-2)' : 'var(--border)'}`,
              borderLeft: m.role === 'head' ? '2px solid var(--accent)' : undefined,
            }}>
              {m.text}
            </div>
          </motion.div>
        ))}

        {busy && (
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }}
            style={{ alignSelf: 'flex-start', maxWidth: '78%' }}
          >
            <div style={{
              fontFamily: 'var(--mono)', fontSize: 9, letterSpacing: '0.16em',
              textTransform: 'uppercase', marginBottom: 5, color: 'var(--accent)',
            }}>
              ◆ Head
            </div>
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '11px 15px', borderRadius: 4, background: 'var(--surface)',
              border: '1px solid var(--border)', borderLeft: '2px solid var(--accent)',
            }}>
              <motion.span
                animate={{ opacity: [0.3, 1, 0.3] }}
                transition={{ duration: 1.2, repeat: Infinity }}
                style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--text-dim)', letterSpacing: '0.1em' }}
              >
                querying knowledge graph…
              </motion.span>
            </div>
          </motion.div>
        )}
      </div>

      {/* Input */}
      <div style={{ padding: '14px 28px 20px', borderTop: '1px solid var(--border)' }}>
        <div style={{
          display: 'flex', alignItems: 'flex-end', gap: 10,
          background: 'var(--surface)', border: '1px solid var(--border-2)',
          borderRadius: 4, padding: '8px 10px 8px 14px',
        }}>
          <textarea
            ref={inputRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}
            placeholder="Ask the Head Agent… (Enter to send, Shift+Enter for newline)"
            rows={1}
            disabled={busy}
            style={{
              flex: 1, resize: 'none', background: 'transparent', border: 'none', outline: 'none',
              fontFamily: 'var(--sans)', fontSize: 13.5, color: 'var(--text)', lineHeight: 1.5,
              maxHeight: 120, padding: '4px 0',
            }}
          />
          <button
            onClick={send}
            disabled={!input.trim() || busy}
            style={{
              flexShrink: 0, padding: '7px 14px',
              background: input.trim() && !busy ? 'var(--accent)' : 'var(--surface-2)',
              border: 'none', borderRadius: 3,
              cursor: input.trim() && !busy ? 'pointer' : 'default',
              fontFamily: 'var(--mono)', fontSize: 10, letterSpacing: '0.1em', fontWeight: 600,
              color: input.trim() && !busy ? '#07090c' : 'var(--text-ghost)',
              textTransform: 'uppercase', transition: 'all 150ms',
            }}
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
