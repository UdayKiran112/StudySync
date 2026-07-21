import { lazy, Suspense } from "react";
import { createBrowserRouter } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Dashboard } from "./pages/Dashboard";
import { StudentsList } from "./pages/students/StudentsList";
import { StudentDetail } from "./pages/students/StudentDetail";
import { AttendancePage } from "./pages/attendance/AttendancePage";
import { DigitalLibraryPage } from "./pages/digital-library/DigitalLibraryPage";
import { OfflineLibraryPage } from "./pages/offline-library/OfflineLibraryPage";
import { BooksPage } from "./pages/books/BooksPage";
import { SubscriptionsPage } from "./pages/subscriptions/SubscriptionsPage";
import { ExamsList } from "./pages/exams/ExamsList";
import { ExamDetail } from "./pages/exams/ExamDetail";
import { QuizzesList } from "./pages/quizzes/QuizzesList";
import { QuizDetail } from "./pages/quizzes/QuizDetail";
import { SettingsPage } from "./pages/settings/SettingsPage";
import { CoachingClassesPage } from "./pages/coaching/CoachingClassesPage";
import { CoachingClassDetail } from "./pages/coaching/CoachingClassDetail";
import { Spinner } from "./components/ui/Feedback";

const StudentAnalyticsPage = lazy(() =>
  import("./pages/analytics/StudentAnalyticsPage").then((m) => ({
    default: m.StudentAnalyticsPage,
  })),
);

function LazyAnalyticsPage() {
  return (
    <Suspense fallback={<Spinner label="Loading analytics…" />}>
      <StudentAnalyticsPage />
    </Suspense>
  );
}

export const router = createBrowserRouter([
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <Dashboard /> },
      { path: "students", element: <StudentsList /> },
      { path: "students/:studentId", element: <StudentDetail /> },
      { path: "attendance", element: <AttendancePage /> },
      { path: "digital-library", element: <DigitalLibraryPage /> },
      { path: "offline-library", element: <OfflineLibraryPage /> },
      { path: "books", element: <BooksPage /> },
      { path: "subscriptions", element: <SubscriptionsPage /> },
      { path: "exams", element: <ExamsList /> },
      { path: "exams/:examId", element: <ExamDetail /> },
      { path: "quizzes", element: <QuizzesList /> },
      { path: "quizzes/:quizId", element: <QuizDetail /> },
      { path: "analytics", element: <LazyAnalyticsPage /> },
      { path: "analytics/:studentId", element: <LazyAnalyticsPage /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "coaching-classes", element: <CoachingClassesPage /> },
      { path: "coaching-classes/:classId", element: <CoachingClassDetail /> },
    ],
  },
]);
