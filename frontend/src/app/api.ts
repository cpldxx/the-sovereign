/** Backend endpoints. Override with VITE_KG_URL / VITE_HERMES_URL. */

/** Sovereign KG API — domains, graph, ingest, query. */
export const KG_API: string = import.meta.env.VITE_KG_URL ?? 'http://localhost:8080';

/** Hermes service — the Head Agent (Ask panel). */
export const HERMES_API: string = import.meta.env.VITE_HERMES_URL ?? 'http://localhost:8090';

/**
 * Create a domain and return its id. The id is derived from the name
 * ('Quant Trading' -> 'quant_trading'); use it for every later call.
 */
export async function createDomain(name: string): Promise<string> {
  const res = await fetch(`${KG_API}/domains`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, description: name }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ?? `HTTP ${res.status}`);
  return data.domain;
}
