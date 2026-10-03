import { Moon, Sun } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "../lib/cn";
import { useTheme } from "../theme";
import { Button } from "./button";

export function ThemeToggle({ className }: { className?: string }) {
  const { t } = useTranslation("ui");
  const [theme, setTheme] = useTheme();
  const isDark = theme === "dark";
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      className={cn("text-muted-foreground hover:text-foreground", className)}
      aria-label={isDark ? t("theme.toLight") : t("theme.toDark")}
      title={isDark ? t("theme.toLight") : t("theme.toDark")}
      onClick={() => {
        setTheme(isDark ? "light" : "dark");
      }}
    >
      {isDark ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
    </Button>
  );
}
