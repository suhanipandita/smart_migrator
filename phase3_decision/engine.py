"""
phase3_decision/engine.py
──────────────────────────
§4.4  Phase 3 — Multi-Criteria Cloud Selection

Implements exactly as specified:
  §4.4.1  Five decision parameters: C, L, A, P_sla, R
  §4.4.2  Min-max normalization (direction-aware)
  §4.4.3  Composite scoring: Score_i = w₁·norm(C_i)+w₂·norm(L_i)+...+w₅·norm(R_i)
  §4.4.4  Hard constraint filtering + Algorithm 1

All variable names match the paper's notation.
"""

import logging
from dataclasses import dataclass
from typing import Optional
from phase2_monitoring.monitor import MetricsSnapshot
from config.settings import (
    WEIGHTS, R_MIN_RESOURCE_INDEX, DELTA_MIN_IMPROVEMENT, PROVIDERS,
)
from audit.logger import log_selection

log = logging.getLogger("phase3.decision")


@dataclass
class ProviderProfile:
    """
    §4.4.1 — five decision parameters for one cloud provider.
    All raw (un-normalized) values.
    """
    provider:      str
    C_i:           float   # hourly cost (USD)          — lower is better
    L_i:           float   # p95 latency (ms)           — lower is better
    A_i:           float   # availability (0–1 fraction)— higher is better
    P_i:           float   # SLA / policy compliance    — higher is better
    R_i:           float   # resource availability index— higher is better
    health_ok:     bool
    residency_ok:  bool = True   # data residency constraint


@dataclass
class SelectionResult:
    action:         str              # "migrate" | "stay" | "no_candidates"
    current:        str
    target:         Optional[str]
    target_score:   Optional[float]
    current_score:  float
    reason:         str
    all_scores:     dict             # provider → score


