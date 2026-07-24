import { useState } from "react";
import toast from "react-hot-toast";
import { Modal } from "../../../components/ui/Modal";
import { Field, Input, Select, Textarea } from "../../../components/ui/Form";
import { Button } from "../../../components/ui/Button";
import { useCreateExternalParticipant } from "../../../api/coaching";
import { extractErrorMessage } from "../../../api/client";

export function ExternalParticipantFormModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [name, setName] = useState("");
  const [village, setVillage] = useState("");
  const [phone, setPhone] = useState("");
  const [gender, setGender] = useState("");
  const [guardianName, setGuardianName] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState("");

  const createMutation = useCreateExternalParticipant();

  function reset() {
    setName("");
    setVillage("");
    setPhone("");
    setGender("");
    setGuardianName("");
    setNotes("");
    setError("");
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!name.trim()) {
      setError("Name is required.");
      return;
    }
    if (!village.trim()) {
      setError("Village is required.");
      return;
    }
    try {
      await createMutation.mutateAsync({
        name,
        village,
        phone: phone || null,
        gender: (gender || null) as "Male" | "Female" | "Other" | null,
        guardian_name: guardianName || null,
        notes: notes || null,
      });
      toast.success(`Added ${name}`);
      reset();
      onClose();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Add external student" width="sm">
      <form onSubmit={handleSubmit} className="space-y-4">
        <Field label="Name" required>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Full name" />
        </Field>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Village" required>
            <Input value={village} onChange={(e) => setVillage(e.target.value)} />
          </Field>
          <Field label="Phone">
            <Input value={phone} onChange={(e) => setPhone(e.target.value)} />
          </Field>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Gender">
            <Select value={gender} onChange={(e) => setGender(e.target.value)}>
              <option value="">Not specified</option>
              <option value="Male">Male</option>
              <option value="Female">Female</option>
              <option value="Other">Other</option>
            </Select>
          </Field>
          <Field label="Guardian name">
            <Input value={guardianName} onChange={(e) => setGuardianName(e.target.value)} />
          </Field>
        </div>
        <Field label="Notes">
          <Textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
        </Field>
        {error && <p className="text-sm text-rust">{error}</p>}
        <div className="flex justify-end gap-2 border-t border-border pt-4">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={createMutation.isPending}>
            {createMutation.isPending ? "Saving…" : "Add student"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
