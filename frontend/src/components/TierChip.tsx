/** What kind of source a text came from (kg/core/sources.py), as a quiet label. It also capped how far each fact it
 * states was trusted alone: papers and official sources most, blogs and forums least. Plain websites and what you
 * added yourself go unlabeled — they are most sources. */
const LABEL: Record<string, string> = {
  paper: 'paper', official: 'official', reference: 'reference', press: 'press', blog: 'blog',
};
const MEANING: Record<string, string> = {
  paper: 'A peer-reviewed paper or preprint — trusted most',
  official: 'An official source: a government, regulator, standards body or university',
  reference: 'A reference work',
  press: 'An established news organization',
  blog: 'A blog, forum or social media — trusted least on its own',
};

export function TierChip({ tier }: { tier?: string | null }) {
  if (!tier || !LABEL[tier]) return null;
  const strong = tier === 'paper' || tier === 'official';
  return (
    <span title={MEANING[tier]}
      className={`shrink-0 rounded border px-1 font-mono text-[9px] leading-[14px] tracking-wider uppercase ${strong ? 'border-accent/50 text-accent' : 'border-line-2 text-faint'}`}>
      {LABEL[tier]}
    </span>
  );
}
