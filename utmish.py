from utm.error import OutOfRangeError

# For most use cases in this module, numpy is indistinguishable
# from math, except it also works on numpy arrays
try:
    import numpy as mathlib
    use_numpy = True
except ImportError:
    import math as mathlib
    use_numpy = False


K0 = 0.9996

E = 0.00669438
E2 = E * E
E3 = E2 * E
E_P2 = E / (1.0 - E)

SQRT_E = mathlib.sqrt(1 - E)
_E = (1 - SQRT_E) / (1 + SQRT_E)
_E2 = _E * _E
_E3 = _E2 * _E
_E4 = _E3 * _E
_E5 = _E4 * _E

M1 = (1 - E / 4 - 3 * E2 / 64 - 5 * E3 / 256)
M2 = (3 * E / 8 + 3 * E2 / 32 + 45 * E3 / 1024)
M3 = (15 * E2 / 256 + 45 * E3 / 1024)
M4 = (35 * E3 / 3072)

P2 = (3. / 2 * _E - 27. / 32 * _E3 + 269. / 512 * _E5)
P3 = (21. / 16 * _E2 - 55. / 32 * _E4)
P4 = (151. / 96 * _E3 - 417. / 128 * _E5)
P5 = (1097. / 512 * _E4)

R = 6378137

def in_bounds(x, lower, upper, upper_strict=False):
    if upper_strict and use_numpy:
        return lower <= mathlib.min(x) and mathlib.max(x) < upper
    elif upper_strict and not use_numpy:
        return lower <= x < upper
    elif use_numpy:
        return lower <= mathlib.min(x) and mathlib.max(x) <= upper
    return lower <= x <= upper

def mixed_signs(x):
    return use_numpy and mathlib.min(x) < 0 and mathlib.max(x) >= 0


def negative(x):
    if use_numpy:
        return mathlib.max(x) < 0
    return x < 0


def to_lonlat(easting, northing, central_longitude):
    """This function convert a UTMish coordinate into Latitude and Longitude
       using a specified central_longitude

        Parameters
        ----------
        easting: int
            Easting value of UTM coordinate

        northing: int
            Northing value of UTM coordinate

        central_longitude: float
            The central line of longitude originally used to create this 
            UTM-ish coordinate.

    """

    x = easting - 500000
    y = northing

    #if not northern:
    #    y -= 10000000
    #TODO: probably should handle northern

    m = y / K0
    mu = m / (R * M1)

    p_rad = (mu +
             P2 * mathlib.sin(2 * mu) +
             P3 * mathlib.sin(4 * mu) +
             P4 * mathlib.sin(6 * mu) +
             P5 * mathlib.sin(8 * mu))

    p_sin = mathlib.sin(p_rad)
    p_sin2 = p_sin * p_sin

    p_cos = mathlib.cos(p_rad)

    p_tan = p_sin / p_cos
    p_tan2 = p_tan * p_tan
    p_tan4 = p_tan2 * p_tan2

    ep_sin = 1 - E * p_sin2
    ep_sin_sqrt = mathlib.sqrt(1 - E * p_sin2)

    n = R / ep_sin_sqrt
    r = (1 - E) / ep_sin

    c = _E * p_cos**2
    c2 = c * c

    d = x / (n * K0)
    d2 = d * d
    d3 = d2 * d
    d4 = d3 * d
    d5 = d4 * d
    d6 = d5 * d

    latitude = (p_rad - (p_tan / r) *
                (d2 / 2 -
                 d4 / 24 * (5 + 3 * p_tan2 + 10 * c - 4 * c2 - 9 * E_P2)) +
                 d6 / 720 * (61 + 90 * p_tan2 + 298 * c + 45 * p_tan4 - 252 * E_P2 - 3 * c2))

    longitude = (d -
                 d3 / 6 * (1 + 2 * p_tan2 + c) +
                 d5 / 120 * (5 - 2 * c + 28 * p_tan2 - 3 * c2 + 8 * E_P2 + 24 * p_tan4)) / p_cos

    return ( mathlib.degrees(longitude) + central_longitude,
             mathlib.degrees(latitude) )


def from_lonlat(longitude, latitude, central_longitude):
    """This function convert Latitude and Longitude to UTMish coordinate
       using a grid centered on central_longitude.  This differs from
       usual UTM coordinates which are based on discrete zones

        Parameters
        ----------
        latitude: float
            Latitude between 80 deg S and 84 deg N, e.g. (-80.0 to 84.0)

        longitude: float
            Longitude between 180 deg W and 180 deg E, e.g. (-180.0 to 180.0).

        central_longitude: float
            A line of longitude to base the UTMish grid around.  Reduces grid
            distortion by basing the grid around the location where the 
            work is actually being done.
    """
    if not in_bounds(latitude, -80.0, 84.0):
        raise OutOfRangeError('latitude out of range (must be between 80 deg S and 84 deg N)')
    if not in_bounds(longitude, -180.0, 180.0):
        raise OutOfRangeError('longitude out of range (must be between 180 deg W and 180 deg E)')

    lat_rad = mathlib.radians(latitude)
    lat_sin = mathlib.sin(lat_rad)
    lat_cos = mathlib.cos(lat_rad)

    lat_tan = lat_sin / lat_cos
    lat_tan2 = lat_tan * lat_tan
    lat_tan4 = lat_tan2 * lat_tan2

    lon_rad = mathlib.radians(longitude)
    central_lon_rad = mathlib.radians(central_longitude)

    n = R / mathlib.sqrt(1 - E * lat_sin**2)
    c = E_P2 * lat_cos**2

    a = lat_cos * (lon_rad - central_lon_rad)
    a2 = a * a
    a3 = a2 * a
    a4 = a3 * a
    a5 = a4 * a
    a6 = a5 * a

    m = R * (M1 * lat_rad -
             M2 * mathlib.sin(2 * lat_rad) +
             M3 * mathlib.sin(4 * lat_rad) -
             M4 * mathlib.sin(6 * lat_rad))

    easting = K0 * n * (a +
                        a3 / 6 * (1 - lat_tan2 + c) +
                        a5 / 120 * (5 - 18 * lat_tan2 + lat_tan4 + 72 * c - 58 * E_P2)) + 500000

    northing = K0 * (m + n * lat_tan * (a2 / 2 +
                                        a4 / 24 * (5 - lat_tan2 + 9 * c + 4 * c**2) +
                                        a6 / 720 * (61 - 58 * lat_tan2 + lat_tan4 + 600 * c - 330 * E_P2)))

    if mixed_signs(latitude):
        raise ValueError("latitudes must all have the same sign")
    elif negative(latitude):
        northing += 10000000

    return easting, northing


