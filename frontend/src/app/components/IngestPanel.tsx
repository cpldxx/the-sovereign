import { useState } from 'react';
import { KG_API } from '../api';


interface IngestResult {
  domain: string;
  total: number;
  stored: number;
  rejected: number;
  edges_created: number;
  details: Array<{ uid: string; status: string; reason?: string; reliability?: number }>;
}

export function IngestPanel({ domain, onSuccess }: { domain: string; onSuccess?: () => void }) {
  const [rawText, setRawText] = useState('');
  const [source, setSource] = useState('');
  const [phase, setPhase] = useState<'idle' | 'running' | 'done' | 'error'>('idle');
  const [result, setResult] = useState<IngestResult | null>(null);

  const canSubmit = rawText.trim().length > 0 && phase !== 'running';

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setPhase('running');
    setResult(null);
    try {
      const res = await fetch(
        `${KG_API}/domains/${encodeURIComponent(domain)}/ingest`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ raw_text: rawText, source: source.trim() || 'user_input' }),
        }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: IngestResult = await res.json();
      setResult(data);
      setPhase('done');
      setRawText('');
      if (data.stored > 0) onSuccess?.();
    } catch {
      setPhase('error');
    }
  };

  return (
    <div style={{
      width: '100%',
      height: '100%',
      display: 'grid',
      gridTemplateColumns: '1fr 1fr',
      overflow: 'hidden',
    }}>
      {/* Left: Input */}
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        padding: 28,
        borderRight: '1px solid var(--border)',
        overflow: 'hidden',
      }}>
        <div style={{ marginBottom: 22 }}>
          <div style={{
            fontFamily: 'var(--mono)', fontSize: 10, letterSpacing: '0.2em',
            color: 'var(--text-faint)', textTransform: 'uppercase', marginBottom: 5,
          }}>
            Pipeline / Ingest
          </div>
          <div style={{ fontFamily: 'var(--sans)', fontSize: 12, color: 'var(--text-dim)', lineHeight: 1.6 }}>
            Raw text &rarr; Ingestor &rarr; Gatekeeper &rarr; KG
          </div>
        </div>

        {/* Source field */}
        <div style={{ marginBottom: 14 }}>
          <label style={{
            fontFamily: 'var(--mono)', fontSize: 9.5, letterSpacing: '0.14em',
            color: 'var(--text-faint)', textTransform: 'uppercase', display: 'block', marginBottom: 6,
          }}>
            Source
          </label>
          <input
            value={source}
            onChange={e => setSource(e.target.value)}
            placeholder="URL, paper name, API name..."
            style={{
              width: '100%', background: 'var(--surface)', border: '1px solid var(--border-2)',
              borderRadius: 2, padding: '7px 10px', fontFamily: 'var(--mono)', fontSize: 11,
              color: 'var(--text)', outline: 'none', transition: 'border-color 150ms',
            }}
            onFocus={e => (e.target.style.borderColor = 'var(--accent-dim)')}
            onBlur={e => (e.target.style.borderColor = 'var(--border-2)')}
          />
        </div>

        {/* Raw text */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', marginBottom: 14, minHeight: 0 }}>
          <label style={{
            fontFamily: 'var(--mono)', fontSize: 9.5, letterSpacing: '0.14em',
            color: 'var(--text-faint)', textTransform: 'uppercase', display: 'block', marginBottom: 6,
          }}>
            Raw data
          </label>
          <textarea
            value={rawText}
            onChange={e => setRawText(e.target.value)}
            placeholder="Paste any text — news, research papers, notes, data feeds..."
            style={{
              flex: 1, resize: 'none', background: 'var(--surface)', border: '1px solid var(--border-2)',
              borderRadius: 2, padding: '10px 12px', fontFamily: 'var(--mono)', fontSize: 11,
              color: 'var(--text)', outline: 'none', lineHeight: 1.65,
              scrollbarWidth: 'thin', scrollbarColor: 'var(--border-3) transparent',
              transition: 'border-color 150ms',
            }}
            onFocus={e => (e.target.style.borderColor = 'var(--accent-dim)')}
            onBlur={e => (e.target.style.borderColor = 'var(--border-2)')}
          />
        </div>

        {/* Submit */}
        <button
          onClick={handleSubmit}
          disabled={!canSubmit}
          style={{
            padding: '9px 16px',
            background: canSubmit ? 'var(--accent)' : 'var(--surface-2)',
            border: `1px solid ${canSubmit ? 'var(--accent)' : 'var(--border)'}`,
            borderRadius: 2, cursor: canSubmit ? 'pointer' : 'default',
            fontFamily: 'var(--mono)', fontSize: 11, letterSpacing: '0.12em', fontWeight: 600,
            color: canSubmit ? '#07090c' : 'var(--text-ghost)',
            textTransform: 'uppercase', transition: 'all 150ms',
          }}
        >
          {phase === 'running' ? 'Processing...' : 'Run pipeline →'}
        </button>
      </div>

      {/* Right: Result */}
      <div style={{
        padding: 28, overflowY: 'auto',
        scrollbarWidth: 'thin', scrollbarColor: 'var(--border-3) transparent',
      }}>
        {phase === 'idle' && (
          <div style={{
            display: 'flex', flexDirection: 'column',
            alignItems: 'center', justifyContent: 'center',
            height: '100%', gap: 8,
          }}>
            <div style={{
              fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--text-ghost)',
              letterSpacing: '0.16em', textTransform: 'uppercase',
            }}>
              Awaiting ingest
            </div>
          </div>
        )}

        {phase === 'running' && (
          <div style={{
            display: 'flex', flexDirection: 'column',
            alignItems: 'center', justifyContent: 'center',
            height: '100%', gap: 14,
          }}>
            <div style={{
              width: 28, height: 28, border: '1.5px solid var(--border-2)',
              borderTopColor: 'var(--accent)', borderRadius: '50%',
              animation: 'spin 0.9s linear infinite',
            }} />
            <div style={{
              fontFamily: 'var(--mono)', fontSize: 10,
              color: 'var(--text-faint)', letterSpacing: '0.14em', textTransform: 'uppercase',
            }}>
              Running pipeline
            </div>
            <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
          </div>
        )}

        {phase === 'error' && (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
            <div style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--warn)' }}>
              Pipeline failed — check backend logs.
            </div>
          </div>
        )}

        {phase === 'done' && result && (
          <div style={{ fontFamily: 'var(--mono)' }}>
            {/* Stats row */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 1, marginBottom: 24 }}>
              {[
                { k: 'Total', v: result.total, color: 'var(--text)' },
                { k: 'Stored', v: result.stored, color: 'var(--cyan)' },
                { k: 'Rejected', v: result.rejected, color: 'var(--warn)' },
                { k: 'Edges', v: result.edges_created, color: 'var(--accent)' },
              ].map(({ k, v, color }) => (
                <div key={k} style={{
                  background: 'var(--surface)', padding: '12px 10px',
                  border: '1px solid var(--border)',
                }}>
                  <div style={{
                    fontSize: 9, letterSpacing: '0.16em', color: 'var(--text-faint)',
                    textTransform: 'uppercase', marginBottom: 6,
                  }}>
                    {k}
                  </div>
                  <div style={{ fontSize: 22, fontWeight: 600, color }}>{v}</div>
                </div>
              ))}
            </div>

            {/* Node list */}
            <div style={{
              fontSize: 9.5, letterSpacing: '0.14em', color: 'var(--text-faint)',
              textTransform: 'uppercase', marginBottom: 10,
            }}>
              Nodes
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
              {result.details.map((d) => (
                <div key={d.uid} style={{
                  display: 'flex', alignItems: 'center', gap: 10, padding: '7px 10px',
                  background: 'var(--surface)', border: '1px solid var(--border)',
                }}>
                  <span style={{
                    fontSize: 9, letterSpacing: '0.1em', fontWeight: 600, padding: '1px 5px',
                    borderRadius: 2, textTransform: 'uppercase', flexShrink: 0,
                    color: d.status === 'stored' ? 'var(--cyan)' : 'var(--warn)',
                    background: d.status === 'stored' ? 'rgba(79,209,197,0.1)' : 'rgba(252,129,129,0.1)',
                  }}>
                    {d.status}
                  </span>
                  <span style={{
                    flex: 1, fontSize: 10, color: 'var(--text-dim)',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>
                    {d.uid}
                  </span>
                  {d.reliability !== undefined && (
                    <span style={{ fontSize: 10, color: 'var(--text-faint)', flexShrink: 0 }}>
                      {Math.round(d.reliability * 100)}%
                    </span>
                  )}
                  {d.reason && (
                    <span style={{
                      fontSize: 9.5, color: 'var(--text-faint)', flexShrink: 0,
                      maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>
                      {d.reason}
                    </span>
                  )}
                </div>
              ))}
            </div>

            {/* Re-ingest button */}
            <button
              onClick={() => setPhase('idle')}
              style={{
                marginTop: 20, padding: '7px 14px',
                background: 'transparent', border: '1px solid var(--border-2)',
                borderRadius: 2, cursor: 'pointer',
                fontFamily: 'var(--mono)', fontSize: 10, letterSpacing: '0.1em',
                color: 'var(--text-dim)', textTransform: 'uppercase', transition: 'all 150ms',
              }}
              onMouseEnter={e => (e.currentTarget.style.borderColor = 'var(--border-3)')}
              onMouseLeave={e => (e.currentTarget.style.borderColor = 'var(--border-2)')}
            >
              + Ingest more
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
