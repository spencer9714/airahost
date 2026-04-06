# AiraHost ML

這個資料夾是 `airahost` 專案的獨立 Python ML pipeline，負責：

- 從 Supabase 讀取目標房源與市場競品資料
- 用 XGBoost 訓練「市場對未來入住日的訂價行為」
- 輸出未來 30 天建議價格
- 產出回測、解釋模型、前端可消費的報表檔案

這份 README 是使用手冊。接手的人看完後，應該要能：

- 知道怎麼安裝與設定環境
- 知道怎麼一鍵訓練與預測
- 知道怎麼做回測與驗證
- 知道怎麼讀 explainability 報表
- 知道怎麼把 ML 串到前端或 API

## 1. 主要檔案

- `ml/batch_pipeline.py`
  - 主入口。最推薦用這支跑完整流程。
- `ml/run_forecast.py`
  - 較低階的訓練與預測腳本，適合 debug。
- `ml/backtest.py`
  - 回測工具，支援單屋 holdout 與時間切分回測。
- `ml/compare_predictions.py`
  - 比較兩個預測日期為什麼差價。
- `ml/data.py`
  - 從 Supabase 取資料，做訓練資料整理與時間特徵抽取。
- `ml/model.py`
  - XGBoost 訓練、特徵矩陣、逐日 explainability。
- `ml/supabase_client.py`
  - 載入 `.env` 並建立 Supabase client。
- `ml/tests/`
  - smoke test、backtest、日期比較等測試。

## 2. 安裝

```powershell
cd c:\airahost
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r ml\requirements.txt
```

## 3. 環境變數

至少要有這些：

```text
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
ML_DEFAULT_SAVED_LISTING_ID=<saved_listings uuid>
```

可選：

```text
ML_BACKTEST_HOLDOUT_COMP_ID=<training airbnb_listing_id>
```

注意：

- `ML_DEFAULT_SAVED_LISTING_ID` 是 `saved_listings.id`
- `ML_BACKTEST_HOLDOUT_COMP_ID` 是訓練資料中的 `airbnb_listing_id`
- `ML_BACKTEST_HOLDOUT_COMP_ID` 不是 `saved_listings` 的 UUID
- `ML_BACKTEST_HOLDOUT_COMP_ID` 也不是任何 Supabase key

`ml/.env` 可以覆蓋根目錄 `.env`，適合放 ML 專用預設值。

## 4. 快速 Demo

如果你只想快速 demo，照這個順序就可以：

1. 啟動虛擬環境

```powershell
cd c:\airahost
.\.venv\Scripts\Activate.ps1
```

2. 跑完整批次流程

```powershell
.\run_ml_batch.cmd
```

3. 打開這三個檔

- `ml/reports/predictions.csv`
- `ml/reports/metrics_latest.csv`
- `ml/reports/prediction_explanations.csv`

4. 比較兩個日期為什麼差價

```powershell
.\run_ml_compare_dates.cmd 2026-04-06 2026-04-13
```

5. 跑單屋回測

```powershell
.\run_ml_backtest.cmd
```

6. 跑更接近上線的時間切分回測

```powershell
.\run_ml_backtest_time.cmd
```

如果你習慣 npm script，也可以用：

```powershell
npm run ml:batch
npm run ml:backtest
npm run ml:backtest:time
npm run ml:compare -- 2026-04-06 2026-04-13
```

## 5. 怎麼訓練與預測

### 推薦：跑完整批次流程

```powershell
python -m ml.batch_pipeline --saved-listing-id <uuid> --smoke-test
```

這會一次完成：

- 從 Supabase 抓訓練資料
- 建立訓練矩陣
- 訓練 XGBoost
- 產出 30 天預測
- 寫出 metrics、feature importance、explainability、manifest

如果不帶 `--saved-listing-id`，會用 `ML_DEFAULT_SAVED_LISTING_ID`。

### 只重用既有模型，不重訓

```powershell
python -m ml.batch_pipeline --saved-listing-id <uuid> --reuse-model
```

### 用較低階腳本做 debug

```powershell
python -m ml.run_forecast --saved-listing-id <uuid> --output ml/reports/predictions.csv
```

這支適合在你想單獨觀察訓練輸出時使用，例如：

```powershell
python -m ml.run_forecast `
  --saved-listing-id <uuid> `
  --dump-training-csv ml/reports/training_matrix.csv `
  --dump-feature-csv ml/reports/feature_descriptions.csv `
  --dump-importance-csv ml/reports/feature_importance.csv `
  --dump-metrics-csv ml/reports/metrics_latest.csv `
  --save-model ml/reports/saved_model.json `
  --output ml/reports/predictions.csv
