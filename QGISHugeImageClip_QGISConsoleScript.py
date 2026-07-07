import subprocess, os, glob, time, shutil, re
from pathlib import Path
from qgis.core import QgsRasterLayer
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
startTime = time.time()


"""
##########################################################
User options
"""

#Variable assignment
approxPixelsPerTile     = 4000
compressOptions         = 'COMPRESS=ZSTD|NUM_THREADS=ALL_CPUS|PREDICTOR=1|ZSTD_LEVEL=1|BIGTIFF=IF_SAFER|TILED=YES'
gdalOptions             = '-co COMPRESS=ZSTD -co ZSTD_LEVEL=1 -co PREDICTOR=2 -co NUM_THREADS=ALL_CPUS -co BIGTIFF=IF_SAFER -co TILED=YES --config GDAL_NUM_THREADS ALL_CPUS -multi -wo NUM_THREADS=ALL_CPUS -overwrite'
gdalOptionsFinal        = '-co COMPRESS=WEBP -co WEBP_LEVEL=75 -co PREDICTOR=2 -co NUM_THREADS=ALL_CPUS -co BIGTIFF=IF_SAFER -co TILED=YES -multi --config GDAL_NUM_THREADS ALL_CPUS -wo NUM_THREADS=ALL_CPUS -overwrite'

#Prompting the user to choose an image and clipping vector
inImage = QFileDialog.getOpenFileName(None, "Select image file", "", "Raster Files (*.tif *.tiff *.ecw *.jp2)")[0]
clippingVector = QFileDialog.getOpenFileName(None, "Select clipping vector", "", "Vector Files (*.gpkg *.shp *.geojson)")[0]

"""
##################################################################################
Variable assignment for processing
"""

#Define the location of gdal and make sure its windows don't appear a hundred times
gdalwarpExe = str(Path(QgsApplication.prefixPath()).parent.parent / 'bin' / 'gdalwarp.exe')
gdalTranslateExe = str(Path(QgsApplication.prefixPath()).parent.parent / 'bin' / 'gdal_translate.exe')
gdalOverviewsExe = str(Path(QgsApplication.prefixPath()).parent.parent / 'bin' / 'gdaladdo.exe')
startupinfo = subprocess.STARTUPINFO()
startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
startupinfo.wShowWindow = subprocess.SW_HIDE

#Get the location of the initial image for storage of processing files
rootProcessDirectory = str(Path(inImage).parent.absolute()).replace('\\','/') + '/'

#Set up the layer name for the raster calculations
inImageName = Path(inImage).stem[:13]
outImageName = inImageName

#Make folders for processing
processDirectoryInstance        = rootProcessDirectory + inImageName + 'Process' + '/'
processDirectory                = processDirectoryInstance + '1Main/'
processExtentBoundsDirectory    = processDirectoryInstance + '2TileExtentBounds/'
processClipBoundsDirectory      = processDirectoryInstance + '3TileClipBounds/'
processTileDirectory            = processDirectoryInstance + '4Tiles/'
processMosaicStagingDirectory   = processDirectoryInstance + '5MosaicStaging/'
finalImageDir                   = processDirectoryInstance + '6Final/'

#Creating all the subfolders
if not os.path.exists(processDirectoryInstance):        os.mkdir(processDirectoryInstance)
if not os.path.exists(processDirectory):                os.mkdir(processDirectory)
if not os.path.exists(processExtentBoundsDirectory):    os.mkdir(processExtentBoundsDirectory)
if not os.path.exists(processClipBoundsDirectory):      os.mkdir(processClipBoundsDirectory)
if not os.path.exists(processTileDirectory):            os.mkdir(processTileDirectory)
if not os.path.exists(processMosaicStagingDirectory):   os.mkdir(processMosaicStagingDirectory)
if not os.path.exists(finalImageDir):                   os.mkdir(finalImageDir)

"""
####################################################################################
Final preps for processing
"""

