"""Faithful port of smartcrop.js for server-side focal point detection.

Translates jonaswagner/smartcrop.js line-by-line using Pillow and numpy.
Preserves internal clamping, prescaling, and cie() weighting so crop results
match client-side smartcrop exactly.
"""

import logging
import math
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

DEFAULTS = {
    "width": 0,
    "height": 0,
    "aspect": 0,
    "cropWidth": 0,
    "cropHeight": 0,
    "detailWeight": 0.2,
    "skinColor": [0.78, 0.57, 0.44],
    "skinBias": 0.01,
    "skinBrightnessMin": 0.2,
    "skinBrightnessMax": 1.0,
    "skinThreshold": 0.8,
    "skinWeight": 1.8,
    "saturationBrightnessMin": 0.05,
    "saturationBrightnessMax": 0.9,
    "saturationThreshold": 0.4,
    "saturationBias": 0.2,
    "saturationWeight": 0.1,
    "scoreDownSample": 8,
    "step": 8,
    "scaleStep": 0.1,
    "minScale": 1.0,
    "maxScale": 1.0,
    "edgeRadius": 0.4,
    "edgeWeight": -20.0,
    "outsideImportance": -0.5,
    "boostWeight": 100.0,
    "ruleOfThirds": True,
    "prescale": True,
    "boost": None,
}


def cie(r, g, b):
    """Note: Blue is weighted highest, red lowest.

    This matches smartcrop.js line 561 (0.5126*b + 0.7152*g + 0.0722*r), which
    differs from standard Rec.709. Do not "fix" this, as baseline parity depends
    on this exact formula.
    """
    return 0.5126 * b + 0.7152 * g + 0.0722 * r


def saturation(r, g, b):
    """Saturation formula matching smartcrop.js lines 566-578."""
    r_f = r / 255.0
    g_f = g / 255.0
    b_f = b / 255.0

    maximum = max(r_f, g_f, b_f)
    minimum = min(r_f, g_f, b_f)

    if maximum == minimum:
        return 0.0

    l = (maximum + minimum) / 2.0
    d = maximum - minimum

    return d / (2.0 - maximum - minimum) if l > 0.5 else d / (maximum + minimum)


def edge_detect(input_rgba, output_rgba):
    """Laplacian edge detection writing to green channel (channel 1), clamped to 0-255."""
    h, w = input_rgba.shape[:2]
    r = input_rgba[:, :, 0].astype(np.float64)
    g = input_rgba[:, :, 1].astype(np.float64)
    b = input_rgba[:, :, 2].astype(np.float64)
    # cie lightness map
    luma = 0.5126 * b + 0.7152 * g + 0.0722 * r

    # pad 1px around matching border sample(id, p)
    padded = np.pad(luma, ((1, 1), (1, 1)), mode='edge')
    centre = padded[1:-1, 1:-1]
    up = padded[0:-2, 1:-1]
    down = padded[2:, 1:-1]
    left = padded[1:-1, 0:-2]
    right = padded[1:-1, 2:]

    edge_val = centre * 4.0 - up - left - right - down

    border_mask = np.zeros((h, w), dtype=bool)
    border_mask[0, :] = True
    border_mask[-1, :] = True
    border_mask[:, 0] = True
    border_mask[:, -1] = True

    lightness = np.where(border_mask, centre, edge_val)
    output_rgba[:, :, 1] = np.clip(np.round(lightness), 0, 255).astype(np.uint8)


