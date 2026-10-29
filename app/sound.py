# -*- coding: utf-8 -*-
"""按键与计时音效：内置素材和用户导入素材由同一播放器管理。"""
from pathlib import Path
import random
import shutil
import subprocess
import uuid

from PySide6.QtCore import QObject, QEvent, Qt, QUrl, QTimer
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QSoundEffect
from PySide6.QtWidgets import QApplication, QAbstractButton

import config as cfg
import theme as T
from logger import app_root, get_logger

log = get_logger()
FORMATS = {".mp3", ".wav", ".flac", ".ogg", ".m4a"}


class SoundManager(QObject):
    """持有播放器；导入文件只写用户数据目录，绝不改动源文件。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.volume = max(0, min(100, int(cfg.get("sound_volume", 35))))
        self.enabled = bool(cfg.get("sound_master", True))
        self.ui_enabled = bool(cfg.get("sound_ui", True))
        self.stage_enabled = bool(cfg.get("sound_stage", True))
        self._builtin = Path(T.asset_path("sounds"))
        self._custom = Path(app_root()) / "data" / "sounds"
        self._stage_cache_map = {}
        self._stage_current_identifier = None
        self._stage_preview_identifier = None
        self._stage_pending = False
        self._stage_start_attempts = 0
        self._stage_fallback_attempted = set()
        self._tap_preview_identifier = None
        self._tap_preview_effect = None
        self._play_order = {"tap": 0, "stage": 0}
        self._cleanup_stage_cache()
        self._pending_deletes = {}
        self._delete_timer = QTimer(self)
        self._delete_timer.setInterval(250)
        self._delete_timer.timeout.connect(self._flush_pending_deletes)
        self._cleanup_previous_deletes()
        self._tap_players = [self._new_effect() for _ in range(5)]
        self._tap_index = 0
        self._stage_player = self._new_player()
        self._stage_player.mediaStatusChanged.connect(self._on_stage_media_status)
        self._stage_player.playbackStateChanged.connect(self._on_stage_playback_state)
        self._stage_retry_timer = QTimer(self)
        self._stage_retry_timer.setSingleShot(True)
        self._stage_retry_timer.setInterval(900)
        self._stage_retry_timer.timeout.connect(self._retry_stage_start)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def _new_player(self):
        player = QMediaPlayer(self)
        output = QAudioOutput(player)
        output.setVolume(self.volume / 100.0)
        player.setAudioOutput(output)
        player.errorOccurred.connect(self._on_stage_player_error)
        return player

    def _start_stage_playback(self, identifier, path, preview=False):
        self._stage_retry_timer.stop()
        self._stage_pending = True
        self._stage_start_attempts = 0
        self._stage_player.stop()
        self._stage_player.setSource(QUrl.fromLocalFile(str(path)))
        self._stage_current_identifier = identifier
        self._stage_preview_identifier = identifier if preview else None
        self._stage_player.play()
        self._stage_retry_timer.start()

    def _on_stage_media_status(self, status):
        if status == QMediaPlayer.LoadedMedia and self._stage_pending:
            self._stage_player.play()
        elif status == QMediaPlayer.InvalidMedia and self._stage_pending:
            self._retry_stage_format("播放器无法读取媒体文件")

    def _on_stage_playback_state(self, state):
        if state == QMediaPlayer.PlayingState:
            self._stage_pending = False
            self._stage_start_attempts = 0
            self._stage_retry_timer.stop()

    def _retry_stage_start(self):
        if not self._stage_pending:
            return
        if self._stage_start_attempts < 1:
            self._stage_start_attempts += 1
            self._stage_player.play()
            self._stage_retry_timer.start()
            return
        self._retry_stage_format("播放器未能启动音频")

    def _on_stage_player_error(self, _error, message):
        log.warning("计时音乐播放失败：%s", message)
        self._stage_pending = False
        self._stage_retry_timer.stop()
        self._retry_stage_format(message)

    def _retry_stage_format(self, reason):
        identifier = self._stage_current_identifier or ""
        if (not identifier.startswith("custom:") or
                identifier in self._stage_fallback_attempted):
            if reason:
                log.warning("计时音乐未能播放：%s", reason)
            return
        item = next((item for item in self.items("stage", include_hidden=True)
                     if item[0] == identifier), None)
        if item is None:
            return
        self._stage_fallback_attempted.add(identifier)
        folder = self._custom / "_playback"
        fallback = folder / (uuid.uuid4().hex + ".mp3")
        converter = Path(T.asset_path("ffmpeg.exe"))
        try:
            folder.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [str(converter), "-nostdin", "-hide_banner", "-loglevel", "error",
                 "-y", "-i", str(item[2]), "-map", "0:a:0", "-vn", "-sn", "-dn",
                 "-map_metadata", "-1", "-ac", "2", "-ar", "44100",
                 "-c:a", "libmp3lame", "-b:a", "160k", str(fallback)],
                check=True, capture_output=True, timeout=300)
            if not fallback.is_file() or fallback.stat().st_size <= 0:
                raise OSError("转换后音频文件为空")
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            fallback.unlink(missing_ok=True)
            log.warning("计时音乐兼容转换失败：%s", error)
            return
        self._stage_cache_map[identifier] = fallback
        self._start_stage_playback(
            identifier, fallback,
            preview=self._stage_preview_identifier == identifier)

    def _new_effect(self):
        effect = QSoundEffect(self)
        effect.setVolume(self.volume / 100.0)
        return effect

    def _cleanup_stage_cache(self):
        """上次运行留下的试听副本此时已不再被播放器占用。"""
        folder = self._custom / "_playback"
        if folder.is_dir():
            for path in folder.iterdir():
                if path.is_file():
                    try:
                        path.unlink()
                    except OSError:
                        pass

    def _stage_play_path(self, identifier, path):
        if not identifier.startswith("custom:"):
            return path
        cached = self._stage_cache_map.get(identifier)
        if cached is not None and cached.exists():
            return cached
        folder = self._custom / "_playback"
        folder.mkdir(parents=True, exist_ok=True)
        cached = folder / f"{uuid.uuid4().hex}{path.suffix}"
        shutil.copy2(path, cached)
        self._stage_cache_map[identifier] = cached
        return cached

    def configure(self, enabled=None, ui_enabled=None, stage_enabled=None,
                  volume=None):
        if enabled is not None:
            self.enabled = bool(enabled)
        if ui_enabled is not None:
            self.ui_enabled = bool(ui_enabled)
        if stage_enabled is not None:
            self.stage_enabled = bool(stage_enabled)
        if volume is not None:
            self.volume = max(0, min(100, int(volume)))
            for effect in self._tap_players:
                effect.setVolume(self.volume / 100.0)
            self._stage_player.audioOutput().setVolume(self.volume / 100.0)
        if not self.enabled:
            self.stop_stage()

    def eventFilter(self, watched, event):
        if (not self.enabled or not self.ui_enabled or
                not isinstance(watched, QAbstractButton) or
                watched.property("sound_silent")):
            return False
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self.play("tap")
        elif event.type() == QEvent.KeyPress and event.key() in (
                Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.play("tap")
        return False

    def items(self, kind, include_hidden=False):
        """返回 (标识、名称、绝对路径)；设置页与播放逻辑共用。"""
        if kind not in ("tap", "stage"):
            return []
        hidden = set(cfg.get("sound_hidden", []))
        result = []
        for scope, root in (("builtin", self._builtin), ("custom", self._custom)):
            folder = root / kind
            if not folder.is_dir():
                continue
            for path in sorted(folder.iterdir()):
                if path.is_file() and path.suffix.lower() in FORMATS:
                    identifier = f"{scope}:{kind}:{path.name}"
                    if include_hidden or identifier not in hidden:
                        result.append((identifier, path.stem, path))
        return result

    def import_files(self, kind, paths):
        if kind not in ("tap", "stage"):
            return 0
        folder = self._custom / kind
        folder.mkdir(parents=True, exist_ok=True)
        count = 0
        for value in paths:
            source = Path(value)
            if not source.is_file() or source.suffix.lower() not in FORMATS:
                continue
            target = folder / (source.stem + (".wav" if kind == "tap" else ".mp3"))
            number = 2
            while target.exists():
                target = folder / f"{source.stem} ({number}){target.suffix}"
                number += 1
            if kind == "tap":
                converter = Path(T.asset_path("ffmpeg.exe"))
                try:
                    subprocess.run([str(converter), "-nostdin", "-hide_banner",
                                    "-loglevel", "error", "-y", "-i", str(source),
                                    "-af", self._tap_filter(),
                                    "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le",
                                    str(target)], check=True, capture_output=True)
                except (OSError, subprocess.CalledProcessError) as error:
                    target.unlink(missing_ok=True)
                    log.warning("导入按键音效失败：%s", error)
                    continue
            else:
                converter = Path(T.asset_path("ffmpeg.exe"))
                try:
                    subprocess.run(
                        [str(converter), "-nostdin", "-hide_banner", "-loglevel", "error",
                         "-y", "-i", str(source), "-map", "0:a:0", "-vn", "-sn", "-dn",
                         "-map_metadata", "-1", "-ac", "2", "-ar", "44100",
                         "-c:a", "libmp3lame", "-b:a", "192k", str(target)],
                        check=True, capture_output=True, timeout=300)
                    if not target.is_file() or target.stat().st_size <= 0:
                        raise OSError("转换后音频文件为空")
                except (OSError, subprocess.CalledProcessError,
                        subprocess.TimeoutExpired) as error:
                    target.unlink(missing_ok=True)
                    log.warning("导入计时音乐失败：%s", error)
                    continue
            count += 1
            if kind == "stage":
                self._stage_fallback_attempted.discard(
                    f"custom:stage:{target.name}")
        return count

    def remove(self, identifier):
        item = next((item for kind in ("tap", "stage")
                     for item in self.items(kind) if item[0] == identifier), None)
        if item is None:
            return False
        for effect in self._tap_players:
            if effect.source().toLocalFile() == str(item[2]):
                effect.stop()
                effect.setSource(QUrl())
        if self._stage_current_identifier == identifier:
            self._stage_player.stop()
            self._stage_player.setSource(QUrl())
            self._stage_current_identifier = None
        if identifier.startswith("custom:"):
            self._pending_deletes[identifier] = item[2]
            hidden = list(cfg.get("sound_hidden", []))
            if identifier not in hidden:
                cfg.set_("sound_hidden", hidden + [identifier])
            self._flush_pending_deletes()
            if self._pending_deletes:
                self._delete_timer.start()
        else:
            hidden = list(cfg.get("sound_hidden", []))
            if identifier not in hidden:
                hidden.append(identifier)
                cfg.set_("sound_hidden", hidden)
        return True

    def _flush_pending_deletes(self):
        for identifier, path in list(self._pending_deletes.items()):
            try:
                path.unlink(missing_ok=True)
            except PermissionError:
                continue
            except OSError as error:
                log.warning("删除音效失败：%s", error)
                continue
            self._pending_deletes.pop(identifier, None)
            hidden = list(cfg.get("sound_hidden", []))
            if identifier in hidden:
                hidden.remove(identifier)
                cfg.set_("sound_hidden", hidden)
        if not self._pending_deletes:
            self._delete_timer.stop()

    def _cleanup_previous_deletes(self):
        """上次关闭时仍被占用的文件，在本次启动时完成删除。"""
        for identifier in cfg.get("sound_hidden", []):
            parts = str(identifier).split(":", 2)
            if (len(parts) != 3 or parts[0] != "custom" or
                    parts[1] not in ("tap", "stage") or
                    Path(parts[2]).name != parts[2]):
                continue
            path = self._custom / parts[1] / parts[2]
            self._pending_deletes[identifier] = path
        if self._pending_deletes:
            self._flush_pending_deletes()
            if self._pending_deletes:
                self._delete_timer.start()

    def restore_builtin(self, kind):
        hidden = [item for item in cfg.get("sound_hidden", [])
                  if not item.startswith(f"builtin:{kind}:")]
        cfg.set_("sound_hidden", hidden)

    @staticmethod
    def _tap_filter():
        """裁去静音并把每个轻触反馈统一为短促的 200 毫秒。"""
        return ("silenceremove=start_periods=1:start_duration=0.01:"
                "start_threshold=-48dB:stop_periods=-1:stop_duration=0.025:"
                "stop_threshold=-48dB,atrim=duration=0.20,"
                "asetpts=PTS-STARTPTS,apad=whole_dur=0.20,atrim=duration=0.20,"
                "afade=t=out:st=0.16:d=0.04")

    def _choose(self, group, options):
        if not options:
            return None
        mode = str(cfg.get(f"sound_{group}_mode", "random"))
        if mode == "single":
            wanted = cfg.get(f"sound_{group}_single")
            return next((item for item in options if item[0] == wanted), options[0])
        if mode == "sequence":
            index = self._play_order[group] % len(options)
            self._play_order[group] += 1
            return options[index]
        return random.choice(options)

    def play(self, kind="tap", force=False, identifier=None):
        if (not force and (not self.enabled or
                           (kind == "tap" and not self.ui_enabled) or
                           (kind in ("stage", "end") and not self.stage_enabled))) \
                or self.volume <= 0:
            return False
        group = "tap" if kind == "tap" else "stage"
        options = self.items(group)
        if identifier:
            options = [item for item in options if item[0] == identifier]
        if not options:
            return False
        selected = self._choose(group, options)
        if selected is None:
            return False
        selected_id, _, path = selected
        if group == "tap":
            effect = self._tap_players[self._tap_index]
            self._tap_index = (self._tap_index + 1) % len(self._tap_players)
            effect.stop()
            effect.setSource(QUrl.fromLocalFile(str(path)))
            effect.play()
        else:
            try:
                path = self._stage_play_path(selected_id, path)
            except OSError as error:
                log.warning("准备计时音效失败：%s", error)
                return False
            self._start_stage_playback(selected_id, path)
        return True

    def preview(self, kind="stage", identifier=None):
        group = "tap" if kind == "tap" else "stage"
        options = self.items(group)
        if identifier:
            options = [item for item in options if item[0] == identifier]
        elif group == "tap":
            options = [item for item in options
                       if item[0] == cfg.get("sound_tap_single")] or options
        elif kind == "stage":
            options = [item for item in options
                       if item[0] == cfg.get("sound_stage_single")] or options
        if not options:
            return False
        selected_id, _, path = options[0]
        if group == "tap":
            if (self._tap_preview_identifier == selected_id and
                    self._tap_preview_effect is not None and
                    self._tap_preview_effect.isPlaying()):
                self._tap_preview_effect.stop()
                return False
            for effect in self._tap_players:
                effect.stop()
            effect = self._tap_players[0]
            effect.stop()
            effect.setSource(QUrl.fromLocalFile(str(path)))
            effect.play()
            self._tap_preview_identifier = selected_id
            self._tap_preview_effect = effect
            return True
        if (self._stage_preview_identifier == selected_id and
                self._stage_player.playbackState() == QMediaPlayer.PlayingState):
            self._stage_player.pause()
            return False
        if (self._stage_preview_identifier == selected_id and
                self._stage_player.playbackState() == QMediaPlayer.PausedState):
            self._stage_player.play()
            return True
        try:
            path = self._stage_play_path(selected_id, path)
        except OSError as error:
            log.warning("准备计时音效失败：%s", error)
            return False
        self._start_stage_playback(selected_id, path, preview=True)
        return True

    def stop_stage(self):
        self._stage_retry_timer.stop()
        self._stage_pending = False
        self._stage_player.stop()
        self._stage_preview_identifier = None

    def shutdown(self):
        self.stop_stage()
        self._stage_player.setSource(QUrl())
        for effect in self._tap_players:
            effect.stop()
            effect.setSource(QUrl())


_manager = None


def manager():
    global _manager
    if _manager is None:
        _manager = SoundManager(QApplication.instance())
    return _manager
