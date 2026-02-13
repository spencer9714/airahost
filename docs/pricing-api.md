# AiraHost Pricing API

> **版本**: v1.0.0
> **最後更新**: 2026-02-12
> **檔案位置**: `backend/main.py`
> **部署目標**: Google Cloud Run (asia-east1)

---

## 概覽

Python FastAPI 服務，負責爬取 Airbnb 房源頁面與附近可比房源，計算建議每晚房價。

**核心流程**:

```
前端 (Vercel)                  後端 (Cloud Run)
     │                              │
     │  POST /api/v1/estimate       │
     │  ─────────────────────────►  │
     │                              ├─ 1. 打開目標房源頁面，抽取房源資訊
     │                              ├─ 2. 搜尋附近同日期可比房源
     │                              ├─ 3. 滾動收集房源卡片
     │                              ├─ 4. 相似度排序 + 加權中位數計算
     │                              ├─ 5. 產生折扣建議
     │  ◄─────────────────────────  │
     │  JSON: target + comps + rec  │
```

---

## API 端點

### `GET /health`

健康檢查。

```json
{ "status": "ok", "service": "airahost-pricing" }
```

### `POST /api/v1/estimate`

主要定價端點。

#### Request Body

| 欄位 | 型別 | 必填 | 預設 | 說明 |
|------|------|------|------|------|
| `listing_url` | string | Y | — | 完整 Airbnb 房源 URL |
| `checkin` | string | Y | — | 入住日期 `YYYY-MM-DD` |
| `checkout` | string | Y | — | 退房日期 `YYYY-MM-DD` |
| `adults` | int | N | `2` | 入住人數 (1–16) |
| `top_k` | int | N | `15` | 回傳前 N 名可比房源 (3–50) |
| `max_scroll_rounds` | int | N | `12` | 搜尋頁滾動次數上限 (1–30) |
| `new_listing_discount` | float | N | `0.10` | 新房源折扣率 (0.0–0.35) |
| `location` | string | N | `null` | 手動指定地點，覆蓋自動偵測 |

#### Request 範例

```json
{
  "listing_url": "https://www.airbnb.com/rooms/1596737613274892756",
  "checkin": "2026-03-10",
  "checkout": "2026-03-12",
  "adults": 2,
  "top_k": 15,
  "new_listing_discount": 0.10
}
```

#### Response 結構

```jsonc
{
  "target": { /* ListingSpecOut — 目標房源資訊 */ },
  "comparables": [ /* ListingSpecOut[] — 附近可比房源 (含 similarity 分數) */ ],
  "recommendation": { /* RecommendationStats — 定價建議統計 */ },
  "discount_suggestions": { /* DiscountSuggestion — 折扣建議 */ },
  "total_comparables_found": 23
}
```

#### Response 型別定義

**ListingSpecOut** (目標 / 可比房源):

| 欄位 | 型別 | 說明 |
|------|------|------|
| `url` | string | 房源 URL |
| `title` | string | 房源標題 |
| `location` | string | 地點 |
| `accommodates` | int \| null | 可容納人數 |
| `bedrooms` | int \| null | 臥室數 |
| `beds` | int \| null | 床位數 |
| `baths` | float \| null | 衛浴數 |
| `property_type` | string | 房源類型 |
| `nightly_price` | float \| null | 每晚價格 |
| `currency` | string | 幣別 (預設 USD) |
| `rating` | float \| null | 評分 |
| `reviews` | int \| null | 評論數 |
| `similarity` | float \| null | 相似度 (僅 comparables) |

**RecommendationStats** (定價建議):

| 欄位 | 型別 | 說明 |
|------|------|------|
| `picked_n` | int | 實際用於計算的可比房源數 |
| `weighted_median` | float \| null | 加權中位數 (折扣前) |
| `discount_applied` | float | 實際套用的折扣率 |
| `recommended_nightly` | float \| null | 建議每晚房價 |
| `p25` | float \| null | 25th percentile |
| `p75` | float \| null | 75th percentile |
| `min` | float \| null | 可比房源最低價 |
| `max` | float \| null | 可比房源最高價 |

**DiscountSuggestion** (折扣建議):

| 欄位 | 型別 | 說明 |
|------|------|------|
| `weekly_discount_pct` | float | 建議週折扣 % (預設 8%) |
| `monthly_discount_pct` | float | 建議月折扣 % (預設 18%) |
| `non_refundable_discount_pct` | float | 建議不可退款折扣 % (預設 10%) |
| `weekly_nightly` | float \| null | 週折扣後每晚價 |
| `monthly_nightly` | float \| null | 月折扣後每晚價 |
| `non_refundable_nightly` | float \| null | 不可退款後每晚價 |

#### Error Responses

| Status | 情境 |
|--------|------|
| `422` | 無法偵測地點，需手動傳入 `location` |
| `404` | 搜尋不到有價格的可比房源 |
| `500` | 爬蟲執行失敗 (Playwright 錯誤等) |

格式: `{ "detail": "錯誤訊息" }`

---

## 內部架構

### 檔案結構

