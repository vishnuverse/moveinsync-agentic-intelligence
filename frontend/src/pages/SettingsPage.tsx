import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type {
  ChartSeriesData,
  GateMode,
  GateSettings,
  NotificationCadence,
  RulesResponse,
  SignalRuleParams,
  UsageStatsResponse,
} from "../api";
import { BreakdownBarChart } from "../charts/BreakdownBarChart";
import { ChartPanel } from "../charts/ChartPanel";
import { TrendLineChart } from "../charts/TrendLineChart";
import { EmptyState, ErrorState, LoadingState } from "../components/AsyncStatus";
import "./SettingsPage.css";

// Plan SP-B §B0: the two Major Risk Hotspot signal types -- always
// escalate + always immediate, non-overridable in the backend (gate.py's
// safety floor). Rendered first, pinned, with their controls disabled so
// the UI never implies they can be suppressed or batched.
const HOTSPOT_SIGNAL_TYPES = new Set(["incident", "escort_compliance_violation"]);

const SIGNAL_TYPE_LABELS: Record<string, string> = {
  incident: "Safety Incident",
  escort_compliance_violation: "Female Traveling Without an Escort",
  delay_breach: "Route Delay Breach",
  cost_divergence: "Vendor Cost Divergence",
  emissions_over_target: "Emissions Over Target",
  attendance_correlated_with_transport: "Attendance ↔ Transport Correlation",
  attendance_unrelated_late: "Attendance Lateness (Unrelated to Transport)",
  billing_discrepancy: "Billing Slab Discrepancy",
  performance_variability: "Route/Vendor Inconsistency (std-dev)",
};

const PARAM_LABELS: Record<string, string> = {
  delay_threshold_minutes: "Delay threshold (min)",
  drop_delay_critical_minutes: "Critical drop-delay threshold (min)",
  divergence_pct: "Divergence threshold (fraction, e.g. 0.2 = 20%)",
  min_ratio_over_baseline: "Min ratio over baseline",
  min_late_samples: "Min late samples",
  signal_limit: "Per-tick signal cap",
  transport_correlation_ratio: "Transport-correlated ratio (≥)",
  unrelated_correlation_ratio: "Unrelated ratio (≤)",
  min_slab_sample: "Min slab sample size",
  min_discrepancy_inr: "Min discrepancy (INR)",
  cv_threshold_pct: "Coefficient-of-variation threshold (%)",
  min_sample_size: "Min sample size",
  variability_minutes_floor: "Variability floor (min, when mean ≈ 0)",
  violation_limit: "Per-tick violation cap",
  night_window_start_hour: "Night window start (hour, 0-23)",
  night_window_end_hour: "Night window end (hour, 0-23)",
  severity_threshold: "Minimum severity",
};

const GATE_MODE_OPTIONS: { value: GateMode; label: string }[] = [
  { value: "auto", label: "Auto (let the gate decide)" },
  { value: "force_suppress", label: "Force suppress" },
  { value: "force_rule_only", label: "Force rule-only (skip LLM)" },
  { value: "force_escalate", label: "Force escalate (always LLM)" },
];

const CADENCE_OPTIONS: { value: NotificationCadence; label: string }[] = [
  { value: "immediate", label: "Immediate" },
  { value: "hourly", label: "Hourly" },
  { value: "every_2_hours", label: "Every 2 hours" },
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
];

