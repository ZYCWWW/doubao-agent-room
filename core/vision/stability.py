"""
加载稳定与黑屏检测
- 连续帧差异：页面/屏幕加载是否还在变化（稳定判定）
- 黑屏检测：图像整体亮度/方差过低判定黑屏
"""
import os


def _image_array_stats(image_path: str):
    """返回 (均值亮度, 方差) ；失败返回 (None, None)"""
    try:
        from PIL import Image
        import numpy as np
        with Image.open(image_path) as im:
            arr = np.asarray(im.convert("L"), dtype=np.float64)
        return float(arr.mean()), float(arr.var())
    except Exception:
        return None, None


def is_black_screen(image_path: str, mean_threshold: float = 25.0,
                    var_threshold: float = 20.0) -> tuple:
    """黑屏检测：均值与方差都极低 → 黑屏。返回 (是否黑屏, 指标)"""
    mean, var = _image_array_stats(image_path)
    if mean is None:
        return False, {"mean": None, "var": None, "error": "图像读取失败"}
    black = mean < mean_threshold and var < var_threshold
    return black, {"mean": round(mean, 1), "var": round(var, 1)}


def frame_diff(frame_a: str, frame_b: str) -> float:
    """两帧差异度（0~1，1=完全变化）"""
    try:
        from PIL import Image
        import numpy as np
        with Image.open(frame_a) as fa, Image.open(frame_b) as fb:
            a = np.asarray(fa.convert("L"), dtype=np.float64)
            b = np.asarray(fb.convert("L"), dtype=np.float64)
        if a.shape != b.shape:
            # 尺寸不同视为完全变化
            return 1.0
        diff = np.abs(a - b).mean() / 255.0
        return float(diff)
    except Exception:
        return 1.0


def is_stable(frame_a: str, frame_b: str, threshold: float = 0.02) -> tuple:
    """加载稳定检测：连续两帧差异小于阈值 → 稳定。返回 (是否稳定, 差异度)"""
    d = frame_diff(frame_a, frame_b)
    return d < threshold, round(d, 4)
