import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import type { Exam, ExamCreateInput, ExamUpdateInput, ExamMark, ExamMarkCreateInput, ExamMarkUpdateInput } from "./types";

export interface ExamListParams {
  subject?: string;
  date_?: string;
  search?: string;
  limit?: number;
  offset?: number;
}

const keys = {
  all: ["exams"] as const,
  list: (params: ExamListParams) => ["exams", "list", params] as const,
  detail: (id: number) => ["exams", "detail", id] as const,
  marksAll: ["exam-marks"] as const,
  marksForExam: (examId: number) => ["exam-marks", "exam", examId] as const,
};

export function useExams(params: ExamListParams) {
  return useQuery({
    queryKey: keys.list(params),
    queryFn: async () => {
      const { data } = await apiClient.get<Exam[]>("/api/exams", { params });
      return data;
    },
  });
}

export function useExam(examId: number | undefined) {
  return useQuery({
    queryKey: keys.detail(examId ?? -1),
    queryFn: async () => {
      const { data } = await apiClient.get<Exam>(`/api/exams/${examId}`);
      return data;
    },
    enabled: examId !== undefined,
  });
}

export function useCreateExam() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: ExamCreateInput) => {
      const { data } = await apiClient.post<Exam>("/api/exams", input);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useUpdateExam(examId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: ExamUpdateInput) => {
      const { data } = await apiClient.patch<Exam>(`/api/exams/${examId}`, input);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useDeleteExam() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (examId: number) => {
      await apiClient.delete(`/api/exams/${examId}`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useMarksForStudent(studentId: number | undefined) {
  return useQuery({
    queryKey: ["exam-marks", "student", studentId ?? -1],
    queryFn: async () => {
      const { data } = await apiClient.get<ExamMark[]>("/api/exam-marks", {
        params: { student_id: studentId, limit: 200 },
      });
      return data;
    },
    enabled: studentId !== undefined,
  });
}

export function useMarksForExam(examId: number | undefined) {
  return useQuery({
    queryKey: keys.marksForExam(examId ?? -1),
    queryFn: async () => {
      const { data } = await apiClient.get<ExamMark[]>(`/api/exams/${examId}/marks`, {
        params: { limit: 200 },
      });
      return data;
    },
    enabled: examId !== undefined,
  });
}

export function useAddExamMark(examId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: ExamMarkCreateInput) => {
      const { data } = await apiClient.post<ExamMark>(`/api/exams/${examId}/marks`, input);
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.marksForExam(examId) });
      qc.invalidateQueries({ queryKey: keys.marksAll });
    },
  });
}

export function useUpdateExamMark(examId: number, markId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: ExamMarkUpdateInput) => {
      const { data } = await apiClient.patch<ExamMark>(`/api/exam-marks/${markId}`, input);
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.marksForExam(examId) });
      qc.invalidateQueries({ queryKey: keys.marksAll });
    },
  });
}

export function useDeleteExamMark(examId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (markId: number) => {
      await apiClient.delete(`/api/exam-marks/${markId}`);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.marksForExam(examId) });
      qc.invalidateQueries({ queryKey: keys.marksAll });
    },
  });
}
