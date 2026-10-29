"""Pinned Windows GEOS scalar selector probes; no buffer/overlay is executed.

BufferOp's selector only reads an envelope's maximum absolute coordinate.
A temporary GEOS point (magnitude,0) supplies the same scalar envelope input;
its extent is checked before the private exported static method is called.
"""
import ctypes as C
import math
from pathlib import Path

from .native_geos_segment import NativeSegmentIntersector


class NativePrecisionSelectors(NativeSegmentIntersector):
    PRECISION='?precisionScale@PrecisionUtil@overlayng@operation@geos@@CANNH@Z'
    SAFE='?safeScale@PrecisionUtil@overlayng@operation@geos@@SANN@Z'
    BUFFER='?precisionScaleFactor@BufferOp@buffer@operation@geos@@CANPEBVGeometry@geom@4@NH@Z'

    def __init__(self):
        super().__init__()
        paths=list(Path(self.path).parent.glob('geos-*.dll'))
        if len(paths)!=1:self.close();raise RuntimeError('Unique bundled C++ GEOS library required')
        self.cpp=C.CDLL(str(paths[0]))
        self.precision=getattr(self.cpp,self.PRECISION);self.precision.argtypes=[C.c_double,C.c_int];self.precision.restype=C.c_double
        self.safe=getattr(self.cpp,self.SAFE);self.safe.argtypes=[C.c_double];self.safe.restype=C.c_double
        self.buffer=getattr(self.cpp,self.BUFFER);self.buffer.argtypes=[C.c_void_p,C.c_double,C.c_int];self.buffer.restype=C.c_double
        self.create=self.library.GEOSGeom_createPointFromXY_r;self.create.argtypes=[C.c_void_p,C.c_double,C.c_double];self.create.restype=C.c_void_p
        self.destroy=self.library.GEOSGeom_destroy_r;self.destroy.argtypes=[C.c_void_p,C.c_void_p];self.destroy.restype=None
        self.extent=self.library.GEOSGeom_getExtent_r;self.extent.argtypes=[C.c_void_p,C.c_void_p]+[C.POINTER(C.c_double)]*4;self.extent.restype=C.c_int
        self.identity['selector_symbols']=[self.PRECISION,self.SAFE,self.BUFFER]

    def evaluate(self,magnitude,distance,digits):
        magnitude,distance=float(magnitude),float(distance)
        digits=tuple(digits)
        if (not math.isfinite(magnitude) or not 1e-12<=magnitude<=1e12
                or not math.isfinite(distance) or abs(distance)>1e6
                or not digits or any(type(p) is not int or not 6<=p<=12 for p in digits)):
            raise ValueError('Controlled positive finite envelope and buffer digit range required')
        point=self.create(self.context,magnitude,0.)
        if not point:raise RuntimeError('Temporary selector point creation failed')
        try:
            bounds=[C.c_double() for _ in range(4)]
            if self.extent(self.context,point,*[C.byref(v) for v in bounds])!=1:
                raise RuntimeError('Temporary selector point extent failed')
            if [v.value for v in bounds]!=[magnitude,0.,magnitude,0.]:
                raise RuntimeError('Temporary geometry magnitude changed')
            buffer=[self.buffer(point,distance,p) for p in digits]
            expanded=magnitude+2*max(distance,0.)
            projected=[self.precision(expanded,p) for p in digits]
            safe=self.safe(magnitude);safe_projected=self.precision(magnitude,14)
            if not all(math.isfinite(v) and v>0 for v in [*buffer,*projected,safe,safe_projected]):
                raise RuntimeError('Controlled native selector returned nonpositive/nonfinite scale')
            return dict(buffer_scales_hex=[v.hex() for v in buffer],
                common_expression_scales_hex=[v.hex() for v in projected],
                overlay_safe_scale_hex=safe.hex(),common_expression_safe_hex=safe_projected.hex(),
                expanded_magnitude_hex=expanded.hex(),temporary_point_extent_checked=True)
        finally:self.destroy(self.context,point)
