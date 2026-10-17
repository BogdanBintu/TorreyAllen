"""Steve-owned Napari mask completion; no edits to installed Napari files."""

import numpy as np
from PyQt5 import QtCore, QtWidgets


def collect_masks(viewer, base_layer, shapes_type, labels_type):
    """Rasterize shapes in the preview's coordinates, including layer transforms."""
    shape = tuple(base_layer.data.shape)
    masks = []
    for layer in viewer.layers:
        if layer is base_layer:
            continue
        if isinstance(layer, shapes_type):
            if not len(layer.data):
                continue
            vertices = []
            for polygon in layer.data:
                mapped = [base_layer.world_to_data(layer.data_to_world(point)) for point in polygon]
                if any(len(point) != 2 for point in mapped):
                    raise ValueError("Make Mask supports two-dimensional Shapes layers")
                vertices.append(np.asarray(mapped))
            raster = shapes_type(data=vertices, shape_type=layer.shape_type)
            # One labels canvas instead of one full-size canvas per shape.
            mask = np.asarray(raster.to_labels(labels_shape=shape)) > 0
        elif isinstance(layer, labels_type):
            if tuple(layer.data.shape) != shape:
                raise ValueError("A Labels mask must have the same dimensions as the preview")
            for point in ((0, 0), (1, 0), (0, 1)):
                mapped = base_layer.world_to_data(layer.data_to_world(point))
                if not np.allclose(mapped, point):
                    raise ValueError("A Labels mask must use the preview's scale and position")
            mask = np.asarray(layer.data) > 0
        else:
            continue
        if mask.any():
            masks.append(mask)
    return masks


class MaskEditor(QtCore.QObject):
    """Capture ROIs before teardown; commit once after an accepted close."""
    def __init__(self, mosaic, on_complete, on_error, parent=None, napari_module=None):
        super().__init__(parent)
        if napari_module is None:
            import napari as napari_module
        self.mosaic = mosaic
        self.on_complete = on_complete
        self.on_error = on_error
        self.done = False
        self.pending_masks = None
        self.handling_close = False
        self.viewer = None
        self.base_layer = None
        self.qt_window = None
        self.shapes_type = napari_module.layers.Shapes
        self.labels_type = napari_module.layers.Labels
        session = self

        class SteveMaskViewer(napari_module.Viewer):
            def close(self):
                # Viewer.close clears layers before closing its Qt window.
                # Capture first for this public close path as well as the X.
                try:
                    session.capture()
                except Exception as error:
                    session.on_error(str(error))
                    return False
                return super().close()

        self.viewer = SteveMaskViewer(title="Make Mask")
        self.base_layer = self.viewer.add_image(
            mosaic.image, contrast_limits=mosaic.contrast_limits, name="base_mosaic")
        self.qt_window = self.viewer.window._qt_window
        self.qt_window.installEventFilter(self)
        # Compatibility with the user's older custom close hook. Completion
        # is guarded so that hook and this filter cannot append twice.
        self.qt_window.steve = self

    def capture(self):
        if self.viewer is not None and self.base_layer is not None and not self.done and self.pending_masks is None:
            self.pending_masks = collect_masks(self.viewer, self.base_layer,
                                               self.shapes_type, self.labels_type)

    def complete(self):
        if self.done:
            return
        self.capture()
        self.done = True
        try:
            self.on_complete(self.pending_masks or [], self.mosaic)
        finally:
            self.pending_masks = None
            try:
                self.qt_window.removeEventFilter(self)
            except (AttributeError, RuntimeError):
                pass

    def addpositions_mask(self):
        # Called only if an old, locally patched Napari close hook still exists.
        # Capture only: the Steve filter commits after the close is accepted.
        self.capture()

    def eventFilter(self, watched, event):
        # Qt may dispatch events during construction/destruction as well.
        if (watched is getattr(self, "qt_window", None) and
                event.type() == QtCore.QEvent.Close and not self.handling_close):
            try:
                self.capture()
            except Exception as error:
                self.pending_masks = None
                self.on_error(str(error))
                event.ignore()
                return True
            # Deliver the real close handler exactly once, then inspect its
            # decision. This respects Napari's Cancel confirmation without
            # scheduling a callback inside its nested confirmation event loop.
            self.handling_close = True
            try:
                QtWidgets.QApplication.sendEvent(watched, event)
            finally:
                self.handling_close = False
            if event.isAccepted():
                try:
                    self.complete()
                except Exception as error:
                    self.on_error("Could not finish Make Mask: " + str(error))
            else:
                self.pending_masks = None
            return True
        return super().eventFilter(watched, event)

    def isOpen(self):
        if self.done:
            return False
        try:
            return self.qt_window.isVisible()
        except RuntimeError:
            return False
