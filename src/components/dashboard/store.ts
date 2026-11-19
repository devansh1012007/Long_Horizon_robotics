import { create } from "zustand";

export type TabId =
  | "control"
  | "live"
  | "leaderboard"
  | "episodes"
  | "ops";

type DashboardState = {
  tab: TabId;
  setTab: (t: TabId) => void;
  user: { id: string; email: string; name: string } | null;
  setUser: (u: DashboardState["user"]) => void;
  activeJobId: string | null;
  setActiveJobId: (id: string | null) => void;
  authOpen: boolean;
  setAuthOpen: (open: boolean) => void;
};

export const useDashboard = create<DashboardState>((set) => ({
  tab: "control",
  setTab: (tab) => set({ tab }),
  user: null,
  setUser: (user) => set({ user }),
  activeJobId: null,
  setActiveJobId: (activeJobId) => set({ activeJobId }),
  authOpen: false,
  setAuthOpen: (authOpen) => set({ authOpen }),
}));