def skin_detect(options, input_rgba, output_rgba):
    """Skin detection writing to red channel (channel 0), clamped to 0-255."""
    r = input_rgba[:, :, 0].astype(np.float64)
    g = input_rgba[:, :, 1].astype(np.float64)
    b = input_rgba[:, :, 2].astype(np.float64)

    lightness = (0.5126 * b + 0.7152 * g + 0.0722 * r) / 255.0

    mag = np.sqrt(r * r + g * g + b * b)
    mag_safe = np.where(mag == 0, 1.0, mag)
    rd = r / mag_safe - options["skinColor"][0]
    gd = g / mag_safe - options["skinColor"][1]
    bd = b / mag_safe - options["skinColor"][2]
    skin = 1.0 - np.sqrt(rd * rd + gd * gd + bd * bd)
    skin = np.where(mag == 0, 0.0, skin)

    is_skin_color = skin > options["skinThreshold"]
    is_skin_brightness = (lightness >= options["skinBrightnessMin"]) & (lightness <= options["skinBrightnessMax"])

    val = np.where(
        is_skin_color & is_skin_brightness,
        (skin - options["skinThreshold"]) * (255.0 / (1.0 - options["skinThreshold"])),
        0.0
    )
    output_rgba[:, :, 0] = np.clip(np.round(val), 0, 255).astype(np.uint8)


def saturation_detect(options, input_rgba, output_rgba):
    """Saturation detection writing to blue channel (channel 2), clamped to 0-255."""
    r = input_rgba[:, :, 0].astype(np.float64)
    g = input_rgba[:, :, 1].astype(np.float64)
    b = input_rgba[:, :, 2].astype(np.float64)

    lightness = (0.5126 * b + 0.7152 * g + 0.0722 * r) / 255.0

    rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
    maximum = np.maximum(np.maximum(rf, gf), bf)
    minimum = np.minimum(np.minimum(rf, gf), bf)

    diff = maximum - minimum
    sum_max_min = maximum + minimum

    sat = np.zeros_like(r)
    mask_diff = diff != 0
    mask_l_gt = mask_diff & (sum_max_min > 1.0)
    mask_l_lte = mask_diff & (~mask_l_gt)

    sat[mask_l_gt] = diff[mask_l_gt] / (2.0 - sum_max_min[mask_l_gt])
    sat[mask_l_lte] = diff[mask_l_lte] / sum_max_min[mask_l_lte]

    acc_sat = sat > options["saturationThreshold"]
    acc_light = (lightness >= options["saturationBrightnessMin"]) & (lightness <= options["saturationBrightnessMax"])

    val = np.where(
        acc_sat & acc_light,
        (sat - options["saturationThreshold"]) * (255.0 / (1.0 - options["saturationThreshold"])),
        0.0
    )
    output_rgba[:, :, 2] = np.clip(np.round(val), 0, 255).astype(np.uint8)


def apply_boosts(options, output_rgba):
    output_rgba[:, :, 3] = 0
    if not options.get("boost"):
        return
    h, w = output_rgba.shape[:2]
    for boost in options["boost"]:
        x0 = max(0, min(w, int(boost["x"])))
        x1 = max(0, min(w, int(boost["x"] + boost["width"])))
        y0 = max(0, min(h, int(boost["y"])))
        y1 = max(0, min(h, int(boost["y"] + boost["height"])))
        weight = int(boost["weight"] * 255)
        if x1 > x0 and y1 > y0:
            curr = output_rgba[y0:y1, x0:x1, 3].astype(np.int32) + weight
            output_rgba[y0:y1, x0:x1, 3] = np.clip(curr, 0, 255).astype(np.uint8)


def down_sample(input_rgba, factor):
    """Downsample input_rgba (h, w, 4) uint8 by factor integer.

    Matches smartcrop.js lines 427-469 Uint8ClampedArray logic.
    """
    h, w = input_rgba.shape[:2]
    out_w = w // factor
    out_h = h // factor
    if out_w == 0 or out_h == 0:
        return np.zeros((out_h, out_w, 4), dtype=np.uint8)

    reshaped = input_rgba[:out_h * factor, :out_w * factor, :4].reshape(out_h, factor, out_w, factor, 4).astype(np.float64)

    sums = reshaped.sum(axis=(1, 3))
    max_r = reshaped[:, :, :, :, 0].max(axis=(1, 3))
    max_g = reshaped[:, :, :, :, 1].max(axis=(1, 3))

    ifactor2 = 1.0 / (factor * factor)
    val_r = sums[:, :, 0] * ifactor2 * 0.5 + max_r * 0.5
    val_g = sums[:, :, 1] * ifactor2 * 0.7 + max_g * 0.3
    val_b = sums[:, :, 2] * ifactor2
    val_a = sums[:, :, 3] * ifactor2

    res = np.stack([val_r, val_g, val_b, val_a], axis=-1)
    return np.clip(np.round(res), 0, 255).astype(np.uint8)


