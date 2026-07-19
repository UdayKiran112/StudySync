import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import type { Book, BookCreateInput, BookUpdateInput } from "./types";

export interface BookListParams {
  category?: string;
  search?: string;
  limit?: number;
  offset?: number;
}

const keys = {
  all: ["books"] as const,
  list: (params: BookListParams) => ["books", "list", params] as const,
};

export function useBooks(params: BookListParams) {
  return useQuery({
    queryKey: keys.list(params),
    queryFn: async () => {
      const { data } = await apiClient.get<Book[]>("/api/books", { params });
      return data;
    },
  });
}

export function useCreateBook() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: BookCreateInput) => {
      const { data } = await apiClient.post<Book>("/api/books", input);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useUpdateBook(bookId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: BookUpdateInput) => {
      const { data } = await apiClient.patch<Book>(`/api/books/${bookId}`, input);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useDeleteBook() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (bookId: string) => {
      await apiClient.delete(`/api/books/${bookId}`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}
