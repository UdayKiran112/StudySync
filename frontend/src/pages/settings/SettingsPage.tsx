import { useState } from "react";
import { CheckCircle2, XCircle } from "lucide-react";
import { useSettings } from "../../context/SettingsContext";
import { PageHeader } from "../../components/ui/Feedback";
import { Field, Input } from "../../components/ui/Form";
import { Button } from "../../components/ui/Button";
import { apiClient, extractErrorMessage } from "../../api/client";
import toast from "react-hot-toast";

export function SettingsPage() {
  const { baseUrl, apiKey, setBaseUrl, setApiKey } = useSettings();
  const [localBaseUrl, setLocalBaseUrl] = useState(baseUrl);
  const [localApiKey, setLocalApiKey] = useState(apiKey);
  const [testState, setTestState] = useState<"idle" | "testing" | "ok" | "fail">("idle");
  const [testMessage, setTestMessage] = useState("");

  function handleSave(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBaseUrl(localBaseUrl.trim());
    setApiKey(localApiKey.trim());
    toast.success("Settings saved");
  }

  async function handleTest() {
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

  return (
    <div>
      <PageHeader
        eyebrow="Configuration"
        title="Settings"
        description="Point StudySync at your backend and provide the staff API key used for every request."
      />

      <form onSubmit={handleSave} className="max-w-lg space-y-5 rounded-lg border border-border bg-card p-6">
        <Field label="API base URL" required hint="The address where the FastAPI backend is running, e.g. http://localhost:8000 or your LAN IP.">
          <Input
            value={localBaseUrl}
            onChange={(e) => setLocalBaseUrl(e.target.value)}
            placeholder="http://localhost:8000"
          />
        </Field>

        <Field label="Staff API key" required hint="Sent as the X-API-Key header on every request, matching STUDYSYNC_API_KEY on the server.">
          <Input
            type="password"
            value={localApiKey}
            onChange={(e) => setLocalApiKey(e.target.value)}
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

      <p className="mt-4 max-w-lg text-xs text-slate">
        These values are stored only in this browser's local storage — they are sent directly from your
        browser to the backend on every request.
      </p>
    </div>
  );
}
