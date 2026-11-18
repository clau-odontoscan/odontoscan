# OdontoScan 3D

Escaneamento 3D odontológico por fotogrametria (câmera comum / celular) — módulo que será integrado ao OdontoCentro.

## Como funciona
1. **Captura** — o navegador tira um frame a cada ~0,45 s e já envia para o servidor durante a captura (o celular não acumula fotos na memória). O servidor reduz para no máx. 1600 px, descarta frames sem textura e devolve a qualidade (nitidez + pontos).
2. **COLMAP (SfM)** — `feature_extractor` → `sequential_matcher` (frames em sequência, sem o custo do exaustivo) → `mapper`. Usa o **maior** modelo gerado. As opções de linha de comando são detectadas automaticamente (os nomes mudam entre versões do COLMAP).
3. **Filtragem** — remove pontos com erro de reprojeção alto e outliers estatísticos.
4. **Escala real** — se o marcador ArUco impresso aparecer em 3+ frames, os cantos são triangulados e o modelo passa a ficar **em milímetros**.
5. **Malha** — Poisson (Open3D) em processo separado, com threads limitadas; remove a mesa, recorta o excesso e mantém só o maior pedaço. Gera `model.stl` (laboratório) e `mesh.ply` (visualizador, com cor).
6. **Odontograma** — salvo no servidor por paciente (SQLite).

## Marcador de escala
- Botão **🖨 Imprimir** no painel → PNG a 300 dpi. Imprimir em **100% / tamanho real**.
- Meça o quadrado preto com uma régua e digite a medida real no campo **mm** (o padrão é 20 mm).
- Coloque o marcador plano, ao lado do modelo, e deixe-o aparecer em boa parte da volta.

## Dicas de captura
- Luz difusa e uniforme, sem reflexo.
- Gesso liso tem pouca textura: risque levemente com lápis ou use spray de escaneamento.
- Uma volta completa devagar a 15–25 cm + uma volta mais alta mostrando as oclusais. 40–100 frames.

## Limitações (importante)
- A reconstrução é **esparsa** (SfM). A reconstrução densa do COLMAP exige GPU NVIDIA/CUDA, que o Railway não tem. A malha serve para **apresentação, documentação e planejamento**; para **fabricação** (coroa, prótese) a precisão não substitui um scanner intraoral/de bancada.
- Sem login por enquanto: a proteção (LGPD) virá da integração com o OdontoCentro.

## Deploy no Railway
1. Conecte o repositório — o build usa o `Dockerfile` (Ubuntu 24.04 + COLMAP 3.9).
2. **Crie um Volume montado em `/data`** — sem volume, scans e odontogramas somem a cada deploy.
3. Variáveis opcionais: `SECRET_KEY`, `MAX_THREADS` (padrão: até 4), `MAX_FRAMES` (180), `MAX_IMAGE_SIDE` (1600), `MAX_FEATURES` (4096), `MATCH_OVERLAP` (12), `MESH_TIMEOUT` (900 s).
4. Diagnóstico: `GET /api/health` mostra a versão do COLMAP encontrada.

## Uso local (Windows)
```
INICIAR.bat
```
Acesse http://localhost:5050. Os dados ficam em `Desktop\odontoscan_work`.
O COLMAP é procurado no PATH, em `bin\colmap.exe` ao lado do app, ou em `Desktop\bin\colmap.exe`. Se estiver em outro lugar, defina `COLMAP_PATH`.
Observação: o navegador só libera a câmera em `https://` ou `localhost` — no celular, use o endereço do Railway.

## API
| Método | Rota | |
|---|---|---|
| POST | `/api/session/new` | `{name, patient, marker_mm}` |
| POST | `/api/frame/upload` | multipart `session_id`, `frame` |
| POST | `/api/scan/process` | `{session_id}` |
| GET | `/api/scan/status/<id>` · `/api/scan/result/<id>` | |
| GET | `/api/scan/stl/<id>` · `/api/scan/mesh/<id>` | STL / PLY |
| GET | `/api/scans/list?patient=` | |
| DELETE | `/api/scan/<id>` | |
| GET/PUT | `/api/odontograma/<paciente>` | |
| GET | `/api/marker.png?mm=20` | marcador para imprimir |
| GET | `/api/health` | |
