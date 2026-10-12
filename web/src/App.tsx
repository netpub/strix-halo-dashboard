import { useEffect, useMemo, useState } from "react";
import { clock, duration, formatNumber, gb, mbs, tokens } from "./format";
import { catalogs, fill, translateError, useI18n } from "./i18n";
import type { Catalog } from "./i18n/types";
import { useTheme, type ThemeName } from "./theme";
import type { LlamaVersion, RequestRow, Service, Snapshot, SystemInfo } from "./types";

const STATE_ORDER = ["idle", "prefill", "generating", "queued", "error"] as const;

export function App() {
  const { catalog: t, language, languages, setLanguage, acceptDefault } = useI18n();
  const { theme, setTheme } = useTheme();
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [pinned, setPinned] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    const source = new EventSource("/api/stream");
    source.onmessage = (event) => {
      setSnap(JSON.parse(event.data) as Snapshot);
      setOffline(false);
    };
    source.onerror = () => setOffline(true);
    return () => source.close();
  }, []);

  useEffect(() => {
    acceptDefault(snap?.language);
  }, [snap?.language, acceptDefault]);

  const live = useMemo(() => pickService(snap?.services ?? [], null), [snap]);
  const contextService = useMemo(() => pickService(snap?.services ?? [], pinned), [snap, pinned]);

  if (!snap) {
    return (
      <main className="page">
        <header className="top">
          <Brand catalog={t} />
          <div className="top-actions">
            <ThemeSwitch theme={theme} onChange={setTheme} catalog={t} />
            <LanguageSwitch language={language} languages={languages} onChange={setLanguage} label={t.languageLabel} />
          </div>
        </header>
        <p className="loading">{t.connecting}</p>
      </main>
    );
  }

  const gpu = snap.host.gpu;
  const active = snap.requests.find((row) => row.status === "generating" || row.status === "prefill");
  const phase = active?.status === "prefill" ? t.phaseReading : active ? t.phaseAnswering : stateLabel(t, snap.overall);
  const progress = live?.state === "prefill" && live.prefill_progress != null
    ? live.prefill_progress
    : live?.state === "generating"
      ? 1
      : 0;
  const serviceErrors = snap.services
    .filter((service) => service.error)
    .map((service) => `${service.name}: ${translateError(service.error ?? "", t.errors)}`)
    .join(" · ");

  return (
    <main className="page">
      <header className="top">
        <Brand catalog={t} fixtures={snap.fixtures} />
        <div className="top-actions">
          <ThemeSwitch theme={theme} onChange={setTheme} catalog={t} />
          <LanguageSwitch language={language} languages={languages} onChange={setLanguage} label={t.languageLabel} />
          <div className={`live live-${snap.overall}`}>
            <i />
            {stateLabel(t, snap.overall)}
            {active?.tps != null && <span> · {formatNumber(active.tps, 1, t.locale)} tok/s</span>}
          </div>
        </div>
      </header>

      <SystemLine system={snap.system} catalog={t} />

      {(offline || snap.db_error || serviceErrors) && (
        <p className="banner">
          {offline && `${t.offline} `}
          {snap.db_error && `${translateError(snap.db_error, t.errors)} `}
          {serviceErrors}
        </p>
      )}

      <section className="card state-card">
        <div className="state-head">
          <div>
            <p className="eyebrow">{t.modelState}</p>
            <div className="legend">
              {STATE_ORDER.map((state) => (
                <span key={state} className={snap.overall === state ? "on" : ""}>
                  <i className={`dot dot-${state}`} />
                  {stateLabel(t, state)}
                </span>
              ))}
            </div>
          </div>
          <p className="state-side">
            {active
              ? fill(t.tokensRate, {
                tokens: tokens(active.output_tokens, t.locale),
                rate: formatNumber(active.tps ?? 0, 1, t.locale),
              })
              : t.noReply}
          </p>
        </div>
        <p className="phase">{phase}</p>
        <div className="bar"><span style={{ width: `${Math.round(progress * 100)}%` }} /></div>
        <div className="chips">
          {snap.services.map((service) => (
            <button
              key={`${service.name}:${service.model ?? ""}`}
              type="button"
              className={`chip ${contextService?.name === service.name ? "selected" : ""}`}
              onClick={() => setPinned((current) => (current === service.name ? null : service.name))}
            >
              <i className={`dot dot-${service.state}`} />
              <strong>{service.name}</strong>
              <span>{service.model ?? "llama-server"}</span>
              <em>{stateLabel(t, service.state)}</em>
            </button>
          ))}
          {snap.services.length === 0 && <span className="muted">{t.noBackend}</span>}
        </div>
      </section>

      <section className="metrics">
        <article className="card metric speed">
          <p className="eyebrow">{t.speed}</p>
          <div className="speed-row">
            <p><strong>{formatNumber(live?.decode_tps ?? 0, 1, t.locale)}</strong><small> tok/s</small></p>
            <p className="secondary"><strong>{formatNumber(live?.prefill_tps ?? 0, 0, t.locale)}</strong><small> tok/s</small></p>
          </div>
          <div className="speed-labels"><span>{t.decode}</span><span>{t.prefill}</span></div>
          <Sparkline data={snap.series.decode_tps} color="#2fbe86" />
          <p className="foot">{live ? live.name : t.noService}</p>
        </article>
        <Metric
          label={t.gpuLoad}
          value={gpu?.busy_percent == null ? "–" : formatNumber(gpu.busy_percent, 0, t.locale)}
          unit="%"
          note={gpu?.name ?? t.amdgpuHidden}
          data={snap.series.gpu}
          color="#2fbe86"
        />
        <Metric
          label={t.gtt}
          value={gpu ? gb(gpu.gtt_used, t.locale) : "–"}
          unit={gpu ? `/ ${gb(gpu.gtt_total, t.locale, 0)} GB` : ""}
          note={gpu ? fill(t.vramReserve, { used: gb(gpu.vram_used, t.locale), total: gb(gpu.vram_total, t.locale, 0) }) : t.sharedMemory}
          data={snap.series.gtt}
          color="#7ddec0"
        />
        <Metric
          label={t.gpuTemp}
          value={gpu?.temp_c == null ? "–" : formatNumber(gpu.temp_c, 0, t.locale)}
          unit="°C"
          note={gpu?.gfx_clock_mhz ? `${formatNumber(gpu.gfx_clock_mhz, 0, t.locale)} MHz` : t.igpu}
          data={snap.series.temp}
          color="#d7b15a"
        />
        <Metric
          label={t.power}
          value={gpu?.power_w == null ? "–" : formatNumber(gpu.power_w, 0, t.locale)}
          unit="W"
          note={t.package}
          data={snap.series.power}
          color="#7ddec0"
        />
        <Metric
          label={t.cpu}
          value={formatNumber(snap.host.cpu_percent, 0, t.locale)}
          unit="%"
          note={fill(t.coresThreads, { cores: snap.host.cpu_cores, threads: snap.host.cpu_threads })}
          data={snap.series.cpu}
          color="#2fbe86"
        />
        <Metric
          label={t.diskRead}
          value={mbs(snap.host.disk_read_bps, t.locale)}
          unit="MB/s"
          note={fill(t.diskWrite, { value: mbs(snap.host.disk_write_bps, t.locale) })}
          data={snap.series.disk_read}
          color="#6cb4ea"
        />
        <article className="card metric">
          <p className="eyebrow">{t.requests}</p>
          <p className="figure">
            {formatNumber(snap.services.reduce((sum, service) => sum + service.requests_processing, 0), 0, t.locale)}
            <small> {t.activeSuffix}</small>
          </p>
          <p className="note">
            {fill(t.waiting, {
              count: formatNumber(snap.services.reduce((sum, service) => sum + service.requests_waiting, 0), 0, t.locale),
            })}
          </p>
          <Sparkline data={snap.series.decode_tps} color="#6cb4ea" />
        </article>
      </section>

      <section className="bottom">
        <article className="card memory">
          <p className="eyebrow">{t.context}</p>
          <Donut used={contextService?.context_used ?? 0} total={contextService?.context_total ?? 0} />
          <p className="memory-model">{contextService ? `${contextService.name} · ${contextService.model ?? "llama-server"}` : t.noModel}</p>
          <Bar label={t.gtt} value={gpu && gpu.gtt_total ? gpu.gtt_used / gpu.gtt_total : 0} text={gpu ? `${gb(gpu.gtt_used, t.locale)} / ${gb(gpu.gtt_total, t.locale, 0)} GB` : "–"} color="#2fbe86" />
          <Bar label={t.systemRam} value={snap.host.ram_total ? snap.host.ram_used / snap.host.ram_total : 0} text={`${gb(snap.host.ram_used, t.locale)} / ${gb(snap.host.ram_total, t.locale, 0)} GB`} color="#e07b86" />
          <Bar label={t.gpuTemp} value={gpu?.temp_c != null ? Math.min(1, gpu.temp_c / 100) : 0} text={gpu?.temp_c == null ? "–" : `${formatNumber(gpu.temp_c, 0, t.locale)} °C`} color="#e0b15a" />
        </article>

        <article className="card requests">
          <p className="eyebrow">{t.recentRequests}</p>
          <div className="table-scroll">
            <div className="table-wrap" tabIndex={0} role="region" aria-label={t.recentRequests}>
              <table>
              <thead>
                <tr>
                  <th>{t.time}</th>
                  <th>{t.status}</th>
                  <th>{t.source}</th>
                  <th>{t.model}</th>
                  <th>{t.question}</th>
                  <th>{t.promptTokens}</th>
                  <th>{t.outputTokens}</th>
                  <th>tok/s</th>
                  <th>{t.duration}</th>
                </tr>
              </thead>
              <tbody>
                {snap.requests.map((row) => (
                  <RequestLine key={row.id} row={row} />
                ))}
                {snap.requests.length === 0 && (
                  <tr><td colSpan={9} className="empty">{t.noRequests}</td></tr>
                )}
              </tbody>
              </table>
            </div>
          </div>
          <p className="table-foot">
            {fill(t.sinceStart, {
              requests: formatNumber(snap.totals.requests, 0, t.locale),
              prompt: tokens(snap.totals.prompt_tokens, t.locale),
              output: tokens(snap.totals.output_tokens, t.locale),
            })}
          </p>
        </article>
      </section>
    </main>
  );
}

