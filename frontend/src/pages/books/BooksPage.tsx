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
import { Input } from "../../components/ui/Form";
import { Button } from "../../components/ui/Button";
import { IdTab } from "../../components/ui/Tabs";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Modal } from "../../components/ui/Modal";
import { Field } from "../../components/ui/Form";
import {
  useBooks,
  useCreateBook,
  useUpdateBook,
  useDeleteBook,
} from "../../api/books";
import { extractErrorMessage } from "../../api/client";
import { formatDate, todayIso } from "../../lib/format";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import type { Book } from "../../api/types";

const LIMIT = 20;

export function BooksPage() {
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Book | undefined>(undefined);
  const [deleting, setDeleting] = useState<Book | undefined>(undefined);

  const debouncedSearch = useDebouncedValue(search);
  const { data, isLoading, isError, error } = useBooks({
    search: debouncedSearch || undefined,
    limit: LIMIT,
    offset,
  });
  const deleteMutation = useDeleteBook();

  async function handleDelete() {
    if (!deleting) return;
    try {
      await deleteMutation.mutateAsync(deleting.book_id);
      toast.success(`Removed "${deleting.title}"`);
      setDeleting(undefined);
    } catch (err) {
      toast.error(extractErrorMessage(err));
    }
  }

  return (
    <div>
      <PageHeader
        eyebrow="Library"
        title="Books catalog"
        description="The reference catalog of books available on-site."
        action={
          <Button
            variant="primary"
            onClick={() => {
              setEditing(undefined);
              setFormOpen(true);
            }}
          >
            <Plus size={16} /> Add book
          </Button>
        }
      />

      <div className="relative mb-4 max-w-sm">
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
          placeholder="Search by title…"
          className="pl-9"
        />
      </div>

      {isLoading && <Spinner label="Loading books…" />}
      {isError && <ErrorBanner message={extractErrorMessage(error)} />}
      {data && data.length === 0 && <EmptyState title="No books found" />}

      {data && data.length > 0 && (
        <>
          <Table>
            <Thead>
              <Th>Book</Th>
              <Th>Category</Th>
              <Th>Author</Th>
              <Th>Added</Th>
              <Th className="text-right">Actions</Th>
            </Thead>
            <tbody>
              {data.map((b) => (
                <Tr key={b.book_id}>
                  <Td>
                    <div className="flex items-center gap-2.5">
                      <IdTab>{b.book_id}</IdTab>
                      <span className="font-medium">{b.title}</span>
                    </div>
                  </Td>
                  <Td className="text-slate">{b.category ?? "—"}</Td>
                  <Td className="text-slate">{b.author ?? "—"}</Td>
                  <Td className="text-slate">{formatDate(b.added_date)}</Td>
                  <Td>
                    <div className="flex justify-end gap-1">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setEditing(b);
                          setFormOpen(true);
                        }}
                      >
                        <Pencil size={14} />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setDeleting(b)}
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

      <BookFormModal
        open={formOpen}
        onClose={() => setFormOpen(false)}
        book={editing}
      />

      <ConfirmDialog
        open={Boolean(deleting)}
        onClose={() => setDeleting(undefined)}
        onConfirm={handleDelete}
        title="Delete book"
        message={`Delete "${deleting?.title}"? This fails if the book has offline-library history attached.`}
        pending={deleteMutation.isPending}
      />
    </div>
  );
}

function BookFormModal({
  open,
  onClose,
  book,
}: {
  open: boolean;
  onClose: () => void;
  book?: Book;
}) {
  const isEdit = Boolean(book);
  const [bookId, setBookId] = useState(book?.book_id ?? "");
  const [title, setTitle] = useState(book?.title ?? "");
  const [category, setCategory] = useState(book?.category ?? "");
  const [author, setAuthor] = useState(book?.author ?? "");
  const [addedDate, setAddedDate] = useState(book?.added_date ?? todayIso());
  const [error, setError] = useState("");

  const createMutation = useCreateBook();
  const updateMutation = useUpdateBook(book?.book_id ?? "");
  const pending = createMutation.isPending || updateMutation.isPending;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!isEdit && !bookId.trim()) {
      setError("Book ID is required.");
      return;
    }
    if (!title.trim()) {
      setError("Title is required.");
      return;
    }
    try {
      if (isEdit && book) {
        await updateMutation.mutateAsync({
          title,
          category: category || null,
          author: author || null,
          added_date: addedDate || null,
        });
        toast.success(`Saved "${title}"`);
      } else {
        await createMutation.mutateAsync({
          book_id: bookId,
          title,
          category: category || null,
          author: author || null,
          added_date: addedDate || null,
        });
        toast.success(`Added "${title}" to the catalog`);
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
      title={isEdit ? "Edit book" : "Add book"}
      width="md"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Book ID" required>
            <Input
              value={bookId}
              onChange={(e) => setBookId(e.target.value)}
              disabled={isEdit}
              placeholder="e.g. 1556"
            />
          </Field>
          <Field label="Added date">
            <Input
              type="date"
              value={addedDate ?? ""}
              onChange={(e) => setAddedDate(e.target.value)}
            />
          </Field>
        </div>
        <Field label="Title" required>
          <Input value={title} onChange={(e) => setTitle(e.target.value)} />
        </Field>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Category">
            <Input
              value={category ?? ""}
              onChange={(e) => setCategory(e.target.value)}
              placeholder="e.g. Polity"
            />
          </Field>
          <Field label="Author">
            <Input
              value={author ?? ""}
              onChange={(e) => setAuthor(e.target.value)}
            />
          </Field>
        </div>
        {error && <p className="text-sm text-rust">{error}</p>}
        <div className="flex justify-end gap-2 border-t border-border pt-4">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={pending}>
            {pending ? "Saving…" : isEdit ? "Save changes" : "Add book"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
