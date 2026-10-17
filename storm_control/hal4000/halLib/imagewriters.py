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
        return [".dax", ".tif", ".big.tif", ".zarr", ".test"]
    else:
        return [".dax", ".tif", ".big.tif", ".zarr", ".comp.zarr"]

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
        
        
        
        self.z1 = group.empty(name='data', shape=(1,self.h,self.w), chunks=(1,self.h,self.w), dtype='uint16')
        
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


from queue import Empty, Full, Queue
from threading import Thread

import numpy as np
import zarr
from zarr.codecs import BloscCodec, BytesCodec
from numba import njit


_ZARR_STOP = object()

# Preserve the original float32 round/sqrt/clip conversion, including the
# upper endpoint: uint16(65535) maps to uint8(255), not a wrapping uint8(0).
SQRT_LUT = np.clip(
    np.rint(np.sqrt(np.arange(65536, dtype=np.float32))),
    0, 255,
).astype(np.uint8)


@njit(cache=True, nogil=True)
def uint16_to_sqrt_uint8(raw, lut):
    """Convert a flat uint16 array into a newly allocated flat uint8 array.

    The HAL saveFrame method below validates dtype and flattens the input.
    Pass SQRT_LUT as lut. Conversion does not modify the camera buffer.
    """
    output = np.empty(raw.size, dtype=np.uint8)
    for i in range(raw.size):
        output[i] = lut[raw[i]]
    return output


class ZarrStreamer:
    """One writer thread; bounded input queue; batched appends in frame order.

    chunk_size controls the number of frames per stored chunk. batch_size
    controls how many frames share an append call. They are independent.
    Defaults retain one frame per chunk and append up to eight frames at once.
    A partial batch is written after 0.1 s without another frame, or on close.

    add_frame / close must be called serially by one producer, as in HAL.
    When the queue is full, add_frame waits for the writer to catch up.
    """

    def __init__(self, filename, shape_xy, chunk_size=1, dtype="uint8",
                 batch_size=8, queue_size=16):
        self.shape_xy = tuple(int(size) for size in shape_xy)
        if len(self.shape_xy) != 2 or min(self.shape_xy) <= 0:
            raise ValueError("shape_xy must contain positive (height, width)")
        if min(chunk_size, batch_size, queue_size) < 1:
            raise ValueError("chunk_size, batch_size, and queue_size must be >= 1")

        self.dtype = np.dtype(dtype)
        self.batch_size = batch_size
        self.queue = Queue(maxsize=queue_size)
        self._error = None
        self._closed = False

        self.store = zarr.create_array(
            store=filename,
            shape=(0, *self.shape_xy),
            chunks=(chunk_size, *self.shape_xy),
            dtype=self.dtype,
            zarr_format=3,
            serializer=BytesCodec(),
            compressors=[BloscCodec(
                cname="lz4", clevel=3, shuffle="bitshuffle",
            )],
            overwrite=True,
            config={"write_empty_chunks": True},
        )

        self.thread = Thread(target=self._writer_worker, name="zarr-writer")
        self.thread.start()

    def _raise_if_failed(self):
        if self._error is not None:
            raise RuntimeError("Background Zarr writing failed") from self._error

    def _put(self, item):
        while True:
            self._raise_if_failed()
            try:
                self.queue.put(item, timeout=0.1)
                break
            except Full:
                continue
        self._raise_if_failed()

    def add_frame(self, frame, *, copy=True):
        """Queue a frame, copying by default to protect reused camera buffers.

        With copy=False the caller transfers ownership and must not modify
        the queued image. saveFrame uses this for its fresh lookup output.
        """
        if self._closed:
            raise RuntimeError("Cannot add frames after closing the streamer")
        self._raise_if_failed()
        if copy:
            image = np.array(frame, dtype=self.dtype, order="C", copy=True)
        else:
            image = np.asarray(frame, dtype=self.dtype, order="C")
        if image.shape != self.shape_xy:
            raise ValueError(
                f"Expected frame shape {self.shape_xy}, got {image.shape}"
            )
        self._put(image)

    def _flush_buffer(self, buffer):
        self.store.append(np.stack(buffer, axis=0), axis=0)

    def _writer_worker(self):
        buffer = []
        try:
            while True:
                try:
                    frame = self.queue.get(timeout=0.1)
                except Empty:
                    if buffer:
                        self._flush_buffer(buffer)
                        buffer = []
                    continue

                if frame is _ZARR_STOP:
                    if buffer:
                        self._flush_buffer(buffer)
                    return

                buffer.append(frame)
                if len(buffer) >= self.batch_size:
                    self._flush_buffer(buffer)
                    buffer = []
        except BaseException as error:
            # Do not silently swallow disk-full or codec errors.
            self._error = error

    def close(self):
        if self._closed:
            self._raise_if_failed()
            return
        self._closed = True
        try:
            # FIFO sentinel follows every frame already accepted.
            self._put(_ZARR_STOP)
        finally:
            self.thread.join()
        self._raise_if_failed()


