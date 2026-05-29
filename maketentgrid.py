#!/usr/bin/python3

import os
import utmish
import simplekml
import shapefile
import math
import sys
import traceback
import zipfile
import datetime
import csv
import fpdf
import json

from io import BytesIO
from io import StringIO

from contextlib import redirect_stdout



class FileWriter(object):
    """ implements a simple class that allows us to write to files in a
        directory using the same call as zipfile, so we can switch between
        using zips and directories easily
    """
    def __init__(self):
        pass

    def __enter__(self):
        # do nothing since there's really no setup and cleanup
        # to do in this class
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        # no cleanup necesary
        pass

    def writestr(self, filename, data):
        """
            Create a file called filename relative to self._path
            write data to it. Either bytes or str, which is 
            implicitly converted to UTF-8.
        """
        #combined_path = os.path.join(self._path, filename)
        directory = os.path.dirname(filename)
        if not os.path.exists(directory):
            os.makedirs(directory)

        file=open(filename,"wb")
        with file:
            if isinstance(data,str):
                file.write(data.encode('UTF-8'))
            else:
                file.write(data)

class BeePDF(fpdf.FPDF):
    def __init__(self, orientation, units, papersize):
        super().__init__(orientation, units, papersize)
        self.last_x=None
        self.last_y=None

    def move_to(self, x,y):
        self.last_x = x
        self.last_y = y

    def line_to(self, x,y):
        self.line(self.last_x, self.last_y, x, y)
        self.last_x = x
        self.last_y = y

def ll_at( lon1, lat1, bearing, distance):
    R = 6378137 #Radius of the Earth in metres
    brng = math.radians(bearing)

    #lat2  52.20444 - the lat result I'm hoping for
    #lon2  0.36056 - the long result I'm hoping for.

    lat1 = math.radians(lat1) #Current lat point converted to radians
    lon1 = math.radians(lon1) #Current long point converted to radians

    lat2 = math.asin( math.sin(lat1)*math.cos(distance/R) +
         math.cos(lat1)*math.sin(distance/R)*math.cos(brng))

    lon2 = lon1 + math.atan2(math.sin(brng)*math.sin(distance/R)*math.cos(lat1),
                 math.cos(distance/R)-math.sin(lat1)*math.sin(lat2))

    return (math.degrees(lon2), math.degrees(lat2))

#name is just the date in MMDDYY format
def make_files(myzip, field_path, name, origin):
    # 
    # create the default trimble files for field origin, etc
    myzip.writestr("%s/origin.kml" % field_path, 
                   "<kml>\n    <Placemark>\n        <name>"
                   "%s_0001</name>\n" % name + 
                   "        <Point>\n            <coordinates>"
                   "%0.9f,%0.9f,761.049" % (origin[0], origin[1]) +
                   "</coordinates>\n        </Point>\n    </Placemark>\n</kml>\n")

    myzip.writestr("%s/%0.5fE%0.5fN761H.pos" % (field_path, origin[0],origin[1]),
                   '')

    myzip.writestr("%s/newField.ok" % (field_path,), '')

def make_pdf_circle_bays(pdf_writer, field):
    """
        Draw the outline of the circle and male bays using the
        pdf_writer (instance of FPDF) onto the current field's PDF page
    """
    #TODO: cleaner, programmatic way to do this
    width = 8.5 * 72
    height = 11 * 72

    #make the entire circle fit on the page
    scale = 8/8.5*width / field['Radius'] / 2

    pdf_writer.set_line_width(0.5)
    pdf_writer.set_draw_color(0,0,0)

    if field['North_limit'] is not None:
        northlimit = field['North_limit']
    else:
        northlimit = field['Radius']

    if field['East_limit'] is not None:
        eastlimit = field['East_limit']
    else:
        eastlimit = field['Radius']

    if field['South_limit'] is not None:
        southlimit = field['South_limit']
    else:
        southlimit = field['Radius']

    if field['West_limit'] is not None:
        westlimit = field['West_limit']
    else:
        westlimit = field['Radius']

    # draw pivot point
    pdf_writer.ellipse(width/2-2,height/2-2,4,4,'DF')
    
    if field['pie_slice']:
        start_angle = 360 - field['pie_slice'][0] + 90
        end_angle = 360 - field['pie_slice'][1] + 90
        x = math.cos(math.radians(start_angle)) * field['Radius']
        y = math.sin(math.radians(start_angle)) * field['Radius']
        if x > eastlimit: x=eastlimit
        elif x < -westlimit: x = -westlimit
        
        if y > northlimit: y = northlimit
        elif y < -southlimit: y = -southlimit

        pdf_writer.move_to(x * scale + width/2,
                           -y * scale + height/2)

        pdf_writer.line_to(width/2, height/2)

        x = math.cos(math.radians(end_angle)) * field['Radius']
        y = math.sin(math.radians(end_angle)) * field['Radius']
        if x > eastlimit: x=eastlimit
        elif x < -westlimit: x = -westlimit
        
        if y > northlimit: y = northlimit
        elif y < -southlimit: y = -southlimit

        pdf_writer.line_to( x * scale + width/2,
                            -y * scale + height/2 )

        if start_angle < end_angle:
            if end_angle > 180: end_angle = -360 + end_angle
    else:
        start_angle = 360
        end_angle = 0

    x = math.cos(math.radians(start_angle)) * field['Radius']
    y = math.sin(math.radians(start_angle)) * field['Radius']
    if x > eastlimit: x=eastlimit
    elif x < -westlimit: x = -westlimit
    
    if y > northlimit: y = northlimit
    elif y < -southlimit: y = -southlimit

    pdf_writer.move_to(x * scale + width/2,
                      -y * scale + height/2)

    # if arc crosses 0, adjust

    pdf_writer.set_line_width(1)
    angle = start_angle
    while True:
        x = math.cos(math.radians(angle)) * field['Radius']
        y = math.sin(math.radians(angle)) * field['Radius']
        if x > eastlimit: x=eastlimit
        elif x < -westlimit: x = -westlimit
        
        if y > northlimit: y = northlimit
        elif y < -southlimit: y = -southlimit

        pdf_writer.line_to(x * scale + width/2,
                          -y * scale + height/2)

        angle -= 1
        if angle < end_angle: break
     
    pdf_writer.set_line_width(0.5)
    radius = field['Radius']
    radius_sqr = radius * radius
    sprayer_width = field['Sprayer_width']

    if field['spacing'] is not None:
        spacing = field['spacing']  #float(field['spacing']) * conv
    else:
        spacing = calculate_spacing(radius, sprayer_width, num_tents = field['# of Structures'])

    directional_offset = field['directional_offset']
    #if directional_offset:
    #    directional_offset = float(directional_offset) * conv
    #else:
    #    directional_offset = 0

    rotate = 0 - field['Seed_angle']
    rotate = (rotate + 180) % 360 - 180
    pie_slice = field['pie_slice']
    lat_shift = field['Lateral_offset']


    if 'Female_bays_per_width' in field and field['Female_bays_per_width']:
        bays_per_sprayer_width = field['Female_bays_per_width']

        # Draw male bays
        pdf_writer.set_draw_color(0,0,230)
        rows = range(-int(radius / sprayer_width * bays_per_sprayer_width ) - 1,int(radius / sprayer_width * bays_per_sprayer_width) + 1)
        for r in rows:
            east = r * sprayer_width / bays_per_sprayer_width + sprayer_width / bays_per_sprayer_width / 2
            north = radius

            east1 = east * math.cos(math.radians(rotate)) - north * math.sin(math.radians(rotate))
            north1 = north * math.cos(math.radians(rotate)) + east * math.sin(math.radians(rotate))

            east2 = east * math.cos(math.radians(rotate)) + north * math.sin(math.radians(rotate))
            north2 = -north * math.cos(math.radians(rotate)) + east * math.sin(math.radians(rotate))

            if abs(east1 - east2) < 0.001: #north and south line
                # trim lines that are outside of the circle entirely
                if east1 > eastlimit or east1 < -westlimit: continue
                if north1 < -southlimit: north1 = -southlimit
                elif north1 > northlimit: north1 = northlimit

                if north2 < -southlimit: north2 = -southlimit
                elif north2 > northlimit: north2 = northlimit

            elif abs(north1 - north2) < 0.001: #east and west line
                #trim lines that are outside of the circle entirely
                if north1 > northlimit or north1 < -southlimit: continue

                if east1 < -westlimit: east1 = -westlimit
                elif east1 > eastlimit: east1 = eastlimit

                if east2 < -westlimit: east2 = -westlimit
                elif east2 > eastlimit: east2 = eastlimit
            else: #some other angle that we can calculate slope
                # if midpoint of bay is outside the radius, we can
                # safely skip it
                e = (east2 + east1) / 2
                n = (north2 + north1) / 2
                #print (east2, east1, e, n)

                if e*e + n*n > radius_sqr: continue

                #TODO: if it's outside the pie skip

            """
            if east1 < -westlimit: east1 = -westlimit
            elif east1 > eastlimit: east1 = eastlimit

            if east2 < -westlimit: east2 = -westlimit
            elif east2 > eastlimit: east2 = eastlimit

            if north1 < -southlimit: north1 = -southlimit
            elif north1 > northlimit: north1 = northlimit

            if north2 < -southlimit: north2 = -southlimit
            elif north2 > northlimit: north2 = northlimit
            """

            pdf_writer.line(east1 * scale + width/2, -north1 * scale + height/2,
                            east2 * scale + width/2, -north2 * scale + height/2)
        
    pdf_writer.set_draw_color(0,0,0)
    pdf_writer.set_font("Arial")
    pdf_writer.set_font_size(24)
    pdf_writer.text(72,72,field['Name'].strip())


