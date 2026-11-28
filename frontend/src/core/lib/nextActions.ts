import { useQuery } from "@tanstack/react-query";
import { api } from "@/core/api/client";
import type { Status } from "@/core/ui/status";

export interface NextItem {
  key: string;
  kind: string;
  priority: number;
  title: string;
  detail: string;
  due: string | null;
  href: string;
  action: string;
  /** Needs nothing from this person (waiting on the system, an account notice): never the next thing to do. */
  passive?: boolean;
}

/** Links from the queue carry this state so the target page can offer "← Việc của tôi". */
export const FROM_QUEUE = { from: "me" } as const;

export const LEVEL: Record<number, { label: string; status: Status }> = {
  0: { label: "Cần bạn làm ngay", status: "failed" },
  1: { label: "Trễ hạn", status: "waiting" },
  2: { label: "Sắp đến hạn", status: "warning" },
  3: { label: "Cần biết", status: "pending" },
  4: { label: "Gợi ý", status: "pending" },
};

/** The signed-in person's "Việc tiếp theo" queue across modules. */
export const useNextActions = () =>
  useQuery({
    queryKey: ["me", "next"],
    queryFn: () => api<{ items: NextItem[]; failed: string[] }>("/api/core/me/next"),
    refetchInterval: 60_000,
  });
