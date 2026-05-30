import { useState, useEffect } from 'react';
import { Routes, Route, useNavigate, useParams, Navigate } from 'react-router';
import { KnowledgeGraph } from './components/KnowledgeGraph';
import { LandingPage } from './components/LandingPage';
import '../styles/graph.css';

const API_BASE = 'http://localhost:8080';

/* ===== Domain storage ===== */
const STORAGE_KEY = 'sovereign_domains';
function loadDomains(): string[] {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]'); }
  catch { return []; }
}
function saveDomains(d: string[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(d));
}

/* ===== Landing ===== */
function Landing() {
  const navigate = useNavigate();

  const handleEnter = async (name: string) => {
    try {
      await fetch(`${API_BASE}/domains`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, description: name }),
      });
    } catch {}
    const existing = loadDomains();
    const updated = existing.includes(name) ? existing : [name, ...existing];
    saveDomains(updated);
    navigate(`/workspace/${encodeURIComponent(name)}`);
  };

  return <LandingPage onEnter={handleEnter} />;
}

/* ===== Workspace ===== */
function Workspace() {
  const navigate = useNavigate();
  const { domain: domainParam } = useParams<{ domain: string }>();
  const [domains, setDomains] = useState<string[]>(() => loadDomains());

  const activeDomain = domainParam ? decodeURIComponent(domainParam) : '';

  // If no domains or invalid domain param, back to landing
  useEffect(() => {
    if (domains.length === 0) navigate('/');
  }, [domains, navigate]);

  // Sync domains from API
  useEffect(() => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 3000);
    fetch(`${API_BASE}/domains`, { signal: controller.signal })
      .then(r => r.json())
      .then(d => {
        const api: string[] = d.domains ?? [];
        if (api.length > 0) {
          setDomains(api);
          saveDomains(api);
        }
      })
      .catch(() => {})
      .finally(() => clearTimeout(timeout));
    return () => controller.abort();
  }, []);

  const handleNewDomain = async () => {
    const name = prompt('Domain name:');
    if (!name?.trim()) return;
    try {
      await fetch(`${API_BASE}/domains`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim(), description: name.trim() }),
      });
    } catch {}
    const updated = domains.includes(name.trim()) ? domains : [name.trim(), ...domains];
    setDomains(updated);
    saveDomains(updated);
    navigate(`/workspace/${encodeURIComponent(name.trim())}`);
  };

  return (
    <div style={{
      display: 'grid',
      gridTemplateRows: 'var(--topbar-h) 1fr var(--statusbar-h)',
      height: '100%',
      background: 'var(--bg)',
    }}>

      {/* Topbar */}
      <header style={{
        display: 'grid', gridTemplateColumns: 'auto 1fr auto',
        alignItems: 'center', gap: 24, padding: '0 18px',
        background: 'linear-gradient(to bottom, #0d1018 0%, #0a0c10 100%)',
        borderBottom: '1px solid var(--border)',
      }}>
        {/* Brand — click to go back to landing */}
        <button
          onClick={() => navigate('/')}
          style={{
            display: 'flex', alignItems: 'baseline', gap: 8,
            background: 'none', border: 'none', cursor: 'pointer',
            padding: 0, transition: 'opacity 150ms',
          }}
          onMouseEnter={e => (e.currentTarget.style.opacity = '0.7')}
          onMouseLeave={e => (e.currentTarget.style.opacity = '1')}
        >
          <span style={{ color: 'var(--accent)', fontSize: 16, fontFamily: 'var(--mono)' }}>◆</span>
          <span style={{ fontFamily: 'var(--mono)', fontSize: 13, fontWeight: 600, letterSpacing: '0.18em', color: 'var(--text)', textTransform: 'uppercase' }}>
            Sovereign
          </span>
          <span style={{ fontFamily: 'var(--mono)', fontSize: 11, letterSpacing: '0.04em', color: 'var(--text-faint)' }}>
            knowledge graph
          </span>
        </button>

        {/* Domain tabs */}
        <div style={{ display: 'flex', gap: 2, overflowX: 'auto' }}>
          {domains.map(d => (
            <button
              key={d}
              onClick={() => navigate(`/workspace/${encodeURIComponent(d)}`)}
              style={{
                display: 'inline-flex', alignItems: 'center', padding: '8px 14px',
                fontFamily: 'var(--mono)', fontSize: 11, letterSpacing: '0.08em',
                textTransform: 'uppercase', cursor: 'pointer',
                border: activeDomain === d ? '1px solid var(--border-2)' : '1px solid transparent',
                borderBottom: 'none', borderRadius: '3px 3px 0 0',
                color: activeDomain === d ? 'var(--accent)' : 'var(--text-dim)',
                background: activeDomain === d ? 'var(--bg-2)' : 'transparent',
                transition: 'color 120ms, background 120ms', whiteSpace: 'nowrap',
              }}
            >
              {d}
            </button>
          ))}
          <button
            onClick={handleNewDomain}
            style={{
              padding: '8px 12px', fontFamily: 'var(--mono)', fontSize: 11,
              letterSpacing: '0.08em', color: 'var(--text-ghost)', cursor: 'pointer',
              border: '1px solid transparent', borderBottom: 'none',
              borderRadius: '3px 3px 0 0', background: 'transparent', transition: 'color 120ms',
            }}
            onMouseEnter={e => (e.currentTarget.style.color = 'var(--text-dim)')}
            onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-ghost)')}
          >
            + NEW
          </button>
        </div>

        {/* Status */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--text-dim)' }}>
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--cyan)', boxShadow: '0 0 6px var(--cyan)', display: 'inline-block' }} />
          LIVE
        </div>
      </header>

      {/* Canvas */}
      <main style={{ position: 'relative', minHeight: 0, overflow: 'hidden', background: 'var(--bg-2)' }}>
        <KnowledgeGraph key={activeDomain} domain={activeDomain} />
      </main>

      {/* Statusbar */}
      <footer style={{
        display: 'flex', alignItems: 'center', gap: 18, padding: '0 16px',
        background: '#060709', borderTop: '1px solid var(--border)',
        fontFamily: 'var(--mono)', fontSize: 10.5, color: 'var(--text-dim)', letterSpacing: '0.04em',
      }}>
        <span style={{ color: 'var(--text-faint)' }}>DOMAIN</span>
        <span style={{ color: 'var(--accent)' }}>{activeDomain}</span>
        <span style={{ marginLeft: 'auto', color: 'var(--text-faint)' }}>
          {new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })}
        </span>
      </footer>
    </div>
  );
}

/* ===== Router ===== */
export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/workspace/:domain" element={<Workspace />} />
      <Route path="/workspace" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
