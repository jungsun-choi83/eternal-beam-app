@echo off
chcp 65001 >nul
echo === action11 비디오를 EternalBeam에 복사합니다 ===
echo.
echo [주의] Unity를 먼저 종료해 주세요!
pause

set SRC=C:\Users\choi jungsun\Desktop\eternal-beam-app\python\output
set DST=C:\Users\choi jungsun\EternalBeam\Assets\Videos

copy /Y "%SRC%\action11_h264.mp4" "%DST%\action11.mp4"
copy /Y "%SRC%\action11_alpha_h264.mp4" "%DST%\action11_alpha.mp4"

echo.
echo 복사 완료!
echo Unity를 열고 Play를 눌러 확인하세요.
pause
