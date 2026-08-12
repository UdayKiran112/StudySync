import { useStudent } from "../../api/students";

/** Renders a student's name alongside their ID (name + #id). */
export function StudentName({ studentId }: { studentId: number }) {
  const { data } = useStudent(studentId);
  const name = data?.name;
  return (
    <span className="flex items-center gap-1.5">
      <span className="font-medium text-ink">
        {name ?? `Student #${studentId}`}
      </span>
      <span className="font-mono text-xs text-slate">#{studentId}</span>
    </span>
  );
}
