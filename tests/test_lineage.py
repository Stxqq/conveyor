from conveyor import Pipeline, step


@step
def raw(seed=0):
    return [seed + 2, seed + 5]


@step
def labels(raw):
    return [x % 2 for x in raw]


@step
def features(raw):
    return [x * 10 for x in raw]


@step
def model(features, labels):
    return {"w": sum(features), "b": sum(labels)}


P = Pipeline("lineage", [raw, labels, features, model])


def test_upstream_walks_back_to_the_source(executor):
    result = executor.run(P)
    model_id = result.steps["model"].artifact.id
    ups = executor.lineage.upstream(model_id)
    assert [(u["step"], u["depth"]) for u in ups] == [
        ("features", 1),
        ("labels", 1),
        ("raw", 2),
    ]
    assert ups[-1]["artifact_id"] == result.steps["raw"].artifact.id


def test_producer_is_the_run_that_computed_it_not_a_cache_hit(executor):
    first = executor.run(P)
    executor.run(P)
    raw_id = first.steps["raw"].artifact.id
    producer = executor.lineage.producer(raw_id)
    assert producer["run_id"] == first.id
    assert producer["step"] == "raw"
    consumers = executor.lineage.consumers(raw_id)
    assert {(c["step"], c["as_input"]) for c in consumers} == {
        ("labels", "raw"),
        ("features", "raw"),
    }
    assert len({c["run_id"] for c in consumers}) == 2


def test_runs_listing_and_reference_resolution(executor):
    a = executor.run(P)
    b = executor.run(P, {"seed": 4})
    runs = executor.lineage.runs()
    assert [r["id"] for r in runs] == [b.id, a.id]
    assert runs[0]["succeeded"] == 4 and runs[1]["succeeded"] == 4
    assert executor.lineage.resolve_run("latest") == b.id
    assert executor.lineage.resolve_run(b.id) == b.id
    assert executor.lineage.resolve_run("nope") is None
    shown = executor.lineage.run(b.id)
    assert shown["params"] == {"seed": 4}
    assert shown["steps"][0]["step"] == "raw"
    assert shown["steps"][-1]["inputs"].keys() == {"features", "labels"}
