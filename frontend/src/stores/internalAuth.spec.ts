// @vitest-environment jsdom

import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";

import { useInternalAuthStore } from "@/stores/internalAuth";

describe("internal auth role state", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("keeps viewers read-only and grants writes only to traders/admins", () => {
    const store = useInternalAuthStore();
    store.identity = {
      id: 1,
      email: "viewer@example.com",
      display_name: "Viewer",
      role: "VIEWER",
      auth_mode: "CLOUDFLARE_ACCESS"
    };
    expect(store.role).toBe("VIEWER");
    expect(store.canWrite).toBe(false);
    expect(store.isAdmin).toBe(false);

    store.identity = { ...store.identity, role: "TRADER" };
    expect(store.canWrite).toBe(true);
    expect(store.isAdmin).toBe(false);

    store.identity = { ...store.identity, role: "ADMIN" };
    expect(store.canWrite).toBe(true);
    expect(store.isAdmin).toBe(true);
  });

  it("clears cached identity when a password or session changes", () => {
    const store = useInternalAuthStore();
    store.identity = {
      id: 1,
      email: "admin@example.com",
      display_name: "Admin",
      role: "ADMIN",
      auth_mode: "CLOUDFLARE_ACCESS_PLUS_LOCAL_PASSWORD"
    };
    store.initialized = true;
    store.reset();
    expect(store.identity).toBeNull();
    expect(store.initialized).toBe(false);
  });
});
