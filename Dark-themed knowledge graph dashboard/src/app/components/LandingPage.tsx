import { useState, useRef, useEffect } from 'react';
import { motion, AnimatePresence } from 'motion/react';

interface LandingPageProps {
  onEnter: (domainName: string) => void;
}

const SUGGESTIONS = [
  'Quantitative trading strategy research',
  'Biotech drug discovery pipeline',
  'Supply chain risk monitoring',
  'Competitive intelligence for SaaS',
];

export function LandingPage({ onEnter }: LandingPageProps) {
  const [input, setInput] = useState('');
  const [phase, setPhase] = useState<'intro' | 'prompt' | 'submitting'>('intro');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const t = setTimeout(() => setPhase('prompt'), 900);
    return () => clearTimeout(t);
  }, []);

  useEffect(() => {
    if (phase === 'prompt') {
      setTimeout(() => inputRef.current?.focus(), 300);
    }
  }, [phase]);

  const handleSubmit = () => {
    if (!input.trim()) return;
    setPhase('submitting');
    setTimeout(() => onEnter(input.trim()), 700);
  };

  return (
    <div style={{
      width: '100%', height: '100%',
      background: 'var(--bg)',
      display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center',
      fontFamily: 'var(--mono)',
      position: 'relative', overflow: 'hidden',
    }}>

      {/* subtle radial glow */}
      <div style={{
        position: 'absolute', inset: 0, pointerEvents: 'none',
        background: 'radial-gradient(circle at 50% 0%, rgba(240,184,110,0.04) 0%, transparent 55%)',
      }} />

      {/* subtle grid */}
      <div style={{
        position: 'absolute', inset: 0, pointerEvents: 'none', opacity: 0.03,
        backgroundImage: 'linear-gradient(rgba(255,255,255,1) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,1) 1px, transparent 1px)',
        backgroundSize: '48px 48px',
      }} />

      <div style={{ position: 'relative', zIndex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 48, width: '100%', maxWidth: 480, padding: '0 32px' }}>

        {/* Brand */}
        <motion.div
          initial={{ opacity: 0, y: -6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}
        >
          <div style={{ fontSize: 10, color: 'var(--text-faint)', letterSpacing: '0.3em', textTransform: 'uppercase' }}>
            the
          </div>
          <div style={{ fontSize: 22, color: 'var(--accent)', letterSpacing: '0.18em', textTransform: 'uppercase', fontWeight: 500 }}>
            Sovereign
          </div>
          <div style={{ width: 32, height: 1, background: 'var(--border-2)', marginTop: 4 }} />
          <div style={{ fontSize: 10, color: 'var(--text-faint)', letterSpacing: '0.1em' }}>
            knowledge graph · agent workspace
          </div>
        </motion.div>

        {/* Prompt */}
        <AnimatePresence>
          {phase !== 'intro' && (
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.4 }}
              style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: 20 }}
            >
              <div style={{ fontSize: 10, color: 'var(--text-faint)', letterSpacing: '0.2em', textTransform: 'uppercase', textAlign: 'center' }}>
                What are you building?
              </div>

              {/* Input */}
              <div style={{
                display: 'flex', alignItems: 'center',
                borderBottom: '1px solid var(--border-2)',
                transition: 'border-color 200ms',
              }}
                onFocus={e => (e.currentTarget.style.borderBottomColor = 'var(--accent)')}
                onBlur={e => (e.currentTarget.style.borderBottomColor = 'var(--border-2)')}
              >
                <span style={{ fontSize: 11, color: 'var(--accent)', marginRight: 8 }}>›</span>
                <input
                  ref={inputRef}
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && handleSubmit()}
                  placeholder="Describe your domain..."
                  disabled={phase === 'submitting'}
                  style={{
                    flex: 1, background: 'transparent', border: 'none', outline: 'none',
                    fontFamily: 'var(--mono)', fontSize: 13, color: 'var(--text)',
                    padding: '10px 0', letterSpacing: '0.02em',
                  }}
                />
                <button
                  onClick={handleSubmit}
                  disabled={!input.trim() || phase === 'submitting'}
                  style={{
                    background: 'none', border: 'none', cursor: input.trim() ? 'pointer' : 'default',
                    fontFamily: 'var(--mono)', fontSize: 10, color: input.trim() ? 'var(--accent)' : 'var(--text-ghost)',
                    letterSpacing: '0.1em', textTransform: 'uppercase', padding: '0 0 0 12px',
                    transition: 'color 150ms',
                  }}
                >
                  Enter
                </button>
              </div>

              {/* Suggestions */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                {SUGGESTIONS.map((s, i) => (
                  <motion.button
                    key={s}
                    initial={{ opacity: 0, x: -6 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: 0.08 + i * 0.06 }}
                    onClick={() => { setInput(s); inputRef.current?.focus(); }}
                    style={{
                      background: 'none', border: 'none', cursor: 'pointer', textAlign: 'left',
                      fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--text-ghost)',
                      padding: '5px 0', letterSpacing: '0.02em', display: 'flex', alignItems: 'center', gap: 8,
                      transition: 'color 120ms',
                    }}
                    onMouseEnter={e => (e.currentTarget.style.color = 'var(--text-dim)')}
                    onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-ghost)')}
                  >
                    <span style={{ color: 'var(--border-3)' }}>—</span>
                    {s}
                  </motion.button>
                ))}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Submitting overlay */}
        <AnimatePresence>
          {phase === 'submitting' && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              style={{
                position: 'fixed', inset: 0, background: 'var(--bg)',
                display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 20,
              }}
            >
              <motion.div
                animate={{ opacity: [0.3, 1, 0.3] }}
                transition={{ duration: 1.4, repeat: Infinity }}
                style={{ fontSize: 10, color: 'var(--text-faint)', letterSpacing: '0.35em', textTransform: 'uppercase' }}
              >
                Initializing domain
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
