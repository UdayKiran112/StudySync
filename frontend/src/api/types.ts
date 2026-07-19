// Mirrors backend/models/*.py response & request shapes exactly.

export type Gender = "Male" | "Female" | "Other";
export type StudentStatus = "Active" | "Inactive";
export type Session = "Morning" | "Afternoon";
export type AccountType = "Library Subscription" | "Own Account";
export type SubscriptionStatus = "Active" | "Expired";

export interface Student {
  student_id: number;
  name: string;
  gender: Gender | null;
  date_of_birth: string | null;
  phone: string | null;
  email: string | null;
  address: string | null;
  join_date: string;
  photo_path: string | null;
  status: StudentStatus;
  created_at: string;
  updated_at: string;
}

export interface StudentCreateInput {
  student_id: number;
  name: string;
  gender?: Gender | null;
  date_of_birth?: string | null;
  phone?: string | null;
  email?: string | null;
  address?: string | null;
  join_date: string;
  photo_path?: string | null;
  status?: StudentStatus;
}

export type StudentUpdateInput = Partial<Omit<StudentCreateInput, "student_id">>;

export interface Attendance {
  attendance_id: number;
  student_id: number;
  date: string;
  session: Session;
  check_in: string | null;
  check_out: string | null;
  duration_minutes: number | null;
}

export interface AttendanceCheckInInput {
  student_id: number;
  session: Session;
  date?: string | null;
  check_in?: string | null;
}

export interface AttendanceCheckOutInput {
  student_id: number;
  session: Session;
  date?: string | null;
  check_out?: string | null;
}

export interface DigitalLibraryUsage {
  usage_id: number;
  student_id: number;
  date: string;
  in_time: string;
  out_time: string | null;
  duration_minutes: number | null;
  account_type: AccountType;
  subscription_id: string | null;
  platform_name: string;
  purpose: string | null;
  notes: string | null;
}

export interface DigitalLibraryCheckInInput {
  student_id: number;
  account_type: AccountType;
  subscription_id?: string | null;
  platform_name: string;
  purpose?: string | null;
  notes?: string | null;
  date?: string | null;
  in_time?: string | null;
}

export interface DigitalLibraryCheckOutInput {
  student_id: number;
  out_time?: string | null;
}

export interface OfflineLibraryUsage {
  usage_id: number;
  student_id: number;
  date: string;
  book_id: string | null;
}

export interface OfflineLibraryCreateInput {
  student_id: number;
  book_id?: string | null;
  date?: string | null;
}

export type OfflineLibraryUpdateInput = Partial<Omit<OfflineLibraryCreateInput, "student_id">>;

export interface Subscription {
  subscription_id: string;
  name: string;
  type: string | null;
  cost: number | null;
  validity_days: number | null;
  status: SubscriptionStatus;
}

export interface SubscriptionCreateInput {
  subscription_id: string;
  name: string;
  type?: string | null;
  cost?: number | null;
  validity_days?: number | null;
  status?: SubscriptionStatus;
}

export type SubscriptionUpdateInput = Partial<Omit<SubscriptionCreateInput, "subscription_id">>;

export interface Book {
  book_id: string;
  title: string;
  category: string | null;
  author: string | null;
  added_date: string | null;
}

export interface BookCreateInput {
  book_id: string;
  title: string;
  category?: string | null;
  author?: string | null;
  added_date?: string | null;
}

export type BookUpdateInput = Partial<Omit<BookCreateInput, "book_id">>;

export interface Exam {
  exam_id: number;
  exam_name: string;
  exam_date: string | null;
  subject: string | null;
  max_marks: number;
}

export interface ExamCreateInput {
  exam_name: string;
  exam_date?: string | null;
  subject?: string | null;
  max_marks: number;
}

export type ExamUpdateInput = Partial<ExamCreateInput>;

export interface ExamMark {
  mark_id: number;
  student_id: number;
  exam_id: number;
  marks_obtained: number;
  remarks: string | null;
}

export interface ExamMarkCreateInput {
  student_id: number;
  marks_obtained: number;
  remarks?: string | null;
}

export type ExamMarkUpdateInput = Partial<Omit<ExamMarkCreateInput, "student_id">>;

export interface Quiz {
  quiz_id: number;
  quiz_name: string;
  quiz_date: string | null;
  subject: string | null;
  max_marks: number;
}

export interface QuizCreateInput {
  quiz_name: string;
  quiz_date?: string | null;
  subject?: string | null;
  max_marks: number;
}

export type QuizUpdateInput = Partial<QuizCreateInput>;

export interface QuizScore {
  score_id: number;
  student_id: number;
  quiz_id: number;
  score: number;
  remarks: string | null;
}

export interface QuizScoreCreateInput {
  student_id: number;
  score: number;
  remarks?: string | null;
}

export type QuizScoreUpdateInput = Partial<Omit<QuizScoreCreateInput, "student_id">>;

export interface ApiErrorBody {
  detail?: string | { msg: string; loc?: (string | number)[] }[];
}
