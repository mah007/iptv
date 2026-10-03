import { isApiError } from "@smart-iptv/api";
import { EmptyState, ErrorState } from "@smart-iptv/ui";
import { FileQuestion, Lock } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useCan, type PermissionRequirement } from "../lib/auth";

/** The admin's roles don't allow this page or panel (SPEC §8.3: permission-denied state). */
export function PermissionDenied({ className }: { className?: string | undefined }) {
  const { t } = useTranslation();
  return (
    <EmptyState
      className={className}
      icon={<Lock />}
      title={t("states.denied.title")}
      description={t("states.denied.description")}
    />
  );
}

/** Renders its children only for admins holding the permission. */
export function RequirePermission({
  permission,
  children,
}: {
  permission: PermissionRequirement;
  children: ReactNode;
}) {
  const can = useCan();
  return can(permission) ? children : <PermissionDenied className="min-h-[50vh]" />;
}

/** A failed query: permission denied, gone, or an error with a retry. */
export function QueryError({
  error,
  onRetry,
  className,
}: {
  error: unknown;
  onRetry?: () => void;
  className?: string | undefined;
}) {
  const { t } = useTranslation();
  if (isApiError(error) && error.code === "PERMISSION_DENIED") {
    return <PermissionDenied className={className} />;
  }
  if (isApiError(error) && error.code === "NOT_FOUND") {
    return (
      <EmptyState
        className={className}
        icon={<FileQuestion />}
        title={t("states.notFound.title")}
        description={t("states.notFound.description")}
      />
    );
  }
  return <ErrorState className={className} error={error} onRetry={onRetry} />;
}
