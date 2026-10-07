/** The browser's own speech synthesis — the fallback when Hermes' local voice is unavailable. */

export const canSpeak = 'speechSynthesis' in window;

/** Read text aloud, minus markdown punctuation and long uids. */
export function speak(text: string) {
  if (!canSpeak) return;
  const plain = text
    .replace(/```[\s\S]*?```/g, '')
    .replace(/`[^`]*`/g, '')
    .replace(/\|/g, ' ')
    .replace(/[#*_>~-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  const utterance = new SpeechSynthesisUtterance(plain);
  // The default voice follows the browser language; pick one that matches the text's script.
  if (/[\uac00-\ud7a3]/.test(plain)) utterance.lang = 'ko-KR';
  else if (/^[\x00-\x7f\u2010-\u2027]*$/.test(plain)) utterance.lang = 'en-US';
  speechSynthesis.cancel();
  speechSynthesis.speak(utterance);
}

export function stopSpeaking() {
  if (canSpeak) speechSynthesis.cancel();
}
