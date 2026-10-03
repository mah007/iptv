import {
  Avatar,
  Button,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@smart-iptv/ui";
import { LogOut, UserRound } from "lucide-react";
import { useTranslation } from "react-i18next";

export interface SignedInUser {
  name: string;
  email: string;
}

export interface UserMenuProps {
  /** The signed-in admin (from /api/v1/auth/me); null until it is known. */
  user: SignedInUser | null;
  onSignOut: () => void;
}

/** Avatar menu at the end of the topbar. */
export function UserMenu({ user, onSignOut }: UserMenuProps) {
  const { t } = useTranslation();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          className="rounded-full"
          aria-label={t("userMenu.label")}
        >
          {user ? (
            <Avatar name={user.name} size="sm" />
          ) : (
            <span className="grid size-6 place-items-center rounded-full bg-muted text-muted-foreground ring-1 ring-inset ring-border">
              <UserRound aria-hidden="true" className="size-3.5" />
            </span>
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        {user ? (
          <>
            <DropdownMenuLabel className="grid gap-0.5 py-2 font-normal">
              <span className="truncate text-ui font-medium text-foreground">{user.name}</span>
              <span className="truncate text-xs text-muted-foreground" dir="ltr">
                {user.email}
              </span>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
          </>
        ) : null}
        <DropdownMenuItem onSelect={onSignOut}>
          <LogOut aria-hidden="true" className="rtl:-scale-x-100" />
          {t("userMenu.signOut")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
