from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from ml.data import TARGET_COLUMN_NAME, build_temporal_feature_values

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, r2_score, mean_absolute_percentage_error
from sklearn.model_selection import KFold, RandomizedSearchCV
from xgboost import DMatrix, XGBRegressor

# 定義專案通用的設施列表
AMENITIES_LIST = [
    "wifi", "kitchen", "washer", "dryer", "ac", "heating", "pool",
    "hot_tub", "free_parking", "ev_charger", "gym", "bbq"
]

NUMERIC_FEATURES = [
    "bedrooms",
    "baths",
    "accommodates",
    "beds",
    "similarity_score",
    "pool_score",
    "effective_rank_score",
    "price_reliability_score",
    "rating",
    "reviews",
    "tenure_runs",
    "is_weekend",
    "is_holiday",
    "days_until_stay",
    "days_since_first_seen",
    "holiday_streak_length",
    "is_long_weekend",
    "distance_to_target_km",
    "lat",
    "lng",
] + [f"has_{a}" for a in AMENITIES_LIST] # 動態加入設施特徵
CATEGORICAL_FEATURES = [
    "property_type",
    "location_country_code",
    "location_state",
    "location_city",
    "location_postal_prefix",
    "geo_bucket",
    "month",
    "day_of_week",
    "day_of_month_bucket",
    "lead_time_bucket",
    "holiday_window_type",
]
TARGET_COLUMN = TARGET_COLUMN_NAME # 使用從 data.py 匯入的統一名稱

CATEGORICAL_DEFAULTS: Dict[str, str] = {
    "property_type": "unknown",
    "location_country_code": "unknown",
    "location_state": "unknown",
    "location_city": "unknown",
    "location_postal_prefix": "unknown",
    "geo_bucket": "unknown",
    "month": "unknown",
    "day_of_week": "unknown",
    "day_of_month_bucket": "unknown",
    "lead_time_bucket": "unknown",
    "holiday_window_type": "regular",
}

FEATURE_DESCRIPTIONS: Dict[str, str] = {
    "bedrooms": "Number of bedrooms in the comparable listing.",
    "baths": "Number of bathrooms in the comparable listing.",
    "accommodates": "Maximum guest capacity of the comparable listing.",
    "beds": "Number of beds in the comparable listing.",
    "similarity_score": "Similarity score between the comparable listing and the target listing.",
    "pool_score": "Ranking score within the comparable pool.",
    "effective_rank_score": "Effective rank score after similarity and pool ranking.",
    "price_reliability_score": "Estimated reliability of the competitor price data.",
    "rating": "Guest rating of the comparable listing.",
    "reviews": "Number of reviews for the comparable listing.",
    "tenure_runs": "Number of times this comparable has been evaluated in the pool.",
    "is_weekend": "Binary flag (1 for Fri/Sat, 0 otherwise) to capture weekend pricing premiums.",
    "is_holiday": "Binary flag (1 for holidays, 0 otherwise) to capture holiday/festival pricing premiums.",
    "days_until_stay": "Number of days between when the price was observed and the stay date.",
    "days_since_first_seen": "Age of this comparable entry in the database when the price was observed.",
    "holiday_streak_length": "Length of the contiguous holiday run that includes this stay date.",
    "is_long_weekend": "Binary flag indicating a stay date that falls in a long-weekend window.",
    "distance_to_target_km": "Distance between this comparable and its target listing in kilometers.",
    "property_type": "Categorical listing property type. This is one-hot encoded into property_type_* columns.",
    "location_country_code": "Country bucket for the target listing's market.",
    "location_state": "State or region bucket for the target listing's market.",
    "location_city": "City bucket for the target listing's market.",
    "location_postal_prefix": "Postal-code prefix bucket for the target listing's market.",
    "geo_bucket": "Rounded latitude/longitude bucket representing local market area.",
    "month": "Categorical month bucket derived from the stay date.",
    "day_of_week": "Categorical weekday bucket derived from the stay date.",
    "day_of_month_bucket": "Categorical bucket for where the stay date falls within the month.",
    "lead_time_bucket": "Categorical bucket for how far the stay date is from the observation date.",
    "holiday_window_type": "Categorical holiday context bucket for the stay date.",
}


