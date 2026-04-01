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

## 產出

- `ml/predictions.csv`：包含 `date` 與 `predicted_price`

## 注意

目前這個版本使用 `comparable_pool_entries` 中的競爭者屋型與價格資料進行建模，初步輸出的是基於房源屬性的 30 天價格建議。
