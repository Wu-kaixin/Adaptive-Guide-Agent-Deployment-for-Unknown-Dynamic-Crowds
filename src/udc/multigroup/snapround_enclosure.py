"""Source-level XY range contracts for a pinned GEOS snap-rounding noder.

Conditional on valid finite segment strings, every initial coordinate/node
being in the input box, the reviewed GEOS 3.14.1 coordinate-production paths,
and the finite binary64 model in precision_enclosure. This neither selects a
BufferOp fallback scale nor bounds the offset curves supplied to that noder.
"""
from .precision_enclosure import PrecisionModel


def snapround_output_box(box, model: PrecisionModel):
    """Bound one SnapRoundingNoder invocation, with no repeated-quantizer premise.

    Unrounded line intersections stay in the global input axis box by their
    envelope/endpoint postcondition. Near vertices are copies. Both vertex
    and intersection pixels, and rounded segment strings, use model.makePrecise.
    Snapping/subdivision subsequently copies those coordinates. Hence the
    componentwise scalar image encloses all finite returned XY coordinates.
    It does not establish topology, successful termination, or local accuracy.
    """
    if len(box)!=2 or model.scale<=0:
        raise ValueError('A 2D box and positive fixed precision model are required')
    return tuple(model.range(axis) for axis in box)


def scaled_snapround_output_box(box, model: PrecisionModel):
    """Bound zero-offset ScaledNoder(SnapRoundingNoder(unit), scale=S).

    For S != 1 the scaled input extrema J(RN(x*S)) are representable integers.
    The inner unit quantizer fixes those extrema, so intersection, vertex and
    pixel coordinates stay within that integer axis box. Monotone RN(z/S)
    gives the combined scalar-map image. For S=1 the wrapper skips scaling,
    and the inner unit model supplies that same image. Nonzero offsets and
    all other inner noders are outside this contract.
    """
    if len(box)!=2 or model.scale<=0:
        raise ValueError('A 2D box and positive fixed precision model are required')
    return tuple(model.range(axis,map_kind='scaled_noder') for axis in box)