function stateLabel(catalog: Catalog, state: string): string {
  return catalog.states[state] ?? state;
}

function SystemLine({ system, catalog }: { system?: SystemInfo; catalog: Catalog }) {
  return (
    <p className="system">
      <span><em>{catalog.host}</em>{system?.hostname || "–"}</span>
      <span><em>{catalog.ip}</em>{system?.ip || "–"}</span>
      <span><em>{catalog.os}</em>{system?.os || "–"}</span>
      <span><em>{catalog.litellm}</em>{system?.litellm_version || "–"}</span>
      <span><em>{catalog.llamaServer}</em>{llamaVersion(system?.llama_server ?? [])}</span>
    </p>
  );
}

function llamaVersion(rows: LlamaVersion[]): string {
  const known = rows.filter((row) => row.version);
  if (known.length === 0) return "–";
  const unique = new Set(known.map((row) => row.version));
  if (unique.size === 1 && known.length === rows.length) return known[0].version ?? "–";
  return rows.map((row) => `${row.name} ${row.version ?? "–"}`).join(" · ");
}

function Brand({ catalog, fixtures = false }: { catalog: Catalog; fixtures?: boolean }) {
  return (
    <div className="brand">
      <img className="logo" src="/logo-desktop.svg" alt="network.publishing Möller-Westbunk GmbH" />
      <span className="nav-pill">{catalog.product}</span>
      {fixtures && <span className="fixture">{catalog.sampleData}</span>}
    </div>
  );
}