#Get the pixel size and coordinate system of the raster
ras = QgsRasterLayer(inImage)
pixelSizeX = ras.rasterUnitsPerPixelX()
pixelSizeY = ras.rasterUnitsPerPixelY()
pixelSizeAve = (pixelSizeX + pixelSizeY) / 2
coordinateSystem = ras.crs().authid()

#Clear out the folders
for folder in [processDirectory, processExtentBoundsDirectory, processClipBoundsDirectory, processTileDirectory]:
    for file in glob.glob(folder + '*'):
        try:
            os.remove(file)
        except BaseException as e:
            print(e)

"""
###############################################################################################
Get all of the tile extents sorted out
"""

#Determine the extent and coordinate system of the extent
processing.run("native:polygonfromlayerextent", {'INPUT':inImage,'ROUND_TO':0,'OUTPUT':processDirectory + inImageName + 'ImageExtent.gpkg'})
extentVector = QgsVectorLayer(processDirectory + inImageName + 'ImageExtent.gpkg')
extentRectangle = extentVector.extent()
extentCrs = extentVector.sourceCrs()

#Then close the layer object so that QGIS doesn't unnecessarily hold on to it
QgsProject.instance().addMapLayer(extentVector, False)
QgsProject.instance().removeMapLayer(extentVector.id())

#Create a grid for dividing the image up into tiles
processing.run("native:creategrid", {'TYPE':2,'EXTENT':extentRectangle,'HSPACING':pixelSizeX * approxPixelsPerTile,
    'VSPACING':pixelSizeY * approxPixelsPerTile,'HOVERLAY':0,'VOVERLAY':0,'CRS':extentCrs,
    'OUTPUT':processDirectory + inImageName + 'ImageExtentGrid.gpkg'})

#Clip this so we're not overrunning into areas where the image isn't
processing.run("native:clip", {'INPUT':processDirectory + inImageName + 'ImageExtentGrid.gpkg',
    'OVERLAY':processDirectory + inImageName + 'ImageExtent.gpkg', 'OUTPUT':processDirectory + inImageName + 'ImageExtentGridClip.gpkg'})

#Only keep the tiles that actually touch the clipping vector
processing.run("native:extractbylocation", {'INPUT':processDirectory + inImageName + 'ImageExtentGridClip.gpkg',
    'PREDICATE':[0],'INTERSECT':clippingVector,'OUTPUT':processDirectory + inImageName + 'ImageExtentGridClipGrabbed.gpkg'})

#Build a unique id with the row number defined
processing.run("native:fieldcalculator", {'INPUT':processDirectory + inImageName + 'ImageExtentGridClipGrabbed.gpkg','FIELD_NAME':'TileId',
    'FIELD_TYPE':2,'FIELD_LENGTH':0,'FIELD_PRECISION':0,'FORMULA':' ("row_index"+1)|| \'_\' ||("col_index" +1)','OUTPUT':processDirectory + inImageName + 'ImageExtentGridClipGrabbedIded.gpkg'})

#Split out the tiles into individual files to define the extent of the tiles
processing.run("native:splitvectorlayer", {'INPUT':processDirectory + inImageName + 'ImageExtentGridClipGrabbedIded.gpkg',
    'FIELD':'TileId','FILE_TYPE':0,'OUTPUT':processExtentBoundsDirectory})

#Now clip the grid so that it's only the area we actually want
processing.run("native:clip", {'INPUT':processDirectory + inImageName + 'ImageExtentGridClipGrabbedIded.gpkg',
    'OVERLAY':clippingVector, 'OUTPUT':processDirectory + inImageName + 'ImageExtentGridClipGrabbedIdedClip.gpkg'})

#Split it out so there is a different extent to work from for each instance of the raster clipping
processing.run("native:splitvectorlayer", {'INPUT':processDirectory + inImageName + 'ImageExtentGridClipGrabbedIdedClip.gpkg',
    'FIELD':'TileId','FILE_TYPE':0,'OUTPUT':processClipBoundsDirectory})

"""
#################################################################################################
Slice up the raster based on the tiles and the cutting vector
"""

boundsFiles = glob.glob(processClipBoundsDirectory + '/*.gpkg')

