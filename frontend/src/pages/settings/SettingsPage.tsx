import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, XCircle, Trash2, Radar } from "lucide-react";
import { useSettings } from "../../context/SettingsContext";
import { PageHeader, Spinner } from "../../components/ui/Feedback";
import { Field, Input } from "../../components/ui/Form";
import { Button } from "../../components/ui/Button";
import { apiClient, extractErrorMessage } from "../../api/client";
import {
  useZkClearDevice,
  useZkDeviceStatus,
  useZkDiscover,
  useZkSetDevice,
} from "../../api/zkteco";
import toast from "react-hot-toast";

function ZktecoDeviceCard() {
  const qc = useQueryClient();
  const {
    data: status,
    isLoading,
    isError,
    error,
    refetch,
  } = useZkDeviceStatus();
  const discover = useZkDiscover();
  const setDevice = useZkSetDevice();
  const clearDevice = useZkClearDevice();
  const [manualIp, setManualIp] = useState("");

  const currentIp = status?.ip;
  const busy =
    discover.isPending || setDevice.isPending || clearDevice.isPending;

  function handleUse(ip: string) {
    setDevice.mutate(ip, {
      onSuccess: () => toast.success(`StudySync is now set to device ${ip}.`),
      onError: (err) => toast.error(extractErrorMessage(err)),
    });
  }

  function handleManualUse(e: React.FormEvent) {
    e.preventDefault();
    const ip = manualIp.trim();
    if (ip) handleUse(ip);
  }

  return (
    <section className="mt-4 max-w-lg rounded-lg border border-border bg-card p-6">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <h2 className="font-display text-sm font-semibold text-ink">
            ZKTeco attendance device
          </h2>
          <p className="text-xs text-slate">
            Which fingerprint device StudySync reads swipes from.
          </p>
        </div>
        <Button size="sm" variant="ghost" onClick={() => refetch()}>
          Refresh
        </Button>
      </div>

      {isLoading && <Spinner label="Loading device status…" />}
      {isError && (
        <p className="text-sm text-rust">
          Couldn't check the device config: {extractErrorMessage(error)}. Save
          the staff API key above and refresh.
        </p>
      )}

      {status && (
        <>
          {status.configured ? (
            <div className="flex items-start gap-2 rounded-md border border-forest/30 bg-forest/5 px-3 py-2 text-sm text-ink">
              <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-forest" />
              <div>
                <p className="font-medium">
                  Set to device at {status.ip}
                  {status.discovered_serial && (
                    <span className="text-slate">
                      {" "}
                      (serial {status.discovered_serial})
                    </span>
                  )}
                </p>
                <p className="mt-0.5 text-xs text-slate">
                  {status.source === "discovered"
                    ? "Selected via discovery — persisted in the database, so it survives restarts and takes precedence over the .env value."
                    : `From the server's .env (ZK_DEVICE_IP). Scan to take over, or leave as-is.`}
                </p>
              </div>
            </div>
          ) : (
            <p className="text-sm text-slate">
              No device is configured. Scan the network below to find your
              fingerprint machine.
            </p>
          )}

          {status.source === "discovered" && (
            <div className="mt-3 flex items-center gap-2">
              <Button
                variant="danger"
                size="sm"
                disabled={busy}
                onClick={() =>
                  clearDevice.mutate(undefined, {
                    onSuccess: () =>
                      toast.success("Reverted to the .env device."),
                    onError: (err) => toast.error(extractErrorMessage(err)),
                  })
                }
              >
                <Trash2 size={14} /> Forget device
              </Button>
              <span className="text-xs text-slate">
                Reverts to ZK_DEVICE_IP from the server's .env.
              </span>
            </div>
          )}

          <div className="mt-4 border-t border-border pt-4">
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={busy}
                onClick={() =>
                  discover.mutate(undefined, {
                    onError: (err) => toast.error(extractErrorMessage(err)),
                  })
                }
              >
                <Radar size={14} />
                {discover.isPending ? "Scanning…" : "Scan network for device"}
              </Button>
              {discover.data && !discover.isPending && (
                <span className="text-xs text-slate">
                  {discover.data.scanned_hosts} host(s) checked in{" "}
                  {Math.max(1, Math.round(discover.data.elapsed_ms / 1000))}s
                  {discover.data.scanned_subnets[0] && (
                    <> · {discover.data.scanned_subnets.join(", ")}</>
                  )}
                </span>
              )}
            </div>

            {discover.data &&
              !discover.isPending &&
              discover.data.devices.length === 0 && (
                <p className="mt-2 text-xs text-slate">
                  No ZKTeco devices answered. They must be reachable from this
                  machine. You can also type the device's IP below.
                </p>
              )}

            {discover.data &&
              !discover.isPending &&
              discover.data.devices.length > 0 && (
                <ul className="mt-3 space-y-2">
                  {discover.data.devices.map((device) => {
                    const isCurrent = device.ip === currentIp;
                    return (
                      <li
                        key={device.ip}
                        className="flex items-center justify-between gap-3 rounded-md border border-border bg-paper-dim px-3 py-2"
                      >
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-mono text-sm text-ink">
                              {device.ip}
                            </span>
                            {device.confirmed ? (
                              <span className="rounded-full bg-forest/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-forest">
                                Confirmed
                              </span>
                            ) : (
                              <span className="rounded-full bg-brass/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-brass">
                                Unconfirmed
                              </span>
                            )}
                            {isCurrent && (
                              <span className="text-[10px] font-medium uppercase tracking-wide text-slate">
                                In use
                              </span>
                            )}
                          </div>
                          <p className="truncate text-xs text-slate">
                            {device.device_name || "ZKTeco device"}
                            {device.serial ? ` · ${device.serial}` : ""}
                          </p>
                        </div>
                        <Button
                          size="sm"
                          variant="primary"
                          disabled={busy || isCurrent}
                          onClick={() => handleUse(device.ip)}
                        >
                          Use this IP
                        </Button>
                      </li>
                    );
                  })}
                </ul>
              )}

            <form onSubmit={handleManualUse} className="mt-3 flex items-end gap-2">
              <div className="flex-1">
                <Field
                  label="Or type the device IP"
                  hint="Useful when a scan can't reach it (different subnet, firewall)."
                >
                  <Input
                    value={manualIp}
                    onChange={(e) => {
                      setManualIp(e.target.value);
                      qc.invalidateQueries({ queryKey: ["zkteco", "device"] });
                    }}
                    placeholder="192.168.1.100"
                  />
                </Field>
              </div>
              <Button
                type="submit"
                variant="secondary"
                disabled={busy || !manualIp.trim()}
              >
                Use IP
              </Button>
            </form>
          </div>

          {status.last_scan_at && (
            <p className="mt-3 text-[11px] text-slate-light">
              Last scan: {status.last_scan_at}
            </p>
          )}
        </>
      )}
    </section>
  );
}

