#!/usr/bin/env python
"""
A utility for creating image mosaics and imaging array tomography type samples.

Hazen 10/18
"""

import os
import sys
#import re
from PyQt5 import QtCore, QtGui, QtWidgets

import storm_control.sc_library.hdebug as hdebug
import storm_control.sc_library.parameters as params

import storm_control.steve.comm as comm
import storm_control.steve.coord as coord
import storm_control.steve.imageCapture as imageCapture
import storm_control.steve.imageItem as imageItem
import storm_control.steve.mosaic as mosaic
import storm_control.steve.positions as positions
import storm_control.steve.qtRegexFileDialog as qtRegexFileDialog
import storm_control.steve.sections as sections
import storm_control.steve.steveItems as steveItems

import storm_control.steve.qtdesigner.steve_ui as steveUi


class Window(QtWidgets.QMainWindow):
    """
    The main window of the Steve program.
    """

    @hdebug.debug
    def __init__(self, parameters = None, **kwds):
        super().__init__(**kwds)

        self.context_actions = []
        self.context_menu = QtWidgets.QMenu(self)
        self.comm = comm.Comm()
        self.item_store = steveItems.SteveItemsStore()
        self.modules = []
        self.parameters = parameters
        self.regexp_str = ""
        self.settings = QtCore.QSettings("storm-control", "steve")
        self.snapshot_directory = self.parameters.get("directory")

        # Set Steve scale, 1 pixel is 0.1 microns.
        coord.Point.pixels_to_um = 0.1
        
        # UI setup
        self.ui = steveUi.Ui_MainWindow()
        self.ui.setupUi(self)

        self.move(self.settings.value("position", self.pos()))
        self.resize(self.settings.value("size", self.size()))
        self.setWindowIcon(QtGui.QIcon("steve.ico"))

        # Create HAL movie/image capture object.
        self.image_capture = imageCapture.MovieCapture(comm = self.comm,
                                                       item_store = self.item_store,
                                                       parameters = parameters)
                
        #
        # Module initializations
        #

        # Mosaic
        #
        # This is the first tab.
        #
        self.mosaic = mosaic.Mosaic(comm = self.comm,
                                    image_capture = self.image_capture,
                                    item_store = self.item_store,
                                    parameters = self.parameters)
        layout = QtWidgets.QVBoxLayout(self.ui.mosaicTab)
        layout.addWidget(self.mosaic)
        layout.setContentsMargins(0,0,0,0)
        self.ui.mosaicTab.setLayout(layout)
        self.modules.append(self.mosaic)

        # Connect mosaic signals.
        self.mosaic.mosaic_view.mosaicViewContextMenuEvent.connect(self.handleMosaicViewContextMenuEvent)
        self.mosaic.mosaic_view.mosaicViewDropEvent.connect(self.handleMosaicViewDropEvent)
        self.mosaic.mosaic_view.mosaicViewKeyPressEvent.connect(self.handleMosaicViewKeyPressEvent)
        self.mosaic.mosaic_view.mosaicViewSelectionChange.connect(self.handleMosaicViewSelectionChange)
        self.mosaic.mosaicRequestPositions.connect(self.handleMosaicPositionRequest)

        # The objectives group box keeps track of the data for each objective. To
        # do this it needs HAL comm access. It also needs the item store so that
        # it can handle moving and resizing the images when the user changes values
        # in the UI.
        self.objectives = self.mosaic.ui.objectivesGroupBox
        self.objectives.postInitialization(comm_object = self.comm,
                                           item_store = self.item_store)

        # The image capture object needs the objectives object.
        self.image_capture.postInitialization(objectives = self.objectives)

        # Configure to use standard image/movie loader.
        movie_loader = imageItem.ImageItemLoaderHAL(objectives = self.objectives)
        self.image_capture.setMovieLoaderTaker(movie_loader = movie_loader,
                                               movie_taker = imageCapture.SingleMovieCapture)
        
        # Positions
        #
        # This is created separately but is contained inside the Mosaic tab.
        #
        self.positions = positions.Positions(item_store = self.item_store,
                                             parameters = self.parameters)
        pos_group_box = self.mosaic.getPositionsGroupBox()
        self.positions.setTitleBar(pos_group_box)
        layout = QtWidgets.QVBoxLayout(pos_group_box)
        layout.addWidget(self.positions)
        layout.setContentsMargins(0,0,0,0)
        pos_group_box.setLayout(layout)
        self.modules.append(self.positions)

        # Sections
        #
        # This is the second tab.
        #
        self.sections = sections.Sections(comm = self.comm,
                                          image_capture = self.image_capture,
                                          item_store = self.item_store,
                                          parameters = self.parameters)
        layout = QtWidgets.QVBoxLayout(self.ui.sectionsTab)
        layout.addWidget(self.sections)
        layout.setContentsMargins(0,0,0,0)
        self.ui.sectionsTab.setLayout(layout)
        self.modules.append(self.sections)

        #
        # UI Signals
        #
        self.ui.tabWidget.currentChanged.connect(self.handleTabChange)

        # File
        self.ui.actionDelete_Images.triggered.connect(self.handleDeleteImages)
        self.ui.actionMake_Mask.triggered.connect(self.handleMakeMask)
        self.ui.actionLoad_Movies.triggered.connect(self.handleLoadMovies)
        self.ui.actionLoad_Mosaic.triggered.connect(self.handleLoadMosaic)
        self.ui.actionLoad_Positions.triggered.connect(self.handleLoadPositions)
        self.ui.actionQuit.triggered.connect(self.handleQuit)
        self.ui.actionSave_Mosaic.triggered.connect(self.handleSaveMosaic)
        self.ui.actionSave_Positions.triggered.connect(self.handleSavePositions)
        self.ui.actionSave_Snapshot.triggered.connect(self.handleSnapshot)
        self.ui.actionSet_Working_Directory.triggered.connect(self.handleSetWorkingDirectory)

        # Mosaic
        self.ui.actionAdjust_Contrast.triggered.connect(self.mosaic.handleAdjustContrast)

        #
        # Context menu initializatoin.
        #
        menu_items = [["Take Picture", self.mosaic.handleTakeMovie],
                      ["Goto Position", self.mosaic.handleGoToPosition],
                      ["Record Position", self.positions.handleRecordPosition],
                      ["Add Section", self.sections.handleAddSection],
                      ["Query Objective", self.objectives.handleGetObjective],
                      ["Remove Last Picture", self.mosaic.handleRemoveLastPicture],
                      ["Extrapolate", self.mosaic.handleExtrapolate]]

        for elt in menu_items:
            action = QtWidgets.QAction(self.tr(elt[0]), self)
            self.context_menu.addAction(action)
            action.triggered.connect(elt[1])
            self.context_actions.append(action)
        
    @hdebug.debug
    def cleanUp(self):
        self.settings.setValue("position", self.pos())
        self.settings.setValue("size", self.size())

    @hdebug.debug
    def closeEvent(self, event):
        self.cleanUp()
    @hdebug.debug
    def handleMakeMask(self, boolean):
        import napari
        import numpy as np


        def linear_flat_correction(ims,fl=None,reshape=True,resample=4,vec=[0.1,0.15,0.25,0.5,0.75,0.9]):
            #correct image as (im-bM[1])/bM[0]
            #ims=np.array(ims)
            if reshape:
                ims_pix = np.reshape(ims,[ims.shape[0]*ims.shape[1],ims.shape[2],ims.shape[3]])
            else:
                ims_pix = np.array(ims[::resample])
            ims_pix_sort = np.sort(ims_pix[::resample],axis=0)
            ims_perc = np.array([ims_pix_sort[int(frac*len(ims_pix_sort))] for frac in vec])
            i1,i2=np.array(np.array(ims_perc.shape)[1:]/2,dtype=int)
            x = ims_perc[:,i1,i2]
            X = np.array([x,np.ones(len(x))]).T
            y=ims_perc
            a = np.linalg.inv(np.dot(X.T,X))
            cM = np.swapaxes(np.dot(X.T,np.swapaxes(y,0,-2)),-2,1)
            bM = np.swapaxes(np.dot(a,np.swapaxes(cM,0,-2)),-2,1)
            if fl is not None:
                folder = os.path.dirname(fl)
                if not os.path.exists(folder):
                    os.makedirs(folder)
                pickle.dump(bM,open(fl,'wb'))
            return bM    
        def compose_mosaic(ims,xs_um,ys_um,ims_c=None,um_per_pix=0.108333,rot = 0):
            dtype = np.float32
            im_ = ims[0]
            szs = im_.shape
            sx,sy = szs[-2],szs[-1]
            ### Apply rotation:
            theta=-np.deg2rad(rot)
            xs_um_ = np.array(xs_um)*np.cos(theta)-np.array(ys_um)*np.sin(theta)
            ys_um_ = np.array(ys_um)*np.cos(theta)+np.array(xs_um)*np.sin(theta)
            ### Calculate per pixel
            xs_pix = np.array(xs_um_)/um_per_pix
            xs_pix = np.array(xs_pix-np.min(xs_pix),dtype=int)
            ys_pix = np.array(ys_um_)/um_per_pix
            ys_pix = np.array(ys_pix-np.min(ys_pix),dtype=int)
            sx_big = np.max(xs_pix)+sx+1
            sy_big = np.max(ys_pix)+sy+1
            dim = [sx_big,sy_big]
            if len(szs)==3:
                dim = [szs[0],sx_big,sy_big]
            
            if ims_c is None:
                if len(ims)>25:
                    try:
                        ims_c = linear_flat_correction(ims,fl=None,reshape=False,resample=1,vec=[0.1,0.15,0.25,0.5,0.65,0.75,0.9])
                    except:
                        imc_c = np.median(ims,axis=0)
                else:
                    ims_c = np.median(ims,axis=0)
            
            im_big = np.zeros(dim,dtype = dtype)
            
            for i,(im_,x_,y_) in enumerate(zip(ims,xs_pix,ys_pix)):
                if ims_c is not None:
                    if len(ims_c)==2:
                        im_coef,im_inters = np.array(ims_c,dtype = 'float32')
                        im__=(np.array(im_,dtype = 'float32')-im_inters)/im_coef
                    else:
                        ims_c_ = np.array(ims_c,dtype = 'float32')
                        im__=np.array(im_,dtype = 'float32')/ims_c_*np.median(ims_c_)
                else:
                    im__=np.array(im_,dtype = 'float32')
                im__ = np.array(im__,dtype = dtype)
                im_big[...,x_:x_+sx,y_:y_+sy]=im__
            return im_big
        
        ###### Actual computation
        obj_name = None
        layers = [] 
        ims,x_ums,y_ums = [],[],[]
        for i, elt in enumerate(self.item_store.itemIterator()):
            if 'imageItem' in str(type(elt)):
                dic = elt.getDict()
                obj_name = dic['objective_name']
                im = dic['numpy_data'].T
                ims.append(im)
                x_ums.append(dic['x_um'])
                y_ums.append(dic['y_um'])
                vmin = dic['pixmap_min']
                vmax = dic['pixmap_max']
                self.fov_sz_low = np.array(im.shape)
        if obj_name is not None:
            dic_obj={}
            for i, elt in enumerate(self.item_store.itemIterator()):
                if 'ObjectiveItem' in str(type(elt)):
                    dic_obj[elt.objective_name] = elt.getData()
                    
            um_per_pixel = dic_obj[obj_name][0]
            drift = dic_obj[obj_name][1:]
            target_um_per_pixel = np.min([e[0] for e in dic_obj.values()])


            imf = compose_mosaic(ims,x_ums,y_ums,ims_c=None,um_per_pix=um_per_pixel,rot = 0)

            #self.im_msk=imf
            self.x_ums=x_ums
            self.y_ums=y_ums
            self.drift = drift
            self.um_per_pixel=um_per_pixel
            self.target_um_per_pixel=target_um_per_pixel
            #with napari.gui_qt(): 
            self.viewer = napari.Viewer(title="Make Mask")
            self.viewer.add_image(imf,contrast_limits=[vmin,vmax],name='base_mosaic')
            
            self.viewer.window._qt_window.steve = self
            napari.run()
            ### get the mask image
    def addpositions_mask(self):
        # Modified C:\Users\Bogdan\anaconda3\envs\cellpose\Lib\site-packages\napari\_qt\qt_main_window.py
        import numpy as np
        viewer = self.viewer
        layers = [layer for layer in viewer.layers if 'base_mosaic' not in layer.name]
        im_msk = None
        if len(layers)>0:
            layer = layers[-1]
            if 'shape' in str(type(layer)):
                im_msk = np.sum(layer.to_masks(),axis=0)>0
            elif 'image' in str(type(layer)):
                im_msk = layer.data>0
            elif 'labels' in str(type(layer)):
                im_msk = layer.data>0
                
                
        ##TSP
        # Cities are represented as Points, which are represented as complex numbers
        Point = complex
        def X(point): 
            "The x coordinate of a point."
            return point.real
        def Y(point): 
            "The y coordinate of a point."
            return point.imag

        def distance(A, B): 
            "The distance between two points."
            return abs(A - B)
        def nn_tsp(cities):
            """Start the tour at the first city; at each step extend the tour 
            by moving from the previous city to its nearest neighbor 
            that has not yet been visited."""
            start = first(cities)
            tour = [start]
            unvisited = set(cities - {start})
            while unvisited:
                C = nearest_neighbor(tour[-1], unvisited)
                tour.append(C)
                unvisited.remove(C)
            return tour
        def first(collection):
            "Start iterating over collection, and return the first element."
            return next(iter(collection))
        def nearest_neighbor(A, cities):
            "Find the city in cities that is nearest to city A."
            return min(cities, key=lambda c: distance(c, A))
        def greedy_tsp(cities):
            """Go through edges, shortest first. Use edge to join segments if possible."""
            edges = shortest_edges_first(cities) # A list of (A, B) pairs
            endpoints = {c: [c] for c in cities} # A dict of {endpoint: segment}
            for (A, B) in edges:
                if A in endpoints and B in endpoints and endpoints[A] != endpoints[B]:
                    new_segment = join_endpoints(endpoints, A, B)
                    if len(new_segment) == len(cities):
                        return new_segment
        def shortest_edges_first(cities):
            "Return all edges between distinct cities, sorted shortest first."
            edges = [(A, B) for A in cities for B in cities 
                            if id(A) < id(B)]
            return sorted(edges, key=lambda edge: distance(*edge))
        def join_endpoints(endpoints, A, B):
            "Join B's segment onto the end of A's and return the segment. Maintain endpoints dict."
            Asegment, Bsegment = endpoints[A], endpoints[B]
            if Asegment[-1] is not A: Asegment.reverse()
            if Bsegment[0] is not B: Bsegment.reverse()
            Asegment.extend(Bsegment)
            del endpoints[A], endpoints[B]
            endpoints[Asegment[0]] = endpoints[Asegment[-1]] = Asegment
            return Asegment

        def alter_tour(tour):
            "Try to alter tour for the better by reversing segments."
            original_length = tour_length(tour)
            for (start, end) in all_segments(len(tour)):
                reverse_segment_if_better(tour, start, end)
            # If we made an improvement, then try again; else stop and return tour.
            if tour_length(tour) < original_length:
                return alter_tour(tour)
            return tour

        def all_segments(N):
            "Return (start, end) pairs of indexes that form segments of tour of length N."
            return [(start, start + length)
                    for length in range(N, 2-1, -1)
                    for start in range(N - length + 1)]
        def tour_length(tour):
            "The total of distances between each pair of consecutive cities in the tour."
            return sum(distance(tour[i], tour[i-1]) 
                       for i in range(len(tour)))
        def reverse_segment_if_better(tour, i, j):
            "If reversing tour[i:j] would make the tour shorter, then do it." 
            # Given tour [...A-B...C-D...], consider reversing B...C to get [...A-C...B-D...]
            A, B, C, D = tour[i-1], tour[i], tour[j-1], tour[j % len(tour)]
            # Are old edges (AB + CD) longer than new ones (AC + BD)? If so, reverse segment.
            if distance(A, B) + distance(C, D) > distance(A, C) + distance(B, D):
                tour[i:j] = reversed(tour[i:j])
        ##

        def get_tsp(xy,first_pos=-1,plt_val=True):
            cities = frozenset(Point(x_,y_) for x_,y_ in xy)
            #be greedy first
            cities_sort=greedy_tsp(cities)
            #alter
            cities_sort=alter_tour(cities_sort)
            x=[val.real for val in cities_sort]
            y=[val.imag for val in cities_sort]
            itinerary = np.array([x,y]).T
            # new file to save

            last_pos = np.where(np.all(np.array(itinerary)==[xy[first_pos]],-1))[0][0]
            itinerary = np.roll(itinerary,-last_pos,axis=0)
            if plt_val:
                plt.plot(itinerary[:,0], itinerary[:,1],'o-')
                plt.show()
            return itinerary
        def flatten(list_):
            return [item for sublist in list_ for item in sublist]
        def grab_block(im,center,block_sizes):
            dims = im.shape
            slices = []
            def in_dim(c,dim):
                c_ = c
                if c_<0: c_=0
                if c_>dim: c_=dim
                return c_
            block_sizes_ = [int(bl) for bl in block_sizes]
            for c,block,dim in zip(center,block_sizes_,dims):
                block_ = int(block/2)
                c=int(c)
                c_min,c_max = in_dim(c-block_,dim),in_dim(c+block-block_,dim)
                slices.append(slice(c_min,c_max))
            slices.append(Ellipsis)
            return im[slices]
        def get_positions(image,pos_ims,low_mag_pixel_size=0.108333*6,high_mag_pixel_size = 0.108333,
                          fov_sz = [3200,3200],
                          perc_overlap=0.95,
                          start_pos=[0,0],
                          drift=[0,0],tag='circular'):
            #low_mag_pixel_size = 0.108333*6 #pixel size of lowmag
            #high_mag_pixel_size = 0.108333#*6/4.
            xoffset,yoffset = drift #offset between low mag and high mag objectives

            x_start,y_start = start_pos#0,0 #starting positions



            xsz,ysz=image.shape

            obj_ratio=low_mag_pixel_size/high_mag_pixel_size
            sz=np.array(fov_sz)/obj_ratio*perc_overlap
            
            sz_=np.array(fov_sz)/obj_ratio
            im_=np.array(image)
            xcand = np.arange(0,int(im_.shape[0]),int(sz[0]))+sz[0]/2
            ycand = np.arange(0,int(im_.shape[1]),int(sz[1]))+sz[1]/2
            x,y = zip(*[(x__,y__) for x__ in xcand 
                            for y__ in ycand 
                            if np.sum(grab_block(im_,[x__,y__],sz_))])
            
            if len(x)>3:
                Point = complex
                cities = frozenset(Point(x[i],y[i]) for i in range(len(x)))
                cities_sort=alter_tour(greedy_tsp(cities))
                x2=[val.real for val in cities_sort]
                y2=[val.imag for val in cities_sort]
            else:
                x2,y2=x,y
            x_ = np.array(x2)#sorted positions in pixels 
            y_ = np.array(y2)#sorted positions in pixels 

            
            pix_sz=low_mag_pixel_size
            
            starts = np.min(pos_ims,axis=0)
            offset = fov_sz/2*low_mag_pixel_size

            x = starts[0]-offset[0]+x_*pix_sz+xoffset#This is -X offset
            y = starts[1]-offset[1]+y_*pix_sz+yoffset#This is -Y offset

            #####################################################################           XMIN             #########################



            #x_min,x_max=-np.inf,np.inf
            def select_points(x,y,delta_x_keep):
                x_min = np.mean(x)-delta_x_keep/2.
                x_max = np.mean(x)+delta_x_keep/2.
                x_keep,y_keep = [],[]
                for x_t,y_t in zip(x,y):
                    if x_t>x_min and x_t<x_max:
                        x_keep.append(x_t)
                        y_keep.append(y_t)
                x_keep = np.array(x_keep)
                y_keep = np.array(y_keep)
                return x_keep,y_keep
            x_keep,y_keep = select_points(x,y,np.inf)
            #plt.plot(y_keep,x_keep,'ro')
            #x_keep,y_keep = select_points(x,y,11000.)





            ind0 = np.argmin((x_keep-x_start)**2+(y_keep-y_start)**2)################can modify to change the start position

            x_keep = np.roll(x_keep,-ind0)
            y_keep = np.roll(y_keep,-ind0)

            if False:#tag=='circular':
                R = 9600
                cm = [0,0]
                #select the points in circle
                keep2 = (x_keep-cm[0])**2+(y_keep-cm[1])**2<R**2
                x_keep,y_keep=x_keep[keep2],y_keep[keep2]


            x_keep = list(x_keep)+[x_keep[0],x_keep[1]]########################## repeat first 2 position
            y_keep = list(y_keep)+[y_keep[0],y_keep[1]]########################## repeat first 2 position
            
            
            
            if False:
                filename=os.path.dirname(folder)+os.sep+r'pos.txt'
                fid=open(filename,'w')
                for x_t,y_t in zip(x_keep,y_keep):
                    fid.write(str(x_t)+','+str(y_t)+'\n')
                fid.close()
                print("Number of snaps:" + str(len(x_keep)))

            if False:
                import matplotlib.pylab as plt
                plt.figure(figsize=(10,10))
                plt.plot(y_keep,x_keep,'g-o')
                plt.plot(y_keep[0],x_keep[0],'ro')
                xcirc,ycirc = (cm[::-1]+R*np.array([[np.cos(th),np.sin(th)] for th in np.linspace(0,2*np.pi,100)])).T
                plt.plot(xcirc,ycirc,'b')
                plt.axis('equal')
                plt.show()
            
            return np.round(x_keep,2),np.round(y_keep,2)
        if im_msk is not None:
            self.im_msk = im_msk
            xkp,ykp = get_positions(im_msk,np.array([self.x_ums,self.y_ums]).T,
                      low_mag_pixel_size=self.um_per_pixel,
                      high_mag_pixel_size =self.target_um_per_pixel,
                      drift=self.drift,
                      fov_sz = self.fov_sz_low,
                      perc_overlap=0.95,
                      start_pos=[0,0],
                      tag='circular')
            import storm_control.steve.coord as coord
            for x,y in zip(xkp,ykp):
                self.positions.addPosition(coord.Point(float(x), float(y), "um"))
        else:
            print("Did not find mask!")
        
        
    @hdebug.debug
    def handleDeleteImages(self, boolean):
        reply = QtWidgets.QMessageBox.question(self,
                                               "Warning!",
                                               "Delete Images?",
                                               QtWidgets.QMessageBox.Yes,
                                               QtWidgets.QMessageBox.No)
        if (reply == QtWidgets.QMessageBox.Yes):
            self.item_store.removeItemType(imageItem.ImageItem)

    @hdebug.debug
    def handleLoadMosaic(self, boolean):
        mosaic_filename = QtWidgets.QFileDialog.getOpenFileName(self,
                                                                "Load Mosaic",
                                                                self.parameters.get("directory"),
                                                                "*.msc")[0]
        if mosaic_filename:
            self.loadMosaic(mosaic_filename)

    @hdebug.debug
    def handleLoadMovies(self, boolean):
        # Open custom dialog to select files and frame number
        [filenames, frame_num, file_filter] = qtRegexFileDialog.regexGetFileNames(directory = self.parameters.get("directory"),
                                                                                  regex = self.regexp_str,
                                                                                  extensions = ["*.dax", "*.tif", "*.spe"])
        if (filenames is not None) and (len(filenames) > 0):
            print("Found " + str(len(filenames)) + " files matching " + str(file_filter) + " in " + os.path.dirname(filenames[0]))
            print("Loading frame: " + str(frame_num))

            # Save regexp string for next time the dialog is opened
            self.regexp_str = file_filter
                
            # Load movies
            self.image_capture.loadMovies(filenames, frame_num)

    @hdebug.debug
    def handleLoadPositions(self, boolean):
        positions_filename = QtWidgets.QFileDialog.getOpenFileName(self,
                                                                   "Load Positions",
                                                                   self.parameters.get("directory"),
                                                                   "*.txt")[0]
        if positions_filename:
            self.positions.loadPositions(positions_filename)

    @hdebug.debug
    def handleMosaicPositionRequest(self, position_dict):
        positions = imageCapture.createGrid(position_dict['x_grid'], position_dict['y_grid'], include_center=True)
        for pos in positions:
            x = position_dict['grid_spacing']*pos[0] + position_dict['x_center']
            y = position_dict['grid_spacing']*pos[1] + position_dict['y_center']
            self.positions.addPosition(coord.Point(float(x), float(y), "um"))

    @hdebug.debug
    def handleMosaicViewContextMenuEvent(self, event, a_coord):
        for elt in self.modules:
            elt.setMosaicEventCoord(a_coord)
        self.context_menu.exec_(event.globalPos())

    @hdebug.debug
    def handleMosaicViewDropEvent(self, filenames_list):

        file_type = os.path.splitext(filenames_list[0])[1]

        # Check for .dax files.
        if (file_type == '.dax') or (file_type == ".tif"):
            self.image_capture.loadMovies(filenames_list, 0)

        # Check for mosaic files.
        elif (file_type == '.msc'):
            for filename in sorted(filenames_list):
                self.loadMosaic(filename)

        else:
            hdebug.logText(" " + file_type + " is not recognized")
            QtGui.QMessageBox.information(self,
                                          "File type not recognized",
                                          "")

    @hdebug.debug
    def handleMosaicViewKeyPressEvent(self, event, a_coord):
        for elt in self.modules:
            elt.setMosaicEventCoord(a_coord)
            
        # Picture taking
        if (event.key() == QtCore.Qt.Key_Space):
            self.mosaic.handleTakeMovie(None)
        elif (event.key() == QtCore.Qt.Key_3):
            self.mosaic.handleTakeSpiral(3)
        elif (event.key() == QtCore.Qt.Key_5):
            self.mosaic.handleTakeSpiral(5)
        elif (event.key() == QtCore.Qt.Key_7):
            self.mosaic.handleTakeSpiral(7)
        elif (event.key() == QtCore.Qt.Key_9):
            self.mosaic.handleTakeSpiral(9)
        elif (event.key() == QtCore.Qt.Key_G):
            self.mosaic.handleTakeGrid()

        # Record position
        elif (event.key() == QtCore.Qt.Key_P):
            self.positions.handleRecordPosition(None)

        # Create section
        elif (event.key() == QtCore.Qt.Key_S):
            self.sections.handleAddSection(None)

        # Record center for new positions grid
        elif (event.key() == QtCore.Qt.Key_N):
            self.mosaic.ui.posCenterSpinX.setValue(a_coord.x_um)
            self.mosaic.ui.posCenterSpinY.setValue(a_coord.y_um)

        # Pass commands back to positions (to coordinate move/delete of selected positions)
        self.positions.keyPressEvent(event)

    @hdebug.debug
    def handleMosaicViewSelectionChange(self, selected_items):
        self.positions.toggleSelectionForSelectedGraphicsItems(selected_items)

    @hdebug.debug
    def handleQuit(self, boolean):
        self.close()

    @hdebug.debug
    def handleSavePositions(self, boolean):
        positions_filename = QtWidgets.QFileDialog.getSaveFileName(self, 
                                                                   "Save Positions", 
                                                                   self.parameters.get("directory"), 
                                                                   "*.txt")[0]
        if positions_filename:
            self.positions.savePositions(positions_filename)

    @hdebug.debug
    def handleSaveMosaic(self, boolean):
        mosaic_filename = QtWidgets.QFileDialog.getSaveFileName(self,
                                                                "Save Mosaic", 
                                                                self.parameters.get("directory"),
                                                                "*.msc")[0]
        if mosaic_filename:
            self.item_store.saveMosaic(mosaic_filename)

    @hdebug.debug
    def handleSetWorkingDirectory(self, boolean):
        directory = QtWidgets.QFileDialog.getExistingDirectory(self,
                                                               "New Directory",
                                                               str(self.parameters.get("directory")),
                                                               QtWidgets.QFileDialog.ShowDirsOnly)
        if directory:
            self.image_capture.setDirectory(directory)
            self.snapshot_directory = directory + os.path.sep

    @hdebug.debug
    def handleSnapshot(self, boolean):
        snapshot_filename = QtWidgets.QFileDialog.getSaveFileName(self, 
                                                                  "Save Snapshot", 
                                                                  self.snapshot_directory, 
                                                                  "*.png")[0]
        if snapshot_filename:
            pixmap = self.mosaic.mosaic_view.grab()
            pixmap.save(snapshot_filename)

            self.snapshot_directory = os.path.dirname(snapshot_filename)

    def handleTabChange(self, tab_index):
        for elt in self.modules:
            elt.currentTabChanged(tab_index)

    def loadMosaic(self, mosaic_filename):
        self.image_capture.mosaicLoaded()
        if self.item_store.loadMosaic(mosaic_filename):
            for elt in self.modules:
                elt.mosaicLoaded()


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)

    # Load settings.
    if (len(sys.argv)==2):
        parameters = params.parameters(sys.argv[1])
    else:
        parameters = params.parameters("settings_default.xml")

    # Start logger.
    hdebug.startLogging(parameters.get("directory") + "logs/", "steve")

    # Load app.
    window = Window(parameters = parameters)
    window.show()
    app.exec_()


#
# The MIT License
#
# Copyright (c) 2013 Zhuang Lab, Harvard University
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