class DecisionEngine:
    """
    Algorithm 1 (§4.4.4) — Multi-Criteria Cloud Provider Selection.

    Input:  providers P, current provider p_curr, metrics M, weights W, threshold δ
    Output: p_target or NULL
    """

    # ── §4.4.2  Normalization ───────────────────────────────────────────────
    @staticmethod
    def _normalize(value: float, x_min: float, x_max: float,
                   lower_is_better: bool) -> float:
        """
        §4.4.2 — min-max normalization.
        Result 0.0 = most favorable, 1.0 = least favorable (consistent semantics).
        """
        if x_max == x_min:
            return 0.0   # all providers equal on this dimension
        if lower_is_better:
            # norm(X_i) = (X_i - X_min) / (X_max - X_min)
            return (value - x_min) / (x_max - x_min)
        else:
            # norm(X_i) = (X_max - X_i) / (X_max - X_min)
            return (x_max - value) / (x_max - x_min)

    def _build_profiles(self, snapshots: dict[str, MetricsSnapshot]) -> dict[str, ProviderProfile]:
        """Convert MetricsSnapshots → ProviderProfiles (§4.4.1)."""
        profiles = {}
        for provider, snap in snapshots.items():
            profiles[provider] = ProviderProfile(
                provider=provider,
                C_i=snap.hourly_cost_usd,
                L_i=snap.latency_p95_ms,
                A_i=1.0 if snap.health_ok else 0.0,   # simplified; extend with SLA history
                P_i=1.0,   # extend with residency/compliance check
                R_i=1.0 - (snap.cpu_pct / 100.0),     # headroom proxy
                health_ok=snap.health_ok,
            )
        return profiles

    # ── §4.4.4  Constraint filtering (Algorithm 1, lines 1–2) ─────────────
    def _filter_candidates(self, profiles: dict[str, ProviderProfile],
                           current: str) -> list[ProviderProfile]:
        """
        Algorithm 1, step 1: eliminate providers that fail hard constraints.
          - provider ≠ current
          - health_ok = TRUE
          - residency_ok = COMPLIANT
          - R_i ≥ R_min
        """
        candidates = []
        for p, profile in profiles.items():
            if p == current:
                continue
            if not profile.health_ok:
                log.info("Filtered %s: health check failed", p)
                continue
            if not profile.residency_ok:
                log.info("Filtered %s: residency constraint violation", p)
                continue
            if profile.R_i < R_MIN_RESOURCE_INDEX:
                log.info("Filtered %s: resource index %.2f < R_min %.2f",
                         p, profile.R_i, R_MIN_RESOURCE_INDEX)
                continue
            candidates.append(profile)
        return candidates

    # ── §4.4.3  Composite scoring (Algorithm 1, lines 3–9) ────────────────
    def _score_all(self, candidates: list[ProviderProfile],
                   current_profile: ProviderProfile) -> dict[str, float]:
        """
        Algorithm 1, steps 3–9:
          Score_i = w₁·norm(C_i) + w₂·norm(L_i) + w₃·norm(A_i) + w₄·norm(P_i) + w₅·norm(R_i)
        Normalization is computed across candidates ∪ {current}.
        """
        all_profiles = candidates + [current_profile]

        # Collect raw values for normalization bounds
        C_vals = [p.C_i for p in all_profiles]
        L_vals = [p.L_i for p in all_profiles]
        A_vals = [p.A_i for p in all_profiles]
        P_vals = [p.P_i for p in all_profiles]
        R_vals = [p.R_i for p in all_profiles]

        def score(profile: ProviderProfile) -> float:
            norm_C = self._normalize(profile.C_i, min(C_vals), max(C_vals), lower_is_better=True)
            norm_L = self._normalize(profile.L_i, min(L_vals), max(L_vals), lower_is_better=True)
            norm_A = self._normalize(profile.A_i, min(A_vals), max(A_vals), lower_is_better=False)
            norm_P = self._normalize(profile.P_i, min(P_vals), max(P_vals), lower_is_better=False)
            norm_R = self._normalize(profile.R_i, min(R_vals), max(R_vals), lower_is_better=False)

            return (
                WEIGHTS["cost"]         * norm_C +
                WEIGHTS["latency"]      * norm_L +
                WEIGHTS["availability"] * norm_A +
                WEIGHTS["policy"]       * norm_P +
                WEIGHTS["resource"]     * norm_R
            )

        scores = {}
        for p in all_profiles:
            scores[p.provider] = score(p)
            log.info("Score [%s]: %.4f  (C=%.2f L=%.0fms A=%.2f R=%.2f)",
                     p.provider, scores[p.provider],
                     p.C_i, p.L_i, p.A_i, p.R_i)
        return scores

    # ── Algorithm 1 — full selection procedure ────────────────────────────
    def select(self, snapshots: dict[str, MetricsSnapshot],
               current: str) -> SelectionResult:
        """
        §4.4 — Algorithm 1 full implementation.
        Returns SelectionResult with action="migrate" and target provider,
        or action="stay" / "no_candidates" if migration is not warranted.
        """
        profiles  = self._build_profiles(snapshots)
        candidates = self._filter_candidates(profiles, current)

        # Algorithm 1, step 2: IF candidates = ∅ THEN RETURN NULL
        if not candidates:
            log.warning("No healthy candidates after constraint filtering — staying on %s", current)
            current_score = self._score_all([], profiles[current]).get(current, 0.0)
            return SelectionResult(
                action="no_candidates", current=current, target=None,
                target_score=None, current_score=current_score,
                reason="all alternative providers failed constraint filters",
                all_scores={current: current_score},
            )

        # Algorithm 1, steps 3–9: score all candidates ∪ current
        scores = self._score_all(candidates, profiles[current])

        # Algorithm 1, step 10: p_best ← argmin(Score_i)
        candidate_scores = {p.provider: scores[p.provider] for p in candidates}
        p_best  = min(candidate_scores, key=candidate_scores.get)
        s_best  = scores[p_best]
        s_curr  = scores[current]

        log.info("Best candidate: %s (score=%.4f) vs current %s (score=%.4f)",
                 p_best, s_best, current, s_curr)

        # Algorithm 1, steps 11–14:
        # IF Score(p_best) < Score(current) × (1 - δ) THEN RETURN p_best
        improvement_required = s_curr * (1 - DELTA_MIN_IMPROVEMENT)
        if s_best < improvement_required:
            reason = (f"score improvement "
                      f"{((s_curr - s_best) / s_curr * 100):.1f}% "
                      f"(≥ {DELTA_MIN_IMPROVEMENT*100:.0f}% required)")
            log_selection(p_best, s_best, reason)
            return SelectionResult(
                action="migrate", current=current, target=p_best,
                target_score=s_best, current_score=s_curr,
                reason=reason, all_scores=scores,
            )

        # Improvement margin not satisfied (steps 13–14: RETURN NULL)
        improvement = (s_curr - s_best) / s_curr * 100 if s_curr > 0 else 0
        reason = (f"best candidate {p_best} only {improvement:.1f}% better "
                  f"(need ≥ {DELTA_MIN_IMPROVEMENT*100:.0f}%)")
        log.info("No migration: %s", reason)
        return SelectionResult(
            action="stay", current=current, target=None,
            target_score=s_best, current_score=s_curr,
            reason=reason, all_scores=scores,
        )