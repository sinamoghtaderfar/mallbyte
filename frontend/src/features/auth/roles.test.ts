import { describe, expect, it } from "vitest";
import { landingPage, mayOpenWorkspace } from "./roles";
import type { AuthUser } from "./types";

const customer: AuthUser = {
  id: 10, full_name: "Customer", email: "shopper@example.invalid", phone: null,
  is_seller: false, email_verified: true,
};

describe("privileged dashboard routing", () => {
  it.each([
    ["manage_content", "/admin/content"], ["view_sellers", "/admin/sellers"],
    ["role:product_admin", "/admin/categories"], ["view_platform_analytics", "/admin/financial"],
    ["view_inventory", "/admin/inventory"], ["view_all_support_tickets", "/support"],
    ["view_observability", "/admin/observability"],
  ])("routes %s without granting staff or superuser access", (permission, path) => {
    const user = { ...customer, capabilities: [permission] };
    expect(landingPage(user)).toBe(path);
    expect(mayOpenWorkspace(user, "/admin/super")).toBe(false);
  });

  it("keeps all granted modules and hides unrelated workspaces", () => {
    const user = { ...customer, capabilities: ["publish_content", "view_inventory"] };
    expect(mayOpenWorkspace(user, "/admin/content")).toBe(true);
    expect(mayOpenWorkspace(user, "/warehouse/transfers")).toBe(true);
    expect(mayOpenWorkspace(user, "/admin/sellers")).toBe(false);
    expect(landingPage({ ...customer, is_seller: true })).toBe("/");
  });
});
