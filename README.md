# OdontoScan 3D

Sistema de escaneamento 3D odontológico por câmera.

## Tecnologias
- **COLMAP** — Structure from Motion (reconstrução 3D)
- **Flask + Socket.IO** — Backend Python com WebSocket
- **Three.js** — Visualização 3D no navegador
- **OpenCV** — Processamento de imagem
- **Trimesh + SciPy** — Geração de malha 3D

## Funcionalidades
- 📷 Captura via webcam ou câmera intraoral
- 📱 Funciona no celular via Wi-Fi
- 🦷 Odontograma 2D interativo
- 👁️ Modo apresentação ao paciente
- ⚙️ Reconstrução 3D com COLMAP
- 📤 Exporta STL para laboratório

## Deploy no Railway
1. Fork este repositório
2. Conecte ao Railway
3. Deploy automático via Dockerfile

## Uso local (Windows)
```
pip install -r requirements.txt
python app.py
```
Acesse: http://localhost:5050

## Variáveis de ambiente
- `PORT` — Porta do servidor (padrão: 5050)
- `SECRET_KEY` — Chave secreta Flask
