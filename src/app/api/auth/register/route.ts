import { NextResponse } from "next/server";
import { db } from "@/lib/db";
import { createSession, hashPassword, sessionCookie } from "@/lib/auth";
import { clientIp, rateLimit, RULES } from "@/lib/rate-limit";

export async function POST(req: Request) {
  const rl = rateLimit(`register:${clientIp(req)}`, RULES.auth);
  if (!rl.ok) {
    return NextResponse.json(
      { error: "Too many attempts. Try later." },
      { status: 429, headers: { "Retry-After": String(rl.retryAfterS) } },
    );
  }
  const body = await req.json().catch(() => null);
  const email = String(body?.email ?? "").trim().toLowerCase();
  const name = String(body?.name ?? "").trim();
  const password = String(body?.password ?? "");
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return NextResponse.json({ error: "Enter a valid email." }, { status: 400 });
  }
  if (name.length < 2 || name.length > 60) {
    return NextResponse.json({ error: "Name must be 2-60 chars." }, { status: 400 });
  }
  if (password.length < 8 || password.length > 128) {
    return NextResponse.json(
      { error: "Password must be at least 8 characters." },
      { status: 400 },
    );
  }
  const exists = await db.user.findUnique({ where: { email } });
  if (exists) {
    return NextResponse.json(
      { error: "An account with this email already exists." },
      { status: 409 },
    );
  }
  const user = await db.user.create({
    data: { email, name, passwordHash: hashPassword(password) },
  });
  const session = await createSession(user.id);
  const res = NextResponse.json({
    user: { id: user.id, email: user.email, name: user.name },
  });
  res.cookies.set(sessionCookie(session.token, session.expiresAt));
  return res;
}
