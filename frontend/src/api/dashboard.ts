import { useQuery } from "@tanstack/react-query";
import { apiClient } from "./client";
import type { StudentDashboardResponse } from "./types";

/**
 * The backend's routers/dashboard.py is currently empty — only the
 * response models (models/dashboard.py) exist. This is the one place
 * that assumes a URL shape for it, following the same convention as
 * every other router in the app (`/api/<resource>/...`):
 *
 *   GET /api/dashboard/students/{student_id} -> StudentDashboardResponse
 *
 * If the real route ends up different once it's implemented, this is
 * the only line that needs to change.
 */
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
