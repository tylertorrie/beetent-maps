#!/usr/bin/python3

import utmish
import simplekml
import shapefile
import math
import sys
import zipfile
import datetime
from io import BytesIO

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

def make_tents(myzip, field_path, pid, pivotpoint, radius, width, spacing, lat_shift, angle):
    # create starting point for grid
    easting, northing = utmish.from_lonlat(pivotpoint[0], pivotpoint[1], pivotpoint[0])
    radius_sqr = radius * radius

    rotate = 0 - angle
    rotate = (rotate + 180) % 360 - 180
    #rotate = -rotate


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

    for c in range(-int(radius / spacing)-1, int(radius / spacing) + 1):
        for r in range(-int(radius / width),int(radius / width) + 1):
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
                #if (r % 2 and r*r*width*width + (c*spacing+spacing/2)*(c*spacing+spacing/2) <= radius_sqr or
                #    not r %2 and r*r*width*width + (c*spacing)*(c*spacing) <= radius_sqr):

                lon, lat = utmish.to_lonlat(east + easting, north + northing, pivotpoint[0])
                #print (lat, lon)
                kml.newpoint (name="", coords = [ (lon, lat) ])

                w.record(Date=datetime.date.today(), Time="12:00:00pm",Version="7.78.002",
                         Id = pid, Name = "Tree_%d" % pid, Latitude = lat, Longitude = lon, Height = 761.064,
                         AlarmRad = 0, WarningRad = 10.0, Status_Text='', Visible=1)

                w.point(lon, lat)

                pid += 1

    myzip.writestr("%s/PointFeature.kml" % field_path, kml.kml())    
    w.close()
    myzip.writestr('%s/PointFeature.dbf' % field_path, dbf.getvalue())
    myzip.writestr('%s/PointFeature.shp' % field_path, shp.getvalue())
    myzip.writestr('%s/PointFeature.shx' % field_path, shx.getvalue())

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

#pivotpoint = (-111.7149103932853,49.86944210874295)
pivotpoint = (-111.744016117205,49.87662108238306) # david's yard
pivotpoint = (-111.727547,  49.876926)
easting, northing = utmish.from_lonlat(pivotpoint[0], pivotpoint[1], pivotpoint[0])
lateral_offset = 3 # meters to shift tracks sideways so it won't hit the pivot point dead on.
width = 120 * 0.3048 # 120' in metres
spacing = width * 3
rotate = 15

with zipfile.ZipFile("/tmp/test.zip", mode='w') as myzip:
    make_files(myzip, "Client/Farm/Field", "030620", pivotpoint)
    make_tents(myzip, "Client/Farm/Field", 3062, pivotpoint, 435, width, spacing, lateral_offset, rotate) 
    make_line(myzip, "Client/Farm/Field", pivotpoint, lateral_offset, rotate)


sys.exit(0)


diameter = 426 # approximately one pivot circle
diamsqr = diameter * diameter

kml = simplekml.Kml()
pid = 3502
print( "Date,Time,Version,Id,Name,Latitude,Longitude,Height,AlarmRad,WarningRad,Status_Txt,Visible")
for c in range(-int(diameter / spacing), int(diameter / spacing) + 1):
    for r in range(-int(diameter / width),int(diameter / width) + 1):
        if r*r*width*width + (c*spacing+width)*(c*spacing+width) <= diamsqr:
            if r % 2: #odd, shift by half spacing
                east = r*width
                north = c*spacing+spacing/2
            else: #even, don't shift
                east = r*width
                north = c*spacing

            if rotate:
                east1 = east * math.cos(math.radians(rotate)) - north * math.sin(math.radians(rotate))
                north1 = north * math.cos(math.radians(rotate)) + east * math.sin(math.radians(rotate))
                east = east1
                north = north1

            lat, lon = utmish.to_lonlat(east + easting, north + northing, pivotpoint[0])
            #print (lat, lon)
            kml.newpoint (name="", coords = [ (lon, lat) ])
            print ("2020-03-05,02:54:16pm,7.78.002,", end="")
            print ('"%d",Tree_%d,%0.7f,%0.7f,761.064,0.0000,10.0000,,"1"' % (pid, pid, lat, lon))
            pid += 1