class ZarrFastFile(BaseFileWriter):
    """Threaded Zarr 3 writer with Numba lookup conversion in saveFrame."""

    def __init__(self, **kwds):
        # The first call compiles (or loads the disk cache). Do this before
        # BaseFileWriter connects saveFrame to the camera's newFrame signal.
        uint16_to_sqrt_uint8(np.zeros(1, dtype=np.uint16), SQRT_LUT)
        super().__init__(**kwds)
        self.filename = self.basename + ".zarr"
        if self.binx != 1 or self.biny != 1:
            # Disconnect signals established by BaseFileWriter before failing.
            self.cam_fn.newFrame.disconnect(self.saveFrame)
            self.cam_fn.stopped.disconnect(self.handleStopped)
            raise NotImplementedError("Software binning is not implemented")

        self.streamer = ZarrStreamer(
            self.filename,
            (self.h, self.w),
            chunk_size=1,
            batch_size=8,
            queue_size=16,
        )
        try:
            self.streamer.store.attrs.update({
                "axes": ["t", "y", "x"],
                "encoding": "clip(round(sqrt(raw)), 0, 255).astype(uint8)",
                "raw_dtype": "uint16",
            })
        except BaseException:
            self.streamer.close()
            raise

    def saveFrame(self, frame):
        raw = np.asarray(frame.getData())
        if raw.dtype != np.uint16:
            raise TypeError(f"Expected uint16 camera data, got {raw.dtype}")
        if raw.size != self.h * self.w:
            raise ValueError(
                f"Expected {self.h * self.w} pixels, got {raw.size}"
            )

        # Usually zero-copy for the contiguous buffers returned by HAL.
        raw = np.ascontiguousarray(raw).reshape(-1)
        image = uint16_to_sqrt_uint8(raw, SQRT_LUT).reshape(self.h, self.w)
        # Lookup returns a fresh array; the queue can take ownership directly.
        self.streamer.add_frame(image, copy=False)
        super().saveFrame()

    def closeWriter(self):
        super().closeWriter()
        self.streamer.close()  # Flush all frames before writing the sidecar.
        written_frames = self.streamer.store.shape[0]
        if written_frames != self.number_frames:
            raise RuntimeError(
                f"Queued {self.number_frames} frames but saved {written_frames}"
            )
        self.streamer.store.attrs["number_frames"] = written_frames

        with open(self.basename + ".inf", "w") as inf_fp:
            inf_fp.write("binning = 1 x 1\n")
            inf_fp.write("data type = 8 bit integers (square-root encoded, Zarr)\n")
            inf_fp.write(f"frame dimensions = {self.w} x {self.h}\n")
            inf_fp.write(f"number of frames = {written_frames}\n")
            inf_fp.write("x_start = 1\n")
            inf_fp.write(f"x_end = {self.w}\n")
            inf_fp.write("y_start = 1\n")
            inf_fp.write(f"y_end = {self.h}\n")


class ZarrCompFile(ZarrFastFile):
    """Route the existing .comp.zarr menu option through the fast writer."""


    
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
        self.tif.write(image.reshape((frame.image_y, frame.image_x)),
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