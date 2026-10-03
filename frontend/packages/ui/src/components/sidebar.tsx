import { Menu, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { Direction, Slot } from "radix-ui";
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ComponentProps,
  type MouseEvent,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";

import { cn } from "../lib/cn";
import { readPreference, writePreference } from "../lib/storage";
import { Button } from "./button";
import { Sheet, SheetContent, SheetTitle } from "./sheet";
import { Tooltip, TooltipContent, TooltipTrigger } from "./tooltip";

const STORAGE_KEY = "smart-iptv.sidebar";

interface SidebarState {
  /** Desktop rail: icons only. */
  collapsed: boolean;
  setCollapsed: (collapsed: boolean) => void;
  /** Phone/tablet slide-over. */
  mobileOpen: boolean;
  setMobileOpen: (open: boolean) => void;
}

const SidebarContext = createContext<SidebarState | null>(null);
/** True inside the mobile sheet, where the sidebar always shows labels. */
const InSheetContext = createContext(false);

export function useSidebar(): SidebarState {
  const state = useContext(SidebarContext);
  if (!state) throw new Error("useSidebar must be used inside <SidebarProvider>.");
  return state;
}

/** Holds the sidebar state (collapse remembered per browser) and lays out sidebar + content. */
export function SidebarProvider({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  const [collapsed, setCollapsedState] = useState(
    () => readPreference(STORAGE_KEY) === "collapsed",
  );
  const [mobileOpen, setMobileOpen] = useState(false);
  const setCollapsed = useCallback((next: boolean) => {
    setCollapsedState(next);
    writePreference(STORAGE_KEY, next ? "collapsed" : "expanded");
  }, []);
  const value = useMemo(
    () => ({ collapsed, setCollapsed, mobileOpen, setMobileOpen }),
    [collapsed, setCollapsed, mobileOpen],
  );
  return (
    <SidebarContext.Provider value={value}>
      <div className={cn("flex min-h-dvh w-full bg-background", className)}>{children}</div>
    </SidebarContext.Provider>
  );
}

export interface SidebarProps {
  /** Accessible name of the navigation landmark. */
  label: string;
  children: ReactNode;
}

/**
 * Collapsible navigation: a sticky rail on desktop (icons + labels, or icons
 * only when collapsed) and a slide-over sheet on small screens.
 */
export function Sidebar({ label, children }: SidebarProps) {
  const { collapsed, mobileOpen, setMobileOpen } = useSidebar();
  return (
    <>
      <aside
        data-slot="sidebar"
        data-collapsed={collapsed}
        className="group/sidebar sticky top-0 hidden h-dvh shrink-0 flex-col border-e border-border bg-sidebar transition-[width] duration-200 ease-out md:flex md:w-60 md:data-[collapsed=true]:w-14"
      >
        <nav aria-label={label} className="flex min-h-0 flex-1 flex-col">
          {children}
        </nav>
      </aside>
      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent
          side="start"
          className="w-72 max-w-[85vw] bg-sidebar p-0 md:hidden"
          aria-describedby={undefined}
          hideClose
        >
          <SheetTitle className="sr-only">{label}</SheetTitle>
          <InSheetContext.Provider value={true}>
            <nav
              aria-label={label}
              className="group/sidebar flex min-h-0 flex-1 flex-col"
              data-collapsed={false}
            >
              {children}
            </nav>
          </InSheetContext.Provider>
        </SheetContent>
      </Sheet>
    </>
  );
}

export function SidebarHeader({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "flex h-14 shrink-0 items-center gap-2 border-b border-border px-3 group-data-[collapsed=true]/sidebar:justify-center group-data-[collapsed=true]/sidebar:px-0",
        className,
      )}
      {...props}
    />
  );
}

export function SidebarContent({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("min-h-0 flex-1 overflow-y-auto py-2", className)} {...props} />;
}

export function SidebarFooter({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "flex shrink-0 items-center gap-2 border-t border-border p-2 group-data-[collapsed=true]/sidebar:justify-center",
        className,
      )}
      {...props}
    />
  );
}

export interface SidebarSectionProps extends ComponentProps<"div"> {
  title: string;
}

