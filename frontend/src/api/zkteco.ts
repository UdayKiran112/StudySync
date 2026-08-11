import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import type { ZkSyncResult } from "./types";

/**
 * Pull swipes from the ZKTeco device and apply each as a check-in or
 * check-out. First swipe of a day opens an attendance row, the next closes
 * it. The device buffer is never cleared — StudySync only reads it.
 */
export function useZkSync() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { data } = await apiClient.post<ZkSyncResult>(
        "/api/zkteco/attendance/sync",
      );
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["zkteco"] });
      qc.invalidateQueries({ queryKey: ["attendance"] });
    },
  });
}
