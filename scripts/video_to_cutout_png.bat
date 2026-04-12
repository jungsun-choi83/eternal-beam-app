@echo off
chcp 65001 >nul
cd /d "%~dp0.."

REM slot1_idle.mp4 + slot1_idle_alpha.mp4 -> cutout.png (1프레임)
set RGB=python\composited\slot1_idle.mp4
set ALPHA=python\composited\slot1_idle_alpha.mp4

if not exist "%RGB%" (
    echo %RGB% 없음
    pause
    exit /b 1
)
if not exist "%ALPHA%" (
    echo %ALPHA% 없음
    pause
    exit /b 1
)

echo RGB+Alpha -> cutout.png 추출 중...
ffmpeg -y -i "%RGB%" -i "%ALPHA%" -filter_complex "[0:v]format=rgba[rgb];[1:v]format=gray[alpha];[rgb][alpha]alphamerge[out]" -map "[out]" -vframes 1 cutout.png 2>nul

if exist "cutout.png" (
    echo OK: cutout.png 생성됨
    echo 이제 compose_subject_only.bat 실행
) else (
    echo ffmpeg 실패. ffmpeg 설치 확인.
)
pause
