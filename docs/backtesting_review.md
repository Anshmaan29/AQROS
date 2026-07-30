# Backtesting Engine — Blueprint Compliance Review

## §3.4 — Backtesting Service Responsibility

| Requirement | Implemented file(s) | Test covering it | Status |
|---|---|---|---|
| Deterministic historical replay | `domain/simulation.py:86-88` (seeded `random.Random`) | `tests/integration/test_golden_replay.py:TestGoldenReplay::test_deterministic_result` — identical config produces identical status, equity length, trade-log length, Sharpe, return | DONE |
| Shared strategy/risk/OMS core from `libs/` | `domain/simulation.py:37` (`from aqros_strategy_core import RiskCheck, Strategy, StrategyContext`), `domain/simulation.py:38` (`from aqros_strategy_core.contracts import OrderIntent`) | `tests/unit/test_strategy_factory.py` (creates Strategy via factory), `tests/unit/test_risk_check_factory.py` (creates RiskCheck via factory) | DONE |
| Simulated EMS (fill models) | `domain/fills.py`: `ImmediateFillModel` (line 208), `LiquidityCappedFillModel` (line 221) | No test for fill model behavior; only `test_golden_replay.py` uses them implicitly | DONE (no tests) |
| Cost/impact model — slippage | `domain/slippage.py`: `ZeroSlippage` (line 72), `FixedBpsSlippage` (line 83) | No dedicated test for slippage models | DONE (no tests) |
| Cost/impact model — commission | `domain/commission.py`: `ZeroCommission` (line 60), `PerShareCommission` (line 71), `PctNotionalCommission` (line 93) | No dedicated test for commission models | DONE (no tests) |
| Cost/impact model — latency | `domain/latency.py`: `ZeroLatency` (line 67), `FixedLatency` (line 79), `ConfigurableLatency` (line 99) | No dedicated test for latency models | DONE (no tests) |
| Produce signed reports | `adapters/report_signer.py`: `generate_report` (line 29), `sign_report` (line 40), `verify_report` (line 47) | `tests/unit/test_report_signer.py`: `TestGenerateReport` (6 tests), `TestSignReport` (4 tests), `TestVerifyReport` (5 tests) | DONE |
| Anti-overfitting analytics — PBO | `domain/validation.py:compute_pbo` (line 65) | `tests/unit/test_validation.py:TestComputePBO` (4 tests) | DONE |
| Anti-overfitting analytics — DSR | `domain/validation.py:compute_dsr` (line 39) | `tests/unit/test_validation.py:TestComputeDSR` (5 tests) | DONE |
| Anti-overfitting analytics — CPCV | `domain/validation.py:run_cpcv` (line 90) | No dedicated unit test for `run_cpcv` | DONE (no tests) |
| Output: signed backtest report → registry | `domain/ports.py:170` (`ModelRegistryClient.publish_result`), `adapters/model_registry_client.py:50` (`HttpModelRegistryClient.publish_result`) | `tests/unit/test_model_registry_client.py:test_publish_result` (line 69) | DONE |

## §6 — Phase 3 / MVP Deliverable

