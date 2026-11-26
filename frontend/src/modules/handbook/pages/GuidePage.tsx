import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, CornerDownRight, Pencil } from "lucide-react";
import { api } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { buttonVariants } from "@/core/ui/button";
import { ErrorState, LoadingRows, PageHeader, Panel } from "@/core/ui/feedback";
import { Field, Select } from "@/core/ui/field";
import { formatDate } from "@/core/lib/format";
import { getGuide, type GuideVersionInfo } from "../api";
import { Mindmap } from "../Mindmap";
import { Note } from "../Note";

export function GuidePage() {
  const { id = "" } = useParams();
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  const version = params.get("v") ?? "published";
  const q = useQuery({ queryKey: ["handbook", "guide", id, version], queryFn: () => getGuide(id, version) });
  const history = useQuery({ queryKey: ["handbook", "versions", id], queryFn: () => api<GuideVersionInfo[]>(`/api/handbook/guides/${id}/versions`) });
  const [selected, setSelected] = useState<string | null>(null);
  const g = q.data;
  useEffect(() => {
    if (g) setSelected((s) => (s && g.tree.nodes[s] ? s : g.tree.root));
  }, [g]);

  const back = (
    <Link to="/handbook" className="mb-2 inline-flex items-center gap-1 text-sm text-muted hover:text-ink">
      <ChevronLeft className="size-4" aria-hidden="true" />Infra Handbook
    </Link>
  );
  if (q.isLoading) return <>{back}<LoadingRows rows={8} /></>;
  if (q.isError || !g) return <>{back}<ErrorState error={q.error} onRetry={() => q.refetch()} /></>;
  const node = selected ? g.tree.nodes[selected] : undefined;
  return (
    <>
      <nav aria-label="Đường dẫn">{back}</nav>
      <PageHeader
        title={g.title}
        description={
          <>
            {g.description && <span className="block">{g.description}</span>}
            <span className="block">Phiên bản {g.version} · xuất bản {formatDate(g.published_at)}{g.published_by ? ` bởi ${g.published_by}` : ""}. Bấm vào từng bước trên sơ đồ để đọc hướng dẫn.</span>
          </>
        }
        actions={can("handbook.guides.edit") && <Link to={`/handbook/${id}/edit`} className={buttonVariants({})}><Pencil className="size-4" aria-hidden="true" />Soạn</Link>}
      />
      {(history.data?.length ?? 0) > 1 && (
        <Field label="Phiên bản" className="mb-3 w-64">
          <Select value={version === "published" ? String(g.version) : version} onChange={(e) => setParams(e.target.value === String(g.published_version) ? {} : { v: e.target.value }, { replace: true })}>
            {history.data!.map((v) => <option key={v.version} value={v.version}>{`Phiên bản ${v.version} – ${formatDate(v.published_at)}${v.version === g.published_version ? " (đang dùng)" : ""}`}</option>)}
          </Select>
        </Field>
      )}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
        <Mindmap tree={g.tree} selected={selected} onSelect={setSelected} label={`Sơ đồ ${g.title}`} />
        <Panel title={node?.title ?? "Chọn một bước"} className="xl:sticky xl:top-4 xl:self-start">
          {node && (
            <>
              <Note text={node.note} />
              {node.children.length > 0 && (
                <div className="mt-4 border-t border-line pt-3">
                  <p className="mb-1.5 text-sm font-medium text-muted">Bước tiếp theo</p>
                  <ul className="space-y-1">
                    {node.children.map((b, i) => (
                      <li key={i}>
                        <button type="button" onClick={() => setSelected(b.to)} className="inline-flex min-h-8 items-center gap-1.5 text-left text-primary hover:underline">
                          <CornerDownRight className="size-4 shrink-0" aria-hidden="true" />
                          {b.label ? <><b className="font-medium">{b.label}</b> → </> : null}{g.tree.nodes[b.to]?.title}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </Panel>
      </div>
    </>
  );
}