function ThemeSwitch({ theme, onChange, catalog }: { theme: ThemeName; onChange: (theme: ThemeName) => void; catalog: Catalog }) {
  return (
    <div className="lang theme" role="group" aria-label={catalog.themeLabel}>
      <button type="button" className={theme === "light" ? "on" : ""} aria-pressed={theme === "light"} aria-label={catalog.themeLight} title={catalog.themeLight} onClick={() => onChange("light")}>
        <SunIcon />
      </button>
      <button type="button" className={theme === "dark" ? "on" : ""} aria-pressed={theme === "dark"} aria-label={catalog.themeDark} title={catalog.themeDark} onClick={() => onChange("dark")}>
        <MoonIcon />
      </button>
    </div>
  );
}

function SunIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="4" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <path d="M12 2.6v2.2M12 19.2v2.2M2.6 12h2.2M19.2 12h2.2M5.2 5.2l1.6 1.6M17.2 17.2l1.6 1.6M18.8 5.2l-1.6 1.6M6.8 17.2l-1.6 1.6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M20 14.6A7.4 7.4 0 0 1 9.4 4 7.2 7.2 0 1 0 20 14.6Z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
    </svg>
  );
}

function LanguageSwitch({ language, languages, onChange, label }: { language: string; languages: string[]; onChange: (language: string) => void; label: string }) {
  return (
    <div className="lang" role="group" aria-label={label}>
      {languages.map((id) => (
        <button key={id} type="button" className={id === language ? "on" : ""} aria-pressed={id === language} onClick={() => onChange(id)}>
          {catalogs[id]?.switchLabel ?? id.toUpperCase()}
        </button>
      ))}
    </div>
  );
}

