import { useState, useEffect } from "react";
import toast from "react-hot-toast";
import { Trash2, LogIn, LogOut } from "lucide-react";
import {
  PageHeader,
  Spinner,
  ErrorBanner,
  EmptyState,
  Pagination,
} from "../../components/ui/Feedback";
import { Table, Thead, Th, Tr, Td } from "../../components/ui/Table";
import { Field, Input, Select, Textarea } from "../../components/ui/Form";
import { Button } from "../../components/ui/Button";
import { StudentPicker } from "../../components/ui/StudentPicker";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import {
  useDigitalLibraryList,
  useDigitalCheckIn,
  useDigitalCheckOut,
  useDeleteDigitalUsage,
} from "../../api/digitalLibrary";
import { useSubscriptions } from "../../api/subscriptions";
import { apiClient, extractErrorMessage } from "../../api/client";
import {
  formatDate,
  formatDuration,
  todayIso,
  nowHHMM,
} from "../../lib/format";
import { ExportMenu } from "../../components/ui/ExportMenu";
import type {
  Student,
  AccountType,
  DigitalLibraryUsage,
} from "../../api/types";
import type { ExportColumn, ExportRow } from "../../components/ui/ExportMenu";

const LIMIT = 20;

const EXPORT_COLUMNS: ExportColumn[] = [
  { key: "student_id", label: "Student ID" },
  { key: "date", label: "Date" },
  { key: "platform_name", label: "Platform" },
  { key: "account_type", label: "Account type" },
  { key: "subscription_id", label: "Subscription ID" },
  { key: "in_time", label: "In time" },
  { key: "out_time", label: "Out time" },
  { key: "duration_minutes", label: "Duration (min)" },
  { key: "purpose", label: "Purpose" },
  { key: "notes", label: "Notes" },
];

function toExportRow(usage: DigitalLibraryUsage): ExportRow {
  return {
    student_id: usage.student_id,
    date: usage.date,
    platform_name: usage.platform_name,
    account_type: usage.account_type,
    subscription_id: usage.subscription_id ?? "",
    in_time: usage.in_time,
    out_time: usage.out_time ?? "",
    duration_minutes: usage.duration_minutes ?? "",
    purpose: usage.purpose ?? "",
    notes: usage.notes ?? "",
  };
}

/** Fetches every matching digital library session (paged 200 at a time). */
async function fetchAllDigitalLibrary(
  filterDate: string,
): Promise<DigitalLibraryUsage[]> {
  const all: DigitalLibraryUsage[] = [];
  let offset = 0;
  for (;;) {
    const { data } = await apiClient.get<DigitalLibraryUsage[]>(
      "/api/digital-library",
      { params: { date_: filterDate || undefined, limit: 200, offset } },
    );
    all.push(...data);
    if (data.length < 200) break;
    offset += 200;
  }
  return all;
}

