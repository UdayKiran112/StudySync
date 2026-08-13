import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { apiClient } from "./client";
import type { Holiday, HolidayCreateInput, HolidayUpdateInput } from "./types";

const keys = {
  all: ["holidays"] as const,
};

export function useHolidays() {
  return useQuery({
    queryKey: keys.all,
    queryFn: async () => {
      const { data } = await apiClient.get<Holiday[]>("/api/holidays");
      return data;
    },
    placeholderData: keepPreviousData,
  });
}

export function useCreateHoliday() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: HolidayCreateInput) => {
      const { data } = await apiClient.post<Holiday>("/api/holidays", input);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useUpdateHoliday(holidayId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: HolidayUpdateInput) => {
      const { data } = await apiClient.patch<Holiday>(
        `/api/holidays/${holidayId}`,
        input,
      );
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useDeleteHoliday() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (holidayId: number) => {
      await apiClient.delete(`/api/holidays/${holidayId}`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}
