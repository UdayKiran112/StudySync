import { useState } from "react";
import { UserPlus, Plus } from "lucide-react";
import { PageHeader, EmptyState } from "../../components/ui/Feedback";
import { Button } from "../../components/ui/Button";
import { SessionList } from "./components/SessionList";
import { SessionWorkspace } from "./components/SessionWorkspace";
import { SessionFormModal } from "./components/SessionFormModal";
import { InstructorFormModal } from "./components/InstructorFormModal";
import { ExternalParticipantFormModal } from "./components/ExternalParticipantFormModal";

export function CoachingClassesPage() {
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [sessionFormOpen, setSessionFormOpen] = useState(false);
  const [instructorFormOpen, setInstructorFormOpen] = useState(false);
  const [externalFormOpen, setExternalFormOpen] = useState(false);

  return (
    <div>
      <PageHeader
        eyebrow="Coaching"
        title="Coaching classes"
        description="Schedule sessions, assign an instructor, and manage who's enrolled — library students and outside participants alike."
        action={
          <div className="flex flex-wrap gap-2">
            <Button
              variant="secondary"
              onClick={() => setInstructorFormOpen(true)}
            >
              <UserPlus size={15} /> Add instructor
            </Button>
            <Button
              variant="secondary"
              onClick={() => setExternalFormOpen(true)}
            >
              <UserPlus size={15} /> Add external student
            </Button>
            <Button variant="primary" onClick={() => setSessionFormOpen(true)}>
              <Plus size={15} /> New session
            </Button>
          </div>
        }
      />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[340px_1fr] lg:items-start">
        <SessionList selectedId={selectedId} onSelect={setSelectedId} />

        {selectedId ? (
          <SessionWorkspace
            classId={selectedId}
            onDeleted={() => setSelectedId(null)}
          />
        ) : (
          <EmptyState
            title="Select a session"
            description="Pick a session on the left, or create a new one, to see its roster."
          />
        )}
      </div>

      <SessionFormModal
        open={sessionFormOpen}
        onClose={() => setSessionFormOpen(false)}
        onCreated={(classId) => setSelectedId(classId)}
      />
      <InstructorFormModal
        open={instructorFormOpen}
        onClose={() => setInstructorFormOpen(false)}
      />
      <ExternalParticipantFormModal
        open={externalFormOpen}
        onClose={() => setExternalFormOpen(false)}
      />
    </div>
  );
}
