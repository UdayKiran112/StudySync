import { useState } from "react";
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
import { extractErrorMessage } from "../../api/client";
import {
  formatDate,
  formatDuration,
  todayIso,
  nowHHMM,
} from "../../lib/format";
import type {
  Student,
  AccountType,
  DigitalLibraryUsage,
} from "../../api/types";

const LIMIT = 20;

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
                  onChange={(e) => setEntryDate(e.target.value)}
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
                onChange={(e) => setEntryTime(e.target.value)}
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
              {data.map((u) => (
                <Tr key={u.usage_id}>
                  <Td className="font-mono text-xs">{u.student_id}</Td>
                  <Td>{formatDate(u.date)}</Td>
                  <Td className="font-medium">{u.platform_name}</Td>
                  <Td className="text-slate">
                    {u.account_type === "Library Subscription"
                      ? u.subscription_id
                      : "Own account"}
                  </Td>
                  <Td className="font-mono text-xs">{u.in_time}</Td>
                  <Td className="font-mono text-xs">
                    {u.out_time ?? <span className="text-brass">Still in</span>}
                  </Td>
                  <Td className="text-slate">
                    {formatDuration(u.duration_minutes)}
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
              ))}
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
