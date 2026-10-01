import pytest

from conveyor import Pipeline, PipelineError, step


@step
def raw(seed=1):
    return seed


@step
def clean(raw):
    return raw


@step
def model(clean, l2=1.0):
    return clean * l2


def test_order_follows_dependencies_then_declaration():
    @step
    def report(model, clean):
        return model

    p = Pipeline("p", [report, model, clean, raw])
    assert p.order == ["raw", "clean", "model", "report"]
    assert p.downstream("clean") == ["model", "report"]
    assert p.edges() == [
        ("raw", "clean"),
        ("clean", "model"),
        ("model", "report"),
        ("clean", "report"),
    ]


def test_params_come_from_defaults_and_overrides():
    p = Pipeline("p", [raw, clean, model], params={"l2": 0.5})
    assert p.params == {"seed": 1, "l2": 0.5}
    assert p.resolve_params({"seed": 3}) == {"seed": 3, "l2": 0.5}
    with pytest.raises(PipelineError, match=r"unknown parameter 'sed'.*'seed'"):
        p.resolve_params({"sed": 3})


def test_duplicate_names():
    other = step(name="raw")(lambda: 2)
    with pytest.raises(PipelineError, match="two steps are named 'raw'"):
        Pipeline("p", [raw, other])


def test_missing_input_suggests_the_closest_step():
    @step
    def evaluate(modl):
        return modl

    with pytest.raises(PipelineError) as err:
        Pipeline("p", [raw, clean, model, evaluate])
    assert "evaluate(modl)" in str(err.value)
    assert "did you mean 'model'" in str(err.value)


def test_cycle_is_reported_as_a_path():
    @step
    def a(c):
        return c

    @step
    def b(a):
        return a

    @step
    def c(b):
        return b

    with pytest.raises(PipelineError, match=r"cycle: a -> b -> c -> a"):
        Pipeline("p", [a, b, c])


def test_conflicting_defaults_need_an_explicit_value():
    @step
    def other(seed=2):
        return seed

    with pytest.raises(PipelineError, match="'seed' defaults to 1"):
        Pipeline("p", [raw, other])
    assert Pipeline("p", [raw, other], params={"seed": 5}).params["seed"] == 5


def test_rejects_plain_functions_and_varargs():
    def plain(x):
        return x

    with pytest.raises(PipelineError, match="did you forget"):
        Pipeline("p", [plain])
    with pytest.raises(TypeError, match="wired by name"):
        step(lambda *xs: xs)