export function SettingsPage() {
  const { baseUrl, apiKey, setBaseUrl, setApiKey, clearApiKey } = useSettings();
  const qc = useQueryClient();
  const [localBaseUrl, setLocalBaseUrl] = useState(baseUrl);
  const [localApiKey, setLocalApiKey] = useState(apiKey);
  const [testState, setTestState] = useState<"idle" | "testing" | "ok" | "fail">("idle");
  const [testMessage, setTestMessage] = useState("");
  const [urlError, setUrlError] = useState("");

  function validateUrl(value: string): boolean {
    const trimmed = value.trim();
    if (!trimmed) {
      setUrlError("URL is required");
      return false;
    }
    try {
      const parsed = new URL(trimmed);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        setUrlError("Only http and https URLs are allowed");
        return false;
      }
      setUrlError("");
      return true;
    } catch {
      setUrlError("Enter a valid URL (e.g. http://localhost:8000)");
      return false;
    }
  }

  function handleSave(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!validateUrl(localBaseUrl)) return;
    setBaseUrl(localBaseUrl.trim());
    setApiKey(localApiKey.trim());
    qc.invalidateQueries({ queryKey: ["zkteco", "device"] });
    toast.success("Settings saved");
  }

  async function handleTest() {
    if (!validateUrl(localBaseUrl)) return;
    setBaseUrl(localBaseUrl.trim());
    setApiKey(localApiKey.trim());
    setTestState("testing");
    try {
      await apiClient.get("/api/students", { params: { limit: 1 } });
      setTestState("ok");
      setTestMessage("Connected — the staff key was accepted.");
    } catch (err) {
      setTestState("fail");
      setTestMessage(extractErrorMessage(err));
    }
  }

  function handleRemoveKey() {
    if (!window.confirm("Remove the stored staff API key from this browser? You will need to re-enter it to use StudySync.")) {
      return;
    }
    clearApiKey();
    setLocalApiKey("");
    setTestState("idle");
    setTestMessage("");
    toast.success("Stored API key removed from this browser.");
  }

  return (
    <div>
      <PageHeader
        eyebrow="Configuration"
        title="Settings"
        description="Point StudySync at your backend, provide the staff API key used for every request, and wire up the attendance device."
      />

      <form onSubmit={handleSave} className="max-w-lg space-y-5 rounded-lg border border-border bg-card p-6">
        <Field label="API base URL" required error={urlError || undefined} hint="The address where the FastAPI backend is running, e.g. http://localhost:8000 or your LAN IP.">
          <Input
            value={localBaseUrl}
            onChange={(e) => {
              setLocalBaseUrl(e.target.value);
              setUrlError("");
              setTestState("idle");
            }}
            placeholder="http://localhost:8000"
          />
        </Field>

        <Field label="Staff API key" required hint="Sent as the X-API-Key header on every request, matching STUDYSYNC_API_KEY on the server.">
          <Input
            type="password"
            value={localApiKey}
            onChange={(e) => {
              setLocalApiKey(e.target.value);
              setTestState("idle");
            }}
            placeholder="Paste the staff key"
          />
        </Field>

        <div className="flex items-center gap-3 pt-2">
          <Button type="submit" variant="primary">
            Save settings
          </Button>
          <Button type="button" variant="secondary" onClick={handleTest} disabled={testState === "testing"}>
            {testState === "testing" ? "Testing…" : "Test connection"}
          </Button>
        </div>

        {testState === "ok" && (
          <p className="flex items-center gap-2 text-sm text-forest">
            <CheckCircle2 size={16} /> {testMessage}
          </p>
        )}
        {testState === "fail" && (
          <p className="flex items-center gap-2 text-sm text-rust">
            <XCircle size={16} /> {testMessage}
          </p>
        )}
      </form>

      <ZktecoDeviceCard />

      <div className="mt-4 max-w-lg space-y-2">
        <div className="flex items-center gap-2 rounded-lg border border-rust/40 bg-rust/5 p-4">
          <Trash2 size={16} className="shrink-0 text-rust" />
          <div className="flex-1">
            <p className="text-sm font-medium text-ink">Remove the stored staff key</p>
            <p className="text-xs text-slate">
              Deletes the API key from this browser. Anything that reads a saved key
              stops working until you paste a valid key again.
            </p>
          </div>
          <Button type="button" variant="danger" onClick={handleRemoveKey}>
            Remove key
          </Button>
        </div>
        <p className="text-xs text-slate">
          These values are stored only in this browser's local storage — they are sent directly from your
          browser to the backend on every request. In production StudySync is served and proxied from the
          same origin, so the base URL is normally left empty.
        </p>
      </div>
    </div>
  );
}
