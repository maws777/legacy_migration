from reporting.report import ReportGenerator, cost_eur


def _report(**kw):
    args = dict(analysed=10000, counts={"MIGRATE": 6940, "CORRECT": 630, "REJECT": 1920, "ASK_HUMAN": 510},
                out_of_scope=990, traced_rows=10000, input_tokens=900, output_tokens=300, price_in=0.000001,
                price_out=0.000005, seconds=125.4, loaded=7570, cache_state="cold", notes=[])
    args.update(kw)
    return ReportGenerator.compose("run-1", **args)


def test_exact_text_format():
    r = _report()
    assert r.text.split("\n")[:10] == [
        "10,000 records analysed",
        "─────────────────────────",
        "6,940 migrated automatically",
        "630 automatically corrected",
        "1,920 rejected",
        "510 requiring human decision",
        "",
        "100% decisions traced",
        "Estimated AI consumption: 1,200 tokens (≈ 0.0024 €)",
        "Processing time: 2 min 5 sec",
    ]


def test_extra_lines_coverage_split_and_cache_state():
    r = _report(notes=["Degraded_Mode: no LLM provider available."], cache_state="warm")
    tail = r.text.split("\n")[10:]
    assert tail[1] == "Coverage: 7,570 of 10,000 source rows loaded (75.7%)"
    assert tail[2] == "Rejected: 990 out of scope, 930 in error; pending human decision: 510"
    assert tail[3].startswith("Run type: warm") and tail[-1].startswith("Degraded_Mode")
    assert (r.rejected_out_of_scope, r.rejected_error, r.pending_human) == (990, 930, 510)


def test_cost_formula_and_zero_without_calls():
    assert cost_eur(1000, 200, 0.001, 0.01) == 1000 * 0.001 + 200 * 0.01
    r = _report(input_tokens=0, output_tokens=0)
    assert r.cost_eur == 0 and "0 tokens (≈ 0.0000 €)" in r.text


def test_counts_sum_and_traced_percentage():
    r = _report(traced_rows=9990)
    assert r.migrate + r.correct + r.reject + r.ask_human == r.analysed
    assert "99.9% decisions traced" in r.text
    assert "0 records analysed" in _report(analysed=0, counts={}, traced_rows=0, loaded=0).text
