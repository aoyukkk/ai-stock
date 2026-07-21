import { apiGet, apiPost } from "@/api/http";

export type InternalRole = "ADMIN" | "TRADER" | "VIEWER";

export interface InternalIdentity {
  id: number;
  email: string;
  display_name: string;
  role: InternalRole;
  auth_mode: "CLOUDFLARE_ACCESS" | "CLOUDFLARE_ACCESS_PLUS_LOCAL_PASSWORD" | "LOCAL_SHARED_PASSWORD";
}

export const internalAuthApi = {
  me: () => apiGet<InternalIdentity>("/api/internal/auth/me"),
  login: (username: string, password: string) =>
    apiPost<{ csrf_token: string; must_change_password: boolean }>("/api/internal/auth/login", { username, password }),
  changePassword: (currentPassword: string, newPassword: string) =>
    apiPost<{ changed: boolean; must_change_password: boolean }>("/api/internal/auth/change-password", {
      current_password: currentPassword,
      new_password: newPassword
    }),
  logout: () => apiPost<{ logged_out: boolean }>("/api/internal/auth/logout")
};
