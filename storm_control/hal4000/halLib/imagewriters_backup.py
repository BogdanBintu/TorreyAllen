#!/usr/bin/env python
"""
Image file writers for various formats.
Hazen 03/17
Modifications by Bogdan based on Aditya 1/20/2022 to include zaar and software binning
"""

import copy
import datetime
import struct
import tifffile
import time
import zarr
import numpy as np
import dask.array as da
import os
from PyQt5 import QtCore

import storm_control.sc_library.halExceptions as halExceptions
import storm_control.sc_library.parameters as params
import torch

class ImageWriterException(halExceptions.HalException):
    pass


def availableFileFormats(test_mode):
    """
    Return a list of the available movie formats.
    """
    #
    # FIXME: Decouple extension from file type so that big tiffs can
    #        have a normal name, and don't need the '.big' in the
    #        extension.
    #

    if test_mode:
        return [".dax", ".tif", ".big.tif", ".zarr", ".comp.zarr", ".fast.zarr", ".test"]
    else:
        return [".dax", ".tif", ".big.tif", ".zarr", ".comp.zarr",".fast.zarr"]

def createFileWriter(camera_functionality, film_settings):
    """
    This is convenience function which creates the appropriate file writer
    based on the filetype.
    """
    ft = film_settings.getFiletype()
    if (ft == ".dax"):
        return DaxFile(camera_functionality = camera_functionality,
                       film_settings = film_settings)
    elif (ft == ".big.tif"):
        return TIFFile(bigtiff = True,
                       camera_functionality = camera_functionality,
                       film_settings = film_settings)
    elif (ft == ".spe"):
        return SPEFile(camera_functionality = camera_functionality,
                       film_settings = film_settings)
    elif (ft == ".test"):
        return TestFile(camera_functionality = camera_functionality,
                       film_settings = film_settings)
    elif (ft == ".tif"):
        return TIFFile(camera_functionality = camera_functionality,
                       film_settings = film_settings)
                       
    elif (ft == ".zarr"):
        return ZarrFile(camera_functionality = camera_functionality,
                       film_settings = film_settings)
    elif (ft == ".comp.zarr"):
        return ZarrCompFile(camera_functionality = camera_functionality,
                       film_settings = film_settings)
    elif (ft == ".fast.zarr"):
        return ZarrFastFile(camera_functionality = camera_functionality,
                       film_settings = film_settings)
                       
    else:
        raise ImageWriterException("Unknown output file format '" + ft + "'")




import ctypes
import os
import platform
import sys
import time
def get_free_space_mb(dirname):
    """Return folder/drive free space (in megabytes)."""
    if platform.system() == 'Windows':
        free_bytes = ctypes.c_ulonglong(0)
        ctypes.windll.kernel32.GetDiskFreeSpaceExW(ctypes.c_wchar_p(dirname), None, None, ctypes.pointer(free_bytes))
        return free_bytes.value / 1024 / 1024
    else:
        st = os.statvfs(dirname)
        return st.f_bavail * st.f_frsize / 1024 / 1024


class BaseFileWriter(object):

    def __init__(self, camera_functionality = None, film_settings = None, **kwds):
        super().__init__(**kwds)
        self.cam_fn = camera_functionality
        self.film_settings = film_settings
        self.stopped = False

        # This is the frame size in MB.
        self.frame_size = self.cam_fn.getParameter("bytes_per_frame") *  0.000000953674
        self.number_frames = 0

        # Figure out the filename.
        self.basename = self.film_settings.getBasename()
        if (len(self.cam_fn.getParameter("extension")) != 0):
            self.basename += "_" + self.cam_fn.getParameter("extension")
        self.filename = self.basename + self.film_settings.getFiletype()


        

        # Connect the camera functionality.
        self.cam_fn.newFrame.connect(self.saveFrame)
        self.cam_fn.stopped.connect(self.handleStopped)
        try:
            self.binx = int(self.cam_fn.getParameter("x_bin_cam"))
            self.biny = int(self.cam_fn.getParameter("y_bin_cam"))
        except:
            self.binx,self.biny=1,1
        self.wT = int(self.cam_fn.getParameter("x_pixels"))
        self.hT = int(self.cam_fn.getParameter("y_pixels"))
        self.w,self.h = self.wT//self.binx,self.hT//self.biny
        
    def closeWriter(self):
        assert self.stopped
        self.cam_fn.newFrame.disconnect(self.saveFrame)
        self.cam_fn.stopped.disconnect(self.handleStopped)

    def getSize(self):
        return self.frame_size * self.number_frames
    
    def handleStopped(self):
        dirname = os.path.dirname(self.filename)
        while get_free_space_mb(dirname)<5000:
            time.sleep(60)
        self.stopped = True

    def isStopped(self):
        return self.stopped
        
    def saveFrame(self):
        
        self.number_frames += 1


