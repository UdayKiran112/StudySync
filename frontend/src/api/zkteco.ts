import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import type {
  ZkDeviceConfigStatus,
  ZkDiscoveryResult,
  ZkSyncResult,
} from "./types";

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

const deviceKey = ["zkteco", "device"] as const;

/** What device StudySync is configured to talk to, and where it came from. */
export function useZkDeviceStatus() {
  return useQuery({
    queryKey: deviceKey,
    queryFn: async () => {
      const { data } = await apiClient.get<ZkDeviceConfigStatus>(
        "/api/zkteco/device",
      );
      return data;
    },
  });
}

/**
 * Scan the LAN for ZKTeco devices. Confirmed hits (ZK handshake + serial
 * read) come first so the operator can pick one. Never changes the
 * configured device.
 */
export function useZkDiscover() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (subnet?: string) => {
      const { data } = await apiClient.get<ZkDiscoveryResult>(
        "/api/zkteco/discover",
        { params: subnet ? { subnet } : undefined },
      );
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: deviceKey });
    },
  });
}

/** Point StudySync at a device (the Settings "use this IP" action). */
export function useZkSetDevice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (ip: string) => {
      const { data } = await apiClient.post<ZkDeviceConfigStatus>(
        "/api/zkteco/device",
        { ip },
      );
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: deviceKey });
    },
  });
}

/** Forget the picked device and fall back to the .env value / none. */
export function useZkClearDevice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { data } = await apiClient.delete<ZkDeviceConfigStatus>(
        "/api/zkteco/device",
      );
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: deviceKey });
    },
  });
}
