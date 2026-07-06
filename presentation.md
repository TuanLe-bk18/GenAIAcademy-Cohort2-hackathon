# HeatSafe AI Ops - Gen AI Academy Presentation

## Slide 1: Participant Details
*   **Participant Name:** [Tên Đội / Cá nhân]
*   **Problem Statement:** AI for better living and smarter communities.

---

## Slide 2: Problem & Idea
*   **Vấn đề:** Nắng nóng cực đoan tại Hà Nội đe dọa sức khỏe lực lượng tài xế công nghệ. Nếu chỉ cảnh báo chung chung, tài xế có thể bỏ qua vì lo mất thu nhập; nhưng cho nghỉ hàng loạt sẽ làm giảm nguồn cung và tăng thời gian chờ.
*   **Ý tưởng (HeatSafe AI Ops):** Nền tảng điều hành can thiệp sớm bằng AI. Hệ thống dự báo rủi ro sốc nhiệt cho **từng tài xế**, sau đó xếp lịch nghỉ luân phiên (SafePause). Hệ thống chỉ đề xuất khi phương án nằm trong giới hạn chi phí, Fulfillment và ETA; nếu không có phương án phù hợp, hệ thống báo xung đột thay vì đưa ra khuyến nghị.

---

## Slide 3: Giải quyết bài toán bằng Google Cloud AI
*   **Cách tiếp cận (Tech & AI):** Kết hợp **BigQuery ML** đánh giá rủi ro cá nhân với quy tắc an toàn bắt buộc cho tài xế phơi nhiễm liên tục từ 4 giờ. **TimesFM** dự báo lượng đơn; **Gemini** chuyển kết quả mô hình thành giải thích vận hành dễ hiểu.
*   **Tác động thực tiễn:** Giúp doanh nghiệp vận tải công nghệ chủ động bảo vệ gig-worker, kiểm soát chi phí can thiệp và duy trì chất lượng dịch vụ.
*   **Kiến trúc cốt lõi:** GCS lưu dữ liệu thô → BigQuery quản lý dữ liệu và chạy ML → Cloud Run mô phỏng SafePause → Gemini giải thích → BigQuery lưu audit.

---

## Slide 4: Opportunities & USP
*   **Sự khác biệt lớn nhất (AI + Safety Rules):** Không dừng ở kịch bản "nhiệt độ cao thì gửi cảnh báo". Mức độ rủi ro được AI tính toán riêng cho **từng người** dựa trên thời gian chạy liên tục, quãng đường, độ ẩm và tải lượng công việc; quy tắc an toàn bảo đảm tài xế phơi nhiễm từ 4 giờ được ưu tiên bắt buộc.
*   **USP (Unique Selling Proposition):**
    1.  **Tối ưu hóa đa mục tiêu (Constrained Optimization):** Hệ thống chỉ đề xuất lịch nghỉ khi bao phủ đầy đủ nhóm bắt buộc, chi phí không vượt ngân sách, Fulfillment suy giảm không quá 2 điểm phần trăm và ETA tăng không quá 2 phút trong kịch bản nhu cầu cao.
    2.  **AI ngay trong kho dữ liệu:** Training, Forecasting và Scoring chạy trong BigQuery ML, giảm pipeline trung gian; Cloud Run chỉ nhận kết quả đã chuẩn hóa để mô phỏng và trình bày quyết định.

---

## Slide 5: Tính năng trọng tâm (Key Features)
*   **Đánh giá rủi ro cá nhân hóa:** Mô hình ML dự đoán tỷ lệ kiệt sức của tài xế theo thời gian thực (`ML.PREDICT`).
*   **Dự báo nhu cầu đặt xe (TimesFM):** Tính toán lượng khách dự kiến để tránh cho tài xế nghỉ vào ngay khung giờ cao điểm.
*   **Mô phỏng quyết định (Counterfactual Scoring):** Chạy giả lập hàng chục kịch bản nghỉ ngơi (nghỉ 15p, 30p, trễ 10p...) để tìm ra action tối ưu nhất.
*   **Trợ lý giải thích AI (Gemini Copilot):** Phân tích gốc rễ vấn đề (`ML.EXPLAIN_PREDICT`). Ví dụ: *"Tài xế A nguy hiểm vì đã chạy liên tục 4h dưới độ ẩm cao"* và tóm tắt đề xuất.

---

