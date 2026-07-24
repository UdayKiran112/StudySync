import { useState } from "react";
import toast from "react-hot-toast";
import { Modal } from "../../../components/ui/Modal";
import { Field, Input, Textarea } from "../../../components/ui/Form";
import { Button } from "../../../components/ui/Button";
import { useCreateInstructor } from "../../../api/coaching";
import { extractErrorMessage } from "../../../api/client";

export function InstructorFormModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [specialization, setSpecialization] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState("");

  const createMutation = useCreateInstructor();

  function reset() {
    setName("");
    setPhone("");
    setSpecialization("");
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
    try {
      await createMutation.mutateAsync({
        name,
        phone: phone || null,
        specialization: specialization || null,
        notes: notes || null,
      });
      toast.success(`Added ${name} as an instructor`);
      reset();
      onClose();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Add instructor" width="sm">
      <form onSubmit={handleSubmit} className="space-y-4">
        <Field label="Name" required>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Full name" />
        </Field>
        <Field label="Phone">
          <Input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="Contact number" />
        </Field>
        <Field label="Specialization">
          <Input value={specialization} onChange={(e) => setSpecialization(e.target.value)} placeholder="e.g. Mathematics" />
        </Field>
        <Field label="Notes">
          <Textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
        </Field>
        {error && <p className="text-sm text-rust">{error}</p>}
        <div className="flex justify-end gap-2 border-t border-border pt-4">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={createMutation.isPending}>
            {createMutation.isPending ? "Saving…" : "Add instructor"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
