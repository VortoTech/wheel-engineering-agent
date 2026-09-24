"""Analytic A/B evidence for a candidate integrator, not a production switch.

The direct legacy call is a deliberately retained negative control. Do not
infer universal accuracy from these two curves or from kernel error estimates.
"""
import math

import cadquery as cq
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
import pytest

from wheelcam.geometry import _window_outline_wire
from wheelcam.sector_program import example_program, patch_feature, sample_feature


@pytest.mark.parametrize("width", [6, 8])
def test_span_integrator_matches_analytic_capsule_where_legacy_gauss_does_not(width):
    program = patch_feature(example_program(), "root_slot_left", {"width_mm": width})
    feature = next(item for item in program.features if item.id == "root_slot_left")
    wire = _window_outline_wire(sample_feature(feature))
    shape = cq.Solid.extrudeLinear(wire, [], cq.Vector(0, 0, 6))
    assert shape.isValid() and len(shape.Solids()) == 1
    analytic = (width * (feature.length_mm - width) + math.pi * (width / 2) ** 2) * 6
    old, candidate = GProp_GProps(), GProp_GProps()
    BRepGProp.VolumeProperties_s(shape.wrapped, old, 1e-8, True)
    error = BRepGProp.VolumePropertiesGK_s(shape.wrapped, candidate, 1e-7, True, True)
    assert abs(old.Mass() - analytic) / analytic > .1  # known bad negative control
    assert math.isfinite(error) and error >= 0
    # Allow the measured periodic spline approximation, not just integration.
    assert candidate.Mass() == pytest.approx(analytic, abs=.005)