export function DigitalLibraryPage() {
  const [mode, setMode] = useState<"check-in" | "check-out">("check-in");
  const [student, setStudent] = useState<Student | null>(null);
  const [accountType, setAccountType] = useState<AccountType>("Own Account");
  const [subscriptionId, setSubscriptionId] = useState("");
  const [platform, setPlatform] = useState("");
  const [purpose, setPurpose] = useState("");
  const [notes, setNotes] = useState("");
  const [entryDate, setEntryDate] = useState(todayIso());
  const [entryTime, setEntryTime] = useState(nowHHMM());
  // Tracks whether staff has manually edited the date/time fields. While
  // untouched, they keep following the live clock below; once edited, we
  // stop overwriting what the user typed.
  const [dateTouched, setDateTouched] = useState(false);
  const [timeTouched, setTimeTouched] = useState(false);
  const [now, setNow] = useState(new Date());

  const [filterDate, setFilterDate] = useState("");
  const [offset, setOffset] = useState(0);
  const [deleting, setDeleting] = useState<DigitalLibraryUsage | undefined>(
    undefined,
  );

  const checkIn = useDigitalCheckIn();
  const checkOut = useDigitalCheckOut();
  const deleteMutation = useDeleteDigitalUsage();
  const { data: subscriptions } = useSubscriptions({
    status: "Active",
    limit: 200,
  });

  const selectedSubscription = subscriptions?.find(
    (s) => s.subscription_id === subscriptionId,
  );
  // When checking in against a library subscription, the platform is
  // whatever that subscription is called — staff shouldn't (and can't)
  // type a different one. Only "Own account" sessions need a manual name.

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  // Keep the check-in/out date & time fields following the live IST clock
  // until the staff member edits one manually. Previously these were only
  // ever set once on mount, so the form silently went stale until a page
  // refresh (or a successful submit, which happened to reset them).
  useEffect(() => {
    if (!dateTouched) setEntryDate(todayIso(now));
    if (!timeTouched) setEntryTime(nowHHMM(now));
  }, [now, dateTouched, timeTouched]);

  const effectivePlatform =
    accountType === "Library Subscription"
      ? (selectedSubscription?.name ?? "")
      : platform;

  const { data, isLoading, isError, error } = useDigitalLibraryList({
    date_: filterDate || undefined,
    limit: LIMIT,
    offset,
  });

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!student) {
      toast.error("Pick a student first");
      return;
    }
    if (!entryTime) {
      toast.error("Enter a time");
      return;
    }
    try {
      if (mode === "check-in") {
        if (accountType === "Library Subscription") {
          if (!subscriptionId) {
            toast.error("Choose a subscription");
            return;
          }
        } else if (!platform.trim()) {
          toast.error("Platform name is required");
          return;
        }
        await checkIn.mutateAsync({
          student_id: student.student_id,
          account_type: accountType,
          subscription_id:
            accountType === "Library Subscription" ? subscriptionId : null,
          platform_name: effectivePlatform,
          purpose: purpose || null,
          notes: notes || null,
          date: entryDate || undefined,
          in_time: entryTime,
        });
        toast.success(
          `Checked in ${student.name} on ${effectivePlatform} at ${entryTime}`,
        );
        setPlatform("");
        setPurpose("");
        setNotes("");
        setSubscriptionId("");
      } else {
        await checkOut.mutateAsync({
          student_id: student.student_id,
          out_time: entryTime,
        });
        toast.success(`Checked out ${student.name} at ${entryTime}`);
      }
      setStudent(null);
      setEntryDate(todayIso());
      setEntryTime(nowHHMM());
      setDateTouched(false);
      setTimeTouched(false);
    } catch (err) {
      toast.error(extractErrorMessage(err));
    }
  }

  async function handleDelete() {
    if (!deleting) return;
    try {
      await deleteMutation.mutateAsync(deleting.usage_id);
      toast.success("Session removed");
      setDeleting(undefined);
    } catch (err) {
      toast.error(extractErrorMessage(err));
    }
  }

  const pending = checkIn.isPending || checkOut.isPending;

  return (
    <div>
      <PageHeader
        eyebrow="Library"
        title="Digital library"
        description="Track sessions on JSTOR, Britannica Online, and other subscribed or self-owned platforms."
        action={
          <div className="rounded-2xl border border-border bg-card px-4 py-3 text-right text-slate">
            <p className="text-xs uppercase tracking-[0.32em] text-slate-light">
              Current time
            </p>
            <p className="mt-1 text-lg font-semibold text-ink">
              {now.toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
              })}
            </p>
            <p className="mt-1 text-sm text-slate">
              {now.toLocaleDateString([], {
                weekday: "long",
                month: "long",
                day: "numeric",
              })}
            </p>
          </div>
        }
      />

      <div className="mb-8 rounded-lg border border-border bg-card p-6">
        <div className="mb-4 flex gap-1 rounded-md bg-paper-dim p-1 w-fit">
          <button
            onClick={() => setMode("check-in")}
            className={`flex items-center gap-1.5 rounded px-3 py-1.5 text-sm font-medium transition-colors ${
              mode === "check-in" ? "bg-card text-ink shadow-sm" : "text-slate"
            }`}
          >
            <LogIn size={14} /> Check in
          </button>
          <button
            onClick={() => setMode("check-out")}
            className={`flex items-center gap-1.5 rounded px-3 py-1.5 text-sm font-medium transition-colors ${
              mode === "check-out" ? "bg-card text-ink shadow-sm" : "text-slate"
            }`}
          >
            <LogOut size={14} /> Check out
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <Field label="Student" required>
            <StudentPicker value={student} onChange={setStudent} />
          </Field>

          {mode === "check-in" && (
            <>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <Field label="Account type" required>
                  <Select
                    value={accountType}
                    onChange={(e) => {
                      setAccountType(e.target.value as AccountType);
                      // Switching account type invalidates whichever of these
                      // belonged to the other mode.
                      setPlatform("");
                      setSubscriptionId("");
                    }}
                  >
                    <option value="Own Account">Own account</option>
                    <option value="Library Subscription">
                      Library subscription
                    </option>
                  </Select>
                </Field>

                {accountType === "Own Account" ? (
                  <Field label="Platform" required>
                    <Input
                      value={platform}
                      onChange={(e) => setPlatform(e.target.value)}
                      placeholder="e.g. JSTOR, Britannica Online"
                    />
                  </Field>
                ) : (
                  <Field
                    label="Subscription"
                    required
                    hint={
                      selectedSubscription
                        ? `Platform will be recorded as "${selectedSubscription.name}"`
                        : undefined
                    }
                  >
                    <Select
                      value={subscriptionId}
                      onChange={(e) => setSubscriptionId(e.target.value)}
                    >
                      <option value="">Select a subscription…</option>
                      {subscriptions?.map((s) => (
                        <option
                          key={s.subscription_id}
                          value={s.subscription_id}
                        >
                          {s.subscription_id} — {s.name}
                        </option>
                      ))}
                    </Select>
                  </Field>
                )}
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <Field label="Purpose">
                  <Input
                    value={purpose}
                    onChange={(e) => setPurpose(e.target.value)}
                    placeholder="What they're studying"
                  />
                </Field>
                <Field label="Notes">
                  <Textarea
                    rows={1}
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                  />
                </Field>
              </div>
            </>
          )}

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {mode === "check-in" && (
              <Field label="Date" required>
                <Input
                  type="date"
                  value={entryDate}
                  onChange={(e) => {
                    setEntryDate(e.target.value);
                    setDateTouched(true);
                  }}
                />
              </Field>
            )}
            <Field
              label={mode === "check-in" ? "In time" : "Out time"}
              required
            >
              <Input
                type="time"
                value={entryTime}
                onChange={(e) => {
                  setEntryTime(e.target.value);
                  setTimeTouched(true);
                }}
              />
            </Field>
          </div>

          <Button type="submit" variant="primary" disabled={pending}>
            {pending
              ? "Saving…"
              : mode === "check-in"
                ? "Check in"
                : "Check out"}
          </Button>
        </form>
      </div>

      <div className="mb-4 flex flex-wrap gap-3">
        <Input
          type="date"
          value={filterDate}
          onChange={(e) => {
            setFilterDate(e.target.value);
            setOffset(0);
          }}
          className="w-44"
        />
        <ExportMenu
          title="Digital library sessions"
          filename={`digital-library-records-${todayIso()}`}
          columns={EXPORT_COLUMNS}
          getRows={async () =>
            (await fetchAllDigitalLibrary(filterDate)).map(toExportRow)
          }
        />
      </div>

      {isLoading && <Spinner label="Loading sessions…" />}
      {isError && <ErrorBanner message={extractErrorMessage(error)} />}
      {data && data.length === 0 && (
        <EmptyState title="No digital library sessions match these filters" />
      )}

      {data && data.length > 0 && (
        <>
          <Table>
            <Thead>
              <Th>Student</Th>
              <Th>Date</Th>
              <Th>Platform</Th>
              <Th>Account</Th>
              <Th>In</Th>
              <Th>Out</Th>
              <Th>Duration</Th>
              <Th className="text-right">Actions</Th>
            </Thead>
            <tbody>
              {data.map((u) => {
                const inDate = u.in_time
                  ? new Date(`${u.date}T${u.in_time}`)
                  : null;
                const outDate = u.out_time
                  ? new Date(`${u.date}T${u.out_time}`)
                  : null;
                const effectiveOutDate = outDate ?? now;
                const inDisplay = inDate
                  ? inDate.toLocaleTimeString([], {
                      hour: "2-digit",
                      minute: "2-digit",
                      second: "2-digit",
                    })
                  : "—";
                const outDisplay = outDate
                  ? outDate.toLocaleTimeString([], {
                      hour: "2-digit",
                      minute: "2-digit",
                      second: "2-digit",
                    })
                  : inDate
                    ? now.toLocaleTimeString([], {
                        hour: "2-digit",
                        minute: "2-digit",
                        second: "2-digit",
                      })
                    : "—";
                const durationMinutes = inDate
                  ? Math.max(
                      0,
                      Math.round(
                        (effectiveOutDate.getTime() - inDate.getTime()) / 60000,
                      ),
                    )
                  : (u.duration_minutes ?? 0);

                return (
                  <Tr key={u.usage_id}>
                    <Td className="font-mono text-xs">{u.student_id}</Td>
                    <Td>{formatDate(u.date)}</Td>
                    <Td className="font-medium">{u.platform_name}</Td>
                    <Td className="text-slate">
                      {u.account_type === "Library Subscription"
                        ? u.subscription_id
                        : "Own account"}
                    </Td>
                    <Td className="font-mono text-xs">{inDisplay}</Td>
                    <Td className="font-mono text-xs">
                      {u.out_time ? (
                        outDisplay
                      ) : (
                        <span className="text-brass">{outDisplay}</span>
                      )}
                    </Td>
                    <Td className="text-slate">
                      {formatDuration(durationMinutes)}
                    </Td>
                    <Td className="text-right">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setDeleting(u)}
                      >
                        <Trash2 size={14} className="text-rust" />
                      </Button>
                    </Td>
                  </Tr>
                );
              })}
            </tbody>
          </Table>
          <Pagination
            offset={offset}
            limit={LIMIT}
            count={data.length}
            onOffsetChange={setOffset}
          />
        </>
      )}

      <ConfirmDialog
        open={Boolean(deleting)}
        onClose={() => setDeleting(undefined)}
        onConfirm={handleDelete}
        title="Delete session"
        message="This removes the digital library session permanently."
        pending={deleteMutation.isPending}
      />
    </div>
  );
}
