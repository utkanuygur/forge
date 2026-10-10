import { useRef, useState } from "react";
import { Paperclip, Play, X } from "lucide-react";
import { api, errMsg, type Run, type Version } from "../api";
import { ErrorBox } from "./ui";

export default function RunForm({
  slug,
  share,
  versions,
  prodVersion,
  onCreated,
  placeholder = "Describe the task for the agent...",
}: {
  slug: string;
  share?: string;
  versions?: Version[];
  prodVersion?: number;
  onCreated: (run: Run) => void;
  placeholder?: string;
}) {
  const [input, setInput] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [version, setVersion] = useState<string>("prod");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!input.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const run = await api.createRun(
        slug,
        { input, files, version: version === "prod" ? undefined : Number(version) },
        share,
      );
      onCreated(run);
    } catch (err) {
      setError(errMsg(err));
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      <textarea
        className="input min-h-[110px]"
        placeholder={placeholder}
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit(e);
        }}
      />
      {files.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {files.map((f, i) => (
            <span
              key={i}
              className="inline-flex items-center gap-1 rounded-md bg-neutral-100 px-2 py-1 text-xs dark:bg-neutral-800"
            >
              {f.name}
              <button type="button" onClick={() => setFiles(files.filter((_, j) => j !== i))} aria-label={`Remove ${f.name}`}>
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <input
          ref={fileRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            setFiles([...files, ...Array.from(e.target.files || [])]);
            e.target.value = "";
          }}
        />
        <button type="button" className="btn-secondary" onClick={() => fileRef.current?.click()}>
          <Paperclip className="h-4 w-4" /> Attach files
        </button>
        {versions && versions.length > 0 && (
          <select className="input w-auto" value={version} onChange={(e) => setVersion(e.target.value)} aria-label="Version">
            <option value="prod">Production (v{prodVersion})</option>
            {[...versions]
              .sort((a, b) => b.n - a.n)
              .map((v) => (
                <option key={v.id} value={v.n}>
                  v{v.n}
                  {v.n === prodVersion ? " (prod)" : ""}
                </option>
              ))}
          </select>
        )}
        <div className="flex-1" />
        <button type="submit" className="btn-primary" disabled={busy || !input.trim()}>
          <Play className="h-4 w-4" /> {busy ? "Starting..." : "Run"}
        </button>
      </div>
      <p className="text-xs text-neutral-500 dark:text-neutral-400">
        Files are placed in the workspace by filename; a single .tar.gz is extracted.
      </p>
      <ErrorBox error={error} />
    </form>
  );
}
