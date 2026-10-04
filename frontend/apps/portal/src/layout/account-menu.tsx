import { useAuthLogout } from "@smart-iptv/api-portal";
import {
  Avatar,
  Button,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  toast,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { CreditCard, History, LogOut, MonitorSmartphone, UserRound } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useMe } from "../lib/auth";

/** Sign out here, forget everything cached, and go to the sign-in page. */
export function useSignOut(): { signOut: () => void; pending: boolean } {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const logout = useAuthLogout();
  return {
    pending: logout.isPending,
    signOut: () => {
      logout.mutate(undefined, {
        onSettled: (_data, error) => {
          if (error) toast.error(t("errors.signOutFailed"));
          void navigate({ to: "/login", replace: true }).then(() => {
            queryClient.clear();
          });
        },
      });
    },
  };
}

/** The avatar menu at the end of the top bar. */
export function AccountMenu() {
  const { t } = useTranslation();
  const me = useMe();
  const { signOut } = useSignOut();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="rounded-full" aria-label={t("nav.account")}>
          {me ? (
            <Avatar name={me.name || me.username} size="sm" />
          ) : (
            <UserRound aria-hidden="true" />
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        {me ? (
          <>
            <DropdownMenuLabel className="grid gap-0.5 py-2 font-normal">
              <span className="truncate text-ui font-medium text-foreground">
                {me.name || me.username}
              </span>
              <span className="truncate text-xs text-muted-foreground" dir="ltr">
                {me.email || me.username}
              </span>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
          </>
        ) : null}
        <DropdownMenuItem asChild>
          <Link to="/account">
            <UserRound aria-hidden="true" />
            {t("nav.profile")}
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link to="/account/devices">
            <MonitorSmartphone aria-hidden="true" />
            {t("nav.devices")}
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link to="/account/subscription">
            <CreditCard aria-hidden="true" />
            {t("nav.subscription")}
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link to="/history">
            <History aria-hidden="true" />
            {t("nav.history")}
          </Link>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={signOut}>
          <LogOut aria-hidden="true" className="rtl:-scale-x-100" />
          {t("nav.signOut")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
