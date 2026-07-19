import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { apiClient } from "./client";
import type { Student, StudentCreateInput, StudentUpdateInput } from "./types";

export interface StudentListParams {
  status?: string;
  search?: string;
  limit?: number;
  offset?: number;
}

const keys = {
  all: ["students"] as const,
  list: (params: StudentListParams) => ["students", "list", params] as const,
  detail: (id: number) => ["students", "detail", id] as const,
};

export function useStudents(params: StudentListParams) {
  return useQuery({
    queryKey: keys.list(params),
    queryFn: async () => {
      const { data } = await apiClient.get<Student[]>("/api/students", {
        params,
      });
      return data;
    },
  });
}

/**
 * Same shape as useStudents, but understands student IDs too.
 *
 * The backend's `search` query param only does a `name LIKE %...%` match
 * (see routers/students.py) — it has no idea what to do with a numeric
 * student ID. So when the search text is purely digits, we look the
 * student up directly via GET /api/students/{id} instead, and fall back
 * to an empty result (not an error) if that ID doesn't exist.
 */
export function useStudentSearch(params: StudentListParams) {
  const trimmed = (params.search ?? "").trim();
  const isIdLookup = trimmed.length > 0 && /^\d+$/.test(trimmed);

  const idLookup = useQuery({
    queryKey: ["students", "id-lookup", trimmed, params.status],
    queryFn: async () => {
      try {
        const { data } = await apiClient.get<Student>(
          `/api/students/${trimmed}`,
        );
        if (params.status && data.status !== params.status)
          return [] as Student[];
        return [data];
      } catch (err) {
        if (axios.isAxiosError(err) && err.response?.status === 404)
          return [] as Student[];
        throw err;
      }
    },
    enabled: isIdLookup,
  });

  const nameSearch = useStudents({
    ...params,
    search: isIdLookup ? undefined : params.search,
  });

  return isIdLookup ? idLookup : nameSearch;
}

export function useStudent(studentId: number | undefined) {
  return useQuery({
    queryKey: keys.detail(studentId ?? -1),
    queryFn: async () => {
      const { data } = await apiClient.get<Student>(
        `/api/students/${studentId}`,
      );
      return data;
    },
    enabled: studentId !== undefined,
  });
}

export function useCreateStudent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: StudentCreateInput) => {
      const { data } = await apiClient.post<Student>("/api/students", input);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useUpdateStudent(studentId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: StudentUpdateInput) => {
      const { data } = await apiClient.patch<Student>(
        `/api/students/${studentId}`,
        input,
      );
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useDeleteStudent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (studentId: number) => {
      await apiClient.delete(`/api/students/${studentId}`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}
