// SPDX-License-Identifier: AGPL-3.0-or-later
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, HashRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import "@fontsource-variable/newsreader";
import "./styles.css";
import App from "./App";
import { DEMO } from "./demo/mode";
import { setDemoNotifier } from "./demo/demoApi";
import { toast } from "./lib";

// the static demo is served from a sub-path on GitHub Pages, so it routes on the hash: a deep link can never 404
const Router = DEMO ? HashRouter : BrowserRouter;
if (DEMO) setDemoNotifier(toast);

const client = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 3000 } } });

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={client}>
      <Router>
        <App />
      </Router>
    </QueryClientProvider>
  </React.StrictMode>,
);
