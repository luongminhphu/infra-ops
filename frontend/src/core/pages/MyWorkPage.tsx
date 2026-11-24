import { useAuth } from "@/core/auth/AuthProvider";
import { EmptyState, PageHeader } from "@/core/ui/feedback";
import { modules } from "@/modules";
import { UserCheck } from "lucide-react";

/** Operator portal: each module contributes a section about the signed-in user's own work. */
export function MyWorkPage() {
  const { can } = useAuth();
  const sections = modules
    .flatMap((m) => m.portal ?? [])
    .filter((s) => can(s.permission))
    .sort((a, b) => a.order - b.order);
  return (
    <>
      <PageHeader
        title="Việc của tôi"
        description="Việc đang giao cho bạn: cần làm ngay, đang làm, sắp tới và đã xong."
      />
      {sections.length ? (
        <div className="space-y-6">
          {sections.map(({ key, component: Section }) => <Section key={key} />)}
        </div>
      ) : (
        <EmptyState icon={UserCheck} title="Chưa có việc nào được giao cho bạn">
          Khi Manager giao Job onboard/offboard hoặc task Helpdesk, chúng sẽ hiện ở đây.
        </EmptyState>
      )}
    </>
  );
}
