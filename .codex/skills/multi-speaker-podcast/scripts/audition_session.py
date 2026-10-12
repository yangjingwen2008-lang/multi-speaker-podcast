"""Portable SESX sessions using the sample-based Audition 23 session schema."""

from __future__ import annotations

import math
import os
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

RATE = 48000


def component(parent, identifier: str, kind: str, gain: float = 1.0):
    node = ET.SubElement(
        parent,
        "component",
        componentGuid=str(uuid.uuid4()),
        componentID=f"Audition.{kind}",
        id=identifier,
        name=kind,
        powered="true",
    )
    values = [("volume", gain), ("static gain", 1)] if kind == "Fader" else [("mute", 0)]
    for index, (name, value) in enumerate(values):
        ET.SubElement(node, "parameter", index=str(index), name=name, parameterValue=str(value))


def write_session(path: Path, tracks: list[dict]) -> dict:
    root = ET.Element("sesx", version="1.9")
    total = max((c["start"] + c["duration"] for t in tracks for c in t["clips"]), default=0)
    session = ET.SubElement(
        root,
        "session",
        appBuild="23.0.0.54",
        appVersion="23.0",
        audioChannelType="stereo",
        bitDepth="24",
        duration=str(round(total * RATE)),
        sampleRate=str(RATE),
    )
    container = ET.SubElement(session, "tracks")
    files = ET.SubElement(root, "files")
    file_ids = {}
    clip_id = 0
    for index, track in enumerate(tracks, 1):
        node = ET.SubElement(
            container,
            "audioTrack",
            id=str(10000 + index),
            index=str(index),
            select="false",
            visible="true",
            automationLaneOpenState="false",
        )
        params = ET.SubElement(
            node, "trackParameters", trackHeight="120", trackHue=str((index * 70) % 360), trackMinimized="false"
        )
        ET.SubElement(params, "name").text = track["name"]
        audio = ET.SubElement(
            node,
            "trackAudioParameters",
            audioChannelType="stereo",
            automationMode="1",
            monitoring="false",
            recordArmed="false",
            solo="false",
            soloSafe="false",
        )
        ET.SubElement(audio, "trackOutput", outputID="10000", type="trackID")
        ET.SubElement(audio, "trackInput", inputID="-1")
        component(audio, "trackFader", "Fader", math.pow(10, track.get("gain_db", 0) / 20))
        component(audio, "trackMute", "Mute")
        for clip in track["clips"]:
            source = Path(clip["file"]).resolve()
            if not source.is_file():
                raise ValueError(f"Missing SESX media: {source}")
            if source not in file_ids:
                file_ids[source] = len(file_ids)
                ET.SubElement(
                    files,
                    "file",
                    id=str(file_ids[source]),
                    absolutePath=str(source),
                    relativePath=os.path.relpath(source, path.parent).replace("\\", "/"),
                    mediaHandler="AmioWav",
                    importerPrivateSettings="Compression:0:0;LargeFileSupport:0:0;SampleType:0:1;",
                )
            start = round(clip["start"] * RATE)
            length = round(clip["duration"] * RATE)
            source_start = round(clip.get("source_start", 0) * RATE)
            if start < 0 or source_start < 0 or length <= 0:
                raise ValueError("Invalid SESX sample range.")
            item = ET.SubElement(
                node,
                "audioClip",
                id=str(clip_id),
                fileID=str(file_ids[source]),
                name=clip["name"],
                startPoint=str(start),
                endPoint=str(start + length),
                sourceInPoint=str(source_start),
                sourceOutPoint=str(source_start + length),
                zOrder=str(clip_id),
                hue="-1",
                offline="false",
                select="false",
                looped="false",
                lockedInTime="false",
                clipAutoCrossfade="true",
                crossFadeHeadClipID="-1",
                crossFadeTailClipID="-1",
            )
            component(item, "clipGain", "Fader")
            component(item, "clipMute", "Mute")
            for name, position in (("fadeIn", 0), ("fadeOut", length)):
                ET.SubElement(
                    item,
                    name,
                    startPoint=str(position),
                    endPoint=str(position),
                    shape="18",
                    type="log",
                    crossFadeLinkType="linkedAsymmetric",
                )
            channels = ET.SubElement(item, "channelMap")
            for channel in range(2):
                ET.SubElement(channels, "channel", index=str(channel), sourceIndex=str(channel))
            clip_id += 1
    master = ET.SubElement(
        container,
        "masterTrack",
        id="10000",
        index=str(len(tracks) + 1),
        select="false",
        visible="true",
        automationLaneOpenState="false",
    )
    ET.SubElement(
        ET.SubElement(master, "trackParameters", trackHeight="120", trackHue="-1", trackMinimized="false"), "name"
    ).text = "Master"
    audio = ET.SubElement(
        master,
        "trackAudioParameters",
        audioChannelType="stereo",
        automationMode="1",
        monitoring="false",
        recordArmed="false",
        solo="false",
        soloSafe="true",
    )
    ET.SubElement(audio, "trackOutput", outputID="1", type="hardwareOutput")
    ET.SubElement(audio, "trackInput", inputID="-1")
    component(audio, "trackFader", "Fader")
    component(audio, "trackMute", "Mute")
    ET.indent(root)
    path.write_bytes(
        b'<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n<!DOCTYPE sesx>\n'
        + ET.tostring(root, encoding="utf-8")
    )
    return {
        "file": path.name,
        "tracks": len(tracks),
        "clips": clip_id,
        "duration": total,
        "sample_rate": RATE,
        "validation": "XML and media references; not GUI-verified in Audition",
        "mastering": "Editable pre-master arrangement; remeasure loudness after AU export.",
    }
