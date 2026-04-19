import { Component, type ReactNode } from "react";
import { errorState, btnDanger, pageWrapper, pageStack, pageTitle, caption, mono } from "@/theme/styles";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

/**
 * Top-level error boundary — catches unhandled React errors and renders
 * a recovery UI instead of a blank screen or raw stack trace.
 *
 * Stateful by necessity (React class component requirement for error
 * boundaries), but the render output uses the design system.
 */
export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  private handleReset = () => {
    this.setState({ hasError: false, error: null });
    window.location.href = "/";
  };

  render() {
    if (!this.state.hasError) {
      return this.props.children;
    }

    return (
      <div className={`${pageWrapper} ${pageStack} mt-12`}>
        <h1 className={pageTitle}>Something went wrong</h1>
        <div className={errorState}>
          <p className="text-[13px] text-fail mb-2">
            An unexpected error occurred. This is a bug in Chronicle.
          </p>
          {this.state.error && (
            <pre className={`${mono} text-[11px] text-text-muted mt-2 max-w-xl text-left whitespace-pre-wrap break-words`}>
              {this.state.error.message}
            </pre>
          )}
          <button className={btnDanger} onClick={this.handleReset}>
            Return to dashboard
          </button>
        </div>
        <p className={caption}>
          If this keeps happening, please report the issue.
        </p>
      </div>
    );
  }
}
