// SPDX-License-Identifier: Apache-2.0
import { Component, type ReactNode } from "react";

/** A rendering error in one page (a graph library failing on this GPU, unexpected data) shows a
 * message instead of blanking the whole window; navigating elsewhere (resetKey) clears it. */
export class ErrorBoundary extends Component<
  { resetKey: string; labels: { title: string; home: string; copy: string }; children: ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidUpdate(prev: { resetKey: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const text = `${error.name}: ${error.message}\n${error.stack ?? ""}`;
    return (
      <div className="notice error stack-sm">
        <b>{this.props.labels.title}</b>
        <pre className="small">{text.slice(0, 2000)}</pre>
        <div className="row">
          <a href="#/">{this.props.labels.home}</a>
          <button className="link" onClick={() => navigator.clipboard?.writeText(text).catch(() => undefined)}>{this.props.labels.copy}</button>
        </div>
      </div>
    );
  }
}
