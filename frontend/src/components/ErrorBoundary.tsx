import React from 'react';

export class ErrorBoundary extends React.Component<React.PropsWithChildren, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return <main role="alert">
        <h1>Voice Comparison Studio</h1>
        <p>The interface could not be displayed. Reload to reconnect to your saved session.</p>
        <button onClick={() => window.location.reload()}>Reload</button>
      </main>;
    }
    return this.props.children;
  }
}
