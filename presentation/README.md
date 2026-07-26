# HeatSafe AI Ops — Architecture & GCP deck

- Presentation: `HeatSafe_AI_Ops_Architecture_GCP.pptx`
- Language: Vietnamese
- Slides: 10 (16:9, editable)

## Nội dung

1. Bài toán vận hành
2. Hai runtime của ứng dụng
3. Luồng dữ liệu và quyết định
4. SafePause decision engine
5. Quy trình end-to-end
6. Hạ tầng GCP
7. Tối ưu hiệu năng/chi phí
8. An toàn, audit và giới hạn demo
9. Điểm mạnh kiến trúc

Deck phản ánh code và cấu hình hiện có. Các hành động trong demo được nêu rõ là `SIMULATED`; app không gửi dispatch thực tới tài xế.

## Tạo lại deck (tùy chọn)

Cần Node.js. Từ thư mục gốc dự án:

```bash
npm --prefix presentation install --no-save pptxgenjs
node presentation/create_heatsafe_architecture_deck.js
```

Sau khi tạo lại, có thể xóa `presentation/node_modules` vì đây chỉ là dependency build cục bộ.
