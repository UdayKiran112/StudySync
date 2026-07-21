import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import type {
  Attendance,
  AttendanceCheckInInput,
  AttendanceCheckOutInput,
  AttendanceUpdateInput,
} from "./types";

/**
 * Matches backend/routers/attendance.py's list_attendance signature.
 *
 * Note: the backend takes a date RANGE (date_from / date_to), not a
 * single `date_` — and it no longer supports limit/offset at all, so
 * this always returns every matching record. Any pagination has to
 * happen client-side (see AttendancePage).
 */
export interface AttendanceListParams {
  student_id?: number;
  date_from?: string;
  date_to?: string;
  session?: string;
}

const keys = {
  all: ["attendance"] as const,
  list: (params: AttendanceListParams) =>
    ["attendance", "list", params] as const,
};

export function useAttendanceList(params: AttendanceListParams) {
  return useQuery({
    queryKey: keys.list(params),
    queryFn: async () => {
      const { data } = await apiClient.get<Attendance[]>("/api/attendance", {
        params,
      });
      return data;
    },
  });
}

export function useCheckIn() {
  const qc = useQueryClient();
  return useMutation({
    // session is no longer sent — the backend derives it from check_in.
    mutationFn: async (input: AttendanceCheckInInput) => {
      const { data } = await apiClient.post<Attendance>(
        "/api/attendance/check-in",
        input,
      );
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useCheckOut() {
  const qc = useQueryClient();
  return useMutation({
    // session/date are gone too — the backend finds this student's one
    // currently-open session on its own (at most one can ever be open).
    mutationFn: async (input: AttendanceCheckOutInput) => {
      const { data } = await apiClient.patch<Attendance>(
        "/api/attendance/check-out",
        input,
      );
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

/** Correct a mistaken check_in/check_out time. session and
 *  duration_minutes are recomputed server-side, not sent here. */
export function useUpdateAttendance(attendanceId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: AttendanceUpdateInput) => {
      const { data } = await apiClient.patch<Attendance>(
        `/api/attendance/${attendanceId}`,
        input,
      );
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
