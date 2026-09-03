#!/usr/bin/env python3
"""
🎬 Generic Programmatic Video Generator CLI
Generates 1080p presentation slides using Pillow with Intelligent Auto-Text Wrapping & Auto-Fit,
dynamic Ken Burns camera motions, 11+ cinematic Xfade transitions, and renders MP4 videos with MP3 audio.
"""

import os
import sys
import json
import math
import struct
import wave
import argparse
import subprocess
import concurrent.futures
from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "/tmp/noto.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
]

def get_font_path():
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    target = "/tmp/noto.ttf"
    if not os.path.exists(target):
        url = "https://github.com/googlefonts/noto-cjk/raw/main/Sans/Variable/TTF/NotoSansCJKtc-VF.ttf"
        try:
            subprocess.run(["curl", "-sL", url, "-o", target], check=True)
            return target
        except Exception:
            pass
    return None

def get_font(size):
    font_path = get_font_path()
    if font_path and os.path.exists(font_path):
        try:
            return ImageFont.truetype(font_path, size)
        except Exception:
            pass
    return ImageFont.load_default()

def draw_badge(draw, text, x, y, bg="#1e293b", fg="#38bdf8", border="#0284c7", size=24):
    font = get_font(size)
    bbox = draw.textbbox((x, y), text, font=font, anchor="mm")
    px, py = 24, 10
    draw.rounded_rectangle([bbox[0]-px, bbox[1]-py, bbox[2]+px, bbox[3]+py], radius=20, fill=bg, outline=border, width=2)
    draw.text((x, y), text, font=font, fill=fg, anchor="mm")

# ==============================================================================
# 📏 Intelligent Text Wrapping & Auto-Fit Engine (Guarantees Zero Overflow)
# ==============================================================================

def wrap_text(text, font, max_width):
    """Wrap text character-by-character (CJK) and word-by-word (English)."""
    lines = []
    for paragraph in text.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current_line = ""
        for char in paragraph:
            test_line = current_line + char
            bbox = font.getbbox(test_line)
            w = bbox[2] - bbox[0]
            if w <= max_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = char
        if current_line:
            lines.append(current_line)
    return lines

def draw_fitted_text(draw, text, box_rect, initial_size=24, min_size=16, fill="#cbd5e1", align="left", line_spacing=1.35, center_y=False):
    """
    Renders multiline text strictly inside box_rect [x1, y1, x2, y2].
    Automatically breaks lines and shrinks font size to prevent ANY overflow.
    """
    x1, y1, x2, y2 = box_rect
    max_w = max(50, (x2 - x1) - 10)
    max_h = max(30, (y2 - y1) - 10)

    for size in range(initial_size, min_size - 1, -2):
        font = get_font(size)
        lines = wrap_text(text, font, max_w)
        line_h = int(size * line_spacing)
        total_h = len(lines) * line_h
        if total_h <= max_h or size == min_size:
            # Layout fits
            start_y = y1 + (max_h - total_h) // 2 if center_y else y1
            for idx, line in enumerate(lines):
                curr_y = start_y + idx * line_h
                if curr_y + line_h > y2 + 10:
                    break
                if align == "center":
                    bbox = font.getbbox(line)
                    line_w = bbox[2] - bbox[0]
                    curr_x = x1 + (max_w - line_w) // 2
                else:
                    curr_x = x1
                draw.text((curr_x, curr_y), line, font=font, fill=fill)
            return

# ==============================================================================
# 🎨 12 Layout Archetypes (Universal Presentation Templates with Auto-Wrap)
# ==============================================================================

