"""Strict frame-load directions. Named CSys requires a verified transformation.

SAP default local 2 lies in the 1-Z plane (vertical members: global X).
https://help.csiamerica.com/help/sap2000/26/26.0.0/SAP2000/WebHelp/Menus/Assign/Frame/Frame_Local_Axes.htm
CSI direction codes: https://docs.csiamerica.com/help-files/etabs-api-2016/html/9a3832e7-d1eb-a2b6-5f28-679cd0f1cfc2.htm
"""
# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from math import cos, sin, radians, sqrt, isfinite


def load_direction_vector(dir_code, frame_axis=None, angle_deg=None, csys="Global"):
    text = str(dir_code).casefold().replace(' ', '').replace('_', '')
    labels = {'x':4, 'globalx':4, 'y':5, 'globaly':5, 'z':6, 'globalz':6,
              'local1':1, 'local2':2, 'local3':3, 'gravity':10,
              'projectedx':7, 'projectedy':8, 'projectedz':9, 'projectedgravity':11,
              'globalxprojected':7, 'globalyprojected':8, 'globalzprojected':9}
    try:
        numeric = float(text) if text not in labels else float(labels[text])
        if not isfinite(numeric) or not numeric.is_integer() or not 1 <= numeric <= 11:
            raise ValueError
        code = int(numeric)
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f'Dirección de carga no interpretable: {dir_code!r}') from None
    system = str(csys).strip().casefold()
    if system not in ('global', 'local'):
        raise ValueError(f'Sistema de coordenadas no interpretable: {csys!r}')
    if code <= 3:
        if system != 'local':
            raise ValueError('Dirección local incompatible con CSys')
        if frame_axis is None or len(frame_axis) != 3 or not all(isfinite(float(x)) for x in frame_axis):
            raise ValueError('Eje de barra no interpretable')
        length = sqrt(sum(float(x)**2 for x in frame_axis))
        if length <= 0:
            raise ValueError('Barra de longitud cero')
        u = tuple(float(x)/length for x in frame_axis)
        if code == 1:
            return u
        if angle_deg is None or not isfinite(float(angle_deg)):
            raise ValueError('Orientación local 2/3 desconocida o avanzada')
        horizontal = sqrt(u[0]**2 + u[1]**2)
        v = (-u[0]*u[2]/horizontal, -u[1]*u[2]/horizontal, horizontal) if horizontal > 1e-12 else (1.,0.,0.)
        w = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
        c, s = cos(radians(float(angle_deg))), sin(radians(float(angle_deg)))
        return tuple(c*a+s*b for a,b in zip(v,w)) if code == 2 else tuple(c*b-s*a for a,b in zip(v,w))
    if system != 'global':
        raise ValueError('Dirección cartesiana incompatible con CSys Local')
    return {4:(1.,0.,0.),5:(0.,1.,0.),6:(0.,0.,1.),7:(1.,0.,0.),
            8:(0.,1.,0.),9:(0.,0.,1.),10:(0.,0.,-1.),11:(0.,0.,-1.)}[code]
