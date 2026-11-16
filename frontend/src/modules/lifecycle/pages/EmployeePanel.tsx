import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCheck, FilePlus2, RotateCcw, X } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button } from "@/core/ui/button";
import { ErrorState, LoadingRows, Mono } from "@/core/ui/feedback";
import { Field, Textarea } from "@/core/ui/field";
import { StatusBadge } from "@/core/ui/status";
import { SystemRail } from "@/core/ui/system-rail";
import { formatDate, formatDateTime } from "@/core/lib/format";
import type { Issue, ReconRow, RtrRequest } from "../api";
import { DOC_STATUS, KIND_SHORT_TITLE, SHORT_OF, type Doc } from "../docs";
import { CELL_STATUS, KIND_LABEL, SEVERITY, SYSTEM_LABEL } from "../labels";

interface Detail {
  row: ReconRow;
  requests: RtrRequest[];
  sdp_users: { id: string; name: string; email: string | null; employee_id: string | null; department: string | null }[];
  assets: { id: string; name: string; asset_tag: string | null; serial: string | null; product: string | null; product_type: string | null; state: string | null }[];
  biostar: {
    user_id: string; name: string; group: string | null; access_groups: string[]; active: boolean; disabled: boolean;
    expired: boolean; expiry: string | null; cards: number; fingerprints: number; faces: number;
  } | null;
  history: { at: string; issue_count: number; max_severity: string | null }[];
  acks: { rule: string; note: string; acked_by: string; acked_at: string }[];
}

