import { useQuery } from "@tanstack/react-query";
import { api } from "@/core/api/client";

export interface SearchItem {
  title: string;
  detail: string;
  href: string;
  kind: string;
}
export interface SearchGroup {
  key: string;
  label: string;
  items: SearchItem[];
}
export const MIN_SEARCH_CHARS = 2;

/** Records matching `q` (Jobs, records, tasks…). Nothing is requested below 2 characters; a stale request is aborted. */
export const useGlobalSearch = (q: string) =>
  useQuery({
    queryKey: ["search", q],
    queryFn: ({ signal }) =>
      api<{ groups: SearchGroup[]; failed: string[] }>(`/api/core/search?q=${encodeURIComponent(q)}`, { signal }),
    enabled: q.length >= MIN_SEARCH_CHARS,
    staleTime: 15_000,
  });
