const pptxgen = require('pptxgenjs');

const pptx = new pptxgen();
pptx.layout = 'LAYOUT_WIDE';
pptx.author = 'HeatSafe AI Ops';
pptx.company = 'HeatSafe';
pptx.subject = 'Cấu trúc, vận hành và hạ tầng GCP của HeatSafe AI Ops';
pptx.title = 'HeatSafe AI Ops — Kiến trúc vận hành & GCP';
pptx.lang = 'vi-VN';
pptx.theme = {
  headFontFace: 'Aptos Display',
  bodyFontFace: 'Aptos',
  lang: 'vi-VN',
};
pptx.defineLayout({ name: 'WIDE', width: 13.333, height: 7.5 });
pptx.layout = 'WIDE';
pptx.defineSlideMaster({
  title: 'HEATSAFE',
  background: { color: 'F6F8FC' },
  objects: [
    { rect: { x: 0, y: 0, w: 13.333, h: 0.11, fill: { color: 'F05A3C' }, line: { color: 'F05A3C' } } },
    { text: { text: 'HEATSAFE  /  AI OPS', options: { x: 0.55, y: 7.08, w: 2.7, h: 0.18, fontFace: 'Aptos', fontSize: 7.5, bold: true, color: '5D6B82', margin: 0, breakLine: false } } },
    { text: { text: 'Kiến trúc vận hành & hạ tầng GCP', options: { x: 9.0, y: 7.08, w: 3.75, h: 0.18, fontFace: 'Aptos', fontSize: 7.5, color: '5D6B82', align: 'right', margin: 0, breakLine: false } } },
  ],
  slideNumber: { x: 12.85, y: 7.04, color: '5D6B82', fontFace: 'Aptos', fontSize: 8 },
});

const C = {
  navy: '102A43', ink: '243B53', muted: '627D98', pale: 'EAF1F8', sky: 'D9EAF7', blue: '1976D2', teal: '00A6A6', green: '1F9D72', orange: 'F59E0B', coral: 'F05A3C', red: 'D64545', white: 'FFFFFF', dark: '0B1F33', cream: 'FFF8EF', line: 'C7D5E4',
};
const S = { title: 25, subtitle: 12.5, body: 13, small: 9.5, label: 9 };
const SH = pptx.ShapeType;

function addText(slide, text, x, y, w, h, opts = {}) {
  slide.addText(text, {
    x, y, w, h, margin: opts.margin ?? 0,
    fontFace: opts.fontFace || 'Aptos', fontSize: opts.fontSize || S.body,
    color: opts.color || C.ink, bold: opts.bold || false,
    breakLine: false, valign: opts.valign || 'mid',
    align: opts.align || 'left', fit: 'shrink',
    paraSpaceAfterPt: opts.paraSpaceAfterPt ?? 0,
    bullet: opts.bullet,
    italic: opts.italic || false,
  });
}
function rect(slide, x, y, w, h, fill, radius = 0.12, line = fill) {
  slide.addShape(radius ? SH.roundRect : SH.rect, { x, y, w, h, rectRadius: radius, fill: { color: fill }, line: { color: line, transparency: line === fill ? 100 : 0, width: 0.5 } });
}
function line(slide, x1, y1, x2, y2, color = C.line, width = 1.1, endArrow = 'none') {
  slide.addShape(SH.line, { x: x1, y: y1, w: x2 - x1, h: y2 - y1, line: { color, width, beginArrowType: 'none', endArrowType: endArrow } });
}
function title(slide, kicker, heading, sub) {
  addText(slide, kicker.toUpperCase(), 0.62, 0.47, 3.6, 0.22, { fontSize: S.label, bold: true, color: C.coral, charSpacing: 1.3 });
  addText(slide, heading, 0.62, 0.78, 12.1, 0.47, { fontSize: S.title, bold: true, color: C.navy });
  if (sub) addText(slide, sub, 0.63, 1.31, 12.0, 0.28, { fontSize: S.subtitle, color: C.muted });
}
function pill(slide, text, x, y, w, fill, color = C.white) {
  rect(slide, x, y, w, 0.28, fill, 0.14);
  addText(slide, text, x + 0.08, y + 0.015, w - 0.16, 0.22, { fontSize: 8.3, bold: true, color, align: 'center' });
}
function card(slide, x, y, w, h, headline, text, accent = C.blue, icon = '') {
  rect(slide, x, y, w, h, C.white, 0.12, C.line);
  rect(slide, x, y, 0.07, h, accent, 0, accent);
  if (icon) {
    rect(slide, x + 0.25, y + 0.22, 0.42, 0.42, accent, 0.1);
    addText(slide, icon, x + 0.25, y + 0.23, 0.42, 0.34, { fontSize: 13, bold: true, color: C.white, align: 'center' });
  }
  addText(slide, headline, x + 0.25 + (icon ? 0.57 : 0), y + 0.2, w - 0.45 - (icon ? 0.57 : 0), 0.27, { fontSize: 13, bold: true, color: C.navy });
  addText(slide, text, x + 0.25, y + (icon ? 0.76 : 0.61), w - 0.48, h - (icon ? 0.95 : 0.76), { fontSize: 10.2, color: C.muted, valign: 'top' });
}
function node(slide, x, y, w, h, label, caption, fill, accent = C.white) {
  rect(slide, x, y, w, h, fill, 0.13);
  addText(slide, label, x + 0.12, y + 0.13, w - 0.24, 0.25, { fontSize: 11, bold: true, color: accent, align: 'center' });
  if (caption) addText(slide, caption, x + 0.13, y + 0.45, w - 0.26, h - 0.54, { fontSize: 8.3, color: accent, align: 'center', valign: 'top' });
}
function note(slide, text, x, y, w, color = C.muted) {
  addText(slide, text, x, y, w, 0.26, { fontSize: 8.4, color, italic: true, valign: 'top' });
}

