import { useSearchParams } from "react-router-dom";
import { Download, Plus } from "lucide-react";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button } from "@/core/ui/button";
import { PageHeader } from "@/core/ui/feedback";
import { Tabs } from "@/core/ui/tabs";
import type { DeviceNeed } from "../api";
import { PurchaseDialog, type PurchasePrefill } from "../PurchaseDialog";
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

/** Prefill from a link (e.g. Kho & cấp phát → “Lập đề xuất mua”): ?new=1&source=onboard&ref=…&item=… */
function prefillFrom(params: URLSearchParams): PurchasePrefill | null {
  if (params.get("new") !== "1") return null;
  const qty = Number(params.get("qty"));
  return {
    kind: (params.get("kind") as DeviceNeed | "new" | null) ?? "new",
    item: params.get("item") ?? "",
    catalogName: params.get("catalog") ?? "",
    quantity: qty > 0 ? qty : 1,
    source: params.get("source") === "onboard" ? "onboard" : "manual",
    sourceRef: params.get("ref") ?? undefined,
    employee: params.get("employee") ?? "",
    reason: params.get("reason") ?? "",
  };
}

export function PurchasesPage() {
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  // Prices, POs and the price list are for supervisors; operators only request devices.
  const TABS = can("workload.price.view") ? ALL_TABS : ALL_TABS.slice(0, 1);
  const tab = TABS.some((t) => t.key === params.get("tab")) ? params.get("tab")! : "proposals";
  const prefill = prefillFrom(params);
  const update = (patch: Record<string, string | null>) =>
    setParams(
      (cur) => {
        const next = new URLSearchParams(cur);
        for (const [k, v] of Object.entries(patch)) v === null ? next.delete(k) : next.set(k, v);
        return next;
      },
      { replace: true },
    );
  const closeNew = () => update({ new: null, source: null, ref: null, item: null, catalog: null, qty: null, kind: null, employee: null, reason: null });

  return (
    <>
      <PageHeader
        title="Đề xuất mua thiết bị"
        description="Operator đề xuất thiết bị cần cấp kèm bằng chứng Function Head duyệt → Supervisor định giá (báo giá đối tác / bảng giá) và duyệt → hệ thống gom PO tổng theo hạn mức → Supervisor chốt PO và gửi email cho Phòng Mua hàng."
        actions={
          <>
            <a className="inline-flex h-8 items-center gap-1.5 rounded-sm border border-line-strong bg-surface px-3 font-medium hover:bg-surface-2" href="/api/workload/purchases/export">
              <Download className="size-4" aria-hidden="true" />Xuất Excel
            </a>
            {can("workload.purchase.create") && (
              <Button variant="primary" onClick={() => update({ new: "1" })}><Plus className="size-4" aria-hidden="true" />Đề xuất mới</Button>
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
      {prefill && can("workload.purchase.create") && <PurchaseDialog prefill={prefill} onClose={closeNew} />}
    </>
  );
}
