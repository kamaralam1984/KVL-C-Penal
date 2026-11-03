import {
  AppWindow,
  Archive,
  BadgeDollarSign,
  BarChart3,
  Boxes,
  FolderOpen,
  CreditCard,
  Database,
  Globe,
  Headphones,
  LayoutDashboard,
  LifeBuoy,
  Lock,
  Palette,
  Receipt,
  ServerCog,
  Settings,
  ShieldCheck,
  Store,
  UserCircle,
  Users,
  Waypoints,
  Wallet,
  Activity,
  Package,
  type LucideIcon,
} from "lucide-react";

export type Role = "admin" | "hosting" | "customer" | "reseller";

export interface NavItem {
  slug: string;
  label: string;
  icon: LucideIcon;
  /** One-line purpose, used for search and placeholder pages. */
  description: string;
  /** Build phase in which this module becomes functional. */
  phase: number;
  /** Items with a bespoke page in Phase 1 (everything else is a placeholder route). */
  built?: boolean;
}

export interface NavSection {
  title?: string;
  items: NavItem[];
}

export interface RoleConfig {
  role: Role;
  label: string;
  description: string;
  base: string;
  /** Slugs shown in the mobile bottom tab bar (rest live under "More"). */
  mobileTabs: string[];
  sections: NavSection[];
}

const item = (
  slug: string,
  label: string,
  icon: LucideIcon,
  description: string,
  phase: number,
  built = false,
): NavItem => ({ slug, label, icon, description, phase, built });

