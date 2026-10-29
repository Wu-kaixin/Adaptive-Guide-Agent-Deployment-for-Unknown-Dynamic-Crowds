"""Read-only GEOSSegmentIntersection_r calls against Shapely's bundled DLL.

One context per instance, process-local. The measured Windows binary is pinned
and hashed; unsupported environments refuse rather than borrow another GEOS.
"""
import ctypes
from pathlib import Path
import math

import shapely

from udc.experiments.static_multigroup import digest


class NativeSegmentIntersector:
    def __init__(self):
        folder=Path(shapely.__file__).resolve().parent.parent/'shapely.libs'
        libraries=list(folder.glob('geos_c-*.dll'))
        if len(libraries)!=1 or shapely.geos_version_string!='3.14.1':
            raise RuntimeError('Measured bundled Windows GEOS 3.14.1 required; no library substitution')
        self.path=libraries[0]
        self.library=ctypes.CDLL(str(self.path))
        self.library.GEOSversion.restype=ctypes.c_char_p
        version=self.library.GEOSversion().decode('ascii')
        if not version.startswith('3.14.1-'):
            raise RuntimeError('Bundled C API version does not match source contract')
        self.library.GEOS_init_r.argtypes=[]
        self.library.GEOS_init_r.restype=ctypes.c_void_p
        self.library.GEOS_finish_r.argtypes=[ctypes.c_void_p]
        self.library.GEOS_finish_r.restype=None
        self.function=self.library.GEOSSegmentIntersection_r
        self.function.argtypes=[ctypes.c_void_p]+[ctypes.c_double]*8+[ctypes.POINTER(ctypes.c_double)]*2
        self.function.restype=ctypes.c_int
        self.context=self.library.GEOS_init_r()
        if not self.context:raise RuntimeError('GEOS context creation failed')
        self.identity={'c_api_version':version,'shapely_version':shapely.__version__,
            'binaries':{str(p):digest(p) for p in [self.path,*folder.glob('geos-*.dll')]}}

    def intersect(self,endpoints):
        if not self.context:raise RuntimeError('Closed GEOS context')
        if len(endpoints)!=4 or any(len(p)!=2 for p in endpoints):
            raise ValueError('Four 2D endpoints required')
        values=[float(v) for p in endpoints for v in p]
        if not all(math.isfinite(v) for v in values):raise ValueError('Finite native segment inputs required')
        x,y=ctypes.c_double(),ctypes.c_double()
        code=self.function(self.context,*values,ctypes.byref(x),ctypes.byref(y))
        if code==0:raise RuntimeError('GEOSSegmentIntersection_r returned an error; preserve this case')
        if code==-1:return None
        if code!=1 or not math.isfinite(x.value) or not math.isfinite(y.value):
            raise RuntimeError('Unexpected or nonfinite GEOS output')
        return (x.value,y.value)

    def close(self):
        if self.context:
            self.library.GEOS_finish_r(self.context);self.context=None

    def __enter__(self):return self
    def __exit__(self,*_):self.close()
