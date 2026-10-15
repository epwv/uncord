#voice calls screen sharing and watching streams
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from rich.style import Style
from rich.text import Text
from textual import work

from . import emojis as emo
from . import media
from . import golive, streamview
from . import voice as vmod
from .fmt import PRIVATE, channelName, isVoice, snippet
from .screens import Picker, UserMenu

from .panels import ChatInput, VoiceBar
from .tasks import spawn

log = logging.getLogger("uncord")

#width height fps kbps
SHARE_QUALITIES = {
    "720p30": (1280, 720, 30, 2500),
    "1080p30": (1920, 1080, 30, 4500),
    "1080p60": (1920, 1080, 60, 6000),
    "1440p60": (2560, 1440, 60, 9000),
}


class CallsMixin:

    #voice

    def action_join_voice(self) -> None:
        if self.incoming is not None:
            self.joinVoice(self.incoming, ring=False)
            return
        ch = self.current
        if ch is None:
            self.notify("Open a DM, group or voice channel first.")
            return
        if not (isinstance(ch, PRIVATE) or isVoice(ch)):
            self.notify(
                "That's a text channel. Pick a 🔊 voice channel, DM or group to call.", severity="warning"
            )
            return
        self.joinVoice(ch)

    #connect apply your saved voice settings and start the audio threads
    @work(exclusive=True, group="voice")
    async def joinVoice(self, ch, ring: bool = True) -> None:
        if self.vc is not None and self.vc.channel is not None and self.vc.channel.id == ch.id:
            self.notify("Already connected here.")
            return
        problem = vmod.audioToolsOk()
        if problem is None and not vmod.load_opus():
            problem = "libopus not found (set UNCORD_OPUS=/path/to/libopus.so.0)"
        if problem:
            self.notify(f"Voice unavailable: {problem}", severity="error", markup=False)
            return
        if self.vc is not None:
            await self._leave()
        self.notify(f"Connecting to {channelName(ch)}…", markup=False)
        try:
            if isinstance(ch, PRIVATE):
                vc = await ch.connect(cls=vmod.UncordVoiceClient, ring=ring)
            else:
                vc = await ch.connect(
                    cls=vmod.UncordVoiceClient,
                    self_deaf=self.selfDeafened,
                    self_mute=self.selfMuted or self.selfDeafened,
                )
        except Exception as e:
            log.exception("voice connect")
            self.notify(f"Voice connect failed: {e!r}", severity="error", markup=False)
            return
        loop = asyncio.get_running_loop()
        vc.onChange = lambda: loop.call_soon_threadsafe(self.updateVoicebar)
        self.stopMicTest()
        vc.vadThreshold = 0 if int(self.cfg.get("vad", 600)) == 0 else int(self.cfg.get("vad", 600))
        vc.autoSensitivity = bool(self.cfg.get("auto_sensitivity", True))
        vc.sensitivityDb = float(self.cfg.get("sensitivity_db", -50))
        vc.inputDevice = self.cfg.get("input_device") or None
        vc.outputDevice = self.cfg.get("output_device") or None
        vc.inputGain = int(self.cfg.get("input_volume", 100)) / 100
        vc.agc = bool(self.cfg.get("agc", True))
        vc.selfMuted = self.selfMuted or self.selfDeafened
        vc.selfDeafened = self.selfDeafened
        vc.userVolume = {int(k): v / 100 for k, v in self.cfg.get("user_volume", {}).items()}
        vc.startAudio()
        if vc.speaker:
            vc.speaker.volume = int(self.cfg.get("volume", 100)) / 100
        self.vc = vc
        self.callStarted = time.monotonic()
        self.sfx("connect")
        if isinstance(ch, PRIVATE) and (self.selfMuted or self.selfDeafened):
            spawn(self._pushVoiceFlags())
        log.info("joined voice %s (%s)", ch.id, type(ch).__name__)
        if self.incoming is not None and self.incoming.id == ch.id:
            self.incoming = None
        self.relabel(ch)
        self.updateVoicebar()
        self.notify(
            f"Connected to voice: {channelName(ch)}"
            + (" (ringing…)" if isinstance(ch, PRIVATE) and ring else ""),
            markup=False,
        )

    async def _leave(self) -> None:
        await self.stopShare(quiet=True)
        await self.stopWatch(quiet=True)
        await self.stopCamera(quiet=True)
        vc, self.vc = self.vc, None
        if vc is None:
            return
        ch = vc.channel
        self.sfx("disconnect")
        try:
            await vc.disconnect(force=True)
        except Exception:
            vc.stopAudio()
        if ch is not None:
            self.relabel(ch)
        self.updateVoicebar()

    def action_leave_voice(self) -> None:
        if self.vc is None:
            self.notify("Not in voice.")
            return
        spawn(self._leave())
        self.notify("Left voice.")

    async def _pushVoiceFlags(self) -> None:
        vc = self.vc
        if vc is None or vc.channel is None:
            return
        try:
            guild = getattr(vc.channel, "guild", None)
            if guild is not None:
                await guild.change_voice_state(
                    channel=vc.channel, self_mute=vc.selfMuted, self_deaf=vc.selfDeafened,
                    self_video=self.camera is not None,
                )
            else:
                await self.client.change_voice_state(
                    channel=vc.channel, self_mute=vc.selfMuted, self_deaf=vc.selfDeafened,
                    self_video=self.camera is not None,
                )
        except Exception:
            log.exception("voice flags")

    def action_toggle_mute(self) -> None:
        self.selfMuted = not self.selfMuted
        if not self.selfMuted and self.selfDeafened:
            self.selfDeafened = False  #unmute also undeafens like discord
        self.sfx("mute" if self.selfMuted else "unmute")
        self._applySelfFlags()

    def action_toggle_deafen(self) -> None:
        self.selfDeafened = not self.selfDeafened
        self.sfx("deafen" if self.selfDeafened else "undeafen")
        self._applySelfFlags()

    def _applySelfFlags(self) -> None:
        if self.vc is not None:
            self.vc.selfMuted = self.selfMuted or self.selfDeafened
            self.vc.selfDeafened = self.selfDeafened
            spawn(self._pushVoiceFlags())
        self.updateMePanel()
        self.updateVoicebar()

    def callParticipants(self) -> list:
        vc = self.vc
        ch = vc.channel if vc else None
        if ch is None:
            return []
        if isinstance(ch, PRIVATE):
            call = getattr(ch, "call", None)
            states = dict(call.voice_states) if call is not None else {}
        else:
            states = dict(getattr(ch, "voice_states", {}))
        out = []
        for uid, vs in states.items():
            if uid == self.meId:
                continue
            u = (ch.guild.get_member(uid) if getattr(ch, "guild", None) else None) or self.client.get_user(
                uid
            )
            out.append((uid, u.display_name if u else str(uid), vs))
        return out

    #the call strip above the chat redrawn every 0.4s but only pushed to the screen when it changed
    def updateVoicebar(self) -> None:
        self.updateMePanel()
        bar = self.query_one("#voicebar", VoiceBar)
        vc = self.vc
        ch = vc.channel if vc else None
        if vc is None or ch is None:
            bar.remove_class("on")
            self._micWarned = False
            return
        bar.add_class("on")
        talking = set(vc.speakingUserIds())
        if vc.micSilent and not self.selfMuted and not self._micWarned:
            self._micWarned = True
            dev = self.cfg.get("input_device") or "your default mic"
            self.notify(
                f"Your mic ({dev}) is sending pure silence: it's muted on the device, or it's the wrong mic. "
                "Press F9 (or ⚙) to pick another input.",
                severity="warning",
                timeout=15,
                markup=False,
            )
        if True:
            t = Text()
            people = self.callParticipants()
            meTalking = vc.transmitting and not self.selfMuted
            here = self.current is not None and self.current.id == ch.id
            if not here:
                t.append(f"🔊 {snippet(channelName(ch), 24)}", style=Style(bold=True, meta={"vbtn": "chat"}))
                t.append("  ")
            if self.share is not None:
                t.append("🔴 LIVE ", style="bold red")
            if self.camera is not None:
                t.append("📷 ", style="bold")
            t.append("● " if meTalking else "○ ", style="bold green" if meTalking else "bright_black")
            t.append("You", style=Style(bold=True, color="green" if meTalking else None))
            if vc.micSilent and not self.selfMuted:
                t.append(" ⚠ no mic input", style=Style(color="yellow", bold=True, meta={"vbtn": "settings"}))
            t.append("   ")
            if not people:
                t.append("waiting for others to join…", style="bright_black")
            for uid, name, vs in people:
                vol = int(self.cfg.get("user_volume", {}).get(str(uid), 100))
                speaking = uid in talking
                t.append(
                    "● " if speaking else "○ ",
                    style=Style(color="green" if speaking else "bright_black", meta={"vuser": uid}),
                )
                label = name
                if getattr(vs, "self_mute", False) or getattr(vs, "mute", False):
                    label += " 🔇"
                if getattr(vs, "self_deaf", False) or getattr(vs, "deaf", False):
                    label += " 🙉"
                if vol != 100:
                    label += f" {vol}%"
                t.append(
                    label,
                    style=Style(
                        bold=speaking,
                        color="green" if speaking else ("bright_black" if vol == 0 else None),
                        meta={"vuser": uid},
                    ),
                )
                if getattr(vs, "self_stream", False):
                    on = self.isWatching(uid, "stream")
                    t.append(
                        " 📺 WATCHING" if on else " 🔴LIVE",
                        style=Style(bold=True, color="white", bgcolor="red", meta={"vwatch": uid}),
                    )
                if getattr(vs, "self_video", False):
                    on = self.isWatching(uid, "camera")
                    t.append(
                        " 📷 WATCHING" if on else " 📷",
                        style=Style(bold=True, color="white", bgcolor="blue" if on else None, meta={"vcam": uid}),
                    )
                t.append("   ")
            t.append("\n")

            def btn(label, key, on=False, danger=False):
                t.append(
                    f" {label} ",
                    style=Style(
                        meta={"vbtn": key},
                        bold=True,
                        color="white" if (on or danger) else None,
                        bgcolor="red" if (on or danger) else "bright_black",
                    ),
                )
                t.append(" ")

            if self.plugin("call_timer") and self.callStarted:
                secs = int(time.monotonic() - self.callStarted)
                h, m = divmod(secs // 60, 60)
                t.append((f"{h}:{m:02d}:{secs % 60:02d}" if h else f"{m}:{secs % 60:02d}") + "  ", style="bright_black")
            btn("🔇 Unmute" if self.selfMuted else "🎙 Mute", "mute", self.selfMuted)
            btn("🙉 Undeafen" if self.selfDeafened else "🎧 Deafen", "deafen", self.selfDeafened)
            btn("■ Stop sharing" if self.share else "🖥 Share", "share", on=bool(self.share))
            btn("■ Camera off" if self.camera else "📷 Camera", "camera", on=bool(self.camera))
            if self.watching is not None or any(
                getattr(vs, "self_stream", False) or getattr(vs, "self_video", False) for _u, _n, vs in people
            ):
                btn(
                    "📺 Watching" if self.watching is not None else "📺 Watch (F11)",
                    "watch",
                    on=self.watching is not None,
                )
            btn("⚙", "settings")
            btn("✆ Leave call (F8)", "leave", danger=True)
            if t != self._lastBar:
                self._lastBar = t
                bar.update(t)
        key = (ch.id if ch else None, tuple(sorted(talking)))
        if key != self._lastTalking and ch is not None and not isinstance(ch, PRIVATE):
            self._lastTalking = key
            node = self.nodes.get(ch.id)
            if node is not None:
                self.fillVoiceMembers(node, ch)

    #screenshare

    def action_share(self) -> None:
        if self.share is not None:
            spawn(self.stopShare())
            return
        if self._shareStarting:
            return
        if self.vc is None or not self.vc.is_connected():
            self.notify("Join a call first (F5), then share your screen.")
            return
        if not golive.GSR:
            self.notify("Screen sharing needs gpu-screen-recorder.", severity="error")
            return
        mons = golive.listMonitors()
        if not mons:
            self.notify("No monitors found to share.", severity="error")
            return
        if len(mons) == 1:
            self.startShare(mons[0][0])
            return
        q = self.shareQuality()
        entries = [
            (Text(f"🖥 {name}") + Text(f"  {res}", style="bright_black"), name.lower(), name)
            for name, res in mons
        ]
        self.push_screen(
            Picker(
                entries,
                placeholder="Which screen?",
                title=f"Share screen · {q} (change: /share 720p30|1080p30|1080p60|1440p60)",
            ),
            lambda m: m and self.startShare(m),
        )

    #non nitro accounts are limited to 720p30
    def shareQuality(self) -> str:
        q = self.cfg.get("share_quality")
        if q in SHARE_QUALITIES:
            return q
        return "1080p60" if emo.hasNitro(self.client) else "720p30"  #non nitro limits

    #go live the stream key and server come from discord then golive.ScreenShare takes over
    @work(exclusive=True, group="share")
    async def startShare(self, monitor: str) -> None:
        vc = self.vc
        if vc is None:
            return
        w, h, fps, kbps = SHARE_QUALITIES[self.shareQuality()]
        golive.ScreenShare.monitor = monitor
        (
            golive.ScreenShare.width,
            golive.ScreenShare.height,
            golive.ScreenShare.fps,
            golive.ScreenShare.kbps,
        ) = (w, h, fps, kbps)
        self._shareStarting = True
        self.notify(f"Going live: {monitor} at {self.shareQuality()}…", timeout=4, markup=False)
        try:
            proto = await vc.create_stream(cls=golive.ScreenShare, timeout=20, reconnect=False)
        except Exception as e:
            log.exception("go live failed")
            self.notify(f"Couldn't start screen share: {e}", severity="error", markup=False)
            return
        finally:
            self._shareStarting = False
        self.share = proto

        def ended(why) -> None:
            if self.share is proto:
                self.share = None
                if why:
                    self.notify(why, severity="warning", markup=False)
                self.updateVoicebar()

        proto.onEnd = lambda why: self.call_later(ended, why)
        self.updateVoicebar()
        self.notify(f"🔴 You're live: sharing {monitor}. F10 or ■ Stop sharing to end.", markup=False)

    async def stopShare(self, quiet: bool = False) -> None:
        proto, self.share = self.share, None
        if proto is None:
            return
        proto.onEnd = None
        try:
            await proto.disconnect(force=True)
        except Exception:
            log.debug("stop share", exc_info=True)
        if not quiet:
            self.notify("Stopped sharing.")
        self.updateVoicebar()

    #watching streams

    def liveStreamers(self) -> list:
        return [(uid, name) for uid, name, vs in self.callParticipants() if getattr(vs, "self_stream", False)]

    def liveCameras(self) -> list:
        return [(uid, name) for uid, name, vs in self.callParticipants() if getattr(vs, "self_video", False)]

    def isWatching(self, uid: int, kind: str) -> bool:
        return self.watching is not None and self.watching.owner_id == uid and self.watchKind == kind

    #f11 watch a screen share or a camera, picks for you if theres only one
    def action_watch(self, uid: Optional[int] = None, camera: bool = False) -> None:
        if not media.FFMPEG:
            self.notify(
                "Watching streams needs ffmpeg. Run uncord --doctor to see what's missing.", severity="error"
            )
            return
        kind = "camera" if camera else "stream"
        if self.watching is not None and (uid is None or self.isWatching(uid, kind)):
            self.openWatchScreen()
            return
        if self._watchStarting:
            return
        if self.vc is None or not self.vc.is_connected():
            self.notify("Join the call first (F5), then watch their stream.")
            return
        if uid is None:
            choices = [(u, n, "stream") for u, n in self.liveStreamers()] + [
                (u, n, "camera") for u, n in self.liveCameras()]
            if not choices:
                self.notify("Nobody in this call is sharing their screen or camera.")
                return
            if len(choices) > 1:
                entries = [(Text(f"🔴 {n}'s screen" if k == "stream" else f"📷 {n}'s camera"), f"{n} {k}".lower(),
                            (u, k)) for u, n, k in choices]
                self.push_screen(
                    Picker(entries, placeholder="Watch what?", title="Screen shares and cameras"),
                    lambda v: v and self.startWatch(v[0], v[1] == "camera"),
                )
                return
            uid, camera = choices[0][0], choices[0][2] == "camera"
        self.startWatch(uid, camera)

    #join someones stream and wire its frames to a decoder and its audio to our speaker
    @work(exclusive=True, group="watch")
    async def startWatch(self, uid: int, camera: bool = False) -> None:
        vc = self.vc
        if vc is None or vc.channel is None:
            return
        if self.watching is not None:
            await self.stopWatch(quiet=True)
        ch = vc.channel
        guild = getattr(ch, "guild", None)
        user = (guild.get_member(uid) if guild is not None else None) or self.client.get_user(uid)
        name = user.display_name if user else str(uid)
        self._watchStarting = True
        try:
            if camera:
                proto = golive.CameraWatch(vc, uid)
                await proto.start()
            else:
                from discord.stream import StreamKey

                key = (
                    StreamKey.from_guild(guild_id=guild.id, channel_id=ch.id, owner_id=uid)
                    if guild is not None
                    else StreamKey.from_call(channel_id=ch.id, owner_id=uid)
                )
                proto = await vc.watch_stream(key, cls=golive.StreamWatch, timeout=20, reconnect=False)
        except Exception as e:
            log.exception("watch failed")
            self.notify(f"Couldn't watch {name}'s {'camera' if camera else 'stream'}: {e}", severity="error",
                        markup=False)
            return
        finally:
            self._watchStarting = False
        decoder = golive.StreamDecoder(proto.requestKeyframe)
        proto.onFrame = decoder.feed
        proto.onCodec = decoder.setCodec
        proto.onAudio = None if camera else self._watchAudio
        self._watchOpus = {}
        self.watchKind = "camera" if camera else "stream"
        self.watching, self.watchDecoder = proto, decoder
        self.watchTitle = f"{name}'s camera" if camera else f"{name}'s screen"

        def ended(why) -> None:
            if self.watching is proto:
                self.watching = self.watchDecoder = None
                decoder.close()
                if isinstance(self.screen, streamview.StreamScreen):
                    self.screen.dismiss(None)
                self.notify(why or f"{name}'s {'camera' if camera else 'stream'} ended.", severity="warning", markup=False)
                self.updateVoicebar()

        proto.onEnd = lambda why: self.call_later(ended, why)
        self.updateVoicebar()
        self.openWatchScreen()

    #stream audio is opus like voice decode it and mix it in as its own source
    def _watchAudio(self, ssrc: int, packet: bytes) -> None:
        vc = self.vc
        if self._watchAudioMuted or vc is None or vc.speaker is None or self.selfDeafened:
            return
        dec = self._watchOpus.get(ssrc)
        if dec is None:
            from discord import opus

            dec = self._watchOpus[ssrc] = opus.Decoder()
        try:
            pcm = dec.decode(bytes(packet), fec=False)
        except Exception:
            return
        vc.speaker.push(("stream", ssrc), pcm)

    def openWatchScreen(self) -> None:
        if (
            self.watching is None
            or self.watchDecoder is None
            or isinstance(self.screen, streamview.StreamScreen)
        ):
            return
        proto, decoder = self.watching, self.watchDecoder
        fps = float(self.cfg.get("watch_fps", 60))

        def closed(result) -> None:
            if self.watching is not proto:
                return
            if result == "stop" or not decoder.popoutOpen():
                spawn(self.stopWatch())
            else:
                decoder.stopDecode()
                self.notify("Still playing in mpv", timeout=3, markup=False)
            self.updateVoicebar()

        self.push_screen(
            streamview.StreamScreen(
                proto, decoder, self.watchTitle, fps=fps, full=bool(self.cfg.get("watch_full", False)),
                box=int(self.cfg.get("watch_size", streamview.DEFAULT_BOX))
            ),
            closed,
        )

    async def stopWatch(self, quiet: bool = False) -> None:
        proto, decoder = self.watching, self.watchDecoder
        self.watching = self.watchDecoder = None
        if proto is None:
            return
        proto.onEnd = None
        proto.onFrame = proto.onAudio = None
        if decoder is not None:
            decoder.close()
        vc = self.vc
        if vc is not None and vc.speaker is not None:
            for ssrc in list(self._watchOpus):
                vc.speaker.drop(("stream", ssrc))
        self._watchOpus = {}
        if isinstance(self.screen, streamview.StreamScreen):
            self.screen.dismiss(None)
        try:
            await proto.disconnect(force=True)
        except Exception:
            log.debug("stop watch", exc_info=True)
        if not quiet:
            self.notify("Stopped watching.")
        self.updateVoicebar()

    #your own webcam f12 toggles it
    def action_camera(self) -> None:
        if self.camera is not None:
            spawn(self.stopCamera())
            return
        if self._cameraStarting:
            return
        if self.vc is None or not self.vc.is_connected():
            self.notify("Join a call first (F5), then turn your camera on.")
            return
        if not golive.GSR:
            self.notify("The camera needs gpu-screen-recorder.", severity="error")
            return
        modes = golive.listCameras()
        devices = sorted({m[0] for m in modes})
        if not devices:
            self.notify("No camera found.", severity="error")
            return
        saved = self.cfg.get("camera_device")
        if saved in devices or len(devices) == 1:
            self.startCamera(golive.pickCameraMode(modes, saved if saved in devices else devices[0]))
            return
        entries = []
        for d in devices:
            m = golive.pickCameraMode(modes, d)
            entries.append((Text(f"📷 {d}") + Text(f"  {m[1]}x{m[2]} {m[3]}fps", style="bright_black"), d, m))
        self.push_screen(Picker(entries, placeholder="Which camera?", title="Camera"),
                         lambda m: m and self.startCamera(m))

    @work(exclusive=True, group="camera")
    async def startCamera(self, mode) -> None:
        vc = self.vc
        if vc is None or mode is None:
            return
        device, w, h, fps = mode
        self._cameraStarting = True
        cam = golive.CameraShare(vc, device, w, h, min(fps, 30) or 30)
        try:
            await cam.start()
        except Exception as e:
            log.exception("camera failed")
            self.notify(f"Couldn't turn the camera on: {e}", severity="error", markup=False)
            await cam.stop()
            return
        finally:
            self._cameraStarting = False
        self.camera = cam
        self.cfg["camera_device"] = device
        self.cfg.save()

        def died(why) -> None:
            if self.camera is cam:
                spawn(self.stopCamera(quiet=True))
                self.notify(why, severity="warning", markup=False)

        cam.onEnd = died
        await self._pushVoiceFlags()
        self.updateVoicebar()
        self.notify(f"📷 Camera on ({w}x{h}). F12 to turn it off.", markup=False)

    async def stopCamera(self, quiet: bool = False) -> None:
        cam, self.camera = self.camera, None
        if cam is None:
            return
        cam.onEnd = None
        await cam.stop()
        await self._pushVoiceFlags()
        if not quiet:
            self.notify("Camera off.")
        self.updateVoicebar()

    #f9 opens settings on the voice tab
    def action_voice_settings(self) -> None:
        self.action_settings("voice")

    #mic level for the meter, from the call or a test mic when not in one
    def micLevelDb(self) -> Optional[float]:
        if self.vc is not None:
            return self.vc.levelDb if self.vc.mic is not None else None
        m = self._testMic
        if m is None:
            return None
        db = vmod.rmsToDb(m.level)
        self._testLevels.append(db)
        del self._testLevels[:-50]
        return db

    def micGateDb(self) -> float:
        if self.vc is not None:
            return self.vc.thresholdDb()
        if self.cfg.get("auto_sensitivity", True):
            lv = sorted(self._testLevels)
            floor = lv[len(lv) // 5] if len(lv) >= 25 else -60.0
            return max(-65.0, min(-25.0, floor + 12.0))
        return float(self.cfg.get("sensitivity_db", -50))

    def startMicTest(self) -> None:
        self.stopMicTest()
        try:
            self._testMic = vmod.Mic(
                self.cfg.get("input_device") or None, int(self.cfg.get("input_volume", 100)) / 100
            )
            self._testLevels = []
            self._testDrain = self.set_interval(0.02, lambda: self._testMic and self._testMic.flush())
        except Exception:
            log.exception("mic test")
            self._testMic = None

    def stopMicTest(self) -> None:
        m, self._testMic = getattr(self, "_testMic", None), None
        if m is not None:
            m.close()
        t = getattr(self, "_testDrain", None)
        if t is not None:
            t.stop()
            self._testDrain = None

    def applyVoiceSetting(self, key: str, value) -> None:
        self.cfg[key] = value
        if key == "auto_sensitivity" and value is False and "sensitivity_db" not in self.cfg:
            self.cfg["sensitivity_db"] = -50
        self.cfg.save()
        vc = self.vc
        if key == "input_device":
            if vc is not None:
                vc.setInputDevice(value)
            elif self._testMic is not None:
                self.startMicTest()
        elif key == "output_device" and vc is not None:
            vc.setOutputDevice(value)
        elif key == "input_volume":
            if vc is not None:
                vc.setInputGain(value / 100)
            if self._testMic is not None:
                self._testMic.gain = value / 100
        elif key == "volume" and vc is not None and vc.speaker is not None:
            vc.speaker.volume = value / 100
        elif key == "auto_sensitivity" and vc is not None:
            vc.autoSensitivity = bool(value)
        elif key == "sensitivity_db" and vc is not None:
            vc.sensitivityDb = float(value)
        elif key == "agc" and vc is not None:
            vc.agc = bool(value)
        self.updateMePanel()

    def userMenu(self, uid: int) -> None:
        u = self.client.get_user(uid)
        name = u.display_name if u else str(uid)
        cur = int(self.cfg.get("user_volume", {}).get(str(uid), 100))
        muted = cur == 0
        actions = [(Text("☑ Mute (for you)" if muted else "☐ Mute (for you)"), "mute")]
        if u is not None:
            actions += [
                (Text("👤 Profile"), "profile"),
                (Text("✉ Message"), "dm"),
                (Text("@ Mention"), "mention"),
            ]
        actions.append((Text("⧉ Copy User ID"), "copyid"))

        def done(act) -> None:
            if act == "mute":
                self.setUserVolume(uid, 100 if muted else 0)
            elif act == "profile" and u is not None:
                self.showProfile(u)
            elif act == "dm" and u is not None:
                self.openDm(u)
            elif act == "mention" and u is not None:
                inp = self.query_one("#input", ChatInput)
                inp.insert_text_at_cursor(f"@{u.name} ")
                inp.focus()
            elif act == "copyid":
                self.copyText(str(uid))

        self.push_screen(UserMenu(name, cur, lambda v: self.setUserVolume(uid, v, quiet=True), actions), done)

    def setUserVolume(self, uid: int, pct: int, quiet: bool = False) -> None:
        pct = max(0, min(200, int(pct)))
        vols = dict(self.cfg.get("user_volume", {}))
        if pct == 100:
            vols.pop(str(uid), None)
        else:
            vols[str(uid)] = pct
        self.cfg["user_volume"] = vols
        self.cfg.save()
        if self.vc is not None:
            if pct == 100:
                self.vc.userVolume.pop(uid, None)
            else:
                self.vc.userVolume[uid] = pct / 100
        if not quiet:
            u = self.client.get_user(uid)
            self.notify(
                f"{u.display_name if u else uid}: {'muted for you' if pct == 0 else f'{pct}%'}",
                markup=False,
                timeout=3,
            )
        self._lastTalking = None
        self.updateVoicebar()
