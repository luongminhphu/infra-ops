import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { BookOpen, ChevronRight, Plus } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { Button } from "@/core/ui/button";
import { Dialog } from "@/core/ui/dialog";
import { EmptyState, ErrorState, LoadingRows, PageHeader } from "@/core/ui/feedback";
import { Field, Input } from "@/core/ui/field";
import { StatusBadge } from "@/core/ui/status";
import { useDebounced } from "@/core/lib/useDebounced";
import type { GuideSummary } from "../api";

export function GuidesPage() {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const debounced = useDebounced(q);
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [category, setCategory] = useState("");
  const [tried, setTried] = useState(false);
  const list = useQuery({
    queryKey: ["handbook", "guides", debounced],
    queryFn: () => api<{ items: GuideSummary[]; can_edit: boolean; can_publish: boolean }>(`/api/handbook/guides?q=${encodeURIComponent(debounced)}`),
  });
  const create = useMutation({
    mutationFn: () => api<GuideSummary>("/api/handbook/guides", { method: "POST", json: { title, category } }),
    onSuccess: (g) => navigate(`/handbook/${g.id}/edit`),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const items = list.data?.items ?? [];
  const groups = [...new Set(items.map((g) => g.category || "Chung"))];
  return (
    <>
      <PageHeader
        title="Infra Playbook"
        description="Quy định và hướng dẫn xử lý cho IT Support: mỗi hướng dẫn là một sơ đồ rẽ nhánh, bấm vào từng bước để đọc ghi chú."
        actions={
          list.data?.can_edit && (
            <Button variant="primary" onClick={() => { setTitle(""); setCategory(""); setTried(false); setOpen(true); }}>
              <Plus className="size-4" aria-hidden="true" />Tạo hướng dẫn
            </Button>
          )
        }
      />
      <form role="search" className="mb-4 max-w-md" onSubmit={(e) => e.preventDefault()}>
        <Field label="Tìm hướng dẫn">
          <Input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Tên, nhóm hoặc mô tả" />
        </Field>
      </form>
      {list.isError && <ErrorState error={list.error} onRetry={() => list.refetch()} />}
      {list.isLoading ? (
        <LoadingRows rows={4} />
      ) : items.length === 0 ? (
        <EmptyState icon={BookOpen} title={q ? "Không có hướng dẫn phù hợp" : "Chưa có hướng dẫn nào"}>
          {list.data?.can_edit ? "Bấm “Tạo hướng dẫn” để soạn hướng dẫn đầu tiên." : "Manager sẽ xuất bản hướng dẫn tại đây."}
        </EmptyState>
      ) : (
        <div className="space-y-6">
          {groups.map((cat) => (
            <section key={cat} aria-label={cat}>
              <h2 className="mb-2 text-base font-semibold">{cat}</h2>
              <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                {items.filter((g) => (g.category || "Chung") === cat).map((g) => (
                  <li key={g.id}>
                    <Link to={g.published_version ? `/handbook/${g.id}` : `/handbook/${g.id}/edit`} className="flex h-full items-start gap-3 rounded-md border border-line bg-surface p-3 shadow-card hover:border-primary">
                      <BookOpen className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden="true" />
                      <span className="min-w-0 flex-1">
                        <span className="block font-medium">{g.title}</span>
                        {g.description && <span className="mt-0.5 block text-sm text-muted line-clamp-2">{g.description}</span>}
                        <span className="mt-1.5 block">
                          {g.published_version ? <StatusBadge status="done" label={`Phiên bản ${g.published_version}`} /> : <StatusBadge status="pending" label="Bản nháp – chưa xuất bản" />}
                        </span>
                      </span>
                      <ChevronRight className="mt-0.5 size-4 shrink-0 text-muted" aria-hidden="true" />
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="Tạo hướng dẫn"
        description="Hướng dẫn bắt đầu với một bước gốc; thêm các nhánh trong trang soạn. Chỉ người soạn thấy bản nháp cho đến khi Manager xuất bản."
        footer={
          <>
            <Button onClick={() => setOpen(false)}>Huỷ</Button>
            <Button variant="primary" loading={create.isPending} onClick={() => { setTried(true); if (title.trim()) create.mutate(); }}>Tạo và soạn</Button>
          </>
        }
      >
        <div className="grid gap-3">
          <Field label="Tên hướng dẫn" required error={title.trim() ? "" : "Nhập tên hướng dẫn."} showError={tried}>
            <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Ví dụ: Xử lý máy trạm mất mạng" maxLength={200} />
          </Field>
          <Field label="Nhóm" hint="Dùng để gom hướng dẫn trên danh sách, ví dụ Mạng, Tài khoản, Thiết bị.">
            <Input value={category} onChange={(e) => setCategory(e.target.value)} maxLength={100} list="handbook-categories" />
          </Field>
          <datalist id="handbook-categories">{groups.map((c) => <option key={c} value={c} />)}</datalist>
        </div>
      </Dialog>
    </>
  );
}