def make_tents(myzip, trimble_path, field_name, pivotpoint, radius, width, lat_shift, angle, pie_slice = None, **kwargs):
    """
        pivotpoint is tuple of longitude, latitude
        radius is maximum radius of circle in metres
        edge_dist is distance to north, south, west, or east edges when end gun is off, if pivot has end gun
        width is sprayer width or distance between rows of tents
        lat_shift is distance from center line to place first row of tents (usually a couple of meters)
        angle is seeding angle

        quadrants is list of quadrants of circle to place tents in (replace with arc)
    """


    #field_path = trimble_path + "/" + field_name

    eastlimit = kwargs.get('eastlimit', radius)
    northlimit = kwargs.get('northlimit', radius)
    westlimit = kwargs.get('westlimit', radius)
    southlimit = kwargs.get('southlimit', radius)
    boundary_polygon_enu = kwargs.get('boundary_polygon_enu', None)
    pivot_tracks_m = kwargs.get('pivot_tracks_m', [])
    pivot_track_exclusion_m = kwargs.get('pivot_track_exclusion_m', 3.048)
    if boundary_polygon_enu:
        # Use bounding radius of polygon for grid generation range
        radius = max(math.sqrt(e*e + n*n) for e, n in boundary_polygon_enu) * 1.05

    # PDF options
    pdf = kwargs.get('pdf', None)
    pdf_width = 8.5 * 72
    pdf_height = 11 * 72
    scale = 8*72 / radius / 2

    # create starting point for grid
    easting, northing = utmish.from_lonlat(pivotpoint[0], pivotpoint[1], pivotpoint[0])
    radius_sqr = radius * radius

    num_tents=kwargs.get('num_tents', None)

    if kwargs['spacing'] is not None:
        spacing = kwargs['spacing']
    elif num_tents is not None:
        kw = dict(pie_slice=pie_slice,
                  northlimit=northlimit, eastlimit=eastlimit,
                  southlimit=southlimit, westlimit=westlimit,
                  exp_rows=kwargs.get('exp_rows') if kwargs.get('exp_rows') else None,
                  exp_rows_start_odd=kwargs.get('exp_rows_start_odd', True),
                  directional_offset=kwargs.get('directional_offset', 0))
        spacing = find_exact_spacing(num_tents, pivotpoint, radius, width,
                                     lat_shift, angle, **kw)
    else:
        spacing = calculate_spacing(radius, width)

    rotate = 0 - angle
    rotate = (rotate + 180) % 360 - 180
    #rotate = -rotate

    margin = width / 2.0


    kml = simplekml.Kml()

    shp = BytesIO()
    shx = BytesIO()
    dbf = BytesIO()

    pid = 3062 #arbitrary number for shapefile

    w = shapefile.Writer(shp = shp, shx = shx, dbf = dbf, shapeType=1 )
    w.field('Date', 'D')
    w.field('Time', 'C', size=10)
    w.field('Version', 'C', size=8)
    w.field('Id', 'N')
    w.field('Name', 'C', size=32)
    w.field('Latitude', 'N', decimal=8)
    w.field('Longitude', 'N', decimal= 8)
    w.field('Height', 'N', decimal=3)
    w.field('AlarmRad', 'N', decimal=4)
    w.field('WarningRad', 'N', decimal=4)
    w.field('Status_Text', 'C', size=8)
    w.field('Visible', 'N')

    csvbuffer = StringIO()
    csvdata = csv.DictWriter(csvbuffer, fieldnames = [ 'GPS Position', 'Tent'])
    csvdata.writeheader()

    # Experimental rows
    exp_rows = kwargs['exp_rows']
    if exp_rows:
        rows = exp_rows
        exp_rows = True
        odd = kwargs.get('exp_rows_start_odd', True)
    else:
        rows = range(-int(radius / width), int(radius / width) + 1)

    directional_offset = kwargs['directional_offset']

    # Collect all valid grid positions first so we can trim to exact num_tents
    positions = []
    for r in rows:
        if not exp_rows:
            odd = r % 2
        for c in range(-int(radius / spacing) - 1, int(radius / spacing) + 1):
            if odd:
                east  = r * width + lat_shift
                north = c * spacing + spacing / 2 + directional_offset
            else:
                east  = r * width + lat_shift
                north = c * spacing + directional_offset

            east1  = east  * math.cos(math.radians(rotate)) - north * math.sin(math.radians(rotate))
            north1 = north * math.cos(math.radians(rotate)) + east  * math.sin(math.radians(rotate))
            east, north = east1, north1

            if boundary_polygon_enu:
                if not _point_in_polygon(east, north, boundary_polygon_enu): continue
            else:
                if east * east + north * north > radius_sqr: continue
                if eastlimit  and east >  eastlimit:  continue
                if westlimit  and east < -westlimit:  continue
                if northlimit and north >  northlimit: continue
                if southlimit and north < -southlimit: continue

            if pie_slice and (north or east):
                a = math.degrees(math.atan2(east, north))
                if a < 0: a += 360
                if pie_slice[0] > pie_slice[1]:
                    if a < pie_slice[0] and a > pie_slice[1]: continue
                else:
                    if a < pie_slice[0] or a > pie_slice[1]: continue

            if pivot_tracks_m:
                d = math.sqrt(east * east + north * north)
                if any(abs(d - tr) < pivot_track_exclusion_m for tr in pivot_tracks_m): continue

            positions.append((east, north))

        if exp_rows:
            odd = not odd

    # Trim to exact count when the user requested a specific number
    if num_tents is not None and len(positions) > num_tents:
        positions = positions[:num_tents]

    tent_id = 0
    for east, north in positions:
        if pdf:
            pdf.set_draw_color(255, 0, 0)
            pdf.set_fill_color(255, 0, 0)
            pdf.ellipse(east * scale + pdf_width / 2 - 2, -north * scale + pdf_height / 2 - 2, 4, 4, 'DF')

        lon, lat = utmish.to_lonlat(east + easting, north + northing, pivotpoint[0])
        kml.newpoint(name="tent %d" % tent_id, coords=[(lon, lat)])
        w.record(Date=datetime.date.today(), Time="12:00:00pm", Version="7.78.002",
                 Id=pid, Name="Tree_%d" % pid, Latitude=lat, Longitude=lon, Height=761.064,
                 AlarmRad=0, WarningRad=10.0, Status_Text='', Visible=1)
        w.point(lon, lat)
        csvdata.writerow({'GPS Position': '%.7f,%.7f' % (lat, lon), 'Tent': '%d' % tent_id})
        pid += 1
        tent_id += 1

    myzip.writestr("%s/googleearth/%s.kml" % (trimble_path, field_name), kml.kml())
    w.close()
    myzip.writestr('%s/AgGPS/Data/TNTBees/BeeTents/%s/PointFeature.dbf' % (trimble_path, field_name), dbf.getvalue())
    myzip.writestr('%s/AgGPS/Data/TNTBees/BeeTents/%s/PointFeature.shp' % (trimble_path, field_name), shp.getvalue())
    myzip.writestr('%s/AgGPS/Data/TNTBees/BeeTents/%s/PointFeature.shx' % (trimble_path, field_name), shx.getvalue())
    myzip.writestr('%s/spreadsheets/%s.csv' % (trimble_path, field_name), csvbuffer.getvalue().encode('utf-8'))

    # John Deere Operations Center — GeoJSON points
    jd_positions = []
    for east, north in positions:
        lon, lat = utmish.to_lonlat(east + easting, north + northing, pivotpoint[0])
        jd_positions.append((lon, lat))
    myzip.writestr('%s/johndeere/%s.geojson' % (trimble_path, field_name),
                   _make_geojson(jd_positions, field_name))

    return len(positions)



