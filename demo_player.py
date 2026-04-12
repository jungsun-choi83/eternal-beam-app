# -*- coding: utf-8 -*-
"""
감성 시연용 고퀄리티 재생기
- 특정 폴더의 sample.mp4 전체화면 재생, 무한 루프
- 동일 폴더의 bgm.mp3 자동 재생
- ESC: 종료, F: 전체화면/창 모드 전환
"""
import os
import sys
from typing import Optional

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtWidgets import QApplication, QMainWindow
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
from PyQt5.QtMultimediaWidgets import QVideoWidget


# 재생할 미디어가 있는 폴더 (스크립트 기준 상대경로 또는 절대경로)
MEDIA_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media")


class DemoPlayerWindow(QMainWindow):
    def __init__(self, video_path: str, bgm_path: Optional[str]):
        super().__init__()
        self._video_path = video_path
        self._bgm_path = bgm_path
        self._is_fullscreen = True

        # 비디오 위젯 (전체 화면에 맞춤)
        self._video_widget = QVideoWidget(self)
        self._video_widget.setStyleSheet("background-color: black;")
        self.setCentralWidget(self._video_widget)

        # 비디오 플레이어 (무음으로 재생)
        self._video_player = QMediaPlayer(self)
        self._video_player.setVideoOutput(self._video_widget)
        self._video_player.setMuted(True)
        self._video_player.setMedia(QMediaContent(QUrl.fromLocalFile(video_path)))
        self._video_player.mediaStatusChanged.connect(self._on_video_status_changed)

        # BGM 플레이어 (별도 오디오만)
        self._bgm_player = None
        if bgm_path and os.path.isfile(bgm_path):
            self._bgm_player = QMediaPlayer(self)
            self._bgm_player.setMedia(QMediaContent(QUrl.fromLocalFile(bgm_path)))
            self._bgm_player.mediaStatusChanged.connect(self._on_bgm_status_changed)

        # 시작 시 전체 화면
        self.showFullScreen()
        self._video_player.play()
        if self._bgm_player:
            self._bgm_player.play()

    def _on_video_status_changed(self, status: QMediaPlayer.MediaStatus):
        if status == QMediaPlayer.EndOfMedia:
            self._video_player.setPosition(0)
            self._video_player.play()

    def _on_bgm_status_changed(self, status: QMediaPlayer.MediaStatus):
        if status == QMediaPlayer.EndOfMedia and self._bgm_player:
            self._bgm_player.setPosition(0)
            self._bgm_player.play()

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        if key == Qt.Key_Escape:
            self.close()
            return
        if key == Qt.Key_F:
            if self._is_fullscreen:
                self.showNormal()
                self._is_fullscreen = False
            else:
                self.showFullScreen()
                self._is_fullscreen = True
            return
        super().keyPressEvent(event)


def main():
    video_file = os.path.join(MEDIA_FOLDER, "sample.mp4")
    bgm_file = os.path.join(MEDIA_FOLDER, "bgm.mp3")

    if not os.path.isfile(video_file):
        print(f"영상 파일이 없습니다: {video_file}")
        print(f"다음 폴더에 sample.mp4 를 넣어주세요: {MEDIA_FOLDER}")
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setApplicationName("Eternal Beam - 시연 재생기")
    win = DemoPlayerWindow(video_file, bgm_file if os.path.isfile(bgm_file) else None)
    win.setWindowTitle("Eternal Beam")
    win.show()

    if not os.path.isfile(bgm_file):
        print(f"(참고) BGM 파일이 없어 음악은 재생되지 않습니다: {bgm_file}")

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