def thirds(x):
    """Rule of thirds weighting matching smartcrop.js line 555."""
    x_val = (np.fmod(x - 1.0 / 3.0 + 1.0, 2.0) * 0.5 - 0.5) * 16.0
    return np.maximum(1.0 - x_val * x_val, 0.0)


def importance_grid(options, crop, out_w, out_h, step=8):
    """Vectorized importance calculation matching smartcrop.js lines 346-368."""
    xs = np.arange(0, out_w * step, step, dtype=np.float64)[None, :]
    ys = np.arange(0, out_h * step, step, dtype=np.float64)[:, None]

    crop_x = crop["x"]
    crop_y = crop["y"]
    crop_w = crop["width"]
    crop_h = crop["height"]

    outside = (xs < crop_x) | (xs >= crop_x + crop_w) | (ys < crop_y) | (ys >= crop_y + crop_h)

    norm_x = (xs - crop_x) / crop_w
    norm_y = (ys - crop_y) / crop_h

    px = np.abs(0.5 - norm_x) * 2.0
    py = np.abs(0.5 - norm_y) * 2.0

    dx = np.maximum(px - 1.0 + options["edgeRadius"], 0.0)
    dy = np.maximum(py - 1.0 + options["edgeRadius"], 0.0)

    d = (dx * dx + dy * dy) * options["edgeWeight"]
    s = 1.41 - np.sqrt(px * px + py * py)

    if options["ruleOfThirds"]:
        t_x = thirds(px)
        t_y = thirds(py)
        s += np.maximum(0.0, s + d + 0.5) * 1.2 * (t_x + t_y)

    imp = s + d
    imp[outside] = options["outsideImportance"]
    return imp


def generate_crops(options, width, height):
    """Generate crop candidates matching smartcrop.js lines 282-304."""
    results = []
    min_dim = min(width, height)
    crop_w = options["cropWidth"] or min_dim
    crop_h = options["cropHeight"] or min_dim

    scale = options["maxScale"]
    step = options["step"]
    scale_step = options["scaleStep"]

    while scale >= options["minScale"] - 1e-9:
        w_s = crop_w * scale
        h_s = crop_h * scale
        y = 0
        while y + h_s <= height:
            x = 0
            while x + w_s <= width:
                results.append({
                    "x": x,
                    "y": y,
                    "width": w_s,
                    "height": h_s
                })
                x += step
            y += step
        scale -= scale_step

    return results


def score(options, output_rgba, crop):
    """Score candidate crop matching smartcrop.js lines 306-344."""
    out_h, out_w = output_rgba.shape[:2]
    down_sample_factor = options["scoreDownSample"]

    imp = importance_grid(options, crop, out_w, out_h, step=down_sample_factor)

    r_val = output_rgba[:, :, 0].astype(np.float64) / 255.0
    g_val = output_rgba[:, :, 1].astype(np.float64) / 255.0
    b_val = output_rgba[:, :, 2].astype(np.float64) / 255.0
    a_val = output_rgba[:, :, 3].astype(np.float64) / 255.0

    detail = g_val
    skin = r_val
    sat = b_val
    boost = a_val

    skin_score = np.sum(skin * (detail + options["skinBias"]) * imp)
    detail_score = np.sum(detail * imp)
    sat_score = np.sum(sat * (detail + options["saturationBias"]) * imp)
    boost_score = np.sum(boost * imp)

    area = crop["width"] * crop["height"]
    total = (
        detail_score * options["detailWeight"]
        + skin_score * options["skinWeight"]
        + sat_score * options["saturationWeight"]
        + boost_score * options["boostWeight"]
    ) / area if area > 0 else 0.0

    return {
        "detail": float(detail_score),
        "saturation": float(sat_score),
        "skin": float(skin_score),
        "boost": float(boost_score),
        "total": float(total),
    }