def make_line(myzip, field_path, pivotpoint, lat_offset, angle):
    # create starting point for grid
    easting, northing = utmish.from_lonlat(pivotpoint[0], pivotpoint[1], pivotpoint[0])

    kml = simplekml.Kml()

    shp = BytesIO()
    shx = BytesIO()
    dbf = BytesIO()

    w = shapefile.Writer(shp = shp, shx = shx, dbf = dbf, shapeType=3 )
    w.field("Date", "D")
    w.field("Time", "C", size=10)
    w.field("Version", "C", size=8)
    w.field("Id", "N")
    w.field("Length","N", decimal=3)
    w.field("Dist1","N", decimal=3)
    w.field("Dist2","N", decimal=3)

    rotate = 0 - angle
    rotate = (rotate + 180) % 360 - 180

    # make an AB line 100 metres long
    east1 = lat_offset
    north1 = 0

    east2 = lat_offset
    north2 = 100

    # rotate the AB line about the pivot point
    east1r = east1 * math.cos(math.radians(rotate)) - north1 * math.sin(math.radians(rotate))
    north1r = north1 * math.cos(math.radians(rotate)) + east1 * math.sin(math.radians(rotate))
    east2r = east2 * math.cos(math.radians(rotate)) - north2 * math.sin(math.radians(rotate))
    north2r = north2 * math.cos(math.radians(rotate)) + east2 * math.sin(math.radians(rotate))
    
    # convert grid coords back to lat lon
    ab1 = utmish.to_lonlat(east1r + easting, north1r + northing, pivotpoint[0])
    ab2 = utmish.to_lonlat(east2r + easting, north2r + northing, pivotpoint[0])

    kml.newlinestring(name = "AB Line", coords = [ ab1, ab2])
    w.record(Date=datetime.date.today(), Time="12:00:00pm",Version="7.78.002",Id=0, Length=100, Dist1=0, Dist2=0)
    w.line( [ [ ab1, ab2 ] ])

    myzip.writestr("%s/Swaths.kml" % field_path, kml.kml())    
    w.close()
    myzip.writestr('%s/Swaths.dbf' % field_path, dbf.getvalue())
    myzip.writestr('%s/Swaths.shp' % field_path, shp.getvalue())
    myzip.writestr('%s/Swaths.shx' % field_path, shx.getvalue())

def _point_in_polygon(px, py, polygon):
    """Ray-casting point-in-polygon. polygon is [(x,y), ...]."""
    inside = False
    j = len(polygon) - 1
    for i, (xi, yi) in enumerate(polygon):
        xj, yj = polygon[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _make_geojson(lonlat_list, field_name):
    """Return a GeoJSON FeatureCollection string for John Deere Operations Center."""
    features = [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [lon, lat]},
         "properties": {"id": i + 1, "name": "Shelter_%d" % (i + 1), "type": "bee_shelter"}}
        for i, (lon, lat) in enumerate(lonlat_list)
    ]
    return json.dumps({
        "type": "FeatureCollection",
        "name": field_name,
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": features,
    }, indent=2)


def _circle_lonlat(lat, lon, r_m, n=24):
    """Closed ring of (lon, lat) points approximating a circle of radius r_m."""
    ring = []
    cos_lat = math.cos(math.radians(lat)) or 1e-9
    for i in range(n + 1):                      # +1 closes the ring (first == last)
        b = math.radians(i * 360.0 / n)
        dlat = r_m / 111111.0 * math.cos(b)
        dlon = r_m / (111111.0 * cos_lat) * math.sin(b)
        ring.append([lon + dlon, lat + dlat])
    return ring


def _make_geojson_with_buffers(lonlat_list, field_name, include_buffers=False,
                               buffer_radius_m=1.524):
    """GeoJSON FeatureCollection: shelter points, plus optional buffer-circle
    polygons (one per shelter) for John Deere Operations Center.

    Buffer polygons are tagged as PASSABLE INTERIOR boundaries so Operations
    Center treats them as drive-through interior zones — not impassable
    interiors and not the field's exterior boundary. The field's outer
    boundary is intentionally NOT written here (it's already in Ops Center)."""
    features = []
    for i, (lon, lat) in enumerate(lonlat_list):
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"id": i + 1, "name": "Shelter_%d" % (i + 1), "type": "bee_shelter"},
        })
    if include_buffers and buffer_radius_m and buffer_radius_m > 0:
        for i, (lon, lat) in enumerate(lonlat_list):
            ring = _circle_lonlat(lat, lon, buffer_radius_m, 24)
            features.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [ring]},
                "properties": {
                    "id": i + 1,
                    "name": "Buffer_%d" % (i + 1),
                    # Signal a passable interior boundary across the property
                    # names different importers look for.
                    "type": "interior",
                    "boundaryType": "interior",
                    "boundary_type": "interior",
                    "interior": True,
                    "exterior": False,
                    "passable": True,
                    "drivable": True,
                },
            })
    return json.dumps({
        "type": "FeatureCollection",
        "name": field_name,
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": features,
    }, indent=2)


def export_field_outputs(positions_latlon, pivotpoint, out_dir, field_name,
                         include_buffers=False, buffer_radius_m=1.524):
    """Write the per-field export files from already-computed shelter positions
    (so the output matches exactly what get_tent_positions drew on the map).

    Creates, under out_dir:
      {field}.kml                                              Google Earth points
      Trimble/AgGPS/Data/TNTBees/BeeTents/{field}/             Trimble import set
          PointFeature.shp / .shx / .dbf, origin.kml, *.pos, newField.ok
      {field}.geojson                                          John Deere Ops Center
          (+ buffer-circle polygons when include_buffers is True)

    positions_latlon : [(lat, lon), ...]
    pivotpoint       : (lon, lat)
    """
    writer = FileWriter()

    # ── Google Earth KML (points) ───────────────────────────────────────────
    kml = simplekml.Kml()
    for i, (lat, lon) in enumerate(positions_latlon):
        kml.newpoint(name="Shelter %d" % (i + 1), coords=[(lon, lat)])
    writer.writestr(os.path.join(out_dir, "%s.kml" % field_name), kml.kml())

    # ── Trimble shapefile set: Trimble/AgGPS/Data/TNTBees/BeeTents/{field} ───
    field_dir = os.path.join(out_dir, "Trimble", "AgGPS", "Data", "TNTBees", "BeeTents", field_name)
    make_files(writer, field_dir, field_name, pivotpoint)

    shp = BytesIO(); shx = BytesIO(); dbf = BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=1)
    w.field('Date', 'D'); w.field('Time', 'C', size=10); w.field('Version', 'C', size=8)
    w.field('Id', 'N'); w.field('Name', 'C', size=32)
    w.field('Latitude', 'N', decimal=8); w.field('Longitude', 'N', decimal=8)
    w.field('Height', 'N', decimal=3); w.field('AlarmRad', 'N', decimal=4)
    w.field('WarningRad', 'N', decimal=4); w.field('Status_Text', 'C', size=8); w.field('Visible', 'N')
    pid = 3062
    for lat, lon in positions_latlon:
        w.record(Date=datetime.date.today(), Time="12:00:00pm", Version="7.78.002",
                 Id=pid, Name="Tree_%d" % pid, Latitude=lat, Longitude=lon, Height=761.064,
                 AlarmRad=0, WarningRad=10.0, Status_Text='', Visible=1)
        w.point(lon, lat)
        pid += 1
    w.close()
    writer.writestr(os.path.join(field_dir, "PointFeature.dbf"), dbf.getvalue())
    writer.writestr(os.path.join(field_dir, "PointFeature.shp"), shp.getvalue())
    writer.writestr(os.path.join(field_dir, "PointFeature.shx"), shx.getvalue())

    # ── John Deere Operations Center GeoJSON ─────────────────────────────────
    jd_lonlat = [(lon, lat) for lat, lon in positions_latlon]
    writer.writestr(os.path.join(out_dir, "%s.geojson" % field_name),
                    _make_geojson_with_buffers(jd_lonlat, field_name,
                                               include_buffers, buffer_radius_m))


def latlon_list_to_enu(latlon_list, pivot_lon, pivot_lat):
    """Convert [(lat,lon), ...] to [(east,north), ...] relative to pivot in metres."""
    pe, pn = utmish.from_lonlat(pivot_lon, pivot_lat, pivot_lon)
    result = []
    for lat, lon in latlon_list:
        e, n = utmish.from_lonlat(lon, lat, pivot_lon)
        result.append((e - pe, n - pn))
    return result


