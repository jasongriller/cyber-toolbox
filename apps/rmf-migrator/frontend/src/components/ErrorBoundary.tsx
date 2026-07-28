import { Component, type ReactNode } from "react";

interface State {
  failed: boolean;
}

/**
 * Last-resort catch for render crashes: swap the broken subtree for a visible
 * escape hatch instead of a blank page.
 */
export default class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <main className="app">
        <section className="panel" role="alert">
          <h2>Something went wrong</h2>
          <p className="muted">The page hit an unexpected error. Reloading starts it fresh.</p>
          <button
            type="button"
            className="btn btn--accent"
            onClick={() => window.location.reload()}
          >
            Reload
          </button>
        </section>
      </main>
    );
  }
}