// Slide 1
{
  const s = pptx.addSlide('HEATSAFE');
  s.background = { color: C.dark };
  s.addShape(SH.rect, { x: 0, y: 0, w: 13.333, h: 0.11, fill: { color: C.coral }, line: { color: C.coral } });
  for (let i = 0; i < 7; i++) {
    s.addShape(SH.arc, { x: 8.2 + i * 0.27, y: 0.8 + i * 0.3, w: 4.5 - i * 0.35, h: 4.5 - i * 0.35, adjustPoint: 0.25, line: { color: i % 2 ? '1A3C56' : '204B68', transparency: 20, width: 1.1 }, fill: { color: C.dark, transparency: 100 }, rotate: 20 });
  }
  pill(s, 'HACKATHON DEMO  •  GCP-NATIVE', 0.65, 1.06, 2.65, C.coral);
  addText(s, 'HeatSafe AI Ops', 0.65, 1.72, 7.6, 0.72, { fontSize: 37, bold: true, color: C.white });
  addText(s, 'Kiến trúc vận hành, tối ưu quyết định\nvà cách ứng dụng hạ tầng Google Cloud', 0.67, 2.6, 6.35, 0.85, { fontSize: 18, color: 'C8D8E8', valign: 'top' });
  line(s, 0.67, 3.72, 3.15, 3.72, C.coral, 2.5);
  addText(s, 'Bảo vệ tài xế trong nắng nóng cực đoan\ntrong khi vẫn giữ chi phí, fulfillment và ETA trong ngưỡng vận hành.', 0.67, 4.04, 5.75, 0.64, { fontSize: 12.5, color: C.white, valign: 'top' });
  rect(s, 0.65, 5.44, 5.95, 0.96, '12304A', 0.12);
  addText(s, 'Từ dữ liệu → dự báo → đề xuất SafePause →\nquyết định có ràng buộc → audit có thể truy vết', 0.92, 5.63, 5.42, 0.49, { fontSize: 12, bold: true, color: C.white });
  addText(s, 'Bản trình bày kỹ thuật  |  26.07.2026', 0.67, 6.72, 5.4, 0.2, { fontSize: 8.5, color: '93AEC4' });
  addText(s, 'HEATSAFE / AI OPS', 10.3, 6.72, 2.35, 0.2, { fontSize: 8.5, color: '93AEC4', bold: true, align: 'right' });
}