def find_best_crop(
    image: Image.Image,
    crop_width: int = 300,
    crop_height: int = 220,
    min_scale: float = 0.4,
    skin_weight: float = 8.0,
    detail_weight: float = 2.0,
) -> dict | None:
    """Find the best crop for the image, matching smartcrop.js options and flow."""
    if image is None:
        logger.debug("find_best_crop called with image=None")
        return None

    try:
        img_rgb = image.convert("RGB")
        w, h = img_rgb.size
    except Exception as e:
        logger.debug("find_best_crop failed to convert image to RGB: %s", e)
        return None

    if w < 3 or h < 3:
        logger.debug("find_best_crop image dimension too small (%sx%s)", w, h)
        return None

    options = dict(DEFAULTS)
    options.update({
        "width": crop_width,
        "height": crop_height,
        "cropWidth": crop_width,
        "cropHeight": crop_height,
        "minScale": min_scale,
        "skinWeight": skin_weight,
        "detailWeight": detail_weight,
    })

    if options["aspect"]:
        options["width"] = options["aspect"]
        options["height"] = 1

    scale = 1.0
    prescale = 1.0

    if options["width"] and options["height"]:
        scale = min(w / options["width"], h / options["height"])
        options["cropWidth"] = int(options["width"] * scale)
        options["cropHeight"] = int(options["height"] * scale)

        options["minScale"] = min(
            options["maxScale"],
            max(1.0 / scale, options["minScale"])
        )

        if options["prescale"] is not False:
            prescale = min(max(256.0 / w, 256.0 / h), 1.0)
            if prescale < 1.0:
                new_w = max(1, int(w * prescale))
                new_h = max(1, int(h * prescale))
                img_rgb = img_rgb.resize((new_w, new_h), Image.Resampling.BILINEAR)
                w, h = new_w, new_h
                options["cropWidth"] = int(options["cropWidth"] * prescale)
                options["cropHeight"] = int(options["cropHeight"] * prescale)
                if options.get("boost"):
                    options["boost"] = [
                        {
                            "x": int(b["x"] * prescale),
                            "y": int(b["y"] * prescale),
                            "width": int(b["width"] * prescale),
                            "height": int(b["height"] * prescale),
                            "weight": b["weight"],
                        }
                        for b in options["boost"]
                    ]
            else:
                prescale = 1.0

    input_rgba = np.dstack([np.array(img_rgb, dtype=np.uint8), np.full((h, w), 255, dtype=np.uint8)])
    output_rgba = np.zeros((h, w, 4), dtype=np.uint8)

    edge_detect(input_rgba, output_rgba)
    skin_detect(options, input_rgba, output_rgba)
    saturation_detect(options, input_rgba, output_rgba)
    apply_boosts(options, output_rgba)

    score_output = down_sample(output_rgba, options["scoreDownSample"])

    crops = generate_crops(options, w, h)
    if not crops:
        logger.debug("find_best_crop generated 0 candidate crops")
        return None

    top_score = -float("inf")
    top_crop = None

    for crop_cand in crops:
        sc = score(options, score_output, crop_cand)
        crop_cand["score"] = sc
        if sc["total"] > top_score:
            top_crop = crop_cand
            top_score = sc["total"]

    if not top_crop:
        logger.debug("find_best_crop found no top crop")
        return None

    res_crop = {
        "x": int(top_crop["x"] / prescale),
        "y": int(top_crop["y"] / prescale),
        "width": int(top_crop["width"] / prescale),
        "height": int(top_crop["height"] / prescale),
    }

    return res_crop
