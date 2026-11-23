// SPDX-License-Identifier: AGPL-3.0-or-later
// What the guided tour says and where it points. Targets are CSS selectors, so the pages themselves stay untouched; a step whose
// target is missing (data still loading, a different mode) is shown centred instead of failing.

export interface Step {
  /** Where this step lives, as pathname plus search ("/evals?tab=agents"). The tour navigates there first. */
  route?: string;
  /** CSS selector of what to point at. No target: the step is a centred card. */
  target?: string;
  title: string;
  body: string;
  placement?: "top" | "right" | "bottom" | "left";
  /** The first card of the full tour: asks whether to start rather than just showing "Next". */
  welcome?: boolean;
}

export const FIRST_RUN = "demo-up_down_counter";

/** The one-minute walk through the static demo, across pages. */
export const DEMO_TOUR: Step[] = [
  {
    route: "/", welcome: true, title: "Welcome to SiliconForge",
    body: "SiliconForge takes a written description of a small chip block and checks it until it can be trusted. It writes the design, proves it correct with formal tools, and lays it out as an actual chip layout. What you are looking at is a recording of it working, so nothing here runs live. Want a one-minute walk through?",
  },
  {
    route: "/", target: ".rail", placement: "right", title: "This list is the whole app",
    body: "Work is where things get done: start a run, look at past runs, talk to the agent. Evidence is where we show how well it all performs. Setup covers models and tools. The page you are on is highlighted.",
  },
  {
    route: "/", target: ".rail .search", placement: "right", title: "Lost? Jump",
    body: "Press Ctrl K, or click here, and type the name of a page or a run. You land there straight away.",
  },
  {
    route: "/", target: ".tour-cards", placement: "bottom", title: "Four things worth a minute",
    body: "These are the parts that show the most. We will go through them in order. You can also click any of them yourself later.",
  },
  {
    route: "/runs", target: ".page table.t", placement: "top", title: "A run is one trip from spec to layout",
    body: "Each row is a complete run: someone wrote what the block should do, and the tools checked the result. These two were made with real tools, and the small stamp says so. A hatched amber stamp would mean a stand-in produced the numbers, so you can always tell the two apart. Click a row to open it.",
  },
  {
    route: `/runs/${FIRST_RUN}`, target: ".titleblock", placement: "bottom", title: "What you are looking at",
    body: "The strip at the top says which design this is, which version of the code, what produced the evidence, how it ended, and how long and how much it cost.",
  },
  {
    route: `/runs/${FIRST_RUN}`, target: ".titleblock + div", placement: "bottom", title: "Results against the limits",
    body: "The spec set limits: the chip has to be fast enough, small enough and frugal enough. Each row shows what came out against its limit. Meets or Fails comes from the tool's numbers. The model has no say in it.",
  },
  {
    route: `/runs/${FIRST_RUN}`, target: ".strip", placement: "top", title: "The flow, stage by stage",
    body: "Each stage has its own colour, drawn like a timing diagram. The amber marker is where formal verification found a bug and sent the design back to be fixed. Click a stage to jump to its details.",
  },
  {
    route: `/runs/${FIRST_RUN}`, target: ".tabs", placement: "bottom", title: "Everything else is in the tabs",
    body: "The design and its code, the proofs, timing and power, the layout picture with downloadable files, and what each model call cost.",
  },
  {
    route: "/workspace", target: ".rp-prov", placement: "bottom", title: "A replay of the agent, and what is real",
    body: "This page replays a recorded session. The agent, its tools and the checker are the real code. The language model is a script of fixed replies, so no AI model is being judged here. The page says so, and so do we.",
  },
  {
    route: "/workspace", target: ".rp-controls", placement: "bottom", title: "Press Play, then decide",
    body: "It runs until the agent wants to change a file, then stops and asks you, the same way the real tool does. Approve it, or ask for a revision. Both outcomes were recorded from the real agent. Step moves one event at a time.",
  },
  {
    route: "/evals?tab=agents", target: ".tabs", placement: "bottom", title: "Measurements, in two kinds",
    body: "Model quality tests language models at writing chip code. Agent loops compares three ways of building the agent, ours and two popular frameworks, on the same tasks with the same model.",
  },
  {
    route: "/evals?tab=agents", target: "svg.chart", placement: "top", title: "How to read the chart",
    body: "The dot is the success rate. The bar around it is a 95% interval, the range the true rate probably sits in. If two bars overlap, the data cannot tell the setups apart. With 13 tasks the bars are wide, and that is the honest answer.",
  },
  {
    route: "/cost", target: ".grid4", placement: "bottom", title: "Insights",
    body: "How the agent behaves over many tasks: where its time goes, how often its tools work, and whether it recovers after a failure. These figures come from the same runs as the chart before.",
  },
  {
    route: "/note", target: ".note h2", placement: "right", title: "The author's note",
    body: "The last stop: what was built, what was measured, and what the numbers do not show.",
  },
  {
    route: "/", target: ".rail a[href*='github.com'], .src-link", placement: "right", title: "The source code",
    body: "All of this is open source (AGPL-3.0). This link goes to the repository: the code, the documentation, and the result files behind every number you have seen. The README says what each part is and what has not been shown.",
  },
  {
    route: "/", title: "That is the tour",
    body: "You can run it again from Take the tour in the sidebar, and the source link is just below it. Every page also has an About this page line at the top, with a shorter walk through just that page.",
  },
];

