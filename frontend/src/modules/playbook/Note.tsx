import type { ReactNode } from "react";
import { Link } from "react-router-dom";

/** Only links that stay in infra-ops, go to http(s) or mailto; images only from the playbook store or https. */
export function safeHref(url: string): string | null {
  const u = url.trim();
  if (/^\/(?!\/)/.test(u) || /^https?:\/\//i.test(u) || /^mailto:/i.test(u)) return u;
  return null;
}
export function safeImage(url: string): string | null {
  const u = url.trim();
  return /^\/api\/playbook\/images\/[0-9a-f-]{36}$/.test(u) || /^https:\/\//i.test(u) ? u : null;
}

const INLINE = /!\[([^\]]*)\]\(([^)\s]+)\)|\[([^\]]+)\]\(([^)\s]+)\)|\*\*([^*]+)\*\*|`([^`]+)`/g;

/** **bold**, `code`, [text](url), ![alt](url) – text only otherwise (never raw HTML). */
export function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  INLINE.lastIndex = 0;
  while ((m = INLINE.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const key = `${keyBase}-${i++}`;
    if (m[2] !== undefined) {
      const src = safeImage(m[2]);
      out.push(src ? <img key={key} src={src} alt={m[1] || "Ảnh minh hoạ"} loading="lazy" className="my-2 block max-h-96 max-w-full rounded-sm border border-line" /> : m[0]);
    } else if (m[4] !== undefined) {
      const href = safeHref(m[4]);
      if (!href) out.push(m[3]);
      else if (href.startsWith("/")) out.push(<Link key={key} to={href} className="text-primary underline">{m[3]}</Link>);
      else out.push(<a key={key} href={href} target="_blank" rel="noreferrer noopener" className="text-primary underline">{m[3]}</a>);
    } else if (m[5] !== undefined) out.push(<strong key={key} className="font-semibold">{m[5]}</strong>);
    else out.push(<code key={key} className="rounded-xs bg-surface-2 px-1 font-mono text-sm">{m[6]}</code>);
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** A step's note: paragraphs, "- " bullet and "1. " numbered lists, inline formatting. */
export function Note({ text }: { text: string }) {
  const blocks = text.replace(/\r/g, "").split(/\n{2,}/).filter((b) => b.trim());
  if (!blocks.length) return <p className="text-sm text-muted">Bước này chưa có ghi chú.</p>;
  return (
    <div className="space-y-2 text-base leading-relaxed">
      {blocks.map((block, bi) => {
        const lines = block.split("\n");
        if (lines.every((l) => /^\s*[-*]\s+/.test(l)))
          return <ul key={bi} className="list-disc space-y-0.5 pl-5">{lines.map((l, li) => <li key={li}>{inline(l.replace(/^\s*[-*]\s+/, ""), `${bi}-${li}`)}</li>)}</ul>;
        if (lines.every((l) => /^\s*\d+[.)]\s+/.test(l)))
          return <ol key={bi} className="list-decimal space-y-0.5 pl-5">{lines.map((l, li) => <li key={li}>{inline(l.replace(/^\s*\d+[.)]\s+/, ""), `${bi}-${li}`)}</li>)}</ol>;
        return <p key={bi}>{lines.flatMap((l, li) => [...(li ? [<br key={`br${li}`} />] : []), ...inline(l, `${bi}-${li}`)])}</p>;
      })}
    </div>
  );
}
