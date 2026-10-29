"""Conditional source-coordinate bounds for default round buffer generation.

Inputs share a global axis box. Simplification/deletion copies inputs; offset
segments use finite_offset_enclosure; round arcs use circle_source_enclosure;
inside-turn segment intersections use the floating envelope/endpoint contract.
Closing auxiliaries require explicit rounded weighted-expression bounds.
The reviewed noding/graph paths add at most two unknown positive quantizers.
This does not establish all-input finite arithmetic or an original-world mass
bound. It is not a runtime service certificate.
"""
from dataclasses import dataclass
from fractions import Fraction as F

from .offset_interval import Interval
from .precision_enclosure import _representable_inside,_rn
from .finite_offset_enclosure import finite_offset_box
from .circle_source_enclosure import circular_source_box
from .universal_precision_enclosure import unknown_precision_box


def hull_boxes(*boxes):
    return tuple(Interval(min(b[j].lo for b in boxes),max(b[j].hi for b in boxes)) for j in (0,1))


def closing_auxiliary_box(offset_box,center_box,factor):
    """Range of RN((RN(k*o+c))/(k+1)), separate or fused numerator.

    Both input variables and the positive arithmetic maps are monotone. The
    library factors 1 and 80 are exact binary64 integers, as are 2 and 81.
    This need not preserve the ideal convex-combination input interval.
    """
    if factor not in (1,80) or len(offset_box)!=2 or len(center_box)!=2:
        raise ValueError('Declared default closing factor and 2D input boxes required')
    k=F(factor);result=[]
    for offset,center in zip(offset_box,center_box):
        ol,oh=_representable_inside(offset);cl,ch=_representable_inside(center)
        def separate(o,c):return _rn(_rn(_rn(k*o)+c)/(k+1))
        def fused(o,c):return _rn(_rn(k*o+c)/(k+1))
        result.append(Interval(min(separate(ol,cl),fused(ol,cl)),max(separate(oh,ch),fused(oh,ch))))
    return tuple(result)


@dataclass(frozen=True)
class RoundBufferSourceEnclosure:
    input_box: tuple
    normal_displacement_limit: F
    offset_box: tuple
    arc_box: tuple
    closing_boxes: tuple
    raw_box: tuple
    output_box: tuple

    def serial(self):
        serial=lambda box:[q.serial() for q in box]
        return {'status':'CONDITIONAL_ROUND_BUFFER_SOURCE_ENCLOSURE','input_box':serial(self.input_box),
            'normal_displacement_limit':str(self.normal_displacement_limit),
            'offset_box':serial(self.offset_box),'arc_box':serial(self.arc_box),
            'closing_boxes':{str(k):serial(b) for k,b in self.closing_boxes},
            'raw_source_box':serial(self.raw_box),'unknown_precision_output_box':serial(self.output_box),
            'all_inputs_defined':False,'original_reference_enclosed':False,'service_certificate':False,
            'condition':'default round/non-single-sided FLOATING input; reviewed source; finite required operations and positive computed norms; unit-range trig; correctly rounded sqrt; XY copies'}


def round_buffer_source_box(input_box,distance):
    """Cover finite branch outputs for either sign of polygon buffer distance.

    Requires every input ring vertex in input_box. Cap/join styles other than
    round, nonzero noder offsets, unsafe fast-math reassociation and unreviewed
    precision models are outside the contract. It does not prove an output
    exists or that all endpoint combinations have a defined offset.
    """
    if len(input_box)!=2 or any(not isinstance(v,Interval) for v in input_box):
        raise ValueError('2D input-coordinate box required')
    d=Interval.point(abs(float(distance)))
    normal=finite_offset_box(input_box,input_box,d,1);offsets=normal.endpoints[0]
    arcs=circular_source_box(input_box,float(d.lo))
    closing=tuple((k,closing_auxiliary_box(offsets,input_box,k)) for k in (1,80))
    raw=hull_boxes(input_box,offsets,arcs,*[b for _,b in closing])
    return RoundBufferSourceEnclosure(tuple(input_box),normal.normal_displacement_limit,offsets,arcs,closing,raw,unknown_precision_box(raw,stages=2))
