import { useState } from "react";
import toast from "react-hot-toast";
import { Modal } from "./ui/Modal";
import { Button } from "./ui/Button";
import { useCreateOfflineUsage } from "../api/offlineLibrary";
import { todayIso } from "../lib/format";
import { extractErrorMessage } from "../api/client";
import type { RenewalEvent } from "../api/realtime";

/**
 * Shown when a check-in auto-renews a lapsed membership. The membership
 * data is already renewed in the backend; this just asks the desk whether
 * the student's visit should also be logged in the offline library
 * (own material, book_id NULL).
 */
export function RenewalDialog({
  renewal,
  onClose,
}: {
  renewal: RenewalEvent;
  onClose: () => void;
}) {
  const createUsage = useCreateOfflineUsage();
  const [pending, setPending] = useState(false);

  const studentName = renewal.name ?? `Student #${renewal.student_id}`;

  async function handleLogVisit() {
    setPending(true);
    try {
      await createUsage.mutateAsync({
        student_id: renewal.student_id,
        book_id: null,
        date: todayIso(),
      });
      toast.success("Offline library visit logged");
      onClose();
    } catch (err) {
      toast.error(extractErrorMessage(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Membership renewed"
      subtitle="Renewal was applied automatically"
      width="sm"
    >
      <p className="text-sm text-slate">
        {studentName}&rsquo;s membership was auto-renewed. Log today&rsquo;s
        offline library visit?
      </p>
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Dismiss
        </Button>
        <Button
          variant="primary"
          onClick={handleLogVisit}
          disabled={pending}
        >
          {pending ? "Logging…" : "Log visit"}
        </Button>
      </div>
    </Modal>
  );
}
