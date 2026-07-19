import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import type { OfflineLibraryUsage, OfflineLibraryCreateInput, OfflineLibraryUpdateInput } from "./types";

export interface OfflineLibraryListParams {
  student_id?: number;
  date_?: string;
  book_id?: string;
  limit?: number;
  offset?: number;
}

const keys = {
  all: ["offline-library"] as const,
  list: (params: OfflineLibraryListParams) => ["offline-library", "list", params] as const,
};

export function useOfflineLibraryList(params: OfflineLibraryListParams) {
  return useQuery({
    queryKey: keys.list(params),
    queryFn: async () => {
      const { data } = await apiClient.get<OfflineLibraryUsage[]>("/api/offline-library", { params });
      return data;
    },
  });
}

export function useCreateOfflineUsage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: OfflineLibraryCreateInput) => {
      const { data } = await apiClient.post<OfflineLibraryUsage>("/api/offline-library", input);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useUpdateOfflineUsage(usageId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: OfflineLibraryUpdateInput) => {
      const { data } = await apiClient.patch<OfflineLibraryUsage>(`/api/offline-library/${usageId}`, input);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useDeleteOfflineUsage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (usageId: number) => {
      await apiClient.delete(`/api/offline-library/${usageId}`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}
