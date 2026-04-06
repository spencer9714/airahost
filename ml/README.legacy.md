# AiraHost ML

這個資料夾是一個獨立的 Python ML pipeline，用來從 Supabase 讀取競爭者價格資料，訓練 XGBoost 模型，並輸出未來 30 天的價格建議 CSV。

## 目的

- 直接連接 Supabase，讀取 `comparable_pool_entries`、`saved_listings` 資料
- 以競爭者價格、房型資訊、相似度與排名信號訓練 XGBoost 回歸模型
- 根據使用者貼上的 Airbnb 房源 URL，生成未來 30 天的價格建議
- 不改動現有前端設計，先獨立放在 `ml/` 資料夾

## 安裝

```powershell
cd c:\airahost
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r ml\requirements.txt
```

## 環境變數

請在專案根目錄或 `worker/` 目錄下的 `.env` 裡提供：

```text
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
```

`CDP_URL` 對這個 ML pipeline 不必要，但可以保留在 `worker/.env`。

## 使用方式

```powershell
python -m ml.run_forecast --listing-url https://www.airbnb.com/rooms/12345678 --output ml/predictions.csv
```

如果你已經有 `saved_listings` 的 `id`：

```powershell
python -m ml.run_forecast --saved-listing-id <uuid> --output ml/predictions.csv
```

如果你想直接跑完整批次流程，從抓資料、訓練到輸出預測一次完成，現在可以直接用：

```powershell
python -m ml.batch_pipeline --saved-listing-id <uuid> --smoke-test
```

如果不帶 `--saved-listing-id`，會自動使用 `ML_DEFAULT_SAVED_LISTING_ID`，或退回內建的預設房源。

如果你想重用既有模型、只更新預測：

```powershell
python -m ml.batch_pipeline --saved-listing-id <uuid> --reuse-model
```

如果你想做「拿訓練資料中的某一間房，先把它從訓練集中拿掉，再預測它的未來價格並和真實價格比對」的回測：

```powershell
python -m ml.backtest --saved-listing-id <uuid>
```

也可以指定要驗證的某一間訓練房：

```powershell
python -m ml.backtest --saved-listing-id <uuid> --holdout-comp-id <airbnb_listing_id>
```

如果你不想每次輸入 `--holdout-comp-id`，可以在 `ml/.env` 或根目錄 `.env` 設：

```text
ML_BACKTEST_HOLDOUT_COMP_ID=<airbnb_listing_id>
```

注意：這裡要放的是訓練資料中的 `airbnb_listing_id`，不是 `saved_listings` 的 UUID，也不是任何 Supabase key。

回測輸出會同時保留金額誤差與比例誤差，但比例欄位是：
- `error_pct`: 帶方向的百分比誤差
- `abs_error_pct`: 絕對百分比誤差
- `mape_pct`: 平均絕對百分比誤差

如果你想做更接近上線情境的時間回測，只用較早期資料訓練、拿最近幾天的 `observed_at_date` 驗證：

```powershell
python -m ml.backtest --saved-listing-id <uuid> --split-mode time --validation-days 7
```

如果你不想記長指令，專案根目錄已經提供了 Windows 短命令：

```powershell
.\run_ml_batch.cmd
.\run_ml_backtest.cmd
.\run_ml_backtest_time.cmd
.\run_ml_compare_dates.cmd 2026-04-06 2026-04-13
```

也可以用 npm scripts：

```powershell
npm run ml:batch
npm run ml:backtest
npm run ml:backtest:time
npm run ml:compare -- 2026-04-06 2026-04-13
```

每次跑完 `python -m ml.batch_pipeline` 或 `.\run_ml_batch.cmd`，現在除了原本的預測與訓練矩陣外，還會多輸出幾個用來解釋模型的檔案：

- `ml/reports/feature_importance.csv`
  - 全域特徵重要性摘要，適合快速看模型最常依賴哪些欄位。
- `ml/reports/feature_importance_detailed.csv`
  - 同時保留 `importance`、`gain`、`cover`、`weight`、`total_gain`、`total_cover`，適合比較不同 importance 定義。
- `ml/reports/model_tree_dump.txt`
  - XGBoost 每棵樹的實際 split 規則文字版，適合追查模型怎麼切特徵。
- `ml/reports/prediction_explanations.csv`
  - 每個預測日期一列，包含 `baseline_price`、`predicted_price`，以及當天最主要的前 5 個驅動特徵。
- `ml/reports/prediction_feature_contributions.csv`
  - 長表格式的逐日 feature contribution，會標記某個特徵是在 `push_up` 還是 `push_down` 價格。

注意：`prediction_explanations.csv` 與 `prediction_feature_contributions.csv` 裡的 contribution 是 XGBoost 在 `log1p(price)` 空間的貢獻值，所以：
- 正值代表把價格往上推
- 負值代表把價格往下壓
- `contribution_multiplier = exp(contribution_log)` 可當作相對乘數來看

這也是為什麼同樣是週間，價格還是可能不同：模型不是只看 `day_of_week`，還會一起看 `days_until_stay`、`lead_time_bucket`、`day_of_month_bucket`、`month`、`holiday_window_type` 等其他時間特徵。

如果你想直接比較兩個日期為什麼差價，可以用：

```powershell
.\run_ml_compare_dates.cmd 2026-04-06 2026-04-13
```

它會輸出：
- `ml/reports/prediction_date_comparison_summary.csv`
  - 兩天的預測價格、價差、價差比例
- `ml/reports/prediction_date_comparison_drivers.csv`
  - 所有 feature 在兩天之間的 contribution 差分，適合找出哪幾個特徵把某一天往上推或往下壓

## 產出

- `ml/predictions.csv`：包含 `date` 與 `predicted_price`

## 注意

目前這個版本使用 `comparable_pool_entries` 中的競爭者屋型與價格資料進行建模，初步輸出的是基於房源屬性的 30 天價格建議。
