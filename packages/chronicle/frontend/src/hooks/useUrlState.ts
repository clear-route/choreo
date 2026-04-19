import { useQueryState, parseAsString, parseAsInteger } from "nuqs";
import { useEffect } from "react";

const TENANT_SLUG_RE = /^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$/;
const TENANT_STORAGE_KEY = "chronicle:tenant";

const parseAsTenantSlug = parseAsString.withOptions({
  clearOnDefault: false,
});

export function useTenantParam() {
  const [tenant, setTenant] = useQueryState("tenant", parseAsTenantSlug);

  // On mount: if no tenant in URL, restore from localStorage
  useEffect(() => {
    if (!tenant) {
      const stored = localStorage.getItem(TENANT_STORAGE_KEY);
      if (stored && TENANT_SLUG_RE.test(stored)) {
        void setTenant(stored);
      }
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const setValidatedTenant = (value: string) => {
    const normalised = value.toLowerCase();
    if (TENANT_SLUG_RE.test(normalised)) {
      localStorage.setItem(TENANT_STORAGE_KEY, normalised);
      void setTenant(normalised);
    }
  };

  return [tenant ?? "", setValidatedTenant] as const;
}

export function useEnvParam() {
  return useQueryState("env", parseAsString);
}

export function useBranchParam() {
  return useQueryState("branch", parseAsString);
}

export function useFromParam() {
  return useQueryState("from", parseAsString);
}

export function useToParam() {
  return useQueryState("to", parseAsString);
}

export function useLimitParam(defaultValue = 50) {
  return useQueryState("limit", parseAsInteger.withDefault(defaultValue));
}

export function useOffsetParam() {
  return useQueryState("offset", parseAsInteger.withDefault(0));
}
