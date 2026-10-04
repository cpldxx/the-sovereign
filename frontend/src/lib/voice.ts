/** Browser speech: recognition (Chrome/Edge/Safari) and synthesis — no backend needed. */

interface Recognition {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void) | null;
  onend: (() => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  start(): void;
  stop(): void;
}

type RecognitionCtor = new () => Recognition;

const Ctor: RecognitionCtor | undefined =
  (window as unknown as { SpeechRecognition?: RecognitionCtor }).SpeechRecognition ??
  (window as unknown as { webkitSpeechRecognition?: RecognitionCtor }).webkitSpeechRecognition;

export const canListen = Boolean(Ctor);
export const canSpeak = 'speechSynthesis' in window;

/** Listen once. `onText` gets the running transcript; `onDone` the final one ('' if nothing was heard). */
export function listen(onText: (text: string) => void, onDone: (text: string) => void): () => void {
  if (!Ctor) {
    onDone('');
    return () => {};
  }
  const rec = new Ctor();
  rec.lang = navigator.language || 'en-US';
  rec.interimResults = true;
  rec.continuous = false;
  let text = '';
  rec.onresult = e => {
    text = Array.from(e.results).map(r => r[0].transcript).join('');
    onText(text);
  };
  rec.onerror = () => {};
  rec.onend = () => onDone(text.trim());
  rec.start();
  return () => rec.stop();
}

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
  speechSynthesis.cancel();
  speechSynthesis.speak(new SpeechSynthesisUtterance(plain));
}

export function stopSpeaking() {
  if (canSpeak) speechSynthesis.cancel();
}
