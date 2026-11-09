"""
长图切片（供 VL 分块识别）
- 按高度切片，70% 重叠去重
- 输出切片坐标区间列表，供逐片识别后合并
"""
from PIL import Image


def slice_image(image_path: str, slice_height: int = 1024,
                overlap_ratio: float = 0.7, min_slice: int = 200) -> list:
    """
    把长图切成带重叠的纵向切片
    :return: [{index, top, bottom, path, width, height}]
    """
    with Image.open(image_path) as img:
        w, h = img.size
    if h <= slice_height:
        return [{"index": 0, "top": 0, "bottom": h, "path": image_path,
                 "width": w, "height": h}]
    overlap = int(slice_height * max(0.0, min(1.0, overlap_ratio)))
    step = max(min_slice, slice_height - overlap)
    slices = []
    top = 0
    idx = 0
    while top < h:
        bottom = min(h, top + slice_height)
        slices.append({"index": idx, "top": top, "bottom": bottom,
                       "width": w, "height": bottom - top})
        idx += 1
        top += step
        if bottom >= h:
            break
    return slices


def crop_to_file(image_path: str, top: int, bottom: int, out_path: str) -> str:
    """按纵向区间裁剪并保存，返回输出路径"""
    with Image.open(image_path) as img:
        box = (0, max(0, top), img.width, min(img.height, bottom))
        img.crop(box).save(out_path)
    return out_path


def merge_slice_texts(slices_text: list, overlap_ratio: float = 0.7) -> str:
    """
    合并各切片识别结果（去重叠重复行）
    :param slices_text: 按顺序的切片文本列表
    """
    out = []
    for i, text in enumerate(slices_text):
        lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
        if not lines:
            continue
        if i == 0:
            out.extend(lines)
            continue
        # 与前一片的尾部行比对，跳过重复（重叠 70% 的近似去重）
        prev_tail = out[-max(1, int(len(lines) * (1 - overlap_ratio))):]
        skip = 0
        for k in range(1, min(len(lines), len(prev_tail)) + 1):
            if lines[:k] == prev_tail[-k:]:
                skip = k
        out.extend(lines[skip:])
    return "\n".join(out)
