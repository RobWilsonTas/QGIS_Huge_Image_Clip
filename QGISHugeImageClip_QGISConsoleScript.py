# Run in the QGIS Python console to clip the input raster and write a tiled WebP GeoTIFF.
from math import ceil, floor
from time import perf_counter
from osgeo import gdal
from qgis.core import QgsRasterLayer

#Choosing the inputs for the clipping
clipPath, _ = QFileDialog.getOpenFileName(None, "Select clipping vector", "", "Vector files (*.gpkg *.shp);;All files (*.*)")
rasterPath, _ = QFileDialog.getOpenFileName(None, "Select input raster", "", "Raster files (*.tif *.tiff *.img);;All files (*.*)")
rasterLayer = QgsRasterLayer(rasterPath, 'input')
outputPath = os.path.join(os.path.dirname(rasterPath), os.path.splitext(os.path.basename(rasterPath))[0] + "_Clipped.tif")

#WebP quality ranges from 1-100. Lower values produce smaller files.
webpQuality = 1
workerCount = 64
tileSize = 512

"""
######################################################################
"""

creationOptions = ['TILED=YES', 'BLOCKXSIZE=' + str(tileSize), 'BLOCKYSIZE=' + str(tileSize), 'COMPRESS=WEBP',
    'WEBP_LEVEL=' + str(webpQuality), 'WEBP_LOSSLESS=NO', 'NUM_THREADS=' + str(workerCount), 'BIGTIFF=IF_SAFER',]

#Set up gdal and load in the raster
gdal.UseExceptions()
gdal.SetConfigOption('GDAL_NUM_THREADS', str(workerCount))
startTime = perf_counter()
sourceDataset = gdal.Open(rasterLayer.source().split('|')[0])
geoTransform = sourceDataset.GetGeoTransform()

#This script expects an 8-bit RGB or RGBA raster.
#Only the first three bands are used as RGB, with a new alpha band generated from the clipping polygon.
if sourceDataset.RasterCount not in (3, 4) or sourceDataset.GetRasterBand(1).DataType != gdal.GDT_Byte:
    raise ValueError('Expected an 8-bit raster with three or four bands; only bands 1-3 are used.')

#Use the raster's coordinate system for the clipping operation.
sourceCrs = rasterLayer.crs().toWkt() if rasterLayer.crs().isValid() else sourceDataset.GetProjection()
if not sourceCrs:
    raise ValueError('Assign a coordinate system to the input layer in QGIS first.')

#Reproject the clipping vector into the raster's CRS and load it into memory.
clipDataset = gdal.VectorTranslate('', clipPath, format='MEM', dstSRS=sourceCrs, geometryType='MULTIPOLYGON')
clipLayer = clipDataset.GetLayer(0)

#Get the outer bounding extent of the clipping geometry.
clipMinX, clipMaxX, clipMinY, clipMaxY = clipLayer.GetExtent()

#Convert the clipping extent from map coords into raster pixel positions
clipLeftPixel = max(0, floor((clipMinX - geoTransform[0]) / geoTransform[1]))
clipRightPixel = min(sourceDataset.RasterXSize, ceil((clipMaxX - geoTransform[0]) / geoTransform[1]))
clipTopPixel = max(0, floor((clipMaxY - geoTransform[3]) / geoTransform[5]))
clipBottomPixel = min(sourceDataset.RasterYSize, ceil((clipMinY - geoTransform[3]) / geoTransform[5]))

#Calculate the dimensions of the cropped raster, and stop the script if there is no overlap
outputWidth = clipRightPixel - clipLeftPixel
outputHeight = clipBottomPixel - clipTopPixel
if outputWidth <= 0 or outputHeight <= 0:
    raise ValueError('The clip polygon does not overlap the raster.')

#Calculate the coordinates of the top-left corner pixel in the output raster
outputOriginX = geoTransform[0] + clipLeftPixel * geoTransform[1]
outputOriginY = geoTransform[3] + clipTopPixel * geoTransform[5]

#Create an empty output raster with four 8-bit bands: red, green, blue and alpha, ready to be filled
outputDataset = gdal.GetDriverByName('GTiff').Create(str(outputPath), outputWidth, outputHeight, 4, gdal.GDT_Byte, creationOptions)
outputDataset.SetGeoTransform((outputOriginX, geoTransform[1], 0, outputOriginY, 0, geoTransform[5]))
outputDataset.SetProjection(sourceCrs)
colorTypes = (gdal.GCI_RedBand, gdal.GCI_GreenBand, gdal.GCI_BlueBand, gdal.GCI_AlphaBand)
for bandNumber, colorType in enumerate(colorTypes, 1):
    outputDataset.GetRasterBand(bandNumber).SetColorInterpretation(colorType)

#Go through each row of tiles, in order to keep memory usage from blowing up
tileRowCount = (outputHeight + tileSize - 1) // tileSize
for tileRowNumber in range(tileRowCount):
    
    #Within the workspace of the entire output raster, work out the position of the current row
    stripTopPixel = tileRowNumber * tileSize
    stripHeight = min(tileSize, outputHeight - stripTopPixel)

    #Create a temporary in-memory raster to hold the clipping mask for this row
    #This is to be a very long thin raster with values of 0 and 255
    maskDataset = gdal.GetDriverByName('MEM').Create('', outputWidth, stripHeight, 1, gdal.GDT_Byte)
    maskDataset.SetProjection(sourceCrs)
    maskDataset.SetGeoTransform((outputOriginX, geoTransform[1], 0, outputOriginY + stripTopPixel * geoTransform[5], 0, geoTransform[5]))

    #Burn a value of 255 into the mask
    clipLayer.ResetReading()
    gdal.RasterizeLayer(maskDataset, [1], clipLayer, burn_values=[255])

    #Write the rgb and the alpha to the output
    alphaBytes = maskDataset.GetRasterBand(1).ReadRaster()
    rgbBytes = sourceDataset.ReadRaster(clipLeftPixel, clipTopPixel + stripTopPixel, outputWidth, stripHeight, band_list=[1, 2, 3])
    outputDataset.WriteRaster(0, stripTopPixel, outputWidth, stripHeight, rgbBytes + alphaBytes)

    print('Done tile row ' + str(tileRowNumber + 1) + ' of ' + str(tileRowCount), flush=True)

#Close the datasets by releasing their references, ensuring the output is flushed to disk.
outputDataset = None
clipLayer = clipDataset = sourceDataset = None

#Report the total processing time and output location.
print('Done in {:.2f} seconds: {}'.format(perf_counter() - startTime, outputPath), flush=True)