| Requirement | Implemented file(s) | Test covering it | Status |
|---|---|---|---|
| Backtest engine on shared strategy/risk core | `domain/services.py:87-428` (`BacktestService`), `domain/simulation.py:58-315` (`SimulationEngine`) | `tests/integration/test_golden_replay.py` — full end-to-end deterministic replay | DONE |
| Cost/impact simulator | `domain/fills.py`, `domain/commission.py`, `domain/slippage.py`, `domain/latency.py` (all concrete models listed above) | None for individual models; `test_golden_replay.py` exercises them implicitly | DONE (no tests) |
| Validation gauntlet — walk-forward | `domain/validation.py:run_cpcv` (date-split purged folds, not pure walk-forward) | No test for walk-forward behavior | PARTIAL — `run_cpcv` does sequential date-split folds but lacks explicit walk-forward (fixed window sliding) |
| Validation gauntlet — purged/combinatorial CV | `domain/validation.py:run_cpcv` (line 105: purges `segment_days // 4` from each fold's test window) | No test for purged CV correctness | PARTIAL — purging logic exists but no test validates it |
| Validation gauntlet — DSR | `domain/validation.py:compute_dsr` (line 39) | `tests/unit/test_validation.py:TestComputeDSR` (5 tests) | DONE |
| Validation gauntlet — PBO | `domain/validation.py:compute_pbo` (line 65) | `tests/unit/test_validation.py:TestComputePBO` (4 tests) | DONE |
| Signed reports | `adapters/report_signer.py`: `generate_report`, `sign_report`, `verify_report` | `tests/unit/test_report_signer.py` (15 tests) | DONE |
| One end-to-end strategy (idea→dataset→model→backtest→signed report) | — the backtest→signed report section exists; the full chain depends on dataset-builder (#6), training-pipeline (#7), and model-registry (#7) which are separate services | `tests/integration/test_golden_replay.py` demonstrates backtest→result determinism with mocked upstreams | PARTIAL — backtest→report works end-to-end within this service; full chain requires wiring external services |
| Honestly net of costs | `domain/services.py:218-230` (wires commission, slippage, fill, latency models into SimulationEngine) | None — no test validates that costs are correctly applied to P&L | DONE (no tests) |

## §8.1 — Priority #10 Dependencies

| Requirement | Implemented file(s) | Test covering it | Status |
|---|---|---|---|
| Depends on #9 (shared strategy/risk core) | `domain/simulation.py:37` imports from `aqros_strategy_core` | All strategy/risk factory tests use the shared types | DONE |
| Depends on #8 (validation gauntlet) | `domain/validation.py` — PBO, DSR, CPCV | `tests/unit/test_validation.py` | DONE |

## §9 — Testing Strategy

| Requirement | Implemented file(s) | Test covering it | Status |
|---|---|---|---|
| Deterministic golden replay (money-path bit-for-bit reproduction) | `tests/integration/test_golden_replay.py` | Lines 193-302 — 4 test methods verifying deterministic output, identical checksums, benchmark reproducibility | DONE |
| Known-answer strategies produce known results | — | No test computes a pre-determined expected P&L from known input bars and asserts equality | NOT DONE |
| Leakage-audit tests (lookahead detection) | `domain/services.py:205-212` (look-ahead violation check in `_execute`) | `tests/unit/test_feature_store_client.py:TestLookAheadRejection` (3 tests for feature store as-of guard); no test for the `services.py` lookahead check itself | PARTIAL — feature-store adapter tested; engine-level lookahead rejection untested |
| Cost-model sanity | `domain/commission.py`, `domain/slippage.py`, `domain/fills.py`, `domain/latency.py` | No test instantiates any cost model and verifies its computation | NOT DONE |
| CPCV/PBO/DSR correctness | `domain/validation.py` | `tests/unit/test_validation.py` has basic PBO/DSR tests but none validate against known reference values | PARTIAL — DSR and PBO have structural tests, CPCV has none, none validated against published reference values |
| Seeded, clock-injected determinism | `domain/simulation.py:91` (`rng = random.Random(self._seed)`), `domain/simulation.py:253` (`context = StrategyContext(as_of=require_clock(),...)`) | `tests/integration/test_golden_replay.py:test_deterministic_result` — same config, different UUID, identical results | DONE |

## General / Supporting (not explicit in cited sections but required for operation)

| Requirement | Implemented file(s) | Test covering it | Status |
|---|---|---|---|
| REST API — submit backtest | `api/routes/backtests.py:130-144` (`POST /v1/backtests`) | No direct HTTP test | DONE (no tests) |
| REST API — list runs | `api/routes/backtests.py:146-159` (`GET /v1/backtests`) | No direct HTTP test | DONE (no tests) |
| REST API — get run status | `api/routes/backtests.py:161-175` (`GET /v1/backtests/{run_uuid}`) | No direct HTTP test | DONE (no tests) |
| REST API — get result | `api/routes/backtests.py:177-209` (`GET /v1/backtests/{run_uuid}/result`) | No direct HTTP test | DONE (no tests) |
| Calendar provider | `adapters/calendar_provider.py`: `DefaultCalendarProvider` | `tests/unit/test_calendar_provider.py` (12 tests covering holidays, exchanges, year range) | DONE |
| Strategy factory | `adapters/strategy_factory.py`: `default_strategy_factory`, `SignalFollowingStrategy` | `tests/unit/test_strategy_factory.py` (9 tests) | DONE |
| Risk check factory | `adapters/risk_check_factory.py`: `default_risk_check_factory`, `ConfigurableRiskCheck` | `tests/unit/test_risk_check_factory.py` (8 tests) | DONE |
| DI wiring (deps.py) | `api/deps.py` — `get_backtest_service`, `get_backtest_query_service`, all sub-dependencies | No test for DI wiring | DONE (no tests) |
| Router mounted in app | `app.py:65` (`app.include_router(backtests_router)`), `app.py:48` (`app.state.calendar_provider = DefaultCalendarProvider()`) | No test | DONE (no tests) |
| Model registry publication | `domain/ports.py:170` (`publish_result`), `adapters/model_registry_client.py:50` | `tests/unit/test_model_registry_client.py:test_publish_result` (line 69), `test_publish_result_raises_on_error` (line 85) | DONE |
| Validation gauntlet API | `api/routes/backtests.py:234-253` (`POST /v1/backtests/{run_uuid}/validate`) | No direct HTTP test | DONE (no tests) |
| Report signing API | `api/routes/backtests.py:211-232` (`POST /v1/backtests/{run_uuid}/sign`) | No direct HTTP test | DONE (no tests) |
