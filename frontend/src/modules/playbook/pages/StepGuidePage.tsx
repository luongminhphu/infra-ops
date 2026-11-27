import { useEffect } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { BookOpen } from "lucide-react";
import { api, ApiError } from "@/core/api/client";
import { EmptyState, ErrorState, LoadingRows } from "@/core/ui/feedback";

/** A Job step's "Hướng dẫn trong Playbook" link: opens the published guide at the step that explains it. */
export function StepGuidePage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const workflow = params.get("workflow") ?? "";
  const step = params.get("step") ?? "";
  const q = useQuery({
    queryKey: ["playbook", "step-guide", workflow, step],
    queryFn: () => api<{ guide_id: string; node_id: string }>(`/api/playbook/step-guide?${new URLSearchParams({ workflow, step })}`),
    retry: false,
  });
  useEffect(() => {
    if (q.data) navigate(`/playbook/${q.data.guide_id}?step=${encodeURIComponent(q.data.node_id)}`, { replace: true });
  }, [q.data, navigate]);
  if (q.isError && q.error instanceof ApiError && q.error.status === 404)
    return (
      <EmptyState icon={BookOpen} title="Chưa có Playbook hướng dẫn bước này">
        Manager hoặc Supervisor thêm “Bước của Job” vào một Playbook rồi xuất bản. <Link to="/playbook" className="text-primary underline">Mở thư viện Playbook</Link>
      </EmptyState>
    );
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  return <LoadingRows rows={4} />;
}
