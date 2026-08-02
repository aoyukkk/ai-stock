import { describe, expect, it } from "vitest";

import { authRedirectPath } from "./http";

describe("authRedirectPath", () => {
  it("does not redirect the whole application for an API permission denial", () => {
    expect(authRedirectPath(403, "INSUFFICIENT_ROLE", "/workbench")).toBeNull();
  });

  it("still redirects unauthenticated sessions to login", () => {
    expect(authRedirectPath(401, "LOCAL_SESSION_REQUIRED", "/workbench")).toBe("/local-login");
  });
});
