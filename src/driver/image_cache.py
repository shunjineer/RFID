import os
import base64
import io
from PIL import Image
import colorsys
from multiprocessing import Process, Manager

IMG_DIR = os.path.join("src", "driver", "img")

# 40段階の色をHSV(色相240→0)で生成
def hue_color_for_level(level_idx: int) -> tuple[int, int, int]:
    # level_idx: 0..29
    ratio = level_idx / 29.0
    hue_deg = 240.0 * (1.0 - ratio)  # 240→0
    h = hue_deg / 360.0
    s = 1.0
    v = 1.0
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return int(r * 255), int(g * 255), int(b * 255)

def recolor_image_rgba(image_path: str, fill_rgb: tuple[int, int, int]) -> bytes:
    img = Image.open(image_path).convert("RGBA")
    datas = img.getdata()
    new_data = []
    fr, fg, fb = fill_rgb
    for item in datas:
        r, g, b, a = item
        if a > 0 and r == 255 and g == 255 and b == 255:
            new_data.append((fr, fg, fb, a))
        else:
            new_data.append(item)
    img.putdata(new_data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def build_cache(cache_dict):
    # 3種類×30段階で生成
    names = ["seat", "backrest", "headrest"]
    paths = {
        "seat": os.path.join(IMG_DIR, "seat.png"),
        "backrest": os.path.join(IMG_DIR, "backrest.png"),
        "headrest": os.path.join(IMG_DIR, "headrest.png"),
    }
    # ローカルで一括構築
    result = {name: {} for name in names}
    for level in range(30):
        fill = hue_color_for_level(level)
        for name in names:
            png_bytes = recolor_image_rgba(paths[name], fill)
            b64 = base64.b64encode(png_bytes).decode("ascii")
            result[name][level] = b64
    # まとめて代入（ネスト辞書は完成形を1回でセット）
    for name in names:
        cache_dict[name] = result[name]
    cache_dict["__ready__"] = True

def start_cache_process():
    manager = Manager()
    cache_dict = manager.dict()
    p = Process(target=build_cache, args=(cache_dict,))
    p.daemon = True
    p.start()
    return p, cache_dict

def temp_to_level(temp_c: float | None) -> int | None:
    if temp_c is None:
        return None
    # 10〜40に丸め、30段階へ離散化
    if temp_c < 10.0:
        temp_c = 10.0
    if temp_c > 40.0:
        temp_c = 40.0
    # 10..40 を 0..29 に
    level = int((temp_c - 10.0) // 1.0)
    return max(0, min(29, level))
