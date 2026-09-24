"""Small native Boolean checks only; no whole-wheel acceptance is implied."""
import math

import cadquery as cq
import pytest
from OCP.TopAbs import TopAbs_IN, TopAbs_ON, TopAbs_UNKNOWN
from OCP.TopoDS import TopoDS_Shape

from scripts import sector_boolean as sb


def box(size=10, x=0):
    return cq.Workplane("XY").box(size, size, size).translate((x, 0, 0)).val()


@pytest.mark.parametrize("operation,expected", [("cut", 500), ("common", 500)])
def test_overlapping_boxes(operation, expected):
    result, metadata = sb.compare_boolean(box(), box(x=5), operation=operation)
    assert result.Volume() == pytest.approx(expected, abs=1e-8)
    assert metadata["result"]["infinity_states"] == ["OUT"]
    assert metadata["kernel_invoked"] is True
    assert metadata["policy"]["fuzzy_mm"] == 1e-6
    assert metadata["policy"]["automatic_retry"] is False


@pytest.mark.parametrize("operation,offset,expected", [
    ("cut", 0, 0), ("common", 0, 1000), ("cut", 20, 1000), ("common", 20, 0),
])
def test_identity_and_disjoint_boxes(operation, offset, expected):
    result, metadata = sb.compare_boolean(box(), box(x=offset), operation=operation)
    assert result.Volume() == pytest.approx(expected, abs=1e-8)
    assert metadata["result"]["empty"] == (expected == 0)


@pytest.mark.parametrize("operation,expected", [("cut", 750), ("common", 250)])
def test_multiple_tools_use_union_semantics(operation, expected):
    result, metadata = sb.compare_boolean(box(), [box(5, -2.5), box(5, 2.5)], operation=operation)
    assert result.Volume() == pytest.approx(expected, abs=1e-8)
    assert metadata["multiple_tools_semantics"] == "union_of_tools"
    assert len(metadata["tools"]) == 2


@pytest.mark.parametrize("operation", ["cut", "common"])
def test_empty_operands_propagate_without_kernel(monkeypatch, operation):
    def forbidden():
        pytest.fail("Set identities with empty tools do not require native Booleans")

    monkeypatch.setattr(sb, "BRepAlgoAPI_Cut", forbidden)
    monkeypatch.setattr(sb, "BRepAlgoAPI_Common", forbidden)
    empty, first = cq.Compound.makeCompound([]), box()
    result, metadata = sb.compare_boolean(empty, first, operation=operation)
    assert metadata["result"]["empty"] and not metadata["kernel_invoked"]
    result, metadata = sb.compare_boolean(first, empty, operation=operation)
    assert metadata["kernel_invoked"] is False
    assert result.Volume() == pytest.approx(1000 if operation == "cut" else 0)
    if operation == "cut":
        assert not result.wrapped.IsSame(first.wrapped)


def test_repeated_operations_leave_operands_unchanged():
    first, tool = box(), box(x=5)
    before = [(sb.inspect_boolean_shape(shape), shape.Volume(), shape.isInside((0, 0, 0)))
              for shape in (first, tool)]
    a, first_meta = sb.compare_boolean(first, tool)
    b, second_meta = sb.compare_boolean(first, tool)
    after = [(sb.inspect_boolean_shape(shape), shape.Volume(), shape.isInside((0, 0, 0)))
             for shape in (first, tool)]
    assert before == after
    assert first_meta["result"] == second_meta["result"]
    assert a.Volume() == b.Volume()


@pytest.mark.parametrize("value", [True, -1e-6, math.nan, math.inf, 1e-5, "0", None])
def test_no_unbounded_or_implicit_fuzzy_policy(value):
    with pytest.raises(ValueError, match="fuzzy_mm"):
        sb.boolean_policy(fuzzy_mm=value)


def test_zero_fuzzy_is_an_explicit_control():
    _, metadata = sb.compare_boolean(box(), box(x=5), fuzzy_mm=0)
    assert metadata["policy"]["fuzzy_mm"] == 0


@pytest.mark.parametrize("operation,tools", [("fuse", [1]), ("cut", []), ("common", None)])
def test_invalid_operation_or_tools_rejected(operation, tools):
    with pytest.raises(ValueError):
        sb.compare_boolean(box(), tools, operation=operation)