// Slide 2
{
  const s = pptx.addSlide('HEATSAFE');
  title(s, '01 / Bài toán', 'Tối ưu “an toàn trước, dịch vụ vững”', 'HeatSafe biến rủi ro nắng nóng thành quyết định vận hành có thể kiểm chứng.');
  card(s, 0.65, 2.0, 3.86, 2.15, 'Rủi ro phi tuyến', 'Nhiệt độ, độ ẩm, thời gian phơi nhiễm và cường độ công việc có thể khiến rủi ro tăng nhanh ở từng tài xế.', C.coral, '↑');
  card(s, 4.74, 2.0, 3.86, 2.15, 'Ràng buộc kinh doanh', 'Cho nghỉ đồng loạt có thể giảm nguồn cung, tăng thời gian chờ và làm giảm fulfillment.', C.orange, '≋');
  card(s, 8.83, 2.0, 3.86, 2.15, 'Khoảng trống quyết định', 'Điều phối viên cần biết ai cần nghỉ, nghỉ khi nào, trong bao lâu — và tác động tới dịch vụ là bao nhiêu.', C.blue, '?');
  rect(s, 0.65, 4.67, 12.04, 1.47, C.navy, 0.15);
  addText(s, 'Nguyên tắc thiết kế', 0.95, 4.96, 2.4, 0.25, { fontSize: 12, bold: true, color: C.white });
  line(s, 3.37, 4.94, 3.37, 5.75, '4D6C86', 1);
  addText(s, 'Không tối ưu chỉ theo điểm rủi ro.\nMọi đề xuất phải đồng thời qua “cổng” an toàn, chi phí và SLA.', 3.7, 4.89, 4.25, 0.56, { fontSize: 13, bold: true, color: C.white, valign: 'top' });
  pill(s, 'SAFETY', 8.54, 5.02, 1.08, C.coral);
  pill(s, 'COST', 9.82, 5.02, 0.9, C.orange, C.dark);
  pill(s, 'SLA', 10.94, 5.02, 0.72, C.teal);
  note(s, 'Lưu ý: Heat Index là chỉ báo sàng lọc vận hành, không phải chẩn đoán y khoa.', 0.66, 6.42, 10.6);
}

// Slide 3
{
  const s = pptx.addSlide('HEATSAFE');
  title(s, '02 / Nhìn tổng thể', 'Một ứng dụng, hai runtime phù hợp hai mục đích', 'Cùng UI và logic SafePause, nhưng tách rõ mode cloud vận hành và mode demo stateful.');
  rect(s, 0.68, 1.92, 5.86, 4.48, C.white, 0.15, C.line);
  pill(s, 'CURRENT / CLOUD-BACKED', 0.96, 2.19, 2.28, C.blue);
  addText(s, 'Quan sát & hỗ trợ quyết định', 0.96, 2.65, 4.95, 0.3, { fontSize: 16, bold: true, color: C.navy });
  const left = [
    ['1', 'Đọc snapshot mới nhất', 'BigQuery + kiểm tra freshness'],
    ['2', 'Lấy dự báo và điểm rủi ro', 'TimesFM + BigQuery ML'],
    ['3', 'Tạo đề xuất có guardrail', 'SafePause optimizer'],
    ['4', 'Ghi audit mô phỏng', 'Không dispatch thực tế'],
  ];
  left.forEach((it, i) => { const y = 3.18 + i * 0.66; rect(s, 0.98, y, 0.34, 0.34, C.sky, 0.17); addText(s, it[0], 0.98, y + 0.03, 0.34, 0.22, { fontSize: 9, bold: true, color: C.blue, align: 'center' }); addText(s, it[1], 1.5, y - 0.005, 3.8, 0.2, { fontSize: 11, bold: true, color: C.ink }); addText(s, it[2], 1.5, y + 0.22, 4.1, 0.18, { fontSize: 8.8, color: C.muted }); });
  rect(s, 6.79, 1.92, 5.86, 4.48, 'F0FAF8', 0.15, 'B8E4D8');
  pill(s, 'ACCELERATED PRODUCTION', 7.07, 2.19, 2.5, C.green);
  addText(s, 'Demo vòng đời có trạng thái', 7.07, 2.65, 4.95, 0.3, { fontSize: 16, bold: true, color: C.navy });
  const right = [
    ['K−8 → K', 'Warm checkpoint; engine tiến theo tick 15 phút'],
    ['Tại K', 'Người dùng chọn Activate hoặc Continue'],
    ['K+1 → K+8', 'Actual và shadow baseline chạy song song'],
    ['Kết quả', 'Hiện lifecycle, service delta và audit lineage'],
  ];
  right.forEach((it, i) => { const y = 3.18 + i * 0.66; rect(s, 7.09, y, 0.34, 0.34, 'D7F3EB', 0.17); addText(s, String(i + 1), 7.09, y + 0.03, 0.34, 0.22, { fontSize: 9, bold: true, color: C.green, align: 'center' }); addText(s, it[0], 7.6, y - 0.005, 4.3, 0.2, { fontSize: 11, bold: true, color: C.ink }); addText(s, it[1], 7.6, y + 0.22, 4.3, 0.18, { fontSize: 8.8, color: C.muted }); });
  note(s, 'Mode accelerated dùng engine deterministic tại tiến trình Streamlit để trình diễn hành vi; nó không gọi provider ở mỗi tick.', 0.68, 6.55, 11.7);
}