def count_tents_only(pivotpoint, radius, width, lat_shift, angle, spacing,
                     pie_slice=None, northlimit=None, eastlimit=None,
                     southlimit=None, westlimit=None, exp_rows=None,
                     exp_rows_start_odd=True, directional_offset=0,
                     boundary_polygon_enu=None, pivot_tracks_m=None,
                     pivot_track_exclusion_m=3.048):
    """Count tent positions without writing any files."""
    radius_sqr = radius * radius
    if northlimit is None: northlimit = radius
    if eastlimit is None:  eastlimit  = radius
    if southlimit is None: southlimit = radius
    if westlimit is None:  westlimit  = radius

    rotate = (0 - angle + 180) % 360 - 180
    count = 0
    tracks = pivot_tracks_m or []

    if exp_rows:
        rows = exp_rows
        odd = exp_rows_start_odd
        use_exp = True
    else:
        rows = range(-int(radius / width), int(radius / width) + 1)
        use_exp = False

    for r in rows:
        if not use_exp:
            odd = r % 2
        for c in range(-int(radius / spacing) - 1, int(radius / spacing) + 1):
            if odd:
                east  = r * width + lat_shift
                north = c * spacing + spacing / 2 + directional_offset
            else:
                east  = r * width + lat_shift
                north = c * spacing + directional_offset
            e = east * math.cos(math.radians(rotate)) - north * math.sin(math.radians(rotate))
            n = north * math.cos(math.radians(rotate)) + east * math.sin(math.radians(rotate))

            if boundary_polygon_enu:
                if not _point_in_polygon(e, n, boundary_polygon_enu): continue
            else:
                if e * e + n * n > radius_sqr: continue
                if eastlimit  and e >  eastlimit:  continue
                if westlimit  and e < -westlimit:  continue
                if northlimit and n >  northlimit: continue
                if southlimit and n < -southlimit: continue

            if pie_slice and (n or e):
                a = math.degrees(math.atan2(e, n))
                if a < 0: a += 360
                if pie_slice[0] > pie_slice[1]:
                    if a < pie_slice[0] and a > pie_slice[1]: continue
                else:
                    if a < pie_slice[0] or a > pie_slice[1]: continue

            if tracks:
                d = math.sqrt(e * e + n * n)
                if any(abs(d - tr) < pivot_track_exclusion_m for tr in tracks): continue

            count += 1
        if use_exp:
            odd = not odd
    return count


def find_exact_spacing(target, pivotpoint, radius, width, lat_shift, angle,
                       pie_slice=None, northlimit=None, eastlimit=None,
                       southlimit=None, westlimit=None, exp_rows=None,
                       exp_rows_start_odd=True, directional_offset=0,
                       boundary_polygon_enu=None, pivot_tracks_m=None,
                       pivot_track_exclusion_m=3.048):
    """Return spacing so the grid places >= target tents (binary search)."""
    kw = dict(pie_slice=pie_slice, northlimit=northlimit, eastlimit=eastlimit,
              southlimit=southlimit, westlimit=westlimit, exp_rows=exp_rows,
              exp_rows_start_odd=exp_rows_start_odd, directional_offset=directional_offset,
              boundary_polygon_enu=boundary_polygon_enu, pivot_tracks_m=pivot_tracks_m,
              pivot_track_exclusion_m=pivot_track_exclusion_m)
    lo, hi = 0.5, radius * 2.0
    if count_tents_only(pivotpoint, radius, width, lat_shift, angle, lo, **kw) < target:
        return lo
    for _ in range(60):
        mid = (lo + hi) / 2
        if count_tents_only(pivotpoint, radius, width, lat_shift, angle, mid, **kw) >= target:
            lo = mid
        else:
            hi = mid
    return lo


def calculate_spacing(radius, width, factor=1, num_tents = None):
    """
        Calculate total length of all the passes from pivot
        point outward, every "width" meters (sprayer passes).
        Divide that by the number of acres in the circle to 
        calculate the distance between tents as a factor of
        the sprayer width.

        factor allows tweaking the spacing, either increasing it, or
        decreasing it
    """
    c = 0
    total = 0
    while c < radius:
        total += math.sqrt(radius*radius-c*c)
        c += width

    total = total * 4 - radius*2
    if num_tents:
        return total / num_tents * factor
    else:
        # otherwise 1 per acre
        return total / (math.pi * radius * radius / 4046.87 + 1) * factor