def render_layout(slide_data, output_path):
    layout = slide_data.get("layout", "hero_poster")
    bg_color = slide_data.get("bg_color", "#080e1a")
    img = Image.new("RGB", (1920, 1080), bg_color)
    draw = ImageDraw.Draw(img)

    badge = slide_data.get("badge", "")
    title = slide_data.get("title", "Presentation Title")
    subtitle = slide_data.get("subtitle", "")
    footer = slide_data.get("footer", "")
    accent = slide_data.get("accent_color", "#38bdf8")

    if layout == "hero_poster":
        # 1. Hero Centered Poster
        for r in range(450, 0, -15):
            draw.ellipse([960-r, 480-r, 960+r, 480+r], fill=(10, 30, 60))
        if badge:
            draw_badge(draw, badge, 960, 150, border=accent, fg=accent)
        draw.text((960, 300), title, font=get_font(80), fill=accent, anchor="mm")
        if subtitle:
            draw.text((960, 400), subtitle, font=get_font(36), fill="#f8fafc", anchor="mm")
        
        cards = slide_data.get("cards", [])
        num_cards = min(len(cards), 3)
        if num_cards > 0:
            spacing = 1600 // num_cards
            start_x = 960 - (num_cards - 1) * spacing // 2
            for i, c in enumerate(cards[:num_cards]):
                cx = start_x + i * spacing
                c_color = c.get("color", accent)
                draw.rounded_rectangle([cx-240, 540, cx+240, 780], radius=24, fill="#0c172a", outline=c_color, width=2)
                draw.text((cx, 610), c.get("title", ""), font=get_font(32), fill=c_color, anchor="mm")
                draw_fitted_text(draw, c.get("desc", ""), [cx-210, 660, cx+210, 760], initial_size=24, min_size=18, fill="#e2e8f0", align="center")

    elif layout == "split_2col":
        # 2. Left-Right Split (40% Highlight Callout + 60% Strips)
        if badge:
            draw_badge(draw, badge, 400, 110, border=accent, fg=accent)
        draw.text((120, 200), title, font=get_font(56), fill="#f8fafc")
        if subtitle:
            draw.text((120, 265), subtitle, font=get_font(26), fill="#94a3b8")
        
        hl = slide_data.get("highlight", {})
        draw.rounded_rectangle([120, 340, 760, 920], radius=24, fill="#0f172a", outline=accent, width=3)
        draw.text((440, 420), hl.get("tag", "KEY METRIC"), font=get_font(24), fill=accent, anchor="mm")
        draw.text((440, 520), hl.get("metric", "99%"), font=get_font(84), fill=accent, anchor="mm")
        draw.text((440, 620), hl.get("label", ""), font=get_font(28), fill="#f8fafc", anchor="mm")
        draw_fitted_text(draw, hl.get("desc", ""), [160, 670, 720, 880], initial_size=24, min_size=18, fill="#94a3b8", align="center")

        strips = slide_data.get("cards", [])
        for i, c in enumerate(strips[:3]):
            y = 340 + i * 200
            c_color = c.get("color", "#10b981")
            draw.rounded_rectangle([820, y, 1800, y+170], radius=20, fill="#0f172a", outline=c_color, width=2)
            draw.text((860, y+35), c.get("title", ""), font=get_font(30), fill=c_color)
            draw_fitted_text(draw, c.get("desc", ""), [860, y+75, 1760, y+155], initial_size=24, min_size=18, fill="#cbd5e1")

    elif layout == "dashboard_racks":
        # 3. Dashboard KPI Row + 3 Machine Racks
        if badge:
            draw_badge(draw, badge, 960, 100, border=accent, fg=accent)
        draw.text((960, 180), title, font=get_font(56), fill="#f8fafc", anchor="mm")

        kpis = slide_data.get("kpis", [])
        if len(kpis) >= 2:
            draw.rounded_rectangle([150, 250, 930, 410], radius=20, fill="#0f172a", outline=accent, width=2)
            draw.text((540, 300), kpis[0].get("label", ""), font=get_font(24), fill="#94a3b8", anchor="mm")
            draw_fitted_text(draw, kpis[0].get("metric", ""), [180, 335, 900, 395], initial_size=34, min_size=22, fill=accent, align="center")

            draw.rounded_rectangle([990, 250, 1770, 410], radius=20, fill="#0f172a", outline="#fbbf24", width=2)
            draw.text((1380, 300), kpis[1].get("label", ""), font=get_font(24), fill="#94a3b8", anchor="mm")
            draw_fitted_text(draw, kpis[1].get("metric", ""), [1020, 335, 1740, 395], initial_size=34, min_size=22, fill="#fbbf24", align="center")

        racks = slide_data.get("cards", [])
        for i, r in enumerate(racks[:3]):
            x = 150 + i * 560
            r_color = r.get("color", accent)
            draw.rounded_rectangle([x, 450, x+500, 920], radius=22, fill="#090d16", outline=r_color, width=2)
            draw.text((x+250, 505), r.get("title", ""), font=get_font(28), fill=r_color, anchor="mm")
            draw_fitted_text(draw, r.get("desc", ""), [x+35, 555, x+465, 895], initial_size=23, min_size=17, fill="#e2e8f0")

    elif layout == "pyramid_peak":
        # 4. Pyramid Peak (Massive top card + 2 symmetrical base cards)
        if badge:
            draw_badge(draw, badge, 960, 110, border=accent, fg=accent)
        draw.text((960, 200), title, font=get_font(58), fill="#f8fafc", anchor="mm")

        peak = slide_data.get("highlight", {})
        draw.rounded_rectangle([520, 280, 1400, 500], radius=24, fill="#0f172a", outline=accent, width=3)
        draw.text((960, 340), peak.get("metric", "TOP TIER"), font=get_font(44), fill=accent, anchor="mm")
        draw_fitted_text(draw, peak.get("label", ""), [550, 390, 1370, 480], initial_size=26, min_size=18, fill="#e2e8f0", align="center")

        cards = slide_data.get("cards", [])
        for i, c in enumerate(cards[:2]):
            x = 150 + i * 830
            c_color = c.get("color", "#38bdf8")
            draw.rounded_rectangle([x, 540, x+790, 890], radius=24, fill="#0f172a", outline=c_color, width=2)
            draw.text((x+45, 600), c.get("title", ""), font=get_font(32), fill=c_color)
            draw_fitted_text(draw, c.get("desc", ""), [x+45, 650, x+745, 860], initial_size=24, min_size=18, fill="#cbd5e1")

    elif layout == "vertical_columns":
        # 5. 3-4 Tall Vertical Column Cards
        if badge:
            draw_badge(draw, badge, 960, 100, border=accent, fg=accent)
        draw.text((960, 180), title, font=get_font(56), fill="#f8fafc", anchor="mm")
        if subtitle:
            draw.text((960, 240), subtitle, font=get_font(26), fill="#94a3b8", anchor="mm")

        pillars = slide_data.get("cards", [])
        num_p = min(len(pillars), 4)
        if num_p > 0:
            width = (1680 - (num_p - 1) * 35) // num_p
            for i, p in enumerate(pillars[:num_p]):
                x = 120 + i * (width + 35)
                p_color = p.get("color", accent)
                draw.rounded_rectangle([x, 310, x+width, 920], radius=22, fill="#0c1722", outline=p_color, width=2)
                draw.text((x+width//2, 375), f"0{i+1}", font=get_font(40), fill=p_color, anchor="mm")
                draw.text((x+width//2, 445), p.get("title", ""), font=get_font(30), fill="#f8fafc", anchor="mm")
                draw_fitted_text(draw, p.get("desc", ""), [x+25, 520, x+width-25, 890], initial_size=22, min_size=16, fill="#cbd5e1", align="center")

    elif layout == "timeline_track":
        # 6. Horizontal Roadmap / Railway Timeline
        if badge:
            draw_badge(draw, badge, 960, 100, border=accent, fg=accent)
        draw.text((960, 180), title, font=get_font(56), fill="#f8fafc", anchor="mm")
        
        draw.line([(150, 460), (1770, 460)], fill=accent, width=4)
        stops = slide_data.get("cards", [])
        num_s = min(len(stops), 4)
        spacing = (1620 - (num_s - 1) * 30) // num_s
        for i, s in enumerate(stops[:num_s]):
            x = 150 + i * (spacing + 30)
            s_color = s.get("color", accent)
            draw.ellipse([x+spacing//2-18, 460-18, x+spacing//2+18, 460+18], fill=s_color, outline="#ffffff", width=3)
            draw.rounded_rectangle([x, 520, x+spacing, 920], radius=20, fill="#1c160c", outline=s_color, width=2)
            draw.text((x+spacing//2, 580), s.get("title", ""), font=get_font(28), fill=s_color, anchor="mm")
            draw_fitted_text(draw, s.get("desc", ""), [x+20, 640, x+spacing-20, 890], initial_size=22, min_size=16, fill="#e2e8f0", align="center")

    elif layout == "asymmetric_showcase":
        # 7. Asymmetric Layout (55% Main Hero Card + 45% Stacked Sub-cards)
        if badge:
            draw_badge(draw, badge, 400, 110, border=accent, fg=accent)
        draw.text((120, 200), title, font=get_font(56), fill="#f8fafc")
        if subtitle:
            draw.text((120, 265), subtitle, font=get_font(26), fill="#94a3b8")

        main_card = slide_data.get("main_card", {})
        draw.rounded_rectangle([120, 340, 1040, 920], radius=24, fill="#0c1e36", outline=accent, width=3)
        draw.text((580, 410), main_card.get("title", ""), font=get_font(34), fill=accent, anchor="mm")
        draw.text((580, 490), main_card.get("subtitle", ""), font=get_font(24), fill="#f8fafc", anchor="mm")
        draw_fitted_text(draw, main_card.get("desc", ""), [160, 560, 1000, 880], initial_size=24, min_size=18, fill="#cbd5e1", align="center")

        sub_cards = slide_data.get("cards", [])
        for i, sc in enumerate(sub_cards[:2]):
            y = 340 + i * 300
            sc_color = sc.get("color", "#34d399")
            draw.rounded_rectangle([1100, y, 1800, y+270], radius=22, fill="#0c1e36", outline=sc_color, width=2)
            draw.text((1140, y+45), sc.get("title", ""), font=get_font(30), fill=sc_color)
            draw_fitted_text(draw, sc.get("desc", ""), [1140, y+95, 1760, y+245], initial_size=22, min_size=16, fill="#cbd5e1")

    elif layout == "quadrant_grid":
        # 8. 2x2 Grid Quadrant
        if badge:
            draw_badge(draw, badge, 960, 100, border=accent, fg=accent)
        draw.text((960, 180), title, font=get_font(56), fill="#f8fafc", anchor="mm")

        cards = slide_data.get("cards", [])
        for i, c in enumerate(cards[:4]):
            x = 150 + (i % 2) * 830
            y = 280 + (i // 2) * 320
            c_color = c.get("color", accent)
            draw.rounded_rectangle([x, y, x+790, y+280], radius=24, fill="#1e1420", outline=c_color, width=2)
            draw.text((x+45, y+50), c.get("title", ""), font=get_font(30), fill=c_color)
            draw_fitted_text(draw, c.get("desc", ""), [x+45, y+100, x+745, y+250], initial_size=23, min_size=16, fill="#fce7f3")

    elif layout == "radial_ring":
        # 9. Center Seal Badge with 4 Corner Cards
        if badge:
            draw_badge(draw, badge, 960, 90, border=accent, fg=accent)
        draw.text((960, 165), title, font=get_font(52), fill="#f8fafc", anchor="mm")

        coords = [(150, 250), (1030, 250), (150, 600), (1030, 600)]
        cards = slide_data.get("cards", [])
        for i, c in enumerate(cards[:4]):
            x, y = coords[i]
            c_color = c.get("color", accent)
            draw.rounded_rectangle([x, y, x+740, y+310], radius=22, fill="#1c142b", outline=c_color, width=2)
            draw.text((x+40, y+45), c.get("title", ""), font=get_font(30), fill=c_color)
            draw_fitted_text(draw, c.get("desc", ""), [x+40, y+95, x+700, y+285], initial_size=23, min_size=16, fill="#f3e8ff")

        # Center Seal
        draw.ellipse([880, 460, 1040, 620], fill=accent, outline="#ffffff", width=3)
        draw_fitted_text(draw, slide_data.get("center_text", "CORE"), [890, 480, 1030, 600], initial_size=28, min_size=20, fill="#ffffff", align="center", center_y=True)

    elif layout == "circle_stats":
        # 10. Circular KPI + Wide Infrastructure Cards
        if badge:
            draw_badge(draw, badge, 960, 100, border=accent, fg=accent)
        draw.text((960, 180), title, font=get_font(56), fill="#f8fafc", anchor="mm")

        hl = slide_data.get("highlight", {})
        draw.rounded_rectangle([150, 280, 750, 920], radius=26, fill="#0d281f", outline=accent, width=3)
        draw.text((450, 350), hl.get("tag", ""), font=get_font(34), fill=accent, anchor="mm")
        draw.text((450, 450), hl.get("metric", ""), font=get_font(68), fill="#ffffff", anchor="mm")
        draw.text((450, 540), hl.get("label", ""), font=get_font(24), fill="#6ee7b7", anchor="mm")
        draw_fitted_text(draw, hl.get("desc", ""), [180, 600, 720, 880], initial_size=23, min_size=16, fill="#d1fae5", align="center")

        cards = slide_data.get("cards", [])
        for i, c in enumerate(cards[:2]):
            y = 280 + i * 330
            c_color = c.get("color", "#38bdf8")
            draw.rounded_rectangle([810, y, 1770, y+290], radius=22, fill="#0d281f", outline=c_color, width=2)
            draw.text((860, y+50), c.get("title", ""), font=get_font(32), fill=c_color)
            draw_fitted_text(draw, c.get("desc", ""), [860, y+105, 1730, y+260], initial_size=24, min_size=17, fill="#e0f2fe")

    elif layout == "quote_testimonial":
        # 11. Quote / Testimonial Card with 3 Badges
        if badge:
            draw_badge(draw, badge, 960, 100, border=accent, fg=accent)
        draw.text((960, 190), title, font=get_font(58), fill="#f8fafc", anchor="mm")
        if subtitle:
            draw.text((960, 260), subtitle, font=get_font(26), fill="#94a3b8", anchor="mm")

        draw.rounded_rectangle([240, 340, 1680, 890], radius=30, fill="#271120", outline=accent, width=3)
        quote = slide_data.get("quote", "Exceptional experiences inspire lasting impact.")
        draw_fitted_text(draw, f"「{quote}」", [300, 390, 1620, 500], initial_size=32, min_size=22, fill="#fbcfe8", align="center")

        badges = slide_data.get("cards", [])
        for i, b in enumerate(badges[:3]):
            x = 300 + i * 460
            b_color = b.get("color", accent)
            draw.rounded_rectangle([x, 540, x+420, 830], radius=18, fill="#1c0a16", outline=b_color, width=2)
            draw.text((x+210, 595), b.get("title", ""), font=get_font(26), fill=b_color, anchor="mm")
            draw_fitted_text(draw, b.get("desc", ""), [x+25, 645, x+395, 805], initial_size=21, min_size=15, fill="#fce7f3", align="center")

    elif layout == "portal_cta":
        # 12. Grand Finale Portal with Action CTA
        for r in range(480, 0, -12):
            draw.ellipse([960-r, 500-r, 960+r, 500+r], fill=(10, 30, 60))
        if badge:
            draw_badge(draw, badge, 960, 130, border=accent, fg=accent)
        draw.text((960, 260), title, font=get_font(80), fill=accent, anchor="mm")
        if subtitle:
            draw.text((960, 360), subtitle, font=get_font(36), fill="#f8fafc", anchor="mm")

        pillars = slide_data.get("cards", [])
        for i, p in enumerate(pillars[:3]):
            x = 240 + i * 490
            p_color = p.get("color", accent)
            draw.rounded_rectangle([x, 470, x+450, 720], radius=22, fill="#0f172a", outline=p_color, width=2)
            draw.text((x+225, 535), p.get("title", ""), font=get_font(30), fill=p_color, anchor="mm")
            draw_fitted_text(draw, p.get("desc", ""), [x+25, 585, x+425, 695], initial_size=21, min_size=15, fill="#cbd5e1", align="center")

        cta = slide_data.get("cta_text", "Get Started")
        draw.rounded_rectangle([660, 780, 1260, 870], radius=40, fill="#2563eb", outline="#60a5fa", width=2)
        draw.text((960, 825), cta, font=get_font(28), fill="#ffffff", anchor="mm")

    if footer:
        draw.text((960, 990), footer, font=get_font(22), fill="#64748b", anchor="mm")

    img.save(output_path)
    print(f"Slide rendered: {output_path} (Layout: {layout})")

# ==============================================================================
# 🎞️ Video Pipeline (Ken Burns + Xfade Transitions)
# ==============================================================================

TRANSITIONS = [
    "smoothleft", "slideup", "fadeblack", "smoothright", "circleopen",
    "slidedown", "dissolve", "radial", "horzopen", "fade", "fadewhite"
]

def render_motion_clip(png_path, out_clip_path, index, duration=11.2, fps=25):
    frames = int(duration * fps)
    if index % 4 == 0:
        zp = f"zoompan=z='min(zoom+0.0005,1.14)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1920x1080:fps={fps}"
    elif index % 4 == 1:
        zp = f"zoompan=z='min(zoom+0.0006,1.15)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)-(on*0.16)':s=1920x1080:fps={fps}"
    elif index % 4 == 2:
        zp = f"zoompan=z='min(zoom+0.0005,1.14)':d={frames}:x='iw/2-(iw/zoom/2)-(on*0.16)':y='ih/2-(ih/zoom/2)':s=1920x1080:fps={fps}"
    else:
        zp = f"zoompan=z='min(zoom+0.0005,1.14)':d={frames}:x='iw/2-(iw/zoom/2)+(on*0.16)':y='ih/2-(ih/zoom/2)+(on*0.1)':s=1920x1080:fps={fps}"

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-t", str(duration), "-i", png_path,
        "-vf", f"{zp},trim=duration={duration}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
        out_clip_path
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"Motion clip {index+1} ready.")

def render_video(slides_data, audio_path, output_mp4, slide_duration=10.0, transition_duration=1.2, fps=25):
    temp_dir = "/tmp/video_gen_workspace"
    os.makedirs(temp_dir, exist_ok=True)
    num_slides = len(slides_data)
    clip_duration = slide_duration + transition_duration

    # 1. Render Slides PNG
    png_paths = []
    for i, s in enumerate(slides_data):
        p = os.path.join(temp_dir, f"slide_{i+1:02d}.png")
        render_layout(s, p)
        png_paths.append(p)

    # 2. Render Motion Clips
    clip_paths = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = []
        for i, p in enumerate(png_paths):
            out_clip = os.path.join(temp_dir, f"clip_{i+1:02d}.mp4")
            clip_paths.append(out_clip)
            futures.append(executor.submit(render_motion_clip, p, out_clip, i, clip_duration, fps))
        for f in futures:
            f.result()

    # 3. Build Xfade Filter Chain
    inputs = []
    for p in clip_paths:
        inputs.extend(["-i", p])
    inputs.extend(["-i", audio_path])

    filter_steps = []
    current_offset = slide_duration

    for i in range(num_slides - 1):
        t_name = TRANSITIONS[i % len(TRANSITIONS)]
        prev_label = "[0:v]" if i == 0 else f"[v{i}]"
        next_input = f"[{i+1}:v]"
        out_label = f"[v{i+1}]"
        offset_val = round(current_offset, 2)
        filter_steps.append(
            f"{prev_label}{next_input}xfade=transition={t_name}:duration={transition_duration}:offset={offset_val}{out_label}"
        )
        current_offset += slide_duration

    total_video_duration = (num_slides * slide_duration) + transition_duration
    last_v = f"[v{num_slides - 1}]"
    filter_steps.append(f"{last_v}format=yuv420p[vout]")
    filter_steps.append(f"[{num_slides}:a]afade=t=out:st={total_video_duration-2.0}:d=2.0[aout]")

    filter_complex = "; ".join(filter_steps)

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-t", str(total_video_duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "19",
        "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart",
        output_mp4
    ]
    print("Executing final MP4 compilation...")
    subprocess.run(cmd, check=True)
    print(f"🎬 Video generation completed successfully: {output_mp4}")

# ==============================================================================
# 🚀 CLI Entry Point
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="Programmatic Video Generator")
    parser.add_argument("--config", help="Path to JSON file containing slides structure")
    parser.add_argument("--audio", default="/tmp/clear_waters.mp3", help="Audio MP3 or WAV file path")
    parser.add_argument("--output", default="output.mp4", help="Output MP4 video path")
    parser.add_argument("--slide-duration", type=float, default=10.0, help="Duration of each slide in seconds")
    parser.add_argument("--transition-duration", type=float, default=1.2, help="Transition duration in seconds")
    parser.add_argument("--fps", type=int, default=25, help="Video frame rate")
    args = parser.parse_args()

    if args.config and os.path.exists(args.config):
        with open(args.config, "r", encoding="utf-8") as f:
            slides = json.load(f)
    else:
        # Default generic demo
        slides = [
            {
                "layout": "hero_poster",
                "badge": "🚀 NEXT-GEN PLATFORM",
                "title": "Cloud Innovation 2026",
                "subtitle": "Accelerating Intelligent Enterprise Transformations",
                "cards": [
                    {"title": "⚡ Ultra High Speed", "desc": "10x Throughput Boost across all global edge nodes", "color": "#38bdf8"},
                    {"title": "🛡️ Zero Trust Security", "desc": "Enterprise Grade Isolation and cryptographic protection", "color": "#10b981"},
                    {"title": "🤖 Autonomous AI", "desc": "Self-healing Infrastructure with pro-active diagnostics", "color": "#fbbf24"}
                ],
                "footer": "Confidential • Global Presentation"
            }
        ]

    render_video(slides, args.audio, args.output, args.slide_duration, args.transition_duration, args.fps)

if __name__ == "__main__":
    main()
