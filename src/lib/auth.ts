// Session auth: scrypt password hashing + DB-backed session tokens.
// Production hardening: HttpOnly SameSite cookie, 7-day expiry, timing-safe
// verification, no secrets in responses. Set COOKIE_SECURE=1 when serving
// over HTTPS (recommended); it stays off for plain-HTTP local deployments.
import { randomBytes, scryptSync, timingSafeEqual } from "crypto";
import { db } from "@/lib/db";

export const SESSION_COOKIE = "rg_session";
const SESSION_DAYS = 7;

export function hashPassword(password: string): string {
  const salt = randomBytes(16).toString("hex");
  const hash = scryptSync(password, salt, 64).toString("hex");
  return `scrypt:64:${salt}:${hash}`;
}

export function verifyPassword(password: string, stored: string): boolean {
  try {
    const [scheme, n, salt, hash] = stored.split(":");
    if (scheme !== "scrypt") return false;
    const candidate = scryptSync(password, salt, Number(n) || 64);
    const expected = Buffer.from(hash, "hex");
    return (
      candidate.length === expected.length && timingSafeEqual(candidate, expected)
    );
  } catch {
    return false;
  }
}

export type SafeUser = { id: string; email: string; name: string };

export async function createSession(userId: string): Promise<{
  token: string;
  expiresAt: Date;
}> {
  const token = randomBytes(32).toString("hex");
  const expiresAt = new Date(Date.now() + SESSION_DAYS * 24 * 3600 * 1000);
  await db.session.create({ data: { token, userId, expiresAt } });
  return { token, expiresAt };
}

export async function getUserByToken(
  token: string | undefined,
): Promise<SafeUser | null> {
  if (!token) return null;
  const session = await db.session.findUnique({
    where: { token },
    include: { user: true },
  });
  if (!session) return null;
  if (session.expiresAt.getTime() < Date.now()) {
    await db.session.delete({ where: { id: session.id } }).catch(() => {});
    return null;
  }
  return { id: session.user.id, email: session.user.email, name: session.user.name };
}

export function sessionCookie(token: string, expiresAt: Date) {
  return {
    name: SESSION_COOKIE,
    value: token,
    httpOnly: true,
    sameSite: "lax" as const,
    secure:
      process.env.NODE_ENV === "production" &&
      process.env.COOKIE_SECURE !== "0",
    path: "/",
    expires: expiresAt,
  };
}

export function clearedSessionCookie() {
  return {
    name: SESSION_COOKIE,
    value: "",
    httpOnly: true,
    sameSite: "lax" as const,
    secure:
      process.env.NODE_ENV === "production" &&
      process.env.COOKIE_SECURE !== "0",
    path: "/",
    maxAge: 0,
  };
}