// Slide 4
{
  const s = pptx.addSlide('HEATSAFE');
  title(s, '03 / Luồng dữ liệu & quyết định', 'Từ tín hiệu vận hành đến đề xuất SafePause', 'Mỗi bước đều gắn theo snapshot, run và model version để kết quả có thể giải thích và truy vết.');
  node(s, 0.6, 2.06, 2.02, 0.78, 'TÍN HIỆU', 'Open-Meteo\n+ fleet simulation', C.blue);
  node(s, 0.6, 3.35, 2.02, 0.78, 'RAW & LINEAGE', 'Cloud Storage\nraw_gcs_uri', C.teal);
  node(s, 3.08, 2.06, 2.28, 0.78, 'SYSTEM OF RECORD', 'BigQuery\nsnapshot + history', C.navy);
  node(s, 3.08, 3.35, 2.28, 0.78, 'AI OUTPUTS', 'features + prediction\n+ forecast', C.navy);
  node(s, 5.84, 2.06, 2.3, 0.78, 'DỰ BÁO CẦU', 'TimesFM\nAI.FORECAST', C.orange, C.dark);
  node(s, 5.84, 3.35, 2.3, 0.78, 'RỦI RO TÀI XẾ', 'Boosted Tree\nBigQuery ML', C.coral);
  node(s, 8.62, 2.69, 2.28, 0.9, 'SAFEPAUSE', 'Counterfactual scoring\n+ constrained optimizer', C.green);
  node(s, 11.34, 2.06, 1.38, 0.78, 'UI OPS', 'Cloud Run\nStreamlit', C.blue);
  node(s, 11.34, 3.35, 1.38, 0.78, 'AUDIT', 'BigQuery\nproposal/event', C.teal);
  line(s, 2.62, 2.45, 3.08, 2.45, C.blue, 1.4, 'triangle');
  line(s, 2.62, 3.74, 3.08, 3.74, C.teal, 1.4, 'triangle');
  line(s, 4.22, 2.84, 4.22, 3.35, C.line, 1.3, 'triangle');
  line(s, 5.36, 2.45, 5.84, 2.45, C.orange, 1.4, 'triangle');
  line(s, 5.36, 3.74, 5.84, 3.74, C.coral, 1.4, 'triangle');
  line(s, 8.14, 2.45, 8.62, 2.95, C.line, 1.4, 'triangle');
  line(s, 8.14, 3.74, 8.62, 3.34, C.line, 1.4, 'triangle');
  line(s, 10.9, 3.02, 11.34, 2.45, C.green, 1.4, 'triangle');
  line(s, 10.9, 3.22, 11.34, 3.74, C.green, 1.4, 'triangle');
  rect(s, 0.68, 4.78, 12.0, 1.05, C.cream, 0.12, 'F4D6A6');
  addText(s, 'Luồng bằng chứng', 0.97, 5.08, 1.45, 0.2, { fontSize: 10.5, bold: true, color: '8A4B08' });
  addText(s, 'Mỗi proposal lưu snapshot nguồn, prediction run, model version, feature attribution, wave timeline, stress outcome, chi phí và proposal ID xác định.', 2.52, 5.01, 9.7, 0.34, { fontSize: 11, color: C.ink });
  note(s, 'Cloud Storage lưu payload bất biến; BigQuery giữ liên kết raw_gcs_uri. Dữ liệu vận hành trong prototype được gắn nhãn simulated.', 0.68, 6.25, 11.9);
}

