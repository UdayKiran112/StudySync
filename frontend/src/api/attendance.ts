import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import type { Attendance, AttendanceCheckInInput, AttendanceCheckOutInput } from "./types";

export interface AttendanceListParams {
  student_id?: number;
  date_?: string;
  session?: string;
  limit?: number;
  offset?: number;
}

const keys = {
  all: ["attendance"] as const,
  list: (params: AttendanceListParams) => ["attendance", "list", params] as const,
};

export function useAttendanceList(params: AttendanceListParams) {
  return useQuery({
    queryKey: keys.list(params),
    queryFn: async () => {
      const { data } = await apiClient.get<Attendance[]>("/api/attendance", { params });
      return data;
    },
  });
}

export function useCheckIn() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: AttendanceCheckInInput) => {
      const { data } = await apiClient.post<Attendance>("/api/attendance/check-in", input);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useCheckOut() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: AttendanceCheckOutInput) => {
      const { data } = await apiClient.post<Attendance>("/api/attendance/check-out", input);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useDeleteAttendance() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (attendanceId: number) => {
      await apiClient.delete(`/api/attendance/${attendanceId}`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}
