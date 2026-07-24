import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import type {
  CoachingClass,
  CoachingClassInput,
  CoachingEnrollment,
  CoachingEnrollmentInput,
  Instructor,
  ExternalParticipant,
} from "./types";

export interface CoachingClassListParams {
  date_?: string;
}

const keys = {
  all: ["coaching-classes"] as const,
  list: (params: CoachingClassListParams) =>
    ["coaching-classes", "list", params] as const,
  detail: (id: number) => ["coaching-classes", "detail", id] as const,
  roster: (id: number) => ["coaching-classes", id, "enrollments"] as const,
  instructors: ["coaching-instructors"] as const,
  externalParticipants: (search: string | undefined) =>
    ["external-participants", search] as const,
};

// Sessions -------------------------------------------------------------

export function useCoachingClasses(params: CoachingClassListParams = {}) {
  return useQuery({
    queryKey: keys.list(params),
    queryFn: async () => {
      const { data } = await apiClient.get<CoachingClass[]>(
        "/api/coaching-classes",
        { params },
      );
      return data;
    },
  });
}

export function useCoachingClass(classId: number | undefined) {
  return useQuery({
    queryKey: keys.detail(classId ?? -1),
    queryFn: async () => {
      const { data } = await apiClient.get<CoachingClass>(
        `/api/coaching-classes/${classId}`,
      );
      return data;
    },
    enabled: classId !== undefined,
  });
}

export function useCreateCoachingClass() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: CoachingClassInput) => {
      const { data } = await apiClient.post<CoachingClass>(
        "/api/coaching-classes",
        input,
      );
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useDeleteCoachingClass() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (classId: number) => {
      await apiClient.delete(`/api/coaching-classes/${classId}`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

// Roster -----------------------------------------------------------------

export function useCoachingEnrollments(classId: number | undefined) {
  return useQuery({
    queryKey: keys.roster(classId ?? -1),
    queryFn: async () => {
      const { data } = await apiClient.get<CoachingEnrollment[]>(
        `/api/coaching-classes/${classId}/enrollments`,
      );
      return data;
    },
    enabled: classId !== undefined,
  });
}

export function useAddCoachingEnrollment(classId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: CoachingEnrollmentInput) => {
      const { data } = await apiClient.post<CoachingEnrollment>(
        `/api/coaching-classes/${classId}/enrollments`,
        input,
      );
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.roster(classId) }),
  });
}

// NOTE: there is no DELETE /api/coaching-classes/enrollments/{id} route on
// the backend yet (checked routers/coaching.py — only GET/POST exist for
// enrollments). This hook is kept ready for when that route is added, but
// nothing in the UI calls it today; wiring up an "unenroll" button against
// it would just 404.
export function useDeleteCoachingEnrollment(classId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (enrollmentId: number) => {
      await apiClient.delete(
        `/api/coaching-classes/enrollments/${enrollmentId}`,
      );
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.roster(classId) }),
  });
}

// Instructors --------------------------------------------------------------

export function useInstructors() {
  return useQuery({
    queryKey: keys.instructors,
    queryFn: async () => {
      const { data } = await apiClient.get<Instructor[]>(
        "/api/coaching-classes/instructors",
      );
      return data;
    },
  });
}

export function useCreateInstructor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (
      input: Pick<Instructor, "name" | "phone" | "specialization" | "notes">,
    ) => {
      const { data } = await apiClient.post<Instructor>(
        "/api/coaching-classes/instructors",
        input,
      );
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.instructors }),
  });
}

// External participants -----------------------------------------------------

export function useExternalParticipants(search?: string) {
  return useQuery({
    queryKey: keys.externalParticipants(search),
    queryFn: async () => {
      const { data } = await apiClient.get<ExternalParticipant[]>(
        "/api/coaching-classes/external-participants",
        {
          params: { search: search || undefined },
        },
      );
      return data;
    },
  });
}

export function useCreateExternalParticipant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (
      input: Omit<
        ExternalParticipant,
        "external_participant_id" | "created_at"
      >,
    ) => {
      const { data } = await apiClient.post<ExternalParticipant>(
        "/api/coaching-classes/external-participants",
        input,
      );
      return data;
    },
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["external-participants"] }),
  });
}
