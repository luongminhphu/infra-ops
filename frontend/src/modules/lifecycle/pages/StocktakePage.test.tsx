import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const perms = vi.hoisted(() => new Set<string>());
const stocktake = vi.hoisted(() => ({ current: {} as Record<string, unknown> }));
const reconciliation = vi.hoisted(() => ({ current: {} as Record<string, unknown> }));
const previewReply = vi.hoisted(() => ({ current: null as Record<string, unknown> | null }));
const calls = vi.hoisted(() => [] as { path: string; init?: { method?: string; json?: unknown; body?: unknown } }[]);

vi.mock("@/core/auth/AuthProvider", () => ({ useAuth: () => ({ can: (p: string) => perms.has(p), me: { id: "u1" } }) }));
vi.mock("@/core/api/client", () => ({
  errorMessage: (e: unknown) => String(e),
  api: vi.fn(async (path: string, init?: { method?: string; json?: unknown; body?: unknown }) => {
    calls.push({ path, init });
    if (path.endsWith("/stocktake-meta"))
      return {
        resolutions: { update_sdp: "Sửa SDP (làm tay)", add_to_books: "Đề nghị KT ghi tăng", repair: "Sửa chữa", dispose: "Đề nghị thanh lý / ghi giảm", investigate: "Tìm thêm (ghi rõ)", no_action: "Không cần xử lý" },
        allowed: { missing: ["investigate", "dispose", "update_sdp", "no_action"], extra: ["add_to_books", "update_sdp", "no_action"], broken: ["repair", "dispose", "no_action"] },
      };
    if (path.endsWith("/reconciliation")) return reconciliation.current;
    if (path.endsWith("/preview"))
      return previewReply.current ?? {
        sheet: "TSCD", preview_token: "t0", headers: [{ col: "A", top: "", sub: "Mã TSCĐ" }, { col: "B", top: "", sub: "Tên TSCĐ" }, { col: "C", top: "", sub: "Nguyên giá" }], mapping: { code: "A", name: "B", cost: "C" }, rows: 2, total_cost: "30000000",
        first_rows: [{ row_no: 2, code: "TS001", name: "Laptop", quantity: "1", cost: "30000000", net: "10000000" }], warnings: [], warnings_total: 0,
      };
    if (path.endsWith("/stocktakes")) return { items: [], last_sync: null };
    if (/\/stocktakes\/[^/]+$/.test(path)) return stocktake.current;
    return {};
  }),
}));

import { api } from "@/core/api/client";
import { allowedResolutions, diffKind } from "./StocktakePage";
import { StocktakePage } from "./StocktakePage";

const item = (id: number, o: Record<string, unknown> = {}) => ({
  id, asset_id: String(id), name: `Máy ${id}`, product_type: "Laptop", asset_tag: `TAG${id}`, serial: `SN${id}`, fa_code: null, state: null,
  user_name: "Lê A", employee_id: "001", department: null, site: null, zone_id: "z1", count: "pending", condition: null, seen_at: "", note: "",
  counted_by: null, resolution: null, resolution_note: "", difference: null, ...o,
});
const zone = (o: Record<string, unknown> = {}) => ({
  id: "z1", name: "Tầng 5", is_store: false, assignee_user_id: "u1", assignee_name: "Tôi",
  book: 1, pending: 1, found: 0, missing: 0, extra: 0, differences: 0, unresolved: 0, ...o,
});
const head = (o: Record<string, unknown> = {}) => ({
  id: "s1", name: "Kiểm kê 2026", cutoff: "2026-12-31", status: "counting", status_label: "Đang kiểm", rules_version: 2,
  source_synced_at: null, created_by: "admin", closed_at: null, record: null, zones: [zone()], items: [item(1)],
  summary: { book: 1, pending: 1, found: 0, missing: 0, extra: 0, differences: 0, unresolved: 0 }, ...o,
});

