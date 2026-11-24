/** Accent- and case-insensitive word matching for Vietnamese device names. */
export const fold = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/đ/gi, "d").toLowerCase();

export const words = (s: string) => new Set(fold(s).split(/[^a-z0-9]+/).filter((w) => w.length > 1));

export const overlap = (a: string, b: string) => {
  const have = words(b);
  return [...words(a)].filter((w) => have.has(w)).length;
};

/** The one candidate sharing the most words (at least `min`) with the text; none on a tie. */
export function bestMatch<T>(items: T[], text: string, name: (t: T) => string, min = 2): T | undefined {
  const scored = items.map((t) => [t, overlap(name(t), text)] as const).sort((x, y) => y[1] - x[1]);
  const [top, second] = scored;
  return top && top[1] >= min && (!second || second[1] < top[1]) ? top[0] : undefined;
}
