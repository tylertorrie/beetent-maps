#!/usr/bin/python3

import os
import utmish
import simplekml
import shapefile
import math
import sys
import zipfile
import datetime
import csv
import cairo
from io import BytesIO
from io import StringIO

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

def make_pdf_circle_bays(ctx, field):
    """
        Draw the outline of the circle and male bays on the
        Cairo context ctx that will end up in the PDF
    """

    surface = ctx.get_target()

    #TODO: cleaner, programmatic way to do this
    width = 8.5 * 72
    height = 11 * 72

    #make the entire circle fit on the page
    scale = 8/8.5*width / field['Radius'] / 2

    ctx.set_line_width(0.5)
    ctx.set_source_rgb(1,1,1)
    ctx.rectangle(0,0,width,height)
    ctx.fill()
    ctx.set_source_rgb(0,0,0)

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


    ctx.arc(width/2, height/2, 2, 0, 2*math.pi)
    ctx.fill()

    if field['pie_slice']:
        start_angle = 360 - field['pie_slice'][0] + 90
        end_angle = 360 - field['pie_slice'][1] + 90
        x = math.cos(math.radians(start_angle)) * field['Radius']
        y = math.sin(math.radians(start_angle)) * field['Radius']
        if x > eastlimit: x=eastlimit
        elif x < -westlimit: x = -westlimit
        
        if y > northlimit: y = northlimit
        elif y < -southlimit: y = -southlimit

        ctx.move_to(x * scale + width/2,
                    -y * scale + height/2)

        ctx.line_to(width/2, height/2)

        x = math.cos(math.radians(end_angle)) * field['Radius']
        y = math.sin(math.radians(end_angle)) * field['Radius']
        if x > eastlimit: x=eastlimit
        elif x < -westlimit: x = -westlimit
        
        if y > northlimit: y = northlimit
        elif y < -southlimit: y = -southlimit

        ctx.line_to(x * scale + width/2,
                    -y * scale + height/2)

        ctx.stroke()
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
    ctx.move_to(x * scale + width/2,
                -y * scale + height/2)

    # if arc crosses 0, adjust

    ctx.set_line_width(1)
    angle = start_angle
    while True:
        x = math.cos(math.radians(angle)) * field['Radius']
        y = math.sin(math.radians(angle)) * field['Radius']
        if x > eastlimit: x=eastlimit
        elif x < -westlimit: x = -westlimit
        
        if y > northlimit: y = northlimit
        elif y < -southlimit: y = -southlimit

        ctx.line_to(x * scale + width/2,
                    -y * scale + height/2)

        angle -= 1
        if angle < end_angle: break
     
    ctx.stroke()

    ctx.set_line_width(0.5)
    radius = field['Radius']
    radius_sqr = radius * radius
    sprayer_width = field['Sprayer_width']
    spacing = calculate_spacing(radius, sprayer_width)
    rotate = 0 - field['Seed_angle']
    rotate = (rotate + 180) % 360 - 180
    pie_slice = field['pie_slice']
    lat_shift = field['Lateral_offset']


    if 'Female_bays_per_width' in field and field['Female_bays_per_width']:
        bays_per_sprayer_width = field['Female_bays_per_width']

        # Draw male bays
        ctx.set_source_rgb(0,0,0.9)
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

            ctx.move_to(east1 * scale + width/2, -north1 * scale + height/2)
            ctx.line_to(east2 * scale + width/2, -north2 * scale + height/2)
            ctx.stroke()
        
    ctx.select_font_face("Arial", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    ctx.set_font_size(24)

    ctx.move_to(72,72)
    ctx.set_source_rgb(0,0,0)
    ctx.show_text(field['Name'])


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

    eastlimit = kwargs.get('eastlimit',radius)
    northlimit = kwargs.get('northlimit', radius)
    westlimit = kwargs.get('westlimit', radius)
    southlimit = kwargs.get('southlimit', radius)

    # PDF options
    pdf = kwargs.get('pdf', None)
    pdf_width = 8.5 * 72
    pdf_height = 11 * 72
    scale = 8*72 / radius / 2

    # create starting point for grid
    easting, northing = utmish.from_lonlat(pivotpoint[0], pivotpoint[1], pivotpoint[0])
    radius_sqr = radius * radius
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

    tent_id = 0

    # Experimental rows

    
    exp_rows = kwargs['exp_rows']
    if exp_rows:
        rows = exp_rows
        exp_rows = True
        odd = kwargs.get('exp_rows_start_odd',True)
    else:
        rows = range(-int(radius / width),int(radius / width) + 1)
        
    for r in rows:
        if not exp_rows:
            odd = r % 2
        #print (odd)
        for c in range(-int(radius / spacing)-1, int(radius / spacing) + 1):
            # only place tents in specified quadrants of circle

            if odd: #odd, shift by half spacing
                east = r*width + lat_shift
                north = c*spacing+spacing/2
            else: #even, don't shift
                east = r*width + lat_shift
                north = c*spacing

            east1 = east * math.cos(math.radians(rotate)) - north * math.sin(math.radians(rotate))
            north1 = north * math.cos(math.radians(rotate)) + east * math.sin(math.radians(rotate))
            east = east1
            north = north1

            if east * east + north * north <= radius_sqr:
                #manual limits to keep tents on field
                if eastlimit and east > eastlimit: continue
                if westlimit and east < -westlimit: continue
                if northlimit and north > northlimit: continue
                if southlimit and north < -southlimit: continue

                flag = True

                if pie_slice and (north or east):
                    angle = math.degrees(math.atan2(east,north))
                    if (angle < 0): angle += 360

                    if pie_slice[0] > pie_slice[1]:
                        # arc crosses zero
                        if angle < pie_slice[0] and angle > pie_slice[1]: 
                            flag = False
                            #print (tent_id, angle)
                        #if angle >= pie_slice[0] or angle <= pie_slice[1]: flag = True
                        #else: flag = False
                    else:
                        if angle < pie_slice[0] or angle > pie_slice[1]: flag = False
                        #if angle >= pie_slice[0] and angle <= pie_slice[1]: flag = True
                        #else: flag = False



                if not flag:
                    # no specified quadrant claims this tent, so skip
                    continue

                if pdf:
                    pdf.set_source_rgb(1,0,0)
                    pdf.arc(east * scale + pdf_width/2 , -north * scale + pdf_height/2, 2,0,2*math.pi)
                    pdf.fill()

                lon, lat = utmish.to_lonlat(east + easting, north + northing, pivotpoint[0])
                kml.newpoint (name="tent %d" % (tent_id), coords = [ (lon, lat) ])

                w.record(Date=datetime.date.today(), Time="12:00:00pm",Version="7.78.002",
                         Id = pid, Name = "Tree_%d" % pid, Latitude = lat, Longitude = lon, Height = 761.064,
                         AlarmRad = 0, WarningRad = 10.0, Status_Text='', Visible=1)

                w.point(lon, lat)

                csvdata.writerow( {'GPS Position': '%.7f,%.7f' % (lat,lon), 'Tent': '%d' % tent_id} )

                pid += 1
                tent_id += 1
        if exp_rows:
            odd = not odd

    myzip.writestr("%s/googleearth/%s.kml" % (trimble_path,field_name), kml.kml())    
    w.close()
    myzip.writestr('%s/AgGPS/Data/TNTBees/BeeTents/%s/PointFeature.dbf' % (trimble_path, field_name), dbf.getvalue())
    myzip.writestr('%s/AgGPS/Data/TNTBees/BeeTents/%s/PointFeature.shp' % (trimble_path, field_name), shp.getvalue())
    myzip.writestr('%s/AgGPS/Data/TNTBees/BeeTents/%s/PointFeature.shx' % (trimble_path, field_name), shx.getvalue())
    myzip.writestr('%s/spreadsheets/%s.csv' % (trimble_path, field_name), csvbuffer.getvalue().encode('utf-8'))



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

def calculate_spacing(radius, width, num_tents = None):
    """
        Calculate total length of all the passes from pivot
        point outward, every "width" meters (sprayer passes).
        Divide that by the number of acres in the circle to 
        calculate the distance between tents as a factor of
        the sprayer width.
    """
    c = 0
    total = 0
    while c < radius:
        total += math.sqrt(radius*radius-c*c)
        c += width

    total = total * 4 - radius*2
    if num_tents:
        return total / num_tents
    else:
        return total / (math.pi * radius * radius / 4046.87 + 1)

if __name__== "__main__":

    """
    # 2020 fields, now using CSV import instead of this
    data_items = [ 'Name', 'pivot_point', 'radius', 'edge_dist', 'seed_angle', 'lat_offset', 'pie_slice', 'width', 'extra' ]

    field_data = [ 
    #           ("RoyPederson",      (-111.7490278, 49.862     ),430, 395, 0, 4, None, 120 * 0.3048, {} ),
    #           ("RVR#1", (-111.795754,49.906374), 425, 0, 0, 2, None, 120 * 0.3048, {} ),
    #           ("RVR#2", (-111.783943,49.906380), 425, 0, 0, 2, None, 120 * 0.3048, {} ),
               ("RVR#9", (-111.806201,49.913440), 355, 0, 215, 2, (40,215), 120 * 0.3048, {} ),
               ("RVR#10", (-111.796499,49.917004), 477, 0, 74, 2, (94,254), 120 * 0.3048, {} ),

    #           ("DJensenNorth",     (-111.9406667, 49.8331944), 430, 395, 0, 4, None, 120 * 0.3048, {'eastlimit': 395} ),

    #           ("Giesbrecht",       (-112.0536667, 49.8694722), 420, 395, 0, 4, None, 120 * 0.3048, {'eastlimit': 395, 'exp_rows' : [-10,-8,-6,-4,-3,-2,-1,0,2,4,6,7,8,9,10]} ),

    #           ("JensenNE31-10-15", (-112.0199444, 49.8695278), 420, 395, 0, 4, None, 120 * 0.3048, {'eastlimit': 395} ),
    #           ("JensenSE25-10-16", (-112.0425556, 49.8479167), 430, 395, 0, 4, (295,245), 120 * 0.3048, {} ),

    #           ("StolkNE24-10-16",  (-112.0425833, 49.8404722), 430, 395,0, 4, None, 120 * 0.3048, {'exp_rows' : [-10,-8,-6,-4,-3,-2,-1,0,2,4,6,7,8,9,10]} ),

               ("LCTorrieNE33-10-13", (-111.703931, 49.869473), 427, 395, 90, 2, None, 118.5 * 0.3048, {'southlimit': 385, 'eastlimit':385, 'exp_rows' : [-10,-8,-6,-4,-3,-2,-1,0,2,4,6,7,8,9,10] } ),
               ("LCTorrieSE33-10-13", (-111.703815, 49.862154), 423, 395, 90, 4, None, 118.5*0.3048, {} ),
               ("LCTorrieN34-10-13",  (-111.686718, 49.865810), 820, 805, 0, 4, (270,90), 118.5 * 0.3048, { 'northlimit': 805, 'eastlimit': 780, } ),

    #           ("Lyle Ypma NW-27-9-14", (-111.833241, 49.768374), 585,0,  0, 4, (0,180), 132*0.3048, {} ),
               ("Lyle Ypma SW-20-10-13", (-111.737769,  49.832830), 387,0,  0, 4, (225,135), 132*0.3048, {} ),
    #           ("Lyle Ypma E 21-10-13", (-111.709377, 49.836641), 830,0,  0, 4, (0,180), 132*0.3048, {} ),


    #           ("Terry Lane SE-1-11-12", (-111.511168,49.876861), 430,394,  0, 4, None , 120*0.3048, {} ),
    #           ("Terry Lane NE-1-11-12", (-111.511232, 49.884128), 430,394,  0, 4, None , 120*0.3048, {} ),
    #           ("Terry Lane SW-12-11-12", (-111.522303,49.891291), 411,394,  0, 4, None , 120*0.3048, {} ),
    #           ("Terry Lane NE-12-11-12 try2", (-111.510798, 49.898546), 430,0,  0, 4, None , 120*0.3048, {} ),
    #           ("Terry Lane NE-12-11-12", (-111.5096496, 49.8977084), 320,0,  0, 4, None , 120*0.3048, {} ),

    #           ("Douwe Huizing SW-1-11-12", (-111.522241,49.876820), 415,395,  0, 4, None , 90*0.3048, {} ),
    #           ("Douwe Huizing SW-26-10-12", (-111.534194, 49.847407), 415,395,  0, 4, None , 90*0.3048, {} ),

    #           ("Reid Hopkins NE-26-10-12", (-111.523085, 49.854559), 415,395,  90, 4, None , 128*0.3048, {} ),
    #           ("Reid Hopkins SE-26-10-12", (-111.523124,  49.847380), 415,395,  90, 4, None , 128*0.3048, {} ),


            ]
    """
    
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('-z','--zip', type=str, help="create a zip file containing all generated files. Default is to write them to a directory.")
    parser.add_argument('-p','--path', type=str, help="write file tree relative to this path. If you specified a zip file, this path will must be relative and will be inside the zip file. If not specified, will write to a folder called TNT in the same folder as the csv file.")
    parser.add_argument('-t', '--timestamp', help="Add timestamp to zip filename or if not using zip, to the path specified.", action="store_true")
    parser.add_argument('csv_file', type=str, help="CSV file to read field information from.")

    args = parser.parse_args()


    fields = []
    with open(args.csv_file, 'r') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            # perform some type conversions, and validate some fields
            # and calculate convenience entries
            row['PP_Longitude'] = float(row['PP_Longitude'])
            row['PP_Latitude'] = float(row['PP_Latitude'])
            row['Radius'] = float(row['Radius'])
            row['Seed_angle'] = float(row['Seed_angle'])
            row['Sprayer_width'] = float(row['Sprayer_width']) * 0.3048 #metres/ft
            if row['Pie_start'] != '' and row['Pie_end'] != '':
                row['pie_slice'] = (int(row['Pie_start']),
                                    int(row['Pie_end']))
            else:
                row['pie_slice'] = None

            if row['North_limit'] != '':
                row['North_limit'] = int(row['North_limit'])
            else:
                row['North_limit'] = row['Radius']

            if row['East_limit'] != '':
                row['East_limit'] = int(row['East_limit'])
            else:
                row['East_limit'] = row['Radius']

            if row['South_limit'] != '':
                row['South_limit'] = int(row['South_limit'])
            else:
                row['South_limit'] = row['Radius']

            if row['West_limit'] != '':
                row['West_limit'] = int(row['West_limit'])
            else:
                row['West_limit'] = row['Radius']

            if row['Lateral_offset'] != '':
                row['Lateral_offset'] = float(row['Lateral_offset']) * 0.3048 #metres/ft
            else:
                row['Lateral_offset'] = 0

            if row['Female_bays_per_width'] != '':
                row['Female_bays_per_width'] = int(row['Female_bays_per_width'])

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


            fields.append(row)


    #for item in field_data:
    #    fields.append(dict(zip(data_items, item)))

    #zipfilename = '/tmp/beetents ' + datetime.date.today().isoformat() + ".zip"

    if not args.path:
        if not args.zip:
            # not using zip, place files in the same directory as the CSV file
            dirpath = os.path.join(os.path.dirname(os.path.abspath(args.csv_file)), "TNT")
        else:
            dirpath = "TNT"
    else:
        dirpath = args.path
        

    if args.zip:
        zipfilename = args.zip
        if args.timestamp:
            zipname_parts = os.path.splitext(zipfilename)
            zipfilename = "%s-%s%s" % (zipname_parts[0],datetime.date.today().isoformat(), zipname_parts[1])
            
        print ("Writing output to %s." % zipfilename)
        writer = zipfile.ZipFile(zipfilename, mode='w')
    else:
        if args.timestamp:
            dirpath = dirpath+"-"+datetime.date.today().isoformat()

        print ("Writing output to folder %s." % dirpath)
        writer = FileWriter()


    width, height = 612,792 #72 dpi

    # TODO place this is the zip file
    if args.zip:
        pdfpath = os.path.dirname(os.path.abspath(args.zip))
    else:
        pdfpath = dirpath
    if not os.path.exists(pdfpath):
        os.makedirs(pdfpath)
    surface = cairo.PDFSurface (os.path.join(pdfpath, "tntfields.pdf"), width, height)

    with writer:
        for field in fields:
            print ("Processing: %s (%f, %f)" % (field['Name'], field['PP_Longitude'], field['PP_Latitude']))
            ctx = cairo.Context (surface)
            make_pdf_circle_bays(ctx, field)

            make_files(writer, os.path.join(dirpath, "AgGPS/Data/TNTBees/BeeTents/%s" % field['Name']), 
                               field['Name'], 
                               (float(field['PP_Longitude']), float(field['PP_Latitude'])))

            make_tents(writer, dirpath, 
                              field['Name'],
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
                              pdf=ctx) 

            make_line(writer, os.path.join(dirpath, "AgGPS/Data/TNTBees/BeeTents/%s" % field['Name']), 
                             (field['PP_Longitude'], field['PP_Latitude']), 
                             field['Lateral_offset'], field['Seed_angle'])

            ctx.show_page()


