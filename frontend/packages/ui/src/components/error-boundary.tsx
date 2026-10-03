import { Component, type ErrorInfo, type ReactNode } from "react";

import { ErrorState } from "./empty-state";

export interface ErrorBoundaryFallbackProps {
  error: unknown;
  reset: () => void;
}

export interface ErrorBoundaryProps {
  children: ReactNode;
  /** Rendered instead of the children after a render error. Defaults to <ErrorState>. */
  fallback?: (props: ErrorBoundaryFallbackProps) => ReactNode;
  /** Report the error (e.g. to logging). Never shown to the user. */
  onError?: (error: unknown, info: ErrorInfo) => void;
  /** When any of these change (e.g. the route), the boundary resets itself. */
  resetKeys?: readonly unknown[];
}

interface ErrorBoundaryState {
  failed: boolean;
  error: unknown;
}

function changed(previous: readonly unknown[] = [], next: readonly unknown[] = []): boolean {
  return (
    previous.length !== next.length ||
    previous.some((value, index) => !Object.is(value, next[index]))
  );
}

/** Contains a render error to one part of the page instead of blanking the app. */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  override state: ErrorBoundaryState = { failed: false, error: null };

  static getDerivedStateFromError(error: unknown): ErrorBoundaryState {
    return { failed: true, error };
  }

  override componentDidCatch(error: unknown, info: ErrorInfo): void {
    this.props.onError?.(error, info);
  }

  override componentDidUpdate(previous: ErrorBoundaryProps): void {
    if (this.state.failed && changed(previous.resetKeys, this.props.resetKeys)) this.reset();
  }

  reset = (): void => {
    this.setState({ failed: false, error: null });
  };

  override render(): ReactNode {
    if (!this.state.failed) return this.props.children;
    const { fallback } = this.props;
    if (fallback) return fallback({ error: this.state.error, reset: this.reset });
    return <ErrorState error={this.state.error} onRetry={this.reset} />;
  }
}
