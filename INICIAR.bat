@echo off
title OdontoScan 3D
echo =====================================
echo   OdontoScan 3D - Iniciando...
echo =====================================
pip install -r requirements.txt --quiet
echo.
echo Servidor iniciado!
echo Acesse: http://localhost:5050
echo Celular: http://SEU_IP:5050
echo.
echo NAO feche esta janela!
echo =====================================
python app.py
pause