def get_tent_positions(field_dict, use_metric=True, return_rows=False):
    """
    Compute shelter positions from a field dict, return [(lat, lon), ...] in NW-snake
    numbering order. Returns [] on any error or missing data.

    If return_rows=True, returns ([(lat, lon), ...], [row_idx, ...]) where row_idx
    is the 0-based NW-snake row index for each shelter (so callers can do 2-D
    spatial reasoning, e.g. spreading tray counts evenly across rows).

    When num_structures is set (no user spacing): uses a TRUE RECTANGULAR GRID.
      - Shelter rows at lateral = r * sprayer_width + lat_offset (sprayer pass edges)
      - Global N-S spacing binary-searched to fit closest to num_tents
      - Same N-S coordinates in every row → straight lines from any viewing angle
      - Radial limits from first/last pivot_track; 1 cm safety margin on exclusion
      - Guarantees: pass-edge placement, never mid-pass, never on pivot tracks

    When user spacing is given: rectangular grid pass-through (unchanged).
    When neither: auto-spacing rectangular grid.
    """
    try:
        from collections import defaultdict

        conv = 1.0 if use_metric else 0.3048
        pivotpoint = (float(field_dict['PP_Longitude']), float(field_dict['PP_Latitude']))
        sprayer_width = float(field_dict['Sprayer_width']) * 0.3048  # always in feet → metres

        boundary_polygon = field_dict.get('boundary_polygon') or None
        pivot_tracks = sorted(float(r) for r in (field_dict.get('pivot_tracks') or []))
        excl_m = float(field_dict.get('track_exclusion_ft') or 10) * 0.3048
        # Outside sprayer pass: when "Yes", keep shelters out of the boundary
        # kill-zone (between the 3 m edge band and one sprayer width in) so the
        # sprayer's outside round can't hit them. When "No", shelters may sit
        # anywhere inside the boundary.
        outside_pass = str(field_dict.get('outside_sprayer_pass') or 'No').strip().lower() == 'yes'

        if boundary_polygon:
            boundary_enu = latlon_list_to_enu(boundary_polygon, pivotpoint[0], pivotpoint[1])
            radius = max(math.sqrt(e*e + n*n) for e, n in boundary_enu) * 1.05
        else:
            r_raw = str(field_dict.get('Radius') or '').strip()
            if not r_raw:
                return []
            radius = float(r_raw) * conv
            boundary_enu = None

        seed_angle = float(field_dict.get('Spray_angle') or field_dict.get('Seed_angle') or 0)

        # Bay parameters → tent_row_width and lat_offset.
        # tent_row_width = female_m + male_m is the bay repeat distance.
        # lat_offset places the shelter a FIXED 4 ft (1.2192 m) into the female
        # bay from the male/female boundary — independent of bay size, so the
        # "just east of the male bay" rule holds for any female/male count.
        nf_raw = str(field_dict.get('num_female_rows') or '').strip()
        nm_raw = str(field_dict.get('num_male_rows') or '').strip()
        rs_in_raw = str(field_dict.get('row_spacing_in') or '').strip()
        if nf_raw and nm_raw and rs_in_raw:
            nf_i = int(nf_raw); nm_i = int(nm_raw); rs_m = float(rs_in_raw) * 0.0254
            female_m = (nf_i + 1) * rs_m
            male_m_w = (nm_i + 1) * rs_m
            tent_row_width = female_m + male_m_w
            lat_offset = 1.2192   # 4 ft east of the male/female boundary (sprayer edge)
        elif boundary_enu is not None:
            tent_row_width = sprayer_width
            lat_offset = 0.0
        else:
            tent_row_width = sprayer_width
            lat_offset = float(field_dict.get('Lateral_offset') or 0) * conv

        do_raw = str(field_dict.get('directional_offset') or '').strip()
        directional_offset = float(do_raw) * conv if do_raw else 0.0

        # Shelter count mode: how the exact number of shelters is specified.
        #   "per_acre" → shelters_per_acre × acres
        #   "total"    → num_structures
        #   "spacing"  → spacing between shelters (no exact count)
        # No mode set → legacy behaviour (num_structures, else spacing).
        mode = str(field_dict.get('shelter_mode') or '').strip().lower()
        sp_raw = ''
        num_tents = None
        def _int(v):
            try: return int(round(float(str(v).strip())))
            except (ValueError, TypeError): return None
        if mode == 'spacing':
            sp_raw = str(field_dict.get('spacing') or '').strip()
        elif mode == 'per_acre':
            try:
                spa = float(field_dict.get('shelters_per_acre') or 0)
                ac  = float(field_dict.get('acres') or 0)
                if spa > 0 and ac > 0:
                    num_tents = max(1, int(round(spa * ac)))
            except (ValueError, TypeError):
                num_tents = None
        elif mode == 'total':
            num_tents = _int(field_dict.get('num_structures') or field_dict.get('# of Structures') or '')
        else:
            sp_raw = str(field_dict.get('spacing') or '').strip()
            num_tents = _int(field_dict.get('num_structures') or field_dict.get('# of Structures') or '')
        user_spacing = bool(sp_raw)

        rotate = (0 - seed_angle + 180) % 360 - 180
        rot_r = math.radians(rotate)
        cos_r, sin_r = math.cos(rot_r), math.sin(rot_r)

        easting, northing = utmish.from_lonlat(pivotpoint[0], pivotpoint[1], pivotpoint[0])
        radius_sqr = radius * radius

        # Precompute corner arm exclusion zones in ENU coordinates
        corner_excl = []
        for arm in (field_dict.get('corner_arms') or []):
            if not isinstance(arm, dict): continue
            if arm.get('type') == 'circle':
                try:
                    ce, cn = utmish.from_lonlat(arm['lon'], arm['lat'], pivotpoint[0])
                    corner_excl.append(('circle', ce - easting, cn - northing,
                                        float(arm['radius_m']) ** 2))
                except Exception: pass
            elif arm.get('type') == 'path':
                pts = arm.get('pts') or []
                if len(pts) >= 2:
                    try:
                        enu_pts = []
                        for p in pts:
                            pe, pn = utmish.from_lonlat(p[1], p[0], pivotpoint[0])
                            enu_pts.append((pe - easting, pn - northing))
                        corner_excl.append(('path', enu_pts))
                    except Exception: pass
        excl_m2 = excl_m * excl_m

        def _in_corner_excl(east, north):
            for zone in corner_excl:
                if zone[0] == 'circle':
                    _, ce, cn, cr2 = zone
                    if (east - ce)**2 + (north - cn)**2 < cr2:
                        return True
                elif zone[0] == 'path':
                    for j in range(len(zone[1]) - 1):
                        ax, ay = zone[1][j]; bx, by = zone[1][j + 1]
                        dx, dy = bx - ax, by - ay; seg2 = dx*dx + dy*dy
                        if seg2 > 0:
                            t = max(0.0, min(1.0, ((east-ax)*dx + (north-ay)*dy) / seg2))
                            px, py = ax + t*dx, ay + t*dy
                        else:
                            px, py = ax, ay
                        if (east - px)**2 + (north - py)**2 < excl_m2:
                            return True
            return False

        # =====================================================================
        # TRUE RECTANGULAR GRID (num_tents mode)
        #
        # Shelter rows are at lateral (pre-rotation) = r * sprayer_width + lat_offset
        # — guaranteeing every shelter sits at a sprayer pass edge.
        #
        # A single global N-S spacing is binary-searched so the total count
        # of valid grid points is >= num_tents (closest above). Because every
        # row uses the SAME N-S coordinates, shelters form perfectly straight
        # lines when viewed from any direction.
        # =====================================================================
        if not user_spacing and num_tents:
            if sprayer_width <= 0:
                return ([], []) if return_rows else []

            # Inner limit: no shelter within sprayer_width of the pivot center.
            inner_r2 = sprayer_width * sprayer_width

            # Outer limit: no shelter within sprayer_width of the field boundary.
            # For a polygon field: check min distance from point to boundary edges.
            # For a circular field: enforce d < radius - sprayer_width.
            outer_r_circle = radius - sprayer_width  # used only for circular fields

            def _min_dist_to_bnd(east, north):
                """Min distance from (east, north) to any boundary polygon edge."""
                min_d2 = float('inf')
                n = len(boundary_enu)
                for i in range(n):
                    ax, ay = boundary_enu[i]
                    bx, by = boundary_enu[(i + 1) % n]
                    dx, dy = bx - ax, by - ay
                    seg2 = dx*dx + dy*dy
                    if seg2 > 0:
                        t = max(0.0, min(1.0, ((east-ax)*dx + (north-ay)*dy) / seg2))
                        px, py = ax + t*dx, ay + t*dy
                    else:
                        px, py = ax, ay
                    d2 = (east-px)**2 + (north-py)**2
                    if d2 < min_d2:
                        min_d2 = d2
                return math.sqrt(min_d2)

            r_max = int(radius / sprayer_width) + 2

            # Add 1 cm safety margin to excl_m so floating-point positions that
            # land within rounding error of the track boundary are consistently excluded.
            excl_m_safe = excl_m + 0.01

            # Boundary rule: a shelter is allowed if it is either right against the
            # boundary (within BND_EDGE_DIST) or well inside (>= sprayer_width away).
            # The annulus in between is the outside-sprayer-pass kill zone.
            BND_EDGE_DIST = 3.0
            # Slide-along-bay budget for rescuing shelters that land in a forbidden
            # zone (pivot-track exclusion or sprayer kill zone).
            SNAP_MAX_M = 15.0
            SNAP_STEP_M = 0.25

            def _valid(east, north):
                d_sq = east*east + north*north
                if d_sq < inner_r2: return False
                # Boundary kill-zone only applies when an outside sprayer pass is run.
                if outside_pass:
                    if boundary_enu:
                        d_b = _min_dist_to_bnd(east, north)
                    else:
                        d_b = radius - math.sqrt(d_sq)
                    if BND_EDGE_DIST < d_b < sprayer_width: return False
                if pivot_tracks:
                    d = math.sqrt(d_sq)
                    if any(abs(d - tr) < excl_m_safe for tr in pivot_tracks): return False
                if corner_excl and _in_corner_excl(east, north): return False
                return True

            def _inside(east, north):
                """Is the point inside the field boundary (polygon or circle)?"""
                if boundary_enu:
                    return _point_in_polygon(east, north, boundary_enu)
                return east*east + north*north <= radius_sqr

            def _snap_along_pre_n(pre_e, pre_n_0):
                """Slide along the planting direction (pre_n axis) to find a valid
                spot within SNAP_MAX_M. Prefers positions closer to the outer
                boundary (smaller d_b); tie-breaker is smaller slide distance.
                Returns the new pre_n or None."""
                steps = int(SNAP_MAX_M / SNAP_STEP_M)
                cands = []
                # Walk each direction; take the first valid in each, then pick
                # whichever has the smaller distance-to-boundary.
                for sign in (+1, -1):
                    for i in range(1, steps + 1):
                        delta = i * SNAP_STEP_M
                        new_pre_n = pre_n_0 + sign * delta
                        east  = pre_e * cos_r - new_pre_n * sin_r
                        north = new_pre_n * cos_r + pre_e * sin_r
                        if not _inside(east, north): continue
                        if _valid(east, north):
                            if boundary_enu:
                                d_b = _min_dist_to_bnd(east, north)
                            else:
                                d_b = radius - math.sqrt(east*east + north*north)
                            cands.append((d_b, i, new_pre_n))
                            break
                if not cands:
                    return None
                cands.sort()  # smallest d_b first; ties go to fewer slide steps
                return cands[0][2]

            def _snappable_coarse(pre_e, pre_n_0):
                """Cheap yes/no: is there a valid spot within SNAP_MAX_M along the
                bay? Uses 1 m steps (coarser than final placement) just for
                counting, so the binary search lands on a spacing whose total
                PLACEABLE cell count matches the requested shelter count."""
                steps = int(SNAP_MAX_M)
                for i in range(1, steps + 1):
                    for sign in (+1, -1):
                        new_pre_n = pre_n_0 + sign * i
                        east  = pre_e * cos_r - new_pre_n * sin_r
                        north = new_pre_n * cos_r + pre_e * sin_r
                        if _inside(east, north) and _valid(east, north):
                            return True
                return False

            # Lateral row positions: ONE ROW PER SPRAYER PASS — never skipped, so
            # there are no empty bands in the field. Each row is snapped to the
            # nearest female-bay spot (k*tent_row_width + 4ft) so the shelter sits
            # 4 ft into the female bay, just east of the male bay. The snap shifts
            # a row by at most half a bay (~20 ft for typical bays; up to ~30 ft
            # for big bays) off the pass line — acceptable, and it's exact when
            # sprayer_width == tent_row_width (the Bay Calculator's default).
            row_list = []   # (pre_e, bay_index k) — k also drives the stagger
            _seen_rows = set()
            for r in range(-r_max, r_max + 1):
                edge = r * sprayer_width
                k = round((edge - lat_offset) / tent_row_width)
                pre_e = k * tent_row_width + lat_offset
                key = round(pre_e, 3)
                if key in _seen_rows:   # only skip a true duplicate column
                    continue            # (happens when one bay spans >1 pass)
                _seen_rows.add(key)
                row_list.append((pre_e, k))

            def _count_at_least(n_sp, target):
                # True if at least `target` PLACEABLE cells exist at this spacing
                # (valid as-is, or snappable out of a kill/track zone). Early-exits
                # as soon as the target is reached, so dense spacings stay cheap.
                # Counting placeable cells makes the chosen spacing yield the
                # requested shelter count regardless of the outside-sprayer-pass
                # toggle — that toggle changes which cells are valid, not how many.
                if n_sp <= 0: return False
                c_max = int(radius / n_sp) + 2
                total = 0
                for pre_e, k in row_list:
                    n_stagger = (n_sp / 2) if (k % 2) else 0.0
                    for c in range(-c_max, c_max + 1):
                        pre_n = c * n_sp + directional_offset + n_stagger
                        east  = pre_e * cos_r - pre_n * sin_r
                        north = pre_n * cos_r + pre_e * sin_r
                        if not _inside(east, north): continue
                        if _valid(east, north) or _snappable_coarse(pre_e, pre_n):
                            total += 1
                            if total >= target: return True
                return False

            # Find the largest N-S spacing that still yields >= num_tents positions.
            lo, hi = 1.0, radius * 2.0
            if not _count_at_least(lo, num_tents):
                ns_spacing = lo
            else:
                for _ in range(32):   # 32 halvings ≈ sub-µm spacing precision, plenty
                    mid = (lo + hi) / 2
                    if _count_at_least(mid, num_tents):
                        lo = mid
                    else:
                        hi = mid
                ns_spacing = lo

            # Generate the final grid at the chosen spacing
            raw = []
            c_max = int(radius / ns_spacing) + 2
            for pre_e, k in row_list:
                n_stagger = (ns_spacing / 2) if (k % 2) else 0.0
                for c in range(-c_max, c_max + 1):
                    pre_n = c * ns_spacing + directional_offset + n_stagger
                    east  = pre_e * cos_r - pre_n * sin_r
                    north = pre_n * cos_r + pre_e * sin_r
                    if not _inside(east, north): continue
                    if _valid(east, north):
                        raw.append((east, north, k))
                    else:
                        snapped = _snap_along_pre_n(pre_e, pre_n)
                        if snapped is not None:
                            new_e = pre_e * cos_r - snapped * sin_r
                            new_n = snapped * cos_r + pre_e * sin_r
                            raw.append((new_e, new_n, k))

            if not raw:
                return ([], []) if return_rows else []

            ldx, ldy = cos_r, sin_r
            tdx, tdy = -sin_r, cos_r

            row_groups = defaultdict(list)
            for e, n, r in raw:
                row_groups[r].append((e, n))

            def row_lat_coord(r):
                pts = row_groups[r]
                return sum(e*ldx + n*ldy for e, n in pts) / len(pts)
            sorted_rows = sorted(row_groups.keys(), key=row_lat_coord)

            travel_nw = tdx * (-1) + tdy * 1
            first_row_descending = travel_nw > 0
            ordered = []
            ordered_rows = []
            for i, r in enumerate(sorted_rows):
                pts = sorted(row_groups[r], key=lambda en: en[0]*tdx + en[1]*tdy)
                descending = (i % 2 == 0 and first_row_descending) or \
                             (i % 2 == 1 and not first_row_descending)
                if descending:
                    pts = list(reversed(pts))
                ordered.extend(pts)
                ordered_rows.extend([i] * len(pts))

            # Place EXACTLY num_tents shelters — the count function targets the
            # placeable total, so any small excess is trimmed from the snake tail.
            if num_tents is not None and len(ordered) > num_tents:
                ordered = ordered[:num_tents]
                ordered_rows = ordered_rows[:num_tents]

            result = []
            for e, n in ordered:
                lon, lat = utmish.to_lonlat(e + easting, n + northing, pivotpoint[0])
                result.append((lat, lon))
            if return_rows:
                return result, ordered_rows
            return result

        # =====================================================================
        # RECTANGULAR GRID — used when user gave an explicit spacing, or neither
        # spacing nor num_tents was provided (auto-spacing).
        # =====================================================================
        eff_row_width = tent_row_width
        use_stagger = True

        if sp_raw:
            spacing = float(sp_raw) * conv
        else:
            spacing = calculate_spacing(radius, tent_row_width)

        if spacing <= 0:
            return []

        rows = range(-int(radius / eff_row_width), int(radius / eff_row_width) + 1)
        ldx, ldy = cos_r, sin_r      # lateral direction (between rows)
        tdx, tdy = -sin_r, cos_r    # travel direction (along a row)

        raw = []
        for r in rows:
            odd = r % 2
            for c in range(-int(radius / spacing) - 1, int(radius / spacing) + 1):
                pre_e = r * eff_row_width + lat_offset
                pre_n = (c * spacing + spacing / 2 + directional_offset) if (odd and use_stagger) \
                        else (c * spacing + directional_offset)
                east  = pre_e * cos_r - pre_n * sin_r
                north = pre_n * cos_r + pre_e * sin_r
                if boundary_enu:
                    if not _point_in_polygon(east, north, boundary_enu): continue
                else:
                    if east*east + north*north > radius_sqr: continue
                if pivot_tracks:
                    d = math.sqrt(east*east + north*north)
                    if any(abs(d - tr) < excl_m for tr in pivot_tracks): continue
                if corner_excl and _in_corner_excl(east, north): continue
                raw.append((east, north, r))

        row_groups = defaultdict(list)
        for e, n, r in raw:
            row_groups[r].append((e, n))

        def row_lat_coord(r):
            pts = row_groups[r]
            return sum(e*ldx + n*ldy for e, n in pts) / len(pts)
        sorted_rows = sorted(row_groups.keys(), key=row_lat_coord)

        travel_nw = tdx * (-1) + tdy * 1
        first_row_descending = travel_nw > 0

        ordered = []
        for i, r in enumerate(sorted_rows):
            pts = sorted(row_groups[r], key=lambda en: en[0]*tdx + en[1]*tdy)
            descending = (i % 2 == 0 and first_row_descending) or \
                         (i % 2 == 1 and not first_row_descending)
            if descending:
                pts = list(reversed(pts))
            ordered.extend(pts)

        result = []
        for e, n in ordered:
            lon, lat = utmish.to_lonlat(e + easting, n + northing, pivotpoint[0])
            result.append((lat, lon))
        if return_rows:
            # Rectangular-grid path doesn't track row indices; return zeros so
            # callers always see a parallel list of the same length.
            return result, [0] * len(result)
        return result

    except Exception:
        return ([], []) if return_rows else []


