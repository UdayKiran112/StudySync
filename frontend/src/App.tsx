import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";
import { Toaster } from "react-hot-toast";
import { SettingsProvider } from "./context/SettingsContext";
import { router } from "./router";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <SettingsProvider>
        <RouterProvider router={router} />
        <Toaster
          position="top-right"
          toastOptions={{
            style: {
              background: "#1E2A38",
              color: "#EFF2EE",
              fontSize: "13px",
              borderRadius: "6px",
            },
            success: { iconTheme: { primary: "#A9782F", secondary: "#EFF2EE" } },
          }}
        />
      </SettingsProvider>
    </QueryClientProvider>
  );
}
