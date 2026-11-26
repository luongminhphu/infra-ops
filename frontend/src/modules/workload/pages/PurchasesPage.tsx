import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { Download, Plus } from "lucide-react";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button } from "@/core/ui/button";
import { PageHeader } from "@/core/ui/feedback";
import { Tabs } from "@/core/ui/tabs";
import { CatalogTab } from "../purchases/CatalogTab";
import { PoTab } from "../purchases/PoTab";
import { ProposalsTab } from "../purchases/ProposalsTab";
import { SuppliersTab } from "../purchases/SuppliersTab";

const ALL_TABS = [
  { key: "proposals", label: "Đề xuất" },
  { key: "pos", label: "PO tổng" },
  { key: "catalog", label: "Bảng giá" },
  { key: "suppliers", label: "Nhà cung cấp" },
];

export function PurchasesPage() {
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  // Prices, POs and the price list are for supervisors; operators only request devices.
  const TABS = can("workload.price.view") ? ALL_TABS : ALL_TABS.slice(0, 1);
  const tab = TABS.some((t) => t.key === params.get("tab")) ? params.get("tab")! : "proposals";
  const update = (patch: Record<string, string | null>) =>
    setParams(
      (cur) => {
        const next = new URLSearchParams(cur);
        for (const [k, v] of Object.entries(patch)) v === null ? next.delete(k) : next.set(k, v);
        return next;
      },
      { replace: true },
    );
  const navigate = useNavigate();
  // Old links (?new=1&…) open the step-by-step proposal with the same pre-filled values.
  if (params.get("new") === "1") {
    const rest = new URLSearchParams(params);
    rest.delete("new");
    return <Navigate replace to={`/workload/purchases/new?${rest}`} />;
  }

  return (
    <>
      <PageHeader
        title="Đề xuất mua thiết bị"
        description={can("workload.purchase.manage") ? "Đề xuất thiết bị cần cấp, duyệt, gom PO theo hạn mức và trình duyệt PO." : "Đề xuất thiết bị cần cấp và theo dõi đề xuất của bạn tới lúc nhận hàng."}
        actions={
          <>
            <a className="inline-flex h-8 items-center gap-1.5 rounded-sm border border-line-strong bg-surface px-3 font-medium hover:bg-surface-2" href="/api/workload/purchases/export">
              <Download className="size-4" aria-hidden="true" />Xuất Excel
            </a>
            {can("workload.purchase.create") && (
              <Button variant="primary" onClick={() => navigate("/workload/purchases/new")}><Plus className="size-4" aria-hidden="true" />Đề xuất mới</Button>
            )}
          </>
        }
      />
      <Tabs label="Mua thiết bị" tabs={TABS} value={tab} onChange={(k) => update({ tab: k === "proposals" ? null : k })}>
        {tab === "proposals" && <ProposalsTab />}
        {tab === "pos" && <PoTab />}
        {tab === "catalog" && <CatalogTab />}
        {tab === "suppliers" && <SuppliersTab />}
      </Tabs>
    </>
  );
}
