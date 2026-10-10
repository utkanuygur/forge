import { useState } from "react";
import { ThumbsDown, ThumbsUp } from "lucide-react";
import { api, errMsg, type Feedback } from "../api";
import { ErrorBox } from "./ui";

export default function FeedbackWidget({
  runId,
  share,
  existing = [],
  onSaved,
}: {
  runId: string;
  share?: string;
  existing?: Feedback[];
  onSaved?: (fb: Feedback) => void;
}) {
  const [rating, setRating] = useState<1 | -1 | null>(null);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<Feedback[]>(existing);

  async function submit() {
    if (!rating) return;
    setBusy(true);
    setError(null);
    try {
      const fb = await api.feedback(runId, rating, comment, share);
      setSaved((xs) => [...xs, fb]);
      setRating(null);
      setComment("");
      onSaved?.(fb);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  const btn = (r: 1 | -1) =>
    `btn-secondary ${
      rating === r
        ? r === 1
          ? "!border-emerald-400 !bg-emerald-50 text-emerald-700 dark:!bg-emerald-950 dark:text-emerald-300"
          : "!border-red-400 !bg-red-50 text-red-700 dark:!bg-red-950 dark:text-red-300"
        : ""
    }`;

  return (
    <div className="space-y-3">
      {saved.length > 0 && (
        <ul className="space-y-1.5">
          {saved.map((f) => (
            <li key={f.id} className="flex items-start gap-2 text-sm">
              {f.rating === 1 ? (
                <ThumbsUp className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
              ) : (
                <ThumbsDown className="mt-0.5 h-4 w-4 shrink-0 text-red-500" />
              )}
              <span className="text-neutral-700 dark:text-neutral-300">{f.comment || <em className="text-neutral-400">No comment</em>}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="flex gap-2">
        <button className={btn(1)} onClick={() => setRating(1)} aria-pressed={rating === 1}>
          <ThumbsUp className="h-4 w-4" /> Good
        </button>
        <button className={btn(-1)} onClick={() => setRating(-1)} aria-pressed={rating === -1}>
          <ThumbsDown className="h-4 w-4" /> Needs work
        </button>
      </div>
      {rating && (
        <div className="space-y-2">
          <textarea
            aria-label="Feedback comment"
            className="input min-h-[72px]"
            placeholder={rating === -1 ? "What should it have done differently?" : "Anything worth noting? (optional)"}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
          />
          <button className="btn-primary" onClick={submit} disabled={busy}>
            {busy ? "Sending..." : "Send feedback"}
          </button>
        </div>
      )}
      <ErrorBox error={error} />
    </div>
  );
}