```
backend/
├── main.py              # FastAPI app + 所有爬蟲邏輯 (單檔案)
├── Dockerfile           # Cloud Run 容器
├── requirements.txt     # Python 依賴
└── .dockerignore
```

### 核心函式 (main.py)

| 函式 | 行數 | 職責 |
|------|------|------|
| `run_estimate()` | L614 | 主流程：啟動 Playwright → 抽取 → 搜尋 → 排序 → 回應 |
| `extract_target_spec()` | L231 | 打開房源頁，從 JSON-LD + body text 抽取房源規格 |
| `build_search_url()` | L345 | 組合 Airbnb 搜尋 URL |
| `collect_search_cards()` | L355 | 用 `page.evaluate()` 在瀏覽器內抓取卡片資料 |
| `scroll_and_collect()` | L433 | 滾動搜尋頁，反覆收集卡片直到沒有新結果 |
| `parse_card_to_spec()` | L467 | 把 JS 卡片資料解析成 `ListingSpec` |
| `similarity_score()` | L490 | 計算目標與可比房源的相似度 (加權) |
| `recommend_price()` | L516 | 加權中位數 + 折扣 = 建議房價 |
| `compute_discount_suggestions()` | L573 | 產生週 / 月 / 不可退款折扣建議 |

### 相似度計算

| 維度 | 權重 | 容差 |
|------|------|------|
| 可容納人數 (`accommodates`) | 2.2 | 3 人 |
| 臥室數 (`bedrooms`) | 2.6 | 2 間 |
| 床位數 (`beds`) | 1.4 | 3 張 |
| 衛浴數 (`baths`) | 2.0 | 1.5 間 |

公式: `score = Σ(max(0, 1 - |target - comp| / tolerance) * weight) / Σ(weight)`
缺失值給予 0.35 的保守分數。

### 定價算法

1. 收集所有帶價格的可比房源
2. 按 similarity 降序排列，取前 `top_k` 名
3. 計算 **加權中位數** (weighted median)
4. 套用 `new_listing_discount` (預設 10%)
5. 回傳 `recommended_nightly = weighted_median * (1 - discount)`

### 反偵測措施

- `playwright-stealth` patch (移除 navigator.webdriver 等自動化痕跡)
- 自訂 User-Agent (Chrome 124)
- `--disable-blink-features=AutomationControlled`
- viewport: 1920x1080, locale: en-US

---

## 環境變數

| 變數 | 預設值 | 說明 |
|------|--------|------|
| `PORT` | `8080` | 服務監聽埠 (Cloud Run 自動注入) |
| `ALLOWED_ORIGINS` | `https://airahost.vercel.app,http://localhost:3000` | CORS 允許的前端網域 (逗號分隔) |

---

## Docker 配置

**Base Image**: `mcr.microsoft.com/playwright/python:v1.49.1-noble`

- 預裝 Ubuntu Noble + Python + Chromium 系統依賴
- 額外執行 `playwright install --with-deps chromium` 確保瀏覽器完整

**建議 Cloud Run 配置**:

| 設定 | 值 | 原因 |
|------|----|------|
| Memory | 2Gi | Playwright + Chromium 約需 1.5 GB |
| Timeout | 300s | 爬蟲含滾動可能耗時 30–120s |
| Concurrency | 4 | 限制每容器的 Chromium 並行數 |
| Min instances | 0 | 空閒時縮放至零，節省成本 |
| Max instances | 3 | 控制成本上限 |

---

## 依賴

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
playwright==1.49.1
playwright-stealth==1.0.6
pydantic==2.10.4
```

---

## 本地開發

```powershell
cd backend
pip install -r requirements.txt
playwright install chromium
uvicorn main:app --reload --port 8000
```

測試:
```powershell
curl -X POST http://localhost:8000/api/v1/estimate `
  -H "Content-Type: application/json" `
  -d '{"listing_url":"https://www.airbnb.com/rooms/123456","checkin":"2026-03-10","checkout":"2026-03-12","adults":2}'
```

---

## 部署 (Google Cloud Run)

```powershell
# 1. 建置並推送映像
cd backend
gcloud builds submit --tag asia-east1-docker.pkg.dev/PROJECT_ID/airahost/pricing-api:latest

# 2. 部署
gcloud run deploy airahost-pricing `
  --image asia-east1-docker.pkg.dev/PROJECT_ID/airahost/pricing-api:latest `
  --region asia-east1 `
  --memory 2Gi `
  --timeout 300s `
  --concurrency 4 `
  --min-instances 0 `
  --max-instances 3 `
  --set-env-vars "ALLOWED_ORIGINS=https://airahost.vercel.app,http://localhost:3000" `
  --allow-unauthenticated

# 3. 取得 URL
gcloud run services describe airahost-pricing --region asia-east1 --format="value(status.url)"
```

---

## Changelog

| 日期 | 版本 | 變更 |
|------|------|------|
| 2026-02-12 | v1.0.0 | 初版：從 `airbnb_price_estimator.py` 重構為 FastAPI 服務。移除 CDP 連接，改用 headless Playwright。加入折扣建議邏輯。 |
