import { Fragment, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CornerDownLeft } from "lucide-react";
import { useAuth } from "@/core/auth/AuthProvider";
import { Dialog } from "@/core/ui/dialog";
import { Input } from "@/core/ui/field";
import { iconFor } from "@/core/ui/icons";
import { cn } from "@/core/lib/cn";
import { fold } from "@/core/lib/format";
import { MIN_SEARCH_CHARS, useGlobalSearch } from "@/core/lib/search";
import { useDebounced } from "@/core/lib/useDebounced";

const KIND_ICON: Record<string, string> = {
  job: "workflow",
  document: "file-text",
  request: "ticket",
  ticket: "ticket",
  purchase: "shopping-cart",
  guide: "book-open",
};

export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { me } = useAuth();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  useEffect(() => {
    if (open) {
      setQ("");
      setActive(0);
    }
  }, [open]);

  const needle = fold(q.trim());
  const debounced = useDebounced(q.trim(), 250);
  const search = useGlobalSearch(open && fold(debounced).length >= MIN_SEARCH_CHARS ? debounced : "");
  const waiting = needle.length >= MIN_SEARCH_CHARS && (debounced !== q.trim() || search.isFetching);
  const groups = needle.length >= MIN_SEARCH_CHARS && debounced === q.trim() ? (search.data?.groups ?? []) : [];

  // One flat list for keyboard navigation: pages first, then the records found.
  const items = useMemo(() => {
    const all = (me?.nav ?? []).flatMap((g) => g.items.map((i) => ({ ...i, group: g.label })));
    const pages = (needle ? all.filter((i) => fold(`${i.label} ${i.group}`).includes(needle)) : all).map((i) => ({
      key: i.path,
      title: i.label,
      detail: i.group,
      href: i.path,
      icon: i.icon,
      heading: null as string | null,
    }));
    const found = groups.flatMap((g) =>
      g.items.map((i, n) => ({ key: `${g.key}:${i.href}`, title: i.title, detail: i.detail, href: i.href, icon: KIND_ICON[i.kind] ?? "file-text", heading: n === 0 ? g.label : null })),
    );
    return [...pages, ...found];
  }, [me, needle, groups]);

  const go = (path: string) => {
    onClose();
    navigate(path);
  };

  return (
    <Dialog open={open} onClose={onClose} title="Đi tới" size="sm">
      <Input
        data-autofocus
        role="combobox"
        aria-expanded="true"
        aria-controls="palette-list"
        aria-activedescendant={items[active] ? `palette-${active}` : undefined}
        aria-label="Tìm trang và dữ liệu"
        placeholder="Gõ tên trang hoặc tìm Job, biên bản, task… (từ 2 ký tự)"
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setActive(0);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setActive((a) => Math.min(a + 1, items.length - 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((a) => Math.max(a - 1, 0));
          } else if (e.key === "Enter" && items[active]) {
            e.preventDefault();
            go(items[active]!.href);
          }
        }}
      />
      <ul id="palette-list" role="listbox" aria-label="Kết quả" className="mt-2 max-h-72 overflow-y-auto">
        {items.map((item, i) => {
          const Icon = iconFor(item.icon);
          return (
            <Fragment key={item.key}>
              {item.heading && (
                <li role="presentation" className="px-2 pb-1 pt-3 text-xs font-medium uppercase text-muted">
                  {item.heading}
                </li>
              )}
              <li
                id={`palette-${i}`}
                role="option"
                aria-selected={i === active}
                onMouseEnter={() => setActive(i)}
                onClick={() => go(item.href)}
                className={cn(
                  "flex cursor-pointer items-center gap-2.5 rounded-sm px-2 py-2",
                  i === active && "bg-primary-soft",
                )}
              >
                <Icon className="size-4 shrink-0 text-muted" aria-hidden="true" />
                <span className="min-w-0 flex-1 truncate">{item.title}</span>
                <span className="max-w-[50%] truncate text-xs text-muted">{item.detail}</span>
                {i === active && <CornerDownLeft className="size-3.5 text-muted" aria-hidden="true" />}
              </li>
            </Fragment>
          );
        })}
      </ul>
      {waiting && <p role="status" className="px-2 py-3 text-center text-sm text-muted">Đang tìm…</p>}
      {!waiting && items.length === 0 && (
        <p role="status" className="px-2 py-6 text-center text-sm text-muted">Không tìm thấy “{q}”.</p>
      )}
      {!waiting && search.isError && (
        <p role="status" className="px-2 py-2 text-xs text-muted">Không tìm được dữ liệu lúc này.</p>
      )}
      {!waiting && (search.data?.failed.length ?? 0) > 0 && (
        <p role="status" className="px-2 py-2 text-xs text-muted">
          Chưa tìm được trong: {search.data?.failed.join(", ")}.
        </p>
      )}
    </Dialog>
  );
}
