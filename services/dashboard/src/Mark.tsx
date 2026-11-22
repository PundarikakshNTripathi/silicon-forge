// SPDX-License-Identifier: AGPL-3.0-or-later
import { STAGES } from "./derive";

/** Five metal layers stacked into an S: contract, RTL, formal, synthesis, place and route. Same drawing as assets/logo.svg. */
export function Mark({ size = 36 }: { size?: number }) {
  return (
    <svg className="brand" width={size} height={size} viewBox="0 0 64 64" role="img" aria-label="SiliconForge">
      <rect x="7" y="5" width="50" height="9" rx="4.5" fill="var(--layer-contract)" />
      <rect x="7" y="17" width="22" height="9" rx="4.5" fill="var(--layer-rtl)" />
      <rect x="7" y="29" width="50" height="9" rx="4.5" fill="var(--layer-formal)" />
      <rect x="35" y="41" width="22" height="9" rx="4.5" fill="var(--layer-lec)" />
      <rect x="7" y="53" width="50" height="9" rx="4.5" fill="var(--layer-pnr)" />
    </svg>
  );
}

/** Seven thin layers in flow order, one per stage: a signature that is also the legend. */
export function StackBar() {
  return <div className="stackbar" aria-hidden="true">{STAGES.map((s) => <i key={s.key} style={{ "--c": `var(--layer-${s.key})` } as React.CSSProperties} />)}</div>;
}
