import { NextResponse } from "next/server";
import { db } from "@/lib/db";
import { createSession, sessionCookie, verifyPassword } from "@/lib/auth";
import { clientIp, rateLimit, RULES } from "@/lib/rate-limit";

export async function POST(req: Request) {
  const rl = rateLimit(`login:${clientIp(req)}`, RULES.auth);
  if (!rl.ok) {
    return NextResponse.json(
      { error: "Too many attempts. Try later." },
      { status: 429, headers: { "Retry-After": String(rl.retryAfterS) } },
    );
  }
  const body = await req.json().catch(() => null);
  const email = String(body?.email ?? "").trim().toLowerCase();
  const password = String(body?.password ?? "");
  const user = await db.user.findUnique({ where: { email } });
  if (!user || !verifyPassword(password, user.passwordHash)) {
    return NextResponse.json(
      { error: "Invalid email or password." },
      { status: 401 },
    );
  }
  const session = await createSession(user.id);
  const res = NextResponse.json({
    user: { id: user.id, email: user.email, name: user.name },
  });
  res.cookies.set(sessionCookie(session.token, session.expiresAt));
  return res;
}
