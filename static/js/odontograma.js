// Odontograma 2D profissional — Canvas 2D
// Dentes desenhados com SVG paths reais por tipo

const ODO = {
  canvas: null, ctx: null,
  selected: null, hovered: null,
  teeth: {},
  treatColors: {
    carie:'#e05555', coroa:'#e8bc4e', implante:'#4a8fff',
    canal:'#a855f7', resina:'#00d4b8', limpeza:'#f0c040', extraido:'#666666'
  },

  UPPER: [18,17,16,15,14,13,12,11, 21,22,23,24,25,26,27,28],
  LOWER: [48,47,46,45,44,43,42,41, 31,32,33,34,35,36,37,38],

  init(canvasId) {
    this.canvas = document.getElementById(canvasId);
    this.ctx = this.canvas.getContext('2d');
    this.resize();
    this.buildTeeth();
    this.bindEvents();
    this.draw();
    window.addEventListener('resize', () => { this.resize(); this.buildTeeth(); this.draw(); });
  },

  resize() {
    const cont = this.canvas.parentElement;
    this.canvas.width  = cont.clientWidth  || 800;
    this.canvas.height = cont.clientHeight || 400;
  },

  // Gera posições dos dentes em grade limpa
  buildTeeth() {
    const W = this.canvas.width;
    const H = this.canvas.height;
    const cx = W / 2;

    // Espaçamento
    const gap = 2;
    // Larguras por tipo: siso=molar, molar, pm, canino, lateral, central
    const widths = [32,34,30,26,22,24, 24,22,26,30,34,32,30,28,26,24];
    // Superior: 18..11 | 21..28
    // Inferior: 48..41 | 31..38

    // Calcula posição X de cada dente (superior)
    const toothH = Math.min(70, H * 0.30); // altura do dente
    const rootH  = Math.min(50, H * 0.22); // altura da raiz
    const archGap = Math.min(22, H * 0.08); // espaço entre arcadas

    const midY  = H / 2;
    const upperCoroaY = midY - archGap - toothH;
    const upperRootY  = upperCoroaY - rootH;
    const lowerCoroaY = midY + archGap;
    const lowerRootY  = lowerCoroaY + toothH;

    // Calcula X acumulado a partir do centro
    // Superior direita (11→18): vai para a esquerda do centro
    // Superior esquerda (21→28): vai para a direita

    let xRight = cx - gap/2;
    let xLeft  = cx + gap/2;

    // Arcada superior
    for(let i=0; i<8; i++){
      const numR = this.UPPER[7-i]; // 11,12,...18 da direita p/ centro
      const numL = this.UPPER[8+i]; // 21,22,...28 da esq p/ direita
      const wR = widths[7-i];
      const wL = widths[8+i];

      xRight -= wR + gap;
      this.teeth[numR] = {
        num: numR, type: this._toothType(numR), isUpper: true,
        x: xRight, w: wR, cy: upperCoroaY, toothH, rootH,
        treat: null, faces: [], notes: ''
      };

      this.teeth[numL] = {
        num: numL, type: this._toothType(numL), isUpper: true,
        x: xLeft, w: wL, cy: upperCoroaY, toothH, rootH,
        treat: null, faces: [], notes: ''
      };
      xLeft += wL + gap;
    }

    // Arcada inferior — mesma lógica
    xRight = cx - gap/2;
    xLeft  = cx + gap/2;
    for(let i=0; i<8; i++){
      const numR = this.LOWER[7-i]; // 41,42,...48
      const numL = this.LOWER[8+i]; // 31,32,...38
      const wR = widths[7-i];
      const wL = widths[8+i];

      xRight -= wR + gap;
      this.teeth[numR] = {
        num: numR, type: this._toothType(numR), isUpper: false,
        x: xRight, w: wR, cy: lowerCoroaY, toothH, rootH,
        treat: null, faces: [], notes: ''
      };

      this.teeth[numL] = {
        num: numL, type: this._toothType(numL), isUpper: false,
        x: xLeft, w: wL, cy: lowerCoroaY, toothH, rootH,
        treat: null, faces: [], notes: ''
      };
      xLeft += wL + gap;
    }
  },

  _toothType(num) {
    const d = num % 10;
    if(d===1||d===2) return 'incisor';
    if(d===3)        return 'canine';
    if(d===4||d===5) return 'premolar';
    return 'molar';
  },

  draw() {
    const ctx = this.ctx;
    const W = this.canvas.width, H = this.canvas.height;
    ctx.clearRect(0,0,W,H);

    // Fundo
    ctx.fillStyle = '#07090f';
    ctx.fillRect(0,0,W,H);

    // Linha de oclusão
    const midY = H/2;
    ctx.strokeStyle = 'rgba(0,212,184,0.15)';
    ctx.lineWidth = 1;
    ctx.setLineDash([6,4]);
    ctx.beginPath(); ctx.moveTo(20,midY); ctx.lineTo(W-20,midY); ctx.stroke();
    ctx.setLineDash([]);

    // Label arcadas
    ctx.fillStyle = 'rgba(90,101,128,0.8)';
    ctx.font = '600 10px Inter,sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText('SUPERIOR', 12, midY - 20);
    ctx.fillText('INFERIOR', 12, midY + 30);

    // Linha central
    ctx.strokeStyle = 'rgba(0,212,184,0.3)';
    ctx.lineWidth = 1.5;
    ctx.setLineDash([4,3]);
    ctx.beginPath(); ctx.moveTo(W/2, 10); ctx.lineTo(W/2, H-10); ctx.stroke();
    ctx.setLineDash([]);

    // Desenha todos os dentes
    Object.values(this.teeth).forEach(t => this.drawTooth(t));
  },

  drawTooth(t) {
    const ctx = this.ctx;
    const isSelected = t.num === this.selected;
    const isHovered  = t.num === this.hovered;
    const treatColor = t.treat ? this.treatColors[t.treat] : null;

    // Cores
    const crownFill   = treatColor || (isSelected ? '#1a3050' : isHovered ? '#162040' : '#111826');
    const crownStroke = treatColor || (isSelected ? '#00d4b8' : isHovered ? '#4488ff' : '#2a3a55');
    const rootFill    = '#0c1018';
    const rootStroke  = '#1a2535';
    const sw = isSelected ? 2 : 1.5;

    const { x, w, cy, toothH, rootH, isUpper, type } = t;

    // ── RAIZ ──
    ctx.beginPath();
    if(isUpper) {
      // Raiz vai para cima
      if(type === 'molar') {
        // 2 raízes
        const r1x = x + w*0.28, r2x = x + w*0.72;
        const rw2 = w*0.22;
        ctx.moveTo(r1x - rw2/2, cy);
        ctx.lineTo(r1x - rw2/4, cy - rootH);
        ctx.lineTo(r1x + rw2/4, cy - rootH);
        ctx.lineTo(r1x + rw2/2, cy);
        ctx.moveTo(r2x - rw2/2, cy);
        ctx.lineTo(r2x - rw2/4, cy - rootH);
        ctx.lineTo(r2x + rw2/4, cy - rootH);
        ctx.lineTo(r2x + rw2/2, cy);
      } else {
        // 1 raiz
        const rw = w * (type==='canine' ? 0.32 : 0.28);
        const rx = x + w/2;
        ctx.moveTo(rx - rw/2, cy);
        ctx.bezierCurveTo(rx - rw/2, cy - rootH*0.5, rx - rw*0.2, cy - rootH, rx, cy - rootH);
        ctx.bezierCurveTo(rx + rw*0.2, cy - rootH, rx + rw/2, cy - rootH*0.5, rx + rw/2, cy);
      }
    } else {
      // Raiz vai para baixo
      const rootBottom = cy + toothH + rootH;
      if(type === 'molar') {
        const r1x = x + w*0.28, r2x = x + w*0.72;
        const rw2 = w*0.22;
        ctx.moveTo(r1x - rw2/2, cy + toothH);
        ctx.lineTo(r1x - rw2/4, rootBottom);
        ctx.lineTo(r1x + rw2/4, rootBottom);
        ctx.lineTo(r1x + rw2/2, cy + toothH);
        ctx.moveTo(r2x - rw2/2, cy + toothH);
        ctx.lineTo(r2x - rw2/4, rootBottom);
        ctx.lineTo(r2x + rw2/4, rootBottom);
        ctx.lineTo(r2x + rw2/2, cy + toothH);
      } else {
        const rw = w * (type==='canine' ? 0.32 : 0.28);
        const rx = x + w/2;
        ctx.moveTo(rx - rw/2, cy + toothH);
        ctx.bezierCurveTo(rx - rw/2, cy + toothH + rootH*0.5, rx - rw*0.2, rootBottom, rx, rootBottom);
        ctx.bezierCurveTo(rx + rw*0.2, rootBottom, rx + rw/2, cy + toothH + rootH*0.5, rx + rw/2, cy + toothH);
      }
    }
    ctx.fillStyle = rootFill;
    ctx.strokeStyle = rootStroke;
    ctx.lineWidth = 1;
    ctx.fill();
    ctx.stroke();

    // ── COROA ──
    const r = Math.min(w*0.3, toothH*0.25); // raio de arredondamento
    this._roundRect(ctx, x, cy, w, toothH, r);
    ctx.fillStyle = crownFill;
    ctx.fill();
    ctx.strokeStyle = crownStroke;
    ctx.lineWidth = sw;
    ctx.stroke();

    // ── DETALHES INTERNOS ──
    if(type === 'molar' || type === 'premolar') {
      // Cruz oclusal
      ctx.strokeStyle = treatColor ? 'rgba(255,255,255,0.3)' : 'rgba(0,212,184,0.25)';
      ctx.lineWidth = 0.8;
      ctx.beginPath();
      ctx.moveTo(x + w/2, cy + toothH*0.2);
      ctx.lineTo(x + w/2, cy + toothH*0.8);
      ctx.moveTo(x + w*0.2, cy + toothH/2);
      ctx.lineTo(x + w*0.8, cy + toothH/2);
      ctx.stroke();
    }
    if(type === 'canine') {
      // Cúspide
      ctx.strokeStyle = treatColor ? 'rgba(255,255,255,0.3)' : 'rgba(0,212,184,0.2)';
      ctx.lineWidth = 0.8;
      ctx.beginPath();
      const tipY = isUpper ? cy + toothH*0.15 : cy + toothH*0.85;
      ctx.moveTo(x + w*0.2, tipY);
      ctx.lineTo(x + w/2, isUpper ? cy + toothH*0.35 : cy + toothH*0.65);
      ctx.lineTo(x + w*0.8, tipY);
      ctx.stroke();
    }

    // ── HIGHLIGHT selecionado ──
    if(isSelected) {
      ctx.strokeStyle = '#00d4b8';
      ctx.lineWidth = 2.5;
      ctx.shadowColor = '#00d4b8';
      ctx.shadowBlur = 8;
      this._roundRect(ctx, x, cy, w, toothH, r);
      ctx.stroke();
      ctx.shadowBlur = 0;
    }

    // ── NÚMERO ──
    ctx.fillStyle = isSelected ? '#00d4b8' : (isHovered ? '#8090b0' : '#3a4a65');
    ctx.font = `${isSelected?'700':'500'} ${Math.max(8,w*0.3)}px Inter,sans-serif`;
    ctx.textAlign = 'center';
    const labelY = isUpper
      ? cy + toothH + 12
      : cy - 4;
    ctx.fillText(t.num, x + w/2, labelY);

    // ── PONTO de tratamento ──
    if(t.treat) {
      const dotX = x + w/2;
      const dotY = isUpper ? cy - 8 : cy + toothH + 8;
      ctx.beginPath();
      ctx.arc(dotX, dotY, 4, 0, Math.PI*2);
      ctx.fillStyle = this.treatColors[t.treat];
      ctx.fill();
    }
  },

  _roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r);
    ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  },

  getToothAt(mx, my) {
    for(const t of Object.values(this.teeth)) {
      if(mx >= t.x && mx <= t.x + t.w && my >= t.cy && my <= t.cy + t.toothH)
        return t.num;
    }
    return null;
  },

  bindEvents() {
    this.canvas.addEventListener('click', e => {
      const r = this.canvas.getBoundingClientRect();
      const num = this.getToothAt(e.clientX - r.left, e.clientY - r.top);
      if(num) { this.selected = num; this.draw(); this.onSelect && this.onSelect(num); }
    });
    this.canvas.addEventListener('mousemove', e => {
      const r = this.canvas.getBoundingClientRect();
      const num = this.getToothAt(e.clientX - r.left, e.clientY - r.top);
      if(num !== this.hovered) { this.hovered = num; this.draw(); this.canvas.style.cursor = num ? 'pointer' : 'default'; }
    });
    this.canvas.addEventListener('touchend', e => {
      const r = this.canvas.getBoundingClientRect();
      const t = e.changedTouches[0];
      const num = this.getToothAt(t.clientX - r.left, t.clientY - r.top);
      if(num) { this.selected = num; this.draw(); this.onSelect && this.onSelect(num); }
      e.preventDefault();
    }, {passive:false});
  },

  setTreat(num, treat) {
    if(this.teeth[num]) { this.teeth[num].treat = treat; this.draw(); }
  },

  setFaces(num, faces) {
    if(this.teeth[num]) { this.teeth[num].faces = faces; }
  }
};