function SignalRuleCard({
  rule,
  onChange,
  onReset,
}: {
  rule: SignalRuleParams;
  onChange: (next: SignalRuleParams) => void;
  onReset: () => void;
}) {
  const isHotspot = HOTSPOT_SIGNAL_TYPES.has(rule.signal_type);
  const label = SIGNAL_TYPE_LABELS[rule.signal_type] ?? rule.signal_type;

  function updateParam(key: string, raw: string) {
    const asNumber = Number(raw);
    const value = raw === "" || Number.isNaN(asNumber) ? raw : asNumber;
    onChange({ ...rule, params: { ...rule.params, [key]: value } });
  }

  return (
    <div className={`settings-card${isHotspot ? " settings-card-hotspot" : ""}`}>
      <div className="settings-card-header">
        <h3>{label}</h3>
        {isHotspot && <span className="badge badge-critical">Safety-critical — always immediate</span>}
      </div>

      <div className="settings-grid">
        {Object.entries(rule.params).map(([key, value]) => (
          <label key={key} className="settings-field">
            <span className="settings-field-label">{PARAM_LABELS[key] ?? key}</span>
            <input
              className="settings-input"
              type={typeof value === "number" ? "number" : "text"}
              value={value}
              disabled={isHotspot && key !== "params-editable-placeholder"}
              onChange={(e) => updateParam(key, e.target.value)}
            />
          </label>
        ))}
      </div>

      <div className="settings-grid">
        <label className="settings-field">
          <span className="settings-field-label">Gate mode</span>
          <select
            className="settings-select"
            value={rule.gate_mode}
            disabled={isHotspot}
            title={isHotspot ? "Always escalated — safety-critical, cannot be overridden." : undefined}
            onChange={(e) => onChange({ ...rule, gate_mode: e.target.value as GateMode })}
          >
            {GATE_MODE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>
        <label className="settings-field">
          <span className="settings-field-label">Notification cadence</span>
          <select
            className="settings-select"
            value={rule.notification_cadence}
            disabled={isHotspot}
            title={isHotspot ? "Always immediate — safety-critical, cannot be batched." : undefined}
            onChange={(e) => onChange({ ...rule, notification_cadence: e.target.value as NotificationCadence })}
          >
            {CADENCE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {!isHotspot && (
        <button type="button" className="link-btn settings-reset-btn" onClick={onReset}>
          Reset to default
        </button>
      )}
    </div>
  );
}

export function SettingsPage() {
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [rules, setRules] = useState<RulesResponse | null>(null);
  const [usage, setUsage] = useState<UsageStatsResponse | null>(null);
  const [funnel, setFunnel] = useState<ChartSeriesData | null>(null);
  const [llmUsage, setLlmUsage] = useState<ChartSeriesData | null>(null);
  const [dirtySignalTypes, setDirtySignalTypes] = useState<Set<string>>(new Set());
  const [gateSettingsDirty, setGateSettingsDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);

  function load() {
    setStatus("loading");
    Promise.all([api.getRules(), api.getUsageStats(), api.getSignalGateFunnel(30), api.getLlmUsage(14)])
      .then(([r, u, f, l]) => {
        setRules(r);
        setUsage(u);
        setFunnel(f);
        setLlmUsage(l);
        setStatus("ready");
        setDirtySignalTypes(new Set());
        setGateSettingsDirty(false);
      })
      .catch(() => setStatus("error"));
  }

  useEffect(load, []);

  const { hotspotRules, otherRules } = useMemo(() => {
    const all = rules?.signal_rules ?? [];
    return {
      hotspotRules: all.filter((r) => HOTSPOT_SIGNAL_TYPES.has(r.signal_type)),
      otherRules: all.filter((r) => !HOTSPOT_SIGNAL_TYPES.has(r.signal_type)),
    };
  }, [rules]);

  function updateRule(next: SignalRuleParams) {
    if (!rules) return;
    setRules({
      ...rules,
      signal_rules: rules.signal_rules.map((r) => (r.signal_type === next.signal_type ? next : r)),
    });
    setDirtySignalTypes((prev) => new Set(prev).add(next.signal_type));
    setSaveMessage(null);
  }

  function updateGateSettings(patch: Partial<GateSettings>) {
    if (!rules) return;
    setRules({ ...rules, gate_settings: { ...rules.gate_settings, ...patch } });
    setGateSettingsDirty(true);
    setSaveMessage(null);
  }

  async function handleSave() {
    if (!rules) return;
    setSaving(true);
    setSaveMessage(null);
    try {
      const signal_rules = rules.signal_rules.filter((r) => dirtySignalTypes.has(r.signal_type));
      await api.updateRules({
        signal_rules: signal_rules.length ? signal_rules : undefined,
        gate_settings: gateSettingsDirty ? rules.gate_settings : undefined,
        updated_by: "dashboard-ui",
      });
      setSaveMessage("Saved. Changes take effect within the next scheduler tick (≤30s).");
      setDirtySignalTypes(new Set());
      setGateSettingsDirty(false);
      api.getUsageStats().then(setUsage);
    } catch {
      setSaveMessage("Couldn't save — please try again.");
    } finally {
      setSaving(false);
    }
  }

  const isDirty = dirtySignalTypes.size > 0 || gateSettingsDirty;

  return (
    <div>
      <div className="dashboard-heading">
        <h2>Settings</h2>
        <p>
          Control which alerts the agent reasons about with a full LLM call, which it resolves with a
          deterministic rule instead, and how urgently each one reaches you. Safety-critical hotspots
          are always immediate and cannot be tuned away.
        </p>
      </div>

      {status === "loading" && <LoadingState label="Loading settings…" />}
      {status === "error" && <ErrorState label="Couldn't load settings." onRetry={load} />}

      {status === "ready" && rules && (
        <>
          {usage && (
            <section className="settings-section">
              <h3 className="settings-section-title">At a Glance</h3>
              <div className="settings-usage-row">
                <div className="settings-usage-stat">
                  <span className="settings-usage-value">
                    {usage.llm_calls_today} / {usage.llm_daily_limit}
                  </span>
                  <span className="settings-usage-label">LLM calls today</span>
                </div>
                <div className="settings-usage-stat">
                  <span className="settings-usage-value">{usage.gate_counts_today.suppress ?? 0}</span>
                  <span className="settings-usage-label">Suppressed today</span>
                </div>
                <div className="settings-usage-stat">
                  <span className="settings-usage-value">{usage.gate_counts_today.rule_only ?? 0}</span>
                  <span className="settings-usage-label">Resolved by rule today</span>
                </div>
                <div className="settings-usage-stat">
                  <span className="settings-usage-value">{usage.gate_counts_today.escalate ?? 0}</span>
                  <span className="settings-usage-label">Escalated to LLM today</span>
                </div>
              </div>

              {usage.suppression_warnings.length > 0 && (
                <div className="settings-warning-banner">
                  {usage.suppression_warnings.map((w) => (
                    <p key={w}>⚠ {w}</p>
                  ))}
                </div>
              )}

              {usage.false_positive_rate_by_signal_type.length > 0 && (
                <table className="settings-fp-table">
                  <thead>
                    <tr>
                      <th>Signal type</th>
                      <th>Dispatched (30d)</th>
                      <th>Marked false positive</th>
                      <th>Rate</th>
                    </tr>
                  </thead>
                  <tbody>
                    {usage.false_positive_rate_by_signal_type.map((row) => (
                      <tr key={row.signal_type}>
                        <td>{SIGNAL_TYPE_LABELS[row.signal_type] ?? row.signal_type}</td>
                        <td>{row.dispatched_count}</td>
                        <td>{row.false_positive_count}</td>
                        <td>{row.false_positive_rate_pct.toFixed(0)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              <div className="settings-charts-row">
                <ChartPanel title="Signal Funnel" subtitle="Last 30 days">
                  {funnel ? <BreakdownBarChart data={funnel} stacked height={220} /> : <EmptyState label="No data yet." />}
                </ChartPanel>
                <ChartPanel title="LLM Call Volume" subtitle="Vs. daily budget">
                  {llmUsage ? <TrendLineChart data={llmUsage} height={220} /> : <EmptyState label="No data yet." />}
                </ChartPanel>
              </div>
            </section>
          )}

          <section className="settings-section settings-section-hotspots">
            <h3 className="settings-section-title">Major Risk Hotspots</h3>
            <p className="settings-section-subtitle">
              Named safety patterns. Always escalate to a full LLM reasoning pass, always reach you
              immediately, and cannot be suppressed, batched, or downgraded from here — by design.
            </p>
            {hotspotRules.map((rule) => (
              <SignalRuleCard key={rule.signal_type} rule={rule} onChange={updateRule} onReset={() => {}} />
            ))}
          </section>

          <section className="settings-section">
            <h3 className="settings-section-title">Operational &amp; Financial Rules</h3>
            <p className="settings-section-subtitle">
              Tune thresholds, decide when the agent skips the LLM for an unambiguous breach, and
              control how urgently each finding surfaces.
            </p>
            {otherRules.map((rule) => (
              <SignalRuleCard
                key={rule.signal_type}
                rule={rule}
                onChange={updateRule}
                onReset={() => updateRule({ ...rule, params: {}, gate_mode: "auto", notification_cadence: "immediate" })}
              />
            ))}
          </section>

          <section className="settings-section">
            <h3 className="settings-section-title">Global Gate Policy</h3>
            <div className="settings-grid">
              <label className="settings-field">
                <span className="settings-field-label">Recurrence window (hours)</span>
                <input
                  className="settings-input"
                  type="number"
                  value={rules.gate_settings.recurrence_window_hours}
                  onChange={(e) => updateGateSettings({ recurrence_window_hours: Number(e.target.value) })}
                />
              </label>
              <label className="settings-field">
                <span className="settings-field-label">Recurrence suppress after (occurrences)</span>
                <input
                  className="settings-input"
                  type="number"
                  value={rules.gate_settings.recurrence_suppress_after}
                  onChange={(e) => updateGateSettings({ recurrence_suppress_after: Number(e.target.value) })}
                />
              </label>
              <label className="settings-field">
                <span className="settings-field-label">Max consecutive suppressions (heartbeat)</span>
                <input
                  className="settings-input"
                  type="number"
                  value={rules.gate_settings.max_consecutive_suppressions}
                  onChange={(e) => updateGateSettings({ max_consecutive_suppressions: Number(e.target.value) })}
                />
              </label>
              <label className="settings-field">
                <span className="settings-field-label">Rule-only margin ratio</span>
                <input
                  className="settings-input"
                  type="number"
                  step="0.1"
                  value={rules.gate_settings.rule_only_margin_ratio}
                  onChange={(e) => updateGateSettings({ rule_only_margin_ratio: Number(e.target.value) })}
                />
              </label>
              <label className="settings-field">
                <span className="settings-field-label">Max false-positive rate for rule-only</span>
                <input
                  className="settings-input"
                  type="number"
                  step="0.01"
                  value={rules.gate_settings.max_fp_rate_for_rule_only}
                  onChange={(e) => updateGateSettings({ max_fp_rate_for_rule_only: Number(e.target.value) })}
                />
              </label>
              <label className="settings-field">
                <span className="settings-field-label">Min confidence for rule-only</span>
                <input
                  className="settings-input"
                  type="number"
                  step="0.01"
                  value={rules.gate_settings.min_confidence_for_rule_only}
                  onChange={(e) => updateGateSettings({ min_confidence_for_rule_only: Number(e.target.value) })}
                />
              </label>
              <label className="settings-field">
                <span className="settings-field-label">Max healthy suppression rate</span>
                <input
                  className="settings-input"
                  type="number"
                  step="0.05"
                  value={rules.gate_settings.max_healthy_suppression_rate}
                  onChange={(e) => updateGateSettings({ max_healthy_suppression_rate: Number(e.target.value) })}
                />
              </label>
              <label className="settings-field">
                <span className="settings-field-label">Escalate after (hours) — critical</span>
                <input
                  className="settings-input"
                  type="number"
                  step="0.5"
                  value={rules.gate_settings.escalation_after_hours_critical}
                  onChange={(e) => updateGateSettings({ escalation_after_hours_critical: Number(e.target.value) })}
                />
              </label>
              <label className="settings-field">
                <span className="settings-field-label">Escalate after (hours) — high/warning</span>
                <input
                  className="settings-input"
                  type="number"
                  step="0.5"
                  value={rules.gate_settings.escalation_after_hours_high}
                  onChange={(e) => updateGateSettings({ escalation_after_hours_high: Number(e.target.value) })}
                />
              </label>
              <label className="settings-field">
                <span className="settings-field-label">Escalate after (hours) — medium</span>
                <input
                  className="settings-input"
                  type="number"
                  step="1"
                  value={rules.gate_settings.escalation_after_hours_medium}
                  onChange={(e) => updateGateSettings({ escalation_after_hours_medium: Number(e.target.value) })}
                />
              </label>
            </div>
          </section>

          <div className="settings-save-bar">
            <button type="button" className="btn btn-primary" disabled={!isDirty || saving} onClick={handleSave}>
              {saving ? "Saving…" : "Save changes"}
            </button>
            {saveMessage && <span className="settings-save-message">{saveMessage}</span>}
          </div>
        </>
      )}
    </div>
  );
}
