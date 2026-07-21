import { useState } from "react";
import { useNavigate } from "react-router-dom";
import toast from "react-hot-toast";
import { CalendarPlus, Download, UserPlus } from "lucide-react";
import { Button } from "../../components/ui/Button";
import { Field, Input, Select } from "../../components/ui/Form";
import { Modal } from "../../components/ui/Modal";
import { EmptyState, PageHeader, Spinner } from "../../components/ui/Feedback";
import { formatDate, todayIso } from "../../lib/format";
import { useCoachingClasses, useCreateCoachingClass, useCreateExternalParticipant, useCreateInstructor, useInstructors } from "../../api/coaching";

export function CoachingClassesPage() {
  const { data: sessions, isLoading } = useCoachingClasses();
  const [modal, setModal] = useState<"session" | "instructor" | "external" | null>(null);
  
  return <div>
    <PageHeader eyebrow="Coaching centre" title="Coaching classes" description="Create sessions, reuse instructors and external-student profiles, and enroll attendees." action={<div className="flex flex-wrap gap-2"><Button variant="secondary" onClick={() => setModal("instructor")}>Add Instructor</Button><Button variant="secondary" onClick={() => setModal("external")}>Add External Student</Button><Button onClick={() => setModal("session")}><CalendarPlus size={16}/> Add New Session</Button></div>}/>
    {isLoading && <Spinner label="Loading coaching sessions…"/>}
    {!isLoading && !sessions?.length && <EmptyState title="No coaching sessions" description="Create a session to begin enrolling attendees."/>}
    {!!sessions?.length && <SessionsList sessions={sessions}/>}
    <SessionForm open={modal === "session"} onClose={() => setModal(null)}/><InstructorForm open={modal === "instructor"} onClose={() => setModal(null)}/><ExternalForm open={modal === "external"} onClose={() => setModal(null)}/>
  </div>;
}

function SessionsList({ sessions }: { sessions: import("../../api/types").CoachingClass[] }) {
  const navigate = useNavigate();
  
  return <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
    {sessions.map(s => <button key={s.class_id} onClick={() => navigate(`/coaching-classes/${s.class_id}`)} className="rounded-lg border border-border bg-card p-4 text-left hover:border-slate hover:shadow-md transition-all">
      <p className="font-semibold text-base">{s.title}</p>
      {s.subject && <p className="mt-1 text-xs text-slate">Subject: {s.subject}</p>}
      <p className="mt-2 text-sm text-slate">{formatDate(s.class_date)}</p>
      <p className="mt-1 text-sm text-slate">{s.instructor_name ?? "No instructor assigned"}</p>
    </button>)}
  </div>;
}

function SessionForm({ open, onClose }: { open: boolean; onClose: () => void }) { 
  const create = useCreateCoachingClass();
  const { data: instructors } = useInstructors(); 
  const [title,setTitle]=useState(""); 
  const [date,setDate]=useState(todayIso()); 
  const [subject,setSubject]=useState(""); 
  const [start,setStart]=useState(""); 
  const [end,setEnd]=useState(""); 
  const [instructor,setInstructor]=useState(""); 
  const submit=async(e:React.FormEvent)=>{
    e.preventDefault();
    try{
      await create.mutateAsync({
        title,
        class_date:date,
        subject:subject||null,
        start_time:start||null,
        end_time:end||null,
        instructor_id:instructor?Number(instructor):null,
        notes:null
      });
      toast.success("Session created");
      onClose();
    }catch{
      toast.error("Could not create session")
    }
  }; 
  return <Modal open={open} onClose={onClose} title="Add New Session">
    <form onSubmit={submit} className="space-y-4">
      <Field label="Session title" required>
        <Input required value={title} onChange={e=>setTitle(e.target.value)}/>
      </Field>
      <Field label="Subject">
        <Input value={subject} onChange={e=>setSubject(e.target.value)}/>
      </Field>
      <div className="grid grid-cols-3 gap-3">
        <Field label="Date" required>
          <Input type="date" required value={date} onChange={e=>setDate(e.target.value)}/>
        </Field>
        <Field label="Class start time">
          <Input type="time" value={start} onChange={e=>setStart(e.target.value)}/>
        </Field>
        <Field label="Class end time">
          <Input type="time" value={end} onChange={e=>setEnd(e.target.value)}/>
        </Field>
      </div>
      <Field label="Instructor">
        <Select value={instructor} onChange={e=>setInstructor(e.target.value)}>
          <option value="">Select instructor</option>
          {instructors?.map(i=><option key={i.instructor_id} value={i.instructor_id}>{i.name}</option>)}
        </Select>
      </Field>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
        <Button type="submit">Save session</Button>
      </div>
    </form>
  </Modal>; 
}
function InstructorForm({open,onClose}:{open:boolean;onClose:()=>void}){
  const create=useCreateInstructor();
  const [name,setName]=useState("");
  const [phone,setPhone]=useState("");
  const [specialization,setSpecialization]=useState("");
  return <Modal open={open} onClose={onClose} title="Add Instructor">
    <form onSubmit={async e=>{e.preventDefault();await create.mutateAsync({name,phone:phone||null,specialization:specialization||null,notes:null});toast.success("Instructor added");onClose()}} className="space-y-4">
      <Field label="Instructor name" required>
        <Input required value={name} onChange={e=>setName(e.target.value)}/>
      </Field>
      <Field label="Phone">
        <Input value={phone} onChange={e=>setPhone(e.target.value)}/>
      </Field>
      <Field label="Specialization">
        <Input value={specialization} onChange={e=>setSpecialization(e.target.value)}/>
      </Field>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
        <Button type="submit">Add Instructor</Button>
      </div>
    </form>
  </Modal>
}
function ExternalForm({open,onClose}:{open:boolean;onClose:()=>void}){
  const create=useCreateExternalParticipant();
  const [name,setName]=useState("");
  const [village,setVillage]=useState("");
  const [phone,setPhone]=useState("");
  return <Modal open={open} onClose={onClose} title="Add External Student">
    <form onSubmit={async e=>{e.preventDefault();await create.mutateAsync({name,village,phone:phone||null,gender:null,guardian_name:null,notes:null});toast.success("External student added");onClose()}} className="space-y-4">
      <Field label="Student name" required>
        <Input required value={name} onChange={e=>setName(e.target.value)}/>
      </Field>
      <Field label="Village" required>
        <Input required value={village} onChange={e=>setVillage(e.target.value)}/>
      </Field>
      <Field label="Phone">
        <Input value={phone} onChange={e=>setPhone(e.target.value)}/>
      </Field>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
        <Button type="submit">Add External Student</Button>
      </div>
    </form>
  </Modal>
}
