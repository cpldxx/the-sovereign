/** Stable category colors: a category keeps its color for as long as its index in the ontology. */
const PALETTE = [
  '#e2b356', '#5fb3f9', '#4ade80', '#f472b6', '#a78bfa',
  '#fb923c', '#2dd4bf', '#f87171', '#facc15', '#94a3b8',
];

/** Categories outside the current ontology (older data after an ontology change). */
const OFF_ONTOLOGY = '#64748b';

/** Always a 7-char hex, so callers can append an alpha suffix. */
export function categoryColor(category: string, entityTypes: string[]): string {
  const i = entityTypes.indexOf(category);
  return i >= 0 ? PALETTE[i % PALETTE.length] : OFF_ONTOLOGY;
}
