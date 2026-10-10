export interface Gpu {
  name: string;
  busy_percent: number | null;
  gtt_used: number;
  gtt_total: number;
  vram_used: number;
  vram_total: number;
  temp_c: number | null;
  power_w: number | null;
  gfx_clock_mhz: number | null;
}

export interface Service {
  name: string;
  url: string;
  state: string;
  model: string | null;
  decode_tps: number;
  prefill_tps: number;
  context_used: number;
  context_total: number;
  requests_processing: number;
  requests_waiting: number;
  error: string | null;
  prefill_progress: number | null;
}

export interface RequestRow {
  id: string;
  started_at: string;
  ended_at: string | null;
  status: string;
  source: string;
  model: string;
  backend: string | null;
  prompt: string;
  user: string | null;
  prompt_tokens: number;
  output_tokens: number;
  tps: number | null;
  duration_s: number | null;
}

export interface LlamaVersion {
  name: string;
  version: string | null;
}

export interface SystemInfo {
  hostname: string | null;
  ip: string | null;
  os: string | null;
  litellm_version: string | null;
  llama_server: LlamaVersion[];
}

export interface Snapshot {
  generated_at: string;
  fixtures: boolean;
  db_ok: boolean;
  db_error: string | null;
  overall: string;
  language?: string;
  system?: SystemInfo;
  host: {
    cpu_percent: number;
    cpu_cores: number;
    cpu_threads: number;
    ram_used: number;
    ram_total: number;
    disk_read_bps: number;
    disk_write_bps: number;
    gpu: Gpu | null;
  };
  services: Service[];
  series: Record<string, number[]>;
  requests: RequestRow[];
  totals: {
    requests: number;
    prompt_tokens: number;
    output_tokens: number;
  };
}