// Slide 5
{
  const s = pptx.addSlide('HEATSAFE');
  title(s, '04 / Decision engine', 'SafePause: tối ưu dưới ràng buộc, không chỉ “chọn người rủi ro cao”', 'Engine chỉ trả đề xuất khi bằng chứng mô hình đầy đủ và toàn bộ guardrail được thỏa mãn.');
  const stages = [
    ['01', 'Bằng chứng đúng snapshot', 'Đọc điểm baseline và 8 biến thể action cho từng tài xế.'],
    ['02', 'Ưu tiên bắt buộc', 'Tài xế phơi nhiễm liên tục ≥ 4 giờ luôn được bao phủ trước.'],
    ['03', 'Tạo ứng viên', 'Liệt kê thời lượng, coverage và các wave lệch nhau.'],
    ['04', 'Stress-test dịch vụ', 'Mô phỏng supply/backlog/fulfillment/ETA với P50 và upper demand.'],
    ['05', 'Fail closed', 'Không đủ model hoặc không feasible → không có recommendation.'],
  ];
  stages.forEach((a, i) => {
    const x = 0.65 + i * 2.45;
    const accent = [C.blue, C.coral, C.orange, C.teal, C.green][i];
    rect(s, x, 2.16, 2.12, 3.0, C.white, 0.12, C.line);
    rect(s, x, 2.16, 2.12, 0.11, accent, 0, accent);
    addText(s, a[0], x + 0.22, 2.48, 0.5, 0.25, { fontSize: 10, bold: true, color: accent });
    addText(s, a[1], x + 0.22, 2.92, 1.68, 0.55, { fontSize: 14, bold: true, color: C.navy, valign: 'top' });
    addText(s, a[2], x + 0.22, 3.78, 1.68, 0.85, { fontSize: 10, color: C.muted, valign: 'top' });
    if (i < stages.length - 1) line(s, x + 2.12, 3.65, x + 2.38, 3.65, C.line, 1.2, 'triangle');
  });
  rect(s, 0.65, 5.63, 12.05, 0.61, C.navy, 0.1);
  addText(s, 'Guardrail hard-coded trong engine: degradation fulfillment upper-demand ≤ 2 điểm %  •  ETA tăng ≤ 2 phút  •  tổng chi phí ≤ budget cap', 0.92, 5.81, 11.5, 0.21, { fontSize: 10.7, color: C.white, bold: true, align: 'center' });
}

// Slide 6
{
  const s = pptx.addSlide('HEATSAFE');
  title(s, '05 / Vận hành end-to-end', 'Quy trình đưa app từ dữ liệu đến màn hình Ops', 'Tách giao diện phục vụ quyết định khỏi tác vụ nặng để hệ thống dễ vận hành và dễ kiểm soát chi phí.');
  const ys = [2.14, 3.17, 4.2, 5.23];
  const rows = [
    ['1', 'Ingest', 'Cloud Run Job lấy thời tiết / tạo snapshot; payload raw vào Cloud Storage, dữ liệu chuẩn hóa vào BigQuery.', C.blue],
    ['2', 'Train / score', 'Cloud Run Job huấn luyện hoặc scoring theo snapshot; BigQuery ML materialize forecast và action-conditioned risk.', C.coral],
    ['3', 'Serve', 'Cloud Run Service chạy Streamlit, đọc snapshot + output đã materialize, render dashboard và decision workspace.', C.teal],
    ['4', 'Decide / audit', 'Người dùng xem proposal; hành động demo được đánh dấu SIMULATED, sự kiện ghi vào BigQuery audit.', C.green],
  ];
  rows.forEach((r, i) => {
    rect(s, 0.72, ys[i], 0.52, 0.52, r[3], 0.26);
    addText(s, r[0], 0.72, ys[i] + 0.08, 0.52, 0.23, { fontSize: 12, bold: true, color: C.white, align: 'center' });
    line(s, 1.5, ys[i] + 0.26, 2.0, ys[i] + 0.26, r[3], 1.5, 'triangle');
    addText(s, r[1], 2.18, ys[i] - 0.02, 1.35, 0.25, { fontSize: 14, bold: true, color: C.navy });
    addText(s, r[2], 3.76, ys[i] - 0.02, 8.4, 0.45, { fontSize: 11.5, color: C.muted, valign: 'top' });
    if (i < 3) line(s, 0.98, ys[i] + 0.52, 0.98, ys[i + 1], C.line, 1.3, 'triangle');
  });
  rect(s, 8.83, 1.9, 3.82, 0.55, C.cream, 0.1, 'F4D6A6');
  addText(s, 'Không có Scheduler định kỳ trong demo', 9.05, 2.05, 3.4, 0.2, { fontSize: 10, bold: true, color: '8A4B08', align: 'center' });
  note(s, 'Các job ingest/train/score được triển khai riêng và gọi thủ công khi cần refresh dữ liệu hoặc mô hình — phù hợp mục tiêu demo và tránh provider compute không cần thiết.', 0.7, 6.38, 11.8);
}

