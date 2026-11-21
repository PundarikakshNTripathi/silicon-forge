// SPDX-License-Identifier: AGPL-3.0-or-later
import { STAGES } from "./derive";

/** One conductor routed through five metal layers: the S is the flow. Same drawing as assets/logo.svg, themed by the layer tokens. */
export function Mark({ size = 36 }: { size?: number }) {
  const s = { fill: "none", strokeWidth: 9 } as const;
  return (
    <svg className="brand" width={size} height={size} viewBox="0 0 64 64" role="img" aria-label="SiliconForge">
      <path {...s} stroke="var(--layer-contract)" d="M46 13H13.5" />
      <path {...s} stroke="var(--layer-rtl)" d="M18 8.5V36.5" />
      <path {...s} stroke="var(--layer-formal)" d="M13.5 32H50.5" />
      <path {...s} stroke="var(--layer-lec)" d="M46 27.5V56.5" />
      <path {...s} stroke="var(--layer-pnr)" d="M50.5 51H18" />
      <rect x="41" y="8" width="10" height="10" fill="var(--chrome)" stroke="var(--layer-contract)" strokeWidth="3" />
      <rect x="8" y="46" width="12" height="12" fill="var(--chrome-ink)" />
    </svg>
  );
}

/** Seven thin layers in flow order, one per stage: a signature that is also the legend. */
export function StackBar() {
  return <div className="stackbar" aria-hidden="true">{STAGES.map((s) => <i key={s.key} style={{ "--c": `var(--layer-${s.key})` } as React.CSSProperties} />)}</div>;
}