def clipTile(boundClipFile):
    try:
        boundName = Path(boundClipFile).stem
        outputFile = processTileDirectory + boundName + 'Tile.tif'

        #Get the extent of the tile
        tileExtentVector = QgsVectorLayer(processExtentBoundsDirectory + boundName + '.gpkg')
        tileExtentRectangle = tileExtentVector.extent()

        #Prep a line for cmd where we are clipping the content to the clip vector, but the extent to the whole tile
        cmdLine = (gdalwarpExe + ' -cutline "' + boundClipFile + '" -of GTiff -te ' +
            str(tileExtentRectangle.xMinimum()) + " " + str(tileExtentRectangle.yMinimum()) + " " + str(tileExtentRectangle.xMaximum()) + " " + str(tileExtentRectangle.yMaximum()) +
            " " + gdalOptions + ' "' + inImage + '" "' + outputFile + '"')
        
        #Run the cmd line, hopefully with no errors
        result = subprocess.run(cmdLine, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, startupinfo=startupinfo)
        print(result.stderr, flush=True)
        print(result.stdout, flush=True)
        
        #Sometimes dumbass handles screw things up
        QgsProject.instance().addMapLayer(tileExtentVector, False)
        QgsProject.instance().removeMapLayer(tileExtentVector.id())
    
    except BaseException as e:
        print(boundName + " error: " + str(e))

#Run in parallel with threads so we get heaps done at once
with ThreadPoolExecutor(max_workers=8) as executor:
    for result in executor.map(clipTile, boundsFiles):
        print('Tile done', flush=True)

print("All tiles clipped")

"""
###################################################################################################################
Mosaic staging, so that we can leverage multi threading to get even closer to the final image
"""

#Get a list of the tiles
boundsFiles = glob.glob(processTileDirectory + '/*.tif')

#Go through all of the tiles and group them up by the row id
groups = {}
for f in boundsFiles:
    match = re.search(r"TileId_(\d+)_", f)
    if match:
        key = int(match.group(1))
        groups.setdefault(key, []).append(f)
boundsFilesLists = [groups[k] for k in sorted(groups)]

#Define a function to mosaic each row of tiles together
def firstStageMosaic(fileList):
    baseName = os.path.splitext(os.path.basename(fileList[0]))[0]
    firstStageMosaicOutput = processMosaicStagingDirectory + baseName + '.tif'

    #Make a list of all the input files
    inputFiles = " ".join(['"' + f + '"' for f in fileList])
    cmdLine = 'gdalwarp -of GTiff ' + gdalOptions + ' ' + inputFiles + ' "' + firstStageMosaicOutput + '"'
    
    #Run the cmd
    result = subprocess.run(cmdLine, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, startupinfo=startupinfo)
    print(result.stderr, flush=True)
    print(result.stdout, flush=True)

#Thread it up
with ThreadPoolExecutor(max_workers=8) as executor:
    for _ in executor.map(firstStageMosaic, boundsFilesLists):
        print("Batch done", flush=True)

"""
#############################################################################################
Finally bring it all together into a final mosaic
"""

#Prepare to make a final mosaic where the alpha bands are respected
finalImageDir = finalImageDir.replace("/", "\\")
processMosaicStagingDirectory = processMosaicStagingDirectory.replace("/", "\\")

#Final mosaicking of the tiles
finalImage = finalImageDir + outImageName + datetime.now().strftime("%Y%m%d%H%M") + '.tif'
cmdLine = 'gdalwarp -of GTiff ' + gdalOptionsFinal + ' "' + processMosaicStagingDirectory + '**.tif" "' + finalImage + '" & timeout 5'
os.system(cmdLine)

#Build pyramids
print("Final image clipped, building pyramids")
subprocess.Popen([gdalOverviewsExe, "--config", "COMPRESS_OVERVIEW", "WEBP", "--config", "WEBP_LEVEL_OVERVIEW", "50", "-r", "cubic", "-ro", finalImage],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.DETACHED_PROCESS)