## Slide 6: Quy trình vận hành (Process Flow)
1.  **Thu thập (Ingestion):** Open-Meteo cung cấp thời tiết thật; trạng thái đội xe của prototype là dữ liệu mô phỏng.
2.  **Lưu vết (GCS):** Payload gốc được lưu bất biến để replay và truy vết nguồn; BigQuery giữ `raw_gcs_uri`.
3.  **Phân tích (BigQuery):** Chuẩn hóa lịch sử thời tiết, vận hành, nhu cầu, tài xế và kết quả can thiệp.
4.  **Học máy (BigQuery ML):** Boosted Tree dự báo rủi ro tài xế; TimesFM dự báo nhu cầu theo khu vực.
5.  **Giả lập (Cloud Run):** Optimizer so sánh các phương án SafePause theo chi phí, Fulfillment và ETA.
6.  **Duyệt lệnh (Human-in-the-loop):** Gemini tóm tắt phương án tốt nhất, Quản lý ấn "Duyệt" trên Dashboard.
7.  **Audit (BigQuery):** Lưu proposal, model provenance và quyết định với trạng thái `SIMULATED`.

---

## Slide 7: Wireframes/Mock diagrams
Trình bày ba màn hình theo hành trình ra quyết định:

1.  **City Intelligence:** Bản đồ Hà Nội, mức Heat Index và danh sách khu vực xếp theo độ khẩn cấp.
2.  **SafePause Workspace:** Khu vực được chọn, nhóm tài xế ưu tiên, lịch nghỉ theo wave và các guardrail chi phí/Fulfillment/ETA.
3.  **Driver Evidence & Copilot:** Rủi ro trước/sau, top feature attributions, câu trả lời Gemini và nguồn model đứng sau đề xuất.

**Thông điệp dưới hình:** *Từ phát hiện điểm nóng đến một phương án can thiệp có thể giải thích và kiểm chứng.*

---

## Slide 8: Kiến trúc hệ thống (Architecture Diagram)
```text
Open-Meteo (thật) + Fleet/Driver Data (mô phỏng)
                       ↓
              Cloud Run Ingestion Job
                 ↙                 ↘
      GCS Raw & Replay        BigQuery System of Record
         (lineage)            weather · fleet · demand
                                      ↓
                         BigQuery ML / AI.FORECAST
                    Boosted Tree risk · TimesFM demand
                                      ↓
                       SafePause Constrained Optimizer
                                      ↓
                        Cloud Run / Streamlit Console
                            ↙                    ↘
             Vertex AI Gemini             BigQuery Audit
        allowlisted explanation      proposal · model · SIMULATED
```

---

## Slide 9: Tại sao chọn hệ sinh thái Google Cloud?
*   **BigQuery ML (Trái tim hệ thống):** Train, Forecast và Predict diễn ra trực tiếp trong kho dữ liệu, giảm pipeline trung gian và giữ feature, prediction, evaluation cùng một nơi có thể audit.
*   **Vertex AI (Gemini):** Đóng vai trò *Explainable AI* (AI có thể giải thích). Gemini không tự tiện ra lệnh (tránh hallucination), mà chỉ làm nhiệm vụ "đọc hiểu" kết quả từ thuật toán để báo cáo cho con người.
*   **Cloud Run:** Nền tảng managed cho Streamlit UI và các ingestion/model jobs, hỗ trợ tự động co giãn và giảm công việc vận hành hạ tầng.
*   **Cloud Storage:** Lưu payload gốc bất biến và replay scenario; liên kết `raw_gcs_uri` giúp truy vết từ dữ liệu phân tích về nguồn.

---

## Slide 10: Snapshots of the prototype
Sử dụng ba ảnh chụp thật, mỗi ảnh chỉ giữ một callout chính:

1.  **Hanoi Operations View:** Bản đồ, zone ranking và dữ liệu provenance của snapshot.
2.  **SafePause Recommendation:** Số tài xế được chọn, lịch nghỉ theo wave, tác động dự kiến và trạng thái guardrail.
3.  **AI Evidence & Audit:** Top feature attributions, Gemini tool trace và quyết định được ghi `SIMULATED` trong audit log.

**Caption:** *Prototype chạy cloud-first với BigQuery ML predictions; dữ liệu đội xe và quyết định can thiệp được ghi rõ là mô phỏng.*

---

## Slide 11: Thank you
**"HeatSafe AI Ops - Data-driven empathy."**
*Bảo vệ người lao động bằng thuật toán, tối ưu vận hành bằng AI.*
