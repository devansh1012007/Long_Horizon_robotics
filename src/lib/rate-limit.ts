// Token-bucket rate limiter (per key, in-memory).
// Bucket lives on globalThis so hot reload does not reset limits.
type Bucket = { tokens: number; last: number };

const g = globalThis as unknown as { __rgRateBuckets?: Map<string, Bucket> };
const buckets: Map<string, Bucket> = (g.__rgRateBuckets ??= new Map());

export type RateRule = { capacity: number; refillPerSec: number };

export const RULES = {
  auth: { capacity: 15, refillPerSec: 15 / 300 }, // 15 per 5 min
  submit: { capacity: 20, refillPerSec: 20 / 60 }, // 20 per min
  read: { capacity: 240, refillPerSec: 240 / 60 }, // 240 per min
  heavy: { capacity: 60, refillPerSec: 60 / 60 }, // 60 per min
} satisfies Record<string, RateRule>;

export function rateLimit(
  key: string,
  rule: RateRule,
): { ok: boolean; retryAfterS: number; remaining: number } {
  const now = Date.now();
  const b = buckets.get(key) ?? { tokens: rule.capacity, last: now };
  const elapsed = (now - b.last) / 1000;
  b.tokens = Math.min(rule.capacity, b.tokens + elapsed * rule.refillPerSec);
  b.last = now;
  if (b.tokens < 1) {
    buckets.set(key, b);
    return {
      ok: false,
      retryAfterS: Math.ceil((1 - b.tokens) / rule.refillPerSec),
      remaining: 0,
    };
  }
  b.tokens -= 1;
  buckets.set(key, b);
  return { ok: true, retryAfterS: 0, remaining: Math.floor(b.tokens) };
}

export function clientIp(req: Request): string {
  const fwd = req.headers.get("x-forwarded-for");
  if (fwd) return fwd.split(",")[0].trim();
  return "local";
}