def test_free_geometry_and_reversed_solid_rejected():
    shape = box()
    with pytest.raises(sb.BooleanComparisonError, match="topology"):
        sb.inspect_boolean_shape(cq.Compound.makeCompound([shape, shape.Faces()[0]]))
    with pytest.raises(sb.BooleanComparisonError, match="infinity"):
        sb.inspect_boolean_shape(cq.Shape.cast(shape.wrapped.Reversed()))


@pytest.mark.parametrize("state", [TopAbs_IN, TopAbs_ON, TopAbs_UNKNOWN])
def test_non_out_native_infinity_never_accepted(monkeypatch, state):
    class Classifier:
        def __init__(self, wrapped):
            pass

        def PerformInfinitePoint(self, tolerance):
            assert tolerance == 1e-7

        def State(self):
            return state

    monkeypatch.setattr(sb, "BRepClass3d_SolidClassifier", Classifier)
    with pytest.raises(sb.BooleanComparisonError, match="infinity must classify OUT"):
        sb.compare_boolean(box(), box(x=5))


def fake_builder(monkeypatch, *, done=True, result=None, error_api=False, errors=False):
    class Builder:
        def __init__(self):
            self.flags = {}
            self.build_count = 0

        def SetArguments(self, values):
            self.arguments = values

        def SetTools(self, values):
            self.tools = values

        def SetNonDestructive(self, value):
            self.flags["non_destructive"] = value

        def SetRunParallel(self, value):
            self.flags["run_parallel"] = value

        def SetFuzzyValue(self, value):
            self.flags["fuzzy_mm"] = value

        def Build(self):
            self.build_count += 1

        def IsDone(self):
            return done

        def Shape(self):
            return result

    builder = Builder()
    if error_api:
        builder.HasErrors = lambda: errors
    monkeypatch.setattr(sb, "BRepAlgoAPI_Cut", lambda: builder)
    return builder


@pytest.mark.parametrize("failure", ["unfinished", "null", "native_error", "invalid"])
def test_failed_native_result_not_repaired_or_retried(monkeypatch, failure):
    output = box(5)
    builder = fake_builder(monkeypatch, done=failure != "unfinished",
                           result=TopoDS_Shape() if failure == "null" else output.wrapped,
                           error_api=failure == "native_error", errors=True)
    if failure == "invalid":
        original = sb.cq.Shape.cast

        def cast(wrapped):
            if wrapped.IsSame(output.wrapped):
                monkeypatch.setattr(output, "isValid", lambda: False)
                return output
            return original(wrapped)

        monkeypatch.setattr(sb.cq.Shape, "cast", cast)
    with pytest.raises(sb.BooleanComparisonError):
        sb.compare_boolean(box(), box(x=5))
    assert builder.build_count == 1


@pytest.mark.parametrize("error_api", [False, True])
def test_policy_flags_copies_and_error_capability_are_reported(monkeypatch, error_api):
    first, tool = box(), box(x=5)
    builder = fake_builder(monkeypatch, result=box(5).wrapped, error_api=error_api)
    _, metadata = sb.compare_boolean(first, tool)
    assert builder.flags == {"non_destructive": True, "run_parallel": False, "fuzzy_mm": 1e-6}
    assert not builder.arguments.First().IsSame(first.wrapped)
    assert not builder.tools.First().IsSame(tool.wrapped)
    assert metadata["error_api_exposed"] is error_api
    assert metadata["native_error_report"] == (
        "HasErrors_false" if error_api else "HasErrors_not_exposed_not_verified")


def test_sub_fuzzy_gap_is_only_a_diagnostic_not_an_equivalence_assertion():
    # A gap below the chosen tolerance may be merged by fuzzy evaluation.
    # Neither control is declared geometrically equivalent by this helper.
    first, tool = box(), box(x=10 + 5e-7)
    records = []
    for tolerance in (0, sb.DEFAULT_FUZZY_MM):
        _, metadata = sb.compare_boolean(first, tool, operation="common", fuzzy_mm=tolerance)
        records.append(metadata)
    assert [record["policy"]["fuzzy_mm"] for record in records] == [0, 1e-6]
    assert all("can change derived topology" in record["policy"]["scope"] for record in records)
    assert all("correct Boolean membership" in record["policy"]["scope"] for record in records)
