import { NextResponse } from "next/server";
import { db } from "@/lib/db";
import { clearedSessionCookie, SESSION_COOKIE } from "@/lib/auth";

export async function POST(req: Request) {
  const cookie = req.headers
    .get("cookie")
    ?.split(";")
    .map((c) => c.trim())
    .find((c) => c.startsWith(`${SESSION_COOKIE}=`));
  const token = cookie?.split("=")[1];
  if (token) {
    await db.session.deleteMany({ where: { token } });
  }
  const res = NextResponse.json({ ok: true });
  res.cookies.set(clearedSessionCookie());
  return res;
}
