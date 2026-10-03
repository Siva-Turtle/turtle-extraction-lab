// Similarity-based agreement (pure functions, no React).
//
// similarity(a, b, hint?) -> number in [0,1]. Normalisation matches the loose
// rules in compare.ts normDeep (trim, collapse whitespace, lowercase).
// - both missing (null/undefined/""/not_found) -> 1; exactly one missing -> 0.
// - boolean, enum (hint "enum") and short strings (<= 6 words both sides):
//   exact after normalise -> 1 else 0.
// - numbers (or numeric strings): 1 when |a-b| <= 1% of max(|a|,|b|)
//   (both 0 -> 1), else 0.
// - long strings (> 6 words either side): token-set Jaccard.
// - arrays of scalars: Jaccard of normalised element sets (both empty -> 1).
// - arrays of objects: pair by key field (first of agent/name/id/key/label/
//   type present), else greedy best-match; sum(pair sims)/max(lenA,lenB).
// - objects: mean over union of keys of similarity(a[k], b[k]).
// - type mismatch: compare canonical strings exactly.
// rowAgreement(values, hint?) = mean pairwise similarity (1 when <2 values).
// peerAgreement(values, index, hint?) = mean similarity of values[index] to
// each other value (1 when <2 values).

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

/** Missing-value check shared by similarity (null/undefined/""/not_found). */
export function isMissingValue(v: unknown): boolean {
  if (v === null || v === undefined) return true;
  if (typeof v === "number") return Number.isNaN(v);
  if (typeof v === "string") {
    const t = v.trim();
    if (t === "") return true;
    const lower = t.toLowerCase();
    return lower === "not_found" || lower === "∅";
  }
  return false;
}

function normStr(s: string): string {
  return s.trim().replace(/\s+/g, " ").toLowerCase();
}

/** Recursively normalise the same loose way compare.ts normDeep does. */
function normDeep(v: unknown): unknown {
  if (v === null || v === undefined) return null;
  if (typeof v === "string") return normStr(v);
  if (typeof v === "number") return Number.isNaN(v) ? null : v;
  if (typeof v === "boolean") return v;
  if (Array.isArray(v)) {
    const mapped = v.map(normDeep);
    mapped.sort((a, b) => {
      const sa = JSON.stringify(a) ?? "";
      const sb = JSON.stringify(b) ?? "";
      return sa < sb ? -1 : sa > sb ? 1 : 0;
    });
    return mapped;
  }
  if (typeof v === "object") {
    const rec = v as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const k of Object.keys(rec).sort()) out[k] = normDeep(rec[k]);
    return out;
  }
  return String(v);
}

/** Canonical string for exact comparisons (same loose normalisation). */
function canonicalOf(v: unknown): string {
  try {
    // normDeep already lowercases strings; JSON keeps numbers/booleans distinct.
    const n = normDeep(v);
    // Unwrap the extra quotes for plain strings so "1" and 1 share "1".
    if (typeof n === "string") return n;
    return JSON.stringify(n) ?? String(v);
  } catch {
    return String(v);
  }
}

/** Canonical key for a single array element (numbers match numeric strings). */
function elementKey(v: unknown): string {
  if (v === null || v === undefined) return "∅";
  if (typeof v === "number") {
    if (Number.isNaN(v)) return "∅";
    return String(Number(v));
  }
  if (typeof v === "boolean") return String(v);
  if (typeof v === "string") {
    const t = normStr(v);
    return t === "" ? "∅" : t;
  }
  try {
    return JSON.stringify(normDeep(v)) ?? String(v);
  } catch {
    return String(v);
  }
}

function hintTypeOf(hint: unknown): string {
  if (typeof hint === "string") return hint.trim().toLowerCase();
  if (hint && typeof hint === "object" && !Array.isArray(hint)) {
    const t = (hint as Record<string, unknown>).type;
    if (typeof t === "string") return t.trim().toLowerCase();
  }
  return "";
}

function wordCount(s: string): number {
  const t = normStr(s);
  if (t === "") return 0;
  return t.split(" ").length;
}

function isNumericValue(v: unknown): boolean {
  if (typeof v === "number") return Number.isFinite(v);
  if (typeof v === "string") {
    const t = v.trim();
    if (t === "") return false;
    const n = Number(t);
    return Number.isFinite(n);
  }
  return false;
}

function toNumber(v: unknown): number {
  if (typeof v === "number") return v;
  return Number((v as string).trim());
}

function tokenSet(s: string): Set<string> {
  const lower = s.toLowerCase();
  // Punctuation stripped: non-alphanumerics become separators.
  const parts = lower
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .filter((w) => w !== "");
  return new Set(parts);
}

function jaccard(a: Set<string>, b: Set<string>): number {
  if (a.size === 0 && b.size === 0) return 1;
  let inter = 0;
  for (const x of a) if (b.has(x)) inter += 1;
  const union = a.size + b.size - inter;
  return union === 0 ? 1 : inter / union;
}

const OBJECT_ARRAY_KEYS = ["agent", "name", "id", "key", "label", "type"];

function keyValueNorm(v: unknown): string {
  return elementKey(v);
}

function findKeyField(a: unknown[], b: unknown[]): string | null {
  const all = [...a, ...b];
  if (all.length === 0) return null;
  for (const item of all) {
    if (!isPlainObject(item)) return null;
  }
  for (const cand of OBJECT_ARRAY_KEYS) {
    let ok = true;
    for (const item of all) {
      const rec = item as Record<string, unknown>;
      if (!(cand in rec)) {
        ok = false;
        break;
      }
      const kv = rec[cand];
      if (kv === null || kv === undefined) {
        ok = false;
        break;
      }
      if (typeof kv !== "string" && typeof kv !== "number" && typeof kv !== "boolean") {
        ok = false;
        break;
      }
      if (typeof kv === "string" && kv.trim() === "") {
        ok = false;
        break;
      }
    }
    if (ok) return cand;
  }
  return null;
}

