/** The browser's own speech synthesis — the fallback when Hermes' local voice is unavailable. */

export const canSpeak = 'speechSynthesis' in window;

/** Read text aloud, minus markdown punctuation and long uids. */
export function speak(text: string) {
  if (!canSpeak) return;
  const plain = text
    .replace(/```[\s\S]*?```/g, '')
    .replace(/`[^`]*`/g, '')
    .replace(/\|/g, ' ')
    // A "-" only as a bullet or a rule: "-3.2%" was read as "3.2%" when every dash went (2026-10-14).
    .replace(/^\s*(?:[-*+]\s+|[-*_]{3,}\s*$)/gm, ' ')
    .replace(/:?-{2,}:?/g, ' ')   // table separators ("|---|:--:|" — its pipes are spaces by now) and "--"
    .replace(/[#*_>~]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  const utterance = new SpeechSynthesisUtterance(plain);
  // The default voice follows the browser language; English text gets an English voice.
  if (/^[\x00-\x7f\u2010-\u2027]*$/.test(plain)) utterance.lang = 'en-US';
  speechSynthesis.cancel();
  speechSynthesis.speak(utterance);
}

export function stopSpeaking() {
  if (canSpeak) speechSynthesis.cancel();
}
