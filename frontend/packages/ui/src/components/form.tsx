import { Slot } from "radix-ui";
import {
  createContext,
  useContext,
  useId,
  useMemo,
  type ComponentProps,
  type ReactNode,
} from "react";
import {
  Controller,
  FormProvider,
  useFormContext,
  useFormState,
  type ControllerProps,
  type FieldPath,
  type FieldValues,
} from "react-hook-form";
import { useTranslation } from "react-i18next";

import { cn } from "../lib/cn";
import { Label } from "./label";

/**
 * Form building blocks for react-hook-form (+ zod via @hookform/resolvers).
 *
 * Validation messages are translation keys in the app namespace (e.g.
 * `z.string().min(1, "login.errors.required")`); <FormMessage> translates them
 * at render time, so switching language re-translates visible errors.
 */
export const Form = FormProvider;

const FieldNameContext = createContext<string | null>(null);
const ItemIdContext = createContext<string | null>(null);

export function FormField<
  TFieldValues extends FieldValues = FieldValues,
  TName extends FieldPath<TFieldValues> = FieldPath<TFieldValues>,
  TTransformedValues = TFieldValues,
>(props: ControllerProps<TFieldValues, TName, TTransformedValues>) {
  return (
    <FieldNameContext.Provider value={props.name}>
      <Controller {...props} />
    </FieldNameContext.Provider>
  );
}

function useFormField() {
  const name = useContext(FieldNameContext);
  const itemId = useContext(ItemIdContext);
  if (name === null || itemId === null) {
    throw new Error("Form controls must be rendered inside <FormField> and <FormItem>.");
  }
  const { getFieldState } = useFormContext();
  const formState = useFormState({ name });
  const fieldState = getFieldState(name, formState);
  return {
    error: fieldState.error,
    controlId: `${itemId}-control`,
    descriptionId: `${itemId}-description`,
    messageId: `${itemId}-message`,
  };
}

export function FormItem({ className, ...props }: ComponentProps<"div">) {
  const id = useId();
  return (
    <ItemIdContext.Provider value={id}>
      <div data-slot="form-item" className={cn("grid gap-1.5", className)} {...props} />
    </ItemIdContext.Provider>
  );
}

export function FormLabel({ className, ...props }: ComponentProps<typeof Label>) {
  const { error, controlId } = useFormField();
  return (
    <Label
      data-invalid={error ? true : undefined}
      className={cn("data-[invalid]:text-danger-text", className)}
      htmlFor={controlId}
      {...props}
    />
  );
}

export function FormControl(props: ComponentProps<typeof Slot.Root>) {
  const { error, controlId, descriptionId, messageId } = useFormField();
  const describedBy = useMemo(
    () => (error ? `${descriptionId} ${messageId}` : descriptionId),
    [error, descriptionId, messageId],
  );
  return (
    <Slot.Root
      id={controlId}
      aria-describedby={describedBy}
      aria-invalid={error ? true : undefined}
      {...props}
    />
  );
}

export function FormDescription({ className, ...props }: ComponentProps<"p">) {
  const { descriptionId } = useFormField();
  return (
    <p id={descriptionId} className={cn("text-xs text-muted-foreground", className)} {...props} />
  );
}

export function FormMessage({ className, children, ...props }: ComponentProps<"p">) {
  const { error, messageId } = useFormField();
  const { t } = useTranslation();
  // Schema messages are translation keys; errors of type "server" carry the
  // API's own wording (problem+json field_errors) and are shown as sent.
  let body: ReactNode = children;
  const server = error?.type === "server";
  if (error?.message) body = server ? error.message : t(error.message);
  if (!body) return null;
  return (
    <p
      id={messageId}
      data-slot="form-message"
      // The API's wording may not be in the page's language: keep its punctuation in place.
      dir={server ? "auto" : undefined}
      className={cn("text-xs font-medium text-danger-text", className)}
      {...props}
    >
      {body}
    </p>
  );
}
