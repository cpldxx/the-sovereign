import { useState } from 'react';
import { Loader2, Pencil, RefreshCw } from 'lucide-react';
import { kg, type Ontology } from '../lib/api';
import { categoryColor } from '../lib/colors';

const parse = (s: string) => s.split(/[\n,]/).map(t => t.trim()).filter(Boolean);

export function OntologyPanel({ domain, ontology, generated, counts, onChanged }: {
  domain: string;
  ontology: Ontology;
  generated: boolean;
  counts: Record<string, number>;
  onChanged: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [entities, setEntities] = useState('');
  const [relations, setRelations] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [regenerating, setRegenerating] = useState(false);

  function startEdit() {
    setEntities(ontology.entity_types.join('\n'));
    setRelations(ontology.relation_types.join('\n'));
    setEditing(true);
    setError(null);
  }

  async function save() {
    setBusy(true); setError(null);
    try {
      await kg.saveOntology(domain, { entity_types: parse(entities), relation_types: parse(relations) });
      setEditing(false);
      await onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function regenerate() {
    setRegenerating(true); setError(null);
    try {
      await kg.generateOntology(domain);
    } catch (e) {
      setError((e as Error).message);
      setRegenerating(false);
      return;
    }
    // Generation runs in the background for minutes; App polls while it is pending
    // only for never-generated ontologies, so poll here for a regenerate.
    const before = JSON.stringify(ontology);
    for (let i = 0; i < 60; i++) {
      await new Promise(r => setTimeout(r, 5000));
      const now = await kg.ontology(domain).catch(() => null);
      if (now && JSON.stringify(now.ontology) !== before) break;
    }
    setRegenerating(false);
    await onChanged();
  }

  const offOntology = Object.keys(counts).filter(c => !ontology.entity_types.includes(c));

  return (
    <div className="h-full overflow-y-auto p-4">
      <p className="mb-4 text-[11.5px] leading-relaxed text-faint">
        The grammar of this knowledge graph. Facts whose category is not listed are rejected at ingest, and edges whose
        relation is not listed are dropped — enforced in code, not just in prompts.
        {!generated && ' This domain still uses the default grammar while its ontology is generated.'}
      </p>

      {editing ? (
        <div className="space-y-3">
          <label className="block">
            <span className="text-[11px] text-dim">Entity types (one per line)</span>
            <textarea value={entities} onChange={e => setEntities(e.target.value)} rows={7}
              className="mt-1 w-full rounded-md border border-line-2 bg-bg px-3 py-2 font-mono text-[12px] outline-none focus:border-gold/60" />
          </label>
          <label className="block">
            <span className="text-[11px] text-dim">Relation types (one per line)</span>
            <textarea value={relations} onChange={e => setRelations(e.target.value)} rows={6}
              className="mt-1 w-full rounded-md border border-line-2 bg-bg px-3 py-2 font-mono text-[12px] outline-none focus:border-gold/60" />
          </label>
          <p className="text-[10.5px] text-faint">Names are normalized: "Price Pattern" → price_pattern. Existing nodes keep their categories.</p>
          {error && <p className="text-xs text-bad">{error}</p>}
          <div className="flex gap-2">
            <button onClick={save} disabled={busy} className="flex items-center gap-2 rounded-md bg-gold px-4 py-1.5 text-sm text-black disabled:opacity-40">
              {busy && <Loader2 size={13} className="animate-spin" />} Save
            </button>
            <button onClick={() => setEditing(false)} className="px-3 text-sm text-dim hover:text-ink">Cancel</button>
          </div>
        </div>
      ) : (
        <>
          <section className="mb-5">
            <h3 className="mb-2 font-mono text-[10px] tracking-widest text-faint uppercase">Entity types</h3>
            <ul className="space-y-1">
              {ontology.entity_types.map(t => (
                <li key={t} className="flex items-center gap-2 text-[12.5px]">
                  <span className="h-2.5 w-2.5 rounded-full" style={{ background: categoryColor(t, ontology.entity_types) }} />
                  <span className="font-mono">{t}</span>
                  <span className="ml-auto font-mono text-[11px] text-faint">{counts[t] ?? 0}</span>
                </li>
              ))}
              {offOntology.map(t => (
                <li key={t} className="flex items-center gap-2 text-[12.5px] text-faint" title="Category no longer in the ontology">
                  <span className="h-2.5 w-2.5 rounded-full border border-faint" />
                  <span className="font-mono line-through">{t}</span>
                  <span className="ml-auto font-mono text-[11px]">{counts[t]}</span>
                </li>
              ))}
            </ul>
          </section>
          <section className="mb-5">
            <h3 className="mb-2 font-mono text-[10px] tracking-widest text-faint uppercase">Relation types</h3>
            <div className="flex flex-wrap gap-1.5">
              {ontology.relation_types.map(r => (
                <span key={r} className="rounded border border-line-2 px-2 py-0.5 font-mono text-[11.5px] text-dim">{r}</span>
              ))}
            </div>
          </section>
          {error && <p className="mb-3 text-xs text-bad">{error}</p>}
          <div className="flex gap-2">
            <button onClick={startEdit} className="flex items-center gap-1.5 rounded-md border border-line-2 px-3 py-1.5 text-xs text-dim hover:text-ink">
              <Pencil size={12} /> Edit
            </button>
            <button
              onClick={regenerate}
              disabled={regenerating}
              className="flex items-center gap-1.5 rounded-md border border-line-2 px-3 py-1.5 text-xs text-dim hover:text-ink disabled:opacity-50"
              title="Ask the Ontologist to design the grammar again from the domain description"
            >
              <RefreshCw size={12} className={regenerating ? 'animate-spin' : ''} />
              {regenerating ? 'Regenerating… (a few min)' : 'Regenerate'}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
