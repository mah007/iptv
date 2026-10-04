// Shell and preferences
export { AppShell, type AppShellProps } from "./components/app-shell";
export { DensityToggle } from "./components/density-toggle";
export { LanguageToggle } from "./components/language-toggle";
export {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarInset,
  SidebarItem,
  SidebarProvider,
  SidebarSection,
  SidebarTrigger,
  useSidebar,
  type SidebarItemProps,
  type SidebarProps,
  type SidebarSectionProps,
} from "./components/sidebar";
export { ThemeToggle } from "./components/theme-toggle";
export { EnvironmentBadge, Topbar, type Environment } from "./components/topbar";
export { UiProvider } from "./components/ui-provider";
export { initDensity, setDensity, useDensity, type Density } from "./density";
export { createI18n, LANGUAGES, type Language, type Messages } from "./i18n";
export { initTheme, setTheme, useTheme, type Theme } from "./theme";

// Primitives
export { Alert, type AlertProps } from "./components/alert";
export { Avatar, initials, type AvatarProps } from "./components/avatar";
export { Badge, badgeVariants, type BadgeProps, type BadgeTone } from "./components/badge";
export { Button, buttonVariants, type ButtonProps } from "./components/button";
export {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "./components/card";
export { Checkbox } from "./components/checkbox";
export {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "./components/dialog";
export {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "./components/dropdown-menu";
export {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "./components/form";
export { Input, Textarea } from "./components/input";
export { Kbd } from "./components/kbd";
export { Label } from "./components/label";
export { PasswordInput } from "./components/password-input";
export {
  Popover,
  PopoverAnchor,
  PopoverClose,
  PopoverContent,
  PopoverTrigger,
} from "./components/popover";
export { RadioGroup, RadioGroupItem } from "./components/radio-group";
export {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "./components/select";
export { Separator } from "./components/separator";
export {
  Sheet,
  SheetBody,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "./components/sheet";
export { Skeleton } from "./components/skeleton";
export { Switch } from "./components/switch";
export {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "./components/table";
export { Tabs, TabsContent, TabsList, TabsTrigger } from "./components/tabs";
export { toast, Toaster } from "./components/toaster";
export { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "./components/tooltip";

// Composites (SPEC §8.1)
export {
  BarList,
  ChartLegend,
  ColumnChart,
  TimeSeriesChart,
  type BarListItem,
  type BarListProps,
  type ChartTone,
  type ColumnChartProps,
  type ColumnSeries,
  type TimeSeriesChartProps,
  type TimeSeriesPoint,
} from "./components/charts";
export {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandLoading,
  CommandSeparator,
  CommandShortcut,
  type CommandDialogProps,
} from "./components/command";
export { ConfirmDialog, type ConfirmDialogProps } from "./components/confirm-dialog";
export { CopyField, useCopy, type CopyFieldProps } from "./components/copy-field";
export {
  createDataTableColumnHelper,
  DataTable,
  dataTableFeatures,
  type DataTableColumnMeta,
  type DataTableColumns,
  type DataTableFeatures,
  type DataTableProps,
} from "./components/data-table";
export { DiffViewer, type DiffViewerProps } from "./components/diff-viewer";
export {
  EmptyState,
  ErrorState,
  type EmptyStateProps,
  type ErrorStateProps,
} from "./components/empty-state";
export {
  ErrorBoundary,
  type ErrorBoundaryFallbackProps,
  type ErrorBoundaryProps,
} from "./components/error-boundary";
export {
  DateRangeFilter,
  FacetFilter,
  FilterBar,
  SearchInput,
  type DateRange,
  type FacetOption,
  type FilterBarProps,
  type FilterChip,
} from "./components/filter-bar";
export {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
  PageHeader,
  type PageHeaderProps,
} from "./components/page-header";
export { QRCodeCard, type QRCodeCardProps } from "./components/qr-code-card";
export { SecretReveal, type SecretRevealProps } from "./components/secret-reveal";
export { StatTile, type StatTileProps } from "./components/stat-tile";
export { Stepper, type StepperProps } from "./components/stepper";
export {
  isKnownStatus,
  STATUS_TONES,
  StatusBadge,
  type KnownStatus,
  type StatusBadgeProps,
} from "./components/status-badge";

// Media and operations (SPEC §8.1, §8.3 pages 4-6 and 10)
export {
  countryFlagEmoji,
  countryName,
  CountryFlag,
  type CountryFlagProps,
} from "./components/country-flag";
export {
  DescriptionItem,
  DescriptionList,
  type DescriptionItemProps,
  type DescriptionListProps,
} from "./components/description-list";
export {
  DeviceIcon,
  deviceKind,
  type DeviceIconProps,
  type DeviceKind,
} from "./components/device-icon";
export { LiveIndicator, type LiveIndicatorProps } from "./components/live-indicator";
export {
  PosterCard,
  PosterCardSkeleton,
  PosterGrid,
  type PosterCardProps,
  type PosterGridProps,
} from "./components/poster-card";
export {
  BackdropImage,
  PosterImage,
  type ImageFormat,
  type ImageUrlResolver,
  type PosterImageProps,
} from "./components/poster-image";
export { ProgressBar, type ProgressBarProps } from "./components/progress-bar";
export {
  QualityBadges,
  qualityKeys,
  type MediaSummary,
  type QualityBadgesProps,
  type QualityKey,
} from "./components/quality-badges";
export {
  LiveDuration,
  RelativeTime,
  type LiveDurationProps,
  type RelativeTimeProps,
} from "./components/relative-time";
export {
  Timeline,
  TimelineItem,
  type TimelineItemProps,
  type TimelineTone,
} from "./components/timeline";

// Helpers
export { copyToClipboard } from "./lib/clipboard";
export { cn } from "./lib/cn";
export {
  createFormatters,
  DEFAULT_TIME_ZONE,
  formatBitrate,
  formatBytes,
  formatDuration,
  formatLocale,
  formatNumber,
  isoDuration,
  useFormatters,
  type DurationStyle,
  type Formatters,
  type Numerals,
} from "./lib/format";
export { diffJson, type DiffEntry, type DiffKind } from "./lib/json-diff";
export { useNow } from "./lib/use-now";
export {
  DEFAULT_PAGE_SIZE,
  MAX_PAGE_SIZE,
  PAGE_SIZE_OPTIONS,
  paginationFromSearch,
  paginationToSearch,
  parseTableSearch,
  sortingFromOrdering,
  sortingToOrdering,
  type TableSearch,
} from "./lib/table-search";
export type {
  ColumnVisibilityState,
  PaginationState,
  RowSelectionState,
  SortingState,
} from "@tanstack/react-table";
