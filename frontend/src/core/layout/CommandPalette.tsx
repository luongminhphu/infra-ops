import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CornerDownLeft } from "lucide-react";
import { useAuth } from "@/core/auth/AuthProvider";
import { Dialog } from "@/core/ui/dialog";
import { Input } from "@/core/ui/field";
import { iconFor } from "@/core/ui/icons";
import { cn } from "@/core/lib/cn";

/** Removes Vietnamese diacritics so "ket noi" finds "Kết nối". */
export function fold(s: string): string {
  return s.normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/đ/g, "d").replace(/Đ/g, "D").toLowerCase();
}

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

  const items = useMemo(() => {
    const all = (me?.nav ?? []).flatMap((g) => g.items.map((i) => ({ ...i, group: g.label })));
    const needle = fold(q.trim());
    return needle ? all.filter((i) => fold(`${i.label} ${i.group}`).includes(needle)) : all;
  }, [me, q]);

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
        aria-label="Tìm trang"
        placeholder="Gõ tên trang, ví dụ: kết nối"
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
            go(items[active]!.path);
          }
        }}
      />
      <ul id="palette-list" role="listbox" aria-label="Kết quả" className="mt-2 max-h-72 overflow-y-auto">
        {items.length === 0 && <li className="px-2 py-6 text-center text-sm text-muted">Không có trang nào khớp “{q}”.</li>}
        {items.map((item, i) => {
          const Icon = iconFor(item.icon);
          return (
            <li
              key={item.path}
              id={`palette-${i}`}
              role="option"
              aria-selected={i === active}
              onMouseEnter={() => setActive(i)}
              onClick={() => go(item.path)}
              className={cn(
                "flex cursor-pointer items-center gap-2.5 rounded-sm px-2 py-2",
                i === active && "bg-primary-soft",
              )}
            >
              <Icon className="size-4 text-muted" aria-hidden="true" />
              <span className="flex-1">{item.label}</span>
              <span className="text-xs text-muted">{item.group}</span>
              {i === active && <CornerDownLeft className="size-3.5 text-muted" aria-hidden="true" />}
            </li>
          );
        })}
      </ul>
    </Dialog>
  );
}
