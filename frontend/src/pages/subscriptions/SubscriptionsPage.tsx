import { useState } from "react";
import toast from "react-hot-toast";
import { Plus, Search, Pencil, Trash2 } from "lucide-react";
import {
  PageHeader,
  Spinner,
  ErrorBanner,
  EmptyState,
  Pagination,
} from "../../components/ui/Feedback";
import { Table, Thead, Th, Tr, Td } from "../../components/ui/Table";
import { Input, Select, Field } from "../../components/ui/Form";
import { Button } from "../../components/ui/Button";
import {
  IdTab,
  StatusTab,
  subscriptionStatusTone,
} from "../../components/ui/Tabs";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Modal } from "../../components/ui/Modal";
import {
  useSubscriptions,
  useCreateSubscription,
  useUpdateSubscription,
  useDeleteSubscription,
} from "../../api/subscriptions";
import { extractErrorMessage } from "../../api/client";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import type { Subscription } from "../../api/types";

const LIMIT = 20;

export function SubscriptionsPage() {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Subscription | undefined>(undefined);
  const [deleting, setDeleting] = useState<Subscription | undefined>(undefined);

  const debouncedSearch = useDebouncedValue(search);
  const { data, isLoading, isError, error } = useSubscriptions({
    search: debouncedSearch || undefined,
    status: status || undefined,
    limit: LIMIT,
    offset,
  });
  const deleteMutation = useDeleteSubscription();

  async function handleDelete() {
    if (!deleting) return;
    try {
      await deleteMutation.mutateAsync(deleting.subscription_id);
      toast.success(`Removed ${deleting.name}`);
      setDeleting(undefined);
    } catch (err) {
      toast.error(extractErrorMessage(err));
    }
  }

  return (
    <div>
      <PageHeader
        eyebrow="Library"
        title="Subscriptions"
        description="The catalog of library-owned digital resources, like JSTOR or Britannica Online."
        action={
          <Button
            variant="primary"
            onClick={() => {
              setEditing(undefined);
              setFormOpen(true);
            }}
          >
            <Plus size={16} /> Add subscription
          </Button>
        }
      />

      <div className="mb-4 flex flex-wrap gap-3">
        <div className="relative flex-1 min-w-[220px]">
          <Search
            size={15}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-light"
          />
          <Input
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setOffset(0);
            }}
            placeholder="Search by name…"
            className="pl-9"
          />
        </div>
        <Select
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setOffset(0);
          }}
          className="w-40"
        >
          <option value="">All statuses</option>
          <option value="Active">Active</option>
          <option value="Expired">Expired</option>
        </Select>
      </div>

      {isLoading && <Spinner label="Loading subscriptions…" />}
      {isError && <ErrorBanner message={extractErrorMessage(error)} />}
      {data && data.length === 0 && (
        <EmptyState title="No subscriptions found" />
      )}

      {data && data.length > 0 && (
        <>
          <Table>
            <Thead>
              <Th>Subscription</Th>
              <Th>Type</Th>
              <Th>Cost</Th>
              <Th>Validity</Th>
              <Th>Status</Th>
              <Th className="text-right">Actions</Th>
            </Thead>
            <tbody>
              {data.map((s) => (
                <Tr key={s.subscription_id}>
                  <Td>
                    <div className="flex items-center gap-2.5">
                      <IdTab>{s.subscription_id}</IdTab>
                      <span className="font-medium">{s.name}</span>
                    </div>
                  </Td>
                  <Td className="text-slate">{s.type ?? "—"}</Td>
                  <Td className="text-slate">
                    {s.cost != null ? `₹${s.cost}` : "—"}
                  </Td>
                  <Td className="text-slate">
                    {s.validity_days != null ? `${s.validity_days} days` : "—"}
                  </Td>
                  <Td>
                    <StatusTab tone={subscriptionStatusTone(s.status)}>
                      {s.status}
                    </StatusTab>
                  </Td>
                  <Td>
                    <div className="flex justify-end gap-1">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setEditing(s);
                          setFormOpen(true);
                        }}
                      >
                        <Pencil size={14} />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setDeleting(s)}
                      >
                        <Trash2 size={14} className="text-rust" />
                      </Button>
                    </div>
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

      <SubscriptionFormModal
        open={formOpen}
        onClose={() => setFormOpen(false)}
        subscription={editing}
      />

      <ConfirmDialog
        open={Boolean(deleting)}
        onClose={() => setDeleting(undefined)}
        onConfirm={handleDelete}
        title="Delete subscription"
        message={`Delete ${deleting?.name}? This fails if any digital library sessions reference it.`}
        pending={deleteMutation.isPending}
      />
    </div>
  );
}

