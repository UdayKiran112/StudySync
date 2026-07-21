// Mirrors backend/models/*.py response & request shapes exactly.

export type Gender = "Male" | "Female" | "Other";
export type StudentStatus = "Active" | "Inactive";
export type Session = "Morning" | "Afternoon" | "Full Day";
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

export type StudentUpdateInput = Partial<
  Omit<StudentCreateInput, "student_id">
>;

export interface Attendance {
  attendance_id: number;
  student_id: number;
  date: string;
  /** Auto-detected server-side from check_in/check_out — never chosen by staff. */
  session: Session;
  check_in: string | null;
  check_out: string | null;
  duration_minutes: number | null;
}

export interface AttendanceCheckInInput {
  student_id: number;
  date?: string | null;
  check_in?: string | null;
}

export interface AttendanceCheckOutInput {
  student_id: number;
  check_out?: string | null;
}

/** Manual correction of a mistaken entry — session/duration are
 *  recomputed server-side whenever check_in or check_out changes. */
export interface AttendanceUpdateInput {
  check_in?: string | null;
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

export type OfflineLibraryUpdateInput = Partial<
  Omit<OfflineLibraryCreateInput, "student_id">
>;

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

export type SubscriptionUpdateInput = Partial<
  Omit<SubscriptionCreateInput, "subscription_id">
>;

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

export type ExamMarkUpdateInput = Partial<
  Omit<ExamMarkCreateInput, "student_id">
>;

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

export type QuizScoreUpdateInput = Partial<
  Omit<QuizScoreCreateInput, "student_id">
>;

export type CoachingParticipantType = "Library Student" | "External Student";
export type CoachingAttendanceStatus = "Registered" | "Present" | "Absent" | "Cancelled";
export interface CoachingClass { class_id: number; title: string; class_date: string; start_time: string | null; end_time: string | null; subject: string | null; instructor: string | null; venue: string | null; capacity: number | null; notes: string | null; created_at: string; }
export type CoachingClassInput = Omit<CoachingClass, "class_id" | "created_at">;
export interface CoachingEnrollment { enrollment_id: number; class_id: number; participant_type: CoachingParticipantType; student_id: number | null; external_name: string | null; village: string | null; phone: string | null; gender: Gender | null; guardian_name: string | null; notes: string | null; attendance_status: CoachingAttendanceStatus; enrolled_at: string; participant_name: string; }
export interface CoachingEnrollmentInput { participant_type: CoachingParticipantType; student_id?: number | null; external_name?: string | null; village?: string | null; phone?: string | null; gender?: Gender | null; guardian_name?: string | null; notes?: string | null; }

export interface ApiErrorBody {
  detail?: string | { msg: string; loc?: (string | number)[] }[];
}

// ---------------------------------------------------------------------
// Student profile dashboard (mirrors backend/models/dashboard.py)
// ---------------------------------------------------------------------

export interface OfflineLibraryProfileItem {
  usage_id: number;
  student_id: number;
  date: string;
  book_id: string | null;
  book_title: string | null;
}

export type AssessmentType = "Exam" | "Quiz" | string;

export interface AssessmentAttempt {
  assessment_id: number;
  assessment_name: string;
  assessment_type: AssessmentType;
  date: string | null;
  subject: string | null;
  marks_obtained: number;
  max_marks: number;
  percentage: number;
  remarks: string | null;
  batch_average_percentage: number | null;
}

export interface SubjectPerformance {
  subject: string;
  total_assessments: number;
  average_percentage: number | null;
  trend: string;
  trend_delta_percentage_points: number | null;
}

export interface BookCategoryCount {
  category: string;
  count: number;
}

export interface AttendanceAnalytics {
  total_sessions: number;
  completed_sessions: number;
  average_duration_minutes: number | null;
  trend: string;
  trend_delta_minutes: number | null;
  attendance_rate_last_30_days_percent: number | null;
  current_streak_days: number;
  days_since_last_visit: number | null;
}

export interface ExamAnalytics {
  total_exams: number;
  average_percentage: number | null;
  trend: string;
  trend_delta_percentage_points: number | null;
}

export interface QuizAnalytics {
  total_quizzes: number;
  average_percentage: number | null;
  trend: string;
  trend_delta_percentage_points: number | null;
}

export interface OverallAnalytics {
  total_assessments: number;
  average_percentage: number | null;
  trend: string;
  trend_delta_percentage_points: number | null;
}

export interface DigitalLibraryAnalytics {
  total_sessions: number;
  average_duration_minutes: number | null;
}

export interface OfflineLibraryAnalytics {
  total_sessions: number;
  self_study_sessions: number;
  by_category: BookCategoryCount[];
  estimated_total_minutes: number;
}

export interface PerformanceAnalytics {
  overall: OverallAnalytics;
  attendance: AttendanceAnalytics;
  exams: ExamAnalytics;
  quizzes: QuizAnalytics;
  subjects: SubjectPerformance[];
  digital_library: DigitalLibraryAnalytics;
  offline_library: OfflineLibraryAnalytics;
}

export interface StudentDashboardResponse {
  student: Student;
  attendance_history: Attendance[];
  digital_library_usage: DigitalLibraryUsage[];
  offline_library_usage: OfflineLibraryProfileItem[];
  exams_attempted: AssessmentAttempt[];
  quizzes_attempted: AssessmentAttempt[];
  score_trend: AssessmentAttempt[];
  analytics: PerformanceAnalytics;
}