```

## 6. 會輸出哪些檔案

每次跑完 batch pipeline，主要看這些：

- `ml/reports/predictions.csv`
  - 30 天預測結果
- `ml/reports/metrics_latest.csv`
  - 最新訓練指標
- `ml/reports/training_data_dump.csv`
  - 原始訓練資料
- `ml/reports/training_matrix.csv`
  - 特徵工程後的訓練矩陣
- `ml/reports/feature_descriptions.csv`
  - 每個特徵欄位的解釋
- `ml/reports/feature_importance.csv`
  - 全域 importance 摘要
- `ml/reports/feature_importance_detailed.csv`
  - `importance`、`gain`、`cover`、`weight`、`total_gain`、`total_cover`
- `ml/reports/model_tree_dump.txt`
  - 每棵樹的 split 規則
- `ml/reports/prediction_explanations.csv`
  - 每一天預測的 top drivers
- `ml/reports/prediction_feature_contributions.csv`
  - 每一天完整 feature contribution 長表
- `ml/reports/batch_pipeline_result.json`
  - 前端或 API 最適合讀的 manifest

## 7. 怎麼測試

### 單元測試

```powershell
python -m pytest ml/tests -q
```

目前測試涵蓋：

- batch pipeline smoke test
- backtest 聚合邏輯
- holdout house 選擇邏輯
- 日期比較工具

### smoke test

batch pipeline 本身支援 smoke test：

```powershell
python -m ml.batch_pipeline --saved-listing-id <uuid> --smoke-test
```

它會確認最主要輸出檔與欄位都有產生。

## 8. 怎麼驗證模型

### 驗證一：單屋 holdout 回測

用途：

- 從訓練資料挑一間房整個拿掉
- 用剩下的房訓練
- 再去預測這間房的未來價格

指令：

```powershell
.\run_ml_backtest.cmd
```

如果要固定驗證某一間訓練房：

```powershell
.\run_ml_backtest.cmd --holdout-comp-id <airbnb_listing_id>
```

主要看：

- `ml/reports/backtest_summary.csv`
- `ml/reports/backtest_predictions.csv`
- `ml/reports/backtest_by_date.csv`

### 驗證二：時間切分回測

用途：

- 用較早期資料訓練
- 用最近幾天 `observed_at_date` 當驗證
- 更接近真實上線情境

指令：

```powershell
.\run_ml_backtest_time.cmd
```

等價指令：

```powershell
python -m ml.backtest --saved-listing-id <uuid> --split-mode time --validation-days 7
```

### 回測時重點看什麼

- `mape_pct`
  - 平均絕對百分比誤差，最重要
- `mean_signed_pct_error_pct`
  - 看模型整體偏高估還是偏低估
- `within_10_pct_ratio`
  - 落在 10% 誤差內的比例
- `within_20_pct_ratio`
  - 落在 20% 誤差內的比例

可以先用這個粗略判斷：

- `mape_pct < 10`：很好
- `10 <= mape_pct < 15`：可接受
- `15 <= mape_pct < 20`：偏弱，但可先 demo
- `mape_pct >= 20`：通常要再調模型或資料

## 9. 怎麼讀模型學習結果

### 全域層級

看：

- `ml/reports/feature_importance.csv`
- `ml/reports/feature_importance_detailed.csv`
- `ml/reports/model_tree_dump.txt`

用途：

- 知道模型整體最常依賴哪些特徵
- 比較不同 importance 定義
- 追查 tree 的 split 規則

### 單日層級

看：

- `ml/reports/prediction_explanations.csv`
- `ml/reports/prediction_feature_contributions.csv`

用途：

- 看某一天的價格是被哪些特徵推高或壓低
- 看同樣是 weekday，為什麼價格還是不同

重要提醒：

- contribution 是在 `log1p(price)` 空間
- 正值代表 `push_up`
- 負值代表 `push_down`
- `contribution_multiplier = exp(contribution_log)` 可以當相對乘數看

這也是為什麼同樣是週間，價格還是可能不同。模型不是只看 `day_of_week`，還會一起看：

- `days_until_stay`
- `lead_time_bucket`
- `day_of_month_bucket`
- `month`
- `holiday_window_type`
- `location_*`
- `bedrooms`、`accommodates`、`effective_rank_score` 等房型與市場特徵

### 比較兩個日期為什麼差價

```powershell
.\run_ml_compare_dates.cmd 2026-04-06 2026-04-13
```

會輸出：

- `ml/reports/prediction_date_comparison_summary.csv`
  - 兩天的預測價格與價差比例
- `ml/reports/prediction_date_comparison_drivers.csv`
  - 兩天之間每個 feature 的 contribution 差分

## 10. 怎麼串接到前端

### 先講結論

前端不要直接 import Python。推薦做法是：

1. 前端把 `saved_listing_id` 送到一個 API route
2. API route 觸發 `python -m ml.batch_pipeline`
3. Python 跑完後寫出 `ml/reports/batch_pipeline_result.json`
4. API route 讀這個 manifest，再回傳給前端

### Next.js / Node 端建議接法

可以在 server route 用 subprocess 啟動：

```ts
import { promises as fs } from "node:fs";
import { spawn } from "node:child_process";
import { NextResponse } from "next/server";

