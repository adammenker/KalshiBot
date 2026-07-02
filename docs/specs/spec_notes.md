Read docs/specs/strategy_variant_engine.md. Implement only the Strategy Variant Engine skeleton and storage layer.

Tasks:
- Add strategy base classes/protocols.
- Add strategy registry.
- Add StrategyContext and StrategyDecision models.
- Add strategy_signals SQLite table/migration.
- Add repository/helper functions to insert strategy signals.
- Add tests for storage and registry.

Do not integrate with heartbeat yet except where necessary for imports.

---

Now integrate the strategy engine into heartbeat.

Tasks:
- Run enabled strategy variants after each observation is inserted.
- Implement legacy_fee_adjusted_edge, loose_poly_lead_scout, persistent_mid_gap, hold_to_resolution_ev_poly_mid, and hold_to_resolution_ev_poly_bid_conservative.
- Add CLI flags for --strategy-mode and --strategy-variants.
- Preserve current default behavior.
- Add tests.

---

Add analysis support for strategy variants.

Tasks:
- Update analyze to show strategy signal counts by strategy_id and signal_type.
- Show paper trade counts by strategy_id if available.
- Add basic follow-through placeholders if easy, but do not overbuild.
- Update README with scout mode examples.
- Add tests.