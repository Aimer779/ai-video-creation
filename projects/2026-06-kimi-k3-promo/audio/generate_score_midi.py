#!/usr/bin/env python3
"""
为《三月纪》生成配乐参考 MIDI。

用法：
    python audio/generate_score_midi.py

输出：
    audio/demo_score.mid

说明：
    这是一个节奏/结构参考 MIDI，用于在 DAW 中对照视频 v6 进行创作。
    MIDI 中包含大致的音符、段落标记和速度变化，不代表最终演奏细节。
"""

import sys
import os

# 使用项目本地安装的 mido
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".python_packages"))

import mido
from mido import Message, MidiFile, MidiTrack, MetaMessage

# 常量
BPM_BASE = 60
TPQ = 480  # ticks per quarter note

# 片段时间点（秒）
SECTIONS = {
    "intro": (0.0, 1.5),
    "phase1": (1.5, 7.5),
    "phase2_slow": (7.5, 10.5),
    "phase2_fast": (10.5, 14.1),
    "phase3": (14.1, 17.6),
    "brand": (17.6, 20.0),
    "outro": (20.0, 21.5),
}


def seconds_to_ticks(seconds, bpm=BPM_BASE):
    """将秒数转换为 MIDI ticks。"""
    quarters = seconds / (60.0 / bpm)
    return int(quarters * TPQ)


def add_note(track, note, velocity, start_tick, duration_ticks, channel=0):
    """添加一个音符事件。"""
    track.append(Message("note_on", note=note, velocity=velocity, time=start_tick, channel=channel))
    track.append(Message("note_off", note=note, velocity=0, time=duration_ticks, channel=channel))


def add_rest(track, ticks):
    """添加休止符（通过延迟下一个事件实现）。"""
    if track:
        # 修改最后一个事件的时间来制造休止
        last = track[-1]
        last.time += ticks
    else:
        track.append(MetaMessage("track_name", name="Rest", time=ticks))


