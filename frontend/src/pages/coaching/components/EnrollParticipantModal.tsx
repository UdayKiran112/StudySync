import { useEffect, useRef, useState } from "react";
import toast from "react-hot-toast";
import { Search, X } from "lucide-react";
import { Modal } from "../../../components/ui/Modal";
import { Field } from "../../../components/ui/Form";
import { Button } from "../../../components/ui/Button";
import { StudentPicker } from "../../../components/ui/StudentPicker";
import { useAddCoachingEnrollment, useExternalParticipants } from "../../../api/coaching";
import { extractErrorMessage } from "../../../api/client";
import { useDebouncedValue } from "../../../lib/useDebouncedValue";
import type { Student, ExternalParticipant, CoachingParticipantType } from "../../../api/types";

export function EnrollParticipantModal({
  open,
  onClose,
  classId,
}: {
  open: boolean;
  onClose: () => void;
  classId: number;
}) {
  const [type, setType] = useState<CoachingParticipantType>("Library Student");
  const [student, setStudent] = useState<Student | null>(null);
  const [external, setExternal] = useState<ExternalParticipant | null>(null);
  const [error, setError] = useState("");

  const addMutation = useAddCoachingEnrollment(classId);

  function reset() {
    setStudent(null);
    setExternal(null);
    setError("");
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (type === "Library Student" && !student) {
      setError("Pick a student.");
      return;
    }
    if (type === "External Student" && !external) {
      setError("Pick an external participant.");
      return;
    }
    try {
      await addMutation.mutateAsync({
        participant_type: type,
        student_id: type === "Library Student" ? student!.student_id : null,
        external_participant_id: type === "External Student" ? external!.external_participant_id : null,
      });
      toast.success(`Enrolled ${type === "Library Student" ? student!.name : external!.name}`);
      reset();
      onClose();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Enroll participant" width="sm">
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="flex gap-1 rounded-md bg-paper-dim p-1 w-fit">
          <button
            type="button"
            onClick={() => {
              setType("Library Student");
              setError("");
            }}
            className={`rounded px-3 py-1.5 text-sm font-medium transition-colors ${
              type === "Library Student" ? "bg-card text-ink shadow-sm" : "text-slate"
            }`}
          >
            Library student
          </button>
          <button
            type="button"
            onClick={() => {
              setType("External Student");
              setError("");
            }}
            className={`rounded px-3 py-1.5 text-sm font-medium transition-colors ${
              type === "External Student" ? "bg-card text-ink shadow-sm" : "text-slate"
            }`}
          >
            External student
          </button>
        </div>

        {type === "Library Student" ? (
          <Field label="Student" required>
            <StudentPicker value={student} onChange={setStudent} activeOnly={false} />
          </Field>
        ) : (
          <Field label="External participant" required>
            <ExternalParticipantPicker value={external} onChange={setExternal} />
          </Field>
        )}

        {error && <p className="text-sm text-rust">{error}</p>}

        <div className="flex justify-end gap-2 border-t border-border pt-4">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={addMutation.isPending}>
            {addMutation.isPending ? "Enrolling…" : "Enroll"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

/** Search-and-pick combobox for external participants, mirroring StudentPicker's UX. */
function ExternalParticipantPicker({
  value,
  onChange,
}: {
  value: ExternalParticipant | null;
  onChange: (participant: ExternalParticipant | null) => void;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const debounced = useDebouncedValue(query, 250);
  const containerRef = useRef<HTMLDivElement>(null);

  const { data } = useExternalParticipants(debounced || undefined);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  if (value) {
    return (
      <div className="flex items-center gap-2 rounded-md border border-border bg-paper-dim px-3 py-2">
        <span className="flex-1 text-sm">
          <span className="font-medium text-ink">{value.name}</span>
          {value.village && <span className="text-slate"> — {value.village}</span>}
        </span>
        <button
          type="button"
          onClick={() => onChange(null)}
          className="rounded p-1 text-slate hover:bg-white hover:text-ink"
          aria-label="Clear selected participant"
        >
          <X size={14} />
        </button>
      </div>
    );
  }

  return (
    <div className="relative" ref={containerRef}>
      <div className="relative">
        <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-light" />
        <input
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          placeholder="Search by name, phone, or village…"
          className="w-full rounded-md border border-border bg-card py-2 pl-9 pr-3 text-sm text-ink placeholder:text-slate-light focus:border-brass focus:outline-none"
        />
      </div>
      {open && data && data.length > 0 && (
        <div className="absolute z-10 mt-1 w-full overflow-hidden rounded-md border border-border bg-card shadow-lg">
          {data.map((p) => (
            <button
              type="button"
              key={p.external_participant_id}
              onClick={() => {
                onChange(p);
                setQuery("");
                setOpen(false);
              }}
              className="flex w-full flex-col px-3 py-2 text-left text-sm hover:bg-paper-dim"
            >
              <span className="font-medium text-ink">{p.name}</span>
              <span className="text-xs text-slate">{[p.village, p.phone].filter(Boolean).join(" · ") || "No contact details"}</span>
            </button>
          ))}
        </div>
      )}
      {open && query && data && data.length === 0 && (
        <div className="absolute z-10 mt-1 w-full rounded-md border border-border bg-card px-3 py-2 text-sm text-slate shadow-lg">
          No matching external participants
        </div>
      )}
    </div>
  );
}
