"""Reduced mask previews with acquisition geometry in original stage microns.

As in Steve's original Make Mask code, array axes are (stage X, stage Y).
Preview resolution never changes the source FOV or acquisition grid spacing.
"""

from dataclasses import dataclass
import math
import numpy as np


@dataclass
class MaskMosaic:
    image: np.ndarray
    pixel_size_um: float
    origin_um: np.ndarray
    extent_um: np.ndarray
    target_fov_um: np.ndarray
    source_shape: tuple
    source_pixel_size_um: float
    downsample: int
    contrast_limits: tuple


def validate_downsample(value):
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value < 1:
        raise ValueError("mask_downsample must be an integer greater than or equal to 1")
    return int(value)


def downsample_image(image, factor):
    """Subsample before converting/copying pixels or fitting illumination."""
    factor = validate_downsample(factor)
    data = np.asarray(image)
    if data.ndim != 2 or min(data.shape) < 1:
        raise ValueError("Make Mask requires nonempty two-dimensional images")
    return np.array(data[::factor, ::factor], dtype=np.float32, order="C", copy=True)


def illumination_model(tiles):
    """Original percentile/central-pixel regression, on reduced tiles only."""
    stack = np.stack(tiles)
    if len(tiles) > 25:
        sorted_tiles = np.sort(stack, axis=0)
        indices = (np.array([0.1, 0.15, 0.25, 0.5, 0.65, 0.75, 0.9]) * len(tiles)).astype(int)
        samples = sorted_tiles[indices]
        x = samples[:, samples.shape[1] // 2, samples.shape[2] // 2].astype(np.float64)
        centered = x - x.mean()
        variance = np.dot(centered, centered)
        if variance > np.finfo(float).eps:
            gain = np.tensordot(centered, samples, axes=(0, 0)) / variance
            offset = samples.mean(axis=0) - gain * x.mean()
            valid = np.isfinite(gain) & (gain > 1.0e-6) & np.isfinite(offset)
            return np.where(valid, gain, 1.0).astype(np.float32), np.where(valid, offset, 0.0).astype(np.float32)
    # Also handles a singular fit (all center-pixel intensities identical).
    background = np.median(stack, axis=0)
    positive = background[np.isfinite(background) & (background > 0)]
    reference = float(np.median(positive)) if positive.size else 1.0
    gain = background / reference
    valid = np.isfinite(gain) & (gain > 1.0e-6)
    return np.where(valid, gain, 1.0).astype(np.float32), np.zeros_like(background)


def build_mosaic(image_items, objective_data, downsample=1, flat_field=True):
    """Read ImageItem dictionaries without making full-resolution pixel copies."""
    factor = validate_downsample(downsample)
    tiles, centers = [], []
    objective_name, source_shape, contrast = None, None, None
    for item in image_items:
        data = item.getDict()
        name = data["objective_name"]
        original = np.asarray(data["numpy_data"])
        shape = tuple(original.T.shape)
        if objective_name is not None and name != objective_name:
            raise ValueError("Make Mask requires images from one objective at a time")
        if source_shape is not None and shape != source_shape:
            raise ValueError("Make Mask requires images with the same original dimensions")
        objective_name, source_shape = name, shape
        # Match Steve's existing mask orientation, then reduce immediately.
        tiles.append(downsample_image(original.T, factor))
        centers.append((float(data["x_um"]), float(data["y_um"])))
        contrast = (float(data["pixmap_min"]), float(data["pixmap_max"]))
    if not tiles:
        raise ValueError("Load or capture images before choosing Make Mask")
    if objective_name not in objective_data:
        raise ValueError("The mosaic is missing calibration for objective " + str(objective_name))
    source_pixel, dx, dy = map(float, objective_data[objective_name])
    target_pixel = min(float(value[0]) for value in objective_data.values())
    if not all(math.isfinite(value) for value in (source_pixel, target_pixel, dx, dy)) or min(source_pixel, target_pixel) <= 0:
        raise ValueError("Objective pixel sizes must be finite and positive, with finite offsets")
    centers = np.asarray(centers, dtype=float)
    if not np.isfinite(centers).all():
        raise ValueError("Image stage positions must be finite")
    pixel_size = source_pixel * factor
    minimum = centers.min(axis=0)
    starts = np.floor((centers - minimum) / pixel_size).astype(int)
    shape = starts.max(axis=0) + np.asarray(tiles[0].shape) + 1
    image = np.zeros(tuple(shape), dtype=np.float32)
    model = illumination_model(tiles) if flat_field else None
    for tile, (x, y) in zip(tiles, starts):
        if model is not None:
            tile = (tile - model[1]) / model[0]
        image[x:x + tile.shape[0], y:y + tile.shape[1]] = tile
    source_fov = np.asarray(source_shape, dtype=float) * source_pixel
    # Keep original FOV geometry even when dimensions are not divisible by factor.
    origin = minimum - source_fov / 2.0 + np.array([dx, dy])
    extent = centers.max(axis=0) - minimum + source_fov
    target_fov = np.asarray(source_shape, dtype=float) * target_pixel
    return MaskMosaic(image, pixel_size, origin, extent, target_fov,
                      source_shape, source_pixel, factor, contrast)


def candidate_positions(mask, mosaic, overlap=0.95):
    """Select a grid defined in microns; only ROI testing uses preview pixels."""
    mask = np.asarray(mask, dtype=bool)
    if mask.shape != mosaic.image.shape:
        raise ValueError("Mask dimensions do not match the Make Mask preview")
    if not math.isfinite(overlap) or not 0 < overlap <= 1:
        raise ValueError("Mask field spacing must be greater than 0 and at most 1")
    if not mask.any():
        return np.empty((0, 2), dtype=float)
    step = mosaic.target_fov_um * overlap
    axes = [np.arange(int(np.ceil(extent / spacing))) * spacing + spacing / 2.0
            for extent, spacing in zip(mosaic.extent_um, step)]
    selected = []
    for row, x in enumerate(axes[0]):
        # Serpentine order is also a bounded-memory route for large selections.
        y_axis = axes[1] if row % 2 == 0 else axes[1][::-1]
        for y in y_axis:
            center = np.array([x, y])
            lower = np.floor((center - mosaic.target_fov_um / 2.0) / mosaic.pixel_size_um).astype(int)
            upper = np.ceil((center + mosaic.target_fov_um / 2.0) / mosaic.pixel_size_um).astype(int)
            lower = np.maximum(lower, 0)
            upper = np.minimum(upper, mask.shape)
            if np.all(upper > lower) and mask[lower[0]:upper[0], lower[1]:upper[1]].any():
                selected.append(mosaic.origin_um + center)
    return np.asarray(selected, dtype=float).reshape((-1, 2))


def order_positions(xy, start=(0.0, 0.0), optimize_limit=250):
    """Keep the old greedy/2-opt route for small masks; bound large-mask work.

    All candidate locations are retained. Large inputs arrive in serpentine
    grid order and avoid the old quadratic list of every possible edge.
    """
    xy = np.asarray(xy, dtype=float).reshape((-1, 2))
    if not len(xy):
        return xy
    if 3 < len(xy) <= optimize_limit:
        points = [complex(x, y) for x, y in xy]
        endpoints = {point: [point] for point in points}
        edges = sorted(((abs(a - b), i, j) for i, a in enumerate(points)
                        for j, b in enumerate(points) if i < j), key=lambda edge: edge[0])
        tour = points
        for _, i, j in edges:
            a, b = points[i], points[j]
            if a in endpoints and b in endpoints and endpoints[a] != endpoints[b]:
                left, right = endpoints[a], endpoints[b]
                if left[-1] != a:
                    left.reverse()
                if right[0] != b:
                    right.reverse()
                left.extend(right)
                del endpoints[a], endpoints[b]
                endpoints[left[0]] = endpoints[left[-1]] = left
                if len(left) == len(points):
                    tour = left
                    break
        # Bound refinement to prevent a large selection from blocking closure.
        for _ in range(20):
            improved = False
            for length in range(len(tour), 1, -1):
                for i in range(len(tour) - length + 1):
                    j = i + length
                    a, b, c, d = tour[i - 1], tour[i], tour[j - 1], tour[j % len(tour)]
                    if abs(a - b) + abs(c - d) > abs(a - c) + abs(b - d) + 1.0e-9:
                        tour[i:j] = reversed(tour[i:j])
                        improved = True
            if not improved:
                break
        xy = np.asarray([(point.real, point.imag) for point in tour])
    first = np.argmin(np.sum((xy - np.asarray(start)) ** 2, axis=1))
    xy = np.roll(xy, -int(first), axis=0)
    # Preserve the old repeat of the first two fields, safely for one field too.
    return np.round(np.concatenate((xy, xy[:2]), axis=0), 2)
