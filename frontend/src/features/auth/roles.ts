import type { AuthUser } from "./types";

export function isAdminUser(user: AuthUser | null) {
  return Boolean(user?.is_staff || user?.is_superuser || user?.capabilities?.length);
}

export function isSellerUser(user: AuthUser | null) {
  return Boolean(user?.is_seller);
}

export function getAccountTypeLabel(user: AuthUser | null) {
  if (!user) {
    return "Guest";
  }

  if (isAdminUser(user)) {
    return "Platform admin";
  }

  if (isSellerUser(user)) {
    return "Seller";
  }

  return "Customer";
}

const workspacePermissions: Record<string, string[]> = {
  "/admin/content": ["view_content_drafts", "manage_content", "publish_content"],
  "/admin/categories": ["role:product_admin", "manage_categories"],
  "/admin/sellers": ["view_sellers"],
  "/admin/financial": ["view_platform_analytics"],
  "/admin/inventory": ["view_inventory"],
  "/support": ["view_all_support_tickets"],
  "/admin/observability": ["view_observability"],
  "/admin/orders": ["manage_orders", "view_all_orders"],
  "/admin/returns": ["manage_returns", "view_all_returns"],
  "/admin/shipments": ["manage_shipments", "view_all_shipments"],
  "/warehouse/transfers": ["view_inventory"],
  "/admin/observability/workflows": ["view_observability_operations"],
  "/admin/analytics": ["view_platform_analytics"],
  "/admin/finance": ["view_platform_analytics"],
  "/admin/monitoring": ["view_platform_analytics"],
};

export function mayOpenWorkspace(user: AuthUser | null, href: string) {
  if (!user) return false;
  if (user.is_superuser) return true;
  if (href === "/admin") return isAdminUser(user);
  return Boolean(workspacePermissions[href]?.some((code) => user.capabilities?.includes(code)));
}

export function landingPage(user: AuthUser | null, fallback = "/") {
  if (user?.is_superuser) return "/admin/super";
  return Object.keys(workspacePermissions).find((path) => mayOpenWorkspace(user, path))
    ?? (mayOpenWorkspace(user, "/admin") ? "/admin" : fallback);
}
