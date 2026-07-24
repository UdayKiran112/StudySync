import { useState } from "react";
import toast from "react-hot-toast";
import { Modal } from "../../../components/ui/Modal";
import { Field, Input, Select, Textarea } from "../../../components/ui/Form";
import { Button } from "../../../components/ui/Button";
import { useCreateCoachingClass, useInstructors } from "../../../api/coaching";
import { extractErrorMessage } from "../../../api/client";
import { todayIso } from "../../../lib/format";

export function SessionFormModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (classId: number) => void;
}) {
  const [title, setTitle] = useState("");
  const [classDate, setClassDate] = useState(todayIso());
  const [startTime, setStartTime] = useState("");
  const [endTime, setEndTime] = useState("");
  const [subject, setSubject] = useState("");
  const [instructorId, setInstructorId] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState("");

  const { data: instructors } = useInstructors();
  const createMutation = useCreateCoachingClass();

  function reset() {
    setTitle("");
    setClassDate(todayIso());
    setStartTime("");
    setEndTime("");
    setSubject("");
    setInstructorId("");
    setNotes("");
    setError("");
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!title.trim()) {
      setError("Session title is required.");
      return;
    }
    if (!classDate) {
      setError("Date is required.");
      return;
    }
    if (startTime && endTime && endTime <= startTime) {
      setError("End time must be later than start time.");
      return;
    }
    try {
      const created = await createMutation.mutateAsync({
        title,
        class_date: classDate,
        start_time: startTime || null,
        end_time: endTime || null,
        subject: subject || null,
        instructor_id: instructorId ? Number(instructorId) : null,
        notes: notes || null,
      });
      toast.success(`Created "${title}"`);
      reset();
      onClose();
      onCreated(created.class_id);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="New coaching session" width="md">
      <form onSubmit={handleSubmit} className="space-y-4">
        <Field label="Title" required>
          <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Weekend Physics Batch" />
        </Field>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Date" required>
            <Input type="date" value={classDate} onChange={(e) => setClassDate(e.target.value)} />
          </Field>
          <Field label="Subject">
            <Input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="e.g. Physics" />
          </Field>
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Start time">
            <Input type="time" value={startTime} onChange={(e) => setStartTime(e.target.value)} />
          </Field>
          <Field label="End time">
            <Input type="time" value={endTime} onChange={(e) => setEndTime(e.target.value)} />
          </Field>
        </div>

        <Field label="Instructor">
          <Select value={instructorId} onChange={(e) => setInstructorId(e.target.value)}>
            <option value="">No instructor assigned</option>
            {instructors?.map((i) => (
              <option key={i.instructor_id} value={i.instructor_id}>
                {i.name}
                {i.specialization ? ` — ${i.specialization}` : ""}
              </option>
            ))}
          </Select>
        </Field>

        <Field label="Notes">
          <Textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Logistics, venue, anything worth flagging" />
        </Field>

        {error && <p className="text-sm text-rust">{error}</p>}

        <div className="flex justify-end gap-2 border-t border-border pt-4">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={createMutation.isPending}>
            {createMutation.isPending ? "Creating…" : "Create session"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
