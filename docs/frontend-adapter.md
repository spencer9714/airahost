# Frontend Python Adapter

> **最後更新**: 2026-02-12
> **檔案位置**: `src/core/pythonAdapter.ts`
> **對應後端**: `backend/main.py` (見 [pricing-api.md](./pricing-api.md))

---

## 概覽

TypeScript adapter，負責將前端的 `PricingCoreInput` 轉送到 Python FastAPI 後端，
並將回傳的爬蟲結果映射回前端既有的 `PricingCoreOutput` 格式。

**設計目標**: 與 `src/core/pricingCore.ts` (mock) 保持相同介面，可直接替換 import。

---

## 如何切換 mock → real

在 `src/app/api/reports/route.ts` 中:

```ts
// 改前 (mock):
import { generatePricingReport } from "@/core/pricingCore";

// 改後 (real):
import { generatePricingReport } from "@/core/pythonAdapter";
```

注意: `pythonAdapter` 版本的 `generatePricingReport` 是 **async**，呼叫處需加 `await`。

---

## 環境變數

在 `.env.local` 或 Vercel Dashboard 設定:

```
NEXT_PUBLIC_PYTHON_API_URL=https://airahost-pricing-xxxxx-de.a.run.app
```

未設定時預設為 `http://localhost:8000` (本地開發用)。

---

## 匯出的 Interfaces

### Python API 原始型別

| Interface | 說明 |
|-----------|------|
| `EstimateRequest` | 發送到 Python API 的請求格式 |
| `EstimateResponse` | Python API 的完整回應 |
| `ListingSpecOut` | 單一房源資訊 (目標 / 可比) |
| `RecommendationStats` | 定價建議統計 |
| `DiscountSuggestion` | 折扣建議 |

### 前端相容型別

| Interface | 說明 |
|-----------|------|
| `PricingCoreInput` | 與 `pricingCore.ts` 相同的輸入格式 |
| `PricingCoreOutput` | 與 `pricingCore.ts` 相同的輸出格式 |

---

## 匯出的函式

### `fetchEstimate(req: EstimateRequest): Promise<EstimateResponse>`

直接呼叫 Python API，回傳原始 response。適合需要完整可比房源資料的場景。

- Timeout: 300 秒 (5 分鐘)
- 錯誤時拋出 `Error`，帶有後端回傳的 detail 訊息

### `generatePricingReport(input: PricingCoreInput): Promise<PricingCoreOutput>`

與 mock `pricingCore.ts` 相容的介面。內部做以下映射:

1. 將 `ListingInput.address` 轉為 `listing_url` + `location`
2. 呼叫 `fetchEstimate()`
3. 用 `recommended_nightly` 生成合成的每日日曆 (synthetic calendar)
4. 週末加價 12%
5. 套用前端的 `DiscountPolicy` (週折扣 / 月折扣 / 不可退款)
6. 回傳 `ReportSummary` + `CalendarDay[]`

---

## 資料流

```
PricingCoreInput
  │
  ├─ listing.address      → EstimateRequest.location
  ├─ listing.maxGuests    → EstimateRequest.adults
  ├─ startDate            → EstimateRequest.checkin
  └─ endDate              → EstimateRequest.checkout
                              │
                              ▼
                    Python API /api/v1/estimate
                              │
                              ▼
                      EstimateResponse
                              │
  ┌───────────────────────────┤
  │                           │
  ▼                           ▼
recommendation          discount_suggestions
  │                           │
  ├─ recommended_nightly  ──► basePrice (per day)
  ├─ min/max              ──► nightlyMin/Max
  └─ weighted_median      ──► insightHeadline
                              │
                              ▼
                      PricingCoreOutput
                    { summary, calendar }
```

---

## Changelog

| 日期 | 變更 |
|------|------|
| 2026-02-12 | 初版：實作 `fetchEstimate()` + `generatePricingReport()` adapter。定義完整 TypeScript interfaces。 |
