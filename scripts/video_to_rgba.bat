@echo off
chcp 65001 >nul
cd /d "%~dp0.."

REM Runway 영상 → RGBA PNG 시퀀스 / RGBA MP4
REM 사용: video_to_rgba.bat input.mp4
REM       video_to_rgba.bat input.mp4 --video
REM       video_to_rgba.bat input.mp4 -o ./output --max-frames 30

set INPUT=%~1
if "%INPUT%"=="" (
    echo 사용법: video_to_rgba.bat ^<input.mp4^> [옵션]
    echo   --video        RGBA MP4 한 파일로 출력
    echo   -o ./output     출력 디렉토리
    echo   --max-frames N  테스트용 N프레임만
    pause
    exit /b 1
)

shift
python scripts/video_to_rgba.py %INPUT% %*
pause