def main():
    mid = MidiFile(type=1, ticks_per_beat=TPQ)

    # Track 0:  tempo / markers
    meta_track = MidiTrack()
    meta_track.append(MetaMessage("track_name", name="Tempo and Markers", time=0))
    meta_track.append(MetaMessage("set_tempo", tempo=mido.bpm2tempo(BPM_BASE), time=0))
    meta_track.append(MetaMessage("time_signature", numerator=4, denominator=4, clocks_per_click=24, notated_32nd_notes_per_beat=8, time=0))

    current_tick = 0
    for name, (start, end) in SECTIONS.items():
        marker_tick = seconds_to_ticks(start)
        meta_track.append(MetaMessage("marker", text=name, time=marker_tick - current_tick))
        current_tick = marker_tick

    # 尾声标记
    end_tick = seconds_to_ticks(21.5)
    meta_track.append(MetaMessage("marker", text="end", time=end_tick - current_tick))
    meta_track.append(MetaMessage("end_of_track", time=0))
    mid.tracks.append(meta_track)

    # Track 1: 钢琴参考旋律
    piano = MidiTrack()
    piano.append(MetaMessage("track_name", name="Piano Reference", time=0))
    piano.append(Message("program_change", program=0, channel=0, time=0))

    t = 0

    # 0:00–0:01.5 引子：D2, A2, D3
    intro_start = seconds_to_ticks(0.3)
    add_note(piano, 38, 45, intro_start - t, seconds_to_ticks(0.6))
    t = intro_start
    add_note(piano, 45, 40, seconds_to_ticks(0.4), seconds_to_ticks(0.5))
    t += seconds_to_ticks(0.4)
    add_note(piano, 50, 38, seconds_to_ticks(0.3), seconds_to_ticks(0.6))
    t += seconds_to_ticks(0.3)

    # 0:01.5–0:07.5 Phase 1：稀疏主题
    phase1_start = seconds_to_ticks(1.5)
    # D4 F4 A4 G4 F4 D4，每音之间留空
    melody_p1 = [62, 65, 69, 67, 65, 62]
    note_dur = seconds_to_ticks(0.6)
    gap = seconds_to_ticks(0.9)
    for i, note in enumerate(melody_p1):
        wait = (phase1_start + i * (note_dur + gap)) - t
        vel = 42 if i == 0 else 38
        add_note(piano, note, vel, max(0, wait), note_dur)
        t = phase1_start + i * (note_dur + gap) + note_dur

    # 低音 heartbeat
    bass_track = MidiTrack()
    bass_track.append(MetaMessage("track_name", name="Bass Reference", time=0))
    bass_track.append(Message("program_change", program=0, channel=1, time=0))
    bt = seconds_to_ticks(2.0)
    while bt < seconds_to_ticks(7.0):
        add_note(bass_track, 38, 35, bt - seconds_to_ticks(2.0), seconds_to_ticks(0.8), channel=1)
        bt += seconds_to_ticks(2.0)
    bass_track.append(MetaMessage("end_of_track", time=0))

    # 0:07.5–0:10.5 Phase 2 前半：重复音型
    phase2_start = seconds_to_ticks(7.5)
    pattern = [(62, 65, 69), (65, 69, 74), (69, 74, 77)]
    cycle_dur = seconds_to_ticks(1.0)
    for cycle in range(3):
        for idx, chord in enumerate(pattern):
            base = phase2_start + cycle * cycle_dur + idx * seconds_to_ticks(0.33)
            for n in chord:
                wait = base - t
                add_note(piano, n, 46, max(0, wait), seconds_to_ticks(0.25))
                t = base

    # 0:10.5–0:14.1 Phase 2 后半：加速上行
    phase2_fast_start = seconds_to_ticks(10.5)
    # 高音闪烁 + 低音分解
    flash_notes = [74, 77, 81, 86, 89, 93]
    flash_times = [phase2_fast_start + i * seconds_to_ticks(0.45) for i in range(len(flash_notes))]
    for note, ft in zip(flash_notes, flash_times):
        wait = ft - t
        add_note(piano, note, 50, max(0, wait), seconds_to_ticks(0.3))
        t = ft

    # 0:14.1–0:17.6 Phase 3：高点，长音 + 琶音
    phase3_start = seconds_to_ticks(14.1)
    wait = phase3_start - t
    # 长 D6
    add_note(piano, 86, 55, max(0, wait), seconds_to_ticks(1.5))
    t = phase3_start + seconds_to_ticks(1.5)
    # 琶音 D5 F5 A5 D6
    arp = [74, 77, 81, 86]
    for i, note in enumerate(arp):
        add_note(piano, note, 52, seconds_to_ticks(0.1), seconds_to_ticks(0.25))
        t += seconds_to_ticks(0.35)
    # 留白
    t += seconds_to_ticks(0.5)
    # 再一个 D6
    add_note(piano, 86, 48, 0, seconds_to_ticks(0.8))
    t += seconds_to_ticks(0.8)

    # 0:17.6–0:20.0 品牌卡：回落
    brand_start = seconds_to_ticks(17.6)
    wait = brand_start - t
    add_note(piano, 62, 40, max(0, wait), seconds_to_ticks(0.8))
    t = brand_start + seconds_to_ticks(1.2)
    add_note(piano, 65, 38, 0, seconds_to_ticks(0.6))
    t += seconds_to_ticks(1.0)
    add_note(piano, 69, 35, 0, seconds_to_ticks(1.0))
    t += seconds_to_ticks(1.0)

    # 0:20.0–0:21.5 尾声：泛音感单音
    outro_start = seconds_to_ticks(20.0)
    wait = outro_start - t
    add_note(piano, 50, 32, max(0, wait), seconds_to_ticks(1.2))
    t = outro_start + seconds_to_ticks(1.5)
    add_note(piano, 45, 28, 0, seconds_to_ticks(1.5))

    piano.append(MetaMessage("end_of_track", time=0))
    mid.tracks.append(piano)
    mid.tracks.append(bass_track)

    output_path = os.path.join(os.path.dirname(__file__), "demo_score.mid")
    mid.save(output_path)
    print(f"Saved {output_path}")


if __name__ == "__main__":
    main()
