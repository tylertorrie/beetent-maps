# beetent_trimble

A program to generate the files needed for Trimble FMX (or similar) GPS
systems to mark out the position of bee tents on pivot-irrigated fields.
For each field described in a CSV file, it lays out a regular grid of tent
positions inside the pivot circle and writes Trimble field data (point
shapefiles, KML, AB-line, origin), a per-field GPS-position spreadsheet,
and a PDF map of every field.

## Running

The program is invoked as `maketentgrid.py`. It can be used either via a
simple Tk GUI or from the command line.

### GUI

Run `python3 maketentgrid.py` with no arguments. The window has:

- **Input file** — pick a CSV file describing the fields.
- **Use Metric for pivot radius and field boundaries** — when checked,
  `Radius`, the four edge limits, `Lateral_offset`, `spacing`, and
  `directional_offset` are interpreted as metres. When unchecked they are
  interpreted as feet. `Sprayer_width` is always in feet regardless of
  this setting.
- **Process** — generate the output files. Status and warnings are
  written to the text area below.

### Command line

```
maketentgrid.py [-z ZIP] [-p PATH] [-t] [-m] [csv_file]
```

- `csv_file` — CSV of fields to process. If omitted, the GUI is shown.
- `-z`, `--zip ZIP` — write all output into the named zip file instead
  of a folder tree.
- `-p`, `--path PATH` — root path for the output tree (or path inside
  the zip). Defaults to a folder named `TNT` next to the CSV file.
- `-t`, `--timestamp` — append today's date to the zip filename or
  output folder name.
- `-m`, `--metric` — interpret distances as metres instead of feet (same
  caveat as the GUI checkbox: sprayer width is still feet).

## CSV file format

The CSV has a header row, one row per field, and the following columns
(in this order):

| Column | Required | Units | Description |
|---|---|---|---|
| `Name` | yes | — | Field name. Used as the folder/file name for this field's output, so keep it filesystem-safe. |
| `PP_Longitude` | yes | decimal degrees | Pivot point longitude (negative for west). |
| `PP_Latitude` | yes | decimal degrees | Pivot point latitude. |
| `Radius` | yes | feet (or metres with `-m`) | Maximum reach of the pivot from the pivot point. |
| `Seed_angle` | no | degrees | Rotation of the tent grid relative to north. Blank is treated as 0 with a warning. |
| `Lateral_offset` | no | feet (or metres with `-m`) | Sideways shift of the first row of tents from the centre line. If blank and `Female_bays_per_width` is given, it is auto-computed to land just inside the male bay. Otherwise defaults to 0. |
| `Sprayer_width` | yes | **feet (always)** | Distance between sprayer passes — also the row spacing for tents. |
| `Pie_start` | no | degrees | Start angle of the planted arc. Leave blank with `Pie_end` to plant the entire circle. |
| `Pie_end` | no | degrees | End angle of the planted arc. Arcs may cross 0°/360°. |
| `North_limit` | no | feet (or metres with `-m`) | Distance from the pivot point to the north field edge. Blank means use `Radius`. |
| `East_limit` | no | feet (or metres with `-m`) | Distance to the east edge. Blank means use `Radius`. |
| `South_limit` | no | feet (or metres with `-m`) | Distance to the south edge. Blank means use `Radius`. |
| `West_limit` | no | feet (or metres with `-m`) | Distance to the west edge. Blank means use `Radius`. |
| `Female_bays_per_width` | no | count | Number of female bays per sprayer width. Used both to draw the male bays on the PDF map and, when `Lateral_offset` is blank, to place the first tent row just to the right of a male bay. |
| `Experimental` | no | comma-separated integers | Explicit list of row numbers to place tents on, instead of every row across the circle. Quote the cell if your CSV editor would otherwise split it (e.g. `"-2,-1,0,1,2"`). |
| `Experimental_start_odd` | no | any non-empty value | If non-empty, the first experimental row is treated as odd (half-spacing shift); otherwise it starts even. |
| `# of Structures` | no | count | Target number of tents in the field. If a pie slice is given, this is extrapolated to a full-circle equivalent before computing spacing. Blank falls back to roughly one tent per acre. |
| `spacing` | no | feet (or metres with `-m`) | Explicit distance between tents along a row. If blank, computed from `# of Structures` (or one-per-acre). |

No sample CSV ships with this repository — supply your own.

## Output

Output is written either to a folder (default `TNT/` next to the CSV file,
or whatever `-p` / `--path` specifies) or, with `-z`, into a zip file.

Only the files under `AgGPS/` are for the Trimble FMX — copy that subtree
onto the FMX. The other outputs are for humans and other tools:

- `TNTFields.pdf` — printable hard-copy map of every field.
- `googleearth/<Name>.kml` — open in Google Earth to review tent layout.
- `spreadsheets/<Name>.csv` — imported by the TNT Pollination phone app.

```
<path>/
    TNTFields.pdf
    googleearth/<Name>.kml
    spreadsheets/<Name>.csv
    AgGPS/Data/TNTBees/BeeTents/<Name>/    <- copy this to the FMX
        origin.kml                         pivot-point origin
        <lon>E<lat>N761H.pos               position marker
        newField.ok                        field marker
        PointFeature.shp / .shx / .dbf     tent positions
        Swaths.shp / .shx / .dbf           AB line
        Swaths.kml                         AB line (KML)
```

## Building a self-contained binary

The `nuitka_compile.sh` shell script uses [Nuitka](https://nuitka.net/)
to build a standalone, self-contained version of the program that does
not require Python to be installed on the target machine. Run it from
the project directory:

```
./nuitka_compile.sh
```

On Windows, run it from a Git Bash shell. The build produces
`maketentgrid.exe` together with its supporting files in a folder
called `beetent_trimble/` (the script renames Nuitka's `maketentgrid.dist`
output). Zip that folder to distribute.

The script refuses to run if a `beetent_trimble/` folder already exists;
remove it before rebuilding.
