export function parseSeq(raw, { min = 1, max = 1, fallback = 1 } = {}) {
  const text = String(raw ?? "").trim();
  const hi = Math.max(min, Number(max) || min);
  if (text === "" || text === "-" || text === "0") return fallback;
  const n = Number(text);
  if (!Number.isFinite(n)) return fallback;
  const v = Math.trunc(n);
  if (v < min) return min;
  if (v > hi) return hi;
  return v;
}

export function commitSeqRange(fromRaw, toRaw, maxSeq) {
  const hi = Math.max(1, Number(maxSeq) || 1);
  let from = parseSeq(fromRaw, { min: 1, max: hi, fallback: 1 });
  let to = parseSeq(toRaw, { min: 1, max: hi, fallback: hi });
  if (from > to) {
    const swap = from;
    from = to;
    to = swap;
  }
  return { from, to };
}
