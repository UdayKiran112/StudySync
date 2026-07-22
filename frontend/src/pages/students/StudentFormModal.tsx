import { useState } from "react";
import toast from "react-hot-toast";
import { Modal } from "../../components/ui/Modal";
import { Field, Input, Select } from "../../components/ui/Form";
import { Button } from "../../components/ui/Button";
import { extractErrorMessage } from "../../api/client";
import { useCreateStudent, useUpdateStudent } from "../../api/students";
import type { Student } from "../../api/types";
import { todayIso } from "../../lib/format";

export function StudentFormModal({
  open,
  onClose,
  student,
}: {
  open: boolean;
  onClose: () => void;
  student?: Student;
}) {
  const isEdit = Boolean(student);
  const [studentId, setStudentId] = useState(
    student ? String(student.student_id) : "",
  );
  const [name, setName] = useState(student?.name ?? "");
  const [gender, setGender] = useState<string>(student?.gender ?? "");
  const [dob, setDob] = useState(student?.date_of_birth ?? "");
  const [phone, setPhone] = useState(student?.phone ?? "");
  const [email, setEmail] = useState(student?.email ?? "");
  const [address, setAddress] = useState(student?.address ?? "");
  const [joinDate, setJoinDate] = useState(student?.join_date ?? todayIso());
  const [error, setError] = useState("");

  const createMutation = useCreateStudent();
  const updateMutation = useUpdateStudent(student?.student_id ?? -1);
  const pending = createMutation.isPending || updateMutation.isPending;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");

    if (!isEdit && !studentId.trim()) {
      setError("Student ID is required.");
      return;
    }
    if (!name.trim()) {
      setError("Name is required.");
      return;
    }
    if (!isEdit && !joinDate) {
      setError("Join date is required.");
      return;
    }

    try {
      if (isEdit && student) {
        await updateMutation.mutateAsync({
          name,
          gender: (gender || null) as Student["gender"],
          date_of_birth: dob || null,
          phone: phone || null,
          email: email || null,
          address: address || null,
        });
        toast.success(`Saved changes for ${name}`);
      } else {
        await createMutation.mutateAsync({
          student_id: Number(studentId),
          name,
          gender: (gender || null) as Student["gender"],
          date_of_birth: dob || null,
          phone: phone || null,
          email: email || null,
          address: address || null,
          join_date: joinDate,
          status: "Active",
        });
        toast.success(`Added ${name} to StudySync`);
      }
      onClose();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={isEdit ? "Edit student" : "Add student"}
      subtitle={
        isEdit
          ? `Student ${student?.student_id}`
          : "Create a new student record"
      }
      width="lg"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Student ID" required>
            <Input
              value={studentId}
              onChange={(e) => setStudentId(e.target.value)}
              type="number"
              disabled={isEdit}
              placeholder="e.g. 4351"
            />
          </Field>
          <div className="rounded-md border border-border bg-paper-dim px-3 py-2 text-sm text-slate">
            Membership is active for one year from joining or renewal.
          </div>
        </div>

        <Field label="Full name" required>
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Student's full name"
          />
        </Field>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Gender">
            <Select
              value={gender ?? ""}
              onChange={(e) => setGender(e.target.value)}
            >
              <option value="">Not specified</option>
              <option value="Male">Male</option>
              <option value="Female">Female</option>
              <option value="Other">Other</option>
            </Select>
          </Field>
          <Field label="Date of birth">
            <Input
              type="date"
              value={dob ?? ""}
              onChange={(e) => setDob(e.target.value)}
            />
          </Field>
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Phone">
            <Input
              value={phone ?? ""}
              onChange={(e) => setPhone(e.target.value)}
              placeholder="Contact number"
            />
          </Field>
          <Field label="Email">
            <Input
              type="email"
              value={email ?? ""}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="Email address"
            />
          </Field>
        </div>

        <Field label="Address">
          <Input
            value={address ?? ""}
            onChange={(e) => setAddress(e.target.value)}
            placeholder="Home address"
          />
        </Field>

        {!isEdit && (
          <Field label="Join date" required>
            <Input
              type="date"
              value={joinDate}
              onChange={(e) => setJoinDate(e.target.value)}
            />
          </Field>
        )}

        {error && <p className="text-sm text-rust">{error}</p>}

        <div className="flex justify-end gap-2 border-t border-border pt-4">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={pending}>
            {pending ? "Saving…" : isEdit ? "Save changes" : "Add student"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
