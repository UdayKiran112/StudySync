import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import type { Quiz, QuizCreateInput, QuizUpdateInput, QuizScore, QuizScoreCreateInput, QuizScoreUpdateInput } from "./types";

export interface QuizListParams {
  subject?: string;
  date_?: string;
  search?: string;
  limit?: number;
  offset?: number;
}

const keys = {
  all: ["quizzes"] as const,
  list: (params: QuizListParams) => ["quizzes", "list", params] as const,
  detail: (id: number) => ["quizzes", "detail", id] as const,
  scoresAll: ["quiz-scores"] as const,
  scoresForQuiz: (quizId: number) => ["quiz-scores", "quiz", quizId] as const,
};

export function useQuizzes(params: QuizListParams) {
  return useQuery({
    queryKey: keys.list(params),
    queryFn: async () => {
      const { data } = await apiClient.get<Quiz[]>("/api/quizzes", { params });
      return data;
    },
  });
}

export function useQuiz(quizId: number | undefined) {
  return useQuery({
    queryKey: keys.detail(quizId ?? -1),
    queryFn: async () => {
      const { data } = await apiClient.get<Quiz>(`/api/quizzes/${quizId}`);
      return data;
    },
    enabled: quizId !== undefined,
  });
}

export function useCreateQuiz() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: QuizCreateInput) => {
      const { data } = await apiClient.post<Quiz>("/api/quizzes", input);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useUpdateQuiz(quizId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: QuizUpdateInput) => {
      const { data } = await apiClient.patch<Quiz>(`/api/quizzes/${quizId}`, input);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useDeleteQuiz() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (quizId: number) => {
      await apiClient.delete(`/api/quizzes/${quizId}`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useScoresForStudent(studentId: number | undefined) {
  return useQuery({
    queryKey: ["quiz-scores", "student", studentId ?? -1],
    queryFn: async () => {
      const { data } = await apiClient.get<QuizScore[]>("/api/quiz-scores", {
        params: { student_id: studentId, limit: 200 },
      });
      return data;
    },
    enabled: studentId !== undefined,
  });
}

export function useScoresForQuiz(quizId: number | undefined) {
  return useQuery({
    queryKey: keys.scoresForQuiz(quizId ?? -1),
    queryFn: async () => {
      const { data } = await apiClient.get<QuizScore[]>(`/api/quizzes/${quizId}/scores`, {
        params: { limit: 200 },
      });
      return data;
    },
    enabled: quizId !== undefined,
  });
}

export function useAddQuizScore(quizId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: QuizScoreCreateInput) => {
      const { data } = await apiClient.post<QuizScore>(`/api/quizzes/${quizId}/scores`, input);
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.scoresForQuiz(quizId) });
      qc.invalidateQueries({ queryKey: keys.scoresAll });
    },
  });
}

export function useUpdateQuizScore(quizId: number, scoreId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: QuizScoreUpdateInput) => {
      const { data } = await apiClient.patch<QuizScore>(`/api/quiz-scores/${scoreId}`, input);
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.scoresForQuiz(quizId) });
      qc.invalidateQueries({ queryKey: keys.scoresAll });
    },
  });
}

export function useDeleteQuizScore(quizId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (scoreId: number) => {
      await apiClient.delete(`/api/quiz-scores/${scoreId}`);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.scoresForQuiz(quizId) });
      qc.invalidateQueries({ queryKey: keys.scoresAll });
    },
  });
}