// Slide 7
{
  const s = pptx.addSlide('HEATSAFE');
  title(s, '06 / Hạ tầng GCP', 'Dùng managed services ở đúng nơi có giá trị', 'Kiến trúc GCP giảm gánh nặng vận hành, giữ dữ liệu/AI gần nhau và scale UI độc lập với pipeline.');
  const items = [
    ['Cloud Run Service', 'Host Streamlit UI public cho demo; image build từ source; giới hạn tối đa 2 instances.', C.blue],
    ['Cloud Run Jobs', 'Tách ingestion, train model và score snapshot; retry + timeout theo từng loại tác vụ.', C.teal],
    ['BigQuery', 'System of record: history, feature, forecast, predictions, audit và current snapshot.', C.navy],
    ['BigQuery ML', 'Boosted-tree classifier cho heat-risk; TimesFM AI.FORECAST cho nhu cầu theo zone.', C.coral],
    ['Cloud Storage', 'Lưu payload raw/replay/checkpoint bất biến; liên kết lineage bằng URI/checksum.', C.orange],
    ['Vertex AI Gemini', 'Function calling trên allowlist để giải thích evidence; không tự viết SQL hay duyệt action.', C.green],
  ];
  items.forEach((it, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    const x = 0.68 + col * 6.08, y = 1.95 + row * 1.48;
    card(s, x, y, 5.6, 1.16, it[0], it[1], it[2]);
  });
  rect(s, 0.68, 6.42, 11.98, 0.34, 'EAF1F8', 0.08);
  addText(s, 'Triển khai bằng `gcloud run deploy --source` kích hoạt Cloud Build / Artifact Registry; Cloud Logging nhận structured stdout từ ứng dụng và jobs.', 0.9, 6.49, 11.55, 0.16, { fontSize: 8.9, color: C.ink, align: 'center' });
}

// Slide 8
{
  const s = pptx.addSlide('HEATSAFE');
  title(s, '07 / Tối ưu hiệu năng & chi phí', 'Tối ưu được thiết kế vào kiến trúc — không chỉ ở UI', 'Mục tiêu: phản hồi nhanh cho operator, hạn chế provider I/O và không đánh đổi tính đúng đắn.');
  const data = [
    ['Cache theo loại dữ liệu', 'Streamlit cache: snapshot 5 phút; AI summary/plan 15 phút; replay metadata 10 giây.', C.blue],
    ['Materialize trước khi serve', 'UI đọc forecast/prediction đã có trong BigQuery thay vì thực hiện train hoặc scoring trong request path.', C.teal],
    ['I/O bị giới hạn', 'TimesFM dùng context bounded 21 ngày / 2.048 điểm; lỗi forecast được surfacing thay vì reuse âm thầm.', C.orange],
    ['Giảm payload action', 'Production evidence bỏ 8 biến thể action cho tài xế nằm xa ngưỡng rủi ro/4 giờ exposure.', C.green],
    ['Idempotent & chi phí an toàn', 'Provision/seed dùng MERGE, không truncate dữ liệu live; staging dataset có TTL 1 giờ.', C.coral],
    ['Replay không gọi provider', 'Replay historical là read-only; production window dùng warm checkpoint + engine deterministic.', C.navy],
  ];
  data.forEach((d, i) => {
    const col = i % 3, row = Math.floor(i / 3);
    const x = 0.68 + col * 4.04, y = 2.04 + row * 2.08;
    card(s, x, y, 3.66, 1.7, d[0], d[1], d[2]);
  });
  rect(s, 0.68, 6.43, 11.98, 0.34, C.navy, 0.08);
  addText(s, 'Kết quả: đường đi nóng ngắn hơn, compute tách rời, dữ liệu có freshness/lineage và demo không đốt compute nền không cần thiết.', 0.88, 6.5, 11.58, 0.16, { fontSize: 9, bold: true, color: C.white, align: 'center' });
}

