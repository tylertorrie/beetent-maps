#!/usr/bin/python3

import utmish
import simplekml
import shapefile
import math
import sys
import zipfile
import datetime
import csv
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

def make_tents(myzip, field_path, pid, pivotpoint, radius, width, spacing, lat_shift, angle, quadrants = None, **kwargs):
    if quadrants is None:
        quadrants = [ 0, 1, 2, 3] #nw, ne, se, sw

    eastlimit = kwargs.get('eastlimit',None)
    northlimit = kwargs.get('northlimit', None)
    westlimit = kwargs.get('westlimit', None)
    southlimit = kwargs.get('southlimit', None)

    # create starting point for grid
    print (pivotpoint)
    easting, northing = utmish.from_lonlat(pivotpoint[0], pivotpoint[1], pivotpoint[0])
    radius_sqr = radius * radius

    rotate = 0 - angle
    rotate = (rotate + 180) % 360 - 180
    #rotate = -rotate

    margin = width / 2.0


    kml = simplekml.Kml()

    shp = BytesIO()
    shx = BytesIO()
    dbf = BytesIO()

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
    csvdata = csv.DictWriter(csvbuffer, fieldnames = [ 'Latitude', 'Longitude'])
    csvdata.writeheader()

    for c in range(-int(radius / spacing)-1, int(radius / spacing) + 1):
        for r in range(-int(radius / width),int(radius / width) + 1):
            # only place tents in specified quadrants of circle

            if r % 2: #odd, shift by half spacing
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

                flag = False
                if east < margin and north >= -margin and 0 in quadrants:
                    flag = True

                if east >= -margin and north >= -margin and 1 in quadrants:
                    flag = True

                if east >= -margin and north < margin and 2 in quadrants:
                    flag = True

                if east < margin and north < margin and 3 in quadrants:
                    flag = True

                if not flag:
                    # no specified quadrant claims this tent, so skip
                    continue

                #if (r % 2 and r*r*width*width + (c*spacing+spacing/2)*(c*spacing+spacing/2) <= radius_sqr or
                #    not r %2 and r*r*width*width + (c*spacing)*(c*spacing) <= radius_sqr):

                lon, lat = utmish.to_lonlat(east + easting, north + northing, pivotpoint[0])
                #print (lat, lon)
                kml.newpoint (name="", coords = [ (lon, lat) ])

                w.record(Date=datetime.date.today(), Time="12:00:00pm",Version="7.78.002",
                         Id = pid, Name = "Tree_%d" % pid, Latitude = lat, Longitude = lon, Height = 761.064,
                         AlarmRad = 0, WarningRad = 10.0, Status_Text='', Visible=1)

                w.point(lon, lat)

                csvdata.writerow( {'Latitude': '%.7f' % lat, 'Longitude': '%.7f' % lon} )

                pid += 1

    myzip.writestr("%s/TentLocations.kml" % field_path, kml.kml())    
    w.close()
    myzip.writestr('%s/PointFeature.dbf' % field_path, dbf.getvalue())
    myzip.writestr('%s/PointFeature.shp' % field_path, shp.getvalue())
    myzip.writestr('%s/PointFeature.shx' % field_path, shx.getvalue())
    myzip.writestr('%s/TentLocations.csv' % field_path, csvbuffer.getvalue().encode('utf-8'))



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

fields = [ ("RoyPederson",      (-111.7490278, 49.862     ),430, 0, [0,1,2,3], {} ),
           ("DJensenNorth",     (-111.9406667, 49.8331944), 430, 0, [0,1,2,3], {'eastlimit': 395} ),
           ("Giesbrecht",       (-112.0536667, 49.8694722), 420, 0, [0,1,2,3], {'eastlimit': 395} ),
           ("JensenNE31-10-15", (-112.0199444, 49.8695278), 420, 0, [0,1,2,3], {'eastlimit': 395} ),
           ("JensenSE25-10-16", (-112.0425556, 49.8479167), 430, 0, [1,2,3], {} ),
           ("StolkNE24-10-16",  (-112.0425833, 49.8404722), 430, 0, [0,1,2,3], {} ),
           ("LCTorrieNE33-10-13", (-111.703931, 49.869473), 430, 90, [0,1,2,3], {'southlimit': 385 } ),
           ("LCTorrieSE33-10-13", (-111.703815, 49.862154), 430, 90, [0,1,2,3], {} ),
           ("LCTorrieN34-10-13",  (-111.686718, 49.865810), 840, 0, [0,1], { 'northlimit': 805,
                                                                             'eastlimit': 802} ),

        ]

lateral_offset = 4 # meters to shift tracks sideways so it won't hit the pivot point dead on.
width = 120 * 0.3048 # 120' in metres
spacing = width * 3
rotate = 0

with zipfile.ZipFile("/tmp/test.zip", mode='w') as myzip:
    for field in fields:
        make_files(myzip, "AgGPS/Data/BeeStuff/BeeTents/%s" % field[0], "030620", field[1])
        make_tents(myzip, "AgGPS/Data/BeeStuff/BeeTents/%s" % field[0], 3062, 
                   pivotpoint=field[1], radius=field[2], width=width, spacing=spacing, 
                   lat_shift=lateral_offset, angle=field[3], quadrants=field[4], **field[5]) 
        make_line(myzip, "AgGPS/Data/BeeStuff/BeeTents/%s" % field[0], field[1], lateral_offset, field[3])

