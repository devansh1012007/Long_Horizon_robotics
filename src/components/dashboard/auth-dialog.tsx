"use client";

import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { useDashboard } from "./store";
import { Loader2, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

export function AuthDialog() {
  const { authOpen, setAuthOpen, setUser } = useDashboard();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    try {
      const res = await fetch(`/api/auth/${mode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          mode === "login" ? { email, password } : { email, name, password },
        ),
      });
      const data = await res.json();
      if (!res.ok) {
        toast.error(data.error ?? "Authentication failed.");
        return;
      }
      setUser(data.user);
      toast.success(
        mode === "login"
          ? `Welcome back, ${data.user.name}.`
          : `Account created. Welcome, ${data.user.name}.`,
      );
      setAuthOpen(false);
      setPassword("");
    } catch {
      toast.error("Network error — is the server running?");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={authOpen} onOpenChange={setAuthOpen}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-emerald-600" />
            {mode === "login" ? "Sign in" : "Create operator account"}
          </DialogTitle>
          <DialogDescription>
            Sessions are HttpOnly and expire after 7 days. Each operator gets a
            private job quota and episode history.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          {mode === "register" && (
            <div className="space-y-1.5">
              <Label htmlFor="auth-name">Name</Label>
              <Input
                id="auth-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Ada Lovelace"
                autoComplete="name"
              />
            </div>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="auth-email">Email</Label>
            <Input
              id="auth-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="operator@example.com"
              autoComplete="email"
              onKeyDown={(e) => e.key === "Enter" && submit()}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="auth-password">Password</Label>
            <Input
              id="auth-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="At least 8 characters"
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              onKeyDown={(e) => e.key === "Enter" && submit()}
            />
          </div>
          <Button
            className="w-full bg-emerald-600 hover:bg-emerald-700 text-white"
            onClick={submit}
            disabled={busy || !email || !password}
          >
            {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {mode === "login" ? "Sign in" : "Create account"}
          </Button>
          <div className="text-center text-sm">
            <button
              className="text-muted-foreground underline-offset-4 hover:underline"
              onClick={() => setMode(mode === "login" ? "register" : "login")}
            >
              {mode === "login"
                ? "Need an account? Register"
                : "Already have an account? Sign in"}
            </button>
          </div>
          <Separator />
          <div className="rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
            <span className="font-medium text-foreground">Demo operator:</span>{" "}
            demo@recovergraph.dev / demo1234 — sign in and press
            &quot;Load demo&quot; in the Control Room to seed a first comparison
            batch.
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
