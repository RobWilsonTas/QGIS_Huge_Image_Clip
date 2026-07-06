import numpy, subprocess, os, glob, time, shutil
from pathlib import Path
from qgis.core import QgsRasterLayer
from qgis.PyQt.QtWidgets import QMessageBox
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
gdalOptionsFinal        = '-co COMPRESS=WEBP -co WEBP_LEVEL=75 -co PREDICTOR=2 -co NUM_THREADS=ALL_CPUS -co BIGTIFF=IF_SAFER -co TILED=YES -multi --config GDAL_NUM_THREADS ALL_CPUS -wo NUM_THREADS=ALL_CPUS -overwrite'

#Prompting the user to choose an image and clipping vector
inImage = QFileDialog.getOpenFileName(None, "Select image file", "", "Raster Files (*.tif *.tiff *.ecw *.jp2)")[0]
clippingVector = QFileDialog.getOpenFileName(None, "Select clipping vector", "", "Vector Files (*.gpkg *.shp *.geojson)")[0]

"""
##########################################################
Variable assignment for processing
"""

#Define the location of gdal and make sure its windows don't appear a hundred times
gdalwarpExe = str(Path(QgsApplication.prefixPath()).parent.parent / 'bin' / 'gdalwarp.exe')
gdalTranslateExe = str(Path(QgsApplication.prefixPath()).parent.parent / 'bin' / 'gdal_translate.exe')
startupinfo = subprocess.STARTUPINFO()
startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
startupinfo.wShowWindow = subprocess.SW_HIDE

#Get the location of the initial image for storage of processing files
rootProcessDirectory = str(Path(inImage).parent.absolute()).replace('\\','/') + '/'

#Set up the layer name for the raster calculations
inImageName = Path(inImage).stem[:8]
outImageName = inImageName

#Making a folder for processing each time, to avoid issues with locks
processDirectoryInstance = rootProcessDirectory + inImageName + 'Process' + '/'

#Creating all the subfolder variables
processDirectory                = processDirectoryInstance + '1Main/'
processExtentBoundsDirectory    = processDirectoryInstance + '2TileExtentBounds/'
processClipBoundsDirectory      = processDirectoryInstance + '3TileClipBounds/'
processTileDirectory            = processDirectoryInstance + '4Tiles/'
processTileDirectoryCopy        = processDirectoryInstance + '5TilesCopy/'
finalImageDir                   = processDirectoryInstance + '6Final/'

#Creating all the subfolders
if not os.path.exists(processDirectoryInstance):        os.mkdir(processDirectoryInstance)
if not os.path.exists(processDirectory):                os.mkdir(processDirectory)
if not os.path.exists(processExtentBoundsDirectory):    os.mkdir(processExtentBoundsDirectory)
if not os.path.exists(processClipBoundsDirectory):      os.mkdir(processClipBoundsDirectory)
if not os.path.exists(processTileDirectory):            os.mkdir(processTileDirectory)
if not os.path.exists(processTileDirectoryCopy):        os.mkdir(processTileDirectoryCopy)
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
Get all of the tile extents
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

#Clip this so we're not overrunning
processing.run("native:clip", {'INPUT':processDirectory + inImageName + 'ImageExtentGrid.gpkg',
    'OVERLAY':processDirectory + inImageName + 'ImageExtent.gpkg', 'OUTPUT':processDirectory + inImageName + 'ImageExtentGridClip.gpkg'})

#Only keep the tiles that actually touch the clipping vector
processing.run("native:extractbylocation", {'INPUT':processDirectory + inImageName + 'ImageExtentGridClip.gpkg',
    'PREDICATE':[0],'INTERSECT':clippingVector,'OUTPUT':processDirectory + inImageName + 'ImageExtentGridClipGrabbed.gpkg'})

#Split out the tiles into individual files to define the extent of the tiles
processing.run("native:splitvectorlayer", {'INPUT':processDirectory + inImageName + 'ImageExtentGridClipGrabbed.gpkg',
    'FIELD':'id','FILE_TYPE':0,'OUTPUT':processExtentBoundsDirectory})

#Now clip the grid so that it's only the area we actually want
processing.run("native:clip", {'INPUT':processDirectory + inImageName + 'ImageExtentGridClipGrabbed.gpkg',
    'OVERLAY':clippingVector, 'OUTPUT':processDirectory + inImageName + 'ImageExtentGridClipGrabbedClip.gpkg'})

#Split it out so there is a different extent to work from for each instance of the raster clipping
processing.run("native:splitvectorlayer", {'INPUT':processDirectory + inImageName + 'ImageExtentGridClipGrabbedClip.gpkg',
    'FIELD':'id','FILE_TYPE':0,'OUTPUT':processClipBoundsDirectory})

"""
#################################################################################################
Slice up the raster based on the tiles and the cutting vector
"""

boundsFiles = glob.glob(processClipBoundsDirectory + '/*.gpkg')

def clipTile(boundClipFile):
    try:
        boundName = Path(boundClipFile).stem
        
        #Check to see if the given tile already exists, so we can resume from a previous clipping attempt
        outputFile = processTileDirectory + boundName + 'Tile.tif'
        outputCopyFile = processTileDirectoryCopy + boundName + 'Tile.tif'
        if not os.path.exists(outputCopyFile):
            
            #Get the extent of the tile
            tileExtentVector = QgsVectorLayer(processExtentBoundsDirectory + boundName + '.gpkg')
            tileExtentRectangle = tileExtentVector.extent()

            cmdLine = [gdalwarpExe, "-cutline", boundClipFile, "-of", "GTiff", "-te", str(tileExtentRectangle.xMinimum()), str(tileExtentRectangle.yMinimum()), str(tileExtentRectangle.xMaximum()), str(tileExtentRectangle.yMaximum()),
                "-co", "COMPRESS=ZSTD", "-co", "ZSTD_LEVEL=1", "-co", "PREDICTOR=2", "-co", "NUM_THREADS=ALL_CPUS", "-co", "BIGTIFF=IF_SAFER",
                "-co", "TILED=YES", "--config", "GDAL_NUM_THREADS", "ALL_CPUS", "-multi", "-wo", "NUM_THREADS=ALL_CPUS",
                "-overwrite", inImage, outputFile]
            
            #Run the cmd line, hopefully with no errors
            result = subprocess.run(cmdLine, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, startupinfo=startupinfo)
            print(result.stderr, flush=True)
            print(result.stdout, flush=True)
            
            #Sometimes dumbass handles prevent cut and pasting
            try:
                shutil.move(outputFile, outputCopyFile)
            except:
                shutil.copy(outputFile, outputCopyFile)
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
#######################################################################
Finally bring it all together into a final mosaic
"""

#Prepare to make a final mosaic where the alpha bands are respected
finalImageDir = finalImageDir.replace("/", "\\")
processTileDirectoryCopy = processTileDirectoryCopy.replace("/", "\\")

#Final mosaicking of the tiles
finalImage = finalImageDir + outImageName + datetime.now().strftime("%Y%m%d%H%M") + '.tif'
cmdLine = 'gdalwarp -of GTiff ' + gdalOptionsFinal + ' "' + processTileDirectoryCopy + '**.tif" "' + finalImage + '" & timeout 5'
os.system(cmdLine)

#Build pyramids
print("Final image clipped, building pyramids")
processing.run("gdal:overviews", {'INPUT':finalImage,'CLEAN':False,'LEVELS':'','RESAMPLING':3,'FORMAT':1,
    'EXTRA':'--config COMPRESS_OVERVIEW WEBP --config WEBP_LEVEL_OVERVIEW 50'})