def _clean_training_frame(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if TARGET_COLUMN not in df.columns:
        raise ValueError(f"Training data must include '{TARGET_COLUMN}' column.")

    # 治本優化：移除價格離群值 (Outlier Removal)
    # 排除價格超過平均值 3 個標準差的樣本，這能顯著提升 Fold 的穩定性
    price_mean = df[TARGET_COLUMN].mean()
    price_std = df[TARGET_COLUMN].std()
    if price_std > 0:
        df = df[df[TARGET_COLUMN] <= (price_mean + 3 * price_std)]
        df = df[df[TARGET_COLUMN] >= (price_mean - 3 * price_std)]

    df = df[df[TARGET_COLUMN].notna()]

    if "is_weekend" not in df.columns:
        # 如果資料來源未提供日期（例如回退到快照模式），則預設為 0
        df["is_weekend"] = 0  # 預設為平日

    if "is_holiday" not in df.columns:
        df["is_holiday"] = 0.0

    # 處理經緯度
    for col in ["lat", "lng"]:
        if col not in df.columns: df[col] = 0.0

    # 處理設施列表展開 (治本：將資料庫原始的 amenities 陣列轉為特徵欄位)
    if "amenities" in df.columns:
        for a in AMENITIES_LIST:
            col_name = f"has_{a}"
            df[col_name] = df["amenities"].apply(
                lambda x: 1.0 if isinstance(x, list) and a in x else 0.0
            )

    for column in NUMERIC_FEATURES:
        if column not in df.columns:
            df[column] = 0.0

    for column in CATEGORICAL_FEATURES:
        default_value = CATEGORICAL_DEFAULTS.get(column, "unknown")
        if column not in df.columns:
            df[column] = default_value
        df[column] = df[column].fillna(default_value).astype(str)

    df[NUMERIC_FEATURES] = df[NUMERIC_FEATURES].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    return df


def _feature_matrix(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
    # 治本修正：不再此處重複清洗。
    # 由呼叫者（train_model 或 build_feature_matrix_df）確保傳入已清洗的資料。
    dummy = pd.get_dummies(df[CATEGORICAL_FEATURES], prefix=CATEGORICAL_FEATURES)
    X = pd.concat([df[NUMERIC_FEATURES], dummy], axis=1)
    y = df[TARGET_COLUMN].astype(float)
    return X, y


def build_default_numeric_features(df: pd.DataFrame) -> Dict[str, float]:
    df = _clean_training_frame(df)
    return {column: float(df[column].median()) for column in NUMERIC_FEATURES}


def build_feature_matrix_df(df: pd.DataFrame) -> pd.DataFrame:
    # 治本修正：確保用於 debug 的原始資料與特徵矩陣經過相同的過濾流程，以對齊索引長度
    cleaned_df = _clean_training_frame(df)
    X, y = _feature_matrix(cleaned_df)
    training_df = X.copy()
    debug_column_map = {
        "price_date": "debug_price_date",
        "observed_at_date": "debug_observed_at_date",
        "first_seen_date": "debug_first_seen_date",
        "source_type": "debug_source_type",
        "source_report_id": "debug_source_report_id",
        "source_listing_id": "debug_source_listing_id",
        "source_comp_id": "debug_source_comp_id",
        "airbnb_listing_id": "debug_airbnb_listing_id",
    }
    for source_column, debug_column in debug_column_map.items():
        if source_column in cleaned_df.columns:
            training_df[debug_column] = cleaned_df[source_column].values
    training_df[TARGET_COLUMN] = y.values
    return training_df


def build_feature_description_df(feature_columns: List[str]) -> pd.DataFrame:
    rows = []
    categorical_prefixes = {f"{feature}_": feature for feature in CATEGORICAL_FEATURES}

    for column in feature_columns:
        if column == TARGET_COLUMN:
            rows.append({
                "feature": column,
                "description": "Target value for supervised training: competitor nightly price.",
            })
            continue

        if column in FEATURE_DESCRIPTIONS:
            description = FEATURE_DESCRIPTIONS[column]
        else:
            description = None
            for prefix, feature_name in categorical_prefixes.items():
                if column.startswith(prefix):
                    category = column[len(prefix):]
                    description = f"One-hot encoded {feature_name} category '{category}'."
                    break

        if description is None:
            if column.startswith("debug_"):
                description = "Debug-only raw field kept for inspection; not used by model training."
            else:
                description = "Feature generated from input data; either a numeric field or a one-hot encoded category."

        rows.append({"feature": column, "description": description})

    return pd.DataFrame(rows)


def build_feature_importance_report(
    model: XGBRegressor,
    feature_columns: List[str],
) -> pd.DataFrame:
    booster = model.get_booster()
    importance_types = [
        "weight",
        "gain",
        "cover",
        "total_gain",
        "total_cover",
    ]
    importance_maps = {
        importance_type: booster.get_score(importance_type=importance_type)
        for importance_type in importance_types
    }

    rows = []
    raw_importances = list(model.feature_importances_)
    for index, feature in enumerate(feature_columns):
        row = {
            "feature": feature,
            "importance": float(raw_importances[index]) if index < len(raw_importances) else 0.0,
        }
        for importance_type in importance_types:
            row[importance_type] = float(importance_maps[importance_type].get(feature, 0.0))
        rows.append(row)

    report_df = pd.DataFrame(rows)
    return report_df.sort_values(["importance", "gain", "weight"], ascending=False).reset_index(drop=True)


def write_model_tree_dump(model: XGBRegressor, output_path: Path) -> None:
    booster = model.get_booster()
    tree_dump = "\n\n".join(booster.get_dump(with_stats=True, dump_format="text"))
    output_path.write_text(tree_dump, encoding="utf-8")


def build_prediction_explanation_frames(
    model: XGBRegressor,
    feature_matrix: pd.DataFrame,
    *,
    metadata: Optional[pd.DataFrame] = None,
    top_n: int = 5,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    if feature_matrix.empty:
        return pd.DataFrame(), pd.DataFrame()

    dmatrix = DMatrix(feature_matrix, feature_names=list(feature_matrix.columns))
    contribs = model.get_booster().predict(
        dmatrix,
        pred_contribs=True,
        validate_features=False,
    )

    if contribs.shape[1] != (len(feature_matrix.columns) + 1):
        raise ValueError("Unexpected contribution matrix shape returned by XGBoost.")

    contribution_df = pd.DataFrame(
        contribs[:, :-1],
        columns=list(feature_matrix.columns),
        index=feature_matrix.index,
    )
    bias_values = contribs[:, -1]
    predicted_log = bias_values + contribution_df.sum(axis=1).to_numpy()
    predicted_price = np.expm1(predicted_log)
    baseline_price = np.expm1(bias_values)

    if metadata is None:
        metadata = pd.DataFrame(index=feature_matrix.index)
    else:
        metadata = metadata.reset_index(drop=True)

    summary_rows: List[Dict[str, Any]] = []
    contribution_rows: List[Dict[str, Any]] = []

    for row_position, row_index in enumerate(feature_matrix.index):
        row_meta = metadata.iloc[row_position].to_dict() if row_position < len(metadata) else {}
        row_values = feature_matrix.iloc[row_position]
        row_contribs = contribution_df.loc[row_index]

        active_contribs = row_contribs[row_contribs.abs() > 1e-9].sort_values(
            key=lambda s: s.abs(),
            ascending=False,
        )
        top_features = list(active_contribs.head(top_n).index)

        summary_row: Dict[str, Any] = {
            **row_meta,
            "baseline_log_price": float(bias_values[row_position]),
            "baseline_price": float(baseline_price[row_position]),
            "predicted_log_price": float(predicted_log[row_position]),
            "predicted_price": float(predicted_price[row_position]),
            "active_feature_count": int((row_contribs.abs() > 1e-9).sum()),
        }
        if top_features:
            summary_row["top_positive_feature"] = str(row_contribs.sort_values(ascending=False).index[0])
            summary_row["top_positive_contribution_log"] = float(row_contribs.sort_values(ascending=False).iloc[0])
            summary_row["top_negative_feature"] = str(row_contribs.sort_values().index[0])
            summary_row["top_negative_contribution_log"] = float(row_contribs.sort_values().iloc[0])

        for rank, feature in enumerate(top_features, start=1):
            contribution_value = float(row_contribs[feature])
            summary_row[f"top_driver_{rank}_feature"] = feature
            summary_row[f"top_driver_{rank}_value"] = row_values[feature]
            summary_row[f"top_driver_{rank}_contribution_log"] = contribution_value
            summary_row[f"top_driver_{rank}_multiplier"] = float(np.exp(contribution_value))

        summary_rows.append(summary_row)

        for rank, feature in enumerate(active_contribs.index, start=1):
            contribution_value = float(row_contribs[feature])
            contribution_rows.append(
                {
                    **row_meta,
                    "feature_rank": rank,
                    "feature": feature,
                    "feature_value": row_values[feature],
                    "contribution_log": contribution_value,
                    "abs_contribution_log": abs(contribution_value),
                    "contribution_multiplier": float(np.exp(contribution_value)),
                    "direction": "push_up" if contribution_value > 0 else "push_down",
                    "baseline_price": float(baseline_price[row_position]),
                    "predicted_price": float(predicted_price[row_position]),
                }
            )

    summary_df = pd.DataFrame(summary_rows)
    contributions_df = pd.DataFrame(contribution_rows)
    return summary_df, contributions_df




def train_model(df: pd.DataFrame) -> Tuple[XGBRegressor, List[str], pd.Series, dict]:
    # 治本修正：在進入特徵工程前先進行一次性清洗，確保後續所有矩陣與權重長度一致
    df = _clean_training_frame(df)
    X, y = _feature_matrix(df)
    y_log = np.log1p(y)
    # 優化：線性加權。保留相似度的影響力，但不至於讓模型完全忽略其他樣本
    weights = df["similarity_score"].fillna(0.5).values

    # 治本優化：執行 5-Fold Cross Validation 以獲取 $Q^2$ 與穩定性指標
    kf = KFold(n_splits=5, shuffle=True, random_state=42)
    fold_results = []

    # 確認訓練集中是否有足夠的時間特徵變異
    if "is_weekend" in X.columns:
        unique_weekends = X["is_weekend"].unique()
        if len(unique_weekends) < 2:
            print("[ML Model] Warning: Training data only contains one type of day (weekday or weekend). The model might not learn weekend premiums.")

    # 退回至之前的穩健參數組合
    best_params = {
        "objective": "reg:squarederror",
        "n_estimators": 1000,
        "max_depth": 6,
        "learning_rate": 0.05,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "tree_method": "hist",
        "random_state": 42,
        "n_jobs": -1,
        "verbosity": 0
    }

    print(f"[ML Model] 啟動 5-Fold 交叉驗證 (總樣本數: {len(X)})...")
    kf = KFold(n_splits=5, shuffle=True, random_state=42)
    fold_results = []

    for fold, (train_idx, test_idx) in enumerate(kf.split(X), 1):
        X_train_cv, X_test_cv = X.iloc[train_idx], X.iloc[test_idx]
        y_train_cv, y_test_cv = y_log.iloc[train_idx], y_log.iloc[test_idx]
        w_train_cv = weights[train_idx]

        # 每一折都使用 Early Stopping 找到最佳收斂點
        # 使用爆搜出來的最佳參數
        fold_model = XGBRegressor(**best_params, early_stopping_rounds=50)
        fold_model.fit(X_train_cv, y_train_cv, sample_weight=w_train_cv, 
                       eval_set=[(X_test_cv, y_test_cv)], verbose=False)

        preds_log = fold_model.predict(X_test_cv)
        preds = np.expm1(preds_log)
        y_test_orig = np.expm1(y_test_cv)

        f_mae = mean_absolute_error(y_test_orig, preds)
        f_mape = mean_absolute_percentage_error(y_test_orig, preds)
        f_r2 = r2_score(y_test_orig, preds)
        
        fold_results.append({
            "fold": fold,
            "mae": f_mae,
            "mape": f_mape,
            "r2": f_r2
        })
        print(f"  - Fold {fold}: MAE=${f_mae:.2f}, R2={f_r2:.4f}")

    # 最終擬合：使用 100% 的資料訓練最終模型供預測使用
    print(f"[ML Model] 正在使用 100% 訓練集進行最終擬合...")
    final_model = XGBRegressor(**best_params)
    final_model.fit(X, y_log, sample_weight=weights)

    # 計算最終模型的 In-sample R2 (用於對照 Q2)
    final_preds = np.expm1(final_model.predict(X))
    y_orig = np.expm1(y_log)
    final_r2 = r2_score(y_orig, final_preds)

    metrics = {
        "mae": np.mean([f["mae"] for f in fold_results]),
        "mae_std": np.std([f["mae"] for f in fold_results]),
        "mape": np.mean([f["mape"] for f in fold_results]),
        "q2": np.mean([f["r2"] for f in fold_results]), # 交叉驗證 R2 即為 Q2
        "r2": final_r2,
        "r2_std": np.std([f["r2"] for f in fold_results]),
        "fold_details": fold_results
    }

    print(f"[ML Model] Training complete. Validation MAE: ${metrics['mae']:.2f}")
    print(f"[ML Model] 預測信心度 (Q2): {metrics['q2']:.4f} (±{metrics['r2_std']:.4f})")
    print(f"[ML Model] 擬合優度 (R2): {metrics['r2']:.4f}")

    if metrics["mae"] > (y.mean() * 0.3):
        print("[Warning] High error rate. The comparable data might be too noisy.")

    feature_importances = pd.Series(final_model.feature_importances_, index=X.columns).sort_values(ascending=False)

    return final_model, list(X.columns), feature_importances, metrics

def build_target_row(feature_values: Dict[str, Any], feature_columns: List[str]) -> pd.DataFrame:
    record = {column: 0.0 for column in feature_columns}

    # 處理座標與設施
    if "lat" in feature_values: record["lat"] = float(feature_values["lat"])
    if "lng" in feature_values: record["lng"] = float(feature_values["lng"])
    for a in AMENITIES_LIST:
        key = f"has_{a}"
        if key in feature_columns:
            record[key] = float(feature_values.get(key, 0.0))

    for key in NUMERIC_FEATURES:
        record[key] = float(feature_values.get(key, 0.0))

    for feature_name in CATEGORICAL_FEATURES:
        default_value = CATEGORICAL_DEFAULTS.get(feature_name, "unknown")
        value = str(feature_values.get(feature_name, default_value) or default_value)
        feature_column = f"{feature_name}_{value}"
        if feature_column in record:
            record[feature_column] = 1.0

    return pd.DataFrame([record], columns=feature_columns)


def forecast_prices(
    model: XGBRegressor,
    feature_columns: List[str],
    target_features: Dict[str, Any],
    start_date: datetime.date,
    horizon: int = 30,
) -> List[Dict[str, Any]]: # 變更返回類型為字典列表
    results = []
    reference_date = datetime.date.today()
    country_code = target_features.get("country_code")
    for i in range(horizon):
        current_date = start_date + datetime.timedelta(days=i)
        day_features = target_features.copy()
        day_features.update(
            build_temporal_feature_values(
                price_date=current_date,
                observed_at_date=reference_date,
                first_seen_date=reference_date,
                country_code=country_code,
            )
        )
        
        row = build_target_row(day_features, feature_columns)
        # 治本：還原對數預測值
        predicted_price = float(np.expm1(model.predict(row)[0]))
        results.append({
            "date": current_date.isoformat(),
            "predicted_price": predicted_price,
            "is_weekend": day_features["is_weekend"] == 1.0, # 儲存為布林值
            "is_holiday": day_features["is_holiday"] == 1.0, # 儲存為布林值
        })
    return results
