/** Stable, muted category colors (graphite theme — tints, not hues): a category keeps its color for as long as its index in the ontology. */
const PALETTE = [
  '#e8e4da', '#9fb1c2', '#a8bba5', '#c4aab5', '#b2abc8',
  '#c9b59c', '#9cbdb8', '#c39d9d', '#c6c19c', '#8f949a',
];

/** Categories outside the current ontology (older data after an ontology change). */
const OFF_ONTOLOGY = '#5c5c5c';

/** Always a 7-char hex, so callers can append an alpha suffix. */
export function categoryColor(category: string, entityTypes: string[]): string {
  const i = entityTypes.indexOf(category);
  return i >= 0 ? PALETTE[i % PALETTE.length] : OFF_ONTOLOGY;
}
