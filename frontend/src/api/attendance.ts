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
 * The backend takes a date RANGE (date_from / date_to) and paginates
 * server-side with limit/offset, returning { items, total } so a page
 * never has to download the full (growing) history. Ordering is
 * newest date first, then session, then check-in time.
 */
export interface AttendanceListParams {
  student_id?: number;
  date_from?: string;
  date_to?: string;
  session?: string;
  limit?: number;
  offset?: number;
}

export interface AttendancePage {
  items: Attendance[];
  total: number;
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
      const { data } = await apiClient.get<AttendancePage>("/api/attendance", {
        params,
      });
      return data;
    },
    // Live refresh: the ZKTeco poller imports swipes in the background, so
    // keep the visible table current without a manual reload.
    refetchInterval: 5000,
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
