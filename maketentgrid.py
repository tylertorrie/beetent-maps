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
                         include_buffers=False, buffer_radius_m=1.524,
                         outer_boundary=None,
                         write_agps=True, write_jd=True, write_kml=True,
                         write_geojson=True, write_boundary=True):
    """Write the per-field export files from already-computed shelter positions
    (so the output matches exactly what get_tent_positions drew on the map).

    Creates, under out_dir (each section only written when its write_* flag is True):
      Shelter Pins KML/{field}_Shelter_Pins.kml          Google Earth points
      AgGPS/Data/TNTBees/BeeTents/{field}/                Trimble import set
      GeoJSON Files/{field}.geojson                       loose GeoJSON
      John Deere/{field}_Shelter_Pins_shp.zip             JD Ops Center → Flags
      John Deere/{field}_Shelter_Buffer_Zones_shp.zip     JD → Internal Boundaries
      Boundary Files/kml files/{field}_Boundary.kml       field boundary KML
      Boundary Files/shp files/{field}_Boundary_shp.zip   field boundary shapefile

    positions_latlon : [(lat, lon), ...]
    pivotpoint       : (lon, lat)
    """
    writer = FileWriter()

    # ── Sub-folder roots ────────────────────────────────────────────────────
    kml_dir     = os.path.join(out_dir, "Shelter Pins KML")
    jd_dir      = os.path.join(out_dir, "John Deere")
    geojson_dir = os.path.join(out_dir, "GeoJSON Files")
    bnd_shp_dir = os.path.join(out_dir, "Boundary Files", "shp files")
    bnd_kml_dir = os.path.join(out_dir, "Boundary Files", "kml files")

    # WGS84 PRJ string — reused by JD and Boundary shapefile zips.
    WGS84_PRJ = ('GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
                 'SPHEROID["WGS_1984",6378137,298.257223563]],'
                 'PRIMEM["Greenwich",0],UNIT["Degree",0.017453292519943295]]')

    # ── Google Earth KML (points) ───────────────────────────────────────────
    if write_kml:
        kml = simplekml.Kml()
        for i, (lat, lon) in enumerate(positions_latlon):
            kml.newpoint(name="Shelter %d" % (i + 1), coords=[(lon, lat)])
        writer.writestr(os.path.join(kml_dir, "%s_Shelter_Pins.kml" % field_name), kml.kml())

    # ── Trimble shapefile set: AgGPS/Data/TNTBees/BeeTents/{field} ──────────
    # Trimble AgGPS uses a Client/Farm/Field hierarchy: Client = "TNTBees",
    # Farm = "BeeTents", Field = the field name. The previous export wrote the
    # field directly under the client (one level too shallow), which the
    # monitor would list but refuse to import. Each field folder also needs the
    # Swaths (AB-line) files alongside PointFeature for a valid import.
    if write_agps:
        field_dir = os.path.join(out_dir, "AgGPS", "Data", "TNTBees", "BeeTents", field_name)
        make_files(writer, field_dir, field_name, pivotpoint)
        make_line(writer, field_dir, pivotpoint, 0, 0)   # north AB line from pivot

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

    # ── GeoJSON ─────────────────────────────────────────────────────────────
    if write_geojson:
        jd_lonlat = [(lon, lat) for lat, lon in positions_latlon]
        jd_geojson_text = _make_geojson_with_buffers(jd_lonlat, field_name,
                                                      include_buffers, buffer_radius_m)
        writer.writestr(os.path.join(geojson_dir, "%s.geojson" % field_name),
                        jd_geojson_text)

    # ── John Deere Operations Center exports ────────────────────────────────
    if write_jd:
        def _shelter_pins_shapefile_bytes():
            """Point shapefile of shelter markers. JD reads this as Flags."""
            shp = BytesIO(); shx = BytesIO(); dbf = BytesIO()
            w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POINT)
            w.field('id',   'N', size=8,  decimal=0)
            w.field('name', 'C', size=32)
            w.field('type', 'C', size=16)
            for i, (lat, lon) in enumerate(positions_latlon):
                w.point(lon, lat)
                w.record(i + 1, "Shelter_%d" % (i + 1), "flag")
            w.close()
            return shp.getvalue(), shx.getvalue(), dbf.getvalue()

        def _buffer_zones_shapefile_bytes(radius_m):
            """Polygon shapefile of shelter buffer circles. JD reads this as
            Internal Boundaries (passable interior boundaries)."""
            shp = BytesIO(); shx = BytesIO(); dbf = BytesIO()
            w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON)
            w.field('id',   'N', size=8,  decimal=0)
            w.field('name', 'C', size=32)
            w.field('type', 'C', size=16)
            for i, (lat, lon) in enumerate(positions_latlon):
                ring = _circle_lonlat(lat, lon, radius_m, 24)
                w.poly([ring])
                w.record(i + 1, "Buffer_%d" % (i + 1), "interior")
            w.close()
            return shp.getvalue(), shx.getvalue(), dbf.getvalue()

        # {field}_Shelter_Pins_shp.zip  (upload to JD → Flags)
        shp, shx, dbf = _shelter_pins_shapefile_bytes()
        pins_zip_buf = BytesIO()
        with zipfile.ZipFile(pins_zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            base = "%s_Shelter_Pins" % field_name
            zf.writestr("%s.shp" % base, shp)
            zf.writestr("%s.shx" % base, shx)
            zf.writestr("%s.dbf" % base, dbf)
            zf.writestr("%s.prj" % base, WGS84_PRJ)
            zf.writestr("README.txt",
                "Beetent Maps — Shelter Pins for John Deere Operations Center\n"
                "\n"
                "This .zip is the shelter-pin layer for the field \"%s\".\n"
                "Contains a point shapefile (one feature per shelter).\n"
                "\n"
                "To upload in John Deere Operations Center:\n"
                "  Files (cloud icon) → Upload Files → Flags → drop this .zip.\n"
                "\n"
                "If Flags isn't available in your JD plan, the \"Other\"\n"
                "category accepts it too and the points stay viewable on the\n"
                "field map.\n"
                % field_name)
        writer.writestr(os.path.join(jd_dir, "%s_Shelter_Pins_shp.zip" % field_name),
                        pins_zip_buf.getvalue())

        # {field}_Shelter_Buffer_Zones_shp.zip  (upload as Internal Boundaries)
        if include_buffers and buffer_radius_m and buffer_radius_m > 0:
            shp, shx, dbf = _buffer_zones_shapefile_bytes(buffer_radius_m)
            bz_zip_buf = BytesIO()
            with zipfile.ZipFile(bz_zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                base = "%s_Shelter_Buffer_Zones" % field_name
                zf.writestr("%s.shp" % base, shp)
                zf.writestr("%s.shx" % base, shx)
                zf.writestr("%s.dbf" % base, dbf)
                zf.writestr("%s.prj" % base, WGS84_PRJ)
                zf.writestr("README.txt",
                    "Beetent Maps — Shelter Buffer Zones for John Deere Operations Center\n"
                    "\n"
                    "This .zip is the shelter-buffer layer for the field \"%s\".\n"
                    "Contains a polygon shapefile (one circle per shelter) marking\n"
                    "the passable interior zones the sprayer / planter should\n"
                    "drive around.\n"
                    "\n"
                    "To upload in John Deere Operations Center:\n"
                    "  Files → Upload Files → Internal Boundaries → drop this .zip.\n"
                    "\n"
                    "The buffer zones import as interior (passable) boundaries.\n"
                    % field_name)
            writer.writestr(os.path.join(jd_dir, "%s_Shelter_Buffer_Zones_shp.zip" % field_name),
                            bz_zip_buf.getvalue())

    # ── Boundary Files ──────────────────────────────────────────────────────
    if write_boundary and outer_boundary and len(outer_boundary) >= 3:
        # KML — polygon outline of the field boundary
        bnd_kml = simplekml.Kml()
        pol = bnd_kml.newpolygon(name="%s Boundary" % field_name)
        pol.outerboundaryis = [(lon, lat) for lat, lon in outer_boundary]
        writer.writestr(os.path.join(bnd_kml_dir, "%s_Boundary.kml" % field_name),
                        bnd_kml.kml())

        # Shapefile zip — single polygon feature
        bshp = BytesIO(); bshx = BytesIO(); bdbf = BytesIO()
        bw = shapefile.Writer(shp=bshp, shx=bshx, dbf=bdbf,
                              shapeType=shapefile.POLYGON)
        bw.field('id',   'N', size=8,  decimal=0)
        bw.field('name', 'C', size=64)
        ring = [(lon, lat) for lat, lon in outer_boundary]
        if ring[0] != ring[-1]:
            ring.append(ring[0])   # ensure closed ring
        bw.poly([ring])
        bw.record(1, "%s Boundary" % field_name)
        bw.close()
        bnd_zip_buf = BytesIO()
        with zipfile.ZipFile(bnd_zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            base = "%s_Boundary" % field_name
            zf.writestr("%s.shp" % base, bshp.getvalue())
            zf.writestr("%s.shx" % base, bshx.getvalue())
            zf.writestr("%s.dbf" % base, bdbf.getvalue())
            zf.writestr("%s.prj" % base, WGS84_PRJ)
        writer.writestr(os.path.join(bnd_shp_dir, "%s_Boundary_shp.zip" % field_name),
                        bnd_zip_buf.getvalue())


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

def parse_jd_seeding_shapefile(shp_path):
    """Parse a John Deere Operations Center "Seeding" shapefile set into a list
    of planter passes. Each pass is one boustrophedon stretch — a polyline of
    tractor-center (lat, lon) points in chronological order.

    Expected attributes on each record (case-sensitive): IsoTime, Heading,
    optionally SECTIONID. Multiple records typically share an IsoTime (one
    per active section); we average their points to get the tractor center.

    Pass split rule: consecutive ground samples whose heading differs by more
    than ~90° are treated as a new pass (handles end-of-row turnaround).

    Returns: [[(lat, lon), ...], ...]   — one polyline per pass, or [] on error.
    """
    base = str(shp_path)
    if base.lower().endswith('.shp'):
        base = base[:-4]
    try:
        r = shapefile.Reader(base)
    except Exception:
        return []
    fields = [f[0] for f in r.fields[1:]]
    try:
        iso_i = fields.index('IsoTime')
    except ValueError:
        return []
    try:
        hdg_i = fields.index('Heading')
    except ValueError:
        hdg_i = None

    # Group section points by IsoTime → tractor center per ground sample.
    samples = {}   # iso -> {'pts': [(lat, lon), ...], 'hdg': float|None}
    for i, rec in enumerate(r.iterRecords()):
        try:
            shp = r.shape(i)
        except Exception:
            continue
        if not shp.points:
            continue
        lon, lat = shp.points[0]
        iso = rec[iso_i]
        hdg = rec[hdg_i] if hdg_i is not None else None
        d = samples.setdefault(iso, {'pts': [], 'hdg': hdg})
        d['pts'].append((lat, lon))

    if not samples:
        return []

    # Sort by IsoTime (ISO-8601 sorts correctly as a string).
    centers = []
    for iso in sorted(samples.keys()):
        d = samples[iso]
        n = len(d['pts'])
        if n == 0: continue
        lat = sum(p[0] for p in d['pts']) / n
        lon = sum(p[1] for p in d['pts']) / n
        centers.append((lat, lon, d['hdg']))

    # Split into passes on a ≥90° heading change between consecutive samples.
    # Tracks the END-OF-ROW TURNAROUND that flips the planter direction.
    passes = []
    cur = []
    last_hdg = None
    for lat, lon, hdg in centers:
        if last_hdg is not None and hdg is not None:
            # Absolute heading delta, normalised to [0, 180].
            delta = abs(((hdg - last_hdg + 180) % 360) - 180)
            if delta > 90:
                if len(cur) >= 2:
                    passes.append(cur)
                cur = []
        cur.append((lat, lon))
        if hdg is not None:
            last_hdg = hdg
    if len(cur) >= 2:
        passes.append(cur)
    return passes


def parse_sprayer_shapefile(shp_path):
    """Parse a sprayer-pass file into a list of GPS polylines.

    Accepts:
      • JD Operations Center seeding shapefile  (.shp + sidecar files)
      • Generic polyline shapefile  (.shp)  — any attribute schema
      • GeoJSON  (.geojson or .json)  — LineString / MultiLineString features

    Returns [[(lat, lon), ...], ...]  — one polyline per pass, or [] on error.
    """
    path = str(shp_path)

    # ── GeoJSON ──────────────────────────────────────────────────────────────
    if path.lower().endswith(('.geojson', '.json')):
        try:
            import json as _json
            with open(path, encoding='utf-8') as fh:
                gj = _json.load(fh)
            passes = []
            features = gj.get('features', []) if isinstance(gj, dict) else []
            for feat in features:
                geom = feat.get('geometry') or {}
                gtype = geom.get('type', '')
                coords = geom.get('coordinates', [])
                lines = [coords] if gtype == 'LineString' else (
                         coords if gtype == 'MultiLineString' else [])
                for line in lines:
                    pts = [(float(c[1]), float(c[0])) for c in line if len(c) >= 2]
                    if len(pts) >= 2:
                        passes.append(pts)
            return passes
        except Exception:
            return []

    # ── Shapefile — try JD seeding format first (handles heading-based splits)
    seeding = parse_jd_seeding_shapefile(shp_path)
    if seeding:
        return seeding

    # ── Generic polyline shapefile fallback ───────────────────────────────────
    base = path[:-4] if path.lower().endswith('.shp') else path
    try:
        r = shapefile.Reader(base)
    except Exception:
        return []
    passes = []
    for i in range(len(r.shapes())):
        try:
            shp = r.shape(i)
        except Exception:
            continue
        if not shp.points:
            continue
        # Handle multi-part shapes (shp.parts is a list of start indices).
        parts = list(shp.parts) if shp.parts else [0]
        parts.append(len(shp.points))
        for j in range(len(parts) - 1):
            segment = shp.points[parts[j]:parts[j + 1]]
            pts = [(float(pt[1]), float(pt[0])) for pt in segment if len(pt) >= 2]
            if len(pts) >= 2:
                passes.append(pts)
    return passes


def resolve_row_mask(nf, nm, layout, custom, total_rows=None):
    """Build the M/F-per-row mask string for the WHOLE planter.

    nf/nm describe one REPEAT UNIT. When total_rows > nf+nm the unit
    pattern repeats to fill the planter (e.g. 8F+2M centered = 'FFFFMMFFFF'
    repeated twice = 'FFFFMMFFFFFFFFMMFFFF' on a 20-row planter).

    layout: "outer" | "centered" | "custom".
    For custom, the user-supplied string is used as-is and must already be
    length total_rows; invalid input falls back to centered.

    Returns a string of length total_rows (or nf+nm if total_rows missing).
    Mirrors the GUI helper of the same name in beetent_app.py so
    get_tent_positions can resolve it without touching the form."""
    nf = int(nf); nm = int(nm)
    unit = max(0, nf + nm)
    if unit == 0: return ""
    try: target = int(total_rows) if total_rows else unit
    except (ValueError, TypeError): target = unit
    if target <= 0: target = unit
    if layout == 'custom':
        s = "".join(c for c in (custom or "").upper() if c in "MF")
        if len(s) == target:
            return s
        layout = 'centered'
    if layout == 'outer':
        left = nm // 2
        right = nm - left
        unit_mask = "M" * left + "F" * nf + "M" * right
    else:  # centered
        left_f = nf // 2
        right_f = nf - left_f
        unit_mask = "F" * left_f + "M" * nm + "F" * right_f
    if target == unit:
        return unit_mask
    copies = (target + unit - 1) // unit
    return (unit_mask * copies)[:target]


def _haversine_m(lat1, lon1, lat2, lon2):
    R = 6378137.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon/2)**2)
    return 2 * R * math.asin(math.sqrt(a))


def _polyline_cumlen_m(poly):
    """Cumulative arc length in metres for a [(lat,lon), ...] polyline."""
    cl = [0.0]
    for i in range(len(poly) - 1):
        d = _haversine_m(poly[i][0], poly[i][1], poly[i+1][0], poly[i+1][1])
        cl.append(cl[-1] + d)
    return cl


def _interp_at_arclen(poly, cl, target_m):
    """Return the (lat, lon) point along `poly` at cumulative arc length
    `target_m` metres (clamped to the polyline's range)."""
    if not poly: return (0.0, 0.0)
    if target_m <= cl[0]: return poly[0]
    if target_m >= cl[-1]: return poly[-1]
    # Linear scan — fine for typical polylines (≤ a few thousand vertices).
    for i in range(len(cl) - 1):
        if cl[i+1] >= target_m:
            seg = cl[i+1] - cl[i]
            t = 0.0 if seg <= 0 else (target_m - cl[i]) / seg
            lat = poly[i][0] + t * (poly[i+1][0] - poly[i][0])
            lon = poly[i][1] + t * (poly[i+1][1] - poly[i][1])
            return (lat, lon)
    return poly[-1]



def mask_runs(mask, char):
    """Return [(start, end_exclusive), ...] for contiguous runs of `char`
    in `mask`. Works for any mask the row-layout resolver can produce."""
    runs = []
    start = None
    for i, c in enumerate(mask):
        if c == char:
            if start is None: start = i
        else:
            if start is not None:
                runs.append((start, i)); start = None
    if start is not None:
        runs.append((start, len(mask)))
    return runs


def _offset_polyline_latlon(pts, signed_offset_m):
    """Return a polyline offset perpendicular to `pts` by `signed_offset_m`.
    Positive offset = "right" side relative to travel direction
    (perpendicular rotated +90°). At interior vertices uses the unit-
    bisector of adjacent perpendiculars; at endpoints uses the single
    segment's perpendicular. ENU is anchored at the polyline's first
    point — small field scale, no need for a global projection."""
    if not pts or len(pts) < 2 or signed_offset_m == 0:
        return [tuple(p) for p in pts]
    lat0, lon0 = pts[0]
    cos_lat = math.cos(math.radians(lat0)) or 1e-9
    R = 6378137.0
    M_PER_DEG_LAT = R * math.pi / 180.0
    M_PER_DEG_LON = M_PER_DEG_LAT * cos_lat
    # Convert to local ENU
    enu = [((lon - lon0) * M_PER_DEG_LON, (lat - lat0) * M_PER_DEG_LAT)
           for lat, lon in pts]
    n_pts = len(enu)
    perp = []
    for i in range(n_pts):
        in_d = None
        if i > 0:
            dx = enu[i][0] - enu[i-1][0]; dy = enu[i][1] - enu[i-1][1]
            L = math.sqrt(dx*dx + dy*dy)
            if L > 0: in_d = (dx/L, dy/L)
        out_d = None
        if i < n_pts - 1:
            dx = enu[i+1][0] - enu[i][0]; dy = enu[i+1][1] - enu[i][1]
            L = math.sqrt(dx*dx + dy*dy)
            if L > 0: out_d = (dx/L, dy/L)
        # perp(d) = (-d.y, d.x) — 90° CCW = "left" when travelling forward.
        # We use the unit-bisector of the two perpendiculars at interior
        # vertices. Signed offset > 0 means right (perpendicular flipped).
        if in_d and out_d:
            px = (-in_d[1] - out_d[1]) * 0.5
            py = ( in_d[0] + out_d[0]) * 0.5
        elif in_d:
            px, py = -in_d[1], in_d[0]
        elif out_d:
            px, py = -out_d[1], out_d[0]
        else:
            px, py = 0.0, 0.0
        L = math.sqrt(px*px + py*py)
        if L > 0: px, py = px/L, py/L
        perp.append((px, py))
    # `signed_offset_m > 0` should point in the perpendicular's natural
    # direction (90° CCW from travel = "left" when going north on a S→N
    # pass). Negate to follow the "right = positive" convention used by
    # _row_offset_m callers (positive row offset = east when going north).
    s = -signed_offset_m
    out = []
    for (e, n), (px, py) in zip(enu, perp):
        e2 = e + s * px
        n2 = n + s * py
        out.append((lat0 + n2 / M_PER_DEG_LAT, lon0 + e2 / M_PER_DEG_LON))
    return out


def _shelter_row_centerlines(passes, layout, mask, row_spacing_m=None,
                              total_rows=None):
    """One polyline per female bay per pass.

    Scans the resolved mask for every contiguous F block and offsets each
    pass polyline by that block's lateral distance from the pass centre.
    Handles arbitrary masks (including repeating units with multiple
    internal M strips, e.g. 2M+8F × 2 = 'FFFFMMFFFFFFFFMMFFFF'); the old
    "outer vs centered" heuristic only worked for single-strip masks and
    silently missed internal bays from repeats.

    Joining bays (where the right F block of pass N butts against the
    left F block of pass N+1) end up with one centerline contributed by
    EACH pass — visually two close shelter rows in the same bay. That's
    a strict improvement over missing them entirely; can be merged later
    if we want exactly-centered placement in joins.
    """
    if not passes: return []
    # Need mask + geometry to compute per-bay offsets. Without them, fall
    # back to legacy behaviour (one centerline per pass — equivalent to
    # the old "outer" mode).
    if not mask or row_spacing_m is None or not total_rows:
        return [list(p) for p in passes]

    f_blocks = mask_runs(mask, 'F')
    if not f_blocks:
        return []   # no female rows = no shelters

    # Lateral offset of each F block's centre from the planter centre line.
    # Block (s, e) covers rows s..e-1; its centre row index is (s + e - 1)/2.
    # Planter centre row index is (total_rows - 1)/2.
    centre = (total_rows - 1) / 2.0
    block_offsets_m = [((s + e - 1) / 2.0 - centre) * row_spacing_m
                       for s, e in f_blocks]

    centerlines = []
    for p in passes:
        if len(p) < 2: continue
        for off_m in block_offsets_m:
            if off_m == 0:
                centerlines.append([tuple(pt) for pt in p])
            else:
                centerlines.append(_offset_polyline_latlon(p, off_m))
    return centerlines


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
    # Manual-pin mode is a complete short-circuit: the user owns the pin
    # set, so we skip all geometry / boundary / placement work and just
    # return the stored pins. No pivot, no boundary, nothing else needed.
    mode_early = str(field_dict.get('shelter_mode') or '').strip().lower()
    if mode_early == 'manual':
        manual = field_dict.get('manual_shelter_pins') or []
        result = []
        for pt in manual:
            try:
                lat, lon = float(pt[0]), float(pt[1])
                result.append((lat, lon))
            except (TypeError, ValueError, IndexError):
                continue
        if return_rows:
            return result, [0] * len(result)
        return result

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

        # Inner-exclusion boundaries (JD-style "interior boundaries":
        # buildings, sloughs, pivot pads, etc.). A shelter is invalid if it
        # lands inside ANY inner ring. Stored on the field as lat/lon; convert
        # to ENU once for fast point-in-polygon checks below.
        boundary_inner_raw = field_dict.get('boundary_inner') or []
        boundary_inner_enu = []
        for ring in boundary_inner_raw:
            if not ring or len(ring) < 3: continue
            try:
                ring_enu = latlon_list_to_enu(
                    [(float(p[0]), float(p[1])) for p in ring],
                    pivotpoint[0], pivotpoint[1])
                if len(ring_enu) >= 3:
                    boundary_inner_enu.append(ring_enu)
            except Exception:
                pass

        seed_angle = float(field_dict.get('Spray_angle') or field_dict.get('Seed_angle') or 0)

        # Bay parameters → tent_row_width and lat_offset.
        # tent_row_width = female_m + male_m is the bay repeat distance.
        # lat_offset places the shelter a FIXED 4 ft (1.2192 m) into the female
        # bay from the male/female boundary — independent of bay size, so the
        # "just east of the male bay" rule holds for any female/male count.
        nf_raw = str(field_dict.get('num_female_rows') or '').strip()
        nm_raw = str(field_dict.get('num_male_rows') or '').strip()
        rs_in_raw = str(field_dict.get('row_spacing_in') or '').strip()
        # Blanket-planted crops (use_bays=False): there's no female-bay
        # structure to snap to, so we use a uniform grid at sprayer_width
        # intervals with no lateral offset. Defaults True so existing fields
        # (canola) keep their current behaviour.
        use_bays = field_dict.get('use_bays', True)
        if isinstance(use_bays, str):
            use_bays = use_bays.strip().lower() in ('1','true','yes','y','on')
        else:
            use_bays = bool(use_bays)
        if not use_bays:
            tent_row_width = sprayer_width
            lat_offset = 0.0
        elif nf_raw and nm_raw and rs_in_raw:
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
        # Manual mode is handled by the early short-circuit above. We
        # shouldn't reach here in manual mode; if we do, fall through to
        # the legacy spacing path defensively.
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
        elif mode == 'acres_per_shelter':
            # User specifies acres each shelter should cover. e.g. 2 → one
            # shelter per 2 acres → 0.5 shelters/acre.
            try:
                aps = float(field_dict.get('acres_per_shelter') or 0)
                ac  = float(field_dict.get('acres') or 0)
                if aps > 0 and ac > 0:
                    num_tents = max(1, int(round(ac / aps)))
            except (ValueError, TypeError):
                num_tents = None
        elif mode in ('trays_1', 'trays_2'):
            # Auto: derived from bee allocation. Total trays = ceil(gpa × acres
            # ÷ gpt). 1 tray per shelter → num = total_trays; 2 per shelter →
            # num = ceil(total_trays / 2).
            try:
                gpa = float(field_dict.get('gals_per_acre') or 0)
                gpt = float(field_dict.get('gals_per_tray') or 0)
                ac  = float(field_dict.get('acres') or 0)
                if gpa > 0 and gpt > 0 and ac > 0:
                    total_trays = int(math.ceil(gpa * ac / gpt))
                    divisor = 2 if mode == 'trays_2' else 1
                    num_tents = max(1, int(math.ceil(total_trays / divisor)))
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
        # PASS-FOLLOWING MODE — use imported JD planter passes as ground truth.
        #
        # When the field has uploaded planter_passes AND use_imported_passes is
        # on, we ignore the synthetic grid entirely and place shelters along
        # centerlines derived from the actual passes:
        #   outer-mask    → centerlines = the passes themselves
        #   centered-mask → centerlines = midpoints between adjacent passes
        # The row mask (resolved from row_layout + custom_row_mask) decides
        # which strategy applies.
        #
        # Falls through to the synthetic-grid branch when there's no usable
        # planter data, the toggle is off, the user gave a manual spacing
        # (those modes don't make sense without a 2D grid), or num_tents is
        # missing.
        # =====================================================================
        planter_passes_raw = field_dict.get('planter_passes') or []
        use_imported = bool(field_dict.get('use_imported_passes', True))
        # Normalise to list of [(lat, lon), ...] lists; saved JSON gives [[lat,lon],...].
        planter_passes = []
        for p in planter_passes_raw:
            if not p or len(p) < 2: continue
            planter_passes.append([(float(pt[0]), float(pt[1])) for pt in p])

        # Pass-following is bay-aware (it maps row mask → female-bay
        # centerlines). With no bays, fall through to the synthetic uniform
        # grid where lat_offset=0 and tent_row_width=sprayer_width already.
        if use_imported and planter_passes and not user_spacing and num_tents and use_bays:
            row_layout_v = str(field_dict.get('row_layout') or 'centered').strip().lower()
            custom_mask  = str(field_dict.get('custom_row_mask') or '').strip()
            nf_i_pf = int(nf_raw) if nf_raw else 8
            nm_i_pf = int(nm_raw) if nm_raw else 2
            # Total planter rows — defaults to nf+nm if not set (legacy fields).
            tr_raw = str(field_dict.get('total_rows') or '').strip()
            try: total_rows_pf = int(tr_raw) if tr_raw else (nf_i_pf + nm_i_pf)
            except (ValueError, TypeError): total_rows_pf = nf_i_pf + nm_i_pf
            mask = resolve_row_mask(nf_i_pf, nm_i_pf, row_layout_v, custom_mask,
                                     total_rows=total_rows_pf)
            # row_spacing in metres — needed to translate mask row indices
            # into lateral offsets for the per-bay centerlines.
            rs_in_pf = float(rs_in_raw) if rs_in_raw else 22.0
            row_spacing_m_pf = rs_in_pf * 0.0254
            centerlines = _shelter_row_centerlines(
                planter_passes, row_layout_v, mask,
                row_spacing_m=row_spacing_m_pf, total_rows=total_rows_pf)

            # Convert each centerline to ENU (relative to pivot) and prebuild
            # arc-length so we can sample evenly and exclude in one pass.
            enu_centerlines = []
            for cl in centerlines:
                if len(cl) < 2: continue
                cl_enu = [latlon_list_to_enu([pt], pivotpoint[0], pivotpoint[1])[0]
                          for pt in cl]
                # cumulative metric length in ENU (matches lat/lon haversine well
                # at field scale)
                lens = [0.0]
                for i in range(len(cl_enu) - 1):
                    dx = cl_enu[i+1][0] - cl_enu[i][0]
                    dy = cl_enu[i+1][1] - cl_enu[i][1]
                    lens.append(lens[-1] + math.sqrt(dx*dx + dy*dy))
                if lens[-1] <= 0: continue
                enu_centerlines.append((cl, cl_enu, lens))

            def _interp_enu(cl_enu, lens, target_m):
                if target_m <= lens[0]: return cl_enu[0]
                if target_m >= lens[-1]: return cl_enu[-1]
                for i in range(len(lens) - 1):
                    if lens[i+1] >= target_m:
                        seg = lens[i+1] - lens[i]
                        t = 0.0 if seg <= 0 else (target_m - lens[i]) / seg
                        return (cl_enu[i][0] + t * (cl_enu[i+1][0] - cl_enu[i][0]),
                                cl_enu[i][1] + t * (cl_enu[i+1][1] - cl_enu[i][1]))
                return cl_enu[-1]

            # Same exclusion rules as the synthetic-grid branch.
            excl_m_safe_pf = excl_m + 0.01
            # User-settable edge buffer: how far in from any sprayer-pass edge
            # shelters can sit. Middle of each pass becomes a kill zone of
            # width max(0, sprayer_width − 2 × buffer). Applies to the outside
            # pass AND every main pass through the field interior.
            try:
                pass_edge_buffer_m_pf = float(field_dict.get("pass_edge_buffer_ft") or 0) * 0.3048
            except (ValueError, TypeError):
                pass_edge_buffer_m_pf = 30.0 * 0.3048
            # buffer ≤ 0 → user wants the kill zones turned off entirely.
            buffer_enabled_pf = pass_edge_buffer_m_pf > 0
            pass_dead_half_pf = max(0.0, sprayer_width / 2.0 - pass_edge_buffer_m_pf)

            # Pre-compute boundary edge data once so the outside-pass
            # min-distance loop avoids re-deriving dx/dy/seg² for every
            # candidate. ~150K validity calls × 87 edges × subtraction =
            # the difference between sub-second and minute-long freezes.
            _bnd_edges = None
            if boundary_enu and outside_pass and buffer_enabled_pf:
                _bnd_edges = []
                n_b = len(boundary_enu)
                for i in range(n_b):
                    ax, ay = boundary_enu[i]
                    bx, by = boundary_enu[(i+1) % n_b]
                    dx_ = bx - ax; dy_ = by - ay
                    seg2 = dx_*dx_ + dy_*dy_
                    _bnd_edges.append((ax, ay, dx_, dy_, seg2))
            # Pre-square sprayer_width for the pivot-inner check.
            _inner_pivot_r2 = sprayer_width * sprayer_width
            # Pre-tuple pivot_tracks so we iterate a tuple (faster than list).
            _pivot_tracks_t = tuple(pivot_tracks) if pivot_tracks else ()
            # Outside-pass kill zone bounds — pre-bound the comparison so the
            # hot loop just does two compares per candidate.
            _outpass_lo = pass_edge_buffer_m_pf
            _outpass_hi = sprayer_width - pass_edge_buffer_m_pf

            def _pf_valid(east, north):
                # Cheapest checks first — pivot inner and main-pass kill zone
                # are constant-time. The round-trip lat/lon safety check that
                # used to live here is gone; the final post-filter still does
                # it, and the savings on the hot path are large (~150K calls
                # at the binary-search starting spacing).
                d_sq = east * east + north * north
                if d_sq < _inner_pivot_r2:
                    return False
                if buffer_enabled_pf and pass_dead_half_pf > 0 and sprayer_width > 0:
                    lat_e = east * cos_r + north * sin_r
                    r_idx = round(lat_e / sprayer_width)
                    d_pc = lat_e - r_idx * sprayer_width
                    if d_pc < 0: d_pc = -d_pc
                    if d_pc < pass_dead_half_pf:
                        return False
                if _pivot_tracks_t:
                    d = math.sqrt(d_sq)
                    for tr in _pivot_tracks_t:
                        diff = d - tr
                        if diff < 0: diff = -diff
                        if diff < excl_m_safe_pf:
                            return False
                if corner_excl and _in_corner_excl(east, north):
                    return False
                # Boundary check (O(N_outer) edges). After the above cheap
                # checks have early-rejected most candidates, only the
                # ones that need this expensive test reach it.
                if boundary_enu:
                    if not _point_in_polygon(east, north, boundary_enu):
                        return False
                else:
                    if d_sq > radius_sqr:
                        return False
                # Inner exclusions — must NOT be inside any of them.
                for ring in boundary_inner_enu:
                    if _point_in_polygon(east, north, ring):
                        return False
                # Outside-pass kill zone last (it does an O(N_outer) min
                # distance calc and is the second-most expensive check).
                if outside_pass and buffer_enabled_pf:
                    if _bnd_edges is not None:
                        min_d2 = float('inf')
                        for ax, ay, dx_, dy_, seg2 in _bnd_edges:
                            if seg2 > 0:
                                t = ((east - ax) * dx_ + (north - ay) * dy_) / seg2
                                if t < 0.0: t = 0.0
                                elif t > 1.0: t = 1.0
                                px = ax + t * dx_; py = ay + t * dy_
                            else:
                                px, py = ax, ay
                            ddx = east - px; ddy = north - py
                            d2 = ddx*ddx + ddy*ddy
                            if d2 < min_d2: min_d2 = d2
                        d_b = math.sqrt(min_d2)
                    else:
                        d_b = radius - math.sqrt(d_sq)
                    if _outpass_lo < d_b < _outpass_hi:
                        return False
                return True

            # Estimate target N-S spacing from the total length of all
            # centerlines and the requested shelter count, then verify and
            # tighten with a quick adjustment loop.
            total_len_m = sum(lens[-1] for _, _, lens in enu_centerlines)
            if total_len_m <= 0 or not enu_centerlines:
                return ([], []) if return_rows else []
            # Add a tiny over-shoot factor so exclusions eat the slack without
            # leaving us short.
            ns_spacing_pf = max(1.0, total_len_m / max(1, num_tents))

            # PERFORMANCE — Pre-sample each centerline at fine resolution and
            # cache (t, e, n, valid). The binary search below calls _place_at
            # ~25 times with different spacings; without caching, that was
            # ~150K × 25 = 3.75M validity checks (a many-second to many-
            # minute freeze on real fields). With caching, validity is
            # computed ONCE (~150K calls) and _place_at then does cheap
            # array lookups for each subsequent spacing.
            SAMPLE_STEP_M = 2.0   # cached sample every 2 m — placement
                                   # accuracy of ~1 m is well below shelter
                                   # spacing; halves cache-build time.
            centerline_cache = []   # one entry per centerline; each = list of (t, e, n, valid)
            for r_idx, (_, cl_enu, lens) in enumerate(enu_centerlines):
                total = lens[-1]
                if total <= 0:
                    centerline_cache.append([])
                    continue
                n_samples = int(total / SAMPLE_STEP_M) + 1
                samples = []
                for j in range(n_samples + 1):
                    t = j * SAMPLE_STEP_M
                    if t > total: t = total
                    e, n_v = _interp_enu(cl_enu, lens, t)
                    samples.append((t, e, n_v, _pf_valid(e, n_v)))
                centerline_cache.append(samples)

            # ── 2D GRID placement ───────────────────────────────────────
            # The previous algorithm placed one shelter per centerline at a
            # golden-ratio scattered N position, which gave a visually random
            # pattern when target < centerlines. Now we pick R uniform N
            # positions × C centerlines (even-stride pick from the 108
            # available) so the result is a proper R × C grid with the same
            # regularity as the synthetic-grid mode the user sees when
            # planter data is toggled off.

            # Direction vectors derived from the first centerline.
            ax = enu_centerlines[0][1][0][0]; ay = enu_centerlines[0][1][0][1]
            bx = enu_centerlines[0][1][-1][0]; by = enu_centerlines[0][1][-1][1]
            tlen0 = math.sqrt((bx-ax)**2 + (by-ay)**2) or 1
            tdx0 = (bx-ax)/tlen0; tdy0 = (by-ay)/tlen0       # travel direction
            ldx0, ldy0 = -tdy0, tdx0                          # lateral direction

            # Sort centerlines by their lateral midpoint coord so even-stride
            # picks a clean E-W spread.
            cl_meta = []
            for r_idx, samples in enumerate(centerline_cache):
                if not samples: continue
                mid = samples[len(samples)//2]
                lat_coord = mid[1] * ldx0 + mid[2] * ldy0
                cl_meta.append((lat_coord, r_idx, samples))
            cl_meta.sort()

            n_clines = len(cl_meta)
            if n_clines == 0:
                return ([], []) if return_rows else []
            e_range = cl_meta[-1][0] - cl_meta[0][0] if n_clines > 1 else 1.0
            if e_range <= 0: e_range = 1.0
            avg_len = total_len_m / n_clines

            # R × C with R/C ≈ N-S range / E-W range. Aim for 25% over-target
            # so validity rejections (track exclusions, kill zones) don't
            # leave us short.
            target_over = max(1, int(math.ceil(num_tents * 1.25)))
            aspect = (avg_len / e_range) if e_range > 0 else 1.0
            R = max(1, round(math.sqrt(target_over * aspect)))
            C = max(1, math.ceil(target_over / R))
            if C > n_clines: C = n_clines

            # Pick C centerlines via even stride from the lateral-sorted list.
            if C >= n_clines:
                picked = list(range(n_clines))
            elif C == 1:
                picked = [n_clines // 2]
            else:
                step = (n_clines - 1) / (C - 1)
                picked = sorted({int(round(i * step)) for i in range(C)})

            # Find the global N-S range across all picked centerlines so the
            # R row positions are ABSOLUTE — every row lands at the same N
            # latitude regardless of which centerlines reach that far.
            # That's what gives the clean horizontal-row look the user
            # expects (was previously using each centerline's own t/total
            # fraction, which produced ragged rows on fields with passes of
            # different lengths).
            travel_n_proj = lambda e, n: e * tdx0 + n * tdy0
            picked_samples = [cl_meta[pi][2] for pi in picked]
            picked_rids    = [cl_meta[pi][1] for pi in picked]
            t_proj_min = min(travel_n_proj(s[0][1], s[0][2])
                             for s in picked_samples)
            t_proj_max = max(travel_n_proj(s[-1][1], s[-1][2])
                             for s in picked_samples)
            # Pass polylines run start→end along travel direction; if any
            # centerline's start projects HIGHER than its end (boustrophedon
            # alternation), our min/max above already covers both cases.
            lo_proj = min(t_proj_min, t_proj_max)
            hi_proj = max(t_proj_min, t_proj_max)
            n_span = hi_proj - lo_proj
            if n_span <= 0: n_span = avg_len

            row_targets = [lo_proj + (r + 0.5) * n_span / R for r in range(R)]
            # For each centerline, project every sample onto the travel axis
            # and find the closest sample to each row's target. Keep only
            # samples within half-step of the target so a too-short
            # centerline doesn't contribute spurious end-clamped pins.
            half_step = n_span / (2 * R) if R > 0 else 0
            placed = []
            for r_idx, samples in zip(picked_rids, picked_samples):
                if not samples: continue
                # Precompute projections so we don't redo per row.
                projs = [travel_n_proj(s[1], s[2]) for s in samples]
                p_lo = min(projs); p_hi = max(projs)
                for n_target in row_targets:
                    if n_target < p_lo - half_step or n_target > p_hi + half_step:
                        continue
                    best_k = None; best_dn = float('inf')
                    for k, p in enumerate(projs):
                        d = p - n_target
                        if d < 0: d = -d
                        if d < best_dn:
                            best_dn = d; best_k = k
                    if best_k is None or best_dn > half_step:
                        continue
                    _ts, es, ns_v, valid = samples[best_k]
                    if valid:
                        placed.append((es, ns_v, r_idx))

            if not placed:
                return ([], []) if return_rows else []

            # Snake order by row (NW-style consistent with the synthetic branch).
            # Sort each row's points by their projection onto the dominant axis.
            from collections import defaultdict
            groups = defaultdict(list)
            for e, n, r in placed:
                groups[r].append((e, n))

            # Determine a global "lateral" direction from the average pass
            # heading (approximated by the first-to-last vector of the first
            # centerline). Travel direction is along the pass.
            ax = enu_centerlines[0][1][0][0]; ay = enu_centerlines[0][1][0][1]
            bx = enu_centerlines[0][1][-1][0]; by = enu_centerlines[0][1][-1][1]
            tlen = math.sqrt((bx-ax)**2 + (by-ay)**2) or 1
            tdx_pf, tdy_pf = (bx-ax)/tlen, (by-ay)/tlen     # travel direction
            ldx_pf, ldy_pf = -tdy_pf, tdx_pf                # lateral direction

            def row_lat_coord_pf(r):
                pts = groups[r]
                return sum(e*ldx_pf + n*ldy_pf for e, n in pts) / len(pts)
            sorted_rows = sorted(groups.keys(), key=row_lat_coord_pf)

            ordered = []
            ordered_rows = []
            for i, r in enumerate(sorted_rows):
                pts = sorted(groups[r], key=lambda en: en[0]*tdx_pf + en[1]*tdy_pf)
                # Snake: alternate row direction so the numbering flows.
                if i % 2 == 1:
                    pts = list(reversed(pts))
                ordered.extend(pts)
                ordered_rows.extend([i] * len(pts))

            # Trim to num_tents using an even-stride pick across the snake order
            # rather than taking the first N. Taking the first N leaves the
            # right-side rows empty whenever the candidate pool exceeds
            # num_tents — visible as a hard E-W cutoff. Striding evenly across
            # all candidates keeps the spatial distribution uniform.
            if len(ordered) > num_tents > 0:
                step = len(ordered) / num_tents
                keep_idx = [int(round(i * step)) for i in range(num_tents)]
                # round() can collide on the last index; clamp to bounds
                keep_idx = sorted(set(min(len(ordered) - 1, k) for k in keep_idx))
                # If de-duplication shrank below target, pad with the gaps
                if len(keep_idx) < num_tents:
                    extras = [i for i in range(len(ordered)) if i not in set(keep_idx)]
                    keep_idx = sorted(keep_idx + extras[:num_tents - len(keep_idx)])
                ordered = [ordered[i] for i in keep_idx]
                ordered_rows = [ordered_rows[i] for i in keep_idx]

            result = []
            kept_rows = []
            for (e, n), r_idx in zip(ordered, ordered_rows):
                lon, lat = utmish.to_lonlat(e + easting, n + northing, pivotpoint[0])
                result.append((lat, lon))
                kept_rows.append(r_idx)
            if return_rows:
                return result, kept_rows
            return result

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

            # Pre-compute boundary edge data (dx, dy, seg²) so the min-
            # distance and outside-pass kill zone calls in the hot loop
            # don't re-derive these per call. The synthetic-grid path runs
            # _count_at_least 32 times during binary search, each walking
            # thousands of candidates × all 87 boundary edges — caching
            # this turns multi-minute compute into sub-second.
            _bnd_edges_sg = None
            if boundary_enu:
                _bnd_edges_sg = []
                n_b = len(boundary_enu)
                for i in range(n_b):
                    ax, ay = boundary_enu[i]
                    bx, by = boundary_enu[(i + 1) % n_b]
                    dx_, dy_ = bx - ax, by - ay
                    _bnd_edges_sg.append((ax, ay, dx_, dy_, dx_*dx_ + dy_*dy_))

            def _min_dist_to_bnd(east, north):
                """Min distance from (east, north) to any boundary polygon edge."""
                min_d2 = float('inf')
                for ax, ay, dx_, dy_, seg2 in _bnd_edges_sg:
                    if seg2 > 0:
                        t = ((east-ax)*dx_ + (north-ay)*dy_) / seg2
                        if t < 0.0: t = 0.0
                        elif t > 1.0: t = 1.0
                        px = ax + t*dx_; py = ay + t*dy_
                    else:
                        px, py = ax, ay
                    ddx = east - px; ddy = north - py
                    d2 = ddx*ddx + ddy*ddy
                    if d2 < min_d2:
                        min_d2 = d2
                return math.sqrt(min_d2)

            r_max = int(radius / sprayer_width) + 2

            # Add 1 cm safety margin to excl_m so floating-point positions that
            # land within rounding error of the track boundary are consistently excluded.
            excl_m_safe = excl_m + 0.01

            # Sprayer edge buffer — how far in from any pass edge shelters
            # can sit. Middle of each pass becomes a kill zone of width
            # max(0, sprayer_width − 2 × buffer). Applies to:
            #   - Outside pass (only when outside_sprayer_pass = Yes)
            #   - Every main pass through the field interior (always on)
            try:
                pass_edge_buffer_m = float(field_dict.get("pass_edge_buffer_ft") or 0) * 0.3048
            except (ValueError, TypeError):
                pass_edge_buffer_m = 30.0 * 0.3048
            # buffer ≤ 0 → kill zones turned off.
            buffer_enabled = pass_edge_buffer_m > 0
            pass_dead_half = max(0.0, sprayer_width / 2.0 - pass_edge_buffer_m)
            # Slide-along-bay budget for rescuing shelters that land in a forbidden
            # zone (pivot-track exclusion or sprayer kill zone).
            SNAP_MAX_M = 15.0
            SNAP_STEP_M = 0.25

            _pivot_tracks_sg = tuple(pivot_tracks) if pivot_tracks else ()

            def _valid(east, north):
                # Cheapest checks first so the hot loop bails fast.
                d_sq = east*east + north*north
                if d_sq < inner_r2: return False
                # Main-pass kill zone — middle of every interior sprayer
                # pass (constant-time, computed in the rotated frame).
                if buffer_enabled and pass_dead_half > 0 and sprayer_width > 0:
                    lat_e = east * cos_r + north * sin_r
                    r_idx = round(lat_e / sprayer_width)
                    d_pc = lat_e - r_idx * sprayer_width
                    if d_pc < 0: d_pc = -d_pc
                    if d_pc < pass_dead_half:
                        return False
                if _pivot_tracks_sg:
                    d = math.sqrt(d_sq)
                    for tr in _pivot_tracks_sg:
                        diff = d - tr
                        if diff < 0: diff = -diff
                        if diff < excl_m_safe:
                            return False
                if corner_excl and _in_corner_excl(east, north): return False
                # Outside-pass kill zone — expensive O(N_outer); do it last.
                if outside_pass and buffer_enabled:
                    if boundary_enu:
                        d_b = _min_dist_to_bnd(east, north)
                    else:
                        d_b = radius - math.sqrt(d_sq)
                    if pass_edge_buffer_m < d_b < (sprayer_width - pass_edge_buffer_m):
                        return False
                return True

            def _inside(east, north):
                """Is the point inside the field boundary (polygon or circle)
                AND outside every inner-exclusion ring?

                The lat/lon round-trip safety check that used to live here
                ran utmish trig per call — way too expensive for the
                _count_at_least binary search hot loop. The final post-filter
                still does it, so any sub-mm drift past the boundary is
                still caught before the result is returned."""
                if boundary_enu:
                    if not _point_in_polygon(east, north, boundary_enu):
                        return False
                else:
                    if east*east + north*north > radius_sqr:
                        return False
                # Inner exclusions — must NOT be inside any of them.
                for ring in boundary_inner_enu:
                    if _point_in_polygon(east, north, ring):
                        return False
                return True

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

            # Optional user override: aim for a target number of N-S shelter
            # rows. Fewer lateral columns → the binary search packs more
            # shelters per column → more rows (and, with the half-step stagger
            # on alternate columns, ~2× that many distinct northing bands).
            # Empty / 0 → automatic (use every sprayer-pass column).
            try:
                forced_rows = int(float(field_dict.get('shelter_rows') or 0))
            except (ValueError, TypeError):
                forced_rows = 0
            if forced_rows > 0 and num_tents and len(row_list) > 1:
                # Each column carries ~forced_rows/2 shelters once staggering
                # interleaves alternate columns, so columns ≈ 2·N / rows.
                want_cols = max(1, min(len(row_list),
                                       int(round(2.0 * num_tents / forced_rows))))
                if want_cols < len(row_list):
                    if want_cols == 1:
                        idxs = [len(row_list) // 2]
                    else:
                        stride = (len(row_list) - 1) / (want_cols - 1)
                        idxs = sorted({int(round(i * stride)) for i in range(want_cols)})
                    row_list = [row_list[i] for i in idxs]

            def _count_at_least(n_sp, target):
                # True if at least `target` PLACEABLE cells exist at this spacing
                # (valid as-is, or snappable out of a kill/track zone). Early-
                # exits as soon as the target is reached, so dense spacings
                # stay cheap. Counting placeable cells makes the chosen spacing
                # yield the requested shelter count regardless of the outside-
                # sprayer-pass toggle — that toggle changes which cells are
                # valid, not how many.
                # Stagger keys off the row's INDEX in row_list (its visual
                # order), not k — k can jump several bays per pass when the
                # sprayer is wider than a bay, so k%2 wouldn't alternate
                # consistently.
                if n_sp <= 0: return False
                c_max = int(radius / n_sp) + 2
                total = 0
                for idx, (pre_e, _k) in enumerate(row_list):
                    n_stagger = (n_sp / 2) if (idx % 2) else 0.0
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

            # Generate the final grid at the chosen spacing. Stagger and row
            # grouping both key off the row's visual index, not k.
            raw = []
            c_max = int(radius / ns_spacing) + 2
            for idx, (pre_e, _k) in enumerate(row_list):
                n_stagger = (ns_spacing / 2) if (idx % 2) else 0.0
                for c in range(-c_max, c_max + 1):
                    pre_n = c * ns_spacing + directional_offset + n_stagger
                    east  = pre_e * cos_r - pre_n * sin_r
                    north = pre_n * cos_r + pre_e * sin_r
                    if not _inside(east, north): continue
                    if _valid(east, north):
                        raw.append((east, north, idx))
                    else:
                        snapped = _snap_along_pre_n(pre_e, pre_n)
                        if snapped is not None:
                            new_e = pre_e * cos_r - snapped * sin_r
                            new_n = snapped * cos_r + pre_e * sin_r
                            raw.append((new_e, new_n, idx))

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

            # Convert to lat/lon, then drop any shelter that — after the
            # ENU→latlon→ENU round-trip — no longer lands strictly inside the
            # boundary. Internal placement uses _inside on the algorithm's
            # (east, north) coordinates, but tiny floating-point drift in the
            # round-trip can push a sub-mm-edge shelter to the visible-outside
            # side. This filter guarantees no shelter ever renders outside the
            # boundary line on the map.
            result = []
            kept_rows = []
            for (e, n), row_idx in zip(ordered, ordered_rows):
                lon, lat = utmish.to_lonlat(e + easting, n + northing, pivotpoint[0])
                if boundary_enu:
                    re_e, re_n = utmish.from_lonlat(lon, lat, pivotpoint[0])
                    re_e -= easting; re_n -= northing
                    if not _point_in_polygon(re_e, re_n, boundary_enu):
                        continue
                    # Drop any shelter that lands inside an inner-exclusion
                    # ring after the round-trip too.
                    if any(_point_in_polygon(re_e, re_n, ring)
                           for ring in boundary_inner_enu):
                        continue
                result.append((lat, lon))
                kept_rows.append(row_idx)
            ordered_rows = kept_rows
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
