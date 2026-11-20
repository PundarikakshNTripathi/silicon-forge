export interface PPAMetrics {
  wns_ns: number;
  tns_ns: number;
  area_um2: number;
  total_power_mw: number;
  cell_count: number;
  drc_violations: number;
  lvs_clean: boolean;
  runtime_sec: number;
  reward: number;
  knobs: Record<string, string | number>;
}

export interface PipelineEvent {
  stage: string;
  status: string;
  detail: string;
}

export interface CostSummary {
  total_cost_usd: number;
  total_tokens: number;
  cache_hit_rate: number;
}

export interface RunResponse {
  state: {
    run_id: string;
    stage: string;
    signoff_passed: boolean;
    lec_passed: boolean;
    gds_path: string | null;
    best_ppa: PPAMetrics | null;
    ppa_episodes: PPAMetrics[];
    pareto_front: PPAMetrics[];
    events: PipelineEvent[];
    rtl_source: string;
  };
  cost: CostSummary;
}
