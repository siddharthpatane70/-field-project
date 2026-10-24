import os
import json
import sqlite3
import datetime
import warnings

if __name__ == '__main__':
    raise SystemExit(
        "This legacy daily trainer used synthetic seed data and is disabled. "
        "Start the Python application with: python app.py"
    )

import numpy as np
import pandas as pd
import joblib
from typing import Dict, Tuple, Any, List, Union

from sklearn.base import BaseEstimator, TransformerMixin, RegressorMixin
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import StandardScaler, OneHotEncoder
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge, HuberRegressor
from sklearn.pipeline import Pipeline
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.utils.validation import check_is_fitted

warnings.filterwarnings('ignore')

DB_PATH = 'tvs_inventory.db'
MODEL_DIR = 'model_registry'
MODEL_FILE = os.path.join(MODEL_DIR, 'advanced_sales_pipeline.pkl')
METADATA_FILE = os.path.join(MODEL_DIR, 'model_metadata.json')


class IntermittentDemandEstimator(BaseEstimator, TransformerMixin):
    """
    Enhanced Intermittent Demand Estimator implementing Croston-like metrics:
    Calculates zero-demand ratio and average inter-demand interval per SKU.
    """
    def __init__(self, default_ratio: float = 0.1):
        self.default_ratio = default_ratio
        self.intermittent_stats_ = {}
        self.mean_demand_interval_ = {}

    def fit(self, X: pd.DataFrame, y: np.ndarray = None):
        if 'quantity' in X.columns and 'part_id' in X.columns:
            # 1. Zero Ratio
            zero_stats = X.groupby('part_id')['quantity'].apply(lambda s: float((s == 0).mean()))
            self.intermittent_stats_ = zero_stats.to_dict()

            # 2. Average Demand Interval (Days between non-zero sales)
            def calc_interval(s):
                non_zero_indices = np.where(s.values > 0)[0]
                if len(non_zero_indices) <= 1:
                    return 30.0  # High default interval for slow movers
                return float(np.mean(np.diff(non_zero_indices)))

            interval_stats = X.groupby('part_id')['quantity'].apply(calc_interval)
            self.mean_demand_interval_ = interval_stats.to_dict()

        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        X_out = X.copy()
        if 'part_id' in X_out.columns:
            X_out['intermittent_ratio'] = X_out['part_id'].map(self.intermittent_stats_).fillna(self.default_ratio)
            X_out['mean_demand_interval'] = X_out['part_id'].map(self.mean_demand_interval_).fillna(1.0)
        else:
            X_out['intermittent_ratio'] = self.default_ratio
            X_out['mean_demand_interval'] = 1.0
        return X_out


