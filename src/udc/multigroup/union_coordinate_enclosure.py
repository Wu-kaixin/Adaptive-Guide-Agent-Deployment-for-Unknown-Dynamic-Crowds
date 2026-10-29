"""Conditional coordinate bounds for polygon unary union and resampling.

The default C API uses ClassicUnionStrategy, including its buffer(0) fallback.
One unknown positive quantizer covers a successful binary union path: zero
buffer clones rings before a unit snap-rounder in scaled coordinates. The
rounded integer-axis endpoints are invariant under that unit noder. A two-map
coarse option preserves the earlier v1 comparison.
the balanced cascade has depth ceil(log2(number of polygon components)).
This does not infer a component count from people, validity, finite arithmetic,
an unknown original reference, or any demand mass.
"""
from dataclasses import dataclass
from fractions import Fraction as F

from .offset_interval import Interval, U, ETA, UncertifiedArithmetic
from .universal_precision_enclosure import unknown_precision_box


@dataclass(frozen=True)
class UnaryUnionCoordinateBox:
    input_box: tuple
    polygon_component_limit: int
    maximum_binary_depth: int
    quantizers_per_binary: int
    output_box: tuple

    def serial(self):
        return dict(input_box=[q.serial() for q in self.input_box],
            polygon_component_limit=self.polygon_component_limit,
            maximum_binary_depth=self.maximum_binary_depth,
            quantizers_per_binary=self.quantizers_per_binary,
            unknown_quantizer_stage_limit=self.quantizers_per_binary*self.maximum_binary_depth,
            output_box=[q.serial() for q in self.output_box],
            status='CONDITIONAL_POLYGON_UNARY_UNION_COORDINATE_BOX',
            component_limit_proved=False,all_inputs_defined=False,
            original_reference_enclosed=False,service_certificate=False)


def unary_union_coordinate_box(input_box, polygon_component_limit, *, quantizers_per_binary=1):
    """Bound default floating polygon-only unary union given a component cap.

    Count polygon COMPONENTS extracted by UnaryUnionOp, not input containers.
    Floating intersection envelopes and snap-index copies preserve axis boxes.
    Fixed OverlayNG uses one quantizer; ClassicUnionStrategy may instead use
    zero-buffer fallback on original inputs, covered by one scaled map. Failed
    attempt outputs are discarded. All reviewed arithmetic/copy preconditions
    and positive finite quantizer parameters are explicit conditions.
    """
    if (len(input_box)!=2 or any(not isinstance(q,Interval) for q in input_box)
            or type(polygon_component_limit) is not int or polygon_component_limit<1
            or type(quantizers_per_binary) is not int or quantizers_per_binary not in (1,2)):
        raise ValueError('2D coordinate box and positive polygon component limit required')
    depth=(polygon_component_limit-1).bit_length()
    return UnaryUnionCoordinateBox(tuple(input_box),polygon_component_limit,depth,quantizers_per_binary,
        unknown_precision_box(input_box,stages=quantizers_per_binary*depth))


def interpolation_coordinate_box(input_box):
    """All finite clamped RN((b-a)*t+a), endpoints in the SAME input box.

    No exact edge list is required. Assume binary64 endpoints and t in [0,1],
    nearest subtraction/multiplication/addition (or fused multiply-add).
    The magnitude admission prevents overflow of these source expressions.
    """
    if len(input_box)!=2 or any(not isinstance(q,Interval) for q in input_box):
        raise ValueError('2D input coordinate box required')
    result=[];errors=[]
    for axis in input_box:
        magnitude=max(abs(axis.lo),abs(axis.hi));width=axis.hi-axis.lo
        if magnitude>2**500:raise UncertifiedArithmetic('interpolation_source_magnitude_exceeds_contract')
        difference_bound=(1+U)*width+ETA
        subtraction_error=U*width+ETA
        product_error=U*difference_bound+ETA
        addition_error=U*(magnitude+difference_bound+product_error)+ETA
        error=subtraction_error+product_error+addition_error
        errors.append(error);result.append(Interval(axis.lo-error,axis.hi+error))
    return tuple(result),tuple(errors)
