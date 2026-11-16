import { useQuery } from "@tanstack/react-query";
import { api } from "@/core/api/client";
import type { ConnectionProfile, ConnectorType } from "@/core/api/types";

export const connectionsKey = ["connections"] as const;

export function useConnectorTypes() {
  return useQuery({
    queryKey: ["connector-types"],
    queryFn: () => api<ConnectorType[]>("/api/core/connectors"),
    staleTime: 5 * 60_000,
  });
}

export function useConnections() {
  return useQuery({ queryKey: connectionsKey, queryFn: () => api<ConnectionProfile[]>("/api/core/connections") });
}

export function useConnection(id: string) {
  return useQuery({
    queryKey: [...connectionsKey, id],
    queryFn: () => api<ConnectionProfile>(`/api/core/connections/${id}`),
  });
}
