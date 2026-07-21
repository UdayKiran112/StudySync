import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import toast from "react-hot-toast";
import { ArrowLeft, Download, UserPlus, Trash2, BarChart3 } from "lucide-react";
import { Spinner, ErrorBanner, EmptyState, PageHeader } from "../../components/ui/Feedback";
import { Table, Thead, Th, Tr, Td } from "../../components/ui/Table";
import { Button } from "../../components/ui/Button";
import { Field, Select } from "../../components/ui/Form";
import { Modal } from "../../components/ui/Modal";
import { StudentPicker } from "../../components/ui/StudentPicker";
import { ExternalStudentPicker } from "../../components/ui/ExternalStudentPicker";
import { useCoachingClasses, useCoachingEnrollments, useAddCoachingEnrollment, useDeleteCoachingEnrollment, useDeleteCoachingClass } from "../../api/coaching";
import { extractErrorMessage } from "../../api/client";
import { formatDate, formatDuration } from "../../lib/format";
import { downloadCsv } from "../../lib/csvExport";
import type { ExternalParticipant, Student } from "../../api/types";

export function CoachingClassDetail() {
  const { classId } = useParams();
  const navigate = useNavigate();
  const id = Number(classId);
  const { data: sessions, isLoading, isError, error } = useCoachingClasses();
  const session = sessions?.find(s => s.class_id === id);
  const enrollments = useCoachingEnrollments(id);
  const [participantModal, setParticipantModal] = useState(false);
  const [metricsTab, setMetricsTab] = useState(false);
  const deleteClass = useDeleteCoachingClass();

  const handleDelete = async () => {
    if (confirm("Delete this coaching session?")) {
      try {
        await deleteClass.mutateAsync(id);
        toast.success("Session deleted");
        navigate("/coaching-classes");
      } catch (err) {
        toast.error(extractErrorMessage(err));
      }
    }
  };

  if (isLoading) return <Spinner label="Loading session…" />;
  if (isError || !session) return <ErrorBanner message={extractErrorMessage(error)} />;

  const metrics = enrollments.data ? {
    totalAttendees: enrollments.data.length,
    libraryStudents: enrollments.data.filter(e => e.participant_type === 'Library Student').length,
    externalStudents: enrollments.data.filter(e => e.participant_type === 'External Student').length,
    averageAttendance: enrollments.data.length > 0 ? 100 : 0,
  } : null;

  const exportRows = () => downloadCsv(`coaching-${id}`, (enrollments.data ?? []).map(x => ({ 
    session: session.title, 
    date: session.class_date, 
    participant: x.participant_name, 
    type: x.participant_type, 
    village: x.village, 
    phone: x.phone, 
    duration_minutes: session.duration_minutes 
  })));

  return (
    <div>
      <button 
        onClick={() => navigate("/coaching-classes")} 
        className="mb-4 flex items-center gap-1.5 text-sm text-slate hover:text-ink"
      >
        <ArrowLeft size={15} /> Back to sessions
      </button>

      <PageHeader
        eyebrow={session.subject ?? "Coaching Class"}
        title={session.title}
        description={`${formatDate(session.class_date)} · ${session.instructor_name ?? "No instructor assigned"}${session.duration_minutes ? ` · ${formatDuration(session.duration_minutes)}` : ""}`}
        action={
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={exportRows}>
              <Download size={15}/> Export CSV
            </Button>
            <Button variant="secondary" onClick={() => setMetricsTab(!metricsTab)}>
              <BarChart3 size={16}/> {metricsTab ? 'Hide Metrics' : 'Metrics'}
            </Button>
            <Button onClick={() => setParticipantModal(true)}>
              <UserPlus size={16}/> Add participant
            </Button>
            <Button variant="ghost" onClick={handleDelete}>
              <Trash2 size={16} className="text-rust"/>
            </Button>
          </div>
        }
      />

      {metricsTab && metrics && (
        <div className="mb-6 grid gap-4 md:grid-cols-4">
          <div className="rounded-lg border border-border bg-card p-4">
            <p className="text-xs font-semibold text-slate uppercase tracking-wider">Total Attendees</p>
            <p className="mt-2 text-2xl font-semibold">{metrics.totalAttendees}</p>
          </div>
          <div className="rounded-lg border border-border bg-card p-4">
            <p className="text-xs font-semibold text-slate uppercase tracking-wider">Library Students</p>
            <p className="mt-2 text-2xl font-semibold">{metrics.libraryStudents}</p>
          </div>
          <div className="rounded-lg border border-border bg-card p-4">
            <p className="text-xs font-semibold text-slate uppercase tracking-wider">External Students</p>
            <p className="mt-2 text-2xl font-semibold">{metrics.externalStudents}</p>
          </div>
          <div className="rounded-lg border border-border bg-card p-4">
            <p className="text-xs font-semibold text-slate uppercase tracking-wider">Attendance Rate</p>
            <p className="mt-2 text-2xl font-semibold">{metrics.averageAttendance}%</p>
          </div>
        </div>
      )}

      {enrollments.isLoading && <Spinner label="Loading attendees…" />}
      {enrollments.isError && <ErrorBanner message={extractErrorMessage(enrollments.error)} />}
      {enrollments.data && enrollments.data.length === 0 && (
        <EmptyState title="No attendees" description="Add a participant to begin tracking attendance." />
      )}

      {enrollments.data && enrollments.data.length > 0 && (
        <div className="rounded-xl border border-border bg-card overflow-hidden">
          <div className="p-5 border-b border-border">
            <h2 className="font-semibold text-lg">Attendees ({enrollments.data.length})</h2>
          </div>
          <Table>
            <Thead>
              <Th>Participant</Th>
              <Th>Type</Th>
              <Th>Village</Th>
              <Th>Phone</Th>
              <Th className="text-right">Action</Th>
            </Thead>
            <tbody>
              {enrollments.data.map(x => (
                <Tr key={x.enrollment_id}>
                  <Td className="font-medium">
                    {x.participant_name}
                    {x.student_id && <span className="ml-2 font-mono text-xs text-slate">#{x.student_id}</span>}
                  </Td>
                  <Td>{x.participant_type}</Td>
                  <Td>{x.village ?? "—"}</Td>
                  <Td>{x.phone ?? "—"}</Td>
                  <Td className="text-right">
                    <RemoveParticipantButton enrollmentId={x.enrollment_id} classId={id} participantName={x.participant_name} />
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}

      <ParticipantForm 
        open={participantModal} 
        onClose={() => setParticipantModal(false)} 
        classId={id}
      />
    </div>
  );
}

function RemoveParticipantButton({ enrollmentId, classId, participantName }: { enrollmentId: number; classId: number; participantName: string }) {
  const deleteEnrollment = useDeleteCoachingEnrollment(classId);
  const [isDeleting, setIsDeleting] = useState(false);

  const handleDelete = async () => {
    if (confirm(`Remove ${participantName} from this session?`)) {
      try {
        setIsDeleting(true);
        await deleteEnrollment.mutateAsync(enrollmentId);
        toast.success("Participant removed");
      } catch (err) {
        toast.error(extractErrorMessage(err));
      } finally {
        setIsDeleting(false);
      }
    }
  };

  return (
    <Button 
      size="sm" 
      variant="ghost" 
      onClick={handleDelete}
      disabled={isDeleting}
    >
      <Trash2 size={14} className="text-rust" />
    </Button>
  );
}

function ParticipantForm({ open, onClose, classId }: { open: boolean; onClose: () => void; classId: number }) {
  const add = useAddCoachingEnrollment(classId);
  const [type, setType] = useState("Library Student");
  const [student, setStudent] = useState<Student | null>(null);
  const [picked, setPicked] = useState<ExternalParticipant | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      if (type === 'Library Student' && student) {
        await add.mutateAsync({ 
          participant_type: 'Library Student', 
          student_id: student.student_id 
        });
      }
      if (type === 'External Student' && picked) {
        await add.mutateAsync({ 
          participant_type: 'External Student', 
          external_participant_id: picked.external_participant_id 
        });
      }
      toast.success("Participant added");
      onClose();
      setStudent(null);
      setPicked(null);
    } catch (err) {
      toast.error(extractErrorMessage(err));
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Add attendee">
      <form onSubmit={handleSubmit} className="space-y-4">
        <Field label="Participant type" required>
          <Select value={type} onChange={e => setType(e.target.value)}>
            <option>Library Student</option>
            <option>External Student</option>
          </Select>
        </Field>

        {type === 'Library Student' ? (
          <Field label="Student" required>
            <StudentPicker value={student} onChange={setStudent} activeOnly={false} />
          </Field>
        ) : (<>
          <Field label="External student" required>
            <ExternalStudentPicker value={picked} onChange={setPicked} />
          </Field>
          {/*
            <Field label="External student" required>
              <Select 
                value={picked?.external_participant_id ?? ""} 
                onChange={e => setPicked(external?.find(x => x.external_participant_id === Number(e.target.value)))}
              >
                <option value="">Select external student</option>
                {external?.map(x => (
                  <option key={x.external_participant_id} value={x.external_participant_id}>
                    {x.name} · {x.village}
                  </option>
                ))}
              </Select>
            </Field>
          */}
        </>)}

        <div className="flex justify-end gap-2 border-t border-border pt-4">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={add.isPending || (type === 'Library Student' && !student) || (type === 'External Student' && !picked)}>
            {add.isPending ? "Adding..." : "Add attendee"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