function IssueItem({ employeeId, issue, ackNote }: { employeeId: string; issue: Issue; ackNote?: { note: string; acked_by: string; acked_at: string } }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const [open, setOpen] = useState(false);
  const refresh = () => qc.invalidateQueries({ queryKey: ["lifecycle", "recon"] });
  const ack = useMutation({
    mutationFn: () => api(`/api/lifecycle/reconciliation/${employeeId}/ack`, { method: "POST", json: { rule: issue.rule, note } }),
    onSuccess: async () => {
      setOpen(false);
      await refresh();
      toast.success("Đã đánh dấu là lệch đã biết.");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const reopen = useMutation({
    mutationFn: () => api(`/api/lifecycle/reconciliation/${employeeId}/ack/${issue.rule}`, { method: "DELETE" }),
    onSuccess: async () => {
      await refresh();
      toast.success("Đã mở lại lệch.");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <li className="rounded-sm border border-line p-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={issue.acked ? "skipped" : SEVERITY[issue.severity].status} label={issue.acked ? "Đã biết" : SEVERITY[issue.severity].label} />
        <span className="text-sm text-muted">{SYSTEM_LABEL[issue.system]}</span>
      </div>
      <p className="mt-1.5">{issue.message}</p>
      {issue.acked && ackNote && (
        <p className="mt-1 text-sm text-muted">
          {ackNote.acked_by} · {formatDateTime(ackNote.acked_at)}
          {ackNote.note && <> · “{ackNote.note}”</>}
        </p>
      )}
      {can("lifecycle.reconciliation.sync") && (
        <div className="mt-2">
          {issue.acked ? (
            <Button size="sm" variant="ghost" onClick={() => reopen.mutate()} loading={reopen.isPending}>
              <RotateCcw className="size-3.5" aria-hidden="true" />
              Mở lại
            </Button>
          ) : open ? (
            <form
              className="space-y-2"
              onSubmit={(e) => {
                e.preventDefault();
                ack.mutate();
              }}
            >
              <Field label="Ghi chú (không bắt buộc)" hint="Ví dụ: đã báo bảo vệ, chờ xoá thẻ.">
                <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} />
              </Field>
              <div className="flex gap-2">
                <Button size="sm" type="submit" variant="primary" loading={ack.isPending}>Đánh dấu đã biết</Button>
                <Button size="sm" onClick={() => setOpen(false)}>Huỷ</Button>
              </div>
            </form>
          ) : (
            <Button size="sm" onClick={() => setOpen(true)}>
              <CheckCheck className="size-3.5" aria-hidden="true" />
              Đánh dấu đã biết
            </Button>
          )}
        </div>
      )}
    </li>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-t border-line px-4 py-3">
      <h3 className="mb-2 text-base font-semibold">{title}</h3>
      {children}
    </section>
  );
}

function DocumentsSection({ employeeId }: { employeeId: string }) {
  const { can } = useAuth();
  const docs = useQuery({
    queryKey: ["documents", "employee", employeeId],
    queryFn: () => api<{ items: Doc[] }>(`/api/core/documents?subject_key=${encodeURIComponent(employeeId)}`),
    enabled: can("lifecycle.documents.view"),
  });
  if (!can("lifecycle.documents.view")) return null;
  return (
    <Section title="Biên bản">
      {docs.data && docs.data.items.length > 0 ? (
        <ul className="mb-2 space-y-1 text-sm">
          {docs.data.items.map((d) => (
            <li key={d.id} className="flex flex-wrap items-center gap-2">
              <Link to={`/lifecycle/documents/${d.id}`} className="font-medium hover:text-primary hover:underline">
                {KIND_SHORT_TITLE[SHORT_OF[d.kind] ?? "handover"]} {d.number ?? "(nháp)"}
              </Link>
              <StatusBadge status={DOC_STATUS[d.status].status} label={DOC_STATUS[d.status].label} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="mb-2 text-sm text-muted">Chưa có biên bản nào.</p>
      )}
      {can("lifecycle.documents.create") && (
        <div className="flex flex-wrap gap-2">
          <Link to={`/lifecycle/documents/new?kind=handover&emp=${encodeURIComponent(employeeId)}`} className="inline-flex h-7 items-center gap-1.5 rounded-sm border border-line-strong px-2.5 text-sm hover:bg-surface-2">
            <FilePlus2 className="size-3.5" aria-hidden="true" />
            Lập biên bản bàn giao
          </Link>
          <Link to={`/lifecycle/documents/new?kind=return&emp=${encodeURIComponent(employeeId)}`} className="inline-flex h-7 items-center gap-1.5 rounded-sm border border-line-strong px-2.5 text-sm hover:bg-surface-2">
            <FilePlus2 className="size-3.5" aria-hidden="true" />
            Lập biên bản thu hồi
          </Link>
        </div>
      )}
    </Section>
  );
}

export function EmployeePanel({ employeeId, onClose }: { employeeId: string; onClose: () => void }) {
  const q = useQuery({
    queryKey: ["lifecycle", "recon", "detail", employeeId],
    queryFn: () => api<Detail>(`/api/lifecycle/reconciliation/${encodeURIComponent(employeeId)}`),
  });
  const d = q.data;
  const flagged = new Set(d?.row.issues.filter((i) => !i.acked).map((i) => i.system));
  return (
    <aside
      aria-label={`Chi tiết nhân viên ${employeeId}`}
      className="sticky top-[calc(var(--topbar-h)+1.25rem)] max-h-[calc(100dvh-110px)] overflow-y-auto rounded-md border border-line bg-surface"
    >
      <header className="flex items-start justify-between gap-2 px-4 py-3">
        <div className="min-w-0">
          <h2 className="truncate text-lg font-semibold">{d?.row.full_name || "Đang tải…"}</h2>
          <p className="text-sm text-muted">
            Mã NV <Mono>{employeeId}</Mono>
            {d?.row.department && ` · ${d.row.department}`}
          </p>
        </div>
        <Button variant="ghost" size="icon" aria-label="Đóng chi tiết" onClick={onClose}>
          <X className="size-4" aria-hidden="true" />
        </Button>
      </header>
      {q.isLoading && <LoadingRows rows={6} />}
      {q.isError && <div className="p-4"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>}
      {d && (
        <>
          <div className="px-4 pb-3">
            <SystemRail
              cells={(["sdp", "assets", "biostar"] as const).map((k) => ({
                key: k,
                label: SYSTEM_LABEL[k]!,
                status: flagged.has(k) ? "mismatch" : CELL_STATUS[d.row.cells[k].status],
                value: k === "assets" ? d.row.cells.assets.label : undefined,
                detail: d.row.cells[k].detail || d.row.cells[k].label,
              }))}
            />
          </div>
          <Section title={`Lệch phát hiện (${d.row.issues.filter((i) => !i.acked).length})`}>
            {d.row.issues.length === 0 ? (
              <p className="text-sm text-muted">Không phát hiện lệch.</p>
            ) : (
              <ul className="space-y-2">
                {d.row.issues.map((i) => (
                  <IssueItem key={i.rule} employeeId={employeeId} issue={i} ackNote={d.acks.find((a) => a.rule === i.rule)} />
                ))}
              </ul>
            )}
          </Section>
          <DocumentsSection employeeId={employeeId} />
          <Section title="Request RTR">
            {d.requests.length === 0 ? (
              <p className="text-sm text-muted">Không có request RTR trong khoảng thời gian đồng bộ.</p>
            ) : (
              <ul className="space-y-1.5 text-sm">
                {d.requests.map((r) => (
                  <li key={r.id}>
                    <Link to={`/lifecycle/requests?open=${r.id}`} className="font-medium hover:text-primary hover:underline">
                      #{r.display_id ?? r.id}
                    </Link>{" "}
                    · {r.kind ? KIND_LABEL[r.kind] : r.change_type_raw ?? "Chưa rõ loại"} · hiệu lực {formatDate(r.effective_date)}
                  </li>
                ))}
              </ul>
            )}
          </Section>
          <Section title={`Tài sản đang gán (${d.assets.length})`}>
            {d.assets.length === 0 ? (
              <p className="text-sm text-muted">Không có tài sản nào gán cho nhân viên này.</p>
            ) : (
              <table className="w-full text-sm">
                <caption className="sr-only">Tài sản đang gán</caption>
                <thead className="text-left text-muted">
                  <tr>
                    <th scope="col" className="py-1 font-medium">Tên tài sản</th>
                    <th scope="col" className="py-1 font-medium">Asset tag</th>
                    <th scope="col" className="py-1 font-medium">Trạng thái</th>
                  </tr>
                </thead>
                <tbody>
                  {d.assets.map((a) => (
                    <tr key={a.id} className="border-t border-line">
                      <td className="py-1">{a.name}<span className="block text-xs text-muted">{a.product_type} · {a.product}</span></td>
                      <td className="py-1"><Mono>{a.asset_tag ?? "—"}</Mono></td>
                      <td className="py-1">{a.state ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {d.sdp_users.length > 0 && (
              <p className="mt-2 text-sm text-muted">User SDP: {d.sdp_users.map((u) => `${u.name}${u.email ? ` (${u.email})` : ""}`).join(", ")}</p>
            )}
          </Section>
          <Section title="BioStar">
            {!d.biostar ? (
              <p className="text-sm text-muted">Không có user BioStar mang Mã NV này.</p>
            ) : (
              <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
                <dt className="text-muted">Trạng thái</dt>
                <dd>
                  <StatusBadge status={d.biostar.active ? "done" : "disabled"} label={d.biostar.active ? "Hoạt động" : d.biostar.disabled ? "Vô hiệu hoá" : "Hết hạn"} />
                </dd>
                <dt className="text-muted">Tên</dt>
                <dd>{d.biostar.name}</dd>
                <dt className="text-muted">Nhóm người dùng</dt>
                <dd>{d.biostar.group ?? "—"}</dd>
                <dt className="text-muted">Nhóm quyền ra vào</dt>
                <dd>{d.biostar.access_groups.join(", ") || "—"}</dd>
                <dt className="text-muted">Hiệu lực tới</dt>
                <dd>{formatDate(d.biostar.expiry)}</dd>
                <dt className="text-muted">Đã đăng ký</dt>
                <dd className="tabular">
                  {d.biostar.cards} thẻ · {d.biostar.fingerprints} vân tay · {d.biostar.faces} khuôn mặt
                </dd>
              </dl>
            )}
          </Section>
          {d.history.length > 1 && (
            <Section title="Lịch sử số lệch">
              <ol className="flex flex-wrap gap-1.5 text-xs">
                {d.history.slice().reverse().map((h) => (
                  <li key={h.at} className="rounded-xs bg-surface-2 px-1.5 py-0.5 tabular" title={formatDateTime(h.at)}>
                    {formatDate(h.at).slice(0, 5)}: {h.issue_count}
                  </li>
                ))}
              </ol>
            </Section>
          )}
        </>
      )}
    </aside>
  );
}
