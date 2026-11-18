@echo off
title Subir OdontoScan para o GitHub
cd /d "%~dp0"
git add -A
git commit -m "OdontoScan: pipeline COLMAP corrigido, malha Poisson, escala por marcador, historico e odontograma salvos"
git push origin master:main
echo.
echo Pronto! Confira acima se apareceu algum erro.
pause
