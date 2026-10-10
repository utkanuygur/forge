/**
 * A line diff of two texts in the unified style DiffView understands: a `@@` header, then one line per line of
 * either text prefixed with " " (unchanged), "-" (removed) or "+" (added). Uses a longest common subsequence,
 * which is fine for texts of a few thousand lines (instructions, notes).
 */
export function lineDiff(before: string, after: string, header = "@@ instructions @@"): string {
  const a = before.split("\n");
  const b = after.split("\n");
  const n = a.length;
  const m = b.length;
  // lcs[i][j] = LCS length of a[i:] and b[j:]
  const lcs: Uint32Array[] = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const out = [header];
  let i = 0;
  let j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && a[i] === b[j]) {
      out.push(" " + a[i]);
      i++;
      j++;
    } else if (j < m && (i >= n || lcs[i][j + 1] >= lcs[i + 1][j])) {
      out.push("+" + b[j]);
      j++;
    } else {
      out.push("-" + a[i]);
      i++;
    }
  }
  return out.join("\n");
}

/** Counts of added and removed lines in a lineDiff result. */
export function diffStats(diff: string): { added: number; removed: number } {
  const lines = diff.split("\n").slice(1);
  return { added: lines.filter((l) => l.startsWith("+")).length, removed: lines.filter((l) => l.startsWith("-")).length };
}