function arrayObjectsSimilarity(a: unknown[], b: unknown[]): number {
  if (a.length === 0 && b.length === 0) return 1;
  const denom = Math.max(a.length, b.length);
  if (denom === 0) return 1;
  const keyField = findKeyField(a, b);
  if (keyField !== null) {
    const mapA = new Map<string, unknown>();
    const mapB = new Map<string, unknown>();
    let dup = false;
    for (const item of a) {
      const k = keyValueNorm((item as Record<string, unknown>)[keyField]);
      if (mapA.has(k)) {
        dup = true;
        break;
      }
      mapA.set(k, item);
    }
    if (!dup) {
      for (const item of b) {
        const k = keyValueNorm((item as Record<string, unknown>)[keyField]);
        if (mapB.has(k)) {
          dup = true;
          break;
        }
        mapB.set(k, item);
      }
    }
    if (!dup) {
      let sum = 0;
      for (const [k, itemA] of mapA) {
        const itemB = mapB.get(k);
        if (itemB !== undefined) sum += similarity(itemA, itemB);
      }
      return sum / denom;
    }
  }
  // Greedy best-match: highest-similarity disjoint pairs first.
  const pairs: { i: number; j: number; s: number }[] = [];
  for (let i = 0; i < a.length; i += 1) {
    for (let j = 0; j < b.length; j += 1) {
      pairs.push({ i, j, s: similarity(a[i], b[j]) });
    }
  }
  pairs.sort((p, q) => q.s - p.s);
  const usedA = new Set<number>();
  const usedB = new Set<number>();
  let sum = 0;
  for (const p of pairs) {
    if (usedA.has(p.i) || usedB.has(p.j)) continue;
    // Remaining pairs are all <= current; stop early when nothing to gain.
    if (p.s <= 0) break;
    usedA.add(p.i);
    usedB.add(p.j);
    sum += p.s;
  }
  return sum / denom;
}

/**
 * Similarity in [0,1] between two cell values. `hint` is the attribute type
 * (e.g. "enum") or an object with a `type` field; only "enum" changes
 * behaviour (exact match after normalisation).
 */
export function similarity(a: unknown, b: unknown, hint?: unknown): number {
  const aMissing = isMissingValue(a);
  const bMissing = isMissingValue(b);
  if (aMissing && bMissing) return 1;
  if (aMissing || bMissing) return 0;

  const hintType = hintTypeOf(hint);
  if (hintType === "enum") {
    return canonicalOf(a) === canonicalOf(b) ? 1 : 0;
  }

  if (typeof a === "boolean" && typeof b === "boolean") {
    return a === b ? 1 : 0;
  }

  if (isNumericValue(a) && isNumericValue(b)) {
    const na = toNumber(a);
    const nb = toNumber(b);
    if (!Number.isFinite(na) || !Number.isFinite(nb)) {
      return canonicalOf(a) === canonicalOf(b) ? 1 : 0;
    }
    if (na === 0 && nb === 0) return 1;
    const diff = Math.abs(na - nb);
    const tol = 0.01 * Math.max(Math.abs(na), Math.abs(nb));
    return diff <= tol ? 1 : 0;
  }

  if (typeof a === "string" && typeof b === "string") {
    const wa = wordCount(a);
    const wb = wordCount(b);
    if (wa <= 6 && wb <= 6) {
      return normStr(a) === normStr(b) ? 1 : 0;
    }
    return jaccard(tokenSet(a), tokenSet(b));
  }

  if (Array.isArray(a) && Array.isArray(b)) {
    const hasObject = [...a, ...b].some((x) => isPlainObject(x));
    if (hasObject) return arrayObjectsSimilarity(a, b);
    const setA = new Set(a.map(elementKey));
    const setB = new Set(b.map(elementKey));
    return jaccard(setA, setB);
  }

  if (isPlainObject(a) && isPlainObject(b)) {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    if (keys.size === 0) return 1;
    let sum = 0;
    for (const k of keys) {
      const va = k in a ? (a as Record<string, unknown>)[k] : undefined;
      const vb = k in b ? (b as Record<string, unknown>)[k] : undefined;
      sum += similarity(va, vb);
    }
    return sum / keys.size;
  }

  return canonicalOf(a) === canonicalOf(b) ? 1 : 0;
}

/** Mean pairwise similarity (1 when fewer than 2 values). */
export function rowAgreement(values: unknown[], hint?: unknown): number {
  if (values.length < 2) return 1;
  let sum = 0;
  let n = 0;
  for (let i = 0; i < values.length; i += 1) {
    for (let j = i + 1; j < values.length; j += 1) {
      sum += similarity(values[i], values[j], hint);
      n += 1;
    }
  }
  return n === 0 ? 1 : sum / n;
}

/** Mean similarity of values[index] to each other value (1 when <2 values). */
export function peerAgreement(values: unknown[], index: number, hint?: unknown): number {
  if (values.length < 2) return 1;
  if (index < 0 || index >= values.length) return 0;
  const self = values[index];
  let sum = 0;
  let n = 0;
  for (let j = 0; j < values.length; j += 1) {
    if (j === index) continue;
    sum += similarity(self, values[j], hint);
    n += 1;
  }
  return n === 0 ? 1 : sum / n;
}
