import { zodResolver } from "@hookform/resolvers/zod";
import { useCustomersCreate, type CustomerCreated } from "@smart-iptv/api";
import {
  Button,
  DEFAULT_TIME_ZONE,
  DescriptionItem,
  DescriptionList,
  Form,
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  Stepper,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowLeft, ArrowRight, CircleCheck, UserPlus } from "lucide-react";
import { useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";

import { useMe } from "../../lib/auth";
import { applyFieldErrors, notifyError } from "../../lib/problems";
import { invalidateCustomerLists } from "./cache";
import { ExpiryText } from "./expiry";
import { AccessFields, DeviceFields, ProfileFields } from "./form-fields";
import { IssuedCredentialPanel } from "./issued-credential";
import {
  WIZARD_FIELD_PATHS,
  createCustomerRequest,
  wizardDefaults,
  wizardSchema,
  type WizardInput,
  type WizardValues,
} from "./schemas";

const STEPS = ["profile", "access", "device"] as const;
type Step = (typeof STEPS)[number];

function stepOfField(path: string): number {
  const index = STEPS.indexOf(path.split(".")[0] as Step);
  return index === -1 ? 0 : index;
}

/** After "Create customer": the account and its first credential, shown once. */
function CreatedStep({
  created,
  onClose,
  onCreateAnother,
}: {
  created: CustomerCreated;
  onClose: () => void;
  onCreateAnother: () => void;
}) {
  const { t } = useTranslation();
  const { customer, credential } = created;
  return (
    <>
      <SheetHeader>
        <SheetTitle className="flex items-center gap-2">
          <CircleCheck aria-hidden="true" className="size-5 text-success" />
          {t("customers.create.doneTitle")}
        </SheetTitle>
        <SheetDescription>
          {t("customers.create.doneDescription", { name: customer.name })}
        </SheetDescription>
      </SheetHeader>
      <SheetBody className="grid content-start gap-6">
        <DescriptionList>
          <DescriptionItem label={t("customers.fields.name")}>{customer.name}</DescriptionItem>
          <DescriptionItem label={t("customers.fields.expires")}>
            <ExpiryText expiresAt={customer.access?.expires_at} />
          </DescriptionItem>
        </DescriptionList>
        {credential ? (
          <section className="grid gap-3">
            <h3 className="text-sm font-semibold text-foreground">
              {t("customers.create.credentialTitle")}
            </h3>
            <IssuedCredentialPanel credential={credential} />
          </section>
        ) : (
          <p className="rounded-input bg-muted/60 px-3 py-2 text-ui text-muted-foreground">
            {t("customers.create.noCredential")}
          </p>
        )}
      </SheetBody>
      <SheetFooter className="flex-wrap">
        <Button variant="ghost" className="me-auto" onClick={onCreateAnother}>
          <UserPlus aria-hidden="true" />
          {t("customers.create.another")}
        </Button>
        <Button variant="secondary" onClick={onClose}>
          {t("customers.create.close")}
        </Button>
        <Button asChild>
          <Link to="/customers/$customerId" params={{ customerId: customer.id }} onClick={onClose}>
            {t("customers.create.open")}
          </Link>
        </Button>
      </SheetFooter>
    </>
  );
}

function Wizard({ onClose }: { onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const me = useMe();
  const timeZone = me?.timezone ?? DEFAULT_TIME_ZONE;
  const queryClient = useQueryClient();
  const [step, setStep] = useState(0);
  const [created, setCreated] = useState<CustomerCreated | null>(null);
  const body = useRef<HTMLDivElement>(null);
  const form = useForm<WizardInput, unknown, WizardValues>({
    resolver: zodResolver(wizardSchema),
    defaultValues: wizardDefaults(i18n.language.startsWith("en") ? "en" : "ar"),
    mode: "onTouched",
  });
  const create = useCustomersCreate();
  const last = step === STEPS.length - 1;
  const current = STEPS[step] ?? "profile";

  /** Bring the first error into view: custom controls (chips, checkboxes) can't take focus. */
  function revealFirstError(): void {
    requestAnimationFrame(() => {
      body.current
        ?.querySelector("[data-slot=form-message]")
        ?.scrollIntoView({ block: "center", behavior: "smooth" });
    });
  }

  async function goNext(): Promise<void> {
    if (await form.trigger(current, { shouldFocus: true })) setStep(step + 1);
    else revealFirstError();
  }

  const submit = form.handleSubmit(async (values) => {
    try {
      const result = await create.mutateAsync({ data: createCustomerRequest(values, timeZone) });
      setCreated(result);
      void invalidateCustomerLists(queryClient);
    } catch (error) {
      const fields = applyFieldErrors(error, form.setError, WIZARD_FIELD_PATHS);
      const first = fields[0];
      if (first === undefined) {
        notifyError(t, error);
      } else {
        setStep(stepOfField(first));
        revealFirstError();
      }
    }
  });

  if (created) {
    return (
      <CreatedStep
        created={created}
        onClose={onClose}
        onCreateAnother={() => {
          form.reset();
          setCreated(null);
          setStep(0);
        }}
      />
    );
  }

  return (
    <Form {...form}>
      <form
        noValidate
        className="flex min-h-0 flex-1 flex-col"
        onSubmit={(event) => {
          event.preventDefault();
          if (last) void submit(event);
          else void goNext();
        }}
      >
        <SheetHeader className="gap-3">
          <div className="grid gap-1">
            <SheetTitle>{t("customers.create.title")}</SheetTitle>
            <SheetDescription>
              {t(`customers.create.steps.${current}.description`)}
            </SheetDescription>
          </div>
          <Stepper
            label={t("customers.create.stepsLabel")}
            steps={STEPS.map((name) => t(`customers.create.steps.${name}.title`))}
            current={step}
          />
        </SheetHeader>
        <SheetBody ref={body}>
          {current === "profile" ? <ProfileFields /> : null}
          {current === "access" ? <AccessFields timeZone={timeZone} /> : null}
          {current === "device" ? <DeviceFields optional /> : null}
        </SheetBody>
        <SheetFooter>
          {step > 0 ? (
            <Button
              type="button"
              variant="ghost"
              className="me-auto"
              onClick={() => {
                setStep(step - 1);
              }}
            >
              <ArrowLeft aria-hidden="true" className="rtl:-scale-x-100" />
              {t("customers.create.back")}
            </Button>
          ) : null}
          <Button type="button" variant="secondary" onClick={onClose}>
            {t("customers.create.cancel")}
          </Button>
          {last ? (
            <Button type="submit" pending={form.formState.isSubmitting}>
              {t("customers.create.submit")}
            </Button>
          ) : (
            <Button type="submit">
              {t("customers.create.next")}
              <ArrowRight aria-hidden="true" className="rtl:-scale-x-100" />
            </Button>
          )}
        </SheetFooter>
      </form>
    </Form>
  );
}

/** "New customer" slide-over (SPEC §8.3.2): Profile → Access profile → First device. */
export function CreateCustomerSheet({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        className="max-w-xl"
        // Typing in the wizard must not be lost to a stray click on the backdrop.
        onInteractOutside={(event) => {
          event.preventDefault();
        }}
      >
        {open ? (
          <Wizard
            onClose={() => {
              onOpenChange(false);
            }}
          />
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
