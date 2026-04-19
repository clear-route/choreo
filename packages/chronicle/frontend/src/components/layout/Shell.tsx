import { NavLink, Outlet } from "react-router-dom";
import * as Select from "@radix-ui/react-select";
import { useTenants } from "@/hooks/useTenants";
import { useTenantParam } from "@/hooks/useUrlState";
import { navLinkActive, navLinkInactive, inputInteractive, selectContent, selectItem } from "@/theme/styles";

const navItems = [
  { to: "/runs", label: "Runs" },
  { to: "/topics", label: "Topics" },
  { to: "/compare", label: "Compare" },
  { to: "/anomalies", label: "Anomalies" },
];

export function Shell() {
  const [tenant, setTenant] = useTenantParam();
  const { data, isLoading } = useTenants();
  const tenants = data?.items ?? [];

  return (
    <div className="flex min-h-screen flex-col bg-bg font-sans text-text text-[13px]">
      <header className="flex h-12 items-center justify-between border-b border-border bg-surface px-5 sticky top-0 z-10">
        <div className="flex items-center gap-6">
          <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-text-subtle">
            Chronicle
            <span className="font-normal normal-case tracking-normal text-text-subtle/60 ml-1">by <a href="https://github.com/clear-route/choreo" className="text-text-muted hover:text-info transition-colors" target="_blank" rel="noopener noreferrer">Choreo</a></span>
          </span>
          <nav className="flex gap-0.5">
            {navItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  isActive ? navLinkActive : navLinkInactive
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>

        <Select.Root value={tenant || undefined} onValueChange={setTenant}>
          <Select.Trigger
            className={`inline-flex items-center gap-2 ${inputInteractive} text-[12px]`}
            disabled={isLoading || tenants.length === 0}
          >
            <Select.Value placeholder={isLoading ? "Loading..." : tenants.length === 0 ? "No tenants" : "Select tenant"} />
            <Select.Icon className="text-text-subtle">
              <ChevronDown />
            </Select.Icon>
          </Select.Trigger>
          <Select.Portal>
            <Select.Content
              position="popper"
              sideOffset={4}
              className={`z-50 ${selectContent} min-w-[var(--radix-select-trigger-width)]`}
            >
              <Select.Viewport className="p-1">
                {tenants.map((t) => (
                  <Select.Item
                    key={t.slug}
                    value={t.slug}
                    className={`${selectItem} flex items-center`}
                  >
                    <Select.ItemText>{t.slug}</Select.ItemText>
                    <Select.ItemIndicator className="ml-auto text-info">
                      <Check />
                    </Select.ItemIndicator>
                  </Select.Item>
                ))}
              </Select.Viewport>
            </Select.Content>
          </Select.Portal>
        </Select.Root>
      </header>
      <main className="flex-1">
        <Outlet />
      </main>
    </div>
  );
}

function ChevronDown() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Check() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M2.5 6L5 8.5L9.5 3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