function renderPage(url = "/lifecycle/stocktake/s1") {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[url]}>
        <Routes><Route path="/lifecycle/stocktake/:id" element={<StocktakePage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
});
beforeEach(() => {
  perms.clear();
  calls.length = 0;
  previewReply.current = null;
  vi.mocked(api).mockClear();
  reconciliation.current = { lines: [], off_register: [], totals: {}, meta: { registers: {}, rules_version: 2 } };
});

describe("stocktake helpers", () => {
  it("derives the kind of difference and its allowed resolutions", () => {
    const meta = { resolutions: { a: "A", b: "B", c: "C" }, allowed: { missing: ["b"] } };
    expect(diffKind(item(1, { count: "missing" }) as never)).toBe("missing");
    expect(diffKind(item(1, { count: "found", condition: "damaged" }) as never)).toBeNull();
    expect(allowedResolutions("missing", meta)).toEqual([["b", "B"]]);
    expect(allowedResolutions("damaged", meta).map(([k]) => k)).toEqual(["a", "b", "c"]); // a kind the server lists nothing for
  });
});

describe("walk", () => {
  it("sends the walked zone with every scan", async () => {
    ["lifecycle.stocktake.view", "lifecycle.stocktake.count"].forEach((p) => perms.add(p));
    stocktake.current = head();
    renderPage();
    const input = await screen.findByRole("textbox", { name: /Quét tem/ });
    fireEvent.change(input, { target: { value: "SN1" } });
    fireEvent.click(screen.getByRole("button", { name: /Ghi nhận/ }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/scan"))).toBe(true));
    expect(calls.find((c) => c.path.endsWith("/scan"))!.init!.json).toEqual({ code: "SN1", zone_id: "z1" });
  });

  it("says so when the asset was already counted in another zone (item null)", async () => {
    ["lifecycle.stocktake.view", "lifecycle.stocktake.count"].forEach((p) => perms.add(p));
    stocktake.current = head();
    vi.mocked(api).mockImplementationOnce(async () => stocktake.current as never); // the page query
    renderPage();
    const input = await screen.findByRole("textbox", { name: /Quét tem/ });
    vi.mocked(api).mockImplementationOnce(async () => ({ matched: true, item: null, already: true }) as never);
    fireEvent.change(input, { target: { value: "SN9" } });
    fireEvent.click(screen.getByRole("button", { name: /Ghi nhận/ }));
    expect(await screen.findByText("Đã kiểm ở khu khác")).toBeTruthy();
  });

  it("counts a whole desk with one count-batch request", async () => {
    ["lifecycle.stocktake.view", "lifecycle.stocktake.count"].forEach((p) => perms.add(p));
    stocktake.current = head({ items: [item(1), item(2)] });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Có đủ 2 máy" }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/count-batch"))).toBe(true));
    expect(calls.find((c) => c.path.endsWith("/count-batch"))!.init!.json).toEqual({ item_ids: [1, 2], count: "found", condition: "good" });
  });
});

