import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import type { Subscription, SubscriptionCreateInput, SubscriptionUpdateInput } from "./types";

export interface SubscriptionListParams {
  status?: string;
  search?: string;
  limit?: number;
  offset?: number;
}

const keys = {
  all: ["subscriptions"] as const,
  list: (params: SubscriptionListParams) => ["subscriptions", "list", params] as const,
};

export function useSubscriptions(params: SubscriptionListParams) {
  return useQuery({
    queryKey: keys.list(params),
    queryFn: async () => {
      const { data } = await apiClient.get<Subscription[]>("/api/subscriptions", { params });
      return data;
    },
  });
}

export function useCreateSubscription() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: SubscriptionCreateInput) => {
      const { data } = await apiClient.post<Subscription>("/api/subscriptions", input);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useUpdateSubscription(subscriptionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: SubscriptionUpdateInput) => {
      const { data } = await apiClient.patch<Subscription>(`/api/subscriptions/${subscriptionId}`, input);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useDeleteSubscription() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (subscriptionId: string) => {
      await apiClient.delete(`/api/subscriptions/${subscriptionId}`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}
