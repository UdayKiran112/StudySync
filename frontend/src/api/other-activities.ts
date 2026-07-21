import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import type { OtherActivity, OtherActivityInput, OtherActivityAttendance, OtherActivityAttendanceInput } from "./types";

const keys = { all: ["other-activities"] as const, detail: (id: number) => ["other-activities", id] as const, attendance: (id: number) => ["other-activities", id, "attendance"] as const };

export function useOtherActivities() {
  return useQuery({
    queryKey: keys.all,
    queryFn: async () => (await apiClient.get<OtherActivity[]>("/api/other-activities", { params: { limit: 200 } })).data,
    staleTime: 5 * 60 * 1000,
  });
}

export function useCreateOtherActivity() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: OtherActivityInput) => (await apiClient.post<OtherActivity>("/api/other-activities", input)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useGetOtherActivity(activityId: number | undefined) {
  return useQuery({
    queryKey: keys.detail(activityId ?? -1),
    queryFn: async () => (await apiClient.get<OtherActivity>(`/api/other-activities/${activityId}`)).data,
    enabled: activityId !== undefined,
    staleTime: 5 * 60 * 1000,
  });
}

export function useUpdateOtherActivity(activityId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: OtherActivityInput) => (await apiClient.put<OtherActivity>(`/api/other-activities/${activityId}`, input)).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.all });
      qc.invalidateQueries({ queryKey: keys.detail(activityId) });
    },
  });
}

export function useDeleteOtherActivity() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => apiClient.delete(`/api/other-activities/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useOtherActivityAttendance(activityId: number | undefined) {
  return useQuery({
    queryKey: keys.attendance(activityId ?? -1),
    queryFn: async () => (await apiClient.get<OtherActivityAttendance[]>(`/api/other-activities/${activityId}/attendance`)).data,
    enabled: activityId !== undefined,
  });
}

export function useAddOtherActivityAttendance(activityId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: OtherActivityAttendanceInput) => (await apiClient.post<OtherActivityAttendance>(`/api/other-activities/${activityId}/attendance`, input)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.attendance(activityId) }),
  });
}

export function useDeleteOtherActivityAttendance(activityId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (attendanceId: number) => apiClient.delete(`/api/other-activities/${activityId}/attendance/${attendanceId}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.attendance(activityId) }),
  });
}
