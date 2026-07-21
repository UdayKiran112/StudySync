import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import toast from "react-hot-toast";
import { CalendarPlus } from "lucide-react";
import { Button } from "../../components/ui/Button";
import { Field, Input } from "../../components/ui/Form";
import { Modal } from "../../components/ui/Modal";
import { EmptyState, PageHeader, Spinner } from "../../components/ui/Feedback";
import { formatDate, todayIso } from "../../lib/format";
import { useOtherActivities, useCreateOtherActivity } from "../../api/other-activities";

export function OtherActivitiesPage() {
  const { data: activities, isLoading } = useOtherActivities();
  const [modal, setModal] = useState(false);

  return (
    <div>
      <PageHeader
        eyebrow="Speakers & Faculty"
        title="Other activities"
        description="Create sessions with external speakers or faculty, and manage attendance."
        action={
          <Button onClick={() => setModal(true)}>
            <CalendarPlus size={16} /> Add New Session
          </Button>
        }
      />
      {isLoading && <Spinner label="Loading activities…" />}
      {!isLoading && !activities?.length && (
        <EmptyState title="No activities" description="Create a session to begin tracking attendance." />
      )}
      {!!activities?.length && <ActivitiesList activities={activities} />}
      <SessionForm open={modal} onClose={() => setModal(false)} />
    </div>
  );
}

function ActivitiesList({ activities }: { activities: import("../../api/types").OtherActivity[] }) {
  const navigate = useNavigate();

  return (
    <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
      {activities.map((activity) => (
        <button
          key={activity.activity_id}
          onClick={() => navigate(`/other-activities/${activity.activity_id}`)}
          className="rounded-lg border border-border bg-card p-4 text-left hover:border-slate hover:shadow-md transition-all"
        >
          <p className="font-semibold text-base">{activity.session_name}</p>
          <p className="mt-1 text-xs text-slate">Speaker: {activity.speaker_name}</p>
          <p className="mt-2 text-sm text-slate">{formatDate(activity.session_date)}</p>
          <p className="mt-1 text-sm text-slate">Type: {activity.session_type}</p>
        </button>
      ))}
    </div>
  );
}

function SessionForm({ open, onClose }: { open: boolean; onClose: () => void }) {
  const create = useCreateOtherActivity();
  const [sessionName, setSessionName] = useState("");
  const [speakerName, setSpeakerName] = useState("");
  const [date, setDate] = useState(todayIso());
  const [sessionType, setSessionType] = useState("");
  const reset = () => { setSessionName(""); setSpeakerName(""); setDate(todayIso()); setSessionType(""); };
  const handleClose = () => { reset(); onClose(); };
  useEffect(() => { if (!open) reset(); }, [open]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await create.mutateAsync({
        session_name: sessionName,
        speaker_name: speakerName,
        session_date: date,
        session_type: sessionType,
        notes: null,
      });
      toast.success("Session created");
      handleClose();
    } catch {
      toast.error("Could not create session");
    }
  };

  return (
    <Modal open={open} onClose={handleClose} title="Add New Session">
      <form onSubmit={submit} className="space-y-4">
        <Field label="Session name" required>
          <Input required value={sessionName} onChange={(e) => setSessionName(e.target.value)} />
        </Field>
        <Field label="Speaker name" required>
          <Input required value={speakerName} onChange={(e) => setSpeakerName(e.target.value)} />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Date" required>
            <Input type="date" required value={date} onChange={(e) => setDate(e.target.value)} />
          </Field>
          <Field label="Session type" required>
            <Input required value={sessionType} onChange={(e) => setSessionType(e.target.value)} placeholder="e.g., Seminar, Workshop, Guest Lecture" />
          </Field>
        </div>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={handleClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={create.isPending || !sessionName || !speakerName || !sessionType}>
            {create.isPending ? "Creating..." : "Create Session"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