export const ROLES: Record<Role, RoleConfig> = {
  admin: {
    role: "admin",
    label: "Super Admin",
    description: "Platform owner: servers, tenants, billing and security.",
    base: "/admin",
    mobileTabs: ["dashboard", "servers", "websites", "customers"],
    sections: [
      {
        items: [item("dashboard", "Dashboard", LayoutDashboard, "Platform overview and recent activity.", 1, true)],
      },
      {
        title: "Infrastructure",
        items: [
          item("servers", "Servers", ServerCog, "Registered servers, agents and their health.", 4),
          item("websites", "Websites", Globe, "All websites across tenants.", 6),
          item("domains", "Domains", Waypoints, "Domain ownership, DNS and mapping.", 6),
          item("databases", "Databases", Database, "Customer databases and users.", 8),
          item("monitoring", "Monitoring", Activity, "Metrics, services and alerts.", 5),
          item("backups", "Backups", Archive, "Backup schedules, inventory and restores.", 11),
        ],
      },
      {
        title: "Business",
        items: [
          item("customers", "Customers", Users, "Tenants and their members.", 3),
          item("resellers", "Resellers", Store, "Reseller accounts and approvals.", 13),
          item("plans", "Plans", Package, "Hosting plans and entitlements.", 14),
          item("subscriptions", "Subscriptions", Receipt, "Subscription lifecycle.", 14),
          item("billing", "Billing", CreditCard, "Invoices, payments and revenue.", 14),
          item("support", "Support", LifeBuoy, "Tickets, knowledge base and SLAs.", 16),
        ],
      },
      {
        title: "Platform",
        items: [
          item("automation", "Automation", Boxes, "n8n workflows and executions.", 15),
          item("security", "Security", ShieldCheck, "Audit log, API keys and sessions.", 17),
          item("settings", "Settings", Settings, "Platform configuration.", 3),
        ],
      },
    ],
  },
  hosting: {
    role: "hosting",
    label: "Hosting Admin",
    description: "Operate websites, applications and server-side resources.",
    base: "/hosting",
    mobileTabs: ["dashboard", "websites", "applications", "ssl"],
    sections: [
      {
        items: [item("dashboard", "Dashboard", LayoutDashboard, "Hosting operations overview.", 1, true)],
      },
      {
        title: "Hosting",
        items: [
          item("websites", "Websites", Globe, "Websites on managed servers.", 6),
          item("domains", "Domains", Waypoints, "Domains and DNS status.", 6),
          item("databases", "Databases", Database, "Provisioned databases.", 8),
          item("applications", "Applications", AppWindow, "PHP, Node.js and Python apps.", 7),
          item("files", "Files", FolderOpen, "Browse and manage site files.", 10),
          item("backups", "Backups", Archive, "Backup status and restores.", 11),
          item("ssl", "SSL & DNS", Lock, "Certificates, DNS records and renewals.", 9),
        ],
      },
    ],
  },
  customer: {
    role: "customer",
    label: "Customer",
    description: "Self-service portal for hosting customers.",
    base: "/customer",
    mobileTabs: ["dashboard", "websites", "billing", "support"],
    sections: [
      {
        items: [item("dashboard", "Dashboard", LayoutDashboard, "Your hosting at a glance.", 1, true)],
      },
      {
        title: "My hosting",
        items: [
          item("websites", "Websites", Globe, "Your websites.", 12),
          item("domains", "Domains", Waypoints, "Your domains.", 12),
          item("databases", "Databases", Database, "Your databases.", 12),
          item("applications", "Applications", AppWindow, "Your applications.", 12),
          item("files", "Files", FolderOpen, "Browse and manage your site files.", 12),
          item("ssl", "SSL", Lock, "Your certificates.", 12),
          item("backups", "Backups", Archive, "Your backups.", 12),
        ],
      },
      {
        title: "Account",
        items: [
          item("billing", "Billing", Wallet, "Plan, invoices and payments.", 14),
          item("support", "Support", Headphones, "Tickets and help.", 16),
          item("profile", "Profile", UserCircle, "Profile, password and 2FA.", 2),
        ],
      },
    ],
  },
  reseller: {
    role: "reseller",
    label: "Reseller",
    description: "Manage clients, packages and commissions.",
    base: "/reseller",
    mobileTabs: ["dashboard", "clients", "packages", "commissions"],
    sections: [
      {
        items: [item("dashboard", "Dashboard", LayoutDashboard, "Your reseller business at a glance.", 1, true)],
      },
      {
        title: "Business",
        items: [
          item("clients", "Clients", Users, "Your clients and their usage.", 13),
          item("packages", "Packages", Package, "Packages you sell.", 13),
          item("commissions", "Commissions", BadgeDollarSign, "Commission ledger.", 13),
          item("reports", "Reports", BarChart3, "Exportable reports.", 13),
        ],
      },
      {
        title: "Brand",
        items: [
          item("branding", "Branding", Palette, "White-label branding.", 13),
          item("settings", "Settings", Settings, "Reseller settings.", 13),
        ],
      },
    ],
  },
};

export const ROLE_ORDER: Role[] = ["admin", "hosting", "customer", "reseller"];

export function flatItems(role: Role): NavItem[] {
  return ROLES[role].sections.flatMap((s) => s.items);
}

export function findItem(role: Role, slug: string): NavItem | undefined {
  return flatItems(role).find((i) => i.slug === slug);
}

/** Permission needed to see a nav entry. UX only: every API call is authorised by the backend. */
export const NAV_PERM: Record<Role, Record<string, string>> = {
  admin: {
    servers: "servers:read", websites: "websites:read", domains: "domains:read", databases: "databases:read",
    monitoring: "monitoring:read", backups: "backups:read", customers: "tenants:read", resellers: "resellers:read",
    plans: "plans:read", subscriptions: "subscriptions:read", billing: "billing:read", support: "tickets:read",
    automation: "automation:read", security: "security:read", settings: "settings:read",
  },
  hosting: { websites: "websites:read", domains: "domains:read", databases: "databases:read", applications: "apps:read", files: "files:read", backups: "backups:read", ssl: "ssl:read" },
  customer: { websites: "websites:read", domains: "domains:read", databases: "databases:read", applications: "apps:read", files: "files:read", ssl: "ssl:read", backups: "backups:read", billing: "billing:read", support: "tickets:read" },
  reseller: { clients: "clients:read", packages: "packages:read", commissions: "commissions:read", reports: "clients:read", branding: "branding:read", settings: "branding:read" },
};