/** Shorter walks through one page, used by "Show me around" in both the demo and the real app. Keys come from helpKey(). */
export const PAGE_TOURS: Record<string, Step[]> = {
  new: [
    { target: "textarea", placement: "right", title: "Say what you want",
      body: "Write the block's interface, its clock and reset, and what must always be true. Plain text is fine. The buttons above the box fill in a bundled example if you would rather start from one." },
    { target: "[aria-label='Model profile']", placement: "left", title: "Pick who writes the design",
      body: "Bundled examples need no model at all. A local model runs on your own graphics card. Cloud options need an API key and cost money; the estimate at the bottom tells you roughly how much." },
    { target: "[aria-label='EDA backend']", placement: "left", title: "Pick what checks it",
      body: "Simulated is a stand-in that runs anywhere and is labelled as such. The others use the real tools (Yosys, SymbiYosys, OpenROAD) and need them installed." },
    { target: ".sticky-bar", placement: "top", title: "Start",
      body: "The bar shows whether the results will be real or simulated, and the expected cost. When everything above looks right, start the run." },
  ],
  runs: [
    { target: ".page table.t", placement: "top", title: "Your runs",
      body: "One row per run. The stamp says whether real tools or a stand-in produced the numbers. Click a row to open it, or use the search box and filter above the table." },
  ],
  run: [
    { target: ".titleblock", placement: "bottom", title: "What this run is",
      body: "The design, its code revision, what produced the evidence, how it ended, and the time and cost." },
    { target: ".titleblock + div", placement: "bottom", title: "Results against the limits",
      body: "What came out, next to the limit the spec set. Meets or Fails is the tool's call." },
    { target: ".strip", placement: "top", title: "The flow",
      body: "Stages in order, drawn against time. Amber marks a stage that sent the design back for repair. Click a stage for its details." },
    { target: ".tabs", placement: "bottom", title: "The details",
      body: "Design, verification, performance, layout and files, the agent's log and the cost. A dot on a tab means something there needs a look." },
  ],
  workspace: [
    { target: ".composer", placement: "top", title: "Talk to the agent",
      body: "It works inside the folder you chose. Ask for a change or a question. By default it asks before it edits a file or runs a command." },
    { target: ".chat", placement: "top", title: "What it did",
      body: "Each tool call and result shows up here. An approval card appears when it wants to change something." },
  ],
  evals: [
    { target: ".tabs", placement: "bottom", title: "Two views",
      body: "Model quality tests models at writing chip code. Agent loops compares ways of building the agent." },
    { target: ".page table.t, svg.chart", placement: "top", title: "Read the intervals",
      body: "Every score comes with a 95% interval. When two intervals overlap, the data cannot tell the two setups apart." },
  ],
  cost: [
    { target: ".grid4", placement: "bottom", title: "The headline figures",
      body: "Spending, tokens and runs at a glance. Local models and bundled examples cost nothing." },
  ],
  models: [
    { target: ".panel", placement: "bottom", title: "Where models come from",
      body: "Run one on your own machine, add an API key for a cloud model, or reuse a subscription you already have. The table at the bottom shows which model each profile uses for which kind of work." },
  ],
};

/** Plain-language description of each page, shown as "About this page". `demo` replaces `live` in the static demo when given. */
export const HELP: Record<string, { title: string; live: string; demo?: string }> = {
  new: {
    title: "Start a run",
    live: "Describe a chip block in plain text. The agent writes the design, proves it, and lays it out. Pick a bundled example if you just want to see it work.",
    demo: "In the real app this page starts a run. In this demo nothing runs, so Start opens a recorded run instead.",
  },
  runs: {
    title: "Runs",
    live: "Every run so far. A run is one trip from a written spec to a finished layout. Click a row to open it.",
    demo: "Two recorded runs made with real tools. A run is one trip from a written spec to a finished layout. Click a row to open it.",
  },
  run: {
    title: "One run, in full",
    live: "The top shows what was run and the results against your limits. Below it are the stages in order, then tabs with the design, proofs, timing and layout.",
  },
  workspace: {
    title: "Agent",
    live: "Chat with the agent inside a folder on your computer. It reads files, makes changes and runs checks, and asks before it changes anything.",
    demo: "A replay of a recorded agent session. Press Play; it pauses when the agent wants to change a file and waits for your decision.",
  },
  evals: {
    title: "Evals",
    live: "How good the models and agents are, measured on fixed tasks. Every number comes from a result file in the repository.",
  },
  cost: {
    title: "Insights",
    live: "What runs cost, and how the agent behaves: where its time goes, how often its tools work, and how it recovers from failures.",
  },
  models: {
    title: "Models",
    live: "Choose where the language models come from: your own machine, an API key, or a subscription you already pay for.",
    demo: "Where models would be set up. This demo shows the catalogue only; nothing here can be changed.",
  },
  knowledge: {
    title: "Knowledge",
    live: "Short reference notes the agent can look up while it works, such as lint warning codes and flow settings. You can edit them.",
  },
  system: {
    title: "System",
    live: "Which tools are installed on this machine and which backends are ready. Check here first if a run says tools are missing.",
    demo: "Which tools would be installed on a real machine. The demo shows neutral values and reveals nothing about the computer that built it.",
  },
  note: {
    title: "Engineering note",
    live: "A short note from the author.",
  },
};

/** Which help and tour entry a route uses. */
export function helpKey(pathname: string): string | null {
  if (pathname === "/" || pathname === "") return null;
  if (pathname.startsWith("/runs/")) return "run";
  const k = pathname.slice(1).split("/")[0];
  return k in HELP ? k : null;
}