class TimeSeriesFeatureEngineer(BaseEstimator, TransformerMixin):
    """
    Advanced Feature Engineering Pipeline:
    Includes time lags, rolling window aggregations, cyclical time embeddings,
    and price elasticity ratios.
    """
    def __init__(self, lag_days: List[int] = None, rolling_windows: List[int] = None):
        self.lag_days = lag_days if lag_days is not None else [1, 7, 14, 30]
        self.rolling_windows = rolling_windows if rolling_windows is not None else [7, 14, 30]

    def fit(self, X: pd.DataFrame, y=None):
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        df = X.copy()
        if 'sale_date' not in df.columns:
            return df

        df['sale_date'] = pd.to_datetime(df['sale_date'])
        df = df.sort_values(['part_id', 'sale_date'])

        # Calendar Features
        df['day_of_week'] = df['sale_date'].dt.dayofweek
        df['day_of_month'] = df['sale_date'].dt.day
        df['month'] = df['sale_date'].dt.month
        df['quarter'] = df['sale_date'].dt.quarter
        df['is_weekend'] = df['day_of_week'].isin([5, 6]).astype(int)

        # Cyclical Time Encodings (Sine / Cosine embeddings)
        df['sin_day_of_week'] = np.sin(2 * np.pi * df['day_of_week'] / 7)
        df['cos_day_of_week'] = np.cos(2 * np.pi * df['day_of_week'] / 7)
        df['sin_month'] = np.sin(2 * np.pi * df['month'] / 12)
        df['cos_month'] = np.cos(2 * np.pi * df['month'] / 12)

        # Lag and Rolling Aggregations
        if 'quantity' in df.columns:
            for lag in self.lag_days:
                df[f'qty_lag_{lag}'] = df.groupby('part_id')['quantity'].shift(lag)

            for window in self.rolling_windows:
                df[f'qty_roll_mean_{window}'] = (
                    df.groupby('part_id')['quantity']
                    .transform(lambda x: x.shift(1).rolling(window, min_periods=1).mean())
                )
                df[f'qty_roll_std_{window}'] = (
                    df.groupby('part_id')['quantity']
                    .transform(lambda x: x.shift(1).rolling(window, min_periods=1).std())
                )
                df[f'qty_roll_max_{window}'] = (
                    df.groupby('part_id')['quantity']
                    .transform(lambda x: x.shift(1).rolling(window, min_periods=1).max())
                )

        # Price Ratio Feature (Normalizing price across dataset)
        if 'unit_price' in df.columns:
            df['price_to_mean_ratio'] = df['unit_price'] / (df['unit_price'].mean() + 1e-5)

        return df


