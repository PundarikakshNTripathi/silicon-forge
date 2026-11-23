// SPDX-License-Identifier: AGPL-3.0-or-later
// The Agent page in the static demo: the replay, and the terminal interface as captures. (The live page needs a local folder and a model.)
import { useSearchParams } from "react-router-dom";
import Replay, { TerminalTab } from "./Replay";

export default function DemoAgent() {
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") === "terminal" ? "terminal" : "replay";
  const set = (t: "replay" | "terminal") => setSp(t === "replay" ? {} : { tab: t }, { replace: true });
  return (
    <>
      <div className="topbar"><span className="title">Agent: replay of a recorded session</span></div>
      <div className="page stack">
        <div className="tabs" role="tablist">
          <button role="tab" aria-selected={tab === "replay"} onClick={() => set("replay")}>Replay in the browser</button>
          <button role="tab" aria-selected={tab === "terminal"} onClick={() => set("terminal")}>Terminal interface</button>
        </div>
        {tab === "replay" ? <Replay /> : <TerminalTab />}
      </div>
    </>
  );
}