class ZarrFile(BaseFileWriter):
    """
    Zarr file writing class.
    """
    def __init__(self, **kwds):
        super().__init__(**kwds)
        
        dirname = os.path.dirname(self.filename)
        group = dirname+os.sep+os.path.basename(self.filename).split("_")[-1].split(".")[0]
        
        import shutil
        if os.path.exists(group): shutil.rmtree(group)
        
        root = zarr.open(self.filename, mode='w')
        group = root.create_group(group)
        
        
        
        self.z1 = group.empty('data', shape=(1,self.h,self.w), chunks=(1,self.h,self.w), dtype='uint16')
        
    def closeWriter(self):
        """
        Close the file and write a very simple .inf file. All the metadata is
        now stored in the .xml file that is saved with each recording.
        """
        super().closeWriter()
        
        w = str(self.w)
        h = str(self.h)
        with open(self.basename + ".inf", "w") as inf_fp:
            inf_fp.write("binning = 1 x 1\n")
            inf_fp.write("data type = 16 bit integers (binary, little endian)\n")
            inf_fp.write("frame dimensions = " + w + " x " + h + "\n")
            inf_fp.write("number of frames = " + str(self.number_frames) + "\n")
            if True:
                inf_fp.write("x_start = 1\n")
                inf_fp.write("x_end = " + w + "\n")
                inf_fp.write("y_start = 1\n")
                inf_fp.write("y_end = " + h + "\n")
            inf_fp.close()
        
    def saveFrame(self, frame):
        
        super().saveFrame()
        image = frame.getData()
        w,h,binx,biny = self.w,self.h,self.binx,self.biny
        if binx!=1 or biny!=1:
            daimage = da.from_array(np_data,chunks = len(np_data) // 4)
            image = daimage.reshape((1,h,binx,w,biny)).sum(axis=(-1,-3),dtype=np.uint16).compute()
        else:
            image = np.array(image).reshape((1,h,w))
        self.z1.append(image)
from numcodecs import Blosc,BZ2
class ZarrCompFile(BaseFileWriter):
    """
    Zarr file writing class.
    """
    def __init__(self, **kwds):
        super().__init__(**kwds)
        self.filename = self.filename.replace('.comp','')
        dirname = os.path.dirname(self.filename)
        group = dirname+os.sep+os.path.basename(self.filename).split("_")[-1].split(".")[0]
        
        import shutil
        if os.path.exists(group): shutil.rmtree(group)
        
        root = zarr.open(self.filename, mode='w')
        group = root.create_group(group)
        
        
        compressor = Blosc(cname='zstd', clevel=3, shuffle=Blosc.BITSHUFFLE)
        self.z1 = group.empty('data', shape=(1,self.h,self.w), chunks=(1,self.h,self.w), dtype='uint8',compressor=compressor)
        
    def closeWriter(self):
        """
        Close the file and write a very simple .inf file. All the metadata is
        now stored in the .xml file that is saved with each recording.
        """
        super().closeWriter()
        
        w = str(self.w)
        h = str(self.h)
        with open(self.basename + ".inf", "w") as inf_fp:
            inf_fp.write("binning = 1 x 1\n")
            inf_fp.write("data type = 16 bit integers (binary, little endian)\n")
            inf_fp.write("frame dimensions = " + w + " x " + h + "\n")
            inf_fp.write("number of frames = " + str(self.number_frames) + "\n")
            if True:
                inf_fp.write("x_start = 1\n")
                inf_fp.write("x_end = " + w + "\n")
                inf_fp.write("y_start = 1\n")
                inf_fp.write("y_end = " + h + "\n")
            inf_fp.close()
        
    def saveFrame(self, frame):
        
        super().saveFrame()
        image = frame.getData()
        w,h,binx,biny = self.w,self.h,self.binx,self.biny
        if binx!=1 or biny!=1:
            #daimage = da.from_array(np_data,chunks = len(np_data) // 4)
            #image = daimage.reshape((1,h,binx,w,biny)).sum(axis=(-1,-3),dtype=np.uint16).compute()
            assert(False)
        else:
            image = np.array(image,dtype=np.float32)
            #image = np.round(np.sqrt(image)).astype(np.uint8)
            #image = np.array(image).reshape((1,h,w))
            
            imageT = torch.from_numpy(image)
            imageT = torch.clamp(torch.round(torch.sqrt(imageT)),0,255).to(torch.uint8)
            image = imageT.numpy()
            image = image.reshape((1,h,w))
        self.z1.append(image)   

import zarr
import numpy as np
from numcodecs import Blosc, blosc
import multiprocessing as mp
from multiprocessing import shared_memory
import time


from numcodecs import Blosc,BZ2
class ZarrFastFile(BaseFileWriter):
    """
    Zarr file writing class.
    """
    def __init__(self, **kwds):
        super().__init__(**kwds)
        self.filename = self.filename.replace('.fast','')
        #self.streamer = MultiprocessZarrStreamer(self.filename, (self.h,self.w), chunk_size=1,n_threads=8)
        self.streamer = ZarrStreamer(self.filename, (self.h,self.w))
    def closeWriter(self):
        """
        Close the file and write a very simple .inf file. All the metadata is
        now stored in the .xml file that is saved with each recording.
        """
        super().closeWriter()
        self.streamer.close()
        w = str(self.w)
        h = str(self.h)
        with open(self.basename + ".inf", "w") as inf_fp:
            inf_fp.write("binning = 1 x 1\n")
            inf_fp.write("data type = 16 bit integers (binary, little endian)\n")
            inf_fp.write("frame dimensions = " + w + " x " + h + "\n")
            inf_fp.write("number of frames = " + str(self.number_frames) + "\n")
            if True:
                inf_fp.write("x_start = 1\n")
                inf_fp.write("x_end = " + w + "\n")
                inf_fp.write("y_start = 1\n")
                inf_fp.write("y_end = " + h + "\n")
            inf_fp.close()
        
    def saveFrame(self, frame):
        
        super().saveFrame()
        image = frame.getData()
        imageT = torch.from_numpy(image).to(torch.float32)
        imageT = torch.clamp(torch.round(torch.sqrt(imageT)),0,255).to(torch.uint8)
        image = imageT.numpy()
        image = image.reshape((self.h,self.w))
        self.streamer.add_frame(image)  

# --- The Independent Writer Process ---
def zarr_writer_process(filename, shape_xy, dtype, shm_name, queue, chunk_size, n_threads):
    # 1. Enable Blosc internal threading inside this process
    blosc.set_nthreads(n_threads)
    
    # 2. Setup Compressor
    compressor = Blosc(cname='lz4', clevel=3, shuffle=Blosc.BITSHUFFLE)
    
    # 3. Open Zarr (Synchronizer not needed for single-writer append)
    store = zarr.open(
        filename, 
        mode='w', 
        shape=(0, *shape_xy), 
        chunks=(chunk_size, *shape_xy),
        dtype=dtype, 
        compressor=compressor
    )

    # 4. Attach to existing Shared Memory
    existing_shm = shared_memory.SharedMemory(name=shm_name)
    # Create a numpy view of the shared memory buffer
    # We assume a buffer large enough for 1 chunk (chunk_size, H, W)
    shm_array = np.ndarray((chunk_size, *shape_xy), dtype=dtype, buffer=existing_shm.buf)

    buffer_count = 0
    
    while True:
        try:
            # We receive just the index/signal, not the data itself
            msg = queue.get()
            
            if msg == 'STOP':
                break
                
            # msg is the index in the shared memory where the latest batch sits
            # In this simple example, we assume the main process fills the shared 
            # memory and tells us "Go write it".
            
            # Write the data from Shared Memory to Disk
            # Note: This reads from RAM (fast) and writes to Disk (slow)
            store.append(shm_array, axis=0)
            
            print(f"Process: Wrote chunk. Current shape: {store.shape}")
            
        except Exception as e:
            print(f"Writer Error: {e}")
            break

    existing_shm.close()

# --- Main Camera Class ---
class MultiprocessZarrStreamer:
    def __init__(self, filename, shape_xy, chunk_size=32, dtype='uint8', n_threads=1):
        self.chunk_size = chunk_size
        self.shape_xy = shape_xy
        self.dtype = dtype
        self.buffer_idx = 0
        
        # 1. Create Shared Memory Block
        # Size = Chunk Size * Image Size * Bytes per pixel
        data_size = chunk_size * shape_xy[0] * shape_xy[1] * np.dtype(dtype).itemsize
        self.shm = shared_memory.SharedMemory(create=True, size=data_size)
        
        # Create a numpy array backed by this shared memory
        self.shm_array = np.ndarray((chunk_size, *shape_xy), dtype=dtype, buffer=self.shm.buf)
        
        # 2. Setup Communication Queue
        self.queue = mp.Queue()
        
        # 3. Start the Writer Process
        self.process = mp.Process(
            target=zarr_writer_process,
            args=(filename, shape_xy, dtype, self.shm.name, self.queue, chunk_size, n_threads)
        )
        self.process.start()

    def add_frame(self, frame):
        # Direct copy into Shared Memory (Very Fast RAM-to-RAM copy)
        self.shm_array[self.buffer_idx] = frame
        self.buffer_idx += 1
        
        # If shared buffer is full, signal the writer to dump it
        if self.buffer_idx >= self.chunk_size:
            # We copy the buffer to ensure the writer has data while we overwrite
            # Note: For true lock-free ring buffering, you'd need 2x SharedMemory blocks
            # switching back and forth. For simplicity, we block momentarily here 
            # or rely on the OS cache speed.
            
            # Signal writer
            self.queue.put('WRITE')
            
            # Reset index (In a production ring-buffer, you would swap SHM blocks here)
            self.buffer_idx = 0

    def close(self):
        self.queue.put('STOP')
        self.process.join()
        self.shm.close()
        self.shm.unlink() # Free the memory
import zarr
import numpy as np
from numcodecs import Blosc
from threading import Thread
from queue import Queue
import time
from numcodecs import Blosc, blosc
class ZarrStreamer:
    def __init__(self, filename, shape_xy, chunk_size=1, dtype='uint8'):
        """
        filename: Path to .zarr file (directory)
        shape_xy: Tuple (Height, Width) of the frames
        chunk_size: Number of frames to buffer before writing (Z axis chunk size)
        """
        self.queue = Queue()
        self.chunk_size = chunk_size
        self.running = True
        blosc.set_nthreads(8)
        # 1. Configure Blosc for speed
        # 'lz4' is generally fastest for streaming. 
        # clevel=5 is a good balance of speed/compression.
        compressor = Blosc(cname='lz4', clevel=3, shuffle=Blosc.BITSHUFFLE,)
        
        # 2. Initialize the Zarr array
        # shape=(0, ...) allows the array to grow along the time axis
        self.store = zarr.open(
            filename, 
            mode='w', 
            shape=(0, *shape_xy), 
            chunks=(chunk_size, *shape_xy),
            dtype=dtype, 
            compressor=compressor
        )
        
        # 3. Start the writer thread
        self.thread = Thread(target=self._writer_worker)
        self.thread.start()

    def add_frame(self, frame):
        """Non-blocking call to add a frame to the write queue."""
        self.queue.put(frame)

    def _writer_worker(self):
        buffer = []
        
        while self.running or not self.queue.empty():
            try:
                # Wait for a frame (timeout allows checking self.running)
                frame = self.queue.get(timeout=0.1)
                buffer.append(frame)
                
                # Only write when we fill a chunk (or close to it)
                if len(buffer) >= self.chunk_size:
                    self._flush_buffer(buffer)
                    buffer = []
                    
            except:
                continue
        
        # Flush remaining frames after stopping
        if buffer:
            self._flush_buffer(buffer)

    def _flush_buffer(self, buffer):
        """Writes the batch to disk."""
        if not buffer:
            return
            
        # Stack frames into (N, X, Y)
        batch = np.stack(buffer, axis=0)
        
        # Append to the Zarr array
        self.store.append(batch, axis=0)

    def close(self):
        self.running = False
        self.thread.join()
        print(f"Stream closed. Final shape: {self.store.shape}")
     
class DaxFile(BaseFileWriter):
    """
    Dax file writing class.
    """
    def __init__(self, **kwds):
        super().__init__(**kwds)
        self.fp = open(self.filename, "wb")

    def closeWriter(self):
        """
        Close the file and write a very simple .inf file. All the metadata is
        now stored in the .xml file that is saved with each recording.
        """
        super().closeWriter()
        self.fp.close()

        w = str(self.cam_fn.getParameter("x_pixels"))
        h = str(self.cam_fn.getParameter("y_pixels"))
        with open(self.basename + ".inf", "w") as inf_fp:
            inf_fp.write("binning = 1 x 1\n")
            inf_fp.write("data type = 16 bit integers (binary, little endian)\n")
            inf_fp.write("frame dimensions = " + w + " x " + h + "\n")
            inf_fp.write("number of frames = " + str(self.number_frames) + "\n")
            if True:
                inf_fp.write("x_start = 1\n")
                inf_fp.write("x_end = " + w + "\n")
                inf_fp.write("y_start = 1\n")
                inf_fp.write("y_end = " + h + "\n")
            inf_fp.close()

    def saveFrame(self, frame):
        super().saveFrame()
        w,h,binx,biny = self.w,self.h,self.binx,self.biny
        
        np_data= frame.getData()
        if binx!=1 or biny!=1:
            daimage = da.from_array(np_data,chunks = len(np_data) // 4)
            np_data = daimage.reshape((h,binx,w,biny)).sum(axis=(-1,1),dtype=np.uint16).compute()
        np_data.tofile(self.fp)


class SPEFile(BaseFileWriter):
    """
    SPE file writing class.
    FIXME: This has not been tested, could be broken..
    """
    def __init__(self, **kwds):
        super().__init__(**kwds)
        self.fp = open(self.filename, "wb")
        
        header = chr(0) * 4100
        self.fp.write(header)

        # NOSCAN
        self.fp.seek(34)
        self.fp.write(struct.pack("h", -1))

        # FACCOUNT (width)
        self.fp.seek(42)
        self.fp.write(struct.pack("h", self.feed_info.getParameter(x_pixels)))

        # DATATYPE
        self.fp.seek(108)
        self.fp.write(struct.pack("h", 3))
           
        # LNOSCAN
        self.fp.seek(664)
        self.fp.write(struct.pack("h", -1))

        # STRIPE (height)
        self.fp.seek(656)
        self.fp.write(struct.pack("h", self.feed_info.getParameter("y_pixels")))

        self.fp.seek(4100)

    def closeWriter(self):
        super().closeWriter()
        self.fp.seek(1446)
        self.fp.write(struct.pack("i", self.number_frames))

    def saveFrame(self, frame):
        super().saveFrame()
        np_data = frame.getData()
        np_data.tofile(self.file_ptrs[index])


class TestFile(DaxFile):
    """
    This is for testing timing issues. The format is .dax, but it only
    saves the first frame. Also it has some long pauses to try and trip
    up HAL.
    """
    def __init__(self, **kwds):
        time.sleep(1.0)
        super().__init__(**kwds)
        
    def closeWriter(self):
        time.sleep(1.0)
        super().closeWriter()

    def saveFrame(self, frame):
        if (self.number_frames < 1):
            super().saveFrame(frame)
    
    
class TIFFile(BaseFileWriter):
    """
    TIF file writing class. This supports both normal and 'big' tiff.
    """
    def __init__(self, bigtiff = False, **kwds):
        super().__init__(**kwds)
        self.metadata = {'unit' : 'um'}
        if bigtiff:
            self.resolution = (25400.0/self.film_settings.getPixelSize(),
                               25400.0/self.film_settings.getPixelSize())
            self.tif = tifffile.TiffWriter(self.filename,
                                           bigtiff = bigtiff)
        else:
            self.resolution = (1.0/self.film_settings.getPixelSize(), 1.0/self.film_settings.getPixelSize())
            self.tif = tifffile.TiffWriter(self.filename,
                                           imagej = True)

    def closeWriter(self):
        super().closeWriter()
        self.tif.close()
        
    def saveFrame(self, frame):
        super().saveFrame()
        image = frame.getData()
        self.tif.save(image.reshape((frame.image_y, frame.image_x)),
                      metadata = self.metadata,
                      resolution = self.resolution, 
                      contiguous = True)


#
# The MIT License
#
# Copyright (c) 2017 Zhuang Lab, Harvard University
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#
 