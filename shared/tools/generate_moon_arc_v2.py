#!/usr/bin/env python3
"""
基于 HTML/CSS 双圆遮挡法生成黑色月球 + 旋转月牙光弧动画。

参考：projects/2026-06-kimi-k3-promo/assets/moon_glow.html
原理：
  1. 黑色月球本体 + 微弱环境光
  2. 一个白色发光圆（径向渐变）作为月牙光源
  3. 一个黑色遮挡圆偏移后叠加在发光圆上，露出月牙形白光
  4. 遮挡圆边缘带白色内发光，使月牙过渡更柔和
  5. 整体旋转指定角度范围，首尾角度对齐以实现循环
"""

import argparse
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter


def make_radial_gradient(size, center_ratio, stops, max_radius=None):
    """
    生成径向渐变的 RGBA numpy 数组。

    size: (w, h)
    center_ratio: (cx, cy)，光源中心在图像中的比例位置，如 (0.35, 0.35)
    stops: [(radius_ratio, r, g, b, a), ...]，radius_ratio 0~1 相对 max_radius
    max_radius: 渐变半径（像素）。默认使用图像对角线一半。
    """
    w, h = size
    cx, cy = w * center_ratio[0], h * center_ratio[1]
    if max_radius is None:
        max_r = np.sqrt(max(cx, w - cx) ** 2 + max(cy, h - cy) ** 2)
    else:
        max_r = max_radius

    y, x = np.ogrid[:h, :w]
    dist = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
    dist_ratio = dist / max_r

    ratios = np.array([s[0] for s in stops])
    colors = np.array([s[1:] for s in stops])

    r_arr = np.interp(dist_ratio, ratios, colors[:, 0])
    g_arr = np.interp(dist_ratio, ratios, colors[:, 1])
    b_arr = np.interp(dist_ratio, ratios, colors[:, 2])
    a_arr = np.interp(dist_ratio, ratios, colors[:, 3])

    img = np.stack([r_arr, g_arr, b_arr, a_arr], axis=-1).astype(np.uint8)
    return img


def make_occlusion_layer(size, center, radius, glow_intensity):
    """
    创建带白色内发光的黑色遮挡圆图层（RGBA）。
    """
    # 黑色实心圆
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    bbox = [
        center[0] - radius,
        center[1] - radius,
        center[0] + radius,
        center[1] + radius,
    ]
    draw.ellipse(bbox, fill=(0, 0, 0, 255))

    # 白色内发光：在遮挡圆内部边缘形成柔和过渡
    edge_width = max(2, int(radius * 0.08))
    blur_radius = max(1, int(edge_width * 0.3))

    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)

    outer_r = radius
    inner_r = max(0, radius - edge_width)
    alpha = int(255 * glow_intensity)

    # 白色实心圆环区域
    gdraw.ellipse(
        [center[0] - outer_r, center[1] - outer_r, center[0] + outer_r, center[1] + outer_r],
        fill=(255, 255, 255, alpha),
    )
    # 抠出内部，只保留边缘
    if inner_r > 0:
        gdraw.ellipse(
            [center[0] - inner_r, center[1] - inner_r, center[0] + inner_r, center[1] + inner_r],
            fill=(0, 0, 0, 0),
        )

    glow = glow.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    # 黑色圆 + 内发光合成
    result = Image.alpha_composite(img, glow)
    return result


