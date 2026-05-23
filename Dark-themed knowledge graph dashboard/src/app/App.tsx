import { useState, useEffect } from 'react';
import { KnowledgeGraph } from './components/KnowledgeGraph';
import { LandingPage } from './components/LandingPage';
import '../styles/graph.css';

/* ===== Mock data (used when API is unavailable) ===== */
const MOCK_GRAPHS: Record<string, { nodes: any[]; edges: any[] }> = {
  'AI Safety': {
    nodes: [
      { id: 'n1', label: 'Mesa-Optimization',        category: 'concept',      reliability: 0.62, tags: ['alignment','inner-opt'] },
      { id: 'n2', label: 'Reward Hacking',           category: 'concept',      reliability: 0.81, tags: ['alignment','specification'] },
      { id: 'n3', label: 'RLHF',                     category: 'technology',   reliability: 0.92, tags: ['training','human-feedback'] },
      { id: 'n4', label: 'Constitutional AI',        category: 'technology',   reliability: 0.88, tags: ['anthropic','self-critique'] },
      { id: 'n5', label: 'Stuart Russell',           category: 'person',       reliability: 0.95, tags: ['berkeley','author'] },
      { id: 'n6', label: 'Anthropic',                category: 'organization', reliability: 0.97, tags: ['lab','sf'] },
      { id: 'n7', label: 'Mechanistic Interp.',      category: 'technology',   reliability: 0.74, tags: ['transparency','circuits'] },
      { id: 'n8', label: 'Deceptive Alignment',      category: 'theory',       reliability: 0.41, tags: ['risk','speculative'] },
    ],
    edges: [
      { from: 'n1', to: 'n2', relation: 'related_to',   weight: 0.55 },
      { from: 'n1', to: 'n8', relation: 'implies',       weight: 0.78 },
      { from: 'n3', to: 'n4', relation: 'precedes',      weight: 0.86 },
      { from: 'n4', to: 'n6', relation: 'developed_by',  weight: 0.97 },
      { from: 'n7', to: 'n1', relation: 'investigates',  weight: 0.74 },
      { from: 'n5', to: 'n2', relation: 'cites',         weight: 0.61 },
    ],
  },
  'Quantum Computing': {
    nodes: [
      { id: 'q1', label: "Shor's Algorithm",   category: 'theory',       reliability: 0.98, tags: ['factoring','1994'] },
      { id: 'q2', label: 'Surface Code',       category: 'technology',   reliability: 0.91, tags: ['error-correction','topology'] },
      { id: 'q3', label: 'Topological Qubit',  category: 'technology',   reliability: 0.52, tags: ['majorana','microsoft'] },
      { id: 'q4', label: 'Peter Shor',         category: 'person',       reliability: 0.99, tags: ['mit','bell-labs'] },
      { id: 'q5', label: 'Quantum Supremacy',  category: 'event',        reliability: 0.79, tags: ['google','2019'] },
      { id: 'q6', label: 'Google Quantum AI',  category: 'organization', reliability: 0.95, tags: ['sycamore','willow'] },
      { id: 'q7', label: 'Decoherence',        category: 'concept',      reliability: 0.94, tags: ['noise','environment'] },
    ],
    edges: [
      { from: 'q1', to: 'q4', relation: 'authored_by',    weight: 0.99 },
      { from: 'q2', to: 'q7', relation: 'mitigates',      weight: 0.88 },
      { from: 'q5', to: 'q6', relation: 'claimed_by',     weight: 0.95 },
      { from: 'q3', to: 'q7', relation: 'addresses',      weight: 0.62 },
      { from: 'q2', to: 'q6', relation: 'implemented_by', weight: 0.81 },
    ],
  },
};

/* ===== Domain storage ===== */
const STORAGE_KEY = 'sovereign_domains';

function loadDomains(): string[] {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]');
  } catch {
    return [];
  }
}
function saveDomains(domains: string[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(domains));
}

const API_BASE = 'http://localhost:8080';