def process_field_data(fields, dirpath, use_metric=True):
    """
    Process a list of field dicts (native Python types) and write output to dirpath.
    Each dict may include the standard CSV keys plus:
      boundary_polygon : [[lat, lon], ...]  — if present, replaces radius+limits
      pivot_tracks     : [radius_m, ...]    — exclusion ring radii in metres
    Returns (output_text, num_fields_ok).
    """
    output = StringIO()
    conv = 1.0 if use_metric else 0.3048

    if not os.path.exists(dirpath):
        os.makedirs(dirpath)

    pdfwriter = BeePDF('P', 'pt', 'Letter')
    writer = FileWriter()
    ok = 0

    with redirect_stdout(output):
        for field in fields:
            try:
                name = str(field['Name']).strip()
                pivotpoint = (float(field['PP_Longitude']), float(field['PP_Latitude']))
                sprayer_width = float(field['Sprayer_width']) * 0.3048

                boundary_polygon = field.get('boundary_polygon') or None
                pivot_tracks = [float(r) for r in (field.get('pivot_tracks') or [])]

                if boundary_polygon:
                    radius = max(
                        math.sqrt((e**2 + n**2))
                        for e, n in latlon_list_to_enu(boundary_polygon, pivotpoint[0], pivotpoint[1])
                    ) * 1.05
                    boundary_enu = latlon_list_to_enu(boundary_polygon, pivotpoint[0], pivotpoint[1])
                else:
                    radius = float(field['Radius']) * conv
                    boundary_enu = None

                # Spray_angle takes priority; fall back to Seed_angle for CSV imports
                seed_angle = float(field.get('Spray_angle') or field.get('Seed_angle') or 0)

                # When a boundary polygon is present, shelters go at wing-tips (lat_offset=0)
                # so the first shelter row is on the pivot centre line.
                # For legacy CSV fields that still carry Lateral_offset, honour it.
                if boundary_enu is not None:
                    lat_offset = 0.0
                else:
                    lat_offset_raw = field.get('Lateral_offset') or ''
                    lat_offset = float(lat_offset_raw) * conv if lat_offset_raw != '' else 0.0
                    fbpw_raw = field.get('Female_bays_per_width') or ''
                    female_bays = float(fbpw_raw) if fbpw_raw != '' else None
                    if female_bays and not lat_offset:
                        lat_offset = sprayer_width / female_bays / 2 * 0.75

                # Limits only used when there is no boundary polygon
                def _lim(key):
                    v = field.get(key) or ''
                    return float(v) * conv if v != '' else radius

                north_limit = radius if boundary_enu else _lim('North_limit')
                east_limit  = radius if boundary_enu else _lim('East_limit')
                south_limit = radius if boundary_enu else _lim('South_limit')
                west_limit  = radius if boundary_enu else _lim('West_limit')

                # Pie slice only used for legacy CSV fields
                pie_slice = None
                if boundary_enu is None:
                    ps = field.get('Pie_start') or ''
                    pe_v = field.get('Pie_end') or ''
                    if ps != '' and pe_v != '':
                        pie_slice = (int(ps), int(pe_v))

                # Experimental rows (legacy CSV only)
                exp_raw = field.get('Experimental') or ''
                exp_rows = [int(x) for x in exp_raw.split(',') if x.strip()] if exp_raw else None
                exp_start_odd = bool(field.get('Experimental_start_odd') or False)

                ns_raw = field.get('# of Structures') or field.get('num_structures') or ''
                num_tents = int(ns_raw) if ns_raw != '' else None
                if num_tents and pie_slice:
                    s, e_ = pie_slice
                    arc = (360 - s + e_) if s > e_ else (e_ - s)
                    num_tents = int(num_tents / (arc / 360))

                sp_raw = field.get('spacing') or ''
                spacing = float(sp_raw) * conv if sp_raw != '' else None

                do_raw = field.get('directional_offset') or ''
                directional_offset = float(do_raw) * conv if do_raw != '' else 0.0

                # Pivot track exclusion in metres (from field dict, default 10 ft)
                excl_ft = float(field.get('track_exclusion_ft') or 10)
                excl_m  = excl_ft * 0.3048

                print("Processing: %s (%.6f, %.6f)" % (name, pivotpoint[0], pivotpoint[1]))
                pdfwriter.add_page()
                make_pdf_circle_bays(pdfwriter, {
                    'Name': name, 'Radius': radius, 'pie_slice': pie_slice,
                    'North_limit': north_limit, 'East_limit': east_limit,
                    'South_limit': south_limit, 'West_limit': west_limit,
                    'Sprayer_width': sprayer_width, 'spacing': spacing,
                    '# of Structures': num_tents, 'Seed_angle': seed_angle,
                    'Lateral_offset': lat_offset, 'directional_offset': directional_offset,
                    'Female_bays_per_width': None,
                })

                field_dir = os.path.join(dirpath, "AgGPS/Data/TNTBees/BeeTents/%s" % name)
                make_files(writer, field_dir, name, pivotpoint)

                count = make_tents(writer, dirpath, name,
                                   pivotpoint=pivotpoint, radius=radius,
                                   width=sprayer_width, lat_shift=lat_offset,
                                   angle=seed_angle, pie_slice=pie_slice,
                                   northlimit=north_limit, eastlimit=east_limit,
                                   southlimit=south_limit, westlimit=west_limit,
                                   exp_rows=exp_rows, exp_rows_start_odd=exp_start_odd,
                                   pdf=pdfwriter, num_tents=num_tents, spacing=spacing,
                                   directional_offset=directional_offset,
                                   boundary_polygon_enu=boundary_enu,
                                   pivot_tracks_m=pivot_tracks,
                                   pivot_track_exclusion_m=excl_m)
                print("  → %d shelters placed" % count)

                make_line(writer, field_dir, pivotpoint, lat_offset, seed_angle)
                ok += 1
            except Exception:
                print("ERROR processing %s:\n%s" % (field.get('Name', '?'), traceback.format_exc()))

        pdfwriter.output(os.path.join(dirpath, 'TNTFields.pdf'), 'F')
        print("\nDone. %d/%d fields processed." % (ok, len(fields)))

    return output.getvalue(), ok


