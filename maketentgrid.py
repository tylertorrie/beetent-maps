#!/usr/bin/python3

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

def make_pdf(myzip, pdf_file, field):

    scale = 8*72 / field['radius'] / 2

    width, height = 612,792 #72 dpi
    surface = cairo.PDFSurface (pdf_file, width, height)
    ctx = cairo.Context (surface)
    ctx.set_line_width(0.5)
    ctx.set_source_rgb(1,1,1)
    ctx.rectangle(0,0,width,height)
    ctx.fill()
    ctx.set_source_rgb(0,0,0)

    if field['edge_dist']:
        northlimit=southlimit=eastlimit=westlimit=field['edge_dist']
    else:
        if 'northlimit' in field['extra']:
            northlimit = field['extra']['northlimit']
        else:
            northlimit = field['radius']

        if 'eastlimit' in field['extra']:
            eastlimit = field['extra']['eastlimit']
        else:
            eastlimit = field['radius']

        if 'southlimit' in field['extra']:
            southlimit = field['extra']['southlimit']
        else:
            southlimit = field['radius']

        if 'westlimit' in field['extra']:
            westlimit = field['extra']['westlimit']
        else:
            print ("beep")
            westlimit = field['radius']

    ctx.arc(width/2, height/2, 2, 0, 2*math.pi)
    ctx.fill()

    if field['pie_slice']:
        start_angle = 360 - field['pie_slice'][0] + 90
        end_angle = 360 - field['pie_slice'][1] + 90
        print ("start_angle: %d, end angle: %d" % (start_angle, end_angle) )
        x = math.cos(math.radians(start_angle)) * field['radius']
        y = math.sin(math.radians(start_angle)) * field['radius']
        if x > eastlimit: x=eastlimit
        elif x < -westlimit: x = -westlimit
        
        if y > northlimit: y = northlimit
        elif y < -southlimit: y = -southlimit

        ctx.move_to(x * scale + width/2,
                    -y * scale + height/2)

        ctx.line_to(width/2, height/2)

        x = math.cos(math.radians(end_angle)) * field['radius']
        y = math.sin(math.radians(end_angle)) * field['radius']
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

    x = math.cos(math.radians(start_angle)) * field['radius']
    y = math.sin(math.radians(start_angle)) * field['radius']
    if x > eastlimit: x=eastlimit
    elif x < -westlimit: x = -westlimit
    
    if y > northlimit: y = northlimit
    elif y < -southlimit: y = -southlimit
    ctx.move_to(x * scale + width/2,
                -y * scale + height/2)

    # if arc crosses 0, adjust

    angle = start_angle
    while True:
        x = math.cos(math.radians(angle)) * field['radius']
        y = math.sin(math.radians(angle)) * field['radius']
        if x > eastlimit: x=eastlimit
        elif x < -westlimit: x = -westlimit
        
        if y > northlimit: y = northlimit
        elif y < -southlimit: y = -southlimit

        ctx.line_to(x * scale + width/2,
                    -y * scale + height/2)

        angle -= 1
        if angle < end_angle: break
     
    ctx.stroke()


    radius = field['radius']
    radius_sqr = radius * radius
    sprayer_width = field['width']
    spacing = calculate_spacing(radius, sprayer_width)
    rotate = 0 - field['seed_angle']
    rotate = (rotate + 180) % 360 - 180
    pie_slice = field['pie_slice']
    lat_shift = field['lat_offset']
    bays_per_sprayer_width = 3

    # Draw male bays
    ctx.set_source_rgb(0,0,0.9)
    rows = range(-int(radius / sprayer_width * bays_per_sprayer_width ),int(radius / sprayer_width * bays_per_sprayer_width) + 1)
    for r in rows:
        east = r * sprayer_width / bays_per_sprayer_width + sprayer_width / bays_per_sprayer_width / 2
        north = radius

        east1 = east * math.cos(math.radians(rotate)) - north * math.sin(math.radians(rotate))
        north1 = north * math.cos(math.radians(rotate)) + east * math.sin(math.radians(rotate))

        east2 = east * math.cos(math.radians(rotate)) + north * math.sin(math.radians(rotate))
        north2 = -north * math.cos(math.radians(rotate)) + east * math.sin(math.radians(rotate))

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


    # Draw tents
    ctx.set_source_rgb(1,0,0)
    rows = range(-int(radius / sprayer_width),int(radius / sprayer_width) + 1)
        
    for r in rows:
        #if not exp_rows:
        #    odd = r % 2
        odd = r % 2
        #print (odd)
        for c in range(-int(radius / spacing)-1, int(radius / spacing) + 1):
            # only place tents in specified quadrants of circle

            if odd: #odd, shift by half spacing
                east = r*sprayer_width + lat_shift
                north = c*spacing+spacing/2
            else: #even, don't shift
                east = r*sprayer_width + lat_shift
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

                ctx.arc(east * scale + width/2 , -north * scale + height/2, 2,0,2*math.pi)
                ctx.fill()



    ctx.show_page()