/* ===== App ===== */
export default function App() {
  const [domains, setDomains] = useState<string[]>(() => loadDomains());
  const [selected, setSelected] = useState<string>(() => loadDomains()[0] ?? '');

  // Fetch domains from API in background
  useEffect(() => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 3000);
    fetch(`${API_BASE}/domains`, { signal: controller.signal })
      .then(r => r.json())
      .then(d => {
        const apiDomains: string[] = d.domains ?? [];
        if (apiDomains.length > 0) {
          setDomains(apiDomains);
          setSelected((prev: string) => prev || apiDomains[0]);
        }
      })
      .catch(() => {})
      .finally(() => clearTimeout(timeout));
    return () => controller.abort();
  }, []);

  const handleCreateDomain = async (name: string) => {
    // Try to create via API
    try {
      await fetch(`${API_BASE}/domains`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, description: name }),
      });
    } catch {
      // API offline, just store locally
    }
    const updated = domains.includes(name) ? domains : [name, ...domains];
    setDomains(updated);
    saveDomains(updated);
    setSelected(name);
  };

  // New user → landing page
  if (domains.length === 0) {
    return <LandingPage onEnter={handleCreateDomain} />;
  }

  return (
    <div style={{
      display: 'grid',
      gridTemplateRows: 'var(--topbar-h) 1fr var(--statusbar-h)',
      height: '100%',
      background: 'var(--bg)',
      position: 'relative',
    }}>

      {/* ===== Topbar ===== */}
      <header style={{
        display: 'grid',
        gridTemplateColumns: 'auto 1fr auto',
        alignItems: 'center',
        gap: 24,
        padding: '0 18px',
        background: 'linear-gradient(to bottom, #0d1018 0%, #0a0c10 100%)',
        borderBottom: '1px solid var(--border)',
      }}>
        {/* Brand */}
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
          <span style={{ color: 'var(--accent)', fontSize: 16, fontFamily: 'var(--mono)', lineHeight: 1 }}>◆</span>
          <span style={{ fontFamily: 'var(--mono)', fontSize: 13, fontWeight: 600, letterSpacing: '0.18em', color: 'var(--text)', textTransform: 'uppercase' }}>
            Sovereign
          </span>
          <span style={{ fontFamily: 'var(--mono)', fontSize: 11, letterSpacing: '0.04em', color: 'var(--text-faint)' }}>
            knowledge graph
          </span>
        </div>

        {/* Domain tabs */}
        <div style={{ display: 'flex', gap: 2, overflowX: 'auto' }}>
          {domains.map(d => (
            <button
              key={d}
              onClick={() => setSelected(d)}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 8,
                padding: '8px 14px',
                fontFamily: 'var(--mono)', fontSize: 11, letterSpacing: '0.08em',
                textTransform: 'uppercase', cursor: 'pointer',
                border: selected === d ? '1px solid var(--border-2)' : '1px solid transparent',
                borderBottom: 'none',
                borderRadius: '3px 3px 0 0',
                color: selected === d ? 'var(--accent)' : 'var(--text-dim)',
                background: selected === d ? 'var(--bg-2)' : 'transparent',
                position: 'relative',
                transition: 'color 120ms, background 120ms',
                whiteSpace: 'nowrap',
              }}
            >
              {d}
            </button>
          ))}
          <button
            onClick={() => {
              const name = prompt('Domain name:');
              if (name?.trim()) handleCreateDomain(name.trim());
            }}
            style={{
              padding: '8px 12px',
              fontFamily: 'var(--mono)', fontSize: 11, letterSpacing: '0.08em',
              color: 'var(--text-ghost)', cursor: 'pointer',
              border: '1px solid transparent', borderBottom: 'none',
              borderRadius: '3px 3px 0 0', background: 'transparent',
              transition: 'color 120ms',
            }}
            onMouseEnter={e => (e.currentTarget.style.color = 'var(--text-dim)')}
            onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-ghost)')}
          >
            + NEW
          </button>
        </div>

        {/* Status */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--text-dim)' }}>
          <span style={{
            width: 7, height: 7, borderRadius: '50%',
            background: 'var(--cyan)', boxShadow: '0 0 6px var(--cyan)',
            display: 'inline-block',
          }} />
          LIVE
        </div>
      </header>

      {/* ===== Canvas ===== */}
      <main style={{ position: 'relative', minHeight: 0, overflow: 'hidden', background: 'var(--bg-2)' }}>
        <KnowledgeGraph
          key={selected}
          domain={selected}
        />
      </main>

      {/* ===== Statusbar ===== */}
      <footer style={{
        display: 'flex', alignItems: 'center', gap: 18,
        padding: '0 16px',
        background: '#060709', borderTop: '1px solid var(--border)',
        fontFamily: 'var(--mono)', fontSize: 10.5, color: 'var(--text-dim)',
        letterSpacing: '0.04em',
      }}>
        <span style={{ color: 'var(--text-faint)' }}>DOMAIN</span>
        <span style={{ color: 'var(--accent)' }}>{selected}</span>
        <span style={{ color: 'var(--text-faint)', marginLeft: 'auto' }}>
          {new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })}
        </span>
      </footer>
    </div>
  );
}