describe("review", () => {
  const reviewing = (items: unknown[], unresolved = 0) =>
    head({
      status: "reviewing", status_label: "Đang xử lý chênh lệch", zones: [zone({ assignee_user_id: null })], items,
      summary: { book: 1, pending: 0, found: 0, missing: 1, extra: 0, differences: items.length, unresolved, unzoned: 0 },
    });
  const reviewer = () => ["view", "view_all", "manage"].forEach((p) => perms.add(`lifecycle.stocktake.${p}`));

  it("offers only the resolutions that fit the kind of difference", async () => {
    reviewer();
    stocktake.current = reviewing([item(1, { count: "missing", difference: "Không tìm thấy khi kiểm" })], 1);
    renderPage();
    const select = await screen.findByRole("combobox", { name: "Hướng xử lý Máy 1" });
    const labels = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(labels).toContain("Tìm thêm (ghi rõ)");
    expect(labels).not.toContain("Đề nghị KT ghi tăng");
    expect(labels).not.toContain("Sửa chữa");
  });

  it("marks a suggested resolution and confirms it with the same note", async () => {
    reviewer();
    stocktake.current = reviewing([item(1, { count: "missing", difference: "Không tìm thấy khi kiểm", resolution: "investigate", resolution_note: "Không tìm thấy khi kiểm", resolution_suggested: true })], 1);
    renderPage();
    expect(await screen.findByText("Gợi ý")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Xác nhận" }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/items/1/resolution"))).toBe(true));
    expect(calls.find((c) => c.path.endsWith("/resolution"))!.init!.json).toEqual({ resolution: "investigate", note: "Không tìm thấy khi kiểm" });
  });

  it("asks for confirmation before closing the stocktake", async () => {
    reviewer();
    stocktake.current = reviewing([item(1, { count: "missing", difference: "x", resolution: "no_action" })], 0);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Đóng kỳ" }));
    expect(calls.some((c) => c.path.endsWith("/close"))).toBe(false);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Không hoàn tác được/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Đóng kỳ kiểm kê" }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/close") && c.init?.method === "POST")).toBe(true));
  });

  it("warns about assets in no zone", async () => {
    reviewer();
    const s = reviewing([]);
    (s.summary as unknown as { unzoned: number }).unzoned = 3;
    stocktake.current = s;
    renderPage();
    expect(await screen.findByText(/3 tài sản chưa thuộc khu nào/)).toBeTruthy();
  });
});

