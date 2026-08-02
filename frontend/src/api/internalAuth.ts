import { apiGet, apiPost } from "@/api/http";

export type InternalRole = "ADMIN" | "TRADER" | "VIEWER";

export interface InternalIdentity {
  authenticated?: boolean;
  id: number;
  email: string;
  display_name: string;
  role: InternalRole;
  auth_mode: "CLOUDFLARE_ACCESS" | "CLOUDFLARE_ACCESS_PLUS_LOCAL_PASSWORD" | "LOCAL_SHARED_PASSWORD";
  auth_source?: "CLOUDFLARE_ACCESS" | "LOCAL_PASSWORD";
  local_password_enabled?: boolean;
  password_change_required?: boolean;
  identity_scope?: "SHARED_IDENTITY" | "VERIFIED_INDIVIDUAL";
  individual_accountability?: boolean;
}

export interface InternalAuthPublicConfig {
  auth_mode: InternalIdentity["auth_mode"];
  local_password_enabled: boolean;
  force_password_change_on_first_login: boolean;
  identity_scope: "SHARED_IDENTITY" | "VERIFIED_INDIVIDUAL";
  individual_accountability: boolean;
}

export const internalAuthApi = {
  me: () => apiGet<InternalIdentity>("/api/internal/auth/me"),
  config: () => apiGet<InternalAuthPublicConfig>("/api/internal/auth/config/public"),
  login: (password: string) =>
    apiPost<{ csrf_token: string; auth_source: "LOCAL_SHARED_PASSWORD"; password_change_required: boolean; redirect_to: string }>("/api/internal/auth/local/login", { password }),
  changePassword: (currentPassword: string, newPassword: string) =>
    apiPost<{ changed: boolean; csrf_token: string; password_change_required: boolean; redirect_to: string }>("/api/internal/auth/set-password", {
      current_password: currentPassword,
      new_password: newPassword
    }),
  logout: () => apiPost<{ logged_out: boolean }>("/api/internal/auth/logout")
};