def make_moon_body(size, center, radius):
    """
    创建黑色月球本体图层，带微弱环境光/外发光。
    """
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    # 极微弱外发光（让月球和纯黑背景有几乎不可见的分离）
    for scale, alpha in [(1.20, 1), (1.08, 2)]:
        r = radius * scale
        glow = Image.new("RGBA", size, (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(glow)
        gdraw.ellipse(
            [center[0] - r, center[1] - r, center[0] + r, center[1] + r],
            fill=(255, 255, 255, alpha),
        )
        glow = glow.filter(ImageFilter.GaussianBlur(radius=radius * (scale - 1) * 1.0))
        layer = Image.alpha_composite(layer, glow)

    # 纯黑月球本体 + 极微弱内发光
    body = Image.new("RGBA", size, (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(body)
    bdraw.ellipse(
        [center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius],
        fill=(0, 0, 0, 255),
    )
    # 内发光：让月牙和本体过渡更自然
    inner_glow = Image.new("RGBA", size, (0, 0, 0, 0))
    igdraw = ImageDraw.Draw(inner_glow)
    ig_r = radius * 1.02
    igdraw.ellipse(
        [center[0] - ig_r, center[1] - ig_r, center[0] + ig_r, center[1] + ig_r],
        fill=(255, 255, 255, 4),
    )
    igdraw.ellipse(
        [center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius],
        fill=(0, 0, 0, 0),
    )
    inner_glow = inner_glow.filter(ImageFilter.GaussianBlur(radius=radius * 0.03))
    body = Image.alpha_composite(body, inner_glow)

    layer = Image.alpha_composite(layer, body)
    return layer


def make_crescent_wrapper(wrapper_size, moon_size, glow_offset_pct, glow_intensity):
    """
    生成月牙光弧图层（未旋转），中心在 wrapper 中心。
    """
    # 发光圆：径向渐变（中等淡出，月牙主体更明亮）
    glow_stops = [
        (0.00, 255, 255, 255, 255),
        (0.15, 255, 255, 255, 253),
        (0.30, 245, 245, 245, 240),
        (0.50, 210, 210, 210, 140),
        (0.70, 0, 0, 0, 0),
        (1.00, 0, 0, 0, 0),
    ]
    glow_arr = make_radial_gradient(
        (wrapper_size, wrapper_size),
        center_ratio=(0.35, 0.35),
        stops=glow_stops,
        max_radius=wrapper_size / 2,
    )
    glow_layer = Image.fromarray(glow_arr)
    # 轻微模糊让月牙边缘更柔和（参考 HTML 的 0.5px blur）
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=0.35))

    # 遮挡圆
    cx = cy = wrapper_size / 2
    glow_offset_px = moon_size * glow_offset_pct / 100.0
    occlusion_center = (cx + glow_offset_px, cy + glow_offset_px)
    occlusion_radius = wrapper_size / 2 * (1.17 / 1.18)
    occlusion_layer = make_occlusion_layer(
        (wrapper_size, wrapper_size),
        center=occlusion_center,
        radius=occlusion_radius,
        glow_intensity=glow_intensity,
    )

    # 合成：发光圆在下，遮挡圆在上
    crescent = Image.alpha_composite(glow_layer, occlusion_layer)

    # 给 wrapper 加圆形羽化 mask，彻底消除方形边界
    mask = Image.new("L", (wrapper_size, wrapper_size), 0)
    mdraw = ImageDraw.Draw(mask)
    # 内部全不透明，边缘 2px 羽化到透明
    mdraw.ellipse([2, 2, wrapper_size - 2, wrapper_size - 2], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=2))
    # 应用 mask 到 alpha 通道
    r, g, b, a = crescent.split()
    a = ImageChops.multiply(a, mask)
    crescent = Image.merge("RGBA", (r, g, b, a))

    return crescent


def render_frame(
    output_size,
    moon_size,
    glow_offset_pct,
    glow_intensity,
    angle_deg,
    add_outer_glow=False,
):
    """
    渲染单帧图像。
    """
    # 黑色背景
    bg = Image.new("RGB", (output_size, output_size), (0, 0, 0))

    center = output_size / 2
    moon_radius = moon_size / 2
    wrapper_size = int(moon_size * 1.18)

    # 月球本体
    moon_layer = make_moon_body(
        (output_size, output_size),
        center=(center, center),
        radius=moon_radius,
    )
    bg_rgba = bg.convert("RGBA")
    bg_rgba = Image.alpha_composite(bg_rgba, moon_layer)

    # 月牙光弧
    crescent = make_crescent_wrapper(
        wrapper_size=wrapper_size,
        moon_size=moon_size,
        glow_offset_pct=glow_offset_pct,
        glow_intensity=glow_intensity,
    )

    # 旋转
    rotated = crescent.rotate(angle_deg, resample=Image.BICUBIC, expand=False)

    # 贴到背景中心
    paste_x = (output_size - wrapper_size) // 2
    paste_y = (output_size - wrapper_size) // 2
    bg_rgba.paste(rotated, (paste_x, paste_y), rotated)

    # 可选：外围脉冲光晕（静态版本）
    if add_outer_glow:
        outer = Image.new("RGBA", (output_size, output_size), (0, 0, 0, 0))
        odraw = ImageDraw.Draw(outer)
        r = moon_radius * 1.45
        odraw.ellipse(
            [center - r, center - r, center + r, center + r],
            fill=(255, 255, 255, 6),
        )
        odraw.ellipse(
            [center - moon_radius, center - moon_radius, center + moon_radius, center + moon_radius],
            fill=(0, 0, 0, 0),
        )
        outer = outer.filter(ImageFilter.GaussianBlur(radius=moon_radius * 0.15))
        bg_rgba = Image.alpha_composite(bg_rgba, outer)

    return bg_rgba.convert("RGB")


def main():
    parser = argparse.ArgumentParser(description="Generate rotating crescent moon arc animation.")
    parser.add_argument("--output", required=True, help="Output MP4 path")
    parser.add_argument("--output-size", type=int, default=720, help="Output width/height in px")
    parser.add_argument("--moon-size", type=int, default=360, help="Moon diameter in px")
    parser.add_argument("--duration", type=float, default=1.5, help="Duration in seconds")
    parser.add_argument("--fps", type=int, default=24, help="Frame rate")
    parser.add_argument("--start-angle", type=float, default=0, help="Start rotation angle in degrees")
    parser.add_argument("--end-angle", type=float, default=180, help="End rotation angle in degrees")
    parser.add_argument("--period", type=float, default=3.0, help="Full rotation period in seconds")
    parser.add_argument("--glow-offset", type=float, default=1.0, help="Crescent width in percent of moon size")
    parser.add_argument("--glow-intensity", type=float, default=11.0, help="Glow intensity (0-100, maps to opacity)")
    parser.add_argument("--outer-glow", action="store_true", help="Add static outer glow ring")
    parser.add_argument("--crf", type=int, default=18, help="ffmpeg CRF quality")
    parser.add_argument("--no-cleanup", action="store_true", help="Keep temporary frame PNGs")
    args = parser.parse_args()

    total_frames = int(round(args.duration * args.fps))
    glow_intensity = np.clip(args.glow_intensity / 100.0, 0.0, 1.0)

    # 按周期计算角速度（度/秒），确保 3s 转一圈
    angular_speed = 360.0 / args.period

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_dir = Path(tempfile.mkdtemp(prefix="moon_arc_v2_"))
    frame_paths = []

    for i in range(total_frames):
        t = i / args.fps
        # 线性插值角度，同时保证角速度与周期一致
        angle = args.start_angle + (angular_speed * t) % 360.0
        # 如果 end_angle > start_angle，也限制在范围内
        if args.end_angle > args.start_angle:
            angle = args.start_angle + (args.end_angle - args.start_angle) * (i / total_frames)

        img = render_frame(
            output_size=args.output_size,
            moon_size=args.moon_size,
            glow_offset_pct=args.glow_offset,
            glow_intensity=glow_intensity,
            angle_deg=angle,
            add_outer_glow=args.outer_glow,
        )
        frame_path = tmp_dir / f"frame_{i:05d}.png"
        img.save(frame_path, "PNG")
        frame_paths.append(frame_path)

    # 用 ffmpeg 编码
    cmd = [
        "ffmpeg",
        "-y",
        "-framerate", str(args.fps),
        "-i", str(tmp_dir / "frame_%05d.png"),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", str(args.crf),
        "-movflags", "+faststart",
        str(output_path),
    ]
    subprocess.run(cmd, check=True)

    if not args.no_cleanup:
        for p in frame_paths:
            p.unlink()
        tmp_dir.rmdir()

    print(f"Saved: {output_path}")


if __name__ == "__main__":
    main()