export async function POST(req: Request) {
  const { savedListingId } = await req.json();

  await new Promise<void>((resolve, reject) => {
    const child = spawn("python", [
      "-m",
      "ml.batch_pipeline",
      "--saved-listing-id",
      savedListingId,
      "--smoke-test",
    ], { cwd: "c:/airahost" });

    child.on("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`ML batch failed with code ${code}`));
    });
  });

  const manifestRaw = await fs.readFile("c:/airahost/ml/reports/batch_pipeline_result.json", "utf8");
  return NextResponse.json(JSON.parse(manifestRaw));
}
```

前端最適合讀的欄位是：

- `listing_id`
- `trained_now`
- `model_mode`
- `n_samples`
- `metrics`
- `artifacts.predictions_latest`
- `artifacts.metrics_latest`
- `artifacts.prediction_explanations`

### 如果是 Python service / worker

可以直接呼叫函式：

```python
from ml.batch_pipeline import execute_batch_workflow

result = execute_batch_workflow(
    saved_listing_id="your-saved-listing-uuid",
    force_train=True,
    smoke_test=True,
)
```

`result` 會回傳 manifest dict，內容和 `batch_pipeline_result.json` 一致。

## 11. 快速 Demo 給接手同事的流程

如果你只有 5 到 10 分鐘帶人 demo，照這個順序最順：

1. 打開 `ml/.env`，確認 `SUPABASE_URL`、`SUPABASE_SERVICE_ROLE_KEY`、`ML_DEFAULT_SAVED_LISTING_ID`
2. 跑 `.\run_ml_batch.cmd`
3. 打開 `ml/reports/predictions.csv` 看 30 天價格
4. 打開 `ml/reports/metrics_latest.csv` 看這次訓練指標
5. 打開 `ml/reports/prediction_explanations.csv` 看每天 top drivers
6. 跑 `.\run_ml_compare_dates.cmd 2026-04-06 2026-04-13`
7. 跑 `.\run_ml_backtest.cmd`
8. 補充說明前端要讀 `ml/reports/batch_pipeline_result.json`

## 12. 常見問題

### 1. `run_ml_backtest.cmd` 炸掉說 holdout comp id 不存在

大多是 `.env` 裡把 `ML_BACKTEST_HOLDOUT_COMP_ID` 設成錯的值。

正確值要像這樣：

```text
ML_BACKTEST_HOLDOUT_COMP_ID=1005860849161564008
```

錯誤示例：

- `saved_listings` 的 UUID
- `SUPABASE_SERVICE_ROLE_KEY`
- `sb_secret_...`

### 2. 假日判斷不合理

通常是這筆 listing 沒有可靠的 `country_code`，目前會避免亂套錯誤國家假日；如果地點資訊不足，`is_holiday` 會偏保守。

### 3. 連 Supabase 失敗

先檢查：

- `.env` 是否有正確 `SUPABASE_URL`
- `.env` 是否有正確 `SUPABASE_SERVICE_ROLE_KEY`
- 本機網路 / proxy 是否允許連線

### 4. 前端可不可以直接 import `ml.batch_pipeline`？

不建議。Next.js 是 Node/TypeScript runtime，不是 Python runtime。請用：

- subprocess 啟動 Python
- 或把 ML 包成獨立 Python service

## 13. 目前推薦的預設入口

如果你不確定要跑哪支，就記這四條：

```powershell
.\run_ml_batch.cmd
.\run_ml_backtest.cmd
.\run_ml_backtest_time.cmd
.\run_ml_compare_dates.cmd 2026-04-06 2026-04-13
```