// Slide 9
{
  const s = pptx.addSlide('HEATSAFE');
  title(s, '08 / Trust by design', 'An toàn, khả năng giải thích và khả năng audit là “first-class”', 'Đây là nền tảng để đưa một công cụ AI vào bối cảnh vận hành nhạy cảm.');
  const columns = [
    ['FAIL CLOSED', 'MODEL_UNAVAILABLE hoặc NO_FEASIBLE\nkhông bao giờ chứa recommendation.', C.coral],
    ['LINEAGE', 'Snapshot, model, prediction run, checksum\nvà proposal ID được giữ cùng kết quả.', C.blue],
    ['HUMAN IN THE LOOP', 'Gemini chỉ giải thích evidence allowlisted;\nkhông duyệt action, không viết SQL.', C.teal],
    ['SIMULATION BOUNDARY', 'Demo action có dispatch_status = NOT_APPLICABLE;\nkhông gửi lệnh thật tới tài xế.', C.green],
  ];
  columns.forEach((c, i) => {
    const x = 0.69 + i * 3.0;
    rect(s, x, 2.12, 2.65, 2.76, C.white, 0.14, C.line);
    rect(s, x, 2.12, 2.65, 0.13, c[2], 0, c[2]);
    rect(s, x + 0.22, 2.53, 0.54, 0.54, c[2], 0.27);
    addText(s, String(i + 1), x + 0.22, 2.64, 0.54, 0.23, { fontSize: 13, bold: true, color: C.white, align: 'center' });
    addText(s, c[0], x + 0.22, 3.42, 2.15, 0.24, { fontSize: 11, bold: true, color: C.navy });
    addText(s, c[1], x + 0.22, 3.87, 2.12, 0.63, { fontSize: 10, color: C.muted, valign: 'top' });
  });
  rect(s, 0.69, 5.43, 11.95, 0.74, C.cream, 0.11, 'F4D6A6');
  addText(s, 'Phạm vi demo hiện tại: public access phù hợp hackathon. Production thật cần authentication, nguyên tắc least privilege chi tiết hơn và downstream command consumer được kiểm soát.', 0.98, 5.66, 11.4, 0.27, { fontSize: 10.3, color: '70410E', align: 'center' });
}

// Slide 10
{
  const s = pptx.addSlide('HEATSAFE');
  title(s, '09 / Điểm mạnh nổi bật', 'HeatSafe là decision system có bằng chứng — không phải dashboard đơn thuần', 'Điểm khác biệt nằm ở cách các thành phần vận hành cùng nhau dưới ràng buộc thực tế.');
  const strengths = [
    ['01', 'Safety-first optimization', 'Ưu tiên cohort phơi nhiễm dài, sau đó mới tối ưu benefit/cost.', C.coral],
    ['02', 'Action-conditioned AI', 'So sánh no-action với 8 biến thể SafePause theo từng tài xế.', C.blue],
    ['03', 'Operational guardrails', 'Kiểm soát chi phí, fulfillment và ETA dưới upper-demand stress.', C.orange],
    ['04', 'Traceable & explainable', 'Lineage, factor, model version và audit được lưu xuyên suốt.', C.teal],
    ['05', 'Cloud-native, practical', 'Cloud Run + BigQuery + Storage + Vertex AI: managed, tách rời và dễ mở rộng.', C.green],
  ];
  strengths.forEach((s0, i) => {
    const y = 1.92 + i * 0.78;
    rect(s, 0.69, y, 0.47, 0.47, s0[3], 0.235);
    addText(s, s0[0], 0.69, y + 0.11, 0.47, 0.17, { fontSize: 7.5, bold: true, color: C.white, align: 'center' });
    addText(s, s0[1], 1.42, y - 0.02, 3.2, 0.22, { fontSize: 12.5, bold: true, color: C.navy });
    addText(s, s0[2], 4.75, y - 0.02, 6.85, 0.27, { fontSize: 10.8, color: C.muted });
  });
  rect(s, 0.69, 6.06, 11.95, 0.58, C.navy, 0.11);
  addText(s, 'Thông điệp chốt: HeatSafe biến AI thành hành động vận hành có rào chắn — bảo vệ con người mà không bỏ quên chất lượng dịch vụ.', 0.98, 6.24, 11.35, 0.2, { fontSize: 11.2, bold: true, color: C.white, align: 'center' });
}

pptx.writeFile({ fileName: 'presentation/HeatSafe_AI_Ops_Architecture_GCP.pptx' });