class HybridSKUEnsemblePredictor(BaseEstimator, RegressorMixin):
    """
    Robust Dual-Engine Ensemble Predictor:
    Combines Gradient Boosting (Fast-Movers), Random Forest (Intermittent/Slow), 
    Huber Robust Regressor (Outliers), and Ridge as a baseline.
    """
    def __init__(
        self, 
        n_estimators: int = 150, 
        learning_rate: float = 0.03, 
        weights: List[float] = None,
        random_state: int = 42
    ):
        self.n_estimators = n_estimators
        self.learning_rate = learning_rate
        self.weights = weights if weights is not None else [0.45, 0.35, 0.12, 0.08]
        self.random_state = random_state

    def fit(self, X: np.ndarray, y: np.ndarray):
        self.gb_model_ = GradientBoostingRegressor(
            n_estimators=self.n_estimators,
            learning_rate=self.learning_rate,
            max_depth=5,
            subsample=0.85,
            random_state=self.random_state
        )
        self.rf_model_ = RandomForestRegressor(
            n_estimators=max(20, self.n_estimators // 2),
            max_depth=6,
            random_state=self.random_state
        )
        self.huber_model_ = HuberRegressor(max_iter=500)
        self.ridge_model_ = Ridge(alpha=2.0)

        self.gb_model_.fit(X, y)
        self.rf_model_.fit(X, y)
        self.huber_model_.fit(X, y)
        self.ridge_model_.fit(X, y)

        self.is_fitted_ = True
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        check_is_fitted(self, 'is_fitted_')
        pred_gb = self.gb_model_.predict(X)
        pred_rf = self.rf_model_.predict(X)
        pred_huber = self.huber_model_.predict(X)
        pred_ridge = self.ridge_model_.predict(X)
        
        # Weighted Ensemble Output
        w = self.weights
        final_preds = w[0] * pred_gb + w[1] * pred_rf + w[2] * pred_huber + w[3] * pred_ridge
        return np.maximum(0.0, final_preds)  # Non-negative constraint


def setup_database_and_seed_data():
    """Disabled legacy setup: forecast training must never add demonstration sales."""
    raise RuntimeError(
        "The legacy daily pipeline is retired. It used synthetic seed sales and is not used by the monthly forecast API. "
        "Run app.py and use the Monthly Forecast Hub with recorded sales data."
    )

    # The original implementation below is retained for reference only.
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            part_name TEXT NOT NULL,
            category TEXT NOT NULL,
            unit_price REAL NOT NULL,
            current_stock INTEGER NOT NULL DEFAULT 0,
            reorder_level INTEGER NOT NULL DEFAULT 10,
            created_at TEXT NOT NULL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            part_id TEXT NOT NULL,
            sale_date TEXT NOT NULL,
            month INTEGER NOT NULL,
            year INTEGER NOT NULL,
            unit_price REAL NOT NULL,
            workshop_service_count INTEGER DEFAULT 30,
            quantity INTEGER NOT NULL
        )
    ''')
    conn.commit()

    cursor.execute("SELECT COUNT(*) FROM inventory")
    if cursor.fetchone()[0] == 0:
        today_str = datetime.date.today().strftime('%Y-%m-%d')
        default_items = [
            ("TVS Apache RTR 200 Front Brake Pad", "Brakes", 850.00, 45, 15, today_str),
            ("TVS Jupiter Drive Belt", "Drive Train", 620.00, 30, 10, today_str),
            ("TVS NTORQ 125 Air Filter", "Engine", 310.00, 8, 12, today_str),
            ("TVS XL100 Spark Plug", "Electrical", 180.00, 120, 25, today_str),
            ("TVS Raider 125 Chain Sprocket Kit", "Transmission", 1450.00, 18, 5, today_str)
        ]
        cursor.executemany('''
            INSERT INTO inventory (part_name, category, unit_price, current_stock, reorder_level, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', default_items)
        conn.commit()

    cursor.execute("SELECT COUNT(*) FROM sales")
    if cursor.fetchone()[0] == 0:
        dates = pd.date_range(end=datetime.date.today(), periods=180, freq='D')
        parts = ['P001_BRAKE_PAD', 'P002_OIL_FILTER', 'P003_CLUTCH_PLATE']
        records = []

        for part in parts:
            base_demand = np.random.randint(18, 42) if 'OIL' in part or 'BRAKE' in part else np.random.randint(2, 9)
            for d in dates:
                multiplier = 1.35 if d.dayofweek in [5, 6] else 1.0
                intermittent_zero = 1 if (base_demand < 10 and np.random.rand() > 0.55) else 0
                qty = 0 if intermittent_zero else int(np.random.poisson(base_demand) * multiplier)
                
                records.append((
                    part,
                    d.strftime('%Y-%m-%d'),
                    d.month,
                    d.year,
                    450.0 if 'BRAKE' in part else 180.0,
                    np.random.randint(20, 55),
                    qty
                ))

        cursor.executemany('''
            INSERT INTO sales (part_id, sale_date, month, year, unit_price, workshop_service_count, quantity)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', records)
        conn.commit()

    conn.close()


def load_data_from_db() -> pd.DataFrame:
    setup_database_and_seed_data()
    conn = sqlite3.connect(DB_PATH)
    query = "SELECT sale_date, part_id, unit_price, workshop_service_count, quantity FROM sales"
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df


def train_advanced_sales_model() -> Tuple[Any, Dict[str, Any]]:
    print("=" * 65)
    print("   TVS ADVANCED PREDICTIVE INVENTORY & SALES PIPELINE (v2.4.0)")
    print("=" * 65)

    raw_df = load_data_from_db()

    # Stage 1: Intermittent Estimations
    intermittent_transformer = IntermittentDemandEstimator()
    raw_df = intermittent_transformer.fit_transform(raw_df)

    # Stage 2: Time Series Feature Engineering
    fe_transformer = TimeSeriesFeatureEngineer()
    df_engineered = fe_transformer.transform(raw_df)
    
    df_engineered = df_engineered.fillna(0)
    df_engineered = df_engineered.iloc[30:].reset_index(drop=True)

    features_num = [
        'unit_price', 'workshop_service_count', 'intermittent_ratio', 'mean_demand_interval',
        'day_of_week', 'day_of_month', 'month', 'quarter', 'is_weekend',
        'sin_day_of_week', 'cos_day_of_week', 'sin_month', 'cos_month',
        'price_to_mean_ratio', 'qty_lag_1', 'qty_lag_7', 'qty_lag_14', 'qty_lag_30',
        'qty_roll_mean_7', 'qty_roll_std_7', 'qty_roll_max_7',
        'qty_roll_mean_14', 'qty_roll_std_14', 'qty_roll_max_14',
        'qty_roll_mean_30', 'qty_roll_std_30', 'qty_roll_max_30'
    ]
    features_cat = ['part_id']
    target_col = 'quantity'

    X = df_engineered[features_num + features_cat]
    y = df_engineered[target_col].values

    preprocessor = ColumnTransformer(
        transformers=[
            ('num', StandardScaler(), features_num),
            ('cat', OneHotEncoder(handle_unknown='ignore', sparse_output=False), features_cat)
        ]
    )

    pipeline = Pipeline(steps=[
        ('preprocessor', preprocessor),
        ('regressor', HybridSKUEnsemblePredictor(n_estimators=160, learning_rate=0.03))
    ])

    # Stage 3: Temporal Cross-Validation (TimeSeriesSplit)
    print("\n[+] Performing 5-Fold Temporal Cross-Validation (TimeSeriesSplit)...")
    tscv = TimeSeriesSplit(n_splits=5)
    cv_scores_mae = []
    
    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        X_tr, y_tr = X.iloc[train_idx], y[train_idx]
        X_val, y_val = X.iloc[val_idx], y[val_idx]
        
        pipeline.fit(X_tr, y_tr)
        preds_val = pipeline.predict(X_val)
        fold_mae = mean_absolute_error(y_val, preds_val)
        cv_scores_mae.append(fold_mae)
        print(f"    - Fold {fold + 1} Out-of-Fold MAE: {fold_mae:.4f}")

    # Final Full Training
    print("\n[+] Training Final Ensemble Pipeline on Full Dataset...")
    pipeline.fit(X, y)

    predictions = pipeline.predict(X)
    mae = mean_absolute_error(y, predictions)
    rmse = np.sqrt(mean_squared_error(y, predictions))
    r2 = r2_score(y, predictions)

    metrics = {
        'model_version': '2.4.0-tvs-production',
        'training_timestamp': datetime.datetime.now().isoformat(),
        'total_samples_trained': int(len(X)),
        'metrics': {
            'MAE': round(float(mae), 4),
            'RMSE': round(float(rmse), 4),
            'R2_Score': round(float(r2), 4),
            'CV_Mean_MAE': round(float(np.mean(cv_scores_mae)), 4)
        },
        'features_used': features_num + features_cat
    }

    print("\n[+] Training Completed Successfully!")
    print(f"    - Training MAE             : {metrics['metrics']['MAE']}")
    print(f"    - Cross-Validated Mean MAE : {metrics['metrics']['CV_Mean_MAE']}")
    print(f"    - Root Mean Sq. Error (RMSE): {metrics['metrics']['RMSE']}")
    print(f"    - Coefficient of Det. (R2)  : {metrics['metrics']['R2_Score']}")

    save_model_artifacts(pipeline, metrics)
    return pipeline, metrics


def save_model_artifacts(pipeline: Any, metrics: Dict[str, Any]):
    os.makedirs(MODEL_DIR, exist_ok=True)
    joblib.dump(pipeline, MODEL_FILE, compress=3)
    print(f"\n[+] Production Pipeline serialized to: '{MODEL_FILE}'")
    with open(METADATA_FILE, 'w') as f:
        json.dump(metrics, f, indent=4)
    print(f"[+] Model Metadata saved to: '{METADATA_FILE}'")


if __name__ == '__main__':
    raise SystemExit(
        "The legacy daily trainer is retired and will not seed or overwrite model data. "
        "Start the Python application with: python app.py"
    )
