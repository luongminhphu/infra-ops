import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import { useAuth } from "@/core/auth/AuthProvider";
import { PageHeader } from "@/core/ui/feedback";
import { iconFor } from "@/core/ui/icons";
import { railSections } from "@/core/layout/nav";

/** Every page of the app you may open, grouped by module – the same tools Playbook steps open. */
export function ToolsPage() {
  const { me } = useAuth();
  const tools = railSections(me?.nav ?? []).find((s) => s.id === "tools")?.items.slice(1) ?? [];
  const groups = [...new Set(tools.map((t) => t.group ?? ""))];
  return (
    <>
      <PageHeader title="Công cụ" description="Các trang làm việc của từng phân hệ. Thường bạn mở chúng từ bước Playbook; ở đây là danh sách đầy đủ." />
      <div className="grid gap-x-10 gap-y-8 md:grid-cols-2 xl:grid-cols-3">
        {groups.map((g) => (
          <section key={g || "chung"} aria-label={g || "Chung"}>
            <h2 className="mb-1 text-sm font-medium text-muted">{g || "Chung"}</h2>
            <ul className="divide-y divide-line border-y border-line">
              {tools.filter((t) => (t.group ?? "") === g).map((t) => {
                const Icon = iconFor(t.icon);
                return (
                  <li key={t.path}>
                    <Link to={t.path} className="flex min-h-11 items-center gap-3 px-1 py-2 hover:text-primary">
                      <Icon className="size-4 shrink-0 text-muted" aria-hidden="true" />
                      <span className="flex-1">{t.label}</span>
                      <ChevronRight className="size-4 text-muted" aria-hidden="true" />
                    </Link>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </div>
    </>
  );
}