def process_csvfile(csv_file, path = None, use_zip = None, timestamp = None, use_metric = False):

    output = StringIO()

    if use_metric:
        conv = 1

    else:
        conv = 0.3048

    with redirect_stdout(output):
        fields = []
        with open(csv_file, 'r') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                # perform some type conversions, and validate some fields
                # and calculate convenience entries
                try:
                    row['PP_Longitude'] = float(row['PP_Longitude'])
                    row['PP_Latitude'] = float(row['PP_Latitude'])
                    row['Radius'] = float(row['Radius']) * conv
                    if row['Seed_angle'] == '':
                        print ("Warning! No seed angle supplied for %s. Assuming 0 degrees." % row['Name'])
                        row['Seed_angle'] = 0
                    else:
                        row['Seed_angle'] = float(row['Seed_angle'])
                    row['Sprayer_width'] = float(row['Sprayer_width']) * 0.3048
                    if row['Pie_start'] != '' and row['Pie_end'] != '':
                        row['pie_slice'] = (int(row['Pie_start']),
                                            int(row['Pie_end']))
                    else:
                        row['pie_slice'] = None

                    if row['North_limit'] != '':
                        row['North_limit'] = float(row['North_limit']) * conv
                    else:
                        row['North_limit'] = row['Radius']

                    if row['East_limit'] != '':
                        row['East_limit'] = float(row['East_limit']) * conv
                    else:
                        row['East_limit'] = row['Radius']

                    if row['South_limit'] != '':
                        row['South_limit'] = float(row['South_limit']) * conv
                    else:
                        row['South_limit'] = row['Radius']

                    if row['West_limit'] != '':
                        row['West_limit'] = float(row['West_limit']) * conv
                    else:
                        row['West_limit'] = row['Radius']

                    if row['Lateral_offset'] != '':
                        row['Lateral_offset'] = float(row['Lateral_offset']) * conv
                    else:
                        row['Lateral_offset'] = 0

                    if row['Female_bays_per_width'] != '':
                        row['Female_bays_per_width'] = float(row['Female_bays_per_width'])

                        if not row['Lateral_offset']:
                            # calculate it based on the width of the bays. This figures
                            # out where the tent is placed, just to the right-hand side
                            # of the male bay
                            row['Lateral_offset'] = row['Sprayer_width'] / float(row['Female_bays_per_width']) / 2 * 0.75
                    else:
                        row['Female_bays_per_width'] = None

                    if row['Experimental'] != '':
                        exp_rows = row['Experimental'].split(',')
                        exp_rows = [int(x) for x in exp_rows]
                        row['Experimental'] = exp_rows
                    else:
                        row['Experimental'] = None

                    if row['Experimental_start_odd'] != '':
                        row['Experimental_start_odd'] = True
                    else:
                        row['Experimental_start_odd'] = False

                    if row['# of Structures'] != '':
                        row['# of Structures'] = int(row['# of Structures'])

                        if row['pie_slice']:
                            # if we have a pie slice, extrapolate how many
                            # tents would cover the entire circle

                            start = row['pie_slice'][0]
                            end = row['pie_slice'][1]

                            if start > end:
                                arc_length = (360 - start) + end
                            else:
                                arc_length = end - start

                            row['# of Structures'] /= arc_length/360

                    else:
                        row['# of Structures'] = None

                    if not 'spacing' in row:
                        row['spacing'] = None
                    else:
                        if not row['spacing']:
                            row['spacing'] = None
                        else:
                            row['spacing'] = float(row['spacing']) * conv

                    if not 'directional_offset' in row:
                        row['directional_offset'] = 0
                    else:
                        row['directional_offset'] = float(row['directional_offset']) * conv

                    fields.append(row)
                except ValueError as e:
                    print ("Warning! %s has incomplete information. It will not be processed." % row['Name'])
                    print (traceback.format_exc())



        #for item in field_data:
        #    fields.append(dict(zip(data_items, item)))

        #zipfilename = '/tmp/beetents ' + datetime.date.today().isoformat() + ".zip"

        if not path:
            if not use_zip:
                # not using zip, place files in the same directory as the CSV file
                dirpath = os.path.join(os.path.dirname(os.path.abspath(csv_file)), "TNT")
            else:
                dirpath = "TNT"
        else:
            dirpath = path
            

        if use_zip:
            zipfilename = use_zip
            if timestamp:
                zipname_parts = os.path.splitext(zipfilename)
                zipfilename = "%s-%s%s" % (zipname_parts[0],datetime.date.today().isoformat(), zipname_parts[1])
                
            print ("Writing output to %s." % zipfilename)
            writer = zipfile.ZipFile(zipfilename, mode='w')
        else:
            if timestamp:
                dirpath = dirpath+"-"+datetime.date.today().isoformat()

            print ()
            if (use_metric):
                print ("Using METRIC units for distance (metres).")
            else:
                print ("Using IMPERIAL units for distance (feet).")

            print ("Writing output to folder %s." % dirpath)
            print ()
            writer = FileWriter()


        width, height = 612,792 #72 dpi

        if use_zip:
            pdfpath = os.path.dirname(os.path.abspath(use_zip))
        else:
            pdfpath = dirpath
        if not os.path.exists(pdfpath):
            os.makedirs(pdfpath)
            
        pdfwriter = BeePDF('P','pt','Letter')
        #surface = cairo.PDFSurface (os.path.join(pdfpath, "tntfields.pdf"), width, height)

        with writer:
            for field in fields:
                print ("Processing: %s (%f, %f)" % (field['Name'].strip(), field['PP_Longitude'], field['PP_Latitude']))
                pdfwriter.add_page()
                make_pdf_circle_bays(pdfwriter, field)

                make_files(writer, os.path.join(dirpath, "AgGPS/Data/TNTBees/BeeTents/%s" % field['Name'].strip()), 
                                   field['Name'].strip(), 
                                   (float(field['PP_Longitude']), float(field['PP_Latitude'])))

                #print (field['# of Structures'])
                make_tents(writer, dirpath, 
                                  field['Name'].strip(),
                                  pivotpoint=(field['PP_Longitude'], field['PP_Latitude']), 
                                  radius=field['Radius'], width=field['Sprayer_width'],  
                                  lat_shift=field['Lateral_offset'], angle=field['Seed_angle'], 
                                  pie_slice=field['pie_slice'],
                                  northlimit=field['North_limit'],
                                  eastlimit=field['East_limit'],
                                  southlimit=field['South_limit'],
                                  westlimit=field['West_limit'],
                                  exp_rows = field['Experimental'],
                                  exp_rows_start_odd = field['Experimental_start_odd'],
                                  pdf=pdfwriter,
                                  num_tents = field['# of Structures'],
                                  spacing = field['spacing'],
                                  directional_offset = field['directional_offset'],

                                  ) 

                make_line(writer, os.path.join(dirpath, "AgGPS/Data/TNTBees/BeeTents/%s" % field['Name'].strip()), 
                                 (field['PP_Longitude'], field['PP_Latitude']), 
                                 field['Lateral_offset'], field['Seed_angle'])

            if not use_zip:
                pdfwriter.output(os.path.join(dirpath,'TNTFields.pdf'),'F')
            else:
                writer.writestr(os.path.join(dirpath,'TNTFields.pdf'),pdfwriter.output(dest='S'))

    return output

