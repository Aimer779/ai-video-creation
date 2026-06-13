#!/usr/bin/env python3
"""
通用图片蒙太奇视频生成器

将一组图片按顺序生成带有 Ken Burns 效果、交叉淡化、暖调调色和纸张纹理的 MP4 视频。

用法示例：
    python shared/tools/generate_montage.py \
        --input-dir projects/2026-06-kimi-k3-promo/assets/processed \
        --output projects/2026-06-kimi-k3-promo/drafts/part1_ancient_montage.mp4 \
        --glob "*.png" \
        --fps 24 \
        --clip-frames 20 \
        --fade-frames 5 \
        --output-size 720
"""

import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter


def parse_args():
    parser = argparse.ArgumentParser(description="Generate a crossfade montage video from images.")
    parser.add_argument("--input-dir", required=True, help="Directory containing source images.")
    parser.add_argument("--output", required=True, help="Output MP4 path.")
    parser.add_argument("--glob", default="*.png", help="Glob pattern to match images (default: *.png).")
    parser.add_argument("--sort", default="name", choices=["name", "mtime"], help="Sort images by name or modification time.")
    parser.add_argument("--fps", type=int, default=24, help="Video frame rate (default: 24).")
    parser.add_argument("--clip-frames", type=int, default=20, help="Frames each image stays on screen (default: 20).")
    parser.add_argument("--clip-durations", default=None, help="Comma-separated per-image durations in seconds. Overrides --clip-frames. Example: 1.0,0.8,0.6,0.3")
    parser.add_argument("--fade-frames", type=int, default=5, help="Crossfade overlap frames (default: 5).")
    parser.add_argument("--output-size", type=int, default=720, help="Output video width/height in pixels (default: 720).")
    parser.add_argument("--render-size", type=int, default=900, help="Internal render size before output crop (default: 900).")
    parser.add_argument("--start-scale", type=float, default=1.0, help="Ken Burns start scale (default: 1.0).")
    parser.add_argument("--end-scale", type=float, default=1.08, help="Ken Burns end scale (default: 1.08).")
    parser.add_argument("--rotation", type=float, default=1.5, help="Ken Burns clockwise rotation in degrees (default: 1.5).")
    parser.add_argument("--warmth", type=float, default=0.12, help="Warm overlay opacity 0-1 (default: 0.12).")
    parser.add_argument("--warmth-color", default="255,230,200", help="Warm overlay RGB (default: 255,230,200).")
    parser.add_argument("--contrast", type=float, default=0.9, help="Contrast multiplier, <1 reduces (default: 0.9).")
    parser.add_argument("--saturation", type=float, default=0.92, help="Saturation multiplier, <1 reduces (default: 0.92).")
    parser.add_argument("--paper-texture", type=float, default=0.10, help="Paper texture overlay opacity 0-1, 0 disables (default: 0.10).")
    parser.add_argument("--paper-color", default="232,220,200", help="Paper base RGB (default: 232,220,200).")
    parser.add_argument("--grain", type=int, default=8, help="Film grain intensity, 0 disables (default: 8).")
    parser.add_argument("--crf", type=int, default=18, help="FFmpeg H.264 CRF quality (default: 18).")
    parser.add_argument("--keep-frames", action="store_true", help="Keep temporary PNG frame sequence.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducible grain/texture (default: 42).")
    return parser.parse_args()


def load_images(input_dir, pattern, sort_by):
    paths = glob.glob(os.path.join(input_dir, pattern))
    if not paths:
        raise ValueError(f"No images found in {input_dir} matching {pattern}")
    if sort_by == "name":
        paths.sort()
    else:
        paths.sort(key=os.path.getmtime)
    return [Image.open(p).convert("RGB") for p in paths]


def normalize_images(images, render_size, fill_color=(232, 220, 200)):
    normalized = []
    for img in images:
        if img.size != (render_size, render_size):
            img = img.resize((render_size, render_size), Image.LANCZOS)
        normalized.append(img)
    return normalized


def generate_paper_texture(size, base_color, seed=42):
    np.random.seed(seed)
    noise = np.random.randint(0, 255, (size, size, 3), dtype=np.uint8)
    texture = Image.fromarray(noise).filter(ImageFilter.GaussianBlur(radius=2))
    base = Image.new("RGB", (size, size), base_color)
    return Image.blend(texture, base, 0.7)


def generate_clip(img, n_frames, output_size, start_scale=1.0, end_scale=1.08, rotation=1.5, fill_color=(232, 220, 200)):
    frames = []
    w, h = img.size
    for i in range(n_frames):
        t = i / max(n_frames - 1, 1)
        scale = start_scale + (end_scale - start_scale) * t
        angle = rotation * t
        scaled = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        rotated = scaled.rotate(angle, resample=Image.BILINEAR, expand=False, fillcolor=fill_color)
        rw, rh = rotated.size
        left = (rw - output_size) // 2
        top = (rh - output_size) // 2
        frames.append(rotated.crop((left, top, left + output_size, top + output_size)))
    return frames


