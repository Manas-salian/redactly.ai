"""OCR preprocessing transforms.

Pure functions over OpenCV BGR ndarrays. Run in this order on raw page or
embedded-image rasters before handing them to Tesseract:

    1. deskew_hough     — corrects tilt within ±15°
    2. apply_clahe      — local-contrast normalization
    3. denoise_fast_nl_means — speckle/jpeg-noise removal

Each transform returns a NEW ndarray; inputs are not mutated.
"""

from __future__ import annotations

import cv2
import numpy as np


def deskew_hough(image: np.ndarray, max_angle_deg: float = 15.0) -> np.ndarray:
    """Detect text-line orientation via the Hough transform on edge-detected
    grayscale, then rotate to deskew. Returns the input unmodified if no
    confident skew angle is detected within ±max_angle_deg.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLines(edges, 1, np.pi / 180, threshold=200)
    if lines is None:
        return image

    angles_deg: list[float] = []
    for rho_theta in lines:
        rho, theta = rho_theta[0]
        angle = (theta * 180.0 / np.pi) - 90.0
        if -max_angle_deg <= angle <= max_angle_deg:
            angles_deg.append(angle)

    if not angles_deg:
        return image

    median_angle = float(np.median(angles_deg))
    if abs(median_angle) < 0.25:
        return image

    h, w = image.shape[:2]
    center = (w / 2, h / 2)
    rotation = cv2.getRotationMatrix2D(center, median_angle, 1.0)
    return cv2.warpAffine(
        image, rotation, (w, h),
        flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE,
    )


def apply_clahe(image: np.ndarray, clip_limit: float = 2.0, tile_grid_size: tuple[int, int] = (8, 8)) -> np.ndarray:
    """CLAHE (Contrast Limited Adaptive Histogram Equalization) on the luminance
    channel. Improves OCR on photos and scanned documents with uneven lighting.
    """
    if image.ndim == 2:
        clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=tile_grid_size)
        return clahe.apply(image)

    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=tile_grid_size)
    l = clahe.apply(l)
    return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)


def denoise_fast_nl_means(image: np.ndarray, h_strength: int = 7) -> np.ndarray:
    """Non-local-means denoising. Slower than bilateral filtering but preserves
    text edges better. h_strength of 7-10 is reasonable for scanned docs.
    """
    if image.ndim == 2:
        return cv2.fastNlMeansDenoising(image, h=h_strength, templateWindowSize=7, searchWindowSize=21)
    return cv2.fastNlMeansDenoisingColored(image, h=h_strength, hColor=h_strength, templateWindowSize=7, searchWindowSize=21)


def preprocess_for_ocr(image: np.ndarray) -> np.ndarray:
    """Run the standard preprocessing chain. Each step is independent and can
    be skipped by callers that want only some transforms; this is the
    convenience entrypoint."""
    return denoise_fast_nl_means(apply_clahe(deskew_hough(image)))
