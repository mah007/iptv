import { Rows3, Rows4 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useDensity } from "../density";
import { cn } from "../lib/cn";
import { Button } from "./button";

/** Comfortable ↔ compact rows, controls and spacing (SPEC §8.1). */
export function DensityToggle({ className }: { className?: string }) {
  const { t } = useTranslation("ui");
  const [density, setDensity] = useDensity();
  const compact = density === "compact";
  const label = compact ? t("density.toComfortable") : t("density.toCompact");
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      className={cn("text-muted-foreground hover:text-foreground", className)}
      aria-label={label}
      title={label}
      onClick={() => {
        setDensity(compact ? "comfortable" : "compact");
      }}
    >
      {compact ? <Rows3 aria-hidden="true" /> : <Rows4 aria-hidden="true" />}
    </Button>
  );
}
