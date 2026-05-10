-- =============================================================================
-- Migration: 002_widen_cost_precision
-- Reason:    numeric(8,4) truncated sub-cent LLM costs to 0.0000 in real
--            calls (smoke_test_pipeline observed 22µ$ rounded away).
--            Widen per-call columns to numeric(10,6) so 10⁻⁶ USD survives.
--            Daily aggregates already plenty (numeric(10,4)) but bumped to
--            numeric(12,8) for symmetry with future per-provider precision.
-- =============================================================================

alter table articles            alter column total_cost_usd type numeric(10,6);
alter table agent_runs          alter column cost_usd       type numeric(10,6);
alter table images              alter column cost_usd       type numeric(10,6);
alter table agent_runs_summary  alter column total_cost_usd type numeric(12,8);

-- metrics_daily.* money columns intentionally NOT widened (they hold daily
-- aggregates in the cents-or-larger range; numeric(8,4)–(10,4) is enough).
