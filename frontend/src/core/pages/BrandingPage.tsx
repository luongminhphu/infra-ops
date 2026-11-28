import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ImageOff, Upload } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { Button } from "@/core/ui/button";
import { ConfirmDialog } from "@/core/ui/confirm-dialog";
import { ErrorState, LoadingRows, PageHeader, Panel } from "@/core/ui/feedback";
import { HelpTip } from "@/core/ui/help-tip";

interface Branding {
  has_logo: boolean;
  logo_version: string | null;
}

const KEY = ["core", "branding"];

/** Hệ thống › Logo báo cáo: the company logo printed on exported reports and records. */
export function BrandingPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: KEY, queryFn: () => api<Branding>("/api/core/branding") });
  const fileRef = useRef<HTMLInputElement>(null);
  const done = (msg: string) => async () => {
    toast.success(msg);
    await qc.invalidateQueries({ queryKey: KEY });
  };
  const upload = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.set("file", file);
      return api<Branding>("/api/core/branding/logo", { method: "POST", body: fd });
    },
    onSuccess: done("Đã lưu logo."),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const [confirmRemove, setConfirmRemove] = useState(false);
  const remove = useMutation({
    mutationFn: () => api<Branding>("/api/core/branding/logo", { method: "DELETE" }),
    onSuccess: done("Đã gỡ logo – báo cáo in không kèm logo."),
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <>
      <PageHeader title="Logo báo cáo" description="Logo công ty in trên các báo cáo và biên bản app xuất ra." />
      {q.isLoading && <LoadingRows rows={2} />}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && (
        <Panel
          title={
            <span className="inline-flex items-center gap-1.5">
              Logo hiện tại
              <HelpTip title="Logo được in ở đâu">
                <span className="block">File Excel: Báo cáo chấm công, Rà soát nghỉ việc (ISO), Kiểm kê tài sản IT – logo ở đầu sheet đầu tiên.</span>
                <span className="block">Biên bản kiểm kê (DOCX/PDF): ở đầu trang. Mẫu biên bản khác: thêm <code className="font-mono">{"{{ logo }}"}</code> vào mẫu Word rồi tải lên ở Mẫu biên bản.</span>
              </HelpTip>
            </span>
          }
          className="max-w-2xl"
        >
          <div className="grid gap-4">
            <div className="flex min-h-28 items-center justify-center rounded-md border border-dashed border-line-strong bg-surface-2 p-4">
              {q.data.has_logo ? (
                <img src={`/api/core/branding/logo?v=${q.data.logo_version}`} alt="Logo công ty đang dùng" className="max-h-20 max-w-full object-contain" />
              ) : (
                <p className="flex items-center gap-2 text-sm text-muted"><ImageOff className="size-4" aria-hidden="true" />Chưa có logo – báo cáo in không kèm logo.</p>
              )}
            </div>
            <p className="text-sm text-muted">PNG hoặc JPG, tối đa 1 MB. Nên dùng logo ngang, nền trắng hoặc trong suốt; app in cao khoảng 1,4 cm.</p>
            <div className="flex flex-wrap gap-2">
              <input
                ref={fileRef}
                type="file"
                accept="image/png,image/jpeg"
                className="sr-only"
                tabIndex={-1}
                aria-hidden="true"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  e.target.value = "";
                  if (f) upload.mutate(f);
                }}
              />
              <Button variant="primary" loading={upload.isPending} onClick={() => fileRef.current?.click()}>
                {!upload.isPending && <Upload className="size-4" aria-hidden="true" />}
                {q.data.has_logo ? "Đổi logo" : "Tải logo lên"}
              </Button>
              {q.data.has_logo && <Button variant="ghost" loading={remove.isPending} onClick={() => setConfirmRemove(true)}>Gỡ logo</Button>}
            </div>
          </div>
        </Panel>
      )}
      <ConfirmDialog
        open={confirmRemove}
        onClose={() => setConfirmRemove(false)}
        onConfirm={() => {
          setConfirmRemove(false);
          remove.mutate();
        }}
        title="Gỡ logo báo cáo"
        confirmLabel="Gỡ logo"
      >
        Báo cáo và biên bản in sau đó sẽ không kèm logo.
      </ConfirmDialog>
    </>
  );
}
