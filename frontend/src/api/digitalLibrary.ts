import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import type {
  DigitalLibraryUsage,
  DigitalLibraryCheckInInput,
  DigitalLibraryCheckOutInput,
} from "./types";

export interface DigitalLibraryListParams {
  student_id?: number;
  date_?: string;
  account_type?: string;
  limit?: number;
  offset?: number;
}

const keys = {
  all: ["digital-library"] as const,
  list: (params: DigitalLibraryListParams) => ["digital-library", "list", params] as const,
};

export function useDigitalLibraryList(params: DigitalLibraryListParams) {
  return useQuery({
    queryKey: keys.list(params),
    queryFn: async () => {
      const { data } = await apiClient.get<DigitalLibraryUsage[]>("/api/digital-library", { params });
      return data;
    },
  });
}

export function useDigitalCheckIn() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: DigitalLibraryCheckInInput) => {
      const { data } = await apiClient.post<DigitalLibraryUsage>("/api/digital-library/check-in", input);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useDigitalCheckOut() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: DigitalLibraryCheckOutInput) => {
      const { data } = await apiClient.post<DigitalLibraryUsage>("/api/digital-library/check-out", input);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useDeleteDigitalUsage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (usageId: number) => {
      await apiClient.delete(`/api/digital-library/${usageId}`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}
