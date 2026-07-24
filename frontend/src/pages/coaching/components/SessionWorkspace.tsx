import { useState } from "react";
import toast from "react-hot-toast";
import { Download, Plus, Trash2, Clock, User, BookOpen } from "lucide-react";
import { Spinner, ErrorBanner, EmptyState } from "../../../components/ui/Feedback";
import { Table, Thead, Th, Tr, Td } from "../../../components/ui/Table";
import { Button } from "../../../components/ui/Button";
import { StatusTab } from "../../../components/ui/Tabs";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { useCoachingClass, useCoachingEnrollments, useDeleteCoachingClass } from "../../../api/coaching";
import { extractErrorMessage } from "../../../api/client";
import { formatDate, formatDuration, formatDateTime } from "../../../lib/format";
import { downloadCsv } from "../../../lib/csvExport";
import { EnrollParticipantModal } from "./EnrollParticipantModal";
import type { CoachingParticipantType } from "../../../api/types";

function participantTone(type: CoachingParticipantType): "forest" | "brass" {
  return type === "Library Student" ? "forest" : "brass";
}

export function SessionWorkspace({ classId, onDeleted }: { classId: number; onDeleted: () => void }) {
  const [enrollOpen, setEnrollOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const { data: cls, isLoading, isError, error } = useCoachingClass(classId);
  const roster = useCoachingEnrollments(classId);
  const deleteMutation = useDeleteCoachingClass();

  async function handleDelete() {
    try {
      await deleteMutation.mutateAsync(classId);
      toast.success("Session deleted");
      setDeleteOpen(false);
      onDeleted();
    } catch (err) {
      toast.error(extractErrorMessage(err));
    }
  }

  function handleExport() {
    if (!roster.data || roster.data.length === 0 || !cls) return;
    downloadCsv(
      `${cls.title}-roster`,
      roster.data.map((r) => ({
        participant: r.participant_name,
        type: r.participant_type,
        village: r.village ?? "",
        phone: r.phone ?? "",
        enrolled_at: r.enrolled_at,
      }))
    );
  }

  if (isLoading) return <Spinner label="Loading session…" />;
  if (isError || !cls) return <ErrorBanner message={extractErrorMessage(error)} />;

  return (
    <div>
      <div className="rounded-lg border border-border bg-card p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="font-display text-lg font-semibold text-ink">{cls.title}</h2>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate">
              <span className="flex items-center gap-1">
                <Clock size={13} /> {formatDate(cls.class_date)}
                {(cls.start_time || cls.end_time) && (
                  <span className="font-mono">
                    {" "}
                    · {cls.start_time ?? "—"}–{cls.end_time ?? "—"}
                    {cls.duration_minutes != null && ` (${formatDuration(cls.duration_minutes)})`}
                  </span>
                )}
              </span>
              {cls.subject && (
                <span className="flex items-center gap-1">
                  <BookOpen size={13} /> {cls.subject}
                </span>
              )}
              {cls.instructor_name && (
                <span className="flex items-center gap-1">
                  <User size={13} /> {cls.instructor_name}
                </span>
              )}
            </div>
            {cls.notes && <p className="mt-2 max-w-2xl text-sm text-slate">{cls.notes}</p>}
          </div>
          <div className="flex shrink-0 gap-2">
            <Button variant="secondary" onClick={handleExport} disabled={!roster.data || roster.data.length === 0}>
              <Download size={15} /> Export
            </Button>
            <Button variant="danger" onClick={() => setDeleteOpen(true)}>
              <Trash2 size={15} />
            </Button>
          </div>
        </div>
      </div>

      <div className="mt-5 mb-3 flex items-center justify-between">
        <h3 className="font-display text-base font-semibold text-ink">
          Roster{roster.data ? ` (${roster.data.length})` : ""}
        </h3>
        <Button variant="primary" size="sm" onClick={() => setEnrollOpen(true)}>
          <Plus size={14} /> Enroll participant
        </Button>
      </div>

      {roster.isLoading && <Spinner label="Loading roster…" />}
      {roster.isError && <ErrorBanner message={extractErrorMessage(roster.error)} />}
      {roster.data && roster.data.length === 0 && (
        <EmptyState title="No one enrolled yet" description="Enroll a library student or an external participant." />
      )}

      {roster.data && roster.data.length > 0 && (
        <Table>
          <Thead>
            <Th>Participant</Th>
            <Th>Type</Th>
            <Th>Village</Th>
            <Th>Phone</Th>
            <Th>Enrolled</Th>
          </Thead>
          <tbody>
            {roster.data.map((r) => (
              <Tr key={r.enrollment_id}>
                <Td className="font-medium">{r.participant_name}</Td>
                <Td>
                  <StatusTab tone={participantTone(r.participant_type as CoachingParticipantType)}>
                    {r.participant_type}
                  </StatusTab>
                </Td>
                <Td className="text-slate">{r.village ?? "—"}</Td>
                <Td className="text-slate">{r.phone ?? "—"}</Td>
                <Td className="text-xs text-slate-light">{formatDateTime(r.enrolled_at)}</Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      )}

      <EnrollParticipantModal open={enrollOpen} onClose={() => setEnrollOpen(false)} classId={classId} />

      <ConfirmDialog
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        onConfirm={handleDelete}
        title="Delete session"
        message={`Delete "${cls.title}"? This also removes everyone's enrollment for it.`}
        pending={deleteMutation.isPending}
      />
    </div>
  );
}
