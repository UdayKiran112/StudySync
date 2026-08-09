import { useQuery } from "@tanstack/react-query";
import { apiClient } from "./client";
import type { PresentItem, StudentDashboardResponse } from "./types";

function dashboardUrl(studentId: number) {
  return `/api/dashboard/students/${studentId}`;
}

export function useStudentDashboard(studentId: number | undefined) {
  return useQuery({
    queryKey: ["dashboard", "student", studentId ?? -1],
    queryFn: async () => {
      const { data } = await apiClient.get<StudentDashboardResponse>(dashboardUrl(studentId as number));
      return data;
    },
    enabled: studentId !== undefined,
  });
}

/** Who is in the building right now: open attendance + digital library
 *  sessions with student names joined in (see routers/dashboard.py). */
export function useCurrentlyPresent() {
  return useQuery({
    queryKey: ["dashboard", "currently-present"],
    queryFn: async () => {
      const { data } = await apiClient.get<PresentItem[]>(
        "/api/dashboard/currently-present",
      );
      return data;
    },
    refetchInterval: 5000,
  });
}
