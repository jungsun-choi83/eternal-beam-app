import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  assetId: string;
  onFailure: () => void;
  children: ReactNode;
}

interface State {
  failed: boolean;
}

/**
 * A rejected lazy chunk or an unsupported renderer must not escape to the app
 * error screen. V1 is already mounted underneath, so fail this optional V2
 * branch and let the parent keep normal baked playback visible.
 */
export class ShakerLayeredBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.warn("Shaker layered V2 failed; continuing with V1", error, info);
    this.props.onFailure();
  }

  componentDidUpdate(previous: Props) {
    if (previous.assetId !== this.props.assetId && this.state.failed) {
      this.setState({ failed: false });
    }
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}
