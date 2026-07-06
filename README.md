Recently I tried leaving a 20GB image clipping over the weekend, and it still hadn't completed by Monday

So I made this script, which splits the image up into tiles, and clips the individual tiles, then puts it all back together

This script takes advantage of being able to easily make multithread calls of gdalwarp from pyqgis

I kid you not, my image went from taking more than a weekend to clip, to only taking about an hour
