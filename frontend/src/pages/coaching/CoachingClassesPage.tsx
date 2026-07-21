import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { Plus, Trash2, UserPlus } from "lucide-react";
import { Button } from "../../components/ui/Button";
import { Field, Input, Select } from "../../components/ui/Form";
import { Modal } from "../../components/ui/Modal";
import { StudentPicker } from "../../components/ui/StudentPicker";
import { EmptyState, ErrorBanner, PageHeader, Spinner } from "../../components/ui/Feedback";
import { Table, Td, Th, Thead, Tr } from "../../components/ui/Table";
import { extractErrorMessage } from "../../api/client";
import { useAddCoachingEnrollment, useCoachingClasses, useCoachingEnrollments, useCreateCoachingClass, useDeleteCoachingClass, useDeleteCoachingEnrollment, useUpdateCoachingEnrollment } from "../../api/coaching";
import type { CoachingClass, CoachingEnrollment, CoachingParticipantType, Student } from "../../api/types";
import { formatDate, todayIso } from "../../lib/format";

export function CoachingClassesPage() {
  const { data: classes, isLoading, isError, error } = useCoachingClasses();
  const [selected, setSelected] = useState<CoachingClass>();
  const [classModal, setClassModal] = useState(false);
  const [participantModal, setParticipantModal] = useState(false);
  const removeClass = useDeleteCoachingClass();
  useEffect(() => { if (!selected && classes?.length) setSelected(classes[0]); }, [classes, selected]);
  async function deleteSelected() { if (!selected || !confirm(`Delete “${selected.title}” and its roster?`)) return; try { await removeClass.mutateAsync(selected.class_id); setSelected(undefined); toast.success("Coaching class removed"); } catch (e) { toast.error(extractErrorMessage(e)); } }
  return <div>
    <PageHeader eyebrow="Coaching" title="Coaching classes" description="Create occasional classes and manage one shared roster for library students and external attendees." action={<Button variant="primary" onClick={() => setClassModal(true)}><Plus size={16}/> Add class</Button>} />
    {isLoading && <Spinner label="Loading coaching classes…"/>}
    {isError && <ErrorBanner message={extractErrorMessage(error)}/>} 
    {classes && classes.length === 0 && <EmptyState title="No coaching classes yet" description="Add a class, then register library students or visitors from outside the library." action={<Button variant="primary" onClick={() => setClassModal(true)}><Plus size={16}/> Add first class</Button>}/>} 
    {classes && classes.length > 0 && <div className="grid gap-6 lg:grid-cols-[18rem_1fr]">
      <aside className="rounded-lg border border-border bg-card p-3"><p className="px-2 pb-2 text-xs font-semibold uppercase tracking-wide text-slate">Classes</p>{classes.map(item => <button key={item.class_id} onClick={() => setSelected(item)} className={`mb-1 w-full rounded-md px-3 py-2.5 text-left ${selected?.class_id === item.class_id ? "bg-brass/15 text-ink" : "hover:bg-paper-dim text-slate"}`}><p className="text-sm font-medium">{item.title}</p><p className="mt-0.5 text-xs">{formatDate(item.class_date)}{item.start_time ? ` · ${item.start_time}` : ""}</p></button>)}</aside>
      {selected && <ClassRoster coachingClass={selected} onAdd={() => setParticipantModal(true)} onDelete={deleteSelected}/>} 
    </div>}
    <ClassForm open={classModal} onClose={() => setClassModal(false)} onCreated={setSelected}/>
    {selected && <EnrollmentForm open={participantModal} onClose={() => setParticipantModal(false)} coachingClass={selected}/>} 
  </div>;
}

function ClassRoster({ coachingClass, onAdd, onDelete }: { coachingClass: CoachingClass; onAdd: () => void; onDelete: () => void }) {
  const { data: roster, isLoading, isError, error } = useCoachingEnrollments(coachingClass.class_id);
  const update = useUpdateCoachingEnrollment(coachingClass.class_id); const remove = useDeleteCoachingEnrollment(coachingClass.class_id);
  async function setStatus(item: CoachingEnrollment, attendance_status: CoachingEnrollment["attendance_status"]) { try { await update.mutateAsync({ id: item.enrollment_id, attendance_status }); } catch (e) { toast.error(extractErrorMessage(e)); } }
  async function removeItem(item: CoachingEnrollment) { if (!confirm(`Remove ${item.participant_name} from this class?`)) return; try { await remove.mutateAsync(item.enrollment_id); toast.success("Participant removed"); } catch (e) { toast.error(extractErrorMessage(e)); } }
  const active = roster?.filter(x => x.attendance_status !== "Cancelled").length ?? 0;
  return <section><div className="mb-4 rounded-lg border border-border bg-card p-5"><div className="flex flex-wrap justify-between gap-3"><div><h2 className="font-display text-xl font-semibold text-ink">{coachingClass.title}</h2><p className="mt-1 text-sm text-slate">{formatDate(coachingClass.class_date)} · {coachingClass.start_time ?? "Time TBA"}{coachingClass.end_time ? `–${coachingClass.end_time}` : ""} · {coachingClass.venue ?? "Venue TBA"}</p><p className="mt-1 text-xs text-slate">{active}{coachingClass.capacity ? ` / ${coachingClass.capacity}` : ""} registered · {coachingClass.instructor ?? "Instructor TBA"}</p></div><div className="flex gap-2"><Button variant="secondary" onClick={onAdd}><UserPlus size={15}/> Add participant</Button><Button variant="ghost" onClick={onDelete}><Trash2 size={15} className="text-rust"/></Button></div></div></div>
    {isLoading && <Spinner label="Loading roster…"/>}{isError && <ErrorBanner message={extractErrorMessage(error)}/>} {roster?.length === 0 && <EmptyState title="No participants yet" description="Register a library student by ID or add an external attendee."/>}
    {roster && roster.length > 0 && <Table><Thead><Th>Participant</Th><Th>Source</Th><Th>Village / phone</Th><Th>Attendance</Th><Th className="text-right">Action</Th></Thead><tbody>{roster.map(item => <Tr key={item.enrollment_id}><Td><p className="font-medium">{item.participant_name}</p>{item.student_id && <p className="font-mono text-xs text-slate">ID {item.student_id}</p>}{item.guardian_name && <p className="text-xs text-slate">Guardian: {item.guardian_name}</p>}</Td><Td>{item.participant_type}</Td><Td className="text-slate">{item.village ?? "—"}<br/>{item.phone ?? ""}</Td><Td><Select value={item.attendance_status} onChange={e => setStatus(item, e.target.value as CoachingEnrollment["attendance_status"])} className="min-w-28 py-1 text-xs"><option>Registered</option><option>Present</option><option>Absent</option><option>Cancelled</option></Select></Td><Td className="text-right"><Button size="sm" variant="ghost" onClick={() => removeItem(item)}><Trash2 size={14} className="text-rust"/></Button></Td></Tr>)}</tbody></Table>}
  </section>;
}