def make_tents(myzip, trimble_path, field_name, pivotpoint, radius, edge_dist, width, lat_shift, angle, pie_slice = None, **kwargs):
    """
        pivotpoint is tuple of longitude, latitude
        radius is maximum radius of circle in metres
        edge_dist is distance to north, south, west, or east edges when end gun is off, if pivot has end gun
        width is sprayer width or distance between rows of tents
        lat_shift is distance from center line to place first row of tents (usually a couple of meters)
        angle is seeding angle

        quadrants is list of quadrants of circle to place tents in (replace with arc)
    """

    field_path = trimble_path + "/" + field_name

    eastlimit = northlimit = westlimit = southlimit = edge_dist

    eastlimit = kwargs.get('eastlimit',eastlimit)
    northlimit = kwargs.get('northlimit', northlimit)
    westlimit = kwargs.get('westlimit', westlimit)
    southlimit = kwargs.get('southlimit', southlimit)

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

    exp_rows = False
    if 'exp_rows' in kwargs:
        exp_rows = True
        rows = kwargs['exp_rows'] #[-10,-8,-6,-4,-3,-2,-1,0,2,4,6,7,8,9,10]
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

    myzip.writestr("TNT/googleearth/%s.kml" % field_name, kml.kml())    
    w.close()
    myzip.writestr('%s/PointFeature.dbf' % field_path, dbf.getvalue())
    myzip.writestr('%s/PointFeature.shp' % field_path, shp.getvalue())
    myzip.writestr('%s/PointFeature.shx' % field_path, shx.getvalue())
    myzip.writestr('TNT/spreadsheets/%s.csv' % field_name, csvbuffer.getvalue().encode('utf-8'))



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

data_items = [ 'name', 'pivot_point', 'radius', 'edge_dist', 'seed_angle', 'lat_offset', 'pie_slice', 'width', 'extra' ]

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

fields = []
for item in field_data:
    fields.append(dict(zip(data_items, item)))


lateral_offset = 0 # meters to shift tracks sideways so it won't hit the pivot point dead on.
width = 120 * 0.3048 # 120' in metres
#width = 132 * 0.3048 # 120' in metres
#width = 90 * 0.3048 # 120' in metres
#width = 128 * 0.3048 # 120' in metres
#spacing = width * 3 # 120 feet
#spacing = width * 2.5 # 132 feet
#spacing = width * 5.4 # 90 feet
#spacing = width * 2.65 # 128 feet


rotate = 0

calculate_spacing(430,132*0.3048)

zipfilename = '/tmp/beetents ' + datetime.date.today().isoformat() + ".zip"
print(zipfilename)
with zipfile.ZipFile(zipfilename, mode='w') as myzip:
    for field in fields:
        print (field)
        make_files(myzip, "TNT/AgGPS/Data/BeeStuff/BeeTents/%s" % field['name'], field['name'], field['pivot_point'])
        make_tents(myzip, "TNT/AgGPS/Data/BeeStuff/BeeTents/", field['name'],  
                   pivotpoint=field['pivot_point'], radius=field['radius'], edge_dist = field['edge_dist'], width=field['width'],  
                   lat_shift=field['lat_offset'], angle=field['seed_angle'], pie_slice=field['pie_slice'], **field['extra']) 
        make_line(myzip, "TNT/AgGPS/Data/BeeStuff/BeeTents/%s" % field['name'], field['pivot_point'], field['lat_offset'], field['seed_angle'])
        make_pdf(myzip, "TNT/%s.pdf" % field['name'], field)