def apply_grading(frame, warmth, warmth_color, contrast, saturation, paper_texture, grain, seed):
    if warmth > 0:
        warm_overlay = Image.new("RGB", frame.size, warmth_color)
        frame = Image.blend(frame, warm_overlay, warmth)
    if contrast != 1.0:
        frame = ImageEnhance.Contrast(frame).enhance(contrast)
    if saturation != 1.0:
        frame = ImageEnhance.Color(frame).enhance(saturation)
    if paper_texture is not None:
        frame = Image.blend(frame, paper_texture, 0.10)
    if grain > 0:
        np.random.seed(seed)
        noise = np.random.randint(-grain, grain, (frame.size[1], frame.size[0], 3), dtype=np.int16)
        arr = np.array(frame, dtype=np.int16) + noise
        frame = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    return frame


def compose_clips(clips, fade_frames):
    # Compute start frame for each clip, accounting for overlap with the previous clip.
    starts = []
    s = 0
    for clip in clips:
        starts.append(s)
        s += len(clip) - fade_frames
    total_frames = sum(len(c) for c in clips) - (len(clips) - 1) * fade_frames

    composed = []
    for i in range(total_frames):
        contributions = []
        for k, clip in enumerate(clips):
            clip_start = starts[k]
            clip_end = clip_start + len(clip)
            if clip_start <= i < clip_end:
                local_frame = i - clip_start
                if k < len(clips) - 1 and local_frame >= len(clip) - fade_frames:
                    fade_progress = (local_frame - (len(clip) - fade_frames)) / fade_frames
                    alpha = 1.0 - fade_progress
                else:
                    alpha = 1.0
                contributions.append((clip[local_frame], alpha))

        if len(contributions) == 1:
            frame = contributions[0][0]
        else:
            base = np.array(contributions[0][0], dtype=np.float32) * contributions[0][1]
            total_alpha = contributions[0][1]
            for frame_img, alpha in contributions[1:]:
                base += np.array(frame_img, dtype=np.float32) * alpha
                total_alpha += alpha
            frame = Image.fromarray(np.clip(base / total_alpha, 0, 255).astype(np.uint8))
        composed.append(frame)
    return composed


def encode_video(frame_paths, output_path, fps, crf=18):
    frame_dir = os.path.dirname(frame_paths[0])
    pattern = os.path.join(frame_dir, "frame_%04d.png")
    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", pattern,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", str(crf),
        "-preset", "medium",
        output_path,
    ]
    subprocess.run(cmd, check=True)


def main():
    args = parse_args()

    warmth_color = tuple(int(c) for c in args.warmth_color.split(","))
    paper_color = tuple(int(c) for c in args.paper_color.split(","))
    fill_color = paper_color

    print(f"Loading images from {args.input_dir} ...")
    images = load_images(args.input_dir, args.glob, args.sort)
    print(f"Loaded {len(images)} images")

    images = normalize_images(images, args.render_size, fill_color)

    # Determine per-image clip durations.
    if args.clip_durations:
        durations = [float(x.strip()) for x in args.clip_durations.split(",")]
        if len(durations) == 1:
            durations = durations * len(images)
        elif len(durations) < len(images):
            durations = durations + [durations[-1]] * (len(images) - len(durations))
        clip_frames_list = [max(1, int(round(d * args.fps))) for d in durations]
    else:
        clip_frames_list = [args.clip_frames] * len(images)

    paper_texture = None
    if args.paper_texture > 0:
        paper_texture = generate_paper_texture(args.output_size, paper_color, args.seed)

    print("Generating individual clips with Ken Burns ...")
    clips = [generate_clip(img, n_frames, args.output_size,
                           args.start_scale, args.end_scale, args.rotation, fill_color)
             for img, n_frames in zip(images, clip_frames_list)]

    print("Composing crossfade montage ...")
    composed = compose_clips(clips, args.fade_frames)

    frame_dir = tempfile.mkdtemp(prefix="montage_frames_")
    try:
        print(f"Writing {len(composed)} frames ...")
        for i, frame in enumerate(composed):
            frame = apply_grading(frame, args.warmth, warmth_color, args.contrast,
                                  args.saturation, paper_texture, args.grain, args.seed + i)
            frame.save(os.path.join(frame_dir, f"frame_{i:04d}.png"))

        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        print(f"Encoding video to {args.output} ...")
        encode_video([os.path.join(frame_dir, f"frame_{i:04d}.png") for i in range(len(composed))],
                     args.output, args.fps, args.crf)
        print(f"Done. Duration: {len(composed)/args.fps:.2f}s, Frames: {len(composed)}")

        if args.keep_frames:
            print(f"Frame sequence kept at: {frame_dir}")
        else:
            shutil.rmtree(frame_dir)
    except Exception as e:
        if not args.keep_frames and os.path.exists(frame_dir):
            shutil.rmtree(frame_dir)
        raise e


if __name__ == "__main__":
    main()