function SubscriptionFormModal({
  open,
  onClose,
  subscription,
}: {
  open: boolean;
  onClose: () => void;
  subscription?: Subscription;
}) {
  const isEdit = Boolean(subscription);
  const [subscriptionId, setSubscriptionId] = useState(
    subscription?.subscription_id ?? "",
  );
  const [name, setName] = useState(subscription?.name ?? "");
  const [type, setType] = useState(subscription?.type ?? "");
  const [cost, setCost] = useState(
    subscription?.cost != null ? String(subscription.cost) : "",
  );
  const [validityDays, setValidityDays] = useState(
    subscription?.validity_days != null
      ? String(subscription.validity_days)
      : "",
  );
  const [status, setStatus] = useState<string>(
    subscription?.status ?? "Active",
  );
  const [error, setError] = useState("");

  const createMutation = useCreateSubscription();
  const updateMutation = useUpdateSubscription(
    subscription?.subscription_id ?? "",
  );
  const pending = createMutation.isPending || updateMutation.isPending;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!isEdit && !subscriptionId.trim()) {
      setError("Subscription ID is required.");
      return;
    }
    if (!name.trim()) {
      setError("Name is required.");
      return;
    }
    try {
      if (isEdit && subscription) {
        await updateMutation.mutateAsync({
          name,
          type: type || null,
          cost: cost ? Number(cost) : null,
          validity_days: validityDays ? Number(validityDays) : null,
          status: status as Subscription["status"],
        });
        toast.success(`Saved ${name}`);
      } else {
        await createMutation.mutateAsync({
          subscription_id: subscriptionId,
          name,
          type: type || null,
          cost: cost ? Number(cost) : null,
          validity_days: validityDays ? Number(validityDays) : null,
          status: status as Subscription["status"],
        });
        toast.success(`Added ${name}`);
      }
      onClose();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={isEdit ? "Edit subscription" : "Add subscription"}
      width="md"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Subscription ID" required>
            <Input
              value={subscriptionId}
              onChange={(e) => setSubscriptionId(e.target.value)}
              disabled={isEdit}
              placeholder="e.g. SUB001"
            />
          </Field>
          <Field label="Status" required>
            <Select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="Active">Active</option>
              <option value="Expired">Expired</option>
            </Select>
          </Field>
        </div>
        <Field label="Name" required>
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. JSTOR"
          />
        </Field>
        <Field label="Type">
          <Input
            value={type ?? ""}
            onChange={(e) => setType(e.target.value)}
            placeholder="e.g. Online Learning"
          />
        </Field>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Cost">
            <Input
              type="number"
              min="0"
              step="0.01"
              value={cost}
              onChange={(e) => setCost(e.target.value)}
            />
          </Field>
          <Field label="Validity (days)">
            <Input
              type="number"
              min="1"
              value={validityDays}
              onChange={(e) => setValidityDays(e.target.value)}
            />
          </Field>
        </div>
        {error && <p className="text-sm text-rust">{error}</p>}
        <div className="flex justify-end gap-2 border-t border-border pt-4">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={pending}>
            {pending ? "Saving…" : isEdit ? "Save changes" : "Add subscription"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
