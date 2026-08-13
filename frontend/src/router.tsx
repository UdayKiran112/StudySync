import { lazy, Suspense, type ReactNode } from "react";
import { createBrowserRouter } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Spinner } from "./components/ui/Feedback";

const Dashboard = lazy(() => import("./pages/Dashboard").then((m) => ({ default: m.Dashboard })));
const StudentsList = lazy(() => import("./pages/students/StudentsList").then((m) => ({ default: m.StudentsList })));
const StudentDetail = lazy(() => import("./pages/students/StudentDetail").then((m) => ({ default: m.StudentDetail })));
const AttendancePage = lazy(() => import("./pages/attendance/AttendancePage").then((m) => ({ default: m.AttendancePage })));
const AttendanceRecordsPage = lazy(() => import("./pages/attendance/AttendanceRecordsPage").then((m) => ({ default: m.AttendanceRecordsPage })));
const DigitalLibraryPage = lazy(() => import("./pages/digital-library/DigitalLibraryPage").then((m) => ({ default: m.DigitalLibraryPage })));
const OfflineLibraryPage = lazy(() => import("./pages/offline-library/OfflineLibraryPage").then((m) => ({ default: m.OfflineLibraryPage })));
const BooksPage = lazy(() => import("./pages/books/BooksPage").then((m) => ({ default: m.BooksPage })));
const SubscriptionsPage = lazy(() => import("./pages/subscriptions/SubscriptionsPage").then((m) => ({ default: m.SubscriptionsPage })));
const ExamsList = lazy(() => import("./pages/exams/ExamsList").then((m) => ({ default: m.ExamsList })));
const ExamDetail = lazy(() => import("./pages/exams/ExamDetail").then((m) => ({ default: m.ExamDetail })));
const QuizzesList = lazy(() => import("./pages/quizzes/QuizzesList").then((m) => ({ default: m.QuizzesList })));
const QuizDetail = lazy(() => import("./pages/quizzes/QuizDetail").then((m) => ({ default: m.QuizDetail })));
const SettingsPage = lazy(() => import("./pages/settings/SettingsPage").then((m) => ({ default: m.SettingsPage })));
const CoachingClassesPage = lazy(() => import("./pages/coaching/CoachingClassesPage").then((m) => ({ default: m.CoachingClassesPage })));
const CoachingClassDetail = lazy(() => import("./pages/coaching/CoachingClassDetail").then((m) => ({ default: m.CoachingClassDetail })));
const OtherActivitiesPage = lazy(() => import("./pages/other-activities/OtherActivitiesPage").then((m) => ({ default: m.OtherActivitiesPage })));
const OtherActivityDetail = lazy(() => import("./pages/other-activities/OtherActivityDetail").then((m) => ({ default: m.OtherActivityDetail })));
const StudentAnalyticsPage = lazy(() => import("./pages/analytics/StudentAnalyticsPage").then((m) => ({ default: m.StudentAnalyticsPage })));
const HolidaysPage = lazy(() => import("./pages/holidays/HolidaysPage").then((m) => ({ default: m.HolidaysPage })));

function LazyPage({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<Spinner label="Loading…" />}>{children}</Suspense>
  );
}

export const router = createBrowserRouter([
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <LazyPage><Dashboard /></LazyPage> },
      { path: "students", element: <LazyPage><StudentsList /></LazyPage> },
      { path: "students/:studentId", element: <LazyPage><StudentDetail /></LazyPage> },
      { path: "attendance", element: <LazyPage><AttendancePage /></LazyPage> },
      { path: "attendance/records", element: <LazyPage><AttendanceRecordsPage /></LazyPage> },
      { path: "digital-library", element: <LazyPage><DigitalLibraryPage /></LazyPage> },
      { path: "offline-library", element: <LazyPage><OfflineLibraryPage /></LazyPage> },
      { path: "books", element: <LazyPage><BooksPage /></LazyPage> },
      { path: "subscriptions", element: <LazyPage><SubscriptionsPage /></LazyPage> },
      { path: "exams", element: <LazyPage><ExamsList /></LazyPage> },
      { path: "exams/:examId", element: <LazyPage><ExamDetail /></LazyPage> },
      { path: "quizzes", element: <LazyPage><QuizzesList /></LazyPage> },
      { path: "quizzes/:quizId", element: <LazyPage><QuizDetail /></LazyPage> },
      { path: "analytics", element: <LazyPage><StudentAnalyticsPage /></LazyPage> },
      { path: "analytics/:studentId", element: <LazyPage><StudentAnalyticsPage /></LazyPage> },
      { path: "holidays", element: <LazyPage><HolidaysPage /></LazyPage> },
      { path: "settings", element: <LazyPage><SettingsPage /></LazyPage> },
      { path: "coaching-classes", element: <LazyPage><CoachingClassesPage /></LazyPage> },
      { path: "coaching-classes/:classId", element: <LazyPage><CoachingClassDetail /></LazyPage> },
      { path: "other-activities", element: <LazyPage><OtherActivitiesPage /></LazyPage> },
      {
        path: "other-activities/:activityId",
        element: <LazyPage><OtherActivityDetail /></LazyPage>,
      },
    ],
  },
]);