if __name__== "__main__":

    """
    CSV should have the following fields:
    Name,PP_Longitude,PP_Latitude,Radius,Seed_angle,Lateral_offset,Sprayer_width,Pie_start,Pie_end,North_limit,East_limit,South_limit,West_limit,Female_bays_per_width,Experimental,Experimental_start_odd,# of Structures,spacing
    """
    
    try:
        import tkinter as tk
        import tkinter.font as tkFont
        import tkinter.filedialog

        use_tk = True
    except ModuleNotFoundError:
        use_tk = False

    if use_tk:
        try:
            class BeeTentGui:
                def __init__(self, root):
                    #setting title
                    root.title("Bee Tent Maps")
                    #setting window size

                    #frame = tk.Frame(root)
                    #frame.pack(fill = tk.BOTH, expand = 1,pad = 5)

                    frame = tk.Frame(root)
                    frame.pack(fill=tk.X)

                    GLabel_429=tk.Label(frame)
                    ft = tkFont.Font(size=12)
                    GLabel_429["font"] = ft
                    GLabel_429["fg"] = "#000000"
                    GLabel_429["justify"] = "center"
                    GLabel_429["text"] = "Input file"
                    GLabel_429.pack(side = tk.LEFT)

                    self.fileinput=tk.Entry(frame)
                    self.fileinput["borderwidth"] = "1"
                    ft = tkFont.Font(size=12)
                    self.fileinput["font"] = ft
                    self.fileinput["fg"] = "#000000"
                    self.fileinput["bg"] = "#ffffff"
                    self.fileinput["justify"] = "left"
                    self.fileinput["text"] = "Entry"
                    self.fileinput.pack(fill = tk.X, expand=True, side = tk.LEFT)

                    choosefile=tk.Button(frame)
                    choosefile["bg"] = "#e9e9ed"
                    ft = tkFont.Font(size=12)
                    choosefile["font"] = ft
                    choosefile["fg"] = "#000000"
                    choosefile["justify"] = "center"
                    choosefile["text"] = "..."
                    choosefile.pack(side = tk.RIGHT)
                    choosefile["command"] = self.choosefile_command

                    frame1 = tk.Frame(root)
                    frame1.pack(fill=tk.X)

                    self.use_metric = True
                    metriccheck = tk.Checkbutton(frame1)
                    metriccheck["text"] = "Use Metric for pivot radius and field boundaries"
                    metriccheck.pack()
                    metriccheck.select()
                    metriccheck["command"] = self.on_metric_toggle

                    processbutton=tk.Button(frame1)
                    processbutton["bg"] = "#e9e9ed"
                    ft = tkFont.Font(size=12)
                    processbutton["font"] = ft
                    processbutton["fg"] = "#000000"
                    processbutton["justify"] = "center"
                    processbutton["text"] = "Process"
                    processbutton.pack()
                    processbutton["command"] = self.process_command

                    tf = tk.Frame(root)
                    tf.pack(fill=tk.BOTH, expand = 1)

                    self.outputtext=tk.Text(tf)
                    self.outputtext["borderwidth"] = "2"
                    ft = tkFont.Font(size=10)
                    self.outputtext["font"] = ft
                    self.outputtext["fg"] = "#000000"
                    self.outputtext["bg"] = "#ffffff"
                    self.outputtext.pack(side = tk.LEFT, fill=tk.BOTH, expand=1)
                    self.outputtext.config(state = tk.DISABLED)

                    scrollbar = tk.Scrollbar(tf)
                    scrollbar.pack(side = tk.RIGHT, fill=tk.Y)
                    scrollbar.config(command = self.outputtext.yview)

                    self.outputtext.config(yscrollcommand = scrollbar.set)


                def choosefile_command(self):
                    file = tk.filedialog.askopenfilename()
                    self.fileinput.delete(0,tk.END)
                    self.fileinput.insert(0,file)

                def on_metric_toggle(self):
                    self.use_metric = not self.use_metric

                def process_command(self):
                    filename = self.fileinput.get()

                    output = process_csvfile(filename, None, None, None, self.use_metric).getvalue()

                    self.outputtext.config(state = tk.NORMAL)
                    self.outputtext.delete("1.0",tk.END)
                    self.outputtext.insert("end", output)
                    self.outputtext.config(state = tk.DISABLED)

            root = tk.Tk()
            beetentgui = BeeTentGui(root)
        except tk._tkinter.TclError:
            use_tk = False

    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('-z','--zip', type=str, help="create a zip file containing all generated files. Default is to write them to a directory.")
    parser.add_argument('-p','--path', type=str, help="write file tree relative to this path. If you specified a zip file, this path will must be relative and will be inside the zip file. If not specified, will write to a folder called TNT in the same folder as the csv file.")
    parser.add_argument('-t', '--timestamp', help="Add timestamp to zip filename or if not using zip, to the path specified.", action="store_true")
    parser.add_argument('-m', '--metric', help="use metres as the length unit", action="store_true")
    parser.add_argument('csv_file', type=str, nargs = "?", help="CSV file to read field information from. If provided, will process automatically, otherwise can pick from the graphical user interface.")

    args = parser.parse_args()

    if args.csv_file:
        csv_filename = args.csv_file
        # place in GUI
        if use_tk:
            beetentgui.fileinput.delete(0,tk.END)
            beetentgui.fileinput.insert(0,csv_filename)

        #context mananger
            
        output = process_csvfile(csv_filename,args.path, args.zip, args.timestamp, args.metric).getvalue()

        if not use_tk:
            print (output)
            print ("Press enter to close this window.")
            input()
            sys.exit(0)
        else:
            beetentgui.outputtext.config(state = tk.NORMAL)
            beetentgui.outputtext.insert("end", output)
            beetentgui.outputtext.config(state = tk.DISABLED)

    else:
        if not use_tk:
            print ("No Tk GUI is available.  Please provide a csv_file argument.")
            parser.print_help()
            sys.exit(1)

    if use_tk:
        root.mainloop()
