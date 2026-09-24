"""Explicit Boolean policy for comparison diagnostics, not CAD generation.

The fixed fuzzy evaluation tolerance can change derived topology near that
tolerance. Original STEP inputs are never rewritten. Valid topology, a finite
bounding box and infinity classified OUT are necessary guards, not proof of
correct set membership. Conservation, protected-region and independent oracle
checks remain separate acceptance gates in the caller.
"""
from __future__ import annotations

import math
from numbers import Real
import time

import cadquery as cq
import OCP
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.TopAbs import TopAbs_OUT
from OCP.TopTools import TopTools_ListOfShape

from wheelcam.mass_properties import CLASSIFIER_TOLERANCE_MM, _solid_leaves


DEFAULT_FUZZY_MM = 1e-6
POLICY_SCHEMA = "wheel-sector-comparison-boolean-v1"


class BooleanComparisonError(ValueError):
    """A comparison operation did not satisfy the declared native guards."""


def boolean_policy(*, fuzzy_mm=DEFAULT_FUZZY_MM):
    if (isinstance(fuzzy_mm, bool) or not isinstance(fuzzy_mm, Real)
            or not math.isfinite(fuzzy_mm) or fuzzy_mm not in (0.0, DEFAULT_FUZZY_MM)):
        raise ValueError("Comparison fuzzy_mm must be 0 or the declared 1e-6 mm policy.")
    return {
        "schema_version": POLICY_SCHEMA,
        "non_destructive": True, "run_parallel": False,
        "fuzzy_mm": float(fuzzy_mm), "automatic_retry": False,
        "copy_geometry": True, "repair_or_reorient": False,
        "classifier": {"method": "BRepClass3d_SolidClassifier.PerformInfinitePoint",
                       "required_state": "OUT", "tolerance_mm": CLASSIFIER_TOLERANCE_MM},
        "cadquery_version": cq.__version__,
        "ocp_version": getattr(OCP, "__version__", "not_exposed"),
        "scope": "Comparison only; original STEP unchanged. Fuzzy evaluation can change derived topology near its tolerance. "
                 "Validity/bounds/infinity guards do not establish correct Boolean membership or engineering accuracy.",
    }


def inspect_boolean_shape(shape):
    """Check only topology, finite bounds and native infinity; no mass integral."""
    if not isinstance(shape, cq.Shape) or shape.wrapped.IsNull():
        raise BooleanComparisonError("Comparison requires a non-null CadQuery shape.")
    try:
        if not shape.isValid():
            raise BooleanComparisonError("Comparison requires valid B-Rep topology.")
        solids = _solid_leaves(shape.wrapped)
        states = []
        for solid in solids:
            classifier = BRepClass3d_SolidClassifier(solid)
            classifier.PerformInfinitePoint(CLASSIFIER_TOLERANCE_MM)
            state = classifier.State()
            if state != TopAbs_OUT:
                raise BooleanComparisonError(f"Comparison infinity must classify OUT, got {state}.")
            states.append("OUT")
        bounds = None
        if solids:
            box = shape.BoundingBox()
            bounds = [box.xmin, box.xmax, box.ymin, box.ymax, box.zmin, box.zmax]
            if not all(math.isfinite(value) for value in bounds) or any(
                    bounds[index] >= bounds[index + 1] for index in (0, 2, 4)):
                raise BooleanComparisonError("Comparison solid bounds must be finite and nondegenerate.")
        return {"valid_brep": True, "shape_type": shape.ShapeType(), "solid_count": len(solids),
                "empty": not solids, "bounds_mm": bounds, "infinity_states": states}
    except BooleanComparisonError:
        raise
    except Exception as exc:
        raise BooleanComparisonError("Could not validate comparison topology/bounds/infinity.") from exc


def compare_boolean(first, tools, *, operation="cut", fuzzy_mm=DEFAULT_FUZZY_MM):
    """Return ``(shape, metadata)`` using one explicit non-retrying policy.

    ``tools`` is one shape or a nonempty sequence. For multiple tools, native
    OCCT semantics are ``first - union(tools)`` / ``first intersect union(tools)``.
    Empty operands follow those set identities without submitting an empty
    argument list to OCCT. No clean/fix/reverse transformation is applied.
    """
    started = time.monotonic()
    policy = boolean_policy(fuzzy_mm=fuzzy_mm)
    if operation not in ("cut", "common"):
        raise ValueError("Comparison operation must be cut or common.")
    if isinstance(tools, cq.Shape):
        tools = (tools,)
    else:
        try:
            tools = tuple(tools)
        except TypeError as exc:
            raise ValueError("Comparison tools must be a shape or a nonempty sequence.") from exc
    if not tools:
        raise ValueError("Comparison requires at least one tool.")
    first_info = inspect_boolean_shape(first)
    tool_info = [inspect_boolean_shape(tool) for tool in tools]
    effective_tools = [tool for tool, info in zip(tools, tool_info) if not info["empty"]]
    metadata = {"policy": policy, "operation": operation, "input": first_info, "tools": tool_info,
                "multiple_tools_semantics": "union_of_tools", "kernel_invoked": False,
                "error_api_exposed": None, "native_error_report": "not_invoked"}
    if first_info["empty"] or (not effective_tools and operation == "common"):
        result = cq.Compound.makeCompound([])
        metadata["empty_operand_identity"] = "empty_result"
    elif not effective_tools:
        result = first.copy()
        metadata["empty_operand_identity"] = "cut_empty_returns_independent_copy"
    else:
        builder = BRepAlgoAPI_Cut() if operation == "cut" else BRepAlgoAPI_Common()
        arguments, native_tools = TopTools_ListOfShape(), TopTools_ListOfShape()
        arguments.Append(first.copy().wrapped)
        for tool in effective_tools:
            native_tools.Append(tool.copy().wrapped)
        try:
            builder.SetArguments(arguments)
            builder.SetTools(native_tools)
            builder.SetNonDestructive(True)
            builder.SetRunParallel(False)
            builder.SetFuzzyValue(float(fuzzy_mm))
            builder.Build()
            metadata["kernel_invoked"] = True
            metadata["error_api_exposed"] = callable(getattr(builder, "HasErrors", None))
            if not builder.IsDone():
                raise BooleanComparisonError("Comparison Boolean did not finish.")
            if metadata["error_api_exposed"]:
                if builder.HasErrors():
                    raise BooleanComparisonError("Comparison Boolean reported native errors.")
                metadata["native_error_report"] = "HasErrors_false"
            else:
                metadata["native_error_report"] = "HasErrors_not_exposed_not_verified"
            native_result = builder.Shape()
            if native_result.IsNull():
                raise BooleanComparisonError("Comparison Boolean returned a null shape.")
            result = cq.Shape.cast(native_result)
        except BooleanComparisonError:
            raise
        except Exception as exc:
            raise BooleanComparisonError("Native comparison Boolean failed; no retry or repair performed.") from exc
    metadata["result"] = inspect_boolean_shape(result)
    metadata["elapsed_seconds"] = time.monotonic() - started
    return result, metadata