export function SidebarSection({ title, className, children, ...props }: SidebarSectionProps) {
  return (
    <div
      role="group"
      aria-label={title}
      className={cn(
        "px-2 py-1.5 group-data-[collapsed=true]/sidebar:border-t group-data-[collapsed=true]/sidebar:border-border group-data-[collapsed=true]/sidebar:first:border-t-0",
        className,
      )}
      {...props}
    >
      <div className="flex h-7 items-center px-2 text-xs font-medium text-muted-foreground group-data-[collapsed=true]/sidebar:sr-only">
        {title}
      </div>
      <ul className="grid gap-0.5">{children}</ul>
    </div>
  );
}

export interface SidebarItemProps extends ComponentProps<"button"> {
  icon: ReactNode;
  label: string;
  /** Count shown at the end of the item, e.g. open reviews. */
  badge?: ReactNode;
  /** Render the child (the router's link) as the item. Active links set aria-current="page". */
  asChild?: boolean;
}

/**
 * One navigation entry. With `asChild`, pass the router link as the only child;
 * the icon, label and badge are placed inside it.
 */
export function SidebarItem({
  icon,
  label,
  badge,
  asChild = false,
  className,
  children,
  onClick,
  ...props
}: SidebarItemProps) {
  const { collapsed, setMobileOpen } = useSidebar();
  const inSheet = useContext(InSheetContext);
  const dir = Direction.useDirection();
  const Component = asChild ? Slot.Root : "button";
  const railOnly = collapsed && !inSheet;

  const item = (
    <Component
      data-slot="sidebar-item"
      className={cn(
        "flex h-(--density-nav) w-full cursor-pointer items-center gap-2.5 rounded-input px-2 text-ui font-medium text-muted-foreground outline-none transition-colors duration-150 hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring aria-[current=page]:bg-primary/10 aria-[current=page]:text-primary [&_svg]:size-4 [&_svg]:shrink-0",
        railOnly && "justify-center px-0",
        className,
      )}
      onClick={(event: MouseEvent<HTMLButtonElement>) => {
        onClick?.(event);
        // Following a link from the slide-over closes it.
        if (inSheet) setMobileOpen(false);
      }}
      {...props}
    >
      {icon}
      <Slot.Slottable>{children}</Slot.Slottable>
      <span className={cn("min-w-0 flex-1 truncate text-start", railOnly && "sr-only")}>
        {label}
      </span>
      {badge !== undefined && !railOnly ? (
        <span className="ms-auto rounded-badge bg-muted px-1.5 text-xs tabular-nums text-muted-foreground">
          {badge}
        </span>
      ) : null}
    </Component>
  );

  return (
    <li>
      {railOnly ? (
        <Tooltip>
          <TooltipTrigger asChild>{item}</TooltipTrigger>
          <TooltipContent side={dir === "rtl" ? "left" : "right"}>{label}</TooltipContent>
        </Tooltip>
      ) : (
        item
      )}
    </li>
  );
}

/** Topbar buttons: open the sheet on small screens, collapse the rail on desktop. */
export function SidebarTrigger() {
  const { t } = useTranslation("ui");
  const { collapsed, setCollapsed, setMobileOpen } = useSidebar();
  const CollapseIcon = collapsed ? PanelLeftOpen : PanelLeftClose;
  return (
    <>
      <Button
        variant="ghost"
        size="icon-sm"
        className="md:hidden"
        aria-label={t("sidebar.open")}
        onClick={() => {
          setMobileOpen(true);
        }}
      >
        <Menu aria-hidden="true" />
      </Button>
      <Button
        variant="ghost"
        size="icon-sm"
        className="hidden text-muted-foreground md:inline-flex"
        aria-label={collapsed ? t("sidebar.expand") : t("sidebar.collapse")}
        aria-expanded={!collapsed}
        onClick={() => {
          setCollapsed(!collapsed);
        }}
      >
        <CollapseIcon aria-hidden="true" className="rtl:-scale-x-100" />
      </Button>
    </>
  );
}

/** The main column next to the sidebar: topbar plus page content. */
export function SidebarInset({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("flex min-w-0 flex-1 flex-col", className)} {...props} />;
}