describe("finance tab", () => {
  it("is hidden without lifecycle.stocktake.finance", async () => {
    ["view", "view_all", "manage"].forEach((p) => perms.add(`lifecycle.stocktake.${p}`));
    stocktake.current = head({ status: "reviewing", items: [] });
    renderPage();
    await screen.findByText("Kiểm kê 2026");
    expect(screen.queryByRole("tab", { name: "Đối chiếu Kế toán" })).toBeNull();
    expect(calls.some((c) => c.path.endsWith("/reconciliation"))).toBe(false);
  });

  it("previews the file, then imports it only after Nhập sổ", async () => {
    ["view", "view_all", "manage", "finance"].forEach((p) => perms.add(`lifecycle.stocktake.${p}`));
    stocktake.current = head({ status: "reviewing", items: [] });
    renderPage("/lifecycle/stocktake/s1?mode=finance");
    const file = new File(["x"], "so-tscd.xlsx");
    fireEvent.change(await screen.findByTestId("file-tscd"), { target: { files: [file] } });
    expect(await screen.findByText(/sheet “TSCD”/)).toBeTruthy();
    expect(screen.getByText("Ngày chốt số liệu")).toBeTruthy();
    expect(calls.some((c) => c.path.endsWith("/registers/tscd/preview") && c.init?.method === "POST")).toBe(true);
    expect(calls.some((c) => c.path.endsWith("/registers/tscd"))).toBe(false); // nothing stored yet
    fireEvent.click(screen.getByRole("button", { name: "Nhập sổ" }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/registers/tscd"))).toBe(true));
    const fd = calls.find((c) => c.path.endsWith("/registers/tscd"))!.init!.body as FormData;
    expect(fd.get("confirm")).toBe("true");
    expect((fd.get("file") as File).name).toBe("so-tscd.xlsx");
  });

  it("lists the register lines with statuses and vi-VN money", async () => {
    ["view", "view_all", "finance"].forEach((p) => perms.add(`lifecycle.stocktake.${p}`));
    stocktake.current = head({ status: "reviewing", items: [] });
    const t = (qty: string | null, cost: string | null, net: string | null) => ({ qty, cost, net });
    reconciliation.current = {
      lines: [{ kind: "tscd", code: "TS001", name: "Laptop Dell", place: "IT", book: t("1", "30000000", "10000000"), counted: t("0", "0", "0"), diff: t("-1", "-30000000", "-10000000"), note: "", items: 1, status: "missing_all" }],
      off_register: [], totals: {}, meta: { registers: { tscd: { rows: 1, total_cost: "30000000", as_of: "2026-12-31", imported_by: "kt@x.vn", imported_at: "2026-12-31T01:00:00Z" } }, rules_version: 2 },
    };
    renderPage("/lifecycle/stocktake/s1?mode=finance");
    const row = (await screen.findByText("Laptop Dell")).closest("tr")!;
    expect(within(row).getByText("Không thấy")).toBeTruthy();
    expect(within(row).getAllByText("30.000.000").length).toBeGreaterThan(0);
  });

  it("refuses to import while the preview has unreadable cells, and sends the preview token otherwise", async () => {
    ["view", "view_all", "manage", "finance"].forEach((p) => perms.add(`lifecycle.stocktake.${p}`));
    stocktake.current = head({ status: "reviewing", items: [] });
    const base = { sheet: "TSCD", preview_token: "abc", headers: [{ col: "A", top: "", sub: "Mã TSCĐ" }, { col: "B", top: "", sub: "Nguyên giá" }], mapping: { code: "A", cost: "B" }, rows: 1, total_cost: "1", first_rows: [], warnings: [], warnings_total: 0 };
    previewReply.current = { ...base, errors: [{ row: 7, column: "Nguyên giá", value: "abc" }] };
    renderPage("/lifecycle/stocktake/s1?mode=finance");
    fireEvent.change(await screen.findByTestId("file-tscd"), { target: { files: [new File(["x"], "a.xlsx")] } });
    expect(await screen.findByText(/1 ô không đọc được/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Nhập sổ" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("blocks the import on same-label columns until one is chosen, then re-previews with letters", async () => {
    ["view", "view_all", "manage", "finance"].forEach((p) => perms.add(`lifecycle.stocktake.${p}`));
    stocktake.current = head({ status: "reviewing", items: [] });
    previewReply.current = { sheet: "TSCD", preview_token: "t1", headers: [{ col: "A", top: "", sub: "Mã TSCĐ" }, { col: "C", top: "Số dư đầu kỳ", sub: "Nguyên giá" }, { col: "F", top: "Số dư cuối kỳ", sub: "Nguyên giá" }], mapping: { code: "A" }, ambiguous: { cost: [{ col: "C", label: "Số dư đầu kỳ – Nguyên giá" }, { col: "F", label: "Số dư cuối kỳ – Nguyên giá" }] }, errors: [], rows: 1, total_cost: "1", first_rows: [], warnings: [], warnings_total: 0 };
    renderPage("/lifecycle/stocktake/s1?mode=finance");
    fireEvent.change(await screen.findByTestId("file-tscd"), { target: { files: [new File(["x"], "a.xlsx")] } });
    expect(await screen.findByText(/Có cột trùng nhãn/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Nhập sổ" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getAllByText("F – Nguyên giá (Số dư cuối kỳ)").length).toBeGreaterThan(0);
  });

  it("sends preview_token from a clean preview", async () => {
    ["view", "view_all", "manage", "finance"].forEach((p) => perms.add(`lifecycle.stocktake.${p}`));
    stocktake.current = head({ status: "reviewing", items: [] });
    previewReply.current = { sheet: "TSCD", preview_token: "abc", headers: [{ col: "A", top: "", sub: "Mã TSCĐ" }], mapping: { code: "A" }, errors: [], rows: 1, total_cost: "1", first_rows: [], warnings: [], warnings_total: 0 };
    renderPage("/lifecycle/stocktake/s1?mode=finance");
    fireEvent.change(await screen.findByTestId("file-ccdc"), { target: { files: [new File(["x"], "a.xlsx")] } });
    fireEvent.click(await screen.findByRole("button", { name: "Nhập sổ" }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/registers/ccdc"))).toBe(true));
    const fd = calls.find((c) => c.path.endsWith("/registers/ccdc"))!.init!.body as FormData;
    expect(fd.get("preview_token")).toBe("abc");
    expect(fd.get("mapping")).toBeNull(); // no override was previewed
  });
});