function ClassForm({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (item: CoachingClass) => void }) {
  const create = useCreateCoachingClass(); const [title, setTitle] = useState(""); const [classDate, setClassDate] = useState(todayIso()); const [start, setStart] = useState(""); const [end, setEnd] = useState(""); const [subject, setSubject] = useState(""); const [instructor, setInstructor] = useState(""); const [venue, setVenue] = useState(""); const [capacity, setCapacity] = useState("");
  async function submit(e: React.FormEvent) { e.preventDefault(); try { const item = await create.mutateAsync({ title, class_date: classDate, start_time: start || null, end_time: end || null, subject: subject || null, instructor: instructor || null, venue: venue || null, capacity: capacity ? Number(capacity) : null, notes: null }); onCreated(item); onClose(); toast.success("Coaching class created"); } catch (err) { toast.error(extractErrorMessage(err)); } }
  return <Modal open={open} onClose={onClose} title="Add coaching class"><form onSubmit={submit} className="space-y-4"><Field label="Class title" required><Input value={title} onChange={e => setTitle(e.target.value)} placeholder="e.g. Banking exam orientation" required/></Field><div className="grid grid-cols-3 gap-3"><Field label="Date" required><Input type="date" value={classDate} onChange={e => setClassDate(e.target.value)} required/></Field><Field label="Start"><Input type="time" value={start} onChange={e => setStart(e.target.value)}/></Field><Field label="End"><Input type="time" value={end} onChange={e => setEnd(e.target.value)}/></Field></div><div className="grid grid-cols-2 gap-3"><Field label="Subject"><Input value={subject} onChange={e => setSubject(e.target.value)}/></Field><Field label="Instructor"><Input value={instructor} onChange={e => setInstructor(e.target.value)}/></Field><Field label="Venue"><Input value={venue} onChange={e => setVenue(e.target.value)}/></Field><Field label="Capacity"><Input type="number" min="1" value={capacity} onChange={e => setCapacity(e.target.value)}/></Field></div><div className="flex justify-end gap-2 border-t border-border pt-4"><Button type="button" variant="ghost" onClick={onClose}>Cancel</Button><Button type="submit" variant="primary" disabled={create.isPending}>Create class</Button></div></form></Modal>;
}

function EnrollmentForm({ open, onClose, coachingClass }: { open: boolean; onClose: () => void; coachingClass: CoachingClass }) {
  const add = useAddCoachingEnrollment(coachingClass.class_id); const [type, setType] = useState<CoachingParticipantType>("Library Student"); const [student, setStudent] = useState<Student | null>(null); const [name, setName] = useState(""); const [village, setVillage] = useState(""); const [phone, setPhone] = useState(""); const [guardian, setGuardian] = useState("");
  async function submit(e: React.FormEvent) { e.preventDefault(); if (type === "Library Student" && !student) return toast.error("Choose a library student"); if (type === "External Student" && !name.trim()) return toast.error("External participant name is required"); try { await add.mutateAsync(type === "Library Student" ? { participant_type: type, student_id: student!.student_id } : { participant_type: type, external_name: name, village: village || null, phone: phone || null, guardian_name: guardian || null }); onClose(); setStudent(null); setName(""); toast.success("Participant registered"); } catch (err) { toast.error(extractErrorMessage(err)); } }
  return <Modal open={open} onClose={onClose} title="Add participant" subtitle={coachingClass.title}><form onSubmit={submit} className="space-y-4"><Field label="Participant type"><Select value={type} onChange={e => setType(e.target.value as CoachingParticipantType)}><option>Library Student</option><option>External Student</option></Select></Field>{type === "Library Student" ? <Field label="Library student" required><StudentPicker value={student} onChange={setStudent}/></Field> : <><Field label="Full name" required><Input value={name} onChange={e => setName(e.target.value)} required/></Field><div className="grid grid-cols-2 gap-3"><Field label="Village"><Input value={village} onChange={e => setVillage(e.target.value)}/></Field><Field label="Phone"><Input value={phone} onChange={e => setPhone(e.target.value)}/></Field></div><Field label="Parent / guardian"><Input value={guardian} onChange={e => setGuardian(e.target.value)}/></Field></>}<div className="flex justify-end gap-2 border-t border-border pt-4"><Button type="button" variant="ghost" onClick={onClose}>Cancel</Button><Button type="submit" variant="primary" disabled={add.isPending}>Register</Button></div></form></Modal>;
}
