import { Eye, EyeOff } from "lucide-react";
import { useState, type ComponentProps } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "../lib/cn";
import { Input } from "./input";

interface PasswordInputProps extends Omit<ComponentProps<"input">, "type"> {
  /** Controlled visibility (e.g. to show a password the app just generated); uncontrolled when omitted. */
  revealed?: boolean;
  onRevealedChange?: (revealed: boolean) => void;
}

/** Password field with a show/hide toggle at the inline end. */
export function PasswordInput({
  className,
  dir,
  revealed,
  onRevealedChange,
  ...props
}: PasswordInputProps) {
  const { t } = useTranslation("ui");
  const [ownVisible, setOwnVisible] = useState(false);
  const visible = revealed ?? ownVisible;
  return (
    // The wrapper shares the input's direction, so the toggle sits on the same
    // side as the input's end padding (e.g. an LTR password on an Arabic page).
    <div className="relative" dir={dir}>
      <Input
        type={visible ? "text" : "password"}
        className={cn("pe-10", className)}
        spellCheck={false}
        autoCapitalize="off"
        dir={dir}
        {...props}
      />
      <button
        type="button"
        className="absolute end-1 top-1/2 grid size-7 -translate-y-1/2 cursor-pointer place-items-center rounded-badge text-muted-foreground outline-none transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50"
        aria-label={visible ? t("password.hide") : t("password.show")}
        aria-pressed={visible}
        disabled={props.disabled}
        onClick={() => {
          setOwnVisible(!visible);
          onRevealedChange?.(!visible);
        }}
      >
        {visible ? (
          <EyeOff aria-hidden="true" className="size-4" />
        ) : (
          <Eye aria-hidden="true" className="size-4" />
        )}
      </button>
    </div>
  );
}
