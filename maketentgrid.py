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

    num_tents=kwargs.get('num_tents', None)

    if kwargs['spacing'] is not None:
        spacing = kwargs['spacing']  #float(field['spacing']) * conv
    else:
        spacing = calculate_spacing(radius, width, num_tents = num_tents)

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

    directional_offset = kwargs['directional_offset']
    #if directional_offset:
    #    directional_offset = float(directional_offset) * conv
    #else:
    #    directional_offset = 0

    for r in rows:
        if not exp_rows:
            odd = r % 2
        #print (odd)
        for c in range(-int(radius / spacing)-1, int(radius / spacing) + 1):
            # only place tents in specified quadrants of circle

            if odd: #odd, shift by half spacing
                east = r*width + lat_shift
                north = c*spacing+ spacing/2 + directional_offset
            else: #even, don't shift
                east = r*width + lat_shift
                north = c*spacing + directional_offset

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
                    pdf.set_draw_color(255,0,0)
                    pdf.set_fill_color(255,0,0)
                    pdf.ellipse(east * scale + pdf_width/2-2 , -north * scale + pdf_height/2-2, 4,4,'DF')

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
                    row['Sprayer_width'] = float(row['Sprayer_width']) * conv
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
            print ("Writing output to folder %s." % dirpath)
            print ()
            writer = FileWriter()


        width, height = 612,792 #72 dpi

        # TODO place this is the zip file
        if args.zip:
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
                    self.fileinput["borderwidth"] = "1px"
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

                    processbutton=tk.Button(root)
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
                    self.outputtext["borderwidth"] = "2px"
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

                def process_command(self):
                    filename = self.fileinput.get()

                    output = process_csvfile(filename, None, None, None).getvalue()

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
