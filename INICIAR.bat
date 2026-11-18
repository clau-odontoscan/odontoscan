@echo off
title OdontoScan 3D
cd /d "%~dp0"
echo =====================================
echo   OdontoScan 3D - Iniciando...
echo =====================================
echo Instalando bibliotecas basicas...
python -m pip install flask flask-socketio simple-websocket numpy "opencv-python-headless>=4.8,<5" scipy Pillow --quiet
echo Instalando Open3D (malha/STL)...
python -m pip install open3d --quiet
if errorlevel 1 (
  echo.
  echo  AVISO: Open3D nao instalou. O sistema funciona, mas sem malha/STL.
  echo  Verifique sua versao do Python com: python --version
  echo.
)
REM Se o COLMAP nao estiver no PATH, descomente e ajuste a linha abaixo:
REM set COLMAP_PATH=C:\caminho\para\colmap.exe
echo.
echo Acesse: http://localhost:5050
echo Celular: use o link https do cloudflared (camera exige https)
echo.
echo NAO feche esta janela!
echo =====================================
python app.py
pause