function Metric({ label, value, unit, note, data, color }: { label: string; value: string; unit: string; note: string; data: number[]; color: string }) {
  return (
    <article className="card metric">
      <p className="eyebrow">{label}</p>
      <p className="figure">{value}{unit && <small> {unit}</small>}</p>
      <p className="note">{note}</p>
      <Sparkline data={data} color={color} />
    </article>
  );
}

function Sparkline({ data, color }: { data: number[] | undefined; color: string }) {
  const points = data ?? [];
  if (points.length < 2) return <svg className="spark" viewBox="0 0 280 42" />;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const coords = points.map((value, index) => {
    const x = (index / (points.length - 1)) * 280;
    const y = 40 - ((value - min) / span) * 34;
    return [x, y] as const;
  });
  const line = coords.map(([x, y], index) => `${index ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  return (
    <svg className="spark" viewBox="0 0 280 42" preserveAspectRatio="none">
      <path d={`${line} L280,42 L0,42 Z`} fill={color} opacity="0.16" />
      <path d={line} fill="none" stroke={color} strokeWidth="1.7" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function Donut({ used, total }: { used: number; total: number }) {
  const { catalog: t } = useI18n();
  const ratio = total > 0 ? Math.max(0, Math.min(1, used / total)) : 0;
  const radius = 46;
  const circ = 2 * Math.PI * radius;
  return (
    <div className="donut">
      <svg viewBox="0 0 120 120">
        <circle cx="60" cy="60" r={radius} className="track" />
        <circle
          cx="60"
          cy="60"
          r={radius}
          className="value"
          strokeDasharray={`${circ * ratio} ${circ}`}
        />
      </svg>
      <div>
        <strong>{formatNumber(ratio * 100, 0, t.locale)}%</strong>
        <span>{tokens(used, t.locale)} / {tokens(total, t.locale)}</span>
      </div>
    </div>
  );
}

function Bar({ label, value, text, color }: { label: string; value: number; text: string; color: string }) {
  return (
    <div className="mem-row">
      <div className="mem-label"><span>{label}</span><span>{text}</span></div>
      <div className="bar thin"><span style={{ width: `${Math.round(value * 100)}%`, background: color }} /></div>
    </div>
  );
}

function RequestLine({ row }: { row: RequestRow }) {
  const { catalog: t } = useI18n();
  return (
    <tr>
      <td>{clock(row.started_at, t.locale)}</td>
      <td><span className={`status status-${row.status}`}><i />{stateLabel(t, row.status)}</span></td>
      <td>{row.source === "litellm" ? "LiteLLM" : t.direct}</td>
      <td><span className="model-pill">{row.model}</span></td>
      <td className="prompt" title={row.prompt}>{row.prompt || "–"}{row.user ? ` · ${row.user}` : ""}</td>
      <td>{formatNumber(row.prompt_tokens, 0, t.locale)}</td>
      <td>{formatNumber(row.output_tokens, 0, t.locale)}</td>
      <td>{row.tps == null ? "–" : formatNumber(row.tps, 1, t.locale)}</td>
      <td>{duration(row.duration_s, t.locale)}</td>
    </tr>
  );
}

function pickService(services: Service[], pinned: string | null): Service | null {
  if (pinned) {
    const chosen = services.find((service) => service.name === pinned);
    if (chosen) return chosen;
  }
  for (const state of ["generating", "prefill", "queued", "idle", "error"]) {
    const found = services.find((service) => service.state === state);
    if (found) return found;
  }
  return services[0] ?? null;
}
